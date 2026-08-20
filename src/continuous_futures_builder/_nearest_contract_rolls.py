from __future__ import annotations

from datetime import date, datetime
from typing import Any

import polars as pl

from continuous_futures_builder._nearest_contract_models import (
    LARGE_ROLL_GAP_PERCENT,
    SOURCE_COVERAGE_GAP_WARNING_MINUTES,
    NearestContractBuildConfig,
    NearestRollBoundary,
    _as_datetime,
    _norm,
)
from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.exceptions import (
    ContractChainError,
    InputDataError,
)
from continuous_futures_builder.models import ContractChainResult, FuturesContract


def _timeframe_minutes(timeframe: str) -> int:
    value = timeframe.strip().lower()
    if value.endswith("m") and value[:-1].isdigit():
        return int(value[:-1])
    if value.endswith("min") and value[:-3].isdigit():
        return int(value[:-3])
    if value in {"1d", "d"}:
        return 24 * 60
    raise InputDataError(f"Unsupported timeframe for gap diagnostics: {timeframe}")


def _matches_asset_code(contract: FuturesContract, asset_code: str | None) -> bool:
    if not asset_code:
        return True
    contract_asset = _norm(contract.assetcode or "")
    requested = _norm(asset_code)
    return contract_asset == requested or contract_asset.startswith(requested)


def _latest_snapshot(builder: ContinuousFuturesBuilder, config: NearestContractBuildConfig) -> str:
    snapshots = builder.list_metadata_snapshots(
        exchange=config.exchange,
        engine=config.engine,
        market=config.market,
    )
    if not snapshots:
        raise InputDataError(
            f"No metadata snapshots found for {config.exchange}/{config.engine}/{config.market}"
        )
    return snapshots[-1]


def _raise_for_missing_expiration_metadata(chain: ContractChainResult) -> None:
    missing = [
        skipped
        for skipped in chain.skipped
        if "missing both expiration_date and last_trade_date" in skipped.reason
        or "invalid expiration metadata" in skipped.reason
    ]
    if not missing:
        return
    details = "; ".join(
        f"{skipped.secid or '<unknown>'}@{skipped.board or '<unknown>'}: {skipped.reason}"
        for skipped in missing
    )
    raise ContractChainError(f"Nearest-contract build rejected due to missing metadata: {details}")


def _frame_ts_min_max(frame: pl.DataFrame) -> tuple[date | datetime | None, date | datetime | None]:
    if frame.height == 0:
        return None, None
    return frame.get_column("ts").min(), frame.get_column("ts").max()


def _row_last_before(frame: pl.DataFrame, target_date: date) -> dict[str, Any] | None:
    rows = (
        frame.with_columns(pl.col("ts").cast(pl.Date).alias("__ts_date"))
        .filter(pl.col("__ts_date") < target_date)
        .sort("ts")
        .tail(1)
        .drop("__ts_date")
    )
    if rows.height == 0:
        return None
    return rows.row(0, named=True)


def _row_first_on_or_after(frame: pl.DataFrame, target_date: date) -> dict[str, Any] | None:
    rows = (
        frame.with_columns(pl.col("ts").cast(pl.Date).alias("__ts_date"))
        .filter(pl.col("__ts_date") >= target_date)
        .sort("ts")
        .head(1)
        .drop("__ts_date")
    )
    if rows.height == 0:
        return None
    return rows.row(0, named=True)


def _minutes_between(start: date | datetime | None, end: date | datetime | None) -> float | None:
    start_dt = _as_datetime(start)
    end_dt = _as_datetime(end)
    if start_dt is None or end_dt is None:
        return None
    return (end_dt - start_dt).total_seconds() / 60.0


def _price_gap_pct(old_close: float | None, price_gap: float | None) -> float | None:
    if old_close in (None, 0.0) or price_gap is None:
        return None
    return (price_gap / old_close) * 100.0


def _roll_warning(boundary: NearestRollBoundary) -> str | None:
    warnings: list[str] = []
    if (
        boundary.gap_minutes_between_contracts is not None
        and boundary.gap_minutes_between_contracts > SOURCE_COVERAGE_GAP_WARNING_MINUTES
    ):
        warnings.append(
            f"source coverage gap: {boundary.gap_minutes_between_contracts:.0f} minutes"
        )
    if boundary.price_gap_pct is not None and abs(boundary.price_gap_pct) >= LARGE_ROLL_GAP_PERCENT:
        warnings.append(f"large roll price gap: {boundary.price_gap_pct:.2f}%")
    return "; ".join(warnings) if warnings else None


def _roll_calendar_frame(boundaries: list[NearestRollBoundary]) -> pl.DataFrame:
    schema = {
        "roll_id": pl.Utf8,
        "old_contract": pl.Utf8,
        "new_contract": pl.Utf8,
        "roll_date": pl.Date,
        "effective_roll_ts": pl.Datetime,
        "old_expiration_date": pl.Date,
        "new_expiration_date": pl.Date,
        "old_last_ts": pl.Datetime,
        "new_first_ts": pl.Datetime,
        "gap_minutes_between_contracts": pl.Float64,
        "old_close_near_roll": pl.Float64,
        "new_close_near_roll": pl.Float64,
        "price_gap": pl.Float64,
        "price_gap_pct": pl.Float64,
        "warning": pl.Utf8,
    }
    if not boundaries:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(
        [
            {
                **boundary.model_dump(),
                "effective_roll_ts": _as_datetime(boundary.effective_roll_ts),
                "old_last_ts": _as_datetime(boundary.old_last_ts),
                "new_first_ts": _as_datetime(boundary.new_first_ts),
            }
            for boundary in boundaries
        ],
        schema=schema,
    )
