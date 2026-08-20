from __future__ import annotations

from collections.abc import Iterable

from continuous_futures_builder._builder_helpers import _date_from_ts, _norm
from continuous_futures_builder.contract_chain import build_contract_chain
from continuous_futures_builder.models import (
    ContractCandleSummary,
    ContractChainResult,
    FuturesContract,
)
from continuous_futures_builder.prefixes import normalize_secid_prefixes


class _BuilderChainMixin:
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
        secid_prefixes: list[str],
        include_secid: list[str] | None,
        exclude_secid: list[str] | None,
        asset_code: str | None,
    ) -> ContractChainResult:
        if not secid_prefixes and not include_secid:
            return chain

        existing = {_norm(contract.secid) for contract in chain.contracts}
        excluded = {_norm(secid) for secid in exclude_secid or []}
        default_assetcode = asset_code or self._common_assetcode(chain.contracts)
        summaries = self.data_access.list_contract_candle_summaries(
            exchange=exchange,
            engine=engine,
            market=market,
            timeframe=timeframe,
            secid_prefixes=secid_prefixes,
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
        secid_prefix: str | list[str] | None = None,
        secid_prefixes: list[str] | None = None,
        include_secid: list[str] | None = None,
        exclude_secid: list[str] | None = None,
        timeframe: str = "1d",
    ) -> ContractChainResult:
        prefixes = normalize_secid_prefixes(
            secid_prefix=secid_prefix,
            secid_prefixes=secid_prefixes,
        )
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
            secid_prefixes=prefixes,
            include_secid=include_secid,
            exclude_secid=exclude_secid,
        )
        return self._augment_chain_from_candle_catalog(
            chain,
            exchange=exchange,
            engine=engine,
            market=market,
            timeframe=timeframe,
            secid_prefixes=prefixes,
            include_secid=include_secid,
            exclude_secid=exclude_secid,
            asset_code=asset_code,
        )
