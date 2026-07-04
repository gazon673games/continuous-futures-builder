from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import polars as pl

from continuous_futures_builder.exceptions import InputDataError
from continuous_futures_builder.models import ContractCandleSummary
from continuous_futures_builder.schemas import (
    validate_candle_schema,
    validate_metadata_schema,
)


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
        secid_prefix: str | None = None,
        include_secid: list[str] | None = None,
    ) -> list[ContractCandleSummary]: ...


def _read_parquet_parts(directory: Path, context: str) -> pl.DataFrame:
    files = sorted(directory.glob("*.parquet"))
    if not files:
        raise InputDataError(f"No Parquet files found for {context}: {directory}")
    frames = [pl.read_parquet(file) for file in files]
    if len(frames) == 1:
        return frames[0]
    return pl.concat(frames, how="diagonal_relaxed")


class LocalParquetDataAccess:
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

    def load_metadata_snapshot(
        self,
        exchange: str,
        engine: str,
        market: str,
        snapshot_ts: str,
    ) -> pl.DataFrame:
        path = self.metadata_snapshot_path(exchange, engine, market, snapshot_ts)
        frame = _read_parquet_parts(path, f"metadata snapshot {snapshot_ts}")
        validate_metadata_schema(frame, str(path))
        return frame

    def load_contract_candles(
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

    def list_metadata_snapshots(
        self,
        exchange: str,
        engine: str,
        market: str,
    ) -> list[str]:
        base = (
            self.data_root
            / "normalized"
            / "metadata"
            / f"exchange={exchange}"
            / f"engine={engine}"
            / f"market={market}"
            / "securities"
        )
        if not base.exists():
            return []
        return sorted(
            path.name.removeprefix("snapshot_ts=")
            for path in base.iterdir()
            if path.is_dir() and path.name.startswith("snapshot_ts=")
        )

    def list_contract_candle_summaries(
        self,
        exchange: str,
        engine: str,
        market: str,
        timeframe: str,
        secid_prefix: str | None = None,
        include_secid: list[str] | None = None,
    ) -> list[ContractCandleSummary]:
        base = (
            self.data_root
            / "normalized"
            / "candles"
            / f"exchange={exchange}"
            / f"engine={engine}"
            / f"market={market}"
        )
        if not base.exists():
            return []

        include = {secid.upper() for secid in include_secid or []}
        prefix = secid_prefix.upper() if secid_prefix else None
        summaries: list[ContractCandleSummary] = []
        for board_path in sorted(base.glob("board=*")):
            if not board_path.is_dir():
                continue
            board = board_path.name.removeprefix("board=")
            for symbol_path in sorted(board_path.glob("symbol=*")):
                if not symbol_path.is_dir():
                    continue
                secid = symbol_path.name.removeprefix("symbol=")
                secid_upper = secid.upper()
                if prefix and not secid_upper.startswith(prefix):
                    continue
                if include and secid_upper not in include and prefix is None:
                    continue
                timeframe_path = symbol_path / f"timeframe={timeframe}"
                if not timeframe_path.exists():
                    continue
                frame = _read_parquet_parts(timeframe_path, f"candles {secid}")
                validate_candle_schema(frame, str(timeframe_path))
                summaries.append(
                    ContractCandleSummary(
                        secid=secid,
                        board=board,
                        row_count=frame.height,
                        min_ts=frame.get_column("ts").min(),
                        max_ts=frame.get_column("ts").max(),
                    )
                )
        return sorted(summaries, key=lambda item: (item.secid, item.board))


class OhlcvDataStoreAccess:
    def __init__(
        self,
        data_root: str | Path,
        data_store: Any | None = None,
        data_store_cls: type[Any] | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        if data_store is not None:
            self.store = data_store
            return
        if data_store_cls is None:
            data_store_cls = _load_data_store_class()
        if data_store_cls is None:
            raise _data_store_unavailable_error()
        self.store = data_store_cls(data_root=self.data_root)

    def load_metadata_snapshot(
        self,
        exchange: str,
        engine: str,
        market: str,
        snapshot_ts: str,
    ) -> pl.DataFrame:
        try:
            frame = self.store.get_metadata_snapshot(
                exchange=exchange,
                engine=engine,
                market=market,
                snapshot_ts=snapshot_ts,
            )
        except Exception as exc:
            raise InputDataError(
                f"Failed to load metadata snapshot through ohlcv_data_store: {exc}"
            ) from exc
        validate_metadata_schema(frame, "ohlcv_data_store metadata snapshot")
        return frame

    def load_contract_candles(
        self,
        exchange: str,
        engine: str,
        market: str,
        board: str,
        symbol: str,
        timeframe: str,
    ) -> pl.DataFrame:
        try:
            frame = self.store.get_candles(
                exchange=exchange,
                engine=engine,
                market=market,
                board=board,
                symbol=symbol,
                timeframe=timeframe,
            )
        except Exception as exc:
            raise InputDataError(f"Failed to load candles through ohlcv_data_store: {exc}") from exc
        validate_candle_schema(frame, "ohlcv_data_store candle data")
        return frame.sort("ts")

    def list_metadata_snapshots(
        self,
        exchange: str,
        engine: str,
        market: str,
    ) -> list[str]:
        try:
            snapshots = self.store.list_metadata_snapshots(include_stats=False)
        except Exception as exc:
            raise InputDataError(
                f"Failed to list metadata snapshots through ohlcv_data_store: {exc}"
            ) from exc
        return sorted(
            snapshot.snapshot_ts
            for snapshot in snapshots
            if snapshot.exchange == exchange
            and snapshot.engine == engine
            and snapshot.market == market
            and getattr(snapshot, "kind", "securities") == "securities"
        )

    def list_contract_candle_summaries(
        self,
        exchange: str,
        engine: str,
        market: str,
        timeframe: str,
        secid_prefix: str | None = None,
        include_secid: list[str] | None = None,
    ) -> list[ContractCandleSummary]:
        return LocalParquetDataAccess(self.data_root).list_contract_candle_summaries(
            exchange=exchange,
            engine=engine,
            market=market,
            timeframe=timeframe,
            secid_prefix=secid_prefix,
            include_secid=include_secid,
        )


def create_data_access(
    data_root: str | Path,
    *,
    prefer_data_store: bool = True,
    allow_local_fallback: bool = True,
    data_store: Any | None = None,
    data_store_cls: type[Any] | None = None,
) -> ContinuousFuturesDataAccess:
    if data_store is not None or data_store_cls is not None:
        return OhlcvDataStoreAccess(data_root, data_store=data_store, data_store_cls=data_store_cls)

    if prefer_data_store and _load_data_store_class() is not None:
        return OhlcvDataStoreAccess(data_root)

    if allow_local_fallback:
        return LocalParquetDataAccess(data_root)

    raise _data_store_unavailable_error()


def _load_data_store_class() -> type[Any] | None:
    try:
        from ohlcv_data_store import DataStore
    except ImportError:
        return None
    return DataStore


def _data_store_unavailable_error() -> InputDataError:
    return InputDataError(
        "ohlcv_data_store is not installed and local fallback is disabled. "
        "Install the sibling data store with `uv pip install -e ../data_store` "
        "or construct ContinuousFuturesBuilder(..., allow_local_fallback=True)."
    )
