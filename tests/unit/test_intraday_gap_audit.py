from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from continuous_futures_builder.cli import app
from continuous_futures_builder.intraday_gap_audit import (
    RECOMMENDATION_EXCLUDE_MONTH,
    RECOMMENDATION_USABLE,
    RECOMMENDATION_WARNING,
    build_contract_month_audit,
    build_decision_time_coverage,
    first_date_with_consecutive_usable_days,
)
from continuous_futures_builder.nearest_gap_diagnostics import (
    GAP_UNEXPECTED_INTRADAY,
    SessionGapRow,
    session_gap_frame,
)


def _rows_for_day(
    trading_date: date,
    *,
    active_contract: str = "CRH4",
    end_time: time = time(13, 30),
    bars: int = 42,
) -> list[tuple[datetime, str]]:
    end_dt = datetime.combine(trading_date, end_time)
    start_dt = end_dt - timedelta(minutes=5 * (bars - 1))
    return [(start_dt + timedelta(minutes=5 * index), active_contract) for index in range(bars)]


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


def _gap(prev_ts: datetime, next_ts: datetime, contract: str = "CRH4") -> SessionGapRow:
    return SessionGapRow(
        prev_ts=prev_ts,
        next_ts=next_ts,
        gap_minutes=(next_ts - prev_ts).total_seconds() / 60.0,
        prev_active_contract=contract,
        next_active_contract=contract,
        classification=GAP_UNEXPECTED_INTRADAY,
        prev_session="main",
        next_session="main",
        same_calendar_date=True,
        same_active_contract=True,
        is_roll_transition=False,
        notes="fixture",
    )


def _coverage(rows: list[tuple[date, str, bool]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "trading_date": trading_date,
                "active_contract": contract,
                "decision_time": "13:30",
                "decision_bar_ts": datetime.combine(trading_date, time(13, 30)),
                "decision_bar_available": usable,
                "bars_before_decision_same_day": 42 if usable else 20,
                "sma_window_available": usable,
                "gap_overlaps_sma_window": False,
                "unknown_gap_overlaps_sma_window": False,
                "decision_day_usable": usable,
                "reason": "ok" if usable else "not_enough_bars_before_decision",
                "sma_window_start_ts": datetime.combine(trading_date, time(10, 5)),
            }
            for trading_date, contract, usable in rows
        ]
    )


def test_usable_day_with_decision_bar_and_sma_window():
    dataset = _dataset(_rows_for_day(date(2024, 3, 11)))
    coverage = build_decision_time_coverage(dataset, session_gap_frame([]))

    row = coverage.row(0, named=True)
    assert row["decision_bar_available"] is True
    assert row["bars_before_decision_same_day"] == 42
    assert row["sma_window_available"] is True
    assert row["decision_day_usable"] is True
    assert row["reason"] == "ok"


def test_missing_decision_bar_marks_day_unusable():
    dataset = _dataset(_rows_for_day(date(2024, 3, 11), end_time=time(13, 20), bars=42))
    coverage = build_decision_time_coverage(dataset, session_gap_frame([]))

    row = coverage.row(0, named=True)
    assert row["decision_bar_available"] is False
    assert row["decision_day_usable"] is False
    assert "missing_decision_bar" in row["reason"]


def test_fewer_than_42_bars_marks_day_unusable():
    dataset = _dataset(_rows_for_day(date(2024, 3, 11), bars=10))
    coverage = build_decision_time_coverage(dataset, session_gap_frame([]))

    row = coverage.row(0, named=True)
    assert row["decision_bar_available"] is True
    assert row["sma_window_available"] is False
    assert row["decision_day_usable"] is False
    assert "not_enough_bars_before_decision" in row["reason"]


def test_gap_overlapping_sma_window_marks_day_unusable():
    dataset = _dataset(_rows_for_day(date(2024, 3, 11), bars=50))
    gaps = session_gap_frame([_gap(datetime(2024, 3, 11, 12, 0), datetime(2024, 3, 11, 12, 10))])

    coverage = build_decision_time_coverage(dataset, gaps)
    row = coverage.row(0, named=True)

    assert row["gap_overlaps_sma_window"] is True
    assert row["decision_day_usable"] is False


def test_gap_outside_sma_window_does_not_mark_day_unusable():
    dataset = _dataset(_rows_for_day(date(2024, 3, 11), bars=50))
    gaps = session_gap_frame([_gap(datetime(2024, 3, 11, 9, 30), datetime(2024, 3, 11, 9, 40))])

    coverage = build_decision_time_coverage(dataset, gaps)
    row = coverage.row(0, named=True)

    assert row["gap_overlaps_sma_window"] is False
    assert row["decision_day_usable"] is True


