from __future__ import annotations

from pathlib import Path
from typing import Annotated

import polars as pl
import typer

from continuous_futures_builder._cli_core import (
    _print_result,
    app,
    nearest_app,
)
from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.exceptions import ContinuousFuturesError
from continuous_futures_builder.nearest_contract import (
    NearestContractBuildConfig,
    build_nearest_contract_dataset,
)
from continuous_futures_builder.nearest_gap_diagnostics import (
    diagnose_dataset_session_gaps,
    parse_session_options,
)
from continuous_futures_builder.prefixes import normalize_secid_prefixes


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


@nearest_app.command("build")
def nearest_build(
    data_root: Annotated[Path, typer.Option()],
    output_root: Annotated[Path, typer.Option()] = Path("data"),
    exchange: Annotated[str, typer.Option()] = "MOEX",
    engine: Annotated[str, typer.Option()] = "futures",
    market: Annotated[str, typer.Option()] = "forts",
    board: Annotated[str, typer.Option()] = "RFUD",
    secid_prefix: Annotated[list[str] | None, typer.Option("--secid-prefix")] = None,
    secid_prefixes: Annotated[str | None, typer.Option("--secid-prefixes")] = None,
    include_secid: Annotated[list[str] | None, typer.Option()] = None,
    exclude_secid: Annotated[list[str] | None, typer.Option()] = None,
    asset_code: Annotated[str | None, typer.Option()] = "CNY",
    query: Annotated[str | None, typer.Option()] = None,
    timeframe: Annotated[str, typer.Option()] = "5m",
    continuous_symbol: Annotated[str, typer.Option()] = "CNYRUB_NEAREST_5M",
    adjustment_mode: Annotated[str, typer.Option()] = "unadjusted",
    roll_days_before_expiration: Annotated[int, typer.Option("--roll-days-before-expiration")] = 3,
    metadata_snapshot_ts: Annotated[str | None, typer.Option()] = None,
    session: Annotated[list[str] | None, typer.Option("--session")] = None,
    overwrite: Annotated[bool, typer.Option()] = False,
    reports_root: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        prefixes = normalize_secid_prefixes(
            secid_prefix=secid_prefix,
            secid_prefixes=secid_prefixes,
        )
        config = NearestContractBuildConfig(
            data_root=data_root,
            output_root=output_root,
            exchange=exchange,
            engine=engine,
            market=market,
            board=board,
            continuous_symbol=continuous_symbol,
            timeframe=timeframe,
            metadata_snapshot_ts=metadata_snapshot_ts,
            query=query,
            asset_code=asset_code,
            secid_prefixes=prefixes,
            include_secid=include_secid or [],
            exclude_secid=exclude_secid or [],
            roll_days_before_expiration=roll_days_before_expiration,
            adjustment_mode=adjustment_mode,
            overwrite=overwrite,
            reports_root=reports_root,
            session_model=parse_session_options(session),
        )
        result = build_nearest_contract_dataset(config)
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    _print_result(result)


@nearest_app.command("diagnose-gaps")
def nearest_diagnose_gaps(
    dataset_path: Annotated[Path, typer.Option()],
    report_root: Annotated[Path, typer.Option()],
    bar_minutes: Annotated[int, typer.Option()] = 5,
    session: Annotated[list[str] | None, typer.Option("--session")] = None,
    update_quality: Annotated[bool, typer.Option()] = True,
) -> None:
    try:
        result, updated_files = diagnose_dataset_session_gaps(
            dataset_path,
            report_root,
            bar_minutes=bar_minutes,
            session_model=parse_session_options(session),
            update_quality=update_quality,
        )
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    _print_result(result)
    if updated_files:
        typer.echo("updated_files: " + ", ".join(str(path) for path in updated_files))
