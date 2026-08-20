from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, field_validator

from continuous_futures_builder.exceptions import InputDataError
from continuous_futures_builder.nearest_gap_diagnostics import (
    GAP_UNEXPECTED_INTRADAY,
    GAP_UNKNOWN,
    diagnose_session_gaps,
    session_gap_frame,
)

RECOMMENDATION_USABLE = "usable"
RECOMMENDATION_WARNING = "warning"
RECOMMENDATION_EXCLUDE_MONTH = "exclude_month"
RECOMMENDATION_EXCLUDE_CONTRACT = "exclude_contract"

REASON_OK = "ok"
REASON_MISSING_DECISION_BAR = "missing_decision_bar"
REASON_NOT_ENOUGH_BARS = "not_enough_bars_before_decision"
REASON_GAP_OVERLAPS_WINDOW = "gap_overlaps_sma_window"
REASON_UNKNOWN_GAP_OVERLAPS_WINDOW = "unknown_gap_overlaps_sma_window"


class IntradayGapAuditConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    dataset_path: Path
    report_root: Path
    session_gaps_path: Path | None = None
    decision_time: time = time(13, 30)
    decision_tolerance_minutes: int = 5
    sma_window_bars: int = 42
    bar_minutes: int = 5
    consecutive_usable_days: int = 30

    @field_validator("dataset_path", "report_root", "session_gaps_path", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        return Path(value)

    @field_validator("decision_time", mode="before")
    @classmethod
    def _coerce_time(cls, value: str | time) -> time:
        if isinstance(value, time):
            return value
        return time.fromisoformat(value)

    @field_validator("decision_tolerance_minutes", "sma_window_bars", "bar_minutes")
    @classmethod
    def _validate_positive_int(cls, value: int) -> int:
        if value < 1:
            raise ValueError("audit numeric parameters must be >= 1")
        return value


class IntradayGapAuditResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    report_root: Path
    gap_audit_summary_json: Path
    gap_audit_by_contract_month_csv: Path
    gap_audit_by_contract_month_parquet: Path
    decision_time_coverage_csv: Path
    decision_time_coverage_parquet: Path
    gap_audit_report_md: Path
    total_trading_days: int
    usable_decision_days: int
    unusable_decision_days: int
    usable_decision_day_pct: float | None
    recommended_start_date: date | None = None
    first_date_with_30_consecutive_usable_decision_days: date | None = None
    first_date_after_last_exclude_month: date | None = None
    first_date_after_last_bad_gap_near_decision: date | None = None
    worst_contract_months: list[dict[str, Any]] = Field(default_factory=list)
    excluded_periods: list[dict[str, Any]] = Field(default_factory=list)


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", ""))
    raise InputDataError(f"Unsupported timestamp value: {value!r}")


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise InputDataError(f"Unsupported date value: {value!r}")


def _month_key(value: date | datetime) -> str:
    value_date = value.date() if isinstance(value, datetime) else value
    return f"{value_date.year:04d}-{value_date.month:02d}"


def _month_end(month: str) -> date:
    year, month_number = (int(part) for part in month.split("-", maxsplit=1))
    return date(year, month_number, calendar.monthrange(year, month_number)[1])


def _load_dataset(path: str | Path) -> pl.DataFrame:
    target = Path(path)
    if not target.exists():
        raise InputDataError(f"Dataset path does not exist: {target}")
    frame = pl.read_parquet(target).sort("ts")
    if frame.height == 0:
        raise InputDataError(f"Dataset is empty: {target}")
    if "ts" not in frame.columns or "active_contract" not in frame.columns:
        raise InputDataError("Dataset must contain ts and active_contract columns")
    return frame


def _load_or_compute_session_gaps(
    dataset: pl.DataFrame,
    session_gaps_path: str | Path | None,
    *,
    bar_minutes: int,
) -> pl.DataFrame:
    if session_gaps_path is not None:
        target = Path(session_gaps_path)
        if target.exists():
            if target.suffix.lower() == ".csv":
                return pl.read_csv(target, try_parse_dates=True)
            return pl.read_parquet(target)
    rows, _summary = diagnose_session_gaps(dataset, bar_minutes=bar_minutes)
    return session_gap_frame(rows)


def _rows_by_trading_date(dataset: pl.DataFrame) -> dict[date, list[dict[str, Any]]]:
    grouped: dict[date, list[dict[str, Any]]] = {}
    for row in dataset.sort("ts").iter_rows(named=True):
        trading_date = _as_datetime(row["ts"]).date()
        grouped.setdefault(trading_date, []).append(row)
    return grouped


def _bad_gap_records(session_gaps: pl.DataFrame) -> list[dict[str, Any]]:
    if session_gaps.height == 0:
        return []
    return [
        row
        for row in session_gaps.iter_rows(named=True)
        if row.get("classification") in {GAP_UNEXPECTED_INTRADAY, GAP_UNKNOWN}
    ]


def _gap_overlaps_window(gap: dict[str, Any], start: datetime, end: datetime) -> bool:
    prev_ts = _as_datetime(gap["prev_ts"])
    next_ts = _as_datetime(gap["next_ts"])
    return prev_ts < end and next_ts > start


def _find_decision_row(
    rows: list[dict[str, Any]],
    *,
    decision_dt: datetime,
    tolerance: timedelta,
) -> dict[str, Any] | None:
    candidates = [
        row for row in rows if decision_dt - tolerance <= _as_datetime(row["ts"]) <= decision_dt
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: _as_datetime(row["ts"]))
