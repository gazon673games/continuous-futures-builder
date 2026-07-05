from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, field_validator

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.constants import (
    ADJUSTMENT_UNADJUSTED,
    DERIVED_SOURCE,
    PRICE_COLUMNS,
)
from continuous_futures_builder.exceptions import (
    ContinuousSeriesValidationError,
    ContractChainError,
    InputDataError,
)
from continuous_futures_builder.models import ContractChainResult, FuturesContract
from continuous_futures_builder.nearest_gap_diagnostics import (
    IntradaySessionModel,
    SessionGapSummary,
    diagnose_session_gaps,
    session_summary_quality_fields,
    write_session_gap_reports,
)
from continuous_futures_builder.prefixes import normalize_secid_prefixes

NEAREST_DATASET_TYPE = "nearest_contract"
NEAREST_FUTURES_DATASET_DIR = "nearest_contract_futures"
NEAREST_QUALITY_OK = "ok"
NEAREST_QUALITY_WARNING = "warning"
NEAREST_QUALITY_REJECTED = "rejected"
LARGE_ROLL_GAP_PERCENT = 5.0
SOURCE_COVERAGE_GAP_WARNING_MINUTES = 3 * 24 * 60

NEAREST_OUTPUT_COLUMNS = (
    "source",
    "exchange",
    "engine",
    "market",
    "board",
    "symbol",
    "continuous_symbol",
    "timeframe",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "currency",
    "active_contract",
    "active_board",
    "source_contract_symbol",
    "roll_id",
    "roll_flag",
    "is_roll_boundary",
    "roll_date",
    "adjustment_mode",
    "adjustment_factor",
    "dataset_type",
)


class NearestContractBuildConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    data_root: Path
    output_root: Path
    exchange: str = "MOEX"
    engine: str = "futures"
    market: str = "forts"
    board: str = "RFUD"
    continuous_symbol: str = "CNYRUB_NEAREST_5M"
    timeframe: str = "5m"
    metadata_snapshot_ts: str | None = None
    query: str | None = None
    asset_code: str | None = "CNY"
    secid_prefixes: list[str] = Field(default_factory=list)
    include_secid: list[str] = Field(default_factory=list)
    exclude_secid: list[str] = Field(default_factory=list)
    roll_days_before_expiration: int = 3
    adjustment_mode: str = ADJUSTMENT_UNADJUSTED
    overwrite: bool = False
    reports_root: Path | None = None
    session_model: IntradaySessionModel | None = None

    @field_validator("data_root", "output_root", "reports_root", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        return Path(value)

    @field_validator("roll_days_before_expiration")
    @classmethod
    def _validate_roll_days_before_expiration(cls, value: int) -> int:
        if value < 0:
            raise ValueError("roll_days_before_expiration must be >= 0")
        return value

    @field_validator("adjustment_mode")
    @classmethod
    def _validate_adjustment_mode(cls, value: str) -> str:
        if value != ADJUSTMENT_UNADJUSTED:
            raise ValueError("nearest-contract intraday datasets support only unadjusted prices")
        return value


class NearestRollBoundary(BaseModel):
    roll_id: str
    old_contract: str
    new_contract: str
    roll_date: date
    effective_roll_ts: date | datetime
    old_expiration_date: date
    new_expiration_date: date
    old_last_ts: date | datetime | None = None
    new_first_ts: date | datetime | None = None
    gap_minutes_between_contracts: float | None = None
    old_close_near_roll: float | None = None
    new_close_near_roll: float | None = None
    price_gap: float | None = None
    price_gap_pct: float | None = None
    warning: str | None = None


class NearestContractQuality(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    continuous_symbol: str
    timeframe: str
    adjustment_mode: str
    dataset_type: str
    row_count: int
    min_ts: date | datetime | None = None
    max_ts: date | datetime | None = None
    contract_count: int
    active_contract_count: int
    roll_count: int
    duplicate_ts_count: int
    null_price_count: int
    null_active_contract_count: int
    max_gap_minutes: float | None = None
    large_gap_count: int
    total_timestamp_gaps: int = 0
    expected_session_break_count: int = 0
    expected_overnight_or_calendar_break_count: int = 0
    roll_transition_gap_count: int = 0
    unexpected_intraday_gap_count: int = 0
    unknown_gap_count: int = 0
    max_unexpected_intraday_gap_minutes: float | None = None
    top_unexpected_gaps: list[dict[str, Any]] = Field(default_factory=list)
    session_model: dict[str, Any] | None = None
    roll_boundaries: list[dict[str, Any]] = Field(default_factory=list)
    contracts_used: list[str] = Field(default_factory=list)
    contracts_skipped: list[str] = Field(default_factory=list)
    status: str
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class NearestContractBuildResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    output_path: Path
    reports_dir: Path
    build_summary_json: Path
    roll_calendar_csv: Path
    roll_calendar_parquet: Path
    session_gaps_csv: Path
    session_gaps_parquet: Path
    session_gap_summary_json: Path
    quality_json: Path
    contract_coverage_csv: Path
    contract_coverage_parquet: Path
    continuous_symbol: str
    timeframe: str
    metadata_snapshot_ts: str
    adjustment_mode: str
    dataset_type: str
    row_count: int
    min_ts: date | datetime | None = None
    max_ts: date | datetime | None = None
    contracts_used: list[str] = Field(default_factory=list)
    contracts_skipped: list[str] = Field(default_factory=list)
    roll_count: int
    quality_status: str
    warnings: list[str] = Field(default_factory=list)


def _norm(value: object) -> str:
    return str(value).strip().upper()


def _date_from_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise InputDataError(f"Unsupported date value: {value!r}")


def _datetime_like(value: object) -> date | datetime:
    if isinstance(value, datetime | date):
        return value
    if isinstance(value, str):
        text = value.replace("Z", "")
        if "T" in text or " " in text:
            return datetime.fromisoformat(text)
        return date.fromisoformat(text[:10])
    raise InputDataError(f"Unsupported timestamp value: {value!r}")


def _as_datetime(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)


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


def _build_roll_calendar(
    contracts: list[FuturesContract],
    candles_by_contract: dict[str, pl.DataFrame],
    roll_days_before_expiration: int,
) -> tuple[list[NearestRollBoundary], list[FuturesContract], list[str]]:
    if not contracts:
        raise ContractChainError("Nearest-contract chain is empty")

    all_max_ts = [
        _datetime_like(max_ts)
        for frame in candles_by_contract.values()
        for _, max_ts in [_frame_ts_min_max(frame)]
        if max_ts is not None
    ]
    if not all_max_ts:
        raise InputDataError("No candle rows found for nearest-contract chain")
    global_max_date = max(_date_from_value(value) for value in all_max_ts)

    active_contracts = [contracts[0]]
    boundaries: list[NearestRollBoundary] = []
    warnings: list[str] = []

    for current, next_contract in zip(contracts, contracts[1:], strict=False):
        roll_date = current.expiration_date - timedelta(days=roll_days_before_expiration)
        if roll_date > global_max_date:
            warnings.append(
                f"{current.secid}: roll_date {roll_date} is beyond available data "
                f"ending {global_max_date}; keeping {current.secid} as the last active contract"
            )
            break

        current_candles = candles_by_contract[current.secid]
        next_candles = candles_by_contract[next_contract.secid]
        old_row = _row_last_before(current_candles, roll_date)
        new_row = _row_first_on_or_after(next_candles, roll_date)
        if old_row is None:
            raise InputDataError(f"{current.secid} has no candles before roll_date {roll_date}")
        if new_row is None:
            raise InputDataError(
                f"{next_contract.secid} has no candles on or after roll_date {roll_date}"
            )

        old_ts = _datetime_like(old_row["ts"])
        new_ts = _datetime_like(new_row["ts"])
        old_close = None if old_row.get("close") is None else float(old_row["close"])
        new_close = None if new_row.get("close") is None else float(new_row["close"])
        price_gap = None if old_close is None or new_close is None else float(new_close - old_close)
        boundary = NearestRollBoundary(
            roll_id=f"{current.secid}_TO_{next_contract.secid}_{roll_date:%Y%m%d}",
            old_contract=current.secid,
            new_contract=next_contract.secid,
            roll_date=roll_date,
            effective_roll_ts=new_ts,
            old_expiration_date=current.expiration_date,
            new_expiration_date=next_contract.expiration_date,
            old_last_ts=old_ts,
            new_first_ts=new_ts,
            gap_minutes_between_contracts=_minutes_between(old_ts, new_ts),
            old_close_near_roll=old_close,
            new_close_near_roll=new_close,
            price_gap=price_gap,
            price_gap_pct=_price_gap_pct(old_close, price_gap),
        )
        boundary.warning = _roll_warning(boundary)
        active_contracts.append(next_contract)
        boundaries.append(boundary)

    return boundaries, active_contracts, warnings


def _segment_bounds(
    index: int,
    boundaries: list[NearestRollBoundary],
) -> tuple[date | None, date | None, NearestRollBoundary | None]:
    start = None if index == 0 else boundaries[index - 1].roll_date
    end = boundaries[index].roll_date if index < len(boundaries) else None
    incoming = None if index == 0 else boundaries[index - 1]
    return start, end, incoming


def _filter_segment(frame: pl.DataFrame, start: date | None, end: date | None) -> pl.DataFrame:
    segment = frame.with_columns(pl.col("ts").cast(pl.Date).alias("__ts_date"))
    if start is not None:
        segment = segment.filter(pl.col("__ts_date") >= start)
    if end is not None:
        segment = segment.filter(pl.col("__ts_date") < end)
    return segment.drop("__ts_date").sort("ts")


def _stitch_nearest_contracts(
    config: NearestContractBuildConfig,
    contracts: list[FuturesContract],
    boundaries: list[NearestRollBoundary],
    candles_by_contract: dict[str, pl.DataFrame],
) -> pl.DataFrame:
    segments: list[pl.DataFrame] = []

    for index, contract in enumerate(contracts):
        start, end, incoming = _segment_bounds(index, boundaries)
        segment = _filter_segment(candles_by_contract[contract.secid], start, end)
        if segment.height == 0:
            raise InputDataError(
                f"{contract.secid} has no candles in active segment start={start} end={end}"
            )

        roll_id = f"segment_000_{contract.secid}" if incoming is None else incoming.roll_id
        roll_date = None if incoming is None else incoming.roll_date
        segment = segment.with_row_index("__segment_row").with_columns(
            pl.lit(DERIVED_SOURCE).alias("source"),
            pl.lit(config.exchange).alias("exchange"),
            pl.lit(config.engine).alias("engine"),
            pl.lit(config.market).alias("market"),
            pl.lit(config.board).alias("board"),
            pl.lit(config.continuous_symbol).alias("symbol"),
            pl.lit(config.continuous_symbol).alias("continuous_symbol"),
            pl.lit(config.timeframe).alias("timeframe"),
            pl.lit(contract.secid).alias("active_contract"),
            pl.lit(contract.board).alias("active_board"),
            pl.lit(contract.secid).alias("source_contract_symbol"),
            pl.lit(roll_id).alias("roll_id"),
            (pl.col("__segment_row") == 0 if incoming is not None else pl.lit(False)).alias(
                "roll_flag"
            ),
            (pl.col("__segment_row") == 0 if incoming is not None else pl.lit(False)).alias(
                "is_roll_boundary"
            ),
            pl.lit(roll_date, dtype=pl.Date).alias("roll_date"),
            pl.lit(ADJUSTMENT_UNADJUSTED).alias("adjustment_mode"),
            pl.lit(0.0).alias("adjustment_factor"),
            pl.lit(NEAREST_DATASET_TYPE).alias("dataset_type"),
        )
        segments.append(segment.select(NEAREST_OUTPUT_COLUMNS))

    output = pl.concat(segments, how="vertical").sort("ts")
    validate_nearest_contract_schema(output)
    return output


def _duplicate_ts_count(frame: pl.DataFrame) -> int:
    if "ts" not in frame.columns or frame.height == 0:
        return 0
    return frame.height - frame.get_column("ts").n_unique()


def _null_price_count(frame: pl.DataFrame) -> int:
    missing = [column for column in PRICE_COLUMNS if column not in frame.columns]
    if missing:
        return frame.height
    return int(
        frame.select(
            pl.any_horizontal([pl.col(column).is_null() for column in PRICE_COLUMNS]).sum()
        ).item()
    )


def _intraday_gap_stats(frame: pl.DataFrame, timeframe: str) -> tuple[float | None, int]:
    if "ts" not in frame.columns or frame.height < 2:
        return None, 0
    expected_minutes = _timeframe_minutes(timeframe)
    large_gap_threshold = expected_minutes * 3
    timestamps = [_datetime_like(value) for value in frame.sort("ts").get_column("ts").to_list()]
    intraday_gaps: list[float] = []
    for previous, current in zip(timestamps, timestamps[1:], strict=False):
        if _date_from_value(previous) != _date_from_value(current):
            continue
        gap = _minutes_between(previous, current)
        if gap is not None:
            intraday_gaps.append(gap)
    if not intraday_gaps:
        return None, 0
    return max(intraday_gaps), sum(gap > large_gap_threshold for gap in intraday_gaps)


def validate_nearest_contract_schema(frame: pl.DataFrame) -> None:
    missing = [column for column in NEAREST_OUTPUT_COLUMNS if column not in frame.columns]
    if missing:
        raise ContinuousSeriesValidationError(
            "nearest-contract series is missing required columns: " + ", ".join(missing)
        )
    if frame.height == 0:
        raise ContinuousSeriesValidationError("nearest-contract series is empty")
    if not frame.select(pl.col("ts").is_sorted()).item():
        raise ContinuousSeriesValidationError("ts must be sorted ascending")
    duplicate_ts_count = _duplicate_ts_count(frame)
    if duplicate_ts_count:
        raise ContinuousSeriesValidationError("duplicate ts rows are not allowed")
    if _null_price_count(frame):
        raise ContinuousSeriesValidationError("OHLC prices must be non-null")
    if frame.get_column("active_contract").null_count() > 0:
        raise ContinuousSeriesValidationError("active_contract must be non-null")
    if frame.get_column("source_contract_symbol").null_count() > 0:
        raise ContinuousSeriesValidationError("source_contract_symbol must be non-null")
    dataset_types = set(frame.get_column("dataset_type").unique().to_list())
    if dataset_types != {NEAREST_DATASET_TYPE}:
        raise ContinuousSeriesValidationError(
            f"invalid dataset_type values: {', '.join(sorted(map(str, dataset_types)))}"
        )
    adjustment_modes = set(frame.get_column("adjustment_mode").unique().to_list())
    if adjustment_modes != {ADJUSTMENT_UNADJUSTED}:
        raise ContinuousSeriesValidationError(
            "nearest-contract series must use adjustment_mode=unadjusted"
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


def _save_artifacts(
    frame: pl.DataFrame,
    config: NearestContractBuildConfig,
    metadata_snapshot_ts: str,
    quality: NearestContractQuality,
    boundaries: list[NearestRollBoundary],
    coverage: pl.DataFrame,
) -> dict[str, Path]:
    output_path = _dataset_output_path(config)
    reports_dir = _reports_dir(config)
    reports_dir.mkdir(parents=True, exist_ok=True)

    _write_output(frame, output_path, output_root=config.output_root, overwrite=config.overwrite)

    roll_calendar = _roll_calendar_frame(boundaries)
    session_gap_result = write_session_gap_reports(
        frame,
        reports_dir,
        bar_minutes=_timeframe_minutes(config.timeframe),
        session_model=config.session_model,
    )
    roll_calendar_csv = reports_dir / "roll_calendar.csv"
    roll_calendar_parquet = reports_dir / "roll_calendar.parquet"
    quality_json = reports_dir / "quality.json"
    contract_coverage_csv = reports_dir / "contract_coverage.csv"
    contract_coverage_parquet = reports_dir / "contract_coverage.parquet"
    build_summary_json = reports_dir / "build_summary.json"

    roll_calendar.write_csv(roll_calendar_csv)
    roll_calendar.write_parquet(roll_calendar_parquet)
    coverage.write_csv(contract_coverage_csv)
    coverage.write_parquet(contract_coverage_parquet)
    _write_json(quality_json, quality.model_dump(mode="json"))

    summary_payload = {
        "output_path": str(output_path),
        "reports_dir": str(reports_dir),
        "metadata_snapshot_ts": metadata_snapshot_ts,
        **quality.model_dump(mode="json"),
    }
    _write_json(build_summary_json, summary_payload)

    return {
        "output_path": output_path,
        "reports_dir": reports_dir,
        "build_summary_json": build_summary_json,
        "roll_calendar_csv": roll_calendar_csv,
        "roll_calendar_parquet": roll_calendar_parquet,
        "session_gaps_csv": session_gap_result.session_gaps_csv,
        "session_gaps_parquet": session_gap_result.session_gaps_parquet,
        "session_gap_summary_json": session_gap_result.session_gap_summary_json,
        "quality_json": quality_json,
        "contract_coverage_csv": contract_coverage_csv,
        "contract_coverage_parquet": contract_coverage_parquet,
    }


class NearestContractFuturesBuilder:
    def __init__(self, data_root: str | Path, *, output_root: str | Path = "data") -> None:
        self.data_root = Path(data_root)
        self.output_root = Path(output_root)
        self.builder = ContinuousFuturesBuilder(self.data_root, output_root=self.output_root)

    def build(self, **kwargs: Any) -> NearestContractBuildResult:
        config = NearestContractBuildConfig(
            data_root=self.data_root,
            output_root=self.output_root,
            **kwargs,
        )
        return build_nearest_contract_dataset(config, builder=self.builder)


def _load_chain(
    builder: ContinuousFuturesBuilder,
    config: NearestContractBuildConfig,
) -> tuple[str, ContractChainResult, list[FuturesContract], list[str], list[str]]:
    snapshot_ts = config.metadata_snapshot_ts or _latest_snapshot(builder, config)
    prefixes = normalize_secid_prefixes(secid_prefixes=config.secid_prefixes)
    chain = builder.build_chain(
        exchange=config.exchange,
        engine=config.engine,
        market=config.market,
        metadata_snapshot_ts=snapshot_ts,
        query=config.query,
        asset_code=None,
        secid_prefixes=prefixes,
        include_secid=config.include_secid,
        exclude_secid=config.exclude_secid,
        timeframe=config.timeframe,
    )
    _raise_for_missing_expiration_metadata(chain)

    warnings = list(chain.warnings)
    skipped: list[str] = [
        f"{item.secid or '<unknown>'}: {item.reason}"
        for item in chain.skipped
        if "missing both expiration_date and last_trade_date" not in item.reason
        and "invalid expiration metadata" not in item.reason
    ]
    selected = [
        contract
        for contract in chain.contracts
        if contract.board == config.board and _matches_asset_code(contract, config.asset_code)
    ]
    for contract in chain.contracts:
        if contract not in selected:
            skipped.append(
                f"{contract.secid}: skipped by board/asset filter "
                f"(board={contract.board}, assetcode={contract.assetcode})"
            )

    selected = sorted(
        selected,
        key=lambda contract: (contract.expiration_date, contract.secid, contract.board),
    )
    if len(selected) < 2:
        raise ContractChainError(
            "Nearest-contract chain must contain at least two eligible contracts. "
            f"Found {len(selected)}."
        )
    return snapshot_ts, chain, selected, skipped, warnings


def _load_candles(
    builder: ContinuousFuturesBuilder,
    config: NearestContractBuildConfig,
    contracts: list[FuturesContract],
) -> tuple[dict[str, pl.DataFrame], list[FuturesContract], list[str]]:
    candles_by_contract: dict[str, pl.DataFrame] = {}
    loaded_contracts: list[FuturesContract] = []
    skipped: list[str] = []
    for contract in contracts:
        try:
            frame = builder.data_access.load_contract_candles(
                exchange=config.exchange,
                engine=config.engine,
                market=config.market,
                board=contract.board,
                symbol=contract.secid,
                timeframe=config.timeframe,
            )
        except InputDataError as exc:
            skipped.append(f"{contract.secid}: missing candles ({exc})")
            continue
        if frame.height == 0:
            skipped.append(f"{contract.secid}: zero candle rows")
            continue
        candles_by_contract[contract.secid] = frame.sort("ts")
        loaded_contracts.append(contract)

    if len(loaded_contracts) < 2:
        raise InputDataError(
            "Nearest-contract build needs at least two contracts with candle data. "
            f"Found {len(loaded_contracts)}."
        )
    return candles_by_contract, loaded_contracts, skipped


def build_nearest_contract_dataset(
    config: NearestContractBuildConfig,
    *,
    builder: ContinuousFuturesBuilder | None = None,
) -> NearestContractBuildResult:
    effective_builder = builder or ContinuousFuturesBuilder(
        config.data_root,
        output_root=config.output_root,
    )
    snapshot_ts, _chain, contracts, chain_skipped, chain_warnings = _load_chain(
        effective_builder, config
    )
    candles_by_contract, loaded_contracts, candle_skipped = _load_candles(
        effective_builder,
        config,
        contracts,
    )
    boundaries, active_contracts, roll_warnings = _build_roll_calendar(
        loaded_contracts,
        candles_by_contract,
        config.roll_days_before_expiration,
    )
    frame = _stitch_nearest_contracts(config, active_contracts, boundaries, candles_by_contract)
    contracts_used = frame.get_column("active_contract").unique(maintain_order=True).to_list()
    inactive_contracts = [
        f"{contract.secid}: not active before available data ends"
        for contract in loaded_contracts
        if contract.secid not in set(contracts_used)
    ]
    contracts_skipped = [*chain_skipped, *candle_skipped, *inactive_contracts]
    _session_gap_rows, session_gap_summary = diagnose_session_gaps(
        frame,
        bar_minutes=_timeframe_minutes(config.timeframe),
        session_model=config.session_model,
    )
    quality = _build_quality(
        frame,
        config,
        boundaries,
        contracts_skipped,
        preexisting_warnings=[*chain_warnings, *roll_warnings],
        contract_count=len(loaded_contracts),
        session_gap_summary=session_gap_summary,
    )
    if quality.status == NEAREST_QUALITY_REJECTED:
        raise ContinuousSeriesValidationError("; ".join(quality.errors))

    coverage = _coverage_frame(loaded_contracts, frame, candles_by_contract, contracts_skipped)
    paths = _save_artifacts(frame, config, snapshot_ts, quality, boundaries, coverage)

    return NearestContractBuildResult(
        output_path=paths["output_path"],
        reports_dir=paths["reports_dir"],
        build_summary_json=paths["build_summary_json"],
        roll_calendar_csv=paths["roll_calendar_csv"],
        roll_calendar_parquet=paths["roll_calendar_parquet"],
        session_gaps_csv=paths["session_gaps_csv"],
        session_gaps_parquet=paths["session_gaps_parquet"],
        session_gap_summary_json=paths["session_gap_summary_json"],
        quality_json=paths["quality_json"],
        contract_coverage_csv=paths["contract_coverage_csv"],
        contract_coverage_parquet=paths["contract_coverage_parquet"],
        continuous_symbol=config.continuous_symbol,
        timeframe=config.timeframe,
        metadata_snapshot_ts=snapshot_ts,
        adjustment_mode=config.adjustment_mode,
        dataset_type=NEAREST_DATASET_TYPE,
        row_count=quality.row_count,
        min_ts=quality.min_ts,
        max_ts=quality.max_ts,
        contracts_used=quality.contracts_used,
        contracts_skipped=contracts_skipped,
        roll_count=quality.roll_count,
        quality_status=quality.status,
        warnings=quality.warnings,
    )
