from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from continuous_futures_builder._gap_audit_core import (
    RECOMMENDATION_EXCLUDE_CONTRACT,
    RECOMMENDATION_EXCLUDE_MONTH,
    RECOMMENDATION_USABLE,
    RECOMMENDATION_WARNING,
    _as_date,
    _month_end,
    _month_key,
)
from continuous_futures_builder._gap_audit_coverage import (
    _base_contract_month_rows,
    _gap_summary_by_contract_month,
)


def _decision_counts_by_contract_month(
    decision_coverage: pl.DataFrame,
) -> dict[tuple[str, str], dict[str, int]]:
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for row in decision_coverage.iter_rows(named=True):
        trading_date = _as_date(row["trading_date"])
        key = (str(row["active_contract"]), _month_key(trading_date))
        item = counts.setdefault(
            key,
            {"decision_days": 0, "usable_decision_days": 0, "unusable_decision_days": 0},
        )
        item["decision_days"] += 1
        if bool(row["decision_day_usable"]):
            item["usable_decision_days"] += 1
        else:
            item["unusable_decision_days"] += 1
    return counts


def _month_recommendation(
    *,
    unexpected_intraday_gap_count: int,
    unknown_gap_count: int,
    usable_decision_day_pct: float | None,
) -> str:
    pct = usable_decision_day_pct or 0.0
    if unexpected_intraday_gap_count == 0 and unknown_gap_count == 0 and pct >= 0.98:
        return RECOMMENDATION_USABLE
    if pct >= 0.90:
        return RECOMMENDATION_WARNING
    return RECOMMENDATION_EXCLUDE_MONTH


def build_contract_month_audit(
    dataset: pl.DataFrame,
    session_gaps: pl.DataFrame,
    decision_coverage: pl.DataFrame,
) -> pl.DataFrame:
    base_rows = _base_contract_month_rows(dataset)
    gap_rows = _gap_summary_by_contract_month(session_gaps)
    decision_counts = _decision_counts_by_contract_month(decision_coverage)
    output_rows: list[dict[str, Any]] = []

    for key in sorted(base_rows):
        base = base_rows[key]
        gap_summary = gap_rows.get(key, {})
        decision_summary = decision_counts.get(key, {})
        decision_days = int(decision_summary.get("decision_days", 0))
        usable_days = int(decision_summary.get("usable_decision_days", 0))
        unusable_days = int(decision_summary.get("unusable_decision_days", 0))
        usable_pct = usable_days / decision_days if decision_days else None
        unexpected_count = int(gap_summary.get("unexpected_intraday_gap_count", 0))
        unknown_count = int(gap_summary.get("unknown_gap_count", 0))
        recommendation = _month_recommendation(
            unexpected_intraday_gap_count=unexpected_count,
            unknown_gap_count=unknown_count,
            usable_decision_day_pct=usable_pct,
        )
        output_rows.append(
            {
                "active_contract": base["active_contract"],
                "month": base["month"],
                "row_count": int(base["row_count"]),
                "trading_day_count": len(base["trading_days"]),
                "unexpected_intraday_gap_count": unexpected_count,
                "unknown_gap_count": unknown_count,
                "max_unexpected_gap_minutes": gap_summary.get("max_unexpected_gap_minutes"),
                "first_unexpected_gap_ts": gap_summary.get("first_unexpected_gap_ts"),
                "last_unexpected_gap_ts": gap_summary.get("last_unexpected_gap_ts"),
                "affected_trading_day_count": len(gap_summary.get("affected_days", set())),
                "decision_days": decision_days,
                "usable_decision_days": usable_days,
                "unusable_decision_days": unusable_days,
                "usable_decision_day_pct": usable_pct,
                "recommendation": recommendation,
            }
        )

    result = pl.DataFrame(output_rows)
    if result.height == 0:
        return result

    contract_recommendations: dict[str, str] = {}
    for contract in result.get_column("active_contract").unique().to_list():
        contract_rows = result.filter(pl.col("active_contract") == contract)
        exclude_count = contract_rows.filter(
            pl.col("recommendation") == RECOMMENDATION_EXCLUDE_MONTH
        ).height
        if exclude_count > contract_rows.height / 2:
            contract_recommendations[str(contract)] = RECOMMENDATION_EXCLUDE_CONTRACT

    if not contract_recommendations:
        return result.sort(["active_contract", "month"])

    return result.with_columns(
        pl.when(pl.col("active_contract").is_in(list(contract_recommendations)))
        .then(pl.lit(RECOMMENDATION_EXCLUDE_CONTRACT))
        .otherwise(pl.col("recommendation"))
        .alias("recommendation")
    ).sort(["active_contract", "month"])


def first_date_with_consecutive_usable_days(
    decision_coverage: pl.DataFrame,
    *,
    consecutive_days: int,
) -> date | None:
    current_start: date | None = None
    current_count = 0
    for row in decision_coverage.sort("trading_date").iter_rows(named=True):
        trading_date = _as_date(row["trading_date"])
        if bool(row["decision_day_usable"]):
            if current_count == 0:
                current_start = trading_date
            current_count += 1
            if current_count >= consecutive_days:
                return current_start
        else:
            current_start = None
            current_count = 0
    return None


def _first_trading_date_after_month(
    decision_coverage: pl.DataFrame,
    month: str | None,
) -> date | None:
    if month is None:
        return (
            _as_date(decision_coverage.get_column("trading_date").min())
            if decision_coverage.height
            else None
        )
    after = _month_end(month)
    for value in decision_coverage.sort("trading_date").get_column("trading_date").to_list():
        trading_date = _as_date(value)
        if trading_date > after:
            return trading_date
    return None


def _first_trading_date_after(
    decision_coverage: pl.DataFrame,
    value: date | None,
) -> date | None:
    if value is None:
        return None
    for item in decision_coverage.sort("trading_date").get_column("trading_date").to_list():
        trading_date = _as_date(item)
        if trading_date > value:
            return trading_date
    return None


def _last_excluded_month(contract_month: pl.DataFrame) -> str | None:
    if contract_month.height == 0:
        return None
    excluded = contract_month.filter(
        pl.col("recommendation").is_in(
            [RECOMMENDATION_EXCLUDE_MONTH, RECOMMENDATION_EXCLUDE_CONTRACT]
        )
    )
    if excluded.height == 0:
        return None
    return str(excluded.get_column("month").max())
