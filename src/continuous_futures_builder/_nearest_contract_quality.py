from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder._nearest_contract_models import (
    NEAREST_DATASET_TYPE,
    NEAREST_FUTURES_DATASET_DIR,
    NEAREST_QUALITY_OK,
    NEAREST_QUALITY_REJECTED,
    NEAREST_QUALITY_WARNING,
    NearestContractBuildConfig,
    NearestContractQuality,
    NearestRollBoundary,
    _as_datetime,
    _datetime_like,
)
from continuous_futures_builder._nearest_contract_rolls import (
    _frame_ts_min_max,
)
from continuous_futures_builder._nearest_contract_series import (
    _duplicate_ts_count,
    _intraday_gap_stats,
    _null_price_count,
)
from continuous_futures_builder.exceptions import (
    InputDataError,
)
from continuous_futures_builder.models import FuturesContract
from continuous_futures_builder.nearest_gap_diagnostics import (
    SessionGapSummary,
    session_summary_quality_fields,
)


def _build_quality(
    frame: pl.DataFrame,
    config: NearestContractBuildConfig,
    boundaries: list[NearestRollBoundary],
    contracts_skipped: list[str],
    preexisting_warnings: list[str],
    contract_count: int,
    session_gap_summary: SessionGapSummary,
) -> NearestContractQuality:
    warnings = list(preexisting_warnings)
    errors: list[str] = []
    duplicate_ts_count = _duplicate_ts_count(frame)
    null_price_count = _null_price_count(frame)
    null_active_contract_count = (
        int(frame.get_column("active_contract").null_count())
        if "active_contract" in frame.columns
        else frame.height
    )

    if frame.height == 0:
        errors.append("zero rows")
    if duplicate_ts_count:
        errors.append(f"duplicate ts rows: {duplicate_ts_count}")
    if null_price_count:
        errors.append(f"null OHLC rows: {null_price_count}")
    if null_active_contract_count:
        errors.append(f"null active_contract rows: {null_active_contract_count}")

    max_gap_minutes, large_gap_count = _intraday_gap_stats(frame, config.timeframe)
    if session_gap_summary.unexpected_intraday_gaps:
        warnings.append(f"unexpected intraday gaps: {session_gap_summary.unexpected_intraday_gaps}")
    if session_gap_summary.unknown_gaps:
        warnings.append(f"unknown timestamp gaps: {session_gap_summary.unknown_gaps}")

    for boundary in boundaries:
        if boundary.warning:
            warnings.append(f"{boundary.old_contract}->{boundary.new_contract}: {boundary.warning}")

    status = (
        NEAREST_QUALITY_REJECTED
        if errors
        else (NEAREST_QUALITY_WARNING if warnings else NEAREST_QUALITY_OK)
    )
    contracts_used = frame.get_column("active_contract").unique(maintain_order=True).to_list()
    roll_boundaries = [boundary.model_dump(mode="json") for boundary in boundaries]

    return NearestContractQuality(
        continuous_symbol=config.continuous_symbol,
        timeframe=config.timeframe,
        adjustment_mode=config.adjustment_mode,
        dataset_type=NEAREST_DATASET_TYPE,
        row_count=frame.height,
        min_ts=frame.get_column("ts").min() if frame.height else None,
        max_ts=frame.get_column("ts").max() if frame.height else None,
        contract_count=contract_count,
        active_contract_count=len(contracts_used),
        roll_count=len(boundaries),
        duplicate_ts_count=duplicate_ts_count,
        null_price_count=null_price_count,
        null_active_contract_count=null_active_contract_count,
        max_gap_minutes=max_gap_minutes,
        large_gap_count=large_gap_count,
        **session_summary_quality_fields(session_gap_summary),
        roll_boundaries=roll_boundaries,
        contracts_used=[str(contract) for contract in contracts_used],
        contracts_skipped=contracts_skipped,
        status=status,
        warnings=warnings,
        errors=errors,
    )


def _coverage_frame(
    contracts: list[FuturesContract],
    active_frame: pl.DataFrame,
    candles_by_contract: dict[str, pl.DataFrame],
    skipped: list[str],
) -> pl.DataFrame:
    active_stats = {
        str(row["active_contract"]): row
        for row in active_frame.group_by("active_contract", maintain_order=True)
        .agg(
            pl.len().alias("active_row_count"),
            pl.col("ts").min().alias("first_active_ts"),
            pl.col("ts").max().alias("last_active_ts"),
        )
        .iter_rows(named=True)
    }
    skipped_by_contract: dict[str, str] = {}
    for item in skipped:
        contract = item.split(":", maxsplit=1)[0]
        skipped_by_contract[contract] = item

    rows: list[dict[str, Any]] = []
    for contract in contracts:
        frame = candles_by_contract.get(contract.secid)
        min_ts, max_ts = _frame_ts_min_max(frame) if frame is not None else (None, None)
        stats = active_stats.get(contract.secid, {})
        rows.append(
            {
                "contract": contract.secid,
                "board": contract.board,
                "assetcode": contract.assetcode,
                "expiration_date": contract.expiration_date,
                "last_trade_date": contract.last_trade_date,
                "source_row_count": frame.height if frame is not None else 0,
                "source_min_ts": _as_datetime(_datetime_like(min_ts))
                if min_ts is not None
                else None,
                "source_max_ts": _as_datetime(_datetime_like(max_ts))
                if max_ts is not None
                else None,
                "used": contract.secid in active_stats,
                "active_row_count": int(stats.get("active_row_count", 0) or 0),
                "first_active_ts": _as_datetime(_datetime_like(stats["first_active_ts"]))
                if stats.get("first_active_ts") is not None
                else None,
                "last_active_ts": _as_datetime(_datetime_like(stats["last_active_ts"]))
                if stats.get("last_active_ts") is not None
                else None,
                "skipped_reason": skipped_by_contract.get(contract.secid),
            }
        )
    return pl.DataFrame(rows)


def _dataset_output_path(config: NearestContractBuildConfig) -> Path:
    return (
        config.output_root
        / "derived"
        / NEAREST_FUTURES_DATASET_DIR
        / f"exchange={config.exchange}"
        / f"engine={config.engine}"
        / f"market={config.market}"
        / f"continuous_symbol={config.continuous_symbol}"
        / f"timeframe={config.timeframe}"
        / f"adjustment_mode={config.adjustment_mode}"
        / "part-000.parquet"
    )


def _reports_dir(config: NearestContractBuildConfig) -> Path:
    base = (
        config.reports_root if config.reports_root is not None else config.output_root / "reports"
    )
    return base / NEAREST_FUTURES_DATASET_DIR / config.continuous_symbol


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_output(frame: pl.DataFrame, path: Path, *, output_root: Path, overwrite: bool) -> None:
    normalized_candles = (output_root / "normalized" / "candles").resolve()
    resolved_path = path.resolve()
    if normalized_candles in resolved_path.parents:
        raise InputDataError(f"Refusing to write derived output into adapter path: {path}")
    if path.exists() and not overwrite:
        raise InputDataError(f"Output already exists; pass overwrite=True to replace: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)
