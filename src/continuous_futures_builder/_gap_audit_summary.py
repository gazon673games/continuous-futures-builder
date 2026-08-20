from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import polars as pl

from continuous_futures_builder._gap_audit_contracts import (
    _first_trading_date_after,
    _first_trading_date_after_month,
    _last_excluded_month,
    first_date_with_consecutive_usable_days,
)
from continuous_futures_builder._gap_audit_core import (
    REASON_GAP_OVERLAPS_WINDOW,
    REASON_UNKNOWN_GAP_OVERLAPS_WINDOW,
    RECOMMENDATION_EXCLUDE_CONTRACT,
    RECOMMENDATION_EXCLUDE_MONTH,
    IntradayGapAuditConfig,
    _as_date,
    _bad_gap_records,
)


def _last_bad_gap_near_decision_date(decision_coverage: pl.DataFrame) -> date | None:
    bad = decision_coverage.filter(
        pl.col("reason").str.contains(REASON_GAP_OVERLAPS_WINDOW)
        | pl.col("reason").str.contains(REASON_UNKNOWN_GAP_OVERLAPS_WINDOW)
    )
    if bad.height == 0:
        return None
    return _as_date(bad.get_column("trading_date").max())


def _pct(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def build_audit_summary(
    dataset: pl.DataFrame,
    session_gaps: pl.DataFrame,
    decision_coverage: pl.DataFrame,
    contract_month: pl.DataFrame,
    config: IntradayGapAuditConfig,
) -> dict[str, Any]:
    total_days = decision_coverage.height
    usable_days = decision_coverage.filter(pl.col("decision_day_usable")).height
    unusable_days = total_days - usable_days
    usable_pct = usable_days / total_days if total_days else None
    first_30 = first_date_with_consecutive_usable_days(
        decision_coverage,
        consecutive_days=config.consecutive_usable_days,
    )
    last_excluded_month = _last_excluded_month(contract_month)
    after_last_exclude = _first_trading_date_after_month(decision_coverage, last_excluded_month)
    last_bad_gap_date = _last_bad_gap_near_decision_date(decision_coverage)
    after_last_bad_gap = _first_trading_date_after(decision_coverage, last_bad_gap_date)
    candidates = [candidate for candidate in (first_30, after_last_exclude) if candidate]
    recommended = max(candidates) if candidates else None
    if contract_month.height:
        worst_rows = (
            contract_month.sort(
                ["usable_decision_day_pct", "unexpected_intraday_gap_count"],
                descending=[False, True],
                nulls_last=True,
            )
            .head(10)
            .to_dicts()
        )
        excluded = contract_month.filter(
            pl.col("recommendation").is_in(
                [RECOMMENDATION_EXCLUDE_MONTH, RECOMMENDATION_EXCLUDE_CONTRACT]
            )
        ).to_dicts()
    else:
        worst_rows = []
        excluded = []

    by_contract_gap_counts = []
    if session_gaps.height:
        gap_counts: dict[str, int] = {}
        for row in _bad_gap_records(session_gaps):
            contract = str(row.get("prev_active_contract") or row.get("next_active_contract"))
            gap_counts[contract] = gap_counts.get(contract, 0) + 1
        by_contract_gap_counts = [
            {"active_contract": contract, "bad_gap_count": count}
            for contract, count in sorted(
                gap_counts.items(), key=lambda item: item[1], reverse=True
            )
        ]

    return {
        "continuous_symbol": _single_value(dataset, "continuous_symbol"),
        "timeframe": _single_value(dataset, "timeframe"),
        "dataset_path": str(config.dataset_path),
        "session_gaps_path": str(config.session_gaps_path) if config.session_gaps_path else None,
        "decision_time": config.decision_time.isoformat(timespec="minutes"),
        "decision_tolerance_minutes": config.decision_tolerance_minutes,
        "sma_window_bars": config.sma_window_bars,
        "bar_minutes": config.bar_minutes,
        "total_trading_days": total_days,
        "usable_decision_days": usable_days,
        "unusable_decision_days": unusable_days,
        "usable_decision_day_pct": _pct(usable_pct),
        "first_date_with_30_consecutive_usable_decision_days": first_30,
        "first_date_after_last_exclude_month": after_last_exclude,
        "first_date_after_last_bad_gap_near_decision": after_last_bad_gap,
        "last_excluded_month": last_excluded_month,
        "recommended_start_date": recommended,
        "worst_contract_months": worst_rows,
        "excluded_periods": excluded,
        "bad_gap_counts_by_contract": by_contract_gap_counts,
    }


def _single_value(frame: pl.DataFrame, column: str) -> str | None:
    if column not in frame.columns:
        return None
    values = frame.get_column(column).drop_nulls().unique().to_list()
    return str(values[0]) if len(values) == 1 else None


def _json_default(value: object) -> str:
    if isinstance(value, date | datetime | time):
        return value.isoformat()
    return str(value)


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "none\n"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines) + "\n"
