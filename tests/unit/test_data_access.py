from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.data_access import (
    LocalParquetDataAccess,
    OhlcvDataStoreAccess,
    create_data_access,
)
from continuous_futures_builder.exceptions import InputDataError


@dataclass
class FakeSnapshot:
    exchange: str
    engine: str
    market: str
    kind: str
    snapshot_ts: str


class FakeDataStore:
    def __init__(self, fixture_root: Path) -> None:
        self.local = LocalParquetDataAccess(fixture_root)
        self.calls: list[tuple[str, dict]] = []

    def get_metadata_snapshot(self, **kwargs) -> pl.DataFrame:
        self.calls.append(("get_metadata_snapshot", kwargs))
        return self.local.load_metadata_snapshot(
            kwargs["exchange"],
            kwargs["engine"],
            kwargs["market"],
            kwargs["snapshot_ts"],
        )

    def get_candles(self, **kwargs) -> pl.DataFrame:
        self.calls.append(("get_candles", kwargs))
        return self.local.load_contract_candles(
            kwargs["exchange"],
            kwargs["engine"],
            kwargs["market"],
            kwargs["board"],
            kwargs["symbol"],
            kwargs["timeframe"],
        )

    def list_metadata_snapshots(self, *, include_stats: bool = False) -> list[FakeSnapshot]:
        self.calls.append(("list_metadata_snapshots", {"include_stats": include_stats}))
        return [
            FakeSnapshot("MOEX", "futures", "forts", "securities", "20240101T000000Z"),
            FakeSnapshot("MOEX", "stock", "shares", "securities", "IGNORE"),
        ]


class CountingDataAccess:
    def __init__(self, fixture_root: Path) -> None:
        self.local = LocalParquetDataAccess(fixture_root)
        self.metadata_calls = 0
        self.candle_calls: list[str] = []

    def load_metadata_snapshot(
        self,
        exchange: str,
        engine: str,
        market: str,
        snapshot_ts: str,
    ) -> pl.DataFrame:
        self.metadata_calls += 1
        return self.local.load_metadata_snapshot(exchange, engine, market, snapshot_ts)

    def load_contract_candles(
        self,
        exchange: str,
        engine: str,
        market: str,
        board: str,
        symbol: str,
        timeframe: str,
    ) -> pl.DataFrame:
        self.candle_calls.append(symbol)
        return self.local.load_contract_candles(exchange, engine, market, board, symbol, timeframe)

    def list_metadata_snapshots(
        self,
        exchange: str,
        engine: str,
        market: str,
    ) -> list[str]:
        return self.local.list_metadata_snapshots(exchange, engine, market)

    def list_contract_candle_summaries(
        self,
        exchange: str,
        engine: str,
        market: str,
        timeframe: str,
        secid_prefix: str | list[str] | None = None,
        secid_prefixes: list[str] | None = None,
        include_secid: list[str] | None = None,
    ):
        return self.local.list_contract_candle_summaries(
            exchange,
            engine,
            market,
            timeframe,
            secid_prefix=secid_prefix,
            secid_prefixes=secid_prefixes,
            include_secid=include_secid,
        )


def test_data_access_loads_metadata_through_fake_data_store(fixture_root):
    fake = FakeDataStore(fixture_root)
    access = OhlcvDataStoreAccess(data_root="unused", data_store=fake)

    frame = access.load_metadata_snapshot("MOEX", "futures", "forts", "20240101T000000Z")

    assert frame.height == 6
    assert fake.calls[0] == (
        "get_metadata_snapshot",
        {
            "exchange": "MOEX",
            "engine": "futures",
            "market": "forts",
            "snapshot_ts": "20240101T000000Z",
        },
    )


def test_data_access_loads_candles_through_fake_data_store(fixture_root):
    fake = FakeDataStore(fixture_root)
    access = OhlcvDataStoreAccess(data_root="unused", data_store=fake)

    frame = access.load_contract_candles("MOEX", "futures", "forts", "RFUD", "CRH4", "1d")

    assert frame.height == 5
    assert fake.calls[0][0] == "get_candles"
    assert fake.calls[0][1]["symbol"] == "CRH4"


def test_data_access_lists_snapshots_through_fake_data_store(fixture_root):
    fake = FakeDataStore(fixture_root)
    access = OhlcvDataStoreAccess(data_root="unused", data_store=fake)

    assert access.list_metadata_snapshots("MOEX", "futures", "forts") == ["20240101T000000Z"]


def test_local_data_access_lists_contract_candle_summaries(fixture_root):
    summaries = LocalParquetDataAccess(fixture_root).list_contract_candle_summaries(
        "MOEX",
        "futures",
        "forts",
        "1d",
        secid_prefix="CR",
    )

    assert [summary.secid for summary in summaries] == ["CRH4", "CRM4", "CRU4", "CRZ4"]
    assert all(summary.board == "RFUD" for summary in summaries)
    assert all(summary.row_count > 0 for summary in summaries)


def test_local_data_access_lists_contract_candle_summaries_by_multiple_prefixes(
    multi_prefix_data_root,
):
    summaries = LocalParquetDataAccess(multi_prefix_data_root).list_contract_candle_summaries(
        "MOEX",
        "futures",
        "forts",
        "1d",
        secid_prefixes=["CY", "CR"],
    )

    assert [summary.secid for summary in summaries] == [
        "CRH4",
        "CRM4",
        "CRU4",
        "CRZ4",
        "CYH3",
        "CYM3",
    ]


def test_builder_uses_data_access_abstraction(fixture_root, tmp_path):
    access = CountingDataAccess(fixture_root)
    builder = ContinuousFuturesBuilder(tmp_path, data_access=access)

    result = builder.build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="back_adjusted",
    )

    assert result.row_count == 14
    assert access.metadata_calls == 1
    assert access.candle_calls == ["CRH4", "CRM4", "CRU4", "CRZ4"]


def test_clear_error_when_data_store_unavailable_and_no_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "continuous_futures_builder._data_access_store._load_data_store_class", lambda: None
    )

    with pytest.raises(InputDataError, match="ohlcv_data_store is not installed"):
        create_data_access(tmp_path, allow_local_fallback=False)
