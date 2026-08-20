from __future__ import annotations

import json
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from continuous_futures_builder.exceptions import ContinuousFuturesError, InputDataError
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
