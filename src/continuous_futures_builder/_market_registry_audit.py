from __future__ import annotations

from pathlib import Path
from typing import Any

from continuous_futures_builder._market_registry_build import (
    build_moex_instrument_registry,
    build_observed_session_calendar,
)
from continuous_futures_builder._market_registry_catalog import (
    audit_instrument_registry,
    build_dataset_catalog,
)
from continuous_futures_builder._market_registry_helpers import (
    _date_set,
    _path_sha256,
    _read_json,
    _utc_now,
    _write_json,
)
from continuous_futures_builder._market_registry_models import (
    AuditArtifactResult,
)


def audit_session_calendar(
    *,
    calendar_path: Path,
    output_path: Path,
) -> AuditArtifactResult:
    payload = _read_json(calendar_path, "session calendar")
    issues: list[str] = []
    warnings: list[str] = []
    if "observed" in str(payload.get("calendar_type", "")) or "observed" in str(
        payload.get("trading_calendar", "")
    ):
        issues.append("Expected calendar must be independent of the checked dataset")
    for field_name in ("main_session_start", "main_session_end"):
        if not payload.get(field_name):
            issues.append(f"{field_name} is required")
    trading_dates = _date_set(payload.get("trading_dates", []), "trading_dates", issues)
    holidays = _date_set(payload.get("holiday_dates", []), "holiday_dates", issues)
    short_days = _date_set(payload.get("short_day_dates", []), "short_day_dates", issues)
    overlap = sorted(trading_dates.intersection(holidays))
    if overlap:
        issues.append("holiday_dates overlap trading_dates: " + ", ".join(overlap[:10]))
    orphan_short_days = sorted(short_days.difference(trading_dates))
    if orphan_short_days:
        warnings.append(
            "short_day_dates are not present in trading_dates: " + ", ".join(orphan_short_days[:10])
        )
    if not trading_dates:
        warnings.append("trading_dates is empty; calendar can still act as holiday/session config")
    result = AuditArtifactResult(
        output_path=output_path,
        audit_status="approved" if not issues else "rejected",
        issue_count=len(issues),
        warning_count=len(warnings),
        item_count=len(trading_dates),
        issues=issues,
        warnings=warnings,
    )
    _write_json(
        output_path,
        {
            "audit_version": 1,
            "audit_type": "session_calendar",
            "generated_at": _utc_now(),
            "calendar_path": str(calendar_path),
            "calendar_sha256": _path_sha256(calendar_path),
            **result.model_dump(mode="json"),
        },
    )
    return result


def prepare_backtest_artifacts(
    *,
    metadata_snapshot_path: Path,
    observed_dataset_path: Path,
    derived_root: Path,
    reports_root: Path | None,
    output_dir: Path,
    symbol_prefixes: list[str] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    registry_path = output_dir / "moex_forts_instrument_specs.json"
    calendar_path = output_dir / "observed_session_calendar.json"
    registry_audit_path = output_dir / "moex_forts_instrument_specs_audit.json"
    calendar_audit_path = output_dir / "observed_session_calendar_audit.json"
    catalog_path = output_dir / "data_catalog.json"
    registry_result = build_moex_instrument_registry(
        metadata_snapshot_path=metadata_snapshot_path,
        output_path=registry_path,
        symbol_prefixes=symbol_prefixes,
    )
    calendar_result = build_observed_session_calendar(
        dataset_path=observed_dataset_path,
        output_path=calendar_path,
    )
    registry_audit = audit_instrument_registry(
        registry_path=registry_path,
        output_path=registry_audit_path,
    )
    calendar_audit = audit_session_calendar(
        calendar_path=calendar_path,
        output_path=calendar_audit_path,
    )
    catalog_result = build_dataset_catalog(
        derived_root=derived_root,
        reports_root=reports_root,
        output_path=catalog_path,
        instrument_registry_path=registry_path,
        calendar_path=calendar_path,
        instrument_registry_audit_path=registry_audit_path,
        calendar_audit_path=calendar_audit_path,
    )
    manifest = {
        "manifest_version": 1,
        "generated_at": _utc_now(),
        "metadata_snapshot_path": str(metadata_snapshot_path),
        "observed_dataset_path": str(observed_dataset_path),
        "derived_root": str(derived_root),
        "reports_root": str(reports_root) if reports_root is not None else None,
        "symbol_prefixes": symbol_prefixes or [],
        "registry": registry_result.model_dump(mode="json"),
        "calendar": calendar_result.model_dump(mode="json"),
        "registry_audit": registry_audit.model_dump(mode="json"),
        "calendar_audit": calendar_audit.model_dump(mode="json"),
        "catalog": catalog_result.model_dump(mode="json"),
    }
    _write_json(output_dir / "backtest_artifacts_manifest.json", manifest)
    return manifest
