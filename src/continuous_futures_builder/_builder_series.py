from __future__ import annotations

import polars as pl

from continuous_futures_builder.adjustments import apply_additive_back_adjustment
from continuous_futures_builder.constants import ADJUSTMENT_BACK_ADJUSTED
from continuous_futures_builder.diagnostics import (
    build_family_transition_diagnostics,
)
from continuous_futures_builder.models import (
    ContinuousFuturesBuildConfig,
    ContractChainResult,
    ContractFamilyTransition,
    FuturesContract,
    PrunedContract,
)
from continuous_futures_builder.prefixes import normalize_secid_prefixes
from continuous_futures_builder.rollability import (
    build_roll_calendar_with_non_empty_segments,
    filter_rollable_contracts,
)
from continuous_futures_builder.schemas import validate_continuous_schema
from continuous_futures_builder.stitcher import stitch_unadjusted


class _BuilderSeriesMixin:
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
    ) -> tuple[
        pl.DataFrame,
        ContractChainResult,
        list[str],
        list[ContractFamilyTransition],
        list[PrunedContract],
    ]:
        prefixes = normalize_secid_prefixes(
            secid_prefix=config.secid_prefix,
            secid_prefixes=config.secid_prefixes,
        )
        chain = self.build_chain(
            exchange=config.exchange,
            engine=config.engine,
            market=config.market,
            metadata_snapshot_ts=config.metadata_snapshot_ts,
            query=config.query,
            asset_code=config.asset_code,
            secid_prefixes=prefixes,
            include_secid=config.include_secid,
            exclude_secid=config.exclude_secid,
            timeframe=config.timeframe,
        )
        candles_by_contract = self._load_candles(config, chain.contracts)
        rollable_contracts, rollability_warnings, rollability_pruned = filter_rollable_contracts(
            chain.contracts,
            candles_by_contract,
        )
        roll_calendar, active_contracts, segment_warnings, segment_pruned = (
            build_roll_calendar_with_non_empty_segments(
                contracts=rollable_contracts,
                candles_by_contract=candles_by_contract,
                roll_days_before=config.roll_days_before,
            )
        )
        frame = stitch_unadjusted(
            config=config,
            contracts=active_contracts,
            roll_events=roll_calendar.events,
            candles_by_contract=candles_by_contract,
        )
        family_transitions = build_family_transition_diagnostics(
            roll_calendar.events,
            candles_by_contract,
            known_prefixes=prefixes,
        )
        if config.adjustment_mode == ADJUSTMENT_BACK_ADJUSTED:
            frame = apply_additive_back_adjustment(
                unadjusted=frame,
                roll_events=roll_calendar.events,
                candles_by_contract=candles_by_contract,
            )
        validate_continuous_schema(frame)
        warnings = [
            *chain.warnings,
            *rollability_warnings,
            *segment_warnings,
            *roll_calendar.warnings,
        ]
        return frame, chain, warnings, family_transitions, [*rollability_pruned, *segment_pruned]
