from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import polars as pl
import pytest


@pytest.fixture
def fixture_root() -> Path:
    return Path(__file__).parent / "fixtures" / "data"


@pytest.fixture
def temp_data_root(tmp_path: Path, fixture_root: Path) -> Path:
    target = tmp_path / "data"
    shutil.copytree(fixture_root, target)
    return target


def _metadata_path(root: Path) -> Path:
    return (
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


def _candle_path(root: Path, secid: str) -> Path:
    return (
        root
        / "normalized"
        / "candles"
        / "exchange=MOEX"
        / "engine=futures"
        / "market=forts"
        / "board=RFUD"
        / f"symbol={secid}"
        / "timeframe=1d"
        / "part-000.parquet"
    )


def _metadata_row(secid: str, expiration_date: date) -> dict:
    return {
        "source": "fixture",
        "exchange": "MOEX",
        "engine": "futures",
        "market": "forts",
        "board": "RFUD",
        "secid": secid,
        "shortname": "CNY/RUB",
        "secname": "CNY/RUB futures",
        "assetcode": "CNYRUB",
        "instrument_type": "futures",
        "last_trade_date": expiration_date,
        "expiration_date": expiration_date,
        "currency": "RUB",
        "lot_size": 1000,
        "raw_metadata": "{}",
        "snapshot_ts": "20240101T000000Z",
    }


def _candle_frame(secid: str, closes: list[tuple[date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "source": "fixture",
                "exchange": "MOEX",
                "engine": "futures",
                "market": "forts",
                "board": "RFUD",
                "symbol": secid,
                "timeframe": "1d",
                "ts": ts,
                "open": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000,
                "value": close * 1000.0,
                "currency": "RUB",
            }
            for ts, close in closes
        ]
    )


def _write_contract(root: Path, secid: str, closes: list[tuple[date, float]]) -> None:
    path = _candle_path(root, secid)
    path.parent.mkdir(parents=True, exist_ok=True)
    _candle_frame(secid, closes).write_parquet(path)


def _append_contract_candles(root: Path, secid: str, closes: list[tuple[date, float]]) -> None:
    path = _candle_path(root, secid)
    existing = pl.read_parquet(path)
    updated = pl.concat([existing, _candle_frame(secid, closes)], how="vertical").unique(
        subset=["ts"],
        maintain_order=True,
    )
    updated.sort("ts").write_parquet(path)


@pytest.fixture
def multi_prefix_data_root(temp_data_root: Path) -> Path:
    metadata_path = _metadata_path(temp_data_root)
    metadata = pl.read_parquet(metadata_path)
    additions = pl.DataFrame(
        [
            _metadata_row("CYH3", date(2023, 3, 15)),
            _metadata_row("CYM3", date(2023, 6, 15)),
            _metadata_row("MYH3", date(2023, 3, 15)),
        ],
        schema=metadata.schema,
    )
    pl.concat([metadata, additions], how="vertical").write_parquet(metadata_path)

    _write_contract(
        temp_data_root,
        "CYH3",
        [
            (date(2023, 3, 8), 90.0),
            (date(2023, 3, 9), 91.0),
            (date(2023, 3, 10), 92.0),
            (date(2023, 3, 13), 93.0),
            (date(2023, 3, 14), 94.0),
        ],
    )
    _write_contract(
        temp_data_root,
        "CYM3",
        [
            (date(2023, 3, 10), 94.0),
            (date(2023, 3, 13), 95.0),
            (date(2023, 6, 9), 96.0),
            (date(2023, 6, 12), 97.0),
            (date(2023, 6, 13), 98.0),
            (date(2023, 6, 14), 99.0),
        ],
    )
    _write_contract(
        temp_data_root,
        "MYH3",
        [
            (date(2023, 3, 8), 70.0),
            (date(2023, 3, 9), 71.0),
        ],
    )
    _append_contract_candles(temp_data_root, "CRH4", [(date(2023, 6, 12), 100.0)])
    return temp_data_root
