from __future__ import annotations

import polars as pl

from continuous_futures_builder.contract_filters import filter_contracts
from continuous_futures_builder.exceptions import ContractChainError
from continuous_futures_builder.models import ContractChainResult, FuturesContract, SkippedContract


def _format_skip(skip: SkippedContract) -> str:
    label = "/".join(part for part in (skip.secid, skip.board) if part)
    if not label:
        label = "<unknown>"
    return f"skipped {label}: {skip.reason}"


def build_contract_chain(
    metadata: pl.DataFrame,
    query: str | None = None,
    asset_code: str | None = None,
    secid_prefix: str | list[str] | None = None,
    secid_prefixes: list[str] | None = None,
    include_secid: list[str] | None = None,
    exclude_secid: list[str] | None = None,
) -> ContractChainResult:
    contracts, skipped = filter_contracts(
        metadata=metadata,
        query=query,
        asset_code=asset_code,
        secid_prefix=secid_prefix,
        secid_prefixes=secid_prefixes,
        include_secid=include_secid,
        exclude_secid=exclude_secid,
    )
    sorted_contracts = sorted(
        contracts,
        key=lambda contract: (contract.expiration_date, contract.secid, contract.board),
    )
    if len(sorted_contracts) < 2:
        warnings = [_format_skip(skip) for skip in skipped]
        raise ContractChainError(
            "Contract chain must contain at least two eligible contracts. "
            f"Found {len(sorted_contracts)}. " + ("; ".join(warnings) if warnings else "")
        )

    warnings = [_format_skip(skip) for skip in skipped]
    return ContractChainResult(contracts=sorted_contracts, skipped=skipped, warnings=warnings)


def contract_by_secid(contracts: list[FuturesContract]) -> dict[str, FuturesContract]:
    return {contract.secid: contract for contract in contracts}
