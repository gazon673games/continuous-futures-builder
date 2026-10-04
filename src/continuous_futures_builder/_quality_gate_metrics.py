from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder._quality_gate_core import (
    QualityGateMetrics,
    _date_value,
    _max_abs_roll_gap_pct_row,
    _max_family_transition_gap_pct,
    _validation_status,
)
from continuous_futures_builder.models import ContinuousFuturesQualityReport, RollReportRow
from continuous_futures_builder.quality import _quality_report_from_frame
from continuous_futures_builder.roll_report import _roll_report_from_frame


def _max_roll_timestamp_gap(frame: pl.DataFrame) -> tuple[int | None, object, list[str]]:
    if frame.height < 2 or "ts" not in frame.columns or "active_contract" not in frame.columns:
        return None, None, []

    previous: dict[str, Any] | None = None
    best_gap: int | None = None
    best_roll_date: object = None
    best_contracts: list[str] = []
    for row in frame.sort("ts").iter_rows(named=True):
        if previous is None:
            previous = row
            continue

        from_contract = str(previous.get("active_contract"))
        to_contract = str(row.get("active_contract"))
        if from_contract == to_contract:
            previous = row
            continue

        previous_ts = _date_value(previous.get("ts"))
        current_ts = _date_value(row.get("ts"))
        if previous_ts is None or current_ts is None:
            previous = row
            continue

        gap_days = max(0, (current_ts - previous_ts).days)
        if best_gap is None or gap_days > best_gap:
            best_gap = gap_days
            best_roll_date = _date_value(row.get("roll_date")) or current_ts
            best_contracts = [from_contract, to_contract]
        previous = row

    return best_gap, best_roll_date, best_contracts


def build_quality_gate_metrics(path: str | Path) -> QualityGateMetrics:
    target = Path(path)
    frame = pl.read_parquet(target)
    roll_rows = _roll_report_from_frame(frame)
    quality_report = _quality_report_from_frame(target, frame, roll_rows)
    return _quality_gate_metrics_from_frame(frame, quality_report, roll_rows)


def _quality_gate_metrics_from_frame(
    frame: pl.DataFrame,
    quality_report: ContinuousFuturesQualityReport,
    roll_rows: list[RollReportRow],
) -> QualityGateMetrics:
    validation_status, validation_error = _validation_status(frame)
    max_roll_gap_pct, max_roll_gap_pct_date, max_roll_gap_pct_contracts = _max_abs_roll_gap_pct_row(
        roll_rows
    )
    max_source_gap, max_source_gap_date, max_source_gap_contracts = _max_roll_timestamp_gap(frame)

    return QualityGateMetrics(
        row_count=quality_report.row_count,
        min_ts=quality_report.min_ts,
        max_ts=quality_report.max_ts,
        duplicate_ts_count=quality_report.duplicate_ts_count,
        null_price_count=quality_report.null_price_count,
        null_active_contract_count=quality_report.null_active_contract_count,
        contract_count=quality_report.contract_count,
        roll_count=quality_report.roll_count,
        max_abs_roll_gap=quality_report.max_abs_roll_gap,
        max_abs_roll_gap_pct=max_roll_gap_pct,
        max_abs_roll_gap_roll_date=quality_report.max_abs_roll_gap_roll_date,
        max_abs_roll_gap_contracts=quality_report.max_abs_roll_gap_contracts,
        max_abs_roll_gap_pct_roll_date=max_roll_gap_pct_date,
        max_abs_roll_gap_pct_contracts=max_roll_gap_pct_contracts,
        max_source_coverage_gap_days=max_source_gap,
        max_source_coverage_gap_roll_date=max_source_gap_date,
        max_source_coverage_gap_contracts=max_source_gap_contracts,
        family_transition_count=quality_report.family_transition_count,
        max_family_transition_gap_pct=_max_family_transition_gap_pct(roll_rows),
        empty_active_segments=None,
        contracts_pruned=None,
        validation_status=validation_status,
        validation_error=validation_error,
        quality_status=quality_report.status,
    )
