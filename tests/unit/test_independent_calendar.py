"""Наблюдения свечей не подтверждают полноту этих же свечей."""

from datetime import datetime, time

import polars as pl

from continuous_futures_builder.session_calendar import expected_calendar_issues, observe_sessions


def test_observed_calendar_does_not_classify_absence_as_holiday():
    observed = observe_sessions(
        [datetime(2024, 1, 2, 10), datetime(2024, 1, 4, 10)],
        main_start=time(10),
        main_end=time(18, 50),
    )
    assert observed["holiday_dates"] == observed["short_day_dates"] == []
    assert observed["unobserved_weekdays"] == ["2024-01-03"]
    assert expected_calendar_issues(pl.DataFrame({"ts": [datetime(2024, 1, 2, 10)]}), observed)


def test_missing_whole_day_and_bar_fail_against_independent_schedule():
    frame = pl.DataFrame({"ts": [datetime(2024, 1, 2, 10)]})
    calendar = {
        "trading_dates": ["2024-01-02", "2024-01-03"],
        "bar_minutes": 5,
        "session_windows": [{"start": "10:00", "end": "10:10"}],
    }
    issues = expected_calendar_issues(
        frame, calendar, from_date="2024-01-02", till_date="2024-01-03"
    )
    assert any("2024-01-03" in issue for issue in issues)
    assert any("bar" in issue.lower() for issue in issues)
