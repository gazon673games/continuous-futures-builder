from __future__ import annotations

import re
from datetime import date
from typing import Any

import polars as pl

from continuous_futures_builder._investigation_core import (
    CANDLE_WINDOW_COLUMNS,
    QUARTER_MONTH_CODES,
    _as_date,
    _available_columns,
    _empty_candle_frame,
    _summary_for_secid,
)
from continuous_futures_builder.data_access import ContinuousFuturesDataAccess
from continuous_futures_builder.exceptions import ContinuousFuturesError
from continuous_futures_builder.models import (
    ContractCandleSummary,
    SourceContractInvestigation,
)
from continuous_futures_builder.prefixes import (
    FUTURES_MONTH_CODES,
)


def _load_contract_candles(
    data_access: ContinuousFuturesDataAccess,
    *,
    exchange: str,
    engine: str,
    market: str,
    timeframe: str,
    secid: str,
    board: str | None,
    summaries: list[ContractCandleSummary],
    warnings: list[str],
) -> tuple[pl.DataFrame, str | None]:
    summary = _summary_for_secid(summaries, secid)
    effective_board = board or (summary.board if summary is not None else None)
    if effective_board is None:
        warnings.append(f"{secid}: no board found in continuous output or candle catalog")
        return _empty_candle_frame(), None

    try:
        return (
            data_access.load_contract_candles(
                exchange=exchange,
                engine=engine,
                market=market,
                board=effective_board,
                symbol=secid,
                timeframe=timeframe,
            ),
            effective_board,
        )
    except ContinuousFuturesError as exc:
        warnings.append(f"{secid}: failed to load source candles: {exc}")
        return _empty_candle_frame(), effective_board


def _source_window(
    candles: pl.DataFrame,
    *,
    roll_date: date,
    side: str,
    window_rows: int,
) -> pl.DataFrame:
    if candles.height == 0 or "ts" not in candles.columns:
        return _empty_candle_frame()
    frame = candles.sort("ts").with_columns(pl.col("ts").cast(pl.Date).alias("__ts_date"))
    if side == "from":
        window = frame.filter(pl.col("__ts_date") <= roll_date).tail(window_rows)
    else:
        window = frame.filter(pl.col("__ts_date") >= roll_date).head(window_rows)
    return window.drop("__ts_date").select(_available_columns(window, CANDLE_WINDOW_COLUMNS))


def _source_summary(
    *,
    secid: str,
    board: str | None,
    candles: pl.DataFrame,
    close_used_ts: object,
    close_used: float | None,
    window: pl.DataFrame,
) -> SourceContractInvestigation:
    return SourceContractInvestigation(
        secid=secid,
        board=board,
        row_count=candles.height,
        min_ts=candles.get_column("ts").min()
        if candles.height and "ts" in candles.columns
        else None,
        max_ts=candles.get_column("ts").max()
        if candles.height and "ts" in candles.columns
        else None,
        close_used_ts=_as_date(close_used_ts),
        close_used=close_used,
        window_row_count=window.height,
    )


def _metadata_by_secid(
    data_access: ContinuousFuturesDataAccess,
    *,
    exchange: str,
    engine: str,
    market: str,
    metadata_snapshot_ts: str,
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    try:
        metadata = data_access.load_metadata_snapshot(
            exchange=exchange,
            engine=engine,
            market=market,
            snapshot_ts=metadata_snapshot_ts,
        )
    except ContinuousFuturesError as exc:
        warnings.append(f"failed to load metadata snapshot {metadata_snapshot_ts}: {exc}")
        return {}
    return {str(row["secid"]).upper(): row for row in metadata.iter_rows(named=True)}


def _contract_year(secid: str, anchor_year: int) -> int | None:
    secid_norm = secid.strip().upper()
    suffix_match = re.search(r"_(\d{4})$", secid_norm)
    if suffix_match:
        return int(suffix_match.group(1))

    match = re.fullmatch(rf"[A-Z]+?[{FUTURES_MONTH_CODES}](\d+)[A-Z]?", secid_norm)
    if not match:
        return None

    year_code = match.group(1)
    if len(year_code) >= 4:
        return int(year_code[-4:])
    if len(year_code) != 1:
        return None

    digit = int(year_code)
    decade = (anchor_year // 10) * 10
    candidates = [decade - 10 + digit, decade + digit, decade + 10 + digit]
    return min(candidates, key=lambda candidate: abs(candidate - anchor_year))


def _month_order(secid: str) -> int:
    secid_norm = secid.strip().upper()
    match = re.fullmatch(rf"[A-Z]+?([{FUTURES_MONTH_CODES}])\d+[A-Z]?(?:_[A-Z0-9]+)?", secid_norm)
    if not match:
        return 99
    code = match.group(1)
    if code in QUARTER_MONTH_CODES:
        return QUARTER_MONTH_CODES.index(code)
    return FUTURES_MONTH_CODES.index(code)


def _candidate_sort_key(secid: str, anchor_year: int) -> tuple[int, int, str]:
    year = _contract_year(secid, anchor_year)
    return (year if year is not None else 9999, _month_order(secid), secid)


def _expected_quarter_contracts(prefix: str, start_year: int, end_year: int) -> set[str]:
    return {
        f"{prefix}{month_code}{year % 10}"
        for year in range(start_year, end_year + 1)
        for month_code in QUARTER_MONTH_CODES
    }
