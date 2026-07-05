from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from continuous_futures_builder.cli import app
from continuous_futures_builder.nearest_gap_diagnostics import (
    GAP_EXPECTED_OVERNIGHT_OR_CALENDAR_BREAK,
    GAP_EXPECTED_SESSION_BREAK,
    GAP_ROLL_TRANSITION,
    GAP_UNEXPECTED_INTRADAY,
    GAP_UNKNOWN,
    diagnose_session_gaps,
)


def _dataset(rows: list[tuple[datetime, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "source": "fixture",
                "exchange": "MOEX",
                "engine": "futures",
                "market": "forts",
                "board": "RFUD",
                "symbol": "CNYRUB_NEAREST_5M",
                "continuous_symbol": "CNYRUB_NEAREST_5M",
                "timeframe": "5m",
                "ts": ts,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "value": 1.0,
                "currency": "RUB",
                "active_contract": active_contract,
                "active_board": "RFUD",
                "source_contract_symbol": active_contract,
                "roll_id": "segment",
                "roll_flag": False,
                "is_roll_boundary": False,
                "roll_date": None,
                "adjustment_mode": "unadjusted",
                "adjustment_factor": 0.0,
                "dataset_type": "nearest_contract",
            }
            for ts, active_contract in rows
        ]
    )


def _summary(rows: list[tuple[datetime, str]]):
    _gap_rows, summary = diagnose_session_gaps(_dataset(rows), bar_minutes=5)
    return summary


def test_continuous_5m_bars_have_no_unexpected_gaps():
    summary = _summary(
        [
            (datetime(2024, 3, 11, 10, 0), "CRH4"),
            (datetime(2024, 3, 11, 10, 5), "CRH4"),
            (datetime(2024, 3, 11, 10, 10), "CRH4"),
        ]
    )

    assert summary.total_timestamp_gaps == 0
    assert summary.unexpected_intraday_gaps == 0


def test_missing_bar_inside_main_session_is_unexpected_intraday_gap():
    rows, summary = diagnose_session_gaps(
        _dataset(
            [
                (datetime(2024, 3, 11, 10, 0), "CRH4"),
                (datetime(2024, 3, 11, 10, 10), "CRH4"),
            ]
        ),
        bar_minutes=5,
    )

    assert rows[0].classification == GAP_UNEXPECTED_INTRADAY
    assert summary.unexpected_intraday_gaps == 1
    assert summary.max_unexpected_intraday_gap_minutes == 10.0


def test_main_to_evening_gap_is_expected_session_break():
    rows, summary = diagnose_session_gaps(
        _dataset(
            [
                (datetime(2024, 3, 11, 18, 45), "CRH4"),
                (datetime(2024, 3, 11, 19, 0), "CRH4"),
            ]
        ),
        bar_minutes=5,
    )

    assert rows[0].classification == GAP_EXPECTED_SESSION_BREAK
    assert summary.expected_session_breaks == 1


def test_overnight_gap_is_expected_calendar_break():
    rows, summary = diagnose_session_gaps(
        _dataset(
            [
                (datetime(2024, 3, 11, 23, 50), "CRH4"),
                (datetime(2024, 3, 12, 10, 0), "CRH4"),
            ]
        ),
        bar_minutes=5,
    )

    assert rows[0].classification == GAP_EXPECTED_OVERNIGHT_OR_CALENDAR_BREAK
    assert summary.expected_overnight_or_calendar_breaks == 1


def test_weekend_like_gap_is_expected_calendar_break():
    rows, summary = diagnose_session_gaps(
        _dataset(
            [
                (datetime(2024, 3, 15, 23, 50), "CRH4"),
                (datetime(2024, 3, 18, 10, 0), "CRH4"),
            ]
        ),
        bar_minutes=5,
    )

    assert rows[0].classification == GAP_EXPECTED_OVERNIGHT_OR_CALENDAR_BREAK
    assert summary.expected_overnight_or_calendar_breaks == 1


def test_active_contract_change_gap_is_roll_transition():
    rows, summary = diagnose_session_gaps(
        _dataset(
            [
                (datetime(2024, 3, 11, 10, 0), "CRH4"),
                (datetime(2024, 3, 11, 10, 10), "CRM4"),
            ]
        ),
        bar_minutes=5,
    )

    assert rows[0].classification == GAP_ROLL_TRANSITION
    assert summary.roll_transition_gaps == 1


def test_ambiguous_same_day_gap_is_unknown():
    rows, summary = diagnose_session_gaps(
        _dataset(
            [
                (datetime(2024, 3, 11, 9, 55), "CRH4"),
                (datetime(2024, 3, 11, 10, 10), "CRH4"),
            ]
        ),
        bar_minutes=5,
    )

    assert rows[0].classification == GAP_UNKNOWN
    assert summary.unknown_gaps == 1


def test_diagnose_gaps_cli_writes_reports_and_updates_quality(tmp_path: Path):
    dataset_path = tmp_path / "dataset.parquet"
    report_root = tmp_path / "reports"
    _dataset(
        [
            (datetime(2024, 3, 11, 10, 0), "CRH4"),
            (datetime(2024, 3, 11, 10, 10), "CRH4"),
            (datetime(2024, 3, 11, 18, 45), "CRM4"),
            (datetime(2024, 3, 11, 19, 0), "CRM4"),
        ]
    ).write_parquet(dataset_path)
    report_root.mkdir(parents=True)
    (report_root / "quality.json").write_text(
        json.dumps(
            {
                "row_count": 4,
                "duplicate_ts_count": 0,
                "null_price_count": 0,
                "null_active_contract_count": 0,
                "warnings": ["large intraday gaps: 2"],
                "errors": [],
                "status": "warning",
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "nearest",
            "diagnose-gaps",
            "--dataset-path",
            str(dataset_path),
            "--report-root",
            str(report_root),
            "--bar-minutes",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (report_root / "session_gaps.csv").exists()
    assert (report_root / "session_gaps.parquet").exists()
    assert (report_root / "session_gap_summary.json").exists()
    quality = json.loads((report_root / "quality.json").read_text(encoding="utf-8"))
    assert quality["unexpected_intraday_gap_count"] == 1
    assert quality["expected_session_break_count"] == 1
    assert quality["warnings"] == ["unexpected intraday gaps: 1"]
