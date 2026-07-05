from __future__ import annotations

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from continuous_futures_builder.exceptions import ContinuousFuturesError, InputDataError
from continuous_futures_builder.quality import build_quality_report
from continuous_futures_builder.roll_report import build_roll_report
from continuous_futures_builder.storage import ContinuousFuturesStorage

QualityGateStatus = Literal["approved", "rejected", "split_required"]


class QualityGatePolicy(BaseModel):
    max_duplicate_ts: int = 0
    max_null_price_count: int = 0
    max_null_active_contract_count: int = 0
    max_source_coverage_gap_days: int = 30
    max_roll_gap_pct: float = 25.0
    allow_family_transitions: bool = True
    max_family_transition_gap_pct: float = 25.0
    allow_empty_active_segments: bool = False
    require_validation_status_ok: bool = True
    max_contracts_pruned: int | None = None


class QualityGateMetrics(BaseModel):
    row_count: int | None = None
    min_ts: Any = None
    max_ts: Any = None
    duplicate_ts_count: int | None = None
    null_price_count: int | None = None
    null_active_contract_count: int | None = None
    contract_count: int | None = None
    roll_count: int | None = None
    max_abs_roll_gap: float | None = None
    max_abs_roll_gap_pct: float | None = None
    max_abs_roll_gap_roll_date: Any = None
    max_abs_roll_gap_contracts: list[str] = Field(default_factory=list)
    max_abs_roll_gap_pct_roll_date: Any = None
    max_abs_roll_gap_pct_contracts: list[str] = Field(default_factory=list)
    max_source_coverage_gap_days: int | None = None
    max_source_coverage_gap_roll_date: Any = None
    max_source_coverage_gap_contracts: list[str] = Field(default_factory=list)
    family_transition_count: int | None = None
    max_family_transition_gap_pct: float | None = None
    empty_active_segments: int | None = None
    contracts_pruned: int | None = None
    validation_status: str
    validation_error: str | None = None
    quality_status: str | None = None


class QualityGateResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    dataset: str | None
    path: Path
    status: QualityGateStatus
    reason_count: int
    reasons: list[str] = Field(default_factory=list)
    metrics: QualityGateMetrics
    approved_for_research: bool


class QualityGateManifest(BaseModel):
    generated_at: datetime
    policy: QualityGatePolicy
    datasets: list[QualityGateResult]


def _parse_scalar(value: str) -> object:
    text = value.strip()
    lowered = text.casefold()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text.strip("\"'")


def _load_simple_yaml(path: Path) -> dict[str, object]:
    data: dict[str, object] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise InputDataError(f"Unsupported YAML policy line: {line}")
        key, value = stripped.split(":", 1)
        data[key.strip()] = _parse_scalar(value)
    return data


def load_quality_gate_policy(path: str | Path | None = None) -> QualityGatePolicy:
    if path is None:
        return QualityGatePolicy()

    target = Path(path)
    if not target.exists():
        raise InputDataError(f"Quality gate policy does not exist: {target}")

    suffix = target.suffix.casefold()
    if suffix == ".json":
        data = json.loads(target.read_text(encoding="utf-8"))
    elif suffix == ".toml":
        with target.open("rb") as file:
            data = tomllib.load(file)
    elif suffix in {".yaml", ".yml"}:
        data = _load_simple_yaml(target)
    else:
        msg = "Unsupported quality gate policy format; use .json, .toml, .yaml, or .yml"
        raise InputDataError(msg)
    return QualityGatePolicy.model_validate(data)


def _date_value(value: object) -> object:
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else value


def _validation_status(path: Path) -> tuple[str, str | None]:
    try:
        ContinuousFuturesStorage(path.parent).validate_path(path)
    except ContinuousFuturesError as exc:
        return "failed", str(exc)
    return "ok", None


def _max_abs_roll_gap_pct_row(rows: list[Any]) -> tuple[float | None, object, list[str]]:
    candidates = [row for row in rows if row.roll_gap_pct is not None]
    if not candidates:
        return None, None, []
    selected = max(candidates, key=lambda row: abs(row.roll_gap_pct or 0.0))
    return (
        abs(selected.roll_gap_pct or 0.0),
        selected.roll_date,
        [selected.from_contract, selected.to_contract],
    )


