from __future__ import annotations

import json
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
