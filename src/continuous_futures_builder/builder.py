from __future__ import annotations

from pathlib import Path

import polars as pl

from continuous_futures_builder.adjustments import apply_additive_back_adjustment
from continuous_futures_builder.constants import ADJUSTMENT_BACK_ADJUSTED
from continuous_futures_builder.contract_chain import build_contract_chain
from continuous_futures_builder.data_access import (
    ContinuousFuturesDataAccess,
    create_data_access,
)
from continuous_futures_builder.models import (
    ContinuousFuturesBuildConfig,
    ContinuousFuturesBuildResult,
    ContractChainResult,
    FuturesContract,
)
from continuous_futures_builder.roll_calendar import build_fixed_days_roll_calendar
from continuous_futures_builder.schemas import validate_continuous_schema
from continuous_futures_builder.stitcher import stitch_unadjusted
from continuous_futures_builder.storage import ContinuousFuturesStorage


class ContinuousFuturesBuilder:
    def __init__(
        self,
        data_root: str | Path,
        *,
        data_access: ContinuousFuturesDataAccess | None = None,
        allow_local_fallback: bool = True,
    ) -> None:
        self.data_root = Path(data_root)
        self.data_access = data_access or create_data_access(
            self.data_root,
            allow_local_fallback=allow_local_fallback,
        )
        self.storage = ContinuousFuturesStorage(self.data_root)

    def list_metadata_snapshots(
        self,
        exchange: str = "MOEX",
        engine: str = "futures",
        market: str = "forts",
    ) -> list[str]:
        return self.data_access.list_metadata_snapshots(
            exchange=exchange,
            engine=engine,
            market=market,
        )

    def build_chain(
        self,
        exchange: str = "MOEX",
        engine: str = "futures",
        market: str = "forts",
        metadata_snapshot_ts: str = "20240101T000000Z",
        query: str | None = "CNY",
        asset_code: str | None = None,
        secid_prefix: str | None = None,
        include_secid: list[str] | None = None,
        exclude_secid: list[str] | None = None,
    ) -> ContractChainResult:
        metadata = self.data_access.load_metadata_snapshot(
            exchange=exchange,
            engine=engine,
            market=market,
            snapshot_ts=metadata_snapshot_ts,
        )
        return build_contract_chain(
            metadata=metadata,
            query=query,
            asset_code=asset_code,
            secid_prefix=secid_prefix,
            include_secid=include_secid,
            exclude_secid=exclude_secid,
        )

    def _load_candles(
        self, config: ContinuousFuturesBuildConfig, contracts: list[FuturesContract]
    ) -> dict[str, pl.DataFrame]:
        return {
            contract.secid: self.data_access.load_contract_candles(
                exchange=config.exchange,
                engine=config.engine,
                market=config.market,
                board=contract.board,
                symbol=contract.secid,
                timeframe=config.timeframe,
            )
            for contract in contracts
        }

    def _build_frame(
        self, config: ContinuousFuturesBuildConfig
    ) -> tuple[pl.DataFrame, ContractChainResult, list[str]]:
        chain = self.build_chain(
            exchange=config.exchange,
            engine=config.engine,
            market=config.market,
            metadata_snapshot_ts=config.metadata_snapshot_ts,
            query=config.query,
            asset_code=config.asset_code,
            secid_prefix=config.secid_prefix,
            include_secid=config.include_secid,
            exclude_secid=config.exclude_secid,
        )
        candles_by_contract = self._load_candles(config, chain.contracts)
        roll_calendar = build_fixed_days_roll_calendar(
            contracts=chain.contracts,
            candles_by_contract=candles_by_contract,
            roll_days_before=config.roll_days_before,
        )
        frame = stitch_unadjusted(
            config=config,
            contracts=chain.contracts,
            roll_events=roll_calendar.events,
            candles_by_contract=candles_by_contract,
        )
        if config.adjustment_mode == ADJUSTMENT_BACK_ADJUSTED:
            frame = apply_additive_back_adjustment(
                unadjusted=frame,
                roll_events=roll_calendar.events,
                candles_by_contract=candles_by_contract,
            )
        validate_continuous_schema(frame)
        warnings = [*chain.warnings, *roll_calendar.warnings]
        return frame, chain, warnings

    def build(
        self,
        exchange: str = "MOEX",
        engine: str = "futures",
        market: str = "forts",
        continuous_symbol: str = "CNYRUB_CONT",
        timeframe: str = "1d",
        metadata_snapshot_ts: str = "20240101T000000Z",
        query: str | None = "CNY",
        asset_code: str | None = None,
        secid_prefix: str | None = None,
        include_secid: list[str] | None = None,
        exclude_secid: list[str] | None = None,
        roll_rule: str = "fixed_days_before_expiration",
        roll_days_before: int = 3,
        adjustment_mode: str = "unadjusted",
        save: bool = False,
        overwrite: bool = False,
    ) -> ContinuousFuturesBuildResult:
        config = ContinuousFuturesBuildConfig(
            data_root=self.data_root,
            exchange=exchange,
            engine=engine,
            market=market,
            continuous_symbol=continuous_symbol,
            timeframe=timeframe,
            metadata_snapshot_ts=metadata_snapshot_ts,
            query=query,
            asset_code=asset_code,
            secid_prefix=secid_prefix,
            include_secid=include_secid or [],
            exclude_secid=exclude_secid or [],
            roll_rule=roll_rule,
            roll_days_before=roll_days_before,
            adjustment_mode=adjustment_mode,
        )
        frame, chain, warnings = self._build_frame(config)

        output_path: Path | None = None
        if save:
            output_path = self.storage.output_path(
                exchange=config.exchange,
                engine=config.engine,
                market=config.market,
                continuous_symbol=config.continuous_symbol,
                timeframe=config.timeframe,
                adjustment_mode=config.adjustment_mode,
            )
            self.storage.write(frame, output_path, overwrite=overwrite)

        return ContinuousFuturesBuildResult(
            output_path=output_path,
            row_count=frame.height,
            start_ts=frame.get_column("ts").min(),
            end_ts=frame.get_column("ts").max(),
            contracts_used=frame.get_column("active_contract")
            .unique(maintain_order=True)
            .to_list(),
            roll_count=max(len(chain.contracts) - 1, 0),
            warnings=warnings,
        )
