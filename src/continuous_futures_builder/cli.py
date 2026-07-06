from __future__ import annotations

import json
from datetime import time
from pathlib import Path
from typing import Annotated

import polars as pl
import typer

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.exceptions import ContinuousFuturesError
from continuous_futures_builder.intraday_gap_audit import (
    IntradayGapAuditConfig,
    write_gap_audit_reports,
)
from continuous_futures_builder.investigation import (
    CANDIDATE_COLUMNS,
    CANDLE_WINDOW_COLUMNS,
    CONTINUOUS_WINDOW_COLUMNS,
    save_roll_investigation,
)
from continuous_futures_builder.investigation import (
    investigate_roll as build_roll_investigation,
)
from continuous_futures_builder.models import ContractChainResult
from continuous_futures_builder.nearest_contract import (
    NearestContractBuildConfig,
    build_nearest_contract_dataset,
)
from continuous_futures_builder.nearest_gap_diagnostics import (
    diagnose_dataset_session_gaps,
    parse_session_options,
)
from continuous_futures_builder.prefixes import infer_contract_prefix, normalize_secid_prefixes
from continuous_futures_builder.quality import build_quality_report
from continuous_futures_builder.quality_gate import (
    QualityGateResult,
    evaluate_quality_gate,
    load_quality_gate_policy,
    save_quality_gate_manifest,
)
from continuous_futures_builder.roll_report import (
    build_roll_report,
    filter_roll_report,
    save_roll_diagnostics,
)
from continuous_futures_builder.storage import ContinuousFuturesStorage

app = typer.Typer(help="Build local derived continuous futures datasets.")
nearest_app = typer.Typer(help="Build nearest-contract futures datasets.")
app.add_typer(nearest_app, name="nearest")


def _print_result(result: object) -> None:
    data = result.model_dump() if hasattr(result, "model_dump") else result
    for key, value in data.items():
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value) if value else ""
        else:
            rendered = value
        typer.echo(f"{key}: {rendered}")


def _print_quality_gate_result(result: QualityGateResult) -> None:
    typer.echo(f"dataset: {result.dataset or ''}")
    typer.echo(f"status: {result.status}")
    typer.echo(f"approved_for_research: {result.approved_for_research}")
    typer.echo(f"reason_count: {result.reason_count}")
    typer.echo(f"reasons: {json.dumps(result.reasons, ensure_ascii=False)}")
    typer.echo(
        "metrics: "
        + json.dumps(result.metrics.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    )


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


def _format_table_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6g}"
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


def _print_frame_table(title: str, frame: pl.DataFrame, columns: tuple[str, ...]) -> None:
    typer.echo(f"{title}:")
    available = [column for column in columns if column in frame.columns]
    if frame.height == 0 or not available:
        typer.echo("none")
        return
    typer.echo(" | ".join(available))
    for row in frame.select(available).iter_rows(named=True):
        typer.echo(" | ".join(_format_table_value(row.get(column)) for column in available))


def _print_source_summary(title: str, summary: object) -> None:
    data = summary.model_dump() if hasattr(summary, "model_dump") else summary
    typer.echo(f"{title}:")
    for key in ("secid", "board", "row_count", "min_ts", "max_ts", "close_used_ts", "close_used"):
        typer.echo(f"{key}: {_format_table_value(data.get(key))}")


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
