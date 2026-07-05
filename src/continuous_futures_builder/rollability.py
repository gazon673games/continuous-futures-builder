from __future__ import annotations

from datetime import date, datetime

import polars as pl

from continuous_futures_builder.models import (
    ContractCandleSummary,
    FuturesContract,
    RollCalendarResult,
    RollEvent,
)
from continuous_futures_builder.roll_calendar import build_fixed_days_roll_calendar


def _trading_dates(frame: pl.DataFrame) -> list[date]:
    return (
        frame.select(pl.col("ts").cast(pl.Date).alias("ts"))
        .unique()
        .sort("ts")
        .get_column("ts")
        .to_list()
    )


def _summary_sort_key(summary: ContractCandleSummary) -> tuple[int, date | datetime]:
    min_ts = summary.min_ts or date.max
    return summary.row_count, min_ts


def _segment_bounds(
    segment_index: int,
    events: list[RollEvent],
) -> tuple[date | None, date | None]:
    start = None if segment_index == 0 else events[segment_index - 1].roll_date
    end = events[segment_index].roll_date if segment_index < len(events) else None
    return start, end


def _segment_row_count(candles: pl.DataFrame, start: date | None, end: date | None) -> int:
    frame = candles.with_columns(pl.col("ts").cast(pl.Date).alias("__ts_date"))
    if start is not None:
        frame = frame.filter(pl.col("__ts_date") >= start)
    if end is not None:
        frame = frame.filter(pl.col("__ts_date") < end)
    return frame.height


def filter_rollable_contracts(
    contracts: list[FuturesContract],
    candles_by_contract: dict[str, pl.DataFrame],
) -> tuple[list[FuturesContract], list[str]]:
    warnings: list[str] = []
    candidates_by_expiration: dict[date, list[tuple[FuturesContract, ContractCandleSummary]]] = {}

    for contract in contracts:
        candles = candles_by_contract[contract.secid]
        dates = _trading_dates(candles)
        if not any(day < contract.expiration_date for day in dates):
            warnings.append(
                f"{contract.secid}: skipped from rollable chain because no candle exists "
                f"before expiration {contract.expiration_date}"
            )
            continue
        summary = ContractCandleSummary(
            secid=contract.secid,
            board=contract.board,
            row_count=candles.height,
            min_ts=min(dates),
            max_ts=max(dates),
        )
        candidates_by_expiration.setdefault(contract.expiration_date, []).append(
            (contract, summary)
        )

    filtered: list[FuturesContract] = []
    for expiration_date in sorted(candidates_by_expiration):
        candidates = candidates_by_expiration[expiration_date]
        chosen, chosen_summary = max(candidates, key=lambda item: _summary_sort_key(item[1]))
        filtered.append(chosen)
        for contract, summary in candidates:
            if contract.secid == chosen.secid and contract.board == chosen.board:
                continue
            warnings.append(
                f"{contract.secid}: skipped duplicate expiration_date {expiration_date}; "
                f"kept {chosen.secid} with {chosen_summary.row_count} rows "
                f"(skipped contract had {summary.row_count})"
            )

    return filtered, warnings


def build_roll_calendar_with_non_empty_segments(
    contracts: list[FuturesContract],
    candles_by_contract: dict[str, pl.DataFrame],
    roll_days_before: int,
) -> tuple[RollCalendarResult, list[FuturesContract], list[str]]:
    active_contracts = list(contracts)
    warnings: list[str] = []

    for _ in range(len(contracts)):
        roll_calendar = build_fixed_days_roll_calendar(
            contracts=active_contracts,
            candles_by_contract=candles_by_contract,
            roll_days_before=roll_days_before,
        )
        segment_contracts = active_contracts[: len(roll_calendar.events) + 1]
        empty_contracts: set[str] = set()

        for index, contract in enumerate(segment_contracts):
            start, end = _segment_bounds(index, roll_calendar.events)
            row_count = _segment_row_count(candles_by_contract[contract.secid], start, end)
            if row_count == 0:
                empty_contracts.add(contract.secid)
                warnings.append(
                    f"{contract.secid}: skipped from rollable chain because active "
                    f"segment is empty (start={start}, end={end})"
                )

        if not empty_contracts:
            return roll_calendar, segment_contracts, warnings

        active_contracts = [
            contract for contract in active_contracts if contract.secid not in empty_contracts
        ]

    roll_calendar = build_fixed_days_roll_calendar(
        contracts=active_contracts,
        candles_by_contract=candles_by_contract,
        roll_days_before=roll_days_before,
    )
    return roll_calendar, active_contracts[: len(roll_calendar.events) + 1], warnings
