from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import polars as pl

from continuous_futures_builder._gap_audit_core import (
    REASON_GAP_OVERLAPS_WINDOW,
    REASON_MISSING_DECISION_BAR,
    REASON_NOT_ENOUGH_BARS,
    REASON_OK,
    REASON_UNKNOWN_GAP_OVERLAPS_WINDOW,
    _as_datetime,
    _bad_gap_records,
    _find_decision_row,
    _gap_overlaps_window,
    _month_key,
    _rows_by_trading_date,
)
from continuous_futures_builder.nearest_gap_diagnostics import (
    GAP_UNEXPECTED_INTRADAY,
    GAP_UNKNOWN,
)


def _active_contract_for_day(
    rows: list[dict[str, Any]],
    decision_row: dict[str, Any] | None,
    decision_dt: datetime,
) -> str:
    if decision_row is not None:
        return str(decision_row["active_contract"])
    prior = [row for row in rows if _as_datetime(row["ts"]) <= decision_dt]
    if prior:
        return str(max(prior, key=lambda row: _as_datetime(row["ts"]))["active_contract"])
    return str(rows[0]["active_contract"])


def build_decision_time_coverage(
    dataset: pl.DataFrame,
    session_gaps: pl.DataFrame,
    *,
    decision_time: time = time(13, 30),
    decision_tolerance_minutes: int = 5,
    sma_window_bars: int = 42,
) -> pl.DataFrame:
    bad_gaps_by_day: dict[date, list[dict[str, Any]]] = {}
    for gap in _bad_gap_records(session_gaps):
        day = _as_datetime(gap["prev_ts"]).date()
        bad_gaps_by_day.setdefault(day, []).append(gap)
    grouped = _rows_by_trading_date(dataset)
    coverage_rows: list[dict[str, Any]] = []
    tolerance = timedelta(minutes=decision_tolerance_minutes)

    for trading_date in sorted(grouped):
        rows = grouped[trading_date]
        decision_dt = datetime.combine(trading_date, decision_time)
        decision_row = _find_decision_row(
            rows,
            decision_dt=decision_dt,
            tolerance=tolerance,
        )
        active_contract = _active_contract_for_day(rows, decision_row, decision_dt)
        decision_bar_ts = _as_datetime(decision_row["ts"]) if decision_row else None
        prior_rows = (
            [row for row in rows if _as_datetime(row["ts"]) <= decision_bar_ts]
            if decision_bar_ts is not None
            else []
        )
        bars_before_decision = len(prior_rows)
        sma_window_available = (
            decision_bar_ts is not None and bars_before_decision >= sma_window_bars
        )
        window_start_ts = None
        window_bad_gaps: list[dict[str, Any]] = []
        if sma_window_available and decision_bar_ts is not None:
            window_start_ts = _as_datetime(prior_rows[-sma_window_bars]["ts"])
            window_bad_gaps = [
                gap
                for gap in bad_gaps_by_day.get(trading_date, ())
                if _gap_overlaps_window(gap, window_start_ts, decision_bar_ts)
            ]

        gap_overlaps = any(
            gap.get("classification") == GAP_UNEXPECTED_INTRADAY for gap in window_bad_gaps
        )
        unknown_gap_overlaps = any(
            gap.get("classification") == GAP_UNKNOWN for gap in window_bad_gaps
        )
        decision_bar_available = decision_row is not None

        reasons: list[str] = []
        if not decision_bar_available:
            reasons.append(REASON_MISSING_DECISION_BAR)
        if not sma_window_available:
            reasons.append(REASON_NOT_ENOUGH_BARS)
        if gap_overlaps:
            reasons.append(REASON_GAP_OVERLAPS_WINDOW)
        if unknown_gap_overlaps:
            reasons.append(REASON_UNKNOWN_GAP_OVERLAPS_WINDOW)
        decision_day_usable = not reasons
        if not reasons:
            reasons.append(REASON_OK)

        coverage_rows.append(
            {
                "trading_date": trading_date,
                "active_contract": active_contract,
                "decision_time": decision_time.isoformat(timespec="minutes"),
                "decision_bar_ts": decision_bar_ts,
                "decision_bar_available": decision_bar_available,
                "bars_before_decision_same_day": bars_before_decision,
                "sma_window_available": sma_window_available,
                "gap_overlaps_sma_window": gap_overlaps,
                "unknown_gap_overlaps_sma_window": unknown_gap_overlaps,
                "decision_day_usable": decision_day_usable,
                "reason": ";".join(reasons),
                "sma_window_start_ts": window_start_ts,
            }
        )

    return pl.DataFrame(coverage_rows)


def _gap_summary_by_contract_month(
    session_gaps: pl.DataFrame,
) -> dict[tuple[str, str], dict[str, Any]]:
    summaries: dict[tuple[str, str], dict[str, Any]] = {}
    for gap in _bad_gap_records(session_gaps):
        contract = str(gap.get("prev_active_contract") or gap.get("next_active_contract") or "")
        prev_ts = _as_datetime(gap["prev_ts"])
        key = (contract, _month_key(prev_ts))
        summary = summaries.setdefault(
            key,
            {
                "unexpected_intraday_gap_count": 0,
                "unknown_gap_count": 0,
                "max_unexpected_gap_minutes": None,
                "first_unexpected_gap_ts": None,
                "last_unexpected_gap_ts": None,
                "affected_days": set(),
            },
        )
        if gap.get("classification") == GAP_UNEXPECTED_INTRADAY:
            summary["unexpected_intraday_gap_count"] += 1
            current_max = summary["max_unexpected_gap_minutes"]
            gap_minutes = float(gap["gap_minutes"])
            summary["max_unexpected_gap_minutes"] = (
                gap_minutes if current_max is None else max(current_max, gap_minutes)
            )
        elif gap.get("classification") == GAP_UNKNOWN:
            summary["unknown_gap_count"] += 1
        first_ts = summary["first_unexpected_gap_ts"]
        last_ts = summary["last_unexpected_gap_ts"]
        summary["first_unexpected_gap_ts"] = prev_ts if first_ts is None else min(first_ts, prev_ts)
        summary["last_unexpected_gap_ts"] = prev_ts if last_ts is None else max(last_ts, prev_ts)
        summary["affected_days"].add(prev_ts.date())
    return summaries


def _base_contract_month_rows(dataset: pl.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for item in dataset.select("ts", "active_contract").iter_rows(named=True):
        ts = _as_datetime(item["ts"])
        contract = str(item["active_contract"])
        key = (contract, _month_key(ts))
        row = rows.setdefault(
            key,
            {
                "active_contract": contract,
                "month": _month_key(ts),
                "row_count": 0,
                "trading_days": set(),
            },
        )
        row["row_count"] += 1
        row["trading_days"].add(ts.date())
    return rows
