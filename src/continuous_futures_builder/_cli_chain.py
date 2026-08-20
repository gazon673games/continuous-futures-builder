from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from continuous_futures_builder._cli_core import (
    app,
)
from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.exceptions import ContinuousFuturesError
from continuous_futures_builder.models import ContractChainResult
from continuous_futures_builder.prefixes import infer_contract_prefix, normalize_secid_prefixes


def _print_chain_diagnostics(
    builder: ContinuousFuturesBuilder,
    result: ContractChainResult,
    *,
    exchange: str,
    engine: str,
    market: str,
    timeframe: str,
    secid_prefixes: list[str],
) -> None:
    typer.echo("contracts:")
    for contract in result.contracts:
        candles = builder.data_access.load_contract_candles(
            exchange=exchange,
            engine=engine,
            market=market,
            board=contract.board,
            symbol=contract.secid,
            timeframe=timeframe,
        )
        typer.echo(f"- secid: {contract.secid}")
        typer.echo(f"  board: {contract.board}")
        typer.echo(f"  prefix: {infer_contract_prefix(contract.secid, secid_prefixes)}")
        typer.echo(f"  assetcode: {contract.assetcode}")
        typer.echo(f"  expiration_date: {contract.expiration_date}")
        typer.echo(f"  last_trade_date: {contract.last_trade_date}")
        typer.echo(f"  row_count: {candles.height}")
        typer.echo(f"  min_ts: {candles.get_column('ts').min()}")
        typer.echo(f"  max_ts: {candles.get_column('ts').max()}")

    typer.echo("skipped_contracts:")
    if result.skipped:
        for skipped in result.skipped:
            label = skipped.secid or "<unknown>"
            if skipped.board:
                label = f"{label}@{skipped.board}"
            typer.echo(f"- {label}: {skipped.reason}")
    else:
        typer.echo("- none")

    family_changes = []
    for current, next_contract in zip(result.contracts, result.contracts[1:], strict=False):
        current_prefix = infer_contract_prefix(current.secid, secid_prefixes)
        next_prefix = infer_contract_prefix(next_contract.secid, secid_prefixes)
        if current_prefix != next_prefix:
            family_changes.append(
                f"{current.secid} -> {next_contract.secid} ({current_prefix} -> {next_prefix})"
            )
    typer.echo("family_transition_candidates:")
    if family_changes:
        for change in family_changes:
            typer.echo(f"- {change}")
    else:
        typer.echo("- none")


@app.command()
def snapshots(
    data_root: Annotated[Path, typer.Option()],
    exchange: Annotated[str, typer.Option()] = "MOEX",
    engine: Annotated[str, typer.Option()] = "futures",
    market: Annotated[str, typer.Option()] = "forts",
) -> None:
    try:
        builder = ContinuousFuturesBuilder(data_root)
        snapshot_ts_values = builder.list_metadata_snapshots(
            exchange=exchange,
            engine=engine,
            market=market,
        )
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if not snapshot_ts_values:
        typer.echo("snapshots:")
        return
    typer.echo("snapshots:")
    for snapshot_ts in snapshot_ts_values:
        typer.echo(snapshot_ts)


@app.command()
def chain(
    data_root: Annotated[Path, typer.Option()],
    metadata_snapshot_ts: Annotated[str, typer.Option()],
    exchange: Annotated[str, typer.Option()] = "MOEX",
    engine: Annotated[str, typer.Option()] = "futures",
    market: Annotated[str, typer.Option()] = "forts",
    query: Annotated[str | None, typer.Option()] = "CNY",
    asset_code: Annotated[str | None, typer.Option()] = None,
    secid_prefix: Annotated[list[str] | None, typer.Option("--secid-prefix")] = None,
    secid_prefixes: Annotated[str | None, typer.Option("--secid-prefixes")] = None,
    include_secid: Annotated[list[str] | None, typer.Option()] = None,
    exclude_secid: Annotated[list[str] | None, typer.Option()] = None,
    timeframe: Annotated[str, typer.Option()] = "1d",
) -> None:
    try:
        builder = ContinuousFuturesBuilder(data_root)
        prefixes = normalize_secid_prefixes(
            secid_prefix=secid_prefix,
            secid_prefixes=secid_prefixes,
        )
        result = builder.build_chain(
            exchange=exchange,
            engine=engine,
            market=market,
            metadata_snapshot_ts=metadata_snapshot_ts,
            query=query,
            asset_code=asset_code,
            secid_prefixes=prefixes,
            include_secid=include_secid,
            exclude_secid=exclude_secid,
            timeframe=timeframe,
        )
        typer.echo(f"contracts_used: {', '.join(result.contract_ids)}")
        _print_chain_diagnostics(
            builder,
            result,
            exchange=exchange,
            engine=engine,
            market=market,
            timeframe=timeframe,
            secid_prefixes=prefixes,
        )
        if result.warnings:
            typer.echo(f"warnings: {'; '.join(result.warnings)}")
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
