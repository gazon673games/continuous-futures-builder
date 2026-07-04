from __future__ import annotations

from datetime import date

import polars as pl

from continuous_futures_builder.constants import (
    ADJUSTMENT_BACK_ADJUSTED,
    OUTPUT_COLUMNS,
    PRICE_COLUMNS,
)
from continuous_futures_builder.exceptions import AdjustmentError
from continuous_futures_builder.models import RollEvent
from continuous_futures_builder.schemas import validate_continuous_schema


def _close_on_roll_date(candles: pl.DataFrame, roll_date: date, contract: str) -> float:
    matches = candles.filter(pl.col("ts").cast(pl.Date) == roll_date)
    if matches.height == 0:
        raise AdjustmentError(f"Missing close for {contract} on roll date {roll_date}")
    return float(matches.sort("ts").get_column("close")[0])


def _previous_close(candles: pl.DataFrame, roll_date: date, contract: str) -> float:
    prior = candles.filter(pl.col("ts").cast(pl.Date) < roll_date).sort("ts")
    if prior.height == 0:
        raise AdjustmentError(f"Missing previous close for {contract} before roll date {roll_date}")
    return float(prior.get_column("close")[-1])


def calculate_roll_gaps(
    roll_events: list[RollEvent], candles_by_contract: dict[str, pl.DataFrame]
) -> list[tuple[date, float]]:
    gaps: list[tuple[date, float]] = []
    for event in roll_events:
        current = candles_by_contract.get(event.from_contract)
        next_contract = candles_by_contract.get(event.to_contract)
        if current is None:
            raise AdjustmentError(f"Missing candles for {event.from_contract}")
        if next_contract is None:
            raise AdjustmentError(f"Missing candles for {event.to_contract}")

        old_close = _previous_close(current, event.roll_date, event.from_contract)
        new_close = _close_on_roll_date(next_contract, event.roll_date, event.to_contract)
        gaps.append((event.roll_date, new_close - old_close))
    return gaps


def apply_additive_back_adjustment(
    unadjusted: pl.DataFrame,
    roll_events: list[RollEvent],
    candles_by_contract: dict[str, pl.DataFrame],
) -> pl.DataFrame:
    gaps = calculate_roll_gaps(roll_events, candles_by_contract)
    frame = unadjusted.with_columns(pl.col("ts").cast(pl.Date).alias("__ts_date"))

    factor_expr = pl.lit(0.0)
    for roll_date, gap in gaps:
        factor_expr = factor_expr + pl.when(pl.col("__ts_date") < roll_date).then(
            pl.lit(gap)
        ).otherwise(pl.lit(0.0))

    frame = frame.with_columns(factor_expr.alias("adjustment_factor"))
    frame = frame.with_columns(
        [
            (pl.col(column).cast(pl.Float64) + pl.col("adjustment_factor")).alias(column)
            for column in PRICE_COLUMNS
        ]
        + [pl.lit(ADJUSTMENT_BACK_ADJUSTED).alias("adjustment_mode")]
    ).drop("__ts_date")
    output = frame.select(OUTPUT_COLUMNS)
    validate_continuous_schema(output)
    return output
