from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from continuous_futures_builder.exceptions import RollCalendarError
from continuous_futures_builder.models import FuturesContract, RollCalendarResult, RollEvent

FUTURE_EXPIRATION_BUFFER_DAYS = 14


def _trading_dates(candles: pl.DataFrame, contract: FuturesContract) -> list[date]:
    if candles.height == 0:
        raise RollCalendarError(f"No candle data for contract {contract.secid}")
    return (
        candles.select(pl.col("ts").cast(pl.Date).alias("ts"))
        .unique()
        .sort("ts")
        .get_column("ts")
        .to_list()
    )


def _choose_roll_date(
    contract: FuturesContract, dates: list[date], roll_days_before: int
) -> tuple[date, str | None]:
    prior_dates = [day for day in dates if day < contract.expiration_date]
    if not prior_dates:
        raise RollCalendarError(
            f"No trading days before expiration for {contract.secid} ({contract.expiration_date})"
        )
    if len(prior_dates) >= roll_days_before:
        return prior_dates[-roll_days_before], None

    warning = (
        f"{contract.secid}: only {len(prior_dates)} trading day(s) before expiration; "
        f"using earliest available {prior_dates[0]}"
    )
    return prior_dates[0], warning


def _available_through(dates: list[date]) -> date:
    return dates[-1]


def build_fixed_days_roll_calendar(
    contracts: list[FuturesContract],
    candles_by_contract: dict[str, pl.DataFrame],
    roll_days_before: int,
) -> RollCalendarResult:
    if len(contracts) < 2:
        raise RollCalendarError("At least two contracts are required to build a roll calendar")

    events: list[RollEvent] = []
    warnings: list[str] = []

    for current, next_contract in zip(contracts, contracts[1:], strict=False):
        current_candles = candles_by_contract.get(current.secid)
        next_candles = candles_by_contract.get(next_contract.secid)
        if current_candles is None:
            raise RollCalendarError(f"Missing candle data for current contract {current.secid}")
        if next_candles is None:
            raise RollCalendarError(f"Missing candle data for next contract {next_contract.secid}")

        current_dates = _trading_dates(current_candles, current)
        available_through = _available_through(current_dates)
        if current.expiration_date - available_through > timedelta(
            days=max(FUTURE_EXPIRATION_BUFFER_DAYS, roll_days_before * 3)
        ):
            warnings.append(
                f"{current.secid}: available candles end at {available_through} before "
                f"expiration {current.expiration_date}; not rolling to {next_contract.secid} yet"
            )
            break

        roll_date, warning = _choose_roll_date(
            current,
            current_dates,
            roll_days_before,
        )
        if warning:
            warnings.append(warning)

        next_dates = _trading_dates(next_candles, next_contract)
        if not any(day >= roll_date for day in next_dates):
            raise RollCalendarError(
                f"Next contract {next_contract.secid} has no candle data on or after "
                f"roll date {roll_date}"
            )

        events.append(
            RollEvent(
                roll_id=f"{current.secid}_TO_{next_contract.secid}_{roll_date:%Y%m%d}",
                from_contract=current.secid,
                to_contract=next_contract.secid,
                roll_date=roll_date,
                from_expiration_date=current.expiration_date,
                to_expiration_date=next_contract.expiration_date,
            )
        )

    return RollCalendarResult(events=events, warnings=warnings)
