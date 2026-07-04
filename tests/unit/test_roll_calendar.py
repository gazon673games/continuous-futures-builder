from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.exceptions import RollCalendarError
from continuous_futures_builder.models import FuturesContract
from continuous_futures_builder.roll_calendar import build_fixed_days_roll_calendar


def _candles(symbol: str, dates: list[date]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "source": ["fixture"] * len(dates),
            "exchange": ["MOEX"] * len(dates),
            "engine": ["futures"] * len(dates),
            "market": ["forts"] * len(dates),
            "board": ["RFUD"] * len(dates),
            "symbol": [symbol] * len(dates),
            "timeframe": ["1d"] * len(dates),
            "ts": dates,
            "open": [1.0] * len(dates),
            "high": [1.0] * len(dates),
            "low": [1.0] * len(dates),
            "close": [1.0] * len(dates),
            "volume": [1] * len(dates),
            "value": [1.0] * len(dates),
            "currency": ["RUB"] * len(dates),
        }
    )


def test_fixed_days_before_expiration_roll_calendar_from_fixture(fixture_root):
    builder = ContinuousFuturesBuilder(fixture_root)
    chain = builder.build_chain(metadata_snapshot_ts="20240101T000000Z", query="CR")
    from continuous_futures_builder.models import ContinuousFuturesBuildConfig

    build_config = ContinuousFuturesBuildConfig(
        data_root=fixture_root,
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
    )
    candles = builder._load_candles(build_config, chain.contracts)  # noqa: SLF001
    calendar = build_fixed_days_roll_calendar(chain.contracts, candles, roll_days_before=3)

    assert [event.roll_date for event in calendar.events] == [
        date(2024, 3, 12),
        date(2024, 6, 12),
        date(2024, 9, 11),
    ]
    assert calendar.warnings == []


def test_roll_fallback_when_fewer_trading_days_exist():
    contracts = [
        FuturesContract(secid="A", board="RFUD", expiration_date=date(2024, 3, 15)),
        FuturesContract(secid="B", board="RFUD", expiration_date=date(2024, 6, 17)),
    ]
    candles = {
        "A": _candles("A", [date(2024, 3, 14)]),
        "B": _candles("B", [date(2024, 3, 14), date(2024, 3, 15)]),
    }

    calendar = build_fixed_days_roll_calendar(contracts, candles, roll_days_before=3)

    assert calendar.events[0].roll_date == date(2024, 3, 14)
    assert "only 1 trading day" in calendar.warnings[0]


def test_roll_error_when_next_contract_has_no_data_after_roll(temp_data_root):
    builder = ContinuousFuturesBuilder(temp_data_root)

    with pytest.raises(RollCalendarError, match="no candle data on or after"):
        builder.build(
            metadata_snapshot_ts="20240102T000000Z",
            query="MX",
            roll_days_before=3,
        )
