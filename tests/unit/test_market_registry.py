from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from continuous_futures_builder.cli import app
from continuous_futures_builder.market_registry import (
    audit_instrument_registry,
    audit_session_calendar,
    build_dataset_catalog,
    build_moex_instrument_registry,
    build_observed_session_calendar,
    prepare_backtest_artifacts,
)


def test_build_moex_instrument_registry_from_metadata(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.parquet"
    output_path = tmp_path / "registry.json"
    _metadata_frame().write_parquet(metadata_path)

    result = build_moex_instrument_registry(
        metadata_snapshot_path=metadata_path,
        output_path=output_path,
        symbol_prefixes=["CR"],
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert result.item_count == 1
    assert payload["instruments"][0]["symbol"] == "CRU6"
    assert payload["instruments"][0]["lot_size"] == 1000.0
    assert payload["instruments"][0]["tick_size"] == 0.001
    assert payload["instruments"][0]["tick_value"] == 1.0
    assert payload["instruments"][0]["initial_margin"] == 1152.23


def test_build_observed_session_calendar_marks_missing_weekday_and_short_day(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "dataset.parquet"
    output_path = tmp_path / "calendar.json"
    rows = []
    for hour in (10, 11, 12, 13):
        rows.append(_row(datetime(2024, 1, 2, hour, 0)))
    rows.append(_row(datetime(2024, 1, 4, 10, 0)))
    pl.DataFrame(rows).write_parquet(dataset_path)

    result = build_observed_session_calendar(
        dataset_path=dataset_path,
        output_path=output_path,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert result.item_count == 2
    assert "2024-01-03" in payload["unobserved_weekdays"]
    assert payload["short_session_candidates"] == ["2024-01-04"]


def test_build_dataset_catalog_indexes_derived_dataset(tmp_path: Path) -> None:
    dataset_dir = tmp_path / (
        "derived/nearest_contract_futures/exchange=MOEX/engine=futures/market=forts/"
        "continuous_symbol=CNYRUB_NEAREST_5M/timeframe=5m/adjustment_mode=unadjusted"
    )
    dataset_dir.mkdir(parents=True)
    dataset_path = dataset_dir / "part-000.parquet"
    pl.DataFrame([_row(datetime(2024, 1, 2, 13, 30))]).write_parquet(dataset_path)
    approval_path = tmp_path / (
        "reports/nearest_contract_futures/CNYRUB_NEAREST_5M/gap_audit/quality_approval.json"
    )
    approval_path.parent.mkdir(parents=True)
    approval_path.write_text('{"approval_status":"approved"}', encoding="utf-8")

    result = build_dataset_catalog(
        derived_root=tmp_path / "derived",
        reports_root=tmp_path / "reports",
        output_path=tmp_path / "catalog.json",
        instrument_registry_path=tmp_path / "registry.json",
        calendar_path=tmp_path / "calendar.json",
    )

    payload = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert result.item_count == 1
    assert payload["entries"][0]["dataset_id"] == "CNYRUB_NEAREST_5M:5m:unadjusted"
    assert payload["entries"][0]["quality_approval_path"] == str(approval_path)


def test_market_registry_cli_smoke(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.parquet"
    output_path = tmp_path / "registry.json"
    _metadata_frame().write_parquet(metadata_path)

    result = CliRunner().invoke(
        app,
        [
            "build-instrument-registry",
            "--metadata-snapshot-path",
            str(metadata_path),
            "--output-path",
            str(output_path),
            "--symbol-prefix",
            "CR",
        ],
    )

    assert result.exit_code == 0, result.output
    assert output_path.exists()


def test_registry_and_calendar_audits_are_written(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    calendar_path = tmp_path / "calendar.json"
    registry_audit_path = tmp_path / "registry_audit.json"
    calendar_audit_path = tmp_path / "calendar_audit.json"
    registry_path.write_text(
        json.dumps(
            {
                "instruments": [
                    {
                        "symbol": "CRU6",
                        "contract_multiplier": 1.0,
                        "lot_size": 1000.0,
                        "tick_size": 0.001,
                        "tick_value": 1.0,
                        "initial_margin": 1152.23,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calendar_path.write_text(
        json.dumps(
            {
                "main_session_start": "10:00",
                "main_session_end": "18:50",
                "trading_dates": ["2024-01-02"],
                "holiday_dates": ["2024-01-03"],
                "short_day_dates": [],
            }
        ),
        encoding="utf-8",
    )

    registry_result = audit_instrument_registry(
        registry_path=registry_path,
        output_path=registry_audit_path,
    )
    calendar_result = audit_session_calendar(
        calendar_path=calendar_path,
        output_path=calendar_audit_path,
    )

    assert registry_result.audit_status == "approved"
    assert calendar_result.audit_status == "approved"
    assert registry_audit_path.exists()
    assert calendar_audit_path.exists()


def test_prepare_backtest_artifacts_builds_manifest(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.parquet"
    dataset_dir = tmp_path / (
        "derived/nearest_contract_futures/exchange=MOEX/engine=futures/market=forts/"
        "continuous_symbol=CNYRUB_NEAREST_5M/timeframe=5m/adjustment_mode=unadjusted"
    )
    dataset_dir.mkdir(parents=True)
    dataset_path = dataset_dir / "part-000.parquet"
    _metadata_frame().write_parquet(metadata_path)
    pl.DataFrame([_row(datetime(2024, 1, 2, 13, 30))]).write_parquet(dataset_path)

    manifest = prepare_backtest_artifacts(
        metadata_snapshot_path=metadata_path,
        observed_dataset_path=dataset_path,
        derived_root=tmp_path / "derived",
        reports_root=None,
        output_dir=tmp_path / "artifacts",
        symbol_prefixes=["CR"],
    )

    assert manifest["registry_audit"]["audit_status"] == "approved"
    assert manifest["calendar_audit"]["audit_status"] == "rejected"
    assert (tmp_path / "artifacts" / "data_catalog.json").exists()
    catalog = json.loads((tmp_path / "artifacts" / "data_catalog.json").read_text())
    assert catalog["entries"][0]["instrument_registry_audit_path"]


def _metadata_frame() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "source": "fixture",
                "exchange": "MOEX",
                "engine": "futures",
                "market": "forts",
                "board": "RFUD",
                "secid": "CRU6",
                "shortname": "CNY-9.26",
                "secname": "CNY futures",
                "assetcode": "CNY",
                "instrument_type": "CR",
                "last_trade_date": date(2026, 9, 17),
                "expiration_date": date(2026, 9, 17),
                "currency": None,
                "lot_size": 1000.0,
                "raw_metadata": json.dumps(
                    {
                        "secid": "CRU6",
                        "boardid": "RFUD",
                        "lotvolume": 1000,
                        "minstep": 0.001,
                        "stepprice": 1.0,
                        "initialmargin": 1152.23,
                        "lasttradedate": "2026-09-17",
                    }
                ),
                "snapshot_ts": datetime(2026, 7, 5, 19, 14, 4),
            },
            {
                "source": "fixture",
                "exchange": "MOEX",
                "engine": "futures",
                "market": "forts",
                "board": "RFUD",
                "secid": "SiU6",
                "shortname": "Si-9.26",
                "secname": "Si futures",
                "assetcode": "USD",
                "instrument_type": "Si",
                "last_trade_date": date(2026, 9, 17),
                "expiration_date": date(2026, 9, 17),
                "currency": "RUB",
                "lot_size": 1.0,
                "raw_metadata": "{}",
                "snapshot_ts": datetime(2026, 7, 5, 19, 14, 4),
            },
        ]
    )


def _row(ts: datetime) -> dict[str, object]:
    return {
        "source": "fixture",
        "exchange": "MOEX",
        "engine": "futures",
        "market": "forts",
        "board": "RFUD",
        "symbol": "CRU6",
        "continuous_symbol": "CNYRUB_NEAREST_5M",
        "timeframe": "5m",
        "ts": ts,
        "open": 10.0,
        "high": 10.0,
        "low": 10.0,
        "close": 10.0,
        "volume": 1.0,
        "value": 10.0,
        "currency": "RUB",
        "active_contract": "CRU6",
        "source_contract_symbol": "CRU6",
        "dataset_type": "nearest_contract",
        "adjustment_mode": "unadjusted",
    }