def _max_family_transition_gap_pct(rows: list[Any]) -> float | None:
    values = [
        abs(row.roll_gap_pct)
        for row in rows
        if row.is_family_transition and row.roll_gap_pct is not None
    ]
    return max(values) if values else None


def _max_roll_timestamp_gap(frame: pl.DataFrame) -> tuple[int | None, object, list[str]]:
    if frame.height < 2 or "ts" not in frame.columns or "active_contract" not in frame.columns:
        return None, None, []

    previous: dict[str, Any] | None = None
    best_gap: int | None = None
    best_roll_date: object = None
    best_contracts: list[str] = []
    for row in frame.sort("ts").iter_rows(named=True):
        if previous is None:
            previous = row
            continue

        from_contract = str(previous.get("active_contract"))
        to_contract = str(row.get("active_contract"))
        if from_contract == to_contract:
            previous = row
            continue

        previous_ts = _date_value(previous.get("ts"))
        current_ts = _date_value(row.get("ts"))
        if previous_ts is None or current_ts is None:
            previous = row
            continue

        gap_days = max(0, (current_ts - previous_ts).days)
        if best_gap is None or gap_days > best_gap:
            best_gap = gap_days
            best_roll_date = _date_value(row.get("roll_date")) or current_ts
            best_contracts = [from_contract, to_contract]
        previous = row

    return best_gap, best_roll_date, best_contracts


def build_quality_gate_metrics(path: str | Path) -> QualityGateMetrics:
    target = Path(path)
    frame = pl.read_parquet(target)
    validation_status, validation_error = _validation_status(target)
    quality_report = build_quality_report(target)
    roll_rows = build_roll_report(target)
    max_roll_gap_pct, max_roll_gap_pct_date, max_roll_gap_pct_contracts = _max_abs_roll_gap_pct_row(
        roll_rows
    )
    max_source_gap, max_source_gap_date, max_source_gap_contracts = _max_roll_timestamp_gap(frame)

    return QualityGateMetrics(
        row_count=quality_report.row_count,
        min_ts=quality_report.min_ts,
        max_ts=quality_report.max_ts,
        duplicate_ts_count=quality_report.duplicate_ts_count,
        null_price_count=quality_report.null_price_count,
        null_active_contract_count=quality_report.null_active_contract_count,
        contract_count=quality_report.contract_count,
        roll_count=quality_report.roll_count,
        max_abs_roll_gap=quality_report.max_abs_roll_gap,
        max_abs_roll_gap_pct=max_roll_gap_pct,
        max_abs_roll_gap_roll_date=quality_report.max_abs_roll_gap_roll_date,
        max_abs_roll_gap_contracts=quality_report.max_abs_roll_gap_contracts,
        max_abs_roll_gap_pct_roll_date=max_roll_gap_pct_date,
        max_abs_roll_gap_pct_contracts=max_roll_gap_pct_contracts,
        max_source_coverage_gap_days=max_source_gap,
        max_source_coverage_gap_roll_date=max_source_gap_date,
        max_source_coverage_gap_contracts=max_source_gap_contracts,
        family_transition_count=quality_report.family_transition_count,
        max_family_transition_gap_pct=_max_family_transition_gap_pct(roll_rows),
        empty_active_segments=None,
        contracts_pruned=None,
        validation_status=validation_status,
        validation_error=validation_error,
        quality_status=quality_report.status,
    )


