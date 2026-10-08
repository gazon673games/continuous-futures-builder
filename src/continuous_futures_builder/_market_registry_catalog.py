from __future__ import annotations

from math import isfinite
from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder._market_registry_helpers import (
    _dataset_id,
    _dataset_type_from_path,
    _file_content_sha256,
    _find_quality_approval_path,
    _optional_float,
    _path_partitions,
    _path_sha256,
    _read_json,
    _read_quality_manifest,
    _require_positive,
    _to_iso,
    _utc_now,
    _write_json,
)
from continuous_futures_builder._market_registry_models import (
    ArtifactWriteResult,
    AuditArtifactResult,
)
from continuous_futures_builder.exceptions import ContinuousFuturesError


def build_dataset_catalog(
    *,
    derived_root: Path,
    reports_root: Path | None,
    output_path: Path,
    instrument_registry_path: Path | None = None,
    calendar_path: Path | None = None,
    instrument_registry_audit_path: Path | None = None,
    calendar_audit_path: Path | None = None,
) -> ArtifactWriteResult:
    dataset_files = sorted(derived_root.rglob("*.parquet"))
    entries: list[dict[str, Any]] = []
    quality_manifest = _read_quality_manifest(reports_root)
    for dataset_file in dataset_files:
        partitions = _path_partitions(dataset_file)
        if "continuous_symbol" not in partitions or "timeframe" not in partitions:
            continue
        frame = pl.read_parquet(dataset_file)
        if frame.height == 0:
            continue
        continuous_symbol = str(partitions["continuous_symbol"])
        dataset_id = _dataset_id(partitions)
        quality_entry = quality_manifest.get(
            (continuous_symbol, str(dataset_file)),
            quality_manifest.get((continuous_symbol, str(partitions.get("adjustment_mode")))),
        )
        quality_approval_path = _find_quality_approval_path(reports_root, continuous_symbol)
        entries.append(
            {
                "dataset_id": dataset_id,
                "dataset_path": str(dataset_file),
                "dataset_sha256": _file_content_sha256(dataset_file),
                "dataset_type": partitions.get("dataset_type")
                or _dataset_type_from_path(dataset_file),
                "exchange": partitions.get("exchange"),
                "engine": partitions.get("engine"),
                "market": partitions.get("market"),
                "continuous_symbol": continuous_symbol,
                "timeframe": partitions.get("timeframe"),
                "adjustment_mode": partitions.get("adjustment_mode"),
                "start_ts": _to_iso(frame.get_column("ts").min())
                if "ts" in frame.columns
                else None,
                "end_ts": _to_iso(frame.get_column("ts").max()) if "ts" in frame.columns else None,
                "row_count": frame.height,
                "quality_status": quality_entry.get("status") if quality_entry else None,
                "approved_for_research": (
                    quality_entry.get("approved_for_research") if quality_entry else None
                ),
                "quality_approval_path": (
                    str(quality_approval_path) if quality_approval_path is not None else None
                ),
                "instrument_registry_path": (
                    str(instrument_registry_path) if instrument_registry_path is not None else None
                ),
                "instrument_registry_audit_path": (
                    str(instrument_registry_audit_path)
                    if instrument_registry_audit_path is not None
                    else None
                ),
                "calendar_path": str(calendar_path) if calendar_path is not None else None,
                "calendar_audit_path": (
                    str(calendar_audit_path) if calendar_audit_path is not None else None
                ),
            }
        )
    entries.sort(key=lambda item: item["dataset_id"])
    payload = {
        "catalog_version": 1,
        "generated_at": _utc_now(),
        "derived_root": str(derived_root),
        "reports_root": str(reports_root) if reports_root is not None else None,
        "entries": entries,
    }
    _write_json(output_path, payload)
    return ArtifactWriteResult(output_path=output_path, item_count=len(entries))


def audit_instrument_registry(
    *,
    registry_path: Path,
    output_path: Path,
) -> AuditArtifactResult:
    payload = _read_json(registry_path, "instrument registry")
    instruments = payload.get("instruments", [])
    if not isinstance(instruments, list):
        raise ContinuousFuturesError("instrument registry instruments must be a list")
    issues: list[str] = []
    warnings: list[str] = []
    seen_symbols: set[str] = set()
    duplicate_symbols: set[str] = set()
    for index, item in enumerate(instruments):
        if not isinstance(item, dict):
            issues.append(f"instrument[{index}] is not an object")
            continue
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            issues.append(f"instrument[{index}] missing symbol")
            continue
        if symbol in seen_symbols:
            duplicate_symbols.add(symbol)
        seen_symbols.add(symbol)
        _require_positive(item, "contract_multiplier", symbol, issues)
        _require_positive(item, "lot_size", symbol, issues)
        for field, value in item.items():
            if isinstance(value, int | float) and not isfinite(value):
                issues.append(f"{symbol}: {field} must be finite")
        tick_size = _optional_float(item.get("tick_size"))
        tick_value = _optional_float(item.get("tick_value"))
        if tick_size is None and tick_value is None:
            issues.append(f"{symbol}: tick_size or tick_value is required")
        if tick_size is not None and (not isfinite(tick_size) or tick_size <= 0.0):
            issues.append(f"{symbol}: tick_size must be positive")
        if tick_value is not None and (not isfinite(tick_value) or tick_value <= 0.0):
            issues.append(f"{symbol}: tick_value must be positive")
        if _optional_float(item.get("initial_margin")) is None:
            warnings.append(f"{symbol}: initial_margin is missing")
    if not instruments:
        issues.append("instrument registry is empty")
    for symbol in sorted(duplicate_symbols):
        issues.append(f"{symbol}: duplicate instrument symbol")
    result = AuditArtifactResult(
        output_path=output_path,
        audit_status="approved" if not issues else "rejected",
        issue_count=len(issues),
        warning_count=len(warnings),
        item_count=len(instruments),
        issues=issues,
        warnings=warnings,
    )
    _write_json(
        output_path,
        {
            "audit_version": 1,
            "audit_type": "instrument_registry",
            "generated_at": _utc_now(),
            "registry_path": str(registry_path),
            "registry_sha256": _path_sha256(registry_path),
            **result.model_dump(mode="json"),
        },
    )
    return result
