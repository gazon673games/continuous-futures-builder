from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Annotated

import typer

from continuous_futures_builder._cli_core import (
    _print_quality_gate_result,
    _print_result,
    app,
    nearest_app,
)
from continuous_futures_builder.exceptions import ContinuousFuturesError
from continuous_futures_builder.intraday_gap_audit import (
    IntradayGapAuditConfig,
    write_gap_audit_reports,
)
from continuous_futures_builder.quality import build_quality_report
from continuous_futures_builder.quality_approval import approve_gap_audit
from continuous_futures_builder.quality_gate import (
    evaluate_quality_gate,
    load_quality_gate_policy,
    save_quality_gate_manifest,
)
from continuous_futures_builder.storage import ContinuousFuturesStorage


@nearest_app.command("audit-gaps")
def nearest_audit_gaps(
    dataset_path: Annotated[Path, typer.Option()],
    report_root: Annotated[Path, typer.Option()],
    session_gaps_path: Annotated[Path | None, typer.Option()] = None,
    decision_time: Annotated[str, typer.Option()] = "13:30",
    decision_tolerance_minutes: Annotated[int, typer.Option()] = 5,
    sma_window_bars: Annotated[int, typer.Option()] = 42,
    bar_minutes: Annotated[int, typer.Option()] = 5,
    consecutive_usable_days: Annotated[int, typer.Option()] = 30,
) -> None:
    try:
        config = IntradayGapAuditConfig(
            dataset_path=dataset_path,
            session_gaps_path=session_gaps_path,
            report_root=report_root,
            decision_time=time.fromisoformat(decision_time),
            decision_tolerance_minutes=decision_tolerance_minutes,
            sma_window_bars=sma_window_bars,
            bar_minutes=bar_minutes,
            consecutive_usable_days=consecutive_usable_days,
        )
        result = write_gap_audit_reports(config)
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    _print_result(result)


@nearest_app.command("approve-audit")
def nearest_approve_audit(
    gap_audit_summary_path: Annotated[Path, typer.Option()],
    output_path: Annotated[Path, typer.Option()],
    dataset_path: Annotated[Path | None, typer.Option()] = None,
    min_usable_decision_day_pct: Annotated[float, typer.Option()] = 0.95,
    approved_from_date: Annotated[str | None, typer.Option()] = None,
    allowed_decision_time: Annotated[
        list[str] | None,
        typer.Option("--allowed-decision-time"),
    ] = None,
    allowed_sma_window_bars: Annotated[
        list[int] | None,
        typer.Option("--allowed-sma-window-bars"),
    ] = None,
) -> None:
    try:
        result = approve_gap_audit(
            gap_audit_summary_path=gap_audit_summary_path,
            output_path=output_path,
            dataset_path=dataset_path,
            min_usable_decision_day_pct=min_usable_decision_day_pct,
            approved_from_date=(
                date.fromisoformat(approved_from_date) if approved_from_date is not None else None
            ),
            allowed_decision_times=allowed_decision_time,
            allowed_sma_window_bars=allowed_sma_window_bars,
        )
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    _print_result(result)
    if result.approval_status != "approved":
        raise typer.Exit(1)


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


@app.command("quality-gate")
def quality_gate(
    path: Annotated[Path | None, typer.Option()] = None,
    paths: Annotated[list[Path] | None, typer.Option("--paths")] = None,
    policy: Annotated[Path | None, typer.Option()] = None,
    save: Annotated[bool, typer.Option()] = False,
    output: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        target_paths = []
        if path is not None:
            target_paths.append(path)
        if paths:
            target_paths.extend(paths)
        if not target_paths:
            msg = "--path or at least one --paths value is required"
            raise ContinuousFuturesError(msg)

        effective_policy = load_quality_gate_policy(policy)
        results = [
            evaluate_quality_gate(target_path, effective_policy) for target_path in target_paths
        ]
        for index, result in enumerate(results):
            if index:
                typer.echo("")
            _print_quality_gate_result(result)

        if save:
            if output is None:
                msg = "--output is required when --save is passed"
                raise ContinuousFuturesError(msg)
            manifest_path = save_quality_gate_manifest(results, output, effective_policy)
            typer.echo(f"manifest_path: {manifest_path}")
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if any(result.status != "approved" for result in results):
        raise typer.Exit(1)
