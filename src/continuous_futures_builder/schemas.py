from __future__ import annotations

import polars as pl

from continuous_futures_builder.constants import (
    INPUT_CANDLE_COLUMNS,
    INPUT_METADATA_COLUMNS,
    OUTPUT_COLUMNS,
    SUPPORTED_ADJUSTMENT_MODES,
)
from continuous_futures_builder.exceptions import ContinuousSeriesValidationError, InputDataError


def require_columns(
    frame: pl.DataFrame | pl.LazyFrame, required: tuple[str, ...], context: str
) -> None:
    columns = frame.collect_schema().names()
    missing = [column for column in required if column not in columns]
    if missing:
        raise InputDataError(f"{context} is missing required columns: {', '.join(missing)}")


def validate_candle_schema(
    frame: pl.DataFrame | pl.LazyFrame, context: str = "candle data"
) -> None:
    require_columns(frame, INPUT_CANDLE_COLUMNS, context)


def validate_metadata_schema(frame: pl.DataFrame, context: str = "metadata snapshot") -> None:
    require_columns(frame, INPUT_METADATA_COLUMNS, context)


def validate_continuous_schema(frame: pl.DataFrame) -> None:
    missing = [column for column in OUTPUT_COLUMNS if column not in frame.columns]
    if missing:
        raise ContinuousSeriesValidationError(
            f"continuous series is missing required columns: {', '.join(missing)}"
        )

    if frame.height == 0:
        raise ContinuousSeriesValidationError("continuous series is empty")

    if frame["active_contract"].null_count() > 0:
        raise ContinuousSeriesValidationError("active_contract must be non-null")

    if frame["source_contract_symbol"].null_count() > 0:
        raise ContinuousSeriesValidationError("source_contract_symbol must be non-null")

    modes = set(frame["adjustment_mode"].unique().to_list())
    invalid_modes = sorted(modes - SUPPORTED_ADJUSTMENT_MODES)
    if invalid_modes:
        raise ContinuousSeriesValidationError(
            f"invalid adjustment_mode values: {', '.join(invalid_modes)}"
        )

    if not frame.schema["adjustment_factor"].is_numeric():
        raise ContinuousSeriesValidationError("adjustment_factor must be numeric")

    if not frame.select(pl.col("ts").is_sorted()).item():
        raise ContinuousSeriesValidationError("ts must be sorted ascending")

    if frame["ts"].n_unique() != frame.height:
        raise ContinuousSeriesValidationError("duplicate ts rows are not allowed")
