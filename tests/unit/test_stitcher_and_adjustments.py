from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from continuous_futures_builder.adjustments import (
    apply_additive_back_adjustment,
    calculate_roll_gaps,
)
from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.exceptions import ContinuousSeriesValidationError
from continuous_futures_builder.models import ContinuousFuturesBuildConfig, RollEvent
from continuous_futures_builder.roll_calendar import build_fixed_days_roll_calendar
from continuous_futures_builder.schemas import validate_continuous_schema
from continuous_futures_builder.stitcher import stitch_unadjusted


def _fixture_parts(fixture_root):
    builder = ContinuousFuturesBuilder(fixture_root)
    config = ContinuousFuturesBuildConfig(
        data_root=fixture_root,
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
    )
    chain = builder.build_chain(metadata_snapshot_ts="20240101T000000Z", query="CR")
    candles = builder._load_candles(config, chain.contracts)  # noqa: SLF001
    calendar = build_fixed_days_roll_calendar(chain.contracts, candles, roll_days_before=3)
    return config, chain, candles, calendar


def test_unadjusted_stitching_and_roll_flags(fixture_root):
    config, chain, candles, calendar = _fixture_parts(fixture_root)

    frame = stitch_unadjusted(config, chain.contracts, calendar.events, candles)

    assert frame.get_column("ts").to_list() == [
        date(2024, 3, 8),
        date(2024, 3, 11),
        date(2024, 3, 12),
        date(2024, 3, 13),
        date(2024, 6, 10),
        date(2024, 6, 11),
        date(2024, 6, 12),
        date(2024, 6, 13),
        date(2024, 9, 9),
        date(2024, 9, 10),
        date(2024, 9, 11),
        date(2024, 9, 12),
        date(2024, 12, 12),
        date(2024, 12, 13),
    ]
    assert frame.filter(frame["roll_flag"]).get_column("ts").to_list() == [
        date(2024, 3, 12),
        date(2024, 6, 12),
        date(2024, 9, 11),
    ]
    assert frame.filter(frame["ts"] == date(2024, 3, 12)).get_column("active_contract")[0] == "CRM4"
    assert frame.get_column("adjustment_mode").unique().to_list() == ["unadjusted"]


def test_back_adjustment_factor_calculation_and_continuity(fixture_root):
    config, chain, candles, calendar = _fixture_parts(fixture_root)
    unadjusted = stitch_unadjusted(config, chain.contracts, calendar.events, candles)

    adjusted = apply_additive_back_adjustment(unadjusted, calendar.events, candles)

    factors = {
        row["ts"]: row["adjustment_factor"]
        for row in adjusted.select("ts", "adjustment_factor").iter_rows(named=True)
    }
    assert factors[date(2024, 3, 11)] == 18.0
    assert factors[date(2024, 3, 12)] == 13.0
    assert factors[date(2024, 6, 12)] == 4.0
    assert factors[date(2024, 9, 11)] == 0.0

    closes = {
        row["ts"]: row["close"] for row in adjusted.select("ts", "close").iter_rows(named=True)
    }
    assert closes[date(2024, 3, 11)] == closes[date(2024, 3, 12)]
    assert closes[date(2024, 6, 11)] == closes[date(2024, 6, 12)]
    assert closes[date(2024, 9, 10)] == closes[date(2024, 9, 11)]
    assert adjusted.get_column("adjustment_mode").unique().to_list() == ["back_adjusted"]


def test_roll_gap_uses_next_close_after_roll_date_when_exact_date_is_missing():
    event = RollEvent(
        roll_id="A_TO_B_20240110",
        from_contract="AAH4",
        to_contract="AAM4",
        roll_date=date(2024, 1, 10),
        from_expiration_date=date(2024, 1, 15),
        to_expiration_date=date(2024, 2, 15),
    )
    candles = {
        "AAH4": pl.DataFrame(
            {
                "ts": [date(2024, 1, 8), date(2024, 1, 9)],
                "close": [100.0, 101.0],
            }
        ),
        "AAM4": pl.DataFrame(
            {
                "ts": [date(2024, 1, 11)],
                "close": [106.0],
            }
        ),
    }

    assert calculate_roll_gaps([event], candles) == [(date(2024, 1, 10), 5.0)]


def test_output_validation_failure_on_duplicate_ts(fixture_root):
    config, chain, candles, calendar = _fixture_parts(fixture_root)
    frame = stitch_unadjusted(config, chain.contracts, calendar.events, candles)
    duplicate = frame.vstack(frame.head(1)).sort("ts")

    with pytest.raises(ContinuousSeriesValidationError, match="duplicate ts"):
        validate_continuous_schema(duplicate)
