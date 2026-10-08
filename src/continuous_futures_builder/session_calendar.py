"""Observed sessions are diagnostics; expected sessions are independent inputs."""

from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any

import polars as pl


def observe_sessions(
    timestamps: list[datetime], *, main_start: time, main_end: time, short_day_ratio: float = 0.8
) -> dict[str, Any]:
    dates = sorted({value.date() for value in timestamps})
    observed = set(dates)
    missing = []
    if dates:
        day = dates[0]
        while day <= dates[-1]:
            if day.weekday() < 5 and day not in observed:
                missing.append(day.isoformat())
            day += timedelta(days=1)
    counts = {day: 0 for day in dates}
    bounds = {}
    for value in timestamps:
        if main_start <= value.time() <= main_end:
            counts[value.date()] += 1
            start, end = bounds.get(value.date(), (value, value))
            bounds[value.date()] = (min(start, value), max(end, value))
    positive = [value for value in counts.values() if value > 0]
    expected_count = int(median(positive)) if positive else 0
    durations = {day: (end - start).total_seconds() for day, (start, end) in bounds.items()}
    positive_durations = [value for value in durations.values() if value > 0]
    expected_duration = median(positive_durations) if positive_durations else 0
    return {
        "calendar_type": "observed_dataset_sessions",
        "trading_calendar": "observed",
        "trading_dates": [day.isoformat() for day in dates],
        "holiday_dates": [],
        "short_day_dates": [],
        "unobserved_weekdays": missing,
        "short_session_candidates": [
            day.isoformat()
            for day, count in counts.items()
            if (
                0 < count < expected_count * short_day_ratio
                or 0 < durations.get(day, 0) < expected_duration * short_day_ratio
            )
        ],
        "observed_main_session_median_bars": expected_count,
    }


def expected_calendar_issues(
    frame: pl.DataFrame,
    calendar: dict[str, Any],
    *,
    from_date: str | None = None,
    till_date: str | None = None,
) -> list[str]:
    if "observed" in str(calendar.get("calendar_type", "")) or "observed" in str(
        calendar.get("trading_calendar", "")
    ):
        return ["Expected calendar must be independent of the checked dataset"]
    declared = {date.fromisoformat(value) for value in calendar.get("trading_dates", [])}
    if not declared:
        return ["Expected calendar requires explicit trading_dates"]
    rows = frame["ts"].to_list()
    observed = {value.date() for value in rows}
    start = date.fromisoformat(from_date) if from_date else min(declared)
    end = date.fromisoformat(till_date) if till_date else max(declared)
    expected = {day for day in declared if start <= day <= end}
    issues = [f"Missing expected trading session: {day}" for day in sorted(expected - observed)]
    issues.extend(f"Unexpected trading session: {day}" for day in sorted(observed - declared))
    step = calendar.get("bar_minutes")
    if step is not None:
        step = int(step)
        if step <= 0:
            return issues + ["Expected calendar bar_minutes must be positive"]
        windows = calendar.get("session_windows") or [
            {"start": calendar["main_session_start"], "end": calendar["main_session_end"]}
        ]
        actual = set(rows)
        missing_count = 0
        for day in expected:
            for window in windows:
                current = datetime.combine(day, time.fromisoformat(window["start"]))
                deadline = datetime.combine(day, time.fromisoformat(window["end"]))
                if deadline <= current:
                    issues.append(f"Expected calendar has invalid session window: {window}")
                    continue
                while current < deadline:
                    missing_count += current not in actual
                    current += timedelta(minutes=step)
        if missing_count:
            issues.append(f"Missing expected session bars: {missing_count}")
    return issues
