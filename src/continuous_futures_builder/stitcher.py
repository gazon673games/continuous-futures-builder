from __future__ import annotations

from datetime import date

import polars as pl

from continuous_futures_builder.constants import (
    ADJUSTMENT_UNADJUSTED,
    DERIVED_SOURCE,
    OUTPUT_COLUMNS,
)
from continuous_futures_builder.exceptions import InputDataError
from continuous_futures_builder.models import (
    ContinuousFuturesBuildConfig,
    FuturesContract,
    RollEvent,
)
from continuous_futures_builder.schemas import validate_continuous_schema


def _segment_bounds(
    segment_index: int, events: list[RollEvent]
) -> tuple[date | None, date | None, RollEvent | None]:
    start = None if segment_index == 0 else events[segment_index - 1].roll_date
    end = events[segment_index].roll_date if segment_index < len(events) else None
    event = None if segment_index == 0 else events[segment_index - 1]
    return start, end, event


def _filter_segment(candles: pl.DataFrame, start: date | None, end: date | None) -> pl.DataFrame:
    frame = candles.with_columns(pl.col("ts").cast(pl.Date).alias("__ts_date"))
    if start is not None:
        frame = frame.filter(pl.col("__ts_date") >= start)
    if end is not None:
        frame = frame.filter(pl.col("__ts_date") < end)
    return frame.drop("__ts_date").sort("ts")


def stitch_unadjusted(
    config: ContinuousFuturesBuildConfig,
    contracts: list[FuturesContract],
    roll_events: list[RollEvent],
    candles_by_contract: dict[str, pl.DataFrame],
) -> pl.DataFrame:
    segments: list[pl.DataFrame] = []

    for index, contract in enumerate(contracts):
        start, end, event = _segment_bounds(index, roll_events)
        candles = candles_by_contract.get(contract.secid)
        if candles is None:
            raise InputDataError(f"Missing candle data for contract {contract.secid}")

        segment = _filter_segment(candles, start, end)
        if segment.height == 0:
            raise InputDataError(
                f"Contract {contract.secid} has no candles in active segment "
                f"start={start} end={end}"
            )

        roll_id = f"segment_000_{contract.secid}" if event is None else event.roll_id
        roll_date = None if event is None else event.roll_date
        segment = segment.with_row_index("__segment_row").with_columns(
            pl.lit(DERIVED_SOURCE).alias("source"),
            pl.lit(config.exchange).alias("exchange"),
            pl.lit(config.engine).alias("engine"),
            pl.lit(config.market).alias("market"),
            pl.lit(config.continuous_symbol).alias("continuous_symbol"),
            pl.lit(config.timeframe).alias("timeframe"),
            pl.lit(contract.secid).alias("active_contract"),
            pl.lit(contract.board).alias("active_board"),
            pl.lit(contract.secid).alias("source_contract_symbol"),
            pl.lit(roll_id).alias("roll_id"),
            (pl.col("__segment_row") == 0 if event is not None else pl.lit(False)).alias(
                "roll_flag"
            ),
            pl.lit(roll_date, dtype=pl.Date).alias("roll_date"),
            pl.lit(ADJUSTMENT_UNADJUSTED).alias("adjustment_mode"),
            pl.lit(0.0).alias("adjustment_factor"),
        )
        segments.append(segment.select(OUTPUT_COLUMNS))

    output = pl.concat(segments, how="vertical").sort("ts")
    validate_continuous_schema(output)
    return output
