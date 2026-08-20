from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder._gap_diagnostics_analysis import (
    build_session_gap_rows,
    build_session_gap_summary,
    session_gap_frame,
)
from continuous_futures_builder._gap_diagnostics_core import (
    IntradaySessionModel,
    SessionGapDiagnosticsResult,
    SessionGapRow,
    SessionGapSummary,
    default_moex_futures_session_model,
)


def diagnose_session_gaps(
    frame: pl.DataFrame,
    *,
    bar_minutes: int,
    session_model: IntradaySessionModel | None = None,
) -> tuple[list[SessionGapRow], SessionGapSummary]:
    effective_model = session_model or default_moex_futures_session_model()
    rows = build_session_gap_rows(
        frame,
        bar_minutes=bar_minutes,
        session_model=effective_model,
    )
    return rows, build_session_gap_summary(
        frame,
        rows,
        bar_minutes=bar_minutes,
        session_model=effective_model,
    )


def write_session_gap_reports(
    frame: pl.DataFrame,
    report_root: str | Path,
    *,
    bar_minutes: int,
    session_model: IntradaySessionModel | None = None,
) -> SessionGapDiagnosticsResult:
    rows, summary = diagnose_session_gaps(
        frame,
        bar_minutes=bar_minutes,
        session_model=session_model,
    )
    target_root = Path(report_root)
    target_root.mkdir(parents=True, exist_ok=True)
    gaps_csv = target_root / "session_gaps.csv"
    gaps_parquet = target_root / "session_gaps.parquet"
    summary_json = target_root / "session_gap_summary.json"

    gap_frame = session_gap_frame(rows)
    gap_frame.write_csv(gaps_csv)
    gap_frame.write_parquet(gaps_parquet)
    summary_json.write_text(
        json.dumps(summary.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return SessionGapDiagnosticsResult(
        report_root=target_root,
        session_gaps_csv=gaps_csv,
        session_gaps_parquet=gaps_parquet,
        session_gap_summary_json=summary_json,
        summary=summary,
    )


def session_summary_quality_fields(summary: SessionGapSummary) -> dict[str, Any]:
    return {
        "total_timestamp_gaps": summary.total_timestamp_gaps,
        "expected_session_break_count": summary.expected_session_breaks,
        "expected_overnight_or_calendar_break_count": (
            summary.expected_overnight_or_calendar_breaks
        ),
        "roll_transition_gap_count": summary.roll_transition_gaps,
        "unexpected_intraday_gap_count": summary.unexpected_intraday_gaps,
        "unknown_gap_count": summary.unknown_gaps,
        "max_unexpected_intraday_gap_minutes": summary.max_unexpected_intraday_gap_minutes,
        "top_unexpected_gaps": summary.top_unexpected_gaps,
        "session_model": summary.session_model,
    }


def _status_from_payload(payload: dict[str, Any]) -> str:
    if (
        int(payload.get("row_count") or 0) == 0
        or int(payload.get("duplicate_ts_count") or 0) > 0
        or int(payload.get("null_price_count") or 0) > 0
        or int(payload.get("null_active_contract_count") or 0) > 0
        or payload.get("errors")
    ):
        return "rejected"
    if (
        int(payload.get("unexpected_intraday_gap_count") or 0) > 0
        or int(payload.get("unknown_gap_count") or 0) > 0
        or payload.get("warnings")
    ):
        return "warning"
    return "ok"


def update_nearest_quality_files(
    report_root: str | Path,
    summary: SessionGapSummary,
) -> list[Path]:
    target_root = Path(report_root)
    updated: list[Path] = []
    fields = session_summary_quality_fields(summary)
    for filename in ("quality.json", "build_summary.json"):
        path = target_root / filename
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(fields)
        warnings = [
            str(warning)
            for warning in payload.get("warnings", [])
            if not str(warning).startswith("large intraday gaps:")
            and not str(warning).startswith("unexpected intraday gaps:")
            and not str(warning).startswith("unknown timestamp gaps:")
        ]
        if summary.unexpected_intraday_gaps:
            warnings.append(f"unexpected intraday gaps: {summary.unexpected_intraday_gaps}")
        if summary.unknown_gaps:
            warnings.append(f"unknown timestamp gaps: {summary.unknown_gaps}")
        payload["warnings"] = warnings
        payload["status"] = _status_from_payload(payload)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        updated.append(path)
    return updated


def diagnose_dataset_session_gaps(
    dataset_path: str | Path,
    report_root: str | Path,
    *,
    bar_minutes: int,
    session_model: IntradaySessionModel | None = None,
    update_quality: bool = True,
) -> tuple[SessionGapDiagnosticsResult, list[Path]]:
    dataset = pl.read_parquet(dataset_path)
    result = write_session_gap_reports(
        dataset,
        report_root,
        bar_minutes=bar_minutes,
        session_model=session_model,
    )
    updated = update_nearest_quality_files(report_root, result.summary) if update_quality else []
    return result, updated
