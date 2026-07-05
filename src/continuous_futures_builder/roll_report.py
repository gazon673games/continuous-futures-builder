from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder.models import RollReportRow, RollReportSaveResult
from continuous_futures_builder.prefixes import infer_contract_prefix

LARGE_ROLL_GAP_PERCENT = 5.0


def _date_value(value: Any) -> Any:
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else value


def _float_value(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _gap_percent(old_close: float | None, roll_gap: float | None) -> float | None:
    if old_close in (None, 0.0) or roll_gap is None:
        return None
    return (roll_gap / old_close) * 100.0


def _warning(
    *,
    old_close: float | None,
    new_close: float | None,
    roll_gap_pct: float | None,
    is_family_transition: bool,
) -> str | None:
    warnings: list[str] = []
    if old_close is None:
        warnings.append("old close is unavailable")
    if new_close is None:
        warnings.append("new close is unavailable")
    if is_family_transition:
        warnings.append("family transition")
    if roll_gap_pct is not None and abs(roll_gap_pct) >= LARGE_ROLL_GAP_PERCENT:
        warnings.append(f"large roll gap: {roll_gap_pct:.2f}%")
    return "; ".join(warnings) if warnings else None


def _rows_by_contract(frame: pl.DataFrame) -> dict[str, int]:
    if "active_contract" not in frame.columns:
        return {}
    return {
        str(row["active_contract"]): int(row["len"])
        for row in frame.group_by("active_contract", maintain_order=True)
        .len()
        .iter_rows(named=True)
    }


def build_roll_report(path: str | Path) -> list[RollReportRow]:
    frame = pl.read_parquet(path).sort("ts")
    if frame.height < 2 or "active_contract" not in frame.columns:
        return []

    rows_by_contract = _rows_by_contract(frame)
    rows = frame.iter_rows(named=True)
    report: list[RollReportRow] = []
    previous: dict[str, Any] | None = None
    for row in rows:
        if previous is None:
            previous = row
            continue

        from_contract = str(previous["active_contract"])
        to_contract = str(row["active_contract"])
        if from_contract == to_contract:
            previous = row
            continue

        from_prefix = infer_contract_prefix(from_contract)
        to_prefix = infer_contract_prefix(to_contract)
        old_close = _float_value(previous.get("close"))
        new_close = _float_value(row.get("close"))
        roll_gap = None
        if old_close is not None and new_close is not None:
            roll_gap = new_close - old_close
        roll_gap_pct = _gap_percent(old_close, roll_gap)
        is_family_transition = from_prefix != to_prefix

        report.append(
            RollReportRow(
                roll_id=row.get("roll_id"),
                roll_date=_date_value(row.get("roll_date") or row.get("ts")),
                from_contract=from_contract,
                to_contract=to_contract,
                from_prefix=from_prefix,
                to_prefix=to_prefix,
                is_family_transition=is_family_transition,
                old_close=old_close,
                new_close=new_close,
                roll_gap=roll_gap,
                roll_gap_pct=roll_gap_pct,
                adjustment_factor_before=_float_value(previous.get("adjustment_factor")),
                adjustment_factor_after=_float_value(row.get("adjustment_factor")),
                rows_before_roll=rows_by_contract.get(from_contract),
                rows_after_roll=rows_by_contract.get(to_contract),
                warning=_warning(
                    old_close=old_close,
                    new_close=new_close,
                    roll_gap_pct=roll_gap_pct,
                    is_family_transition=is_family_transition,
                ),
            )
        )
        previous = row

    return report


def filter_roll_report(
    rows: list[RollReportRow],
    *,
    top: int | None = None,
    family_transitions_only: bool = False,
) -> list[RollReportRow]:
    selected = (
        [row for row in rows if row.is_family_transition] if family_transitions_only else list(rows)
    )
    if top is not None:
        selected = sorted(
            selected,
            key=lambda row: abs(row.roll_gap) if row.roll_gap is not None else -1.0,
            reverse=True,
        )[:top]
    return selected


def roll_report_frame(rows: list[RollReportRow]) -> pl.DataFrame:
    data = [row.model_dump(mode="json") for row in rows]
    if data:
        return pl.DataFrame(data)
    return pl.DataFrame(
        schema={
            "roll_id": pl.Utf8,
            "roll_date": pl.Date,
            "from_contract": pl.Utf8,
            "to_contract": pl.Utf8,
            "from_prefix": pl.Utf8,
            "to_prefix": pl.Utf8,
            "is_family_transition": pl.Boolean,
            "old_close": pl.Float64,
            "new_close": pl.Float64,
            "roll_gap": pl.Float64,
            "roll_gap_pct": pl.Float64,
            "adjustment_factor_before": pl.Float64,
            "adjustment_factor_after": pl.Float64,
            "rows_before_roll": pl.Int64,
            "rows_after_roll": pl.Int64,
            "warning": pl.Utf8,
        }
    )


def save_roll_diagnostics(path: str | Path, output_dir: str | Path) -> RollReportSaveResult:
    from continuous_futures_builder.quality import build_quality_report

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    rows = build_roll_report(path)
    frame = roll_report_frame(rows)
    parquet_path = target_dir / "roll_report.parquet"
    csv_path = target_dir / "roll_report.csv"
    json_path = target_dir / "quality_summary.json"

    frame.write_parquet(parquet_path)
    frame.write_csv(csv_path)
    quality_report = build_quality_report(path)
    json_path.write_text(
        json.dumps(quality_report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )

    return RollReportSaveResult(
        output_dir=target_dir,
        roll_report_parquet=parquet_path,
        roll_report_csv=csv_path,
        quality_summary_json=json_path,
    )
