from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder._data_access_local import (
    LocalParquetDataAccess,
)
from continuous_futures_builder._data_access_protocol import (
    ContinuousFuturesDataAccess,
)
from continuous_futures_builder.exceptions import InputDataError
from continuous_futures_builder.models import ContractCandleSummary
from continuous_futures_builder.schemas import (
    validate_candle_schema,
    validate_metadata_schema,
)


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
        secid_prefix: str | list[str] | None = None,
        secid_prefixes: list[str] | None = None,
        include_secid: list[str] | None = None,
    ) -> list[ContractCandleSummary]:
        return LocalParquetDataAccess(self.data_root).list_contract_candle_summaries(
            exchange=exchange,
            engine=engine,
            market=market,
            timeframe=timeframe,
            secid_prefix=secid_prefix,
            secid_prefixes=secid_prefixes,
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