def test_contract_month_recommendations_cover_usable_warning_and_exclude_month():
    dataset_rows: list[tuple[datetime, str]] = []
    coverage_rows: list[tuple[date, str, bool]] = []
    for index in range(10):
        day = date(2024, 1, 2 + index)
        dataset_rows.extend(_rows_for_day(day, active_contract="CRH4"))
        coverage_rows.append((day, "CRH4", True))
    for index in range(10):
        day = date(2024, 2, 2 + index)
        dataset_rows.extend(_rows_for_day(day, active_contract="CRH4"))
        coverage_rows.append((day, "CRH4", True))
    for index in range(10):
        day = date(2024, 3, 2 + index)
        dataset_rows.extend(_rows_for_day(day, active_contract="CRH4"))
        coverage_rows.append((day, "CRH4", index < 8))
    gaps = session_gap_frame([_gap(datetime(2024, 2, 5, 12, 0), datetime(2024, 2, 5, 12, 10))])

    summary = build_contract_month_audit(_dataset(dataset_rows), gaps, _coverage(coverage_rows))
    recommendations = {row["month"]: row["recommendation"] for row in summary.iter_rows(named=True)}

    assert recommendations["2024-01"] == RECOMMENDATION_USABLE
    assert recommendations["2024-02"] == RECOMMENDATION_WARNING
    assert recommendations["2024-03"] == RECOMMENDATION_EXCLUDE_MONTH


def test_recommended_start_after_30_consecutive_usable_days():
    rows = []
    start = date(2024, 1, 1)
    for index in range(35):
        rows.append((start + timedelta(days=index), "CRH4", index >= 5))
    coverage = _coverage(rows)

    assert first_date_with_consecutive_usable_days(
        coverage,
        consecutive_days=30,
    ) == date(2024, 1, 6)


def test_audit_gaps_cli_smoke_writes_expected_files(tmp_path: Path):
    dataset_path = tmp_path / "dataset.parquet"
    gaps_path = tmp_path / "session_gaps.parquet"
    report_root = tmp_path / "audit"
    _dataset(_rows_for_day(date(2024, 3, 11))).write_parquet(dataset_path)
    session_gap_frame([]).write_parquet(gaps_path)

    result = CliRunner().invoke(
        app,
        [
            "nearest",
            "audit-gaps",
            "--dataset-path",
            str(dataset_path),
            "--session-gaps-path",
            str(gaps_path),
            "--report-root",
            str(report_root),
            "--decision-time",
            "13:30",
            "--decision-tolerance-minutes",
            "5",
            "--sma-window-bars",
            "42",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (report_root / "gap_audit_summary.json").exists()
    assert (report_root / "gap_audit_by_contract_month.csv").exists()
    assert (report_root / "gap_audit_by_contract_month.parquet").exists()
    assert (report_root / "decision_time_coverage.csv").exists()
    assert (report_root / "decision_time_coverage.parquet").exists()
    assert (report_root / "gap_audit_report.md").exists()
    summary = json.loads((report_root / "gap_audit_summary.json").read_text(encoding="utf-8"))
    assert summary["total_trading_days"] == 1
    assert summary["usable_decision_days"] == 1


def test_approve_audit_cli_writes_formal_quality_approval(tmp_path: Path):
    dataset_path = tmp_path / "dataset.parquet"
    audit_summary_path = tmp_path / "gap_audit_summary.json"
    approval_path = tmp_path / "quality_approval.json"
    _dataset(_rows_for_day(date(2024, 3, 11))).write_parquet(dataset_path)
    audit_summary_path.write_text(
        json.dumps(
            {
                "continuous_symbol": "CNYRUB_NEAREST_5M",
                "timeframe": "5m",
                "dataset_path": str(dataset_path),
                "decision_time": "13:30",
                "sma_window_bars": 42,
                "usable_decision_day_pct": 1.0,
                "recommended_start_date": "2024-03-11",
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "nearest",
            "approve-audit",
            "--gap-audit-summary-path",
            str(audit_summary_path),
            "--dataset-path",
            str(dataset_path),
            "--output-path",
            str(approval_path),
            "--min-usable-decision-day-pct",
            "0.95",
        ],
    )

    assert result.exit_code == 0, result.output
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval["approval_status"] == "approved"
    assert approval["dataset_sha256"]
    assert approval["gap_audit_sha256"]
    assert approval["allowed_decision_times"] == ["13:30"]
    assert approval["allowed_sma_window_bars"] == [42]
