from __future__ import annotations

from pathlib import Path
from typing import Annotated

import polars as pl
import typer

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.exceptions import ContinuousFuturesError
from continuous_futures_builder.models import ContractChainResult
from continuous_futures_builder.prefixes import infer_contract_prefix, normalize_secid_prefixes
from continuous_futures_builder.quality import build_quality_report
from continuous_futures_builder.roll_report import (
    build_roll_report,
    filter_roll_report,
    save_roll_diagnostics,
)
from continuous_futures_builder.storage import ContinuousFuturesStorage

app = typer.Typer(help="Build local derived continuous futures datasets.")


def _print_result(result: object) -> None:
    data = result.model_dump() if hasattr(result, "model_dump") else result
    for key, value in data.items():
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value) if value else ""
        else:
            rendered = value
        typer.echo(f"{key}: {rendered}")


def _print_roll_rows(rows: list[object]) -> None:
    typer.echo(
        "roll_date | from_contract | to_contract | roll_gap | roll_gap_pct | "
        "is_family_transition | warning"
    )
    if not rows:
        typer.echo("none")
        return
    for row in rows:
        typer.echo(
            " | ".join(
                (
                    str(row.roll_date or ""),
                    row.from_contract,
                    row.to_contract,
                    "" if row.roll_gap is None else f"{row.roll_gap:.6g}",
                    "" if row.roll_gap_pct is None else f"{row.roll_gap_pct:.4f}",
                    str(row.is_family_transition),
                    row.warning or "",
                )
            )
        )


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


@app.command()
def build(
    data_root: Annotated[Path, typer.Option()],
    metadata_snapshot_ts: Annotated[str, typer.Option()],
    exchange: Annotated[str, typer.Option()] = "MOEX",
    engine: Annotated[str, typer.Option()] = "futures",
    market: Annotated[str, typer.Option()] = "forts",
    continuous_symbol: Annotated[str, typer.Option()] = "CNYRUB_CONT",
    timeframe: Annotated[str, typer.Option()] = "1d",
    query: Annotated[str | None, typer.Option()] = "CNY",
    asset_code: Annotated[str | None, typer.Option()] = None,
    secid_prefix: Annotated[list[str] | None, typer.Option("--secid-prefix")] = None,
    secid_prefixes: Annotated[str | None, typer.Option("--secid-prefixes")] = None,
    include_secid: Annotated[list[str] | None, typer.Option()] = None,
    exclude_secid: Annotated[list[str] | None, typer.Option()] = None,
    roll_rule: Annotated[str, typer.Option()] = "fixed_days_before_expiration",
    roll_days_before: Annotated[int, typer.Option()] = 3,
    adjustment_mode: Annotated[str, typer.Option()] = "unadjusted",
    save: Annotated[bool, typer.Option()] = False,
    overwrite: Annotated[bool, typer.Option()] = False,
    output_root: Annotated[Path | None, typer.Option()] = None,
    head: Annotated[int, typer.Option()] = 0,
) -> None:
    try:
        builder = ContinuousFuturesBuilder(data_root)
        prefixes = normalize_secid_prefixes(
            secid_prefix=secid_prefix,
            secid_prefixes=secid_prefixes,
        )
        result = builder.build(
            exchange=exchange,
            engine=engine,
            market=market,
            continuous_symbol=continuous_symbol,
            timeframe=timeframe,
            metadata_snapshot_ts=metadata_snapshot_ts,
            query=query,
            asset_code=asset_code,
            secid_prefixes=prefixes,
            include_secid=include_secid,
            exclude_secid=exclude_secid,
            roll_rule=roll_rule,
            roll_days_before=roll_days_before,
            adjustment_mode=adjustment_mode,
            save=save,
            overwrite=overwrite,
            output_root=output_root,
        )
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    _print_result(result)
    if head > 0 and result.output_path is not None:
        typer.echo(pl.read_parquet(result.output_path).head(head))


@app.command()
def validate(
    path: Annotated[Path, typer.Option()],
    head: Annotated[int, typer.Option()] = 0,
) -> None:
    try:
        frame = ContinuousFuturesStorage(path.parent).validate_path(path)
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"valid: {path}")
    typer.echo(f"row_count: {frame.height}")
    typer.echo(f"start_ts: {frame.get_column('ts').min()}")
    typer.echo(f"end_ts: {frame.get_column('ts').max()}")
    if head > 0:
        typer.echo(frame.head(head))


@app.command()
def quality(path: Annotated[Path, typer.Option()]) -> None:
    try:
        report = build_quality_report(path)
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    _print_result(report)


@app.command("roll-report")
def roll_report(
    path: Annotated[Path, typer.Option()],
    top: Annotated[int | None, typer.Option(min=1)] = None,
    family_transitions_only: Annotated[bool, typer.Option()] = False,
    save: Annotated[bool, typer.Option()] = False,
    output_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        rows = build_roll_report(path)
        selected = filter_roll_report(
            rows,
            top=top,
            family_transitions_only=family_transitions_only,
        )
        _print_roll_rows(selected)
        if save:
            if output_dir is None:
                msg = "--output-dir is required when --save is passed"
                raise ContinuousFuturesError(msg)
            save_result = save_roll_diagnostics(path, output_dir)
            _print_result(save_result)
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()
