from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl

from continuous_futures_builder.nearest_contract import (
    NearestContractBuildConfig,
)


def _metadata_row(
    secid: str,
    expiration_date: date | None,
    *,
    board: str = "RFUD",
    assetcode: str = "CNY",
) -> dict:
    return {
        "source": "fixture",
        "exchange": "MOEX",
        "engine": "futures",
        "market": "forts",
        "board": board,
        "secid": secid,
        "shortname": secid,
        "secname": f"{secid} futures",
        "assetcode": assetcode,
        "instrument_type": "futures",
        "last_trade_date": expiration_date,
        "expiration_date": expiration_date,
        "currency": "RUB",
        "lot_size": 1000,
        "raw_metadata": "{}",
        "snapshot_ts": "20240101T000000Z",
    }


def _write_metadata(root: Path, rows: list[dict]) -> None:
    path = (
        root
        / "normalized"
        / "metadata"
        / "exchange=MOEX"
        / "engine=futures"
        / "market=forts"
        / "securities"
        / "snapshot_ts=20240101T000000Z"
        / "part-000.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema_overrides={
            "last_trade_date": pl.Date,
            "expiration_date": pl.Date,
        },
    ).write_parquet(path)


def _candle_row(secid: str, ts: datetime, close: float) -> dict:
    return {
        "source": "fixture",
        "exchange": "MOEX",
        "engine": "futures",
        "market": "forts",
        "board": "RFUD",
        "symbol": secid,
        "timeframe": "5m",
        "ts": ts,
        "open": close,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "volume": 10,
        "value": close * 10,
        "currency": "RUB",
    }


def _write_candles(root: Path, secid: str, rows: list[tuple[datetime, float]]) -> None:
    path = (
        root
        / "normalized"
        / "candles"
        / "exchange=MOEX"
        / "engine=futures"
        / "market=forts"
        / "board=RFUD"
        / f"symbol={secid}"
        / "timeframe=5m"
        / "part-000.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame([_candle_row(secid, ts, close) for ts, close in rows]).write_parquet(path)


def _write_nearest_fixture(root: Path) -> None:
    _write_metadata(
        root,
        [
            _metadata_row("CRH4", date(2024, 3, 15)),
            _metadata_row("CRM4", date(2024, 6, 20)),
            _metadata_row("CRU4", date(2024, 9, 19)),
        ],
    )
    _write_candles(
        root,
        "CRH4",
        [
            (datetime(2024, 3, 11, 10, 0), 100.0),
            (datetime(2024, 3, 12, 10, 0), 101.0),
        ],
    )
    _write_candles(
        root,
        "CRM4",
        [
            (datetime(2024, 3, 12, 10, 0), 200.0),
            (datetime(2024, 3, 12, 10, 5), 201.0),
            (datetime(2024, 6, 16, 10, 0), 202.0),
            (datetime(2024, 6, 17, 10, 0), 203.0),
        ],
    )
    _write_candles(
        root,
        "CRU4",
        [
            (datetime(2024, 6, 17, 10, 0), 300.0),
            (datetime(2024, 6, 17, 10, 5), 301.0),
        ],
    )


def _config(root: Path, output_root: Path) -> NearestContractBuildConfig:
    return NearestContractBuildConfig(
        data_root=root,
        output_root=output_root,
        secid_prefixes=["CR"],
        asset_code="CNY",
        timeframe="5m",
        continuous_symbol="CNYRUB_NEAREST_5M",
        roll_days_before_expiration=3,
        overwrite=True,
    )
