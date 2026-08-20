from __future__ import annotations

from pathlib import Path

import polars as pl

from continuous_futures_builder._data_access_protocol import (
    _read_parquet_parts,
)
from continuous_futures_builder.models import ContractCandleSummary
from continuous_futures_builder.prefixes import matches_any_secid_prefix, normalize_secid_prefixes
from continuous_futures_builder.schemas import (
    validate_candle_schema,
    validate_metadata_schema,
)


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
        secid_prefix: str | list[str] | None = None,
        secid_prefixes: list[str] | None = None,
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
        prefixes = normalize_secid_prefixes(
            secid_prefix=secid_prefix,
            secid_prefixes=secid_prefixes,
        )
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
                forced = secid_upper in include
                if prefixes and not matches_any_secid_prefix(secid, prefixes) and not forced:
                    continue
                if include and not forced and not prefixes:
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
