from __future__ import annotations

from pathlib import Path
from typing import Protocol

import polars as pl

from continuous_futures_builder.exceptions import InputDataError
from continuous_futures_builder.models import ContractCandleSummary


class ContinuousFuturesDataAccess(Protocol):
    def load_metadata_snapshot(
        self,
        exchange: str,
        engine: str,
        market: str,
        snapshot_ts: str,
    ) -> pl.DataFrame: ...

    def load_contract_candles(
        self,
        exchange: str,
        engine: str,
        market: str,
        board: str,
        symbol: str,
        timeframe: str,
    ) -> pl.DataFrame: ...

    def list_metadata_snapshots(
        self,
        exchange: str,
        engine: str,
        market: str,
    ) -> list[str]: ...

    def list_contract_candle_summaries(
        self,
        exchange: str,
        engine: str,
        market: str,
        timeframe: str,
        secid_prefix: str | list[str] | None = None,
        secid_prefixes: list[str] | None = None,
        include_secid: list[str] | None = None,
    ) -> list[ContractCandleSummary]: ...


def _read_parquet_parts(directory: Path, context: str) -> pl.DataFrame:
    return _scan_parquet_parts(directory, context).collect()


def _scan_parquet_parts(directory: Path, context: str) -> pl.LazyFrame:
    files = sorted(directory.glob("*.parquet"))
    if not files:
        raise InputDataError(f"No Parquet files found for {context}: {directory}")
    frames = [pl.scan_parquet(file) for file in files]
    if len(frames) == 1:
        return frames[0]
    return pl.concat(frames, how="diagonal_relaxed")
