from __future__ import annotations

import calendar
import json
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
    bad_gaps = _bad_gap_records(session_gaps)
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
        bars_before_decision = (
            sum(1 for row in rows if _as_datetime(row["ts"]) <= decision_bar_ts)
            if decision_bar_ts is not None
            else 0
        )
        sma_window_available = decision_bar_ts is not None and bars_before_decision >= (
            sma_window_bars
        )
        window_start_ts = None
        window_bad_gaps: list[dict[str, Any]] = []
        if sma_window_available and decision_bar_ts is not None:
            prior_rows = [row for row in rows if _as_datetime(row["ts"]) <= decision_bar_ts][
                -sma_window_bars:
            ]
            window_start_ts = _as_datetime(prior_rows[0]["ts"])
            window_bad_gaps = [
                gap
                for gap in bad_gaps
                if _as_datetime(gap["prev_ts"]).date() == trading_date
                and _gap_overlaps_window(gap, window_start_ts, decision_bar_ts)
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
    for item in dataset.iter_rows(named=True):
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
