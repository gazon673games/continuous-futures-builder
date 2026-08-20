from __future__ import annotations

from datetime import datetime
from typing import Any

import polars as pl

from continuous_futures_builder._gap_diagnostics_core import (
    GAP_EXPECTED_OVERNIGHT_OR_CALENDAR_BREAK,
    GAP_EXPECTED_SESSION_BREAK,
    GAP_ROLL_TRANSITION,
    GAP_UNEXPECTED_INTRADAY,
    GAP_UNKNOWN,
    IntradaySessionModel,
    SessionGapRow,
    SessionGapSummary,
    TradingSession,
    _as_datetime,
    _is_expected_session_break,
    _unique_string,
    default_moex_futures_session_model,
)
from continuous_futures_builder.exceptions import InputDataError


def _classify_gap(
    *,
    prev_ts: datetime,
    next_ts: datetime,
    prev_contract: str | None,
    next_contract: str | None,
    prev_session: TradingSession | None,
    next_session: TradingSession | None,
    session_model: IntradaySessionModel,
    bar_minutes: int,
) -> tuple[str, str | None]:
    same_date = prev_ts.date() == next_ts.date()
    same_contract = prev_contract == next_contract
    if not same_contract:
        return GAP_ROLL_TRANSITION, "active contract changed between adjacent rows"

    if _is_expected_session_break(
        prev_ts=prev_ts,
        next_ts=next_ts,
        prev_session=prev_session,
        next_session=next_session,
        session_model=session_model,
        bar_minutes=bar_minutes,
    ):
        return GAP_EXPECTED_SESSION_BREAK, "gap matches configured same-day session break"

    if not same_date:
        return (
            GAP_EXPECTED_OVERNIGHT_OR_CALENDAR_BREAK,
            "gap crosses a calendar date boundary",
        )

    if prev_session is not None and next_session is not None:
        if prev_session.name == next_session.name:
            return GAP_UNEXPECTED_INTRADAY, "missing bars inside configured session"
        return GAP_UNKNOWN, "same-day cross-session gap is not near configured session boundary"

    return GAP_UNKNOWN, "one or both timestamps are outside configured sessions"


def build_session_gap_rows(
    frame: pl.DataFrame,
    *,
    bar_minutes: int,
    session_model: IntradaySessionModel | None = None,
) -> list[SessionGapRow]:
    effective_model = session_model or default_moex_futures_session_model()
    if frame.height < 2:
        return []
    if "ts" not in frame.columns:
        raise InputDataError("dataset is missing required ts column")

    sorted_frame = frame.sort("ts")
    rows = sorted_frame.iter_rows(named=True)
    gaps: list[SessionGapRow] = []
    previous: dict[str, Any] | None = None
    for row in rows:
        if previous is None:
            previous = row
            continue

        prev_ts = _as_datetime(previous["ts"])
        next_ts = _as_datetime(row["ts"])
        gap_minutes = (next_ts - prev_ts).total_seconds() / 60.0
        if gap_minutes <= bar_minutes:
            previous = row
            continue

        prev_contract = (
            str(previous["active_contract"])
            if previous.get("active_contract") is not None
            else None
        )
        next_contract = (
            str(row["active_contract"]) if row.get("active_contract") is not None else None
        )
        prev_session = effective_model.session_for(prev_ts)
        next_session = effective_model.session_for(next_ts)
        classification, notes = _classify_gap(
            prev_ts=prev_ts,
            next_ts=next_ts,
            prev_contract=prev_contract,
            next_contract=next_contract,
            prev_session=prev_session,
            next_session=next_session,
            session_model=effective_model,
            bar_minutes=bar_minutes,
        )
        gaps.append(
            SessionGapRow(
                prev_ts=prev_ts,
                next_ts=next_ts,
                gap_minutes=gap_minutes,
                prev_active_contract=prev_contract,
                next_active_contract=next_contract,
                classification=classification,
                prev_session=prev_session.name if prev_session is not None else None,
                next_session=next_session.name if next_session is not None else None,
                same_calendar_date=prev_ts.date() == next_ts.date(),
                same_active_contract=prev_contract == next_contract,
                is_roll_transition=prev_contract != next_contract,
                notes=notes,
            )
        )
        previous = row
    return gaps


def session_gap_frame(rows: list[SessionGapRow]) -> pl.DataFrame:
    schema = {
        "prev_ts": pl.Datetime,
        "next_ts": pl.Datetime,
        "gap_minutes": pl.Float64,
        "prev_active_contract": pl.Utf8,
        "next_active_contract": pl.Utf8,
        "classification": pl.Utf8,
        "prev_session": pl.Utf8,
        "next_session": pl.Utf8,
        "same_calendar_date": pl.Boolean,
        "same_active_contract": pl.Boolean,
        "is_roll_transition": pl.Boolean,
        "notes": pl.Utf8,
    }
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame([row.model_dump() for row in rows], schema=schema)


def build_session_gap_summary(
    frame: pl.DataFrame,
    rows: list[SessionGapRow],
    *,
    bar_minutes: int,
    session_model: IntradaySessionModel,
) -> SessionGapSummary:
    counts = {
        GAP_EXPECTED_SESSION_BREAK: 0,
        GAP_EXPECTED_OVERNIGHT_OR_CALENDAR_BREAK: 0,
        GAP_ROLL_TRANSITION: 0,
        GAP_UNEXPECTED_INTRADAY: 0,
        GAP_UNKNOWN: 0,
    }
    for row in rows:
        counts[row.classification] = counts.get(row.classification, 0) + 1

    unexpected = [row for row in rows if row.classification == GAP_UNEXPECTED_INTRADAY]
    max_unexpected_gap = (
        max((row.gap_minutes for row in unexpected), default=None) if unexpected else None
    )
    top_unexpected = sorted(unexpected, key=lambda row: row.gap_minutes, reverse=True)[:10]

    return SessionGapSummary(
        continuous_symbol=_unique_string(frame, "continuous_symbol"),
        timeframe=_unique_string(frame, "timeframe"),
        bar_minutes=bar_minutes,
        session_model=session_model.model_dump(mode="json"),
        total_timestamp_gaps=len(rows),
        expected_session_breaks=counts[GAP_EXPECTED_SESSION_BREAK],
        expected_overnight_or_calendar_breaks=counts[GAP_EXPECTED_OVERNIGHT_OR_CALENDAR_BREAK],
        roll_transition_gaps=counts[GAP_ROLL_TRANSITION],
        unexpected_intraday_gaps=counts[GAP_UNEXPECTED_INTRADAY],
        unknown_gaps=counts[GAP_UNKNOWN],
        max_unexpected_intraday_gap_minutes=max_unexpected_gap,
        top_unexpected_gaps=[row.model_dump(mode="json") for row in top_unexpected],
    )
