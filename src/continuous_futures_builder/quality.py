from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder.constants import (
    ADJUSTMENT_BACK_ADJUSTED,
    ADJUSTMENT_UNADJUSTED,
    OUTPUT_COLUMNS,
    PRICE_COLUMNS,
    SUPPORTED_ADJUSTMENT_MODES,
)
from continuous_futures_builder.models import ContinuousFuturesQualityReport
from continuous_futures_builder.prefixes import infer_contract_prefix

LARGE_TRANSITION_GAP_PERCENT = 5.0


def _column_or_empty(frame: pl.DataFrame, column: str) -> pl.Series:
    if column not in frame.columns:
        return pl.Series(column, [])
    return frame.get_column(column)


def _duplicate_ts_count(frame: pl.DataFrame) -> int:
    if "ts" not in frame.columns or frame.height == 0:
        return 0
    return frame.height - frame.get_column("ts").n_unique()


def _gaps_count(frame: pl.DataFrame) -> int:
    if "ts" not in frame.columns or frame.height < 2:
        return 0
    dates = [value.date() if hasattr(value, "date") else value for value in frame["ts"].to_list()]
    gaps = 0
    for previous, current in zip(dates, dates[1:], strict=False):
        if current - previous > timedelta(days=1):
            gaps += 1
    return gaps


def _null_price_count(frame: pl.DataFrame) -> int:
    available = [column for column in PRICE_COLUMNS if column in frame.columns]
    if not available:
        return frame.height
    return int(
        frame.select(
            pl.any_horizontal([pl.col(column).is_null() for column in available]).sum()
        ).item()
    )


def _roll_dates(frame: pl.DataFrame) -> list[Any]:
    if "roll_flag" not in frame.columns or "roll_date" not in frame.columns:
        return []
    return (
        frame.filter(pl.col("roll_flag").fill_null(False))
        .select("roll_date")
        .drop_nulls()
        .unique(maintain_order=True)
        .get_column("roll_date")
        .to_list()
    )


def _contracts_used(frame: pl.DataFrame) -> list[str]:
    if "active_contract" not in frame.columns:
        return []
    return [
        str(contract)
        for contract in frame.get_column("active_contract")
        .drop_nulls()
        .unique(maintain_order=True)
        .to_list()
    ]


def _prefixes_used(contracts: list[str]) -> list[str]:
    prefixes: list[str] = []
    for contract in contracts:
        prefix = infer_contract_prefix(contract)
        if prefix not in prefixes:
            prefixes.append(prefix)
    return prefixes


def _family_transitions(frame: pl.DataFrame):
    from continuous_futures_builder.models import ContractFamilyTransition

    required = {"ts", "close", "active_contract"}
    if not required.issubset(frame.columns) or frame.height < 2:
        return []

    rows = (
        frame.sort("ts")
        .select(["ts", "close", "active_contract", "roll_date"])
        .iter_rows(named=True)
        if "roll_date" in frame.columns
        else frame.sort("ts").select(["ts", "close", "active_contract"]).iter_rows(named=True)
    )
    transitions: list[ContractFamilyTransition] = []
    previous = None
    for row in rows:
        if previous is None:
            previous = row
            continue

        old_contract = str(previous["active_contract"])
        new_contract = str(row["active_contract"])
        if old_contract == new_contract:
            previous = row
            continue

        old_prefix = infer_contract_prefix(old_contract)
        new_prefix = infer_contract_prefix(new_contract)
        if old_prefix == new_prefix:
            previous = row
            continue

        old_close = float(previous["close"]) if previous["close"] is not None else None
        new_close = float(row["close"]) if row["close"] is not None else None
        roll_gap = None
        gap_percent = None
        if old_close is not None and new_close is not None:
            roll_gap = new_close - old_close
            if old_close != 0.0:
                gap_percent = (roll_gap / old_close) * 100.0

        warning = None
        if gap_percent is not None and abs(gap_percent) >= LARGE_TRANSITION_GAP_PERCENT:
            warning = f"large family transition gap: {gap_percent:.2f}%"

        roll_date = row.get("roll_date") or row["ts"]
        if hasattr(roll_date, "date"):
            roll_date = roll_date.date()

        transitions.append(
            ContractFamilyTransition(
                from_contract=old_contract,
                to_contract=new_contract,
                from_prefix=old_prefix,
                to_prefix=new_prefix,
                transition_date=row["ts"],
                roll_date=roll_date,
                old_close=old_close,
                new_close=new_close,
                roll_gap=roll_gap,
                gap_percent=gap_percent,
                warning=warning,
            )
        )
        previous = row

    return transitions


