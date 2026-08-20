from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder.exceptions import InputDataError
from continuous_futures_builder.models import (
    ContractCandleSummary,
    RollInvestigationSummary,
    RollReportRow,
)
from continuous_futures_builder.roll_report import build_roll_report

CONTINUOUS_WINDOW_COLUMNS = (
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "active_contract",
    "source_contract_symbol",
    "roll_id",
    "roll_flag",
    "roll_date",
    "adjustment_mode",
    "adjustment_factor",
)
CANDLE_WINDOW_COLUMNS = ("ts", "open", "high", "low", "close", "volume", "value")
CANDIDATE_COLUMNS = (
    "secid",
    "metadata_found",
    "candles_found",
    "row_count",
    "min_ts",
    "max_ts",
    "would_be_active_segment_start",
    "would_be_active_segment_end",
    "active_segment_row_count",
    "pruned",
    "pruning_reason",
)
QUARTER_MONTH_CODES = ("H", "M", "U", "Z")


@dataclass
class RollInvestigation:
    summary: RollInvestigationSummary
    continuous_window: pl.DataFrame
    from_contract_window: pl.DataFrame
    to_contract_window: pl.DataFrame
    candidate_contracts_frame: pl.DataFrame


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _single_value(frame: pl.DataFrame, column: str, default: str) -> str:
    if column not in frame.columns:
        return default
    values = frame.get_column(column).drop_nulls().unique().to_list()
    if not values:
        return default
    if len(values) == 1:
        return str(values[0])
    return default


def _available_columns(frame: pl.DataFrame, columns: tuple[str, ...]) -> list[str]:
    return [column for column in columns if column in frame.columns]


def _find_transition(
    frame: pl.DataFrame,
    *,
    roll_date: date,
    from_contract: str,
    to_contract: str,
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    indexed = frame.sort("ts").with_row_index("__row_nr")
    previous: dict[str, Any] | None = None
    for row in indexed.iter_rows(named=True):
        if previous is None:
            previous = row
            continue

        row_roll_date = _as_date(row.get("roll_date")) or _as_date(row.get("ts"))
        row_ts = _as_date(row.get("ts"))
        if (
            str(previous.get("active_contract")) == from_contract
            and str(row.get("active_contract")) == to_contract
            and (row_roll_date == roll_date or row_ts == roll_date)
        ):
            return int(row["__row_nr"]), previous, row
        previous = row

    msg = (
        f"Could not find transition {from_contract} -> {to_contract} "
        f"on roll_date={roll_date} in continuous output"
    )
    raise InputDataError(msg)


def _matching_roll_report_row(
    path: str | Path,
    *,
    roll_date: date,
    from_contract: str,
    to_contract: str,
) -> RollReportRow | None:
    for row in build_roll_report(path):
        if (
            _as_date(row.roll_date) == roll_date
            and row.from_contract == from_contract
            and row.to_contract == to_contract
        ):
            return row
    return None


def _continuous_window(frame: pl.DataFrame, roll_index: int, window_rows: int) -> pl.DataFrame:
    sorted_frame = frame.sort("ts")
    start = max(0, roll_index - window_rows)
    length = min(sorted_frame.height - start, window_rows * 2 + 1)
    return sorted_frame.slice(start, length).select(
        _available_columns(sorted_frame, CONTINUOUS_WINDOW_COLUMNS)
    )


def _board_from_continuous(frame: pl.DataFrame, secid: str) -> str | None:
    if "active_board" not in frame.columns or "active_contract" not in frame.columns:
        return None
    values = (
        frame.filter(pl.col("active_contract") == secid)
        .get_column("active_board")
        .drop_nulls()
        .unique()
        .to_list()
    )
    return str(values[0]) if values else None


def _empty_candle_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "ts": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "value": pl.Float64,
        }
    )


def _summary_for_secid(
    summaries: list[ContractCandleSummary],
    secid: str,
) -> ContractCandleSummary | None:
    matches = [summary for summary in summaries if summary.secid.upper() == secid.upper()]
    if not matches:
        return None
    return max(matches, key=lambda summary: summary.row_count)
