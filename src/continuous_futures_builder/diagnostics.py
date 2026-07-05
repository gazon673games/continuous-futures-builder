from __future__ import annotations

from datetime import date, datetime

import polars as pl

from continuous_futures_builder.models import ContractFamilyTransition, RollEvent
from continuous_futures_builder.prefixes import infer_contract_prefix

LARGE_TRANSITION_GAP_PERCENT = 5.0


def _date_from_ts(value: object) -> date | datetime | None:
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else value


def _previous_close(
    candles: pl.DataFrame,
    roll_date: date,
) -> tuple[date | datetime | None, float | None]:
    prior = candles.filter(pl.col("ts").cast(pl.Date) < roll_date).sort("ts")
    if prior.height == 0:
        return None, None
    row = prior.select("ts", "close").tail(1).row(0, named=True)
    return _date_from_ts(row["ts"]), float(row["close"])


def _first_close_on_or_after(
    candles: pl.DataFrame,
    roll_date: date,
) -> tuple[date | datetime | None, float | None]:
    next_rows = candles.filter(pl.col("ts").cast(pl.Date) >= roll_date).sort("ts")
    if next_rows.height == 0:
        return None, None
    row = next_rows.select("ts", "close").head(1).row(0, named=True)
    return _date_from_ts(row["ts"]), float(row["close"])


def _gap_percent(old_close: float | None, roll_gap: float | None) -> float | None:
    if old_close in (None, 0.0) or roll_gap is None:
        return None
    return (roll_gap / old_close) * 100.0


def _gap_warning(gap_percent: float | None) -> str | None:
    if gap_percent is None or abs(gap_percent) < LARGE_TRANSITION_GAP_PERCENT:
        return None
    return f"large family transition gap: {gap_percent:.2f}%"


def build_family_transition_diagnostics(
    roll_events: list[RollEvent],
    candles_by_contract: dict[str, pl.DataFrame],
    *,
    known_prefixes: object = None,
) -> list[ContractFamilyTransition]:
    transitions: list[ContractFamilyTransition] = []
    for event in roll_events:
        from_prefix = infer_contract_prefix(event.from_contract, known_prefixes)
        to_prefix = infer_contract_prefix(event.to_contract, known_prefixes)
        if from_prefix == to_prefix:
            continue

        old_ts: date | datetime | None = None
        new_ts: date | datetime | None = None
        old_close: float | None = None
        new_close: float | None = None
        warning: str | None = None

        current = candles_by_contract.get(event.from_contract)
        next_contract = candles_by_contract.get(event.to_contract)
        if current is None:
            warning = f"missing candles for {event.from_contract}"
        else:
            old_ts, old_close = _previous_close(current, event.roll_date)
            if old_close is None:
                warning = f"missing previous close for {event.from_contract}"

        if next_contract is None:
            warning = f"missing candles for {event.to_contract}"
        else:
            new_ts, new_close = _first_close_on_or_after(next_contract, event.roll_date)
            if new_close is None:
                warning = f"missing close for {event.to_contract} on or after {event.roll_date}"

        roll_gap = None
        if old_close is not None and new_close is not None:
            roll_gap = new_close - old_close
        gap_percent = _gap_percent(old_close, roll_gap)
        warning = warning or _gap_warning(gap_percent)

        transitions.append(
            ContractFamilyTransition(
                from_contract=event.from_contract,
                to_contract=event.to_contract,
                from_prefix=from_prefix,
                to_prefix=to_prefix,
                transition_date=new_ts or event.roll_date,
                roll_date=event.roll_date,
                old_close=old_close,
                new_close=new_close,
                roll_gap=roll_gap,
                gap_percent=gap_percent,
                warning=warning,
            )
        )
    return transitions


def max_abs_transition_gap(transitions: list[ContractFamilyTransition]) -> float | None:
    gaps = [
        abs(transition.roll_gap) for transition in transitions if transition.roll_gap is not None
    ]
    return max(gaps) if gaps else None
