from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from continuous_futures_builder._cli_core import (
    _print_result,
    app,
)
from continuous_futures_builder.exceptions import ContinuousFuturesError
from continuous_futures_builder.market_registry import (
    audit_instrument_registry,
    audit_session_calendar,
    build_dataset_catalog,
    prepare_backtest_artifacts,
)


@app.command("audit-instrument-registry")
def audit_instrument_registry_command(
    registry_path: Annotated[Path, typer.Option(help="Input JSON instrument registry path.")],
    output_path: Annotated[Path, typer.Option(help="Output JSON registry audit path.")],
) -> None:
    """Audit an instrument spec registry before using it in backtests."""

    try:
        result = audit_instrument_registry(
            registry_path=registry_path,
            output_path=output_path,
        )
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print_result(result)
    if result.audit_status != "approved":
        raise typer.Exit(1)


@app.command("audit-calendar")
def audit_calendar_command(
    calendar_path: Annotated[Path, typer.Option(help="Input JSON session calendar path.")],
    output_path: Annotated[Path, typer.Option(help="Output JSON calendar audit path.")],
) -> None:
    """Audit a session calendar artifact before using it in backtests."""

    try:
        result = audit_session_calendar(
            calendar_path=calendar_path,
            output_path=output_path,
        )
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print_result(result)
    if result.audit_status != "approved":
        raise typer.Exit(1)


@app.command("build-data-catalog")
def build_data_catalog_command(
    derived_root: Annotated[Path, typer.Option(help="Derived datasets root.")],
    output_path: Annotated[Path, typer.Option(help="Output JSON data catalog path.")],
    reports_root: Annotated[Path | None, typer.Option(help="Reports root.")] = None,
    instrument_registry_path: Annotated[
        Path | None,
        typer.Option(help="Optional default instrument registry linked into catalog entries."),
    ] = None,
    instrument_registry_audit_path: Annotated[
        Path | None,
        typer.Option(
            help="Optional default instrument registry audit linked into catalog entries."
        ),
    ] = None,
    calendar_path: Annotated[
        Path | None,
        typer.Option(help="Optional default calendar linked into catalog entries."),
    ] = None,
    calendar_audit_path: Annotated[
        Path | None,
        typer.Option(help="Optional default calendar audit linked into catalog entries."),
    ] = None,
) -> None:
    """Index local derived datasets into a reproducible data catalog."""

    try:
        result = build_dataset_catalog(
            derived_root=derived_root,
            reports_root=reports_root,
            output_path=output_path,
            instrument_registry_path=instrument_registry_path,
            calendar_path=calendar_path,
            instrument_registry_audit_path=instrument_registry_audit_path,
            calendar_audit_path=calendar_audit_path,
        )
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print_result(result)


@app.command("prepare-backtest-artifacts")
def prepare_backtest_artifacts_command(
    metadata_snapshot_path: Annotated[
        Path,
        typer.Option(
            help="Path to normalized MOEX futures metadata snapshot parquet or directory."
        ),
    ],
    observed_dataset_path: Annotated[
        Path,
        typer.Option(help="Dataset used to infer observed session calendar."),
    ],
    derived_root: Annotated[Path, typer.Option(help="Derived datasets root for catalog.")],
    output_dir: Annotated[Path, typer.Option(help="Output artifact directory.")],
    reports_root: Annotated[
        Path | None, typer.Option(help="Reports root for quality links.")
    ] = None,
    symbol_prefix: Annotated[
        list[str] | None,
        typer.Option("--symbol-prefix", help="SECID prefix for registry. Repeatable."),
    ] = None,
) -> None:
    """Build registry, calendar, audits, catalog, and one preparation manifest."""

    try:
        manifest = prepare_backtest_artifacts(
            metadata_snapshot_path=metadata_snapshot_path,
            observed_dataset_path=observed_dataset_path,
            derived_root=derived_root,
            reports_root=reports_root,
            output_dir=output_dir,
            symbol_prefixes=symbol_prefix,
        )
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"output_dir: {output_dir}")
    typer.echo(f"registry_audit_status: {manifest['registry_audit']['audit_status']}")
    typer.echo(f"calendar_audit_status: {manifest['calendar_audit']['audit_status']}")
    typer.echo(f"catalog_item_count: {manifest['catalog']['item_count']}")
