from __future__ import annotations

from collections.abc import Iterable
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
    ContractCandleSummary,
    ContractChainResult,
    FuturesContract,
)
from continuous_futures_builder.roll_calendar import build_fixed_days_roll_calendar
from continuous_futures_builder.schemas import validate_continuous_schema
from continuous_futures_builder.stitcher import stitch_unadjusted
from continuous_futures_builder.storage import ContinuousFuturesStorage


def _norm(value: str) -> str:
    return value.strip().upper()


def _date_from_ts(value: object) -> object:
    if hasattr(value, "date"):
        return value.date()
    return value


class ContinuousFuturesBuilder:
    def __init__(
        self,
        data_root: str | Path,
        *,
        output_root: str | Path | None = None,
        data_access: ContinuousFuturesDataAccess | None = None,
        allow_local_fallback: bool = True,
    ) -> None:
        self.data_root = Path(data_root)
        self.output_root = Path(output_root) if output_root is not None else self.data_root
        self.data_access = data_access or create_data_access(
            self.data_root,
            allow_local_fallback=allow_local_fallback,
        )
        self.storage = ContinuousFuturesStorage(self.output_root)

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

    def _common_assetcode(self, contracts: Iterable[FuturesContract]) -> str | None:
        assetcodes = {
            contract.assetcode for contract in contracts if contract.assetcode is not None
        }
        if len(assetcodes) == 1:
            return next(iter(assetcodes))
        return None

    def _augment_chain_from_candle_catalog(
        self,
        chain: ContractChainResult,
        *,
        exchange: str,
        engine: str,
        market: str,
        timeframe: str,
        secid_prefix: str | None,
        include_secid: list[str] | None,
        exclude_secid: list[str] | None,
        asset_code: str | None,
    ) -> ContractChainResult:
        if secid_prefix is None and not include_secid:
            return chain

        existing = {_norm(contract.secid) for contract in chain.contracts}
        excluded = {_norm(secid) for secid in exclude_secid or []}
        default_assetcode = asset_code or self._common_assetcode(chain.contracts)
        summaries = self.data_access.list_contract_candle_summaries(
            exchange=exchange,
            engine=engine,
            market=market,
            timeframe=timeframe,
            secid_prefix=secid_prefix,
            include_secid=include_secid,
        )
        additions: list[FuturesContract] = []
        skipped = list(chain.skipped)
        for summary in summaries:
            key = _norm(summary.secid)
            if key in existing or key in excluded:
                continue
            expiration_date = _date_from_ts(summary.max_ts)
            if expiration_date is None:
                skipped.append(self._skip_from_candle_summary(summary, "missing candle max_ts"))
                continue
            additions.append(
                FuturesContract(
                    secid=summary.secid,
                    board=summary.board,
                    assetcode=default_assetcode,
                    expiration_date=expiration_date,
                    last_trade_date=expiration_date,
                )
            )
            existing.add(key)

        if not additions:
            return chain

        contracts = sorted(
            [*chain.contracts, *additions],
            key=lambda contract: (contract.expiration_date, contract.secid, contract.board),
        )
        warning = (
            "augmented "
            f"{len(additions)} contract(s) from existing candle partitions missing from metadata: "
            + ", ".join(contract.secid for contract in additions)
        )
        return ContractChainResult(
            contracts=contracts,
            skipped=skipped,
            warnings=[*chain.warnings, warning],
        )

    def _skip_from_candle_summary(self, summary: ContractCandleSummary, reason: str):
        from continuous_futures_builder.models import SkippedContract

        return SkippedContract(secid=summary.secid, board=summary.board, reason=reason)

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
        timeframe: str = "1d",
    ) -> ContractChainResult:
        metadata = self.data_access.load_metadata_snapshot(
            exchange=exchange,
            engine=engine,
            market=market,
            snapshot_ts=metadata_snapshot_ts,
        )
        chain = build_contract_chain(
            metadata=metadata,
            query=query,
            asset_code=asset_code,
            secid_prefix=secid_prefix,
            include_secid=include_secid,
            exclude_secid=exclude_secid,
        )
        return self._augment_chain_from_candle_catalog(
            chain,
            exchange=exchange,
            engine=engine,
            market=market,
            timeframe=timeframe,
            secid_prefix=secid_prefix,
            include_secid=include_secid,
            exclude_secid=exclude_secid,
            asset_code=asset_code,
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
            timeframe=config.timeframe,
        )
        candles_by_contract = self._load_candles(config, chain.contracts)
        roll_calendar = build_fixed_days_roll_calendar(
            contracts=chain.contracts,
            candles_by_contract=candles_by_contract,
            roll_days_before=config.roll_days_before,
        )
        active_contracts = chain.contracts[: len(roll_calendar.events) + 1]
        frame = stitch_unadjusted(
            config=config,
            contracts=active_contracts,
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
        output_root: str | Path | None = None,
    ) -> ContinuousFuturesBuildResult:
        effective_output_root = Path(output_root) if output_root is not None else self.output_root
        config = ContinuousFuturesBuildConfig(
            data_root=self.data_root,
            output_root=effective_output_root,
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
            storage = ContinuousFuturesStorage(effective_output_root)
            output_path = storage.output_path(
                exchange=config.exchange,
                engine=config.engine,
                market=config.market,
                continuous_symbol=config.continuous_symbol,
                timeframe=config.timeframe,
                adjustment_mode=config.adjustment_mode,
            )
            storage.write(frame, output_path, overwrite=overwrite)

        return ContinuousFuturesBuildResult(
            output_path=output_path,
            row_count=frame.height,
            start_ts=frame.get_column("ts").min(),
            end_ts=frame.get_column("ts").max(),
            contracts_used=frame.get_column("active_contract")
            .unique(maintain_order=True)
            .to_list(),
            roll_count=int(frame.get_column("roll_flag").sum()),
            warnings=warnings,
        )
