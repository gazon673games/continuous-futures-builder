from __future__ import annotations

from pathlib import Path
from typing import Annotated

import polars as pl
import typer

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.exceptions import ContinuousFuturesError
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
    secid_prefix: Annotated[str | None, typer.Option()] = None,
    include_secid: Annotated[list[str] | None, typer.Option()] = None,
    exclude_secid: Annotated[list[str] | None, typer.Option()] = None,
) -> None:
    try:
        builder = ContinuousFuturesBuilder(data_root)
        result = builder.build_chain(
            exchange=exchange,
            engine=engine,
            market=market,
            metadata_snapshot_ts=metadata_snapshot_ts,
            query=query,
            asset_code=asset_code,
            secid_prefix=secid_prefix,
            include_secid=include_secid,
            exclude_secid=exclude_secid,
        )
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"contracts_used: {', '.join(result.contract_ids)}")
    if result.warnings:
        typer.echo(f"warnings: {'; '.join(result.warnings)}")


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
    secid_prefix: Annotated[str | None, typer.Option()] = None,
    include_secid: Annotated[list[str] | None, typer.Option()] = None,
    exclude_secid: Annotated[list[str] | None, typer.Option()] = None,
    roll_rule: Annotated[str, typer.Option()] = "fixed_days_before_expiration",
    roll_days_before: Annotated[int, typer.Option()] = 3,
    adjustment_mode: Annotated[str, typer.Option()] = "unadjusted",
    save: Annotated[bool, typer.Option()] = False,
    overwrite: Annotated[bool, typer.Option()] = False,
    head: Annotated[int, typer.Option()] = 0,
) -> None:
    try:
        builder = ContinuousFuturesBuilder(data_root)
        result = builder.build(
            exchange=exchange,
            engine=engine,
            market=market,
            continuous_symbol=continuous_symbol,
            timeframe=timeframe,
            metadata_snapshot_ts=metadata_snapshot_ts,
            query=query,
            asset_code=asset_code,
            secid_prefix=secid_prefix,
            include_secid=include_secid,
            exclude_secid=exclude_secid,
            roll_rule=roll_rule,
            roll_days_before=roll_days_before,
            adjustment_mode=adjustment_mode,
            save=save,
            overwrite=overwrite,
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


if __name__ == "__main__":
    app()
