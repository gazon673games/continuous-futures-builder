from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from continuous_futures_builder.cli import app
from continuous_futures_builder.exceptions import (
    ContinuousSeriesValidationError,
    ContractChainError,
)
from continuous_futures_builder.nearest_contract import (
    NEAREST_DATASET_TYPE,
    NearestContractBuildConfig,
    build_nearest_contract_dataset,
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


def test_nearest_chain_roll_dates_and_intraday_boundary(tmp_path: Path):
    data_root = tmp_path / "data"
    output_root = tmp_path / "out"
    _write_nearest_fixture(data_root)

    result = build_nearest_contract_dataset(_config(data_root, output_root))
    frame = pl.read_parquet(result.output_path)
    roll_calendar = pl.read_parquet(result.roll_calendar_parquet)

    assert roll_calendar.get_column("roll_date").to_list() == [
        date(2024, 3, 12),
        date(2024, 6, 17),
    ]
    assert list(frame.select("ts", "active_contract", "close").iter_rows()) == [
        (datetime(2024, 3, 11, 10, 0), "CRH4", 100.0),
        (datetime(2024, 3, 12, 10, 0), "CRM4", 200.0),
        (datetime(2024, 3, 12, 10, 5), "CRM4", 201.0),
        (datetime(2024, 6, 16, 10, 0), "CRM4", 202.0),
        (datetime(2024, 6, 17, 10, 0), "CRU4", 300.0),
        (datetime(2024, 6, 17, 10, 5), "CRU4", 301.0),
    ]
    assert frame.get_column("adjustment_mode").unique().to_list() == ["unadjusted"]
    assert frame.get_column("dataset_type").unique().to_list() == [NEAREST_DATASET_TYPE]
    assert frame.get_column("symbol").unique().to_list() == ["CNYRUB_NEAREST_5M"]
    assert frame.get_column("source_contract_symbol").to_list() == [
        "CRH4",
        "CRM4",
        "CRM4",
        "CRM4",
        "CRU4",
        "CRU4",
    ]


def test_duplicate_output_ts_is_rejected(tmp_path: Path):
    data_root = tmp_path / "data"
    output_root = tmp_path / "out"
    _write_metadata(
        data_root,
        [
            _metadata_row("CRH4", date(2024, 3, 15)),
            _metadata_row("CRM4", date(2024, 6, 20)),
        ],
    )
    _write_candles(
        data_root,
        "CRH4",
        [
            (datetime(2024, 3, 11, 10, 0), 100.0),
            (datetime(2024, 3, 11, 10, 0), 101.0),
        ],
    )
    _write_candles(data_root, "CRM4", [(datetime(2024, 3, 12, 10, 0), 102.0)])

    with pytest.raises(ContinuousSeriesValidationError, match="duplicate ts"):
        build_nearest_contract_dataset(_config(data_root, output_root))


def test_missing_expiration_metadata_is_rejected(tmp_path: Path):
    data_root = tmp_path / "data"
    output_root = tmp_path / "out"
    _write_metadata(
        data_root,
        [
            _metadata_row("CRH4", None),
            _metadata_row("CRM4", date(2024, 6, 20)),
            _metadata_row("CRU4", date(2024, 9, 19)),
        ],
    )
    _write_candles(data_root, "CRM4", [(datetime(2024, 6, 16, 10, 0), 100.0)])
    _write_candles(data_root, "CRU4", [(datetime(2024, 6, 17, 10, 0), 101.0)])

    with pytest.raises(ContractChainError, match="missing metadata"):
        build_nearest_contract_dataset(_config(data_root, output_root))


def test_large_intraday_gaps_are_warning_not_rejected(tmp_path: Path):
    data_root = tmp_path / "data"
    output_root = tmp_path / "out"
    _write_metadata(
        data_root,
        [
            _metadata_row("CRH4", date(2024, 3, 15)),
            _metadata_row("CRM4", date(2024, 6, 20)),
        ],
    )
    _write_candles(
        data_root,
        "CRH4",
        [
            (datetime(2024, 3, 11, 10, 0), 100.0),
            (datetime(2024, 3, 11, 10, 25), 100.1),
        ],
    )
    _write_candles(
        data_root,
        "CRM4",
        [
            (datetime(2024, 3, 12, 10, 0), 100.2),
            (datetime(2024, 3, 12, 10, 5), 100.3),
        ],
    )

    result = build_nearest_contract_dataset(_config(data_root, output_root))
    quality = json.loads(result.quality_json.read_text(encoding="utf-8"))

    assert result.quality_status == "warning"
    assert quality["large_gap_count"] == 1
    assert quality["duplicate_ts_count"] == 0
    assert quality["null_active_contract_count"] == 0


def test_nearest_cli_smoke_writes_dataset_and_reports(tmp_path: Path):
    data_root = tmp_path / "data"
    output_root = tmp_path / "out"
    _write_nearest_fixture(data_root)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "nearest",
            "build",
            "--data-root",
            str(data_root),
            "--output-root",
            str(output_root),
            "--exchange",
            "MOEX",
            "--engine",
            "futures",
            "--market",
            "forts",
            "--board",
            "RFUD",
            "--secid-prefix",
            "CR",
            "--asset-code",
            "CNY",
            "--timeframe",
            "5m",
            "--continuous-symbol",
            "CNYRUB_NEAREST_5M",
            "--adjustment-mode",
            "unadjusted",
            "--roll-days-before-expiration",
            "3",
            "--overwrite",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "row_count: 6" in result.output
    assert "roll_count: 2" in result.output
    assert (
        output_root
        / "derived"
        / "nearest_contract_futures"
        / "exchange=MOEX"
        / "engine=futures"
        / "market=forts"
        / "continuous_symbol=CNYRUB_NEAREST_5M"
        / "timeframe=5m"
        / "adjustment_mode=unadjusted"
        / "part-000.parquet"
    ).exists()
    reports_dir = output_root / "reports" / "nearest_contract_futures" / "CNYRUB_NEAREST_5M"
    assert (reports_dir / "build_summary.json").exists()
    assert (reports_dir / "roll_calendar.csv").exists()
    assert (reports_dir / "quality.json").exists()
    assert (reports_dir / "contract_coverage.parquet").exists()
