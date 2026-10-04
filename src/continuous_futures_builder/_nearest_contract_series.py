from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from continuous_futures_builder._nearest_contract_models import (
    NEAREST_DATASET_TYPE,
    NEAREST_OUTPUT_COLUMNS,
    NearestContractBuildConfig,
    NearestRollBoundary,
    _date_from_value,
    _datetime_like,
)
from continuous_futures_builder._nearest_contract_rolls import (
    _minutes_between,
    _price_gap_pct,
    _roll_warning,
    _row_first_on_or_after,
    _row_last_before,
    _timeframe_minutes,
)
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
from continuous_futures_builder.models import FuturesContract


def _build_roll_calendar(
    contracts: list[FuturesContract],
    candles_by_contract: dict[str, pl.DataFrame],
    roll_days_before_expiration: int,
) -> tuple[list[NearestRollBoundary], list[FuturesContract], list[str]]:
    if not contracts:
        raise ContractChainError("Nearest-contract chain is empty")

    latest_dates = []
    for frame in candles_by_contract.values():
        max_ts = frame.get_column("ts").max() if frame.height else None
        if max_ts is not None:
            latest_dates.append(_date_from_value(_datetime_like(max_ts)))
    if not latest_dates:
        raise InputDataError("No candle rows found for nearest-contract chain")
    global_max_date = max(latest_dates)

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
