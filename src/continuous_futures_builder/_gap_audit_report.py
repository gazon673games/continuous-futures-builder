from __future__ import annotations

import json
from typing import Any

from continuous_futures_builder._gap_audit_contracts import (
    build_contract_month_audit,
)
from continuous_futures_builder._gap_audit_core import (
    IntradayGapAuditConfig,
    IntradayGapAuditResult,
    _load_dataset,
    _load_or_compute_session_gaps,
)
from continuous_futures_builder._gap_audit_coverage import (
    build_decision_time_coverage,
)
from continuous_futures_builder._gap_audit_summary import (
    _json_default,
    _markdown_table,
    build_audit_summary,
)


def build_markdown_report(summary: dict[str, Any]) -> str:
    worst_columns = [
        "active_contract",
        "month",
        "usable_decision_day_pct",
        "unexpected_intraday_gap_count",
        "unknown_gap_count",
        "recommendation",
    ]
    excluded_columns = ["active_contract", "month", "recommendation", "usable_decision_day_pct"]
    recommended = summary.get("recommended_start_date")
    return "\n".join(
        [
            "# Intraday Gap Audit",
            "",
            "## Dataset",
            f"- continuous_symbol: {summary.get('continuous_symbol')}",
            f"- timeframe: {summary.get('timeframe')}",
            f"- dataset_path: {summary.get('dataset_path')}",
            "",
            "## Purpose",
            "Assess whether the nearest-contract 5m dataset is suitable for the first "
            "decision-time research backtest.",
            "",
            "## Decision-Time Assumptions",
            f"- decision_time: {summary.get('decision_time')}",
            f"- decision_tolerance_minutes: {summary.get('decision_tolerance_minutes')}",
            f"- sma_window_bars: {summary.get('sma_window_bars')}",
            f"- bar_minutes: {summary.get('bar_minutes')}",
            "",
            "## Overall Coverage",
            f"- total_trading_days: {summary.get('total_trading_days')}",
            f"- usable_decision_days: {summary.get('usable_decision_days')}",
            f"- unusable_decision_days: {summary.get('unusable_decision_days')}",
            f"- usable_decision_day_pct: {summary.get('usable_decision_day_pct')}",
            "",
            "## Gap Summary",
            _markdown_table(
                summary.get("bad_gap_counts_by_contract", []),
                ["active_contract", "bad_gap_count"],
            ),
            "## Contract/Month Findings",
            _markdown_table(summary.get("worst_contract_months", []), worst_columns),
            "## Decision-Time Coverage",
            "See `decision_time_coverage.csv` and `decision_time_coverage.parquet`.",
            "",
            "## Recommended Start Date",
            f"- first_date_with_30_consecutive_usable_decision_days: "
            f"{summary.get('first_date_with_30_consecutive_usable_decision_days')}",
            f"- first_date_after_last_exclude_month: "
            f"{summary.get('first_date_after_last_exclude_month')}",
            f"- first_date_after_last_bad_gap_near_decision: "
            f"{summary.get('first_date_after_last_bad_gap_near_decision')}",
            f"- recommended_start_date: {recommended}",
            "",
            "## Excluded or Warning Periods",
            _markdown_table(summary.get("excluded_periods", []), excluded_columns),
            "## Interpretation",
            "The recommendation is a conservative research-data filter. It does not repair "
            "data, prove exchange completeness, or model tradeability.",
            "",
            "## Next Step",
            "Review excluded months and then pass only the recommended date range to the "
            "first backtest experiment.",
            "",
        ]
    )


def write_gap_audit_reports(
    config: IntradayGapAuditConfig,
) -> IntradayGapAuditResult:
    dataset = _load_dataset(config.dataset_path)
    session_gaps = _load_or_compute_session_gaps(
        dataset,
        config.session_gaps_path,
        bar_minutes=config.bar_minutes,
    )
    decision_coverage = build_decision_time_coverage(
        dataset,
        session_gaps,
        decision_time=config.decision_time,
        decision_tolerance_minutes=config.decision_tolerance_minutes,
        sma_window_bars=config.sma_window_bars,
    )
    contract_month = build_contract_month_audit(dataset, session_gaps, decision_coverage)
    summary = build_audit_summary(dataset, session_gaps, decision_coverage, contract_month, config)

    config.report_root.mkdir(parents=True, exist_ok=True)
    summary_json = config.report_root / "gap_audit_summary.json"
    by_month_csv = config.report_root / "gap_audit_by_contract_month.csv"
    by_month_parquet = config.report_root / "gap_audit_by_contract_month.parquet"
    coverage_csv = config.report_root / "decision_time_coverage.csv"
    coverage_parquet = config.report_root / "decision_time_coverage.parquet"
    report_md = config.report_root / "gap_audit_report.md"

    summary_json.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )
    contract_month.write_csv(by_month_csv)
    contract_month.write_parquet(by_month_parquet)
    decision_coverage.write_csv(coverage_csv)
    decision_coverage.write_parquet(coverage_parquet)
    report_md.write_text(build_markdown_report(summary), encoding="utf-8")

    return IntradayGapAuditResult(
        report_root=config.report_root,
        gap_audit_summary_json=summary_json,
        gap_audit_by_contract_month_csv=by_month_csv,
        gap_audit_by_contract_month_parquet=by_month_parquet,
        decision_time_coverage_csv=coverage_csv,
        decision_time_coverage_parquet=coverage_parquet,
        gap_audit_report_md=report_md,
        total_trading_days=int(summary["total_trading_days"]),
        usable_decision_days=int(summary["usable_decision_days"]),
        unusable_decision_days=int(summary["unusable_decision_days"]),
        usable_decision_day_pct=summary["usable_decision_day_pct"],
        recommended_start_date=summary["recommended_start_date"],
        first_date_with_30_consecutive_usable_decision_days=summary[
            "first_date_with_30_consecutive_usable_decision_days"
        ],
        first_date_after_last_exclude_month=summary["first_date_after_last_exclude_month"],
        first_date_after_last_bad_gap_near_decision=summary[
            "first_date_after_last_bad_gap_near_decision"
        ],
        worst_contract_months=summary["worst_contract_months"],
        excluded_periods=summary["excluded_periods"],
    )
