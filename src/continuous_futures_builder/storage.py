from __future__ import annotations

from pathlib import Path

import polars as pl

from continuous_futures_builder.constants import ADJUSTMENT_BACK_ADJUSTED, ADJUSTMENT_UNADJUSTED
from continuous_futures_builder.exceptions import InputDataError
from continuous_futures_builder.schemas import (
    validate_candle_schema,
    validate_continuous_schema,
    validate_metadata_schema,
)


def _read_parquet_parts(directory: Path, context: str) -> pl.DataFrame:
    files = sorted(directory.glob("*.parquet"))
    if not files:
        raise InputDataError(f"No Parquet files found for {context}: {directory}")
    frames = [pl.read_parquet(file) for file in files]
    if len(frames) == 1:
        return frames[0]
    return pl.concat(frames, how="diagonal_relaxed")


class LocalDataStore:
    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root)

    def metadata_snapshot_path(
        self, exchange: str, engine: str, market: str, metadata_snapshot_ts: str
    ) -> Path:
        return (
            self.data_root
            / "normalized"
            / "metadata"
            / f"exchange={exchange}"
            / f"engine={engine}"
            / f"market={market}"
            / "securities"
            / f"snapshot_ts={metadata_snapshot_ts}"
        )

    def candles_path(
        self,
        exchange: str,
        engine: str,
        market: str,
        board: str,
        symbol: str,
        timeframe: str,
    ) -> Path:
        return (
            self.data_root
            / "normalized"
            / "candles"
            / f"exchange={exchange}"
            / f"engine={engine}"
            / f"market={market}"
            / f"board={board}"
            / f"symbol={symbol}"
            / f"timeframe={timeframe}"
        )

    def read_metadata_snapshot(
        self, exchange: str, engine: str, market: str, metadata_snapshot_ts: str
    ) -> pl.DataFrame:
        path = self.metadata_snapshot_path(exchange, engine, market, metadata_snapshot_ts)
        frame = _read_parquet_parts(path, f"metadata snapshot {metadata_snapshot_ts}")
        validate_metadata_schema(frame, str(path))
        return frame

    def read_contract_candles(
        self,
        exchange: str,
        engine: str,
        market: str,
        board: str,
        symbol: str,
        timeframe: str,
    ) -> pl.DataFrame:
        path = self.candles_path(exchange, engine, market, board, symbol, timeframe)
        frame = _read_parquet_parts(path, f"candles {symbol}")
        validate_candle_schema(frame, str(path))
        return frame.sort("ts")


class ContinuousFuturesStorage:
    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root)

    def output_directory(
        self,
        exchange: str,
        engine: str,
        market: str,
        continuous_symbol: str,
        timeframe: str,
        adjustment_mode: str,
    ) -> Path:
        return (
            self.data_root
            / "derived"
            / "continuous_futures"
            / f"exchange={exchange}"
            / f"engine={engine}"
            / f"market={market}"
            / f"continuous_symbol={continuous_symbol}"
            / f"timeframe={timeframe}"
            / f"adjustment_mode={adjustment_mode}"
        )

    def output_path(
        self,
        exchange: str,
        engine: str,
        market: str,
        continuous_symbol: str,
        timeframe: str,
        adjustment_mode: str,
    ) -> Path:
        return (
            self.output_directory(
                exchange, engine, market, continuous_symbol, timeframe, adjustment_mode
            )
            / "part-000.parquet"
        )

    def write(self, frame: pl.DataFrame, path: Path, overwrite: bool = False) -> Path:
        validate_continuous_schema(frame)
        normalized_candles = (self.data_root / "normalized" / "candles").resolve()
        resolved_path = path.resolve()
        if normalized_candles in resolved_path.parents:
            raise InputDataError(f"Refusing to write derived output into adapter path: {path}")
        if path.exists() and not overwrite:
            raise InputDataError(f"Output already exists; pass overwrite=True to replace: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(path)
        return path

    def validate_path(self, path: str | Path) -> pl.DataFrame:
        target = Path(path)
        if target.is_dir():
            target = target / "part-000.parquet"
        if not target.exists():
            raise InputDataError(f"Continuous futures file does not exist: {target}")
        frame = pl.read_parquet(target)
        validate_continuous_schema(frame)
        return frame


def output_path_for_mode(
    data_root: str | Path,
    exchange: str,
    engine: str,
    market: str,
    continuous_symbol: str,
    timeframe: str,
    adjustment_mode: str,
) -> Path:
    if adjustment_mode not in {ADJUSTMENT_UNADJUSTED, ADJUSTMENT_BACK_ADJUSTED}:
        raise InputDataError(f"Unsupported adjustment_mode for output path: {adjustment_mode}")
    return ContinuousFuturesStorage(data_root).output_path(
        exchange, engine, market, continuous_symbol, timeframe, adjustment_mode
    )
