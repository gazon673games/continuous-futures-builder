from __future__ import annotations

from pathlib import Path

from continuous_futures_builder._builder_chain import _BuilderChainMixin
from continuous_futures_builder._builder_helpers import _norm
from continuous_futures_builder._builder_series import _BuilderSeriesMixin
from continuous_futures_builder.data_access import (
    ContinuousFuturesDataAccess,
    create_data_access,
)
from continuous_futures_builder.diagnostics import (
    max_abs_transition_gap,
)
from continuous_futures_builder.models import (
    ContinuousFuturesBuildConfig,
    ContinuousFuturesBuildResult,
    PrunedContract,
)
from continuous_futures_builder.prefixes import infer_contract_prefix, normalize_secid_prefixes
from continuous_futures_builder.storage import ContinuousFuturesStorage


class ContinuousFuturesBuilder(_BuilderSeriesMixin, _BuilderChainMixin):
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
        secid_prefix: str | list[str] | None = None,
        secid_prefixes: list[str] | None = None,
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
        prefixes = normalize_secid_prefixes(
            secid_prefix=secid_prefix,
            secid_prefixes=secid_prefixes,
        )
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
            secid_prefixes=prefixes,
            include_secid=include_secid or [],
            exclude_secid=exclude_secid or [],
            roll_rule=roll_rule,
            roll_days_before=roll_days_before,
            adjustment_mode=adjustment_mode,
        )
        frame, chain, warnings, family_transitions, pruned_contracts = self._build_frame(config)

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

        contracts_used = frame.get_column("active_contract").unique(maintain_order=True).to_list()
        used_contracts = {_norm(contract) for contract in contracts_used}
        pruned_keys = {_norm(contract.secid) for contract in pruned_contracts if contract.secid}
        inactive_contracts = [
            PrunedContract(
                secid=contract.secid,
                board=contract.board,
                category="inactive_future_contract",
                reason=(
                    f"{contract.secid}: not active in output because no roll into contract "
                    "occurred within available candle history"
                ),
            )
            for contract in chain.contracts
            if _norm(contract.secid) not in used_contracts
            and _norm(contract.secid) not in pruned_keys
        ]
        pruned_contracts.extend(inactive_contracts)
        prefixes_used = []
        for contract in contracts_used:
            prefix = infer_contract_prefix(contract, prefixes)
            if prefix not in prefixes_used:
                prefixes_used.append(prefix)

        return ContinuousFuturesBuildResult(
            output_path=output_path,
            row_count=frame.height,
            start_ts=frame.get_column("ts").min(),
            end_ts=frame.get_column("ts").max(),
            contracts_in_chain=len(chain.contracts),
            contracts_used=contracts_used,
            contracts_pruned=len(pruned_contracts),
            pruned_contracts=[contract.secid or "<unknown>" for contract in pruned_contracts],
            pruning_reasons=[contract.reason for contract in pruned_contracts],
            empty_active_segments=[
                contract.secid or "<unknown>"
                for contract in pruned_contracts
                if contract.category == "empty_active_segment"
            ],
            roll_count=int(frame.get_column("roll_flag").sum()),
            prefixes_used=prefixes_used,
            families_used=prefixes_used,
            family_transition_count=len(family_transitions),
            family_transitions=family_transitions,
            max_abs_family_transition_gap=max_abs_transition_gap(family_transitions),
            warnings=warnings,
        )
