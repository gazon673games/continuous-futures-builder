from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from continuous_futures_builder._cli_core import (
    _format_table_value,
    _print_frame_table,
    _print_result,
    _print_roll_rows,
    _print_source_summary,
    app,
)
from continuous_futures_builder.exceptions import ContinuousFuturesError
from continuous_futures_builder.investigation import (
    CANDIDATE_COLUMNS,
    CANDLE_WINDOW_COLUMNS,
    CONTINUOUS_WINDOW_COLUMNS,
    save_roll_investigation,
)
from continuous_futures_builder.investigation import (
    investigate_roll as build_roll_investigation,
)
from continuous_futures_builder.roll_report import (
    build_roll_report,
    filter_roll_report,
    save_roll_diagnostics,
)


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


@app.command("investigate-roll")
def investigate_roll(
    data_root: Annotated[Path, typer.Option()],
    path: Annotated[Path, typer.Option()],
    roll_date: Annotated[str, typer.Option()],
    from_contract: Annotated[str, typer.Option()],
    to_contract: Annotated[str, typer.Option()],
    metadata_snapshot_ts: Annotated[str, typer.Option()],
    exchange: Annotated[str | None, typer.Option()] = None,
    engine: Annotated[str | None, typer.Option()] = None,
    market: Annotated[str | None, typer.Option()] = None,
    timeframe: Annotated[str | None, typer.Option()] = None,
    query: Annotated[str | None, typer.Option()] = "CNY",
    secid_prefix: Annotated[list[str] | None, typer.Option("--secid-prefix")] = None,
    roll_days_before: Annotated[int, typer.Option()] = 3,
    window_rows: Annotated[int, typer.Option(min=1)] = 20,
    structural_gap_roll_pct_threshold: Annotated[float, typer.Option()] = 25.0,
    structural_gap_calendar_days_threshold: Annotated[int, typer.Option()] = 90,
    save: Annotated[bool, typer.Option()] = False,
    output_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        investigation = build_roll_investigation(
            data_root=data_root,
            path=path,
            roll_date=roll_date,
            from_contract=from_contract,
            to_contract=to_contract,
            metadata_snapshot_ts=metadata_snapshot_ts,
            exchange=exchange,
            engine=engine,
            market=market,
            timeframe=timeframe,
            query=query,
            secid_prefix=secid_prefix,
            roll_days_before=roll_days_before,
            window_rows=window_rows,
            structural_gap_roll_pct_threshold=structural_gap_roll_pct_threshold,
            structural_gap_calendar_days_threshold=structural_gap_calendar_days_threshold,
        )
        if save:
            if output_dir is None:
                msg = "--output-dir is required when --save is passed"
                raise ContinuousFuturesError(msg)
            save_result = save_roll_investigation(investigation, output_dir)
        else:
            save_result = None
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    summary = investigation.summary
    typer.echo("investigation:")
    typer.echo(f"roll_date: {summary.roll_date}")
    typer.echo(f"transition: {summary.from_contract} -> {summary.to_contract}")
    typer.echo(f"old_close: {_format_table_value(summary.old_close)}")
    typer.echo(f"new_close: {_format_table_value(summary.new_close)}")
    typer.echo(f"roll_gap: {_format_table_value(summary.roll_gap)}")
    typer.echo(f"roll_gap_pct: {_format_table_value(summary.roll_gap_pct)}")
    typer.echo(f"primary_classification: {summary.primary_classification}")
    typer.echo(
        "secondary_classifications: "
        + (
            ", ".join(summary.secondary_classifications)
            if summary.secondary_classifications
            else ""
        )
    )
    typer.echo(f"structural_gap: {summary.structural_gap.triggered}")
    if summary.structural_gap.reasons:
        typer.echo("structural_gap_reasons: " + "; ".join(summary.structural_gap.reasons))
    typer.echo(f"recommendation: {summary.recommendation}")
    if summary.warnings:
        typer.echo("warnings: " + "; ".join(summary.warnings))
    _print_source_summary("from_contract_summary", summary.from_contract_summary)
    _print_source_summary("to_contract_summary", summary.to_contract_summary)
    _print_frame_table(
        "continuous_window", investigation.continuous_window, CONTINUOUS_WINDOW_COLUMNS
    )
    _print_frame_table(
        "from_contract_window", investigation.from_contract_window, CANDLE_WINDOW_COLUMNS
    )
    _print_frame_table(
        "to_contract_window", investigation.to_contract_window, CANDLE_WINDOW_COLUMNS
    )
    _print_frame_table(
        "candidate_contracts",
        investigation.candidate_contracts_frame,
        CANDIDATE_COLUMNS,
    )
    if save_result is not None:
        typer.echo("saved_artifacts:")
        _print_result(save_result)


if __name__ == "__main__":
    app()