def evaluate_quality_gate(
    path: str | Path,
    policy: QualityGatePolicy | None = None,
) -> QualityGateResult:
    target = Path(path)
    effective_policy = policy or QualityGatePolicy()
    metrics = build_quality_gate_metrics(target)
    quality_report = build_quality_report(target)

    rejected_reasons: list[str] = []
    split_reasons: list[str] = []

    if effective_policy.require_validation_status_ok and metrics.validation_status != "ok":
        rejected_reasons.append(
            f"validation_status {metrics.validation_status} != ok"
            + (f": {metrics.validation_error}" if metrics.validation_error else "")
        )

    if (metrics.duplicate_ts_count or 0) > effective_policy.max_duplicate_ts:
        rejected_reasons.append(
            f"duplicate_ts_count {metrics.duplicate_ts_count} > {effective_policy.max_duplicate_ts}"
        )
    if (metrics.null_price_count or 0) > effective_policy.max_null_price_count:
        rejected_reasons.append(
            f"null_price_count {metrics.null_price_count} > {effective_policy.max_null_price_count}"
        )
    if (metrics.null_active_contract_count or 0) > effective_policy.max_null_active_contract_count:
        rejected_reasons.append(
            f"null_active_contract_count {metrics.null_active_contract_count} > "
            f"{effective_policy.max_null_active_contract_count}"
        )

    if (
        metrics.max_source_coverage_gap_days is not None
        and metrics.max_source_coverage_gap_days > effective_policy.max_source_coverage_gap_days
    ):
        split_reasons.append(
            f"source_coverage_gap_days {metrics.max_source_coverage_gap_days} > "
            f"{effective_policy.max_source_coverage_gap_days}"
            + (
                " at "
                f"{metrics.max_source_coverage_gap_roll_date} "
                f"{' -> '.join(metrics.max_source_coverage_gap_contracts)}"
                if metrics.max_source_coverage_gap_contracts
                else ""
            )
        )

    if (
        metrics.max_abs_roll_gap_pct is not None
        and metrics.max_abs_roll_gap_pct > effective_policy.max_roll_gap_pct
    ):
        split_reasons.append(
            f"roll_gap_pct {metrics.max_abs_roll_gap_pct:.4f} > "
            f"{effective_policy.max_roll_gap_pct:.4f}"
            + (
                " at "
                f"{metrics.max_abs_roll_gap_pct_roll_date} "
                f"{' -> '.join(metrics.max_abs_roll_gap_pct_contracts)}"
                if metrics.max_abs_roll_gap_pct_contracts
                else ""
            )
        )

    if not effective_policy.allow_family_transitions and (metrics.family_transition_count or 0):
        rejected_reasons.append(f"family_transition_count {metrics.family_transition_count} > 0")
    if (
        metrics.max_family_transition_gap_pct is not None
        and metrics.max_family_transition_gap_pct > effective_policy.max_family_transition_gap_pct
    ):
        split_reasons.append(
            f"family_transition_gap_pct {metrics.max_family_transition_gap_pct:.4f} > "
            f"{effective_policy.max_family_transition_gap_pct:.4f}"
        )

    if (
        not effective_policy.allow_empty_active_segments
        and metrics.empty_active_segments is not None
        and metrics.empty_active_segments > 0
    ):
        rejected_reasons.append(f"empty_active_segments {metrics.empty_active_segments} > 0")
    if (
        effective_policy.max_contracts_pruned is not None
        and metrics.contracts_pruned is not None
        and metrics.contracts_pruned > effective_policy.max_contracts_pruned
    ):
        rejected_reasons.append(
            f"contracts_pruned {metrics.contracts_pruned} > {effective_policy.max_contracts_pruned}"
        )

    reasons = [*rejected_reasons, *split_reasons]
    if rejected_reasons:
        status: QualityGateStatus = "rejected"
    elif split_reasons:
        status = "split_required"
    else:
        status = "approved"

    return QualityGateResult(
        dataset=quality_report.continuous_symbol,
        path=target,
        status=status,
        reason_count=len(reasons),
        reasons=reasons,
        metrics=metrics,
        approved_for_research=status == "approved",
    )


def save_quality_gate_manifest(
    results: list[QualityGateResult],
    output: str | Path,
    policy: QualityGatePolicy | None = None,
) -> Path:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = QualityGateManifest(
        generated_at=datetime.now(UTC),
        policy=policy or QualityGatePolicy(),
        datasets=results,
    )
    target.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return target
