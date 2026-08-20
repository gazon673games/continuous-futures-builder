from __future__ import annotations

import json
from datetime import time
from pathlib import Path
from typing import Annotated

import polars as pl
import typer

from continuous_futures_builder.exceptions import ContinuousFuturesError
from continuous_futures_builder.market_registry import (
    build_moex_instrument_registry,
    build_observed_session_calendar,
)
from continuous_futures_builder.quality_gate import (
    QualityGateResult,
)

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


@app.command("build-instrument-registry")
def build_instrument_registry_command(
    metadata_snapshot_path: Annotated[
        Path,
        typer.Option(
            help="Path to normalized MOEX futures metadata snapshot parquet or directory."
        ),
    ],
    output_path: Annotated[Path, typer.Option(help="Output JSON instrument registry path.")],
    symbol_prefix: Annotated[
        list[str] | None,
        typer.Option("--symbol-prefix", help="Optional SECID prefix filter. Repeatable."),
    ] = None,
) -> None:
    """Build a MOEX FORTS instrument spec registry from local metadata."""

    try:
        result = build_moex_instrument_registry(
            metadata_snapshot_path=metadata_snapshot_path,
            output_path=output_path,
            symbol_prefixes=symbol_prefix,
        )
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print_result(result)


@app.command("build-observed-calendar")
def build_observed_calendar_command(
    dataset_path: Annotated[Path, typer.Option(help="Dataset parquet or directory to observe.")],
    output_path: Annotated[Path, typer.Option(help="Output JSON session calendar path.")],
    main_session_start: Annotated[str, typer.Option(help="Main session start HH:MM.")] = "10:00",
    main_session_end: Annotated[str, typer.Option(help="Main session end HH:MM.")] = "18:50",
    evening_session_start: Annotated[
        str | None,
        typer.Option(help="Evening session start HH:MM, or omit."),
    ] = "19:00",
    evening_session_end: Annotated[
        str | None,
        typer.Option(help="Evening session end HH:MM, or omit."),
    ] = "23:50",
    short_day_bar_ratio: Annotated[
        float,
        typer.Option(help="Mark days below this fraction of median main-session bars as short."),
    ] = 0.8,
) -> None:
    """Build an observed session calendar from local dataset timestamps."""

    try:
        result = build_observed_session_calendar(
            dataset_path=dataset_path,
            output_path=output_path,
            main_session_start=time.fromisoformat(main_session_start),
            main_session_end=time.fromisoformat(main_session_end),
            evening_session_start=(
                time.fromisoformat(evening_session_start)
                if evening_session_start is not None
                else None
            ),
            evening_session_end=(
                time.fromisoformat(evening_session_end) if evening_session_end is not None else None
            ),
            short_day_bar_ratio=short_day_bar_ratio,
        )
    except ContinuousFuturesError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print_result(result)