def _max_abs_roll_gap(frame: pl.DataFrame) -> float | None:
    if "roll_flag" not in frame.columns or "close" not in frame.columns or frame.height < 2:
        return None
    indexed = (
        frame.sort("ts")
        .with_row_index("__row")
        .with_columns(pl.col("close").shift(1).alias("__previous_close"))
    )
    roll_rows = indexed.filter(pl.col("roll_flag").fill_null(False))
    if roll_rows.height == 0:
        return None
    gaps = (pl.col("close") - pl.col("__previous_close")).abs()
    value = roll_rows.select(gaps.max()).item()
    return None if value is None else float(value)


def _adjustment_factor_min_max(frame: pl.DataFrame) -> tuple[float | None, float | None]:
    if (
        "adjustment_factor" not in frame.columns
        or not frame.schema["adjustment_factor"].is_numeric()
    ):
        return None, None
    minimum = frame.get_column("adjustment_factor").min()
    maximum = frame.get_column("adjustment_factor").max()
    return (
        None if minimum is None else float(minimum),
        None if maximum is None else float(maximum),
    )


def build_quality_report(path: str | Path) -> ContinuousFuturesQualityReport:
    target = Path(path)
    frame = pl.read_parquet(target)
    warnings: list[str] = []

    missing = [column for column in OUTPUT_COLUMNS if column not in frame.columns]
    if missing:
        warnings.append(f"missing required columns: {', '.join(missing)}")

    if "ts" in frame.columns and not frame.select(pl.col("ts").is_sorted()).item():
        warnings.append("ts is not sorted ascending")

    duplicate_ts_count = _duplicate_ts_count(frame)
    if duplicate_ts_count:
        warnings.append(f"duplicate ts rows: {duplicate_ts_count}")

    null_active_contract_count = int(_column_or_empty(frame, "active_contract").null_count())
    if null_active_contract_count:
        warnings.append(f"null active_contract rows: {null_active_contract_count}")

    null_source_count = int(_column_or_empty(frame, "source_contract_symbol").null_count())
    if null_source_count:
        warnings.append(f"null source_contract_symbol rows: {null_source_count}")

    adjustment_mode = None
    if "adjustment_mode" in frame.columns:
        modes = frame.get_column("adjustment_mode").drop_nulls().unique().to_list()
        adjustment_mode = str(modes[0]) if len(modes) == 1 else ",".join(sorted(map(str, modes)))
        invalid_modes = sorted(set(map(str, modes)) - SUPPORTED_ADJUSTMENT_MODES)
        if invalid_modes:
            warnings.append(f"invalid adjustment_mode values: {', '.join(invalid_modes)}")

    if "adjustment_factor" not in frame.columns:
        warnings.append("missing adjustment_factor")
    elif not frame.schema["adjustment_factor"].is_numeric():
        warnings.append("adjustment_factor is not numeric")

    null_price_count = _null_price_count(frame)
    if null_price_count:
        warnings.append(f"rows with null OHLC price: {null_price_count}")

    factor_min, factor_max = _adjustment_factor_min_max(frame)
    max_abs_roll_gap = (
        _max_abs_roll_gap(frame) if adjustment_mode == ADJUSTMENT_UNADJUSTED else None
    )
    if adjustment_mode == ADJUSTMENT_BACK_ADJUSTED:
        max_abs_roll_gap = None

    roll_dates = _roll_dates(frame)
    contracts_used = _contracts_used(frame)
    prefixes_used = _prefixes_used(contracts_used)
    family_transitions = _family_transitions(frame)
    family_transition_gaps = [
        abs(transition.roll_gap)
        for transition in family_transitions
        if transition.roll_gap is not None
    ]
    return ContinuousFuturesQualityReport(
        path=target,
        adjustment_mode=adjustment_mode,
        row_count=frame.height,
        min_ts=frame.get_column("ts").min() if "ts" in frame.columns else None,
        max_ts=frame.get_column("ts").max() if "ts" in frame.columns else None,
        contracts_used=contracts_used,
        contract_count=len(contracts_used),
        prefixes_used=prefixes_used,
        families_used=prefixes_used,
        roll_count=len(roll_dates),
        roll_dates=roll_dates,
        family_transition_count=len(family_transitions),
        family_transitions=family_transitions,
        gaps_count=_gaps_count(frame.sort("ts")) if "ts" in frame.columns else 0,
        duplicate_ts_count=duplicate_ts_count,
        null_active_contract_count=null_active_contract_count,
        null_price_count=null_price_count,
        max_abs_roll_gap=max_abs_roll_gap,
        max_abs_family_transition_gap=max(family_transition_gaps)
        if family_transition_gaps
        else None,
        adjustment_factor_min=factor_min,
        adjustment_factor_max=factor_max,
        status="failed" if warnings else "ok",
        warnings=warnings,
    )
