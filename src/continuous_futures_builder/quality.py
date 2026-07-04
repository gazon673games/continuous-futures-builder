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
    return ContinuousFuturesQualityReport(
        path=target,
        adjustment_mode=adjustment_mode,
        row_count=frame.height,
        min_ts=frame.get_column("ts").min() if "ts" in frame.columns else None,
        max_ts=frame.get_column("ts").max() if "ts" in frame.columns else None,
        contracts_used=_contracts_used(frame),
        contract_count=len(_contracts_used(frame)),
        roll_count=len(roll_dates),
        roll_dates=roll_dates,
        gaps_count=_gaps_count(frame.sort("ts")) if "ts" in frame.columns else 0,
        duplicate_ts_count=duplicate_ts_count,
        null_active_contract_count=null_active_contract_count,
        null_price_count=null_price_count,
        max_abs_roll_gap=max_abs_roll_gap,
        adjustment_factor_min=factor_min,
        adjustment_factor_max=factor_max,
        status="failed" if warnings else "ok",
        warnings=warnings,
    )
