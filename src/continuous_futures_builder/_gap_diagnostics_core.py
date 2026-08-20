from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, field_validator

from continuous_futures_builder.exceptions import InputDataError

GAP_EXPECTED_SESSION_BREAK = "expected_session_break"
GAP_EXPECTED_OVERNIGHT_OR_CALENDAR_BREAK = "expected_overnight_or_calendar_break"
GAP_ROLL_TRANSITION = "roll_transition_gap"
GAP_UNEXPECTED_INTRADAY = "unexpected_intraday_gap"
GAP_UNKNOWN = "unknown_gap"


class TradingSession(BaseModel):
    name: str
    start: time
    end: time

    @field_validator("start", "end", mode="before")
    @classmethod
    def _coerce_time(cls, value: str | time) -> time:
        if isinstance(value, time):
            return value
        return time.fromisoformat(value)


class IntradaySessionModel(BaseModel):
    timezone: str = "local_exchange_time"
    sessions: list[TradingSession] = Field(default_factory=list)

    def session_for(self, value: datetime) -> TradingSession | None:
        value_time = value.time()
        for session in self.sessions:
            if session.start <= value_time <= session.end:
                return session
        return None

    def session_index(self, session: TradingSession | None) -> int | None:
        if session is None:
            return None
        for index, candidate in enumerate(self.sessions):
            if candidate.name == session.name:
                return index
        return None


class SessionGapRow(BaseModel):
    prev_ts: datetime
    next_ts: datetime
    gap_minutes: float
    prev_active_contract: str | None = None
    next_active_contract: str | None = None
    classification: str
    prev_session: str | None = None
    next_session: str | None = None
    same_calendar_date: bool
    same_active_contract: bool
    is_roll_transition: bool
    notes: str | None = None


class SessionGapSummary(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    continuous_symbol: str | None = None
    timeframe: str | None = None
    bar_minutes: int
    session_model: dict[str, Any]
    total_timestamp_gaps: int
    expected_session_breaks: int
    expected_overnight_or_calendar_breaks: int
    roll_transition_gaps: int
    unexpected_intraday_gaps: int
    unknown_gaps: int
    max_unexpected_intraday_gap_minutes: float | None = None
    top_unexpected_gaps: list[dict[str, Any]] = Field(default_factory=list)


class SessionGapDiagnosticsResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    report_root: Path
    session_gaps_csv: Path
    session_gaps_parquet: Path
    session_gap_summary_json: Path
    summary: SessionGapSummary


def default_moex_futures_session_model() -> IntradaySessionModel:
    return IntradaySessionModel(
        sessions=[
            TradingSession(name="main", start=time(10, 0), end=time(18, 45)),
            TradingSession(name="evening", start=time(19, 0), end=time(23, 50)),
        ]
    )


def parse_session_options(values: list[str] | None) -> IntradaySessionModel:
    if not values:
        return default_moex_futures_session_model()

    sessions: list[TradingSession] = []
    for value in values:
        try:
            name, bounds = value.split("=", maxsplit=1)
            start_text, end_text = bounds.split("-", maxsplit=1)
        except ValueError as exc:
            raise InputDataError(
                f"Session values must look like name=HH:MM-HH:MM, got {value!r}"
            ) from exc
        sessions.append(
            TradingSession(
                name=name.strip(),
                start=start_text.strip(),
                end=end_text.strip(),
            )
        )
    if not sessions:
        return default_moex_futures_session_model()
    return IntradaySessionModel(sessions=sessions)


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", ""))
    raise InputDataError(f"Unsupported timestamp value: {value!r}")


def _unique_string(frame: pl.DataFrame, column: str) -> str | None:
    if column not in frame.columns:
        return None
    values = frame.get_column(column).drop_nulls().unique().to_list()
    return str(values[0]) if len(values) == 1 else None


def _near_session_end(value: datetime, session: TradingSession, bar_minutes: int) -> bool:
    end_dt = datetime.combine(value.date(), session.end)
    return value >= end_dt - timedelta(minutes=bar_minutes)


def _near_session_start(value: datetime, session: TradingSession, bar_minutes: int) -> bool:
    start_dt = datetime.combine(value.date(), session.start)
    return value <= start_dt + timedelta(minutes=bar_minutes)


def _is_expected_session_break(
    *,
    prev_ts: datetime,
    next_ts: datetime,
    prev_session: TradingSession | None,
    next_session: TradingSession | None,
    session_model: IntradaySessionModel,
    bar_minutes: int,
) -> bool:
    if prev_session is None or next_session is None:
        return False
    if prev_ts.date() != next_ts.date():
        return False
    prev_index = session_model.session_index(prev_session)
    next_index = session_model.session_index(next_session)
    if prev_index is None or next_index is None or next_index != prev_index + 1:
        return False
    return _near_session_end(prev_ts, prev_session, bar_minutes) and _near_session_start(
        next_ts,
        next_session,
        bar_minutes,
    )
