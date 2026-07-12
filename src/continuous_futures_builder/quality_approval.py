from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from continuous_futures_builder.exceptions import ContinuousFuturesError


class QualityApprovalResult(BaseModel):
    approval_path: Path
    approval_status: str
    continuous_symbol: str
    timeframe: str
    dataset_path: str | None
    dataset_sha256: str | None
    gap_audit_summary_path: str
    gap_audit_sha256: str
    approved_from_date: date | None
    usable_decision_day_pct: float | None
    min_usable_decision_day_pct: float
    allowed_decision_times: list[str]
    allowed_sma_window_bars: list[int]
    reasons: list[str]


def approve_gap_audit(
    *,
    gap_audit_summary_path: Path,
    output_path: Path,
    dataset_path: Path | None = None,
    min_usable_decision_day_pct: float = 0.95,
    approved_from_date: date | None = None,
    allowed_decision_times: list[str] | None = None,
    allowed_sma_window_bars: list[int] | None = None,
) -> QualityApprovalResult:
    if min_usable_decision_day_pct < 0.0 or min_usable_decision_day_pct > 1.0:
        raise ContinuousFuturesError("min_usable_decision_day_pct must be between 0 and 1")
    summary = _read_json(gap_audit_summary_path)
    effective_dataset_path = dataset_path or _optional_path(summary.get("dataset_path"))
    usable_pct = _optional_float(summary.get("usable_decision_day_pct"))
    effective_from_date = approved_from_date or _optional_date(
        summary.get("recommended_start_date")
    )
    effective_decision_times = allowed_decision_times or [str(summary.get("decision_time"))]
    effective_decision_times = [
        value for value in effective_decision_times if value and value != "None"
    ]
    effective_sma_windows = allowed_sma_window_bars or [
        _optional_int(summary.get("sma_window_bars"))
    ]
    effective_sma_windows = [value for value in effective_sma_windows if value is not None]

    reasons: list[str] = []
    if usable_pct is None:
        reasons.append("usable_decision_day_pct is missing")
    elif usable_pct < min_usable_decision_day_pct:
        reasons.append(
            f"usable_decision_day_pct {usable_pct} is below {min_usable_decision_day_pct}"
        )
    if not effective_decision_times:
        reasons.append("no allowed decision times")
    if not effective_sma_windows:
        reasons.append("no allowed SMA windows")

    dataset_sha = None
    if effective_dataset_path is not None:
        dataset_sha = _file_sha256(effective_dataset_path)

    approval_status = "approved" if not reasons else "rejected"
    payload = {
        "approval_version": 1,
        "approval_status": approval_status,
        "continuous_symbol": summary.get("continuous_symbol"),
        "timeframe": summary.get("timeframe"),
        "dataset_path": str(effective_dataset_path) if effective_dataset_path is not None else None,
        "dataset_sha256": dataset_sha,
        "gap_audit_summary_path": str(gap_audit_summary_path),
        "gap_audit_sha256": _file_sha256(gap_audit_summary_path),
        "approved_from_date": (
            effective_from_date.isoformat() if effective_from_date is not None else None
        ),
        "usable_decision_day_pct": usable_pct,
        "min_usable_decision_day_pct": min_usable_decision_day_pct,
        "allowed_decision_times": effective_decision_times,
        "allowed_sma_window_bars": effective_sma_windows,
        "reasons": reasons,
        "source_gap_audit_summary": summary,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return QualityApprovalResult(
        approval_path=output_path,
        approval_status=approval_status,
        continuous_symbol=str(summary.get("continuous_symbol")),
        timeframe=str(summary.get("timeframe")),
        dataset_path=str(effective_dataset_path) if effective_dataset_path is not None else None,
        dataset_sha256=dataset_sha,
        gap_audit_summary_path=str(gap_audit_summary_path),
        gap_audit_sha256=payload["gap_audit_sha256"],
        approved_from_date=effective_from_date,
        usable_decision_day_pct=usable_pct,
        min_usable_decision_day_pct=min_usable_decision_day_pct,
        allowed_decision_times=effective_decision_times,
        allowed_sma_window_bars=effective_sma_windows,
        reasons=reasons,
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ContinuousFuturesError(f"gap audit summary does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContinuousFuturesError(f"invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ContinuousFuturesError("gap audit summary JSON must be an object")
    return payload


def _file_sha256(path: Path) -> str:
    if not path.exists():
        raise ContinuousFuturesError(f"file does not exist: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_path(value: Any) -> Path | None:
    if value is None:
        return None
    return Path(str(value))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_date(value: Any) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(str(value))
