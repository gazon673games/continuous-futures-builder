from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import BaseModel, Field

from continuous_futures_builder.exceptions import ContinuousFuturesError


class ArtifactWriteResult(BaseModel):
    output_path: Path
    item_count: int
    source_path: Path | None = None
    source_sha256: str | None = None
    warnings: list[str] = Field(default_factory=list)


class AuditArtifactResult(BaseModel):
    output_path: Path
    audit_status: str
    issue_count: int
    warning_count: int
    item_count: int
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def build_moex_instrument_registry(
    *,
    metadata_snapshot_path: Path,
    output_path: Path,
    symbol_prefixes: list[str] | None = None,
) -> ArtifactWriteResult:
    frame = _read_parquet_path(metadata_snapshot_path, "metadata snapshot")
    prefixes = [value.upper() for value in symbol_prefixes or []]
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []

    for row in frame.iter_rows(named=True):
        secid = str(row.get("secid") or "").strip()
        if not secid:
            continue
        if prefixes and not any(secid.upper().startswith(prefix) for prefix in prefixes):
            continue
        raw = _parse_raw_metadata(row.get("raw_metadata"), secid, warnings)
        lot_size = _optional_float(raw.get("lotvolume"), row.get("lot_size")) or 1.0
        tick_size = _optional_float(raw.get("minstep"))
        tick_value = _optional_float(raw.get("stepprice"))
        initial_margin = _optional_float(raw.get("initialmargin"))
        valid_to = _optional_date(
            raw.get("lasttradedate"),
            row.get("last_trade_date"),
            row.get("expiration_date"),
        )
        rows.append(
            {
                "symbol": secid,
                "valid_to": valid_to.isoformat() if valid_to is not None else None,
                "price_currency": str(row.get("currency") or "RUB"),
                "pnl_currency": "RUB",
                "contract_multiplier": 1.0,
                "lot_size": lot_size,
                "tick_size": tick_size,
                "tick_value": tick_value,
                "initial_margin": initial_margin,
                "assetcode": row.get("assetcode"),
                "board": row.get("board") or raw.get("boardid"),
                "expiration_date": _optional_date(row.get("expiration_date")),
                "last_trade_date": _optional_date(row.get("last_trade_date")),
                "source": "moex_normalized_metadata",
            }
        )

    rows.sort(key=lambda item: str(item["symbol"]))
    payload = {
        "registry_version": 1,
        "registry_type": "moex_forts_instrument_specs",
        "generated_at": _utc_now(),
        "source_metadata_snapshot_path": str(metadata_snapshot_path),
        "source_sha256": _path_sha256(metadata_snapshot_path),
        "symbol_prefixes": prefixes,
        "instruments": rows,
        "warnings": warnings,
    }
    _write_json(output_path, payload)
    return ArtifactWriteResult(
        output_path=output_path,
        item_count=len(rows),
        source_path=metadata_snapshot_path,
        source_sha256=payload["source_sha256"],
        warnings=warnings,
    )


def build_observed_session_calendar(
    *,
    dataset_path: Path,
    output_path: Path,
    main_session_start: time = time(10, 0),
    main_session_end: time = time(18, 50),
    evening_session_start: time | None = time(19, 0),
    evening_session_end: time | None = time(23, 50),
    short_day_bar_ratio: float = 0.8,
) -> ArtifactWriteResult:
    if short_day_bar_ratio <= 0.0 or short_day_bar_ratio > 1.0:
        raise ContinuousFuturesError("short_day_bar_ratio must be in (0, 1]")
    frame = _read_parquet_path(dataset_path, "dataset")
    if "ts" not in frame.columns:
        raise ContinuousFuturesError("dataset is missing required ts column")
    timestamps = [_as_datetime(value) for value in frame.get_column("ts").to_list()]
    if not timestamps:
        raise ContinuousFuturesError("dataset is empty")

    dates = sorted({value.date() for value in timestamps})
    trading_dates = [value.isoformat() for value in dates]
    holiday_dates = [
        value.isoformat()
        for value in _weekday_dates_between(dates[0], dates[-1])
        if value not in set(dates)
    ]
    main_counts: dict[date, int] = {value: 0 for value in dates}
    for ts in timestamps:
        if _time_in_range(ts.time(), main_session_start, main_session_end):
            main_counts[ts.date()] += 1
    positive_counts = [value for value in main_counts.values() if value > 0]
    expected_count = int(_median(positive_counts)) if positive_counts else 0
    short_threshold = expected_count * short_day_bar_ratio
    short_days = [
        value.isoformat()
        for value, count in sorted(main_counts.items())
        if expected_count > 0 and 0 < count < short_threshold
    ]

    payload = {
        "calendar_version": 1,
        "calendar_type": "observed_dataset_sessions",
        "generated_at": _utc_now(),
        "source_dataset_path": str(dataset_path),
        "source_sha256": _file_content_sha256(dataset_path),
        "trading_calendar": "observed",
        "session_filter": "all",
        "main_session_start": main_session_start.isoformat(timespec="minutes"),
        "main_session_end": main_session_end.isoformat(timespec="minutes"),
        "evening_session_start": (
            evening_session_start.isoformat(timespec="minutes")
            if evening_session_start is not None
            else None
        ),
        "evening_session_end": (
            evening_session_end.isoformat(timespec="minutes")
            if evening_session_end is not None
            else None
        ),
        "short_day_policy": "allow",
        "trading_dates": trading_dates,
        "holiday_dates": holiday_dates,
        "short_day_dates": short_days,
        "expected_main_session_bar_count": expected_count,
        "short_day_bar_ratio": short_day_bar_ratio,
    }
    _write_json(output_path, payload)
    return ArtifactWriteResult(
        output_path=output_path,
        item_count=len(trading_dates),
        source_path=dataset_path,
        source_sha256=payload["source_sha256"],
    )


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
        tick_size = _optional_float(item.get("tick_size"))
        tick_value = _optional_float(item.get("tick_value"))
        if tick_size is None and tick_value is None:
            issues.append(f"{symbol}: tick_size or tick_value is required")
        if tick_size is not None and tick_size <= 0.0:
            issues.append(f"{symbol}: tick_size must be positive")
        if tick_value is not None and tick_value <= 0.0:
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


def audit_session_calendar(
    *,
    calendar_path: Path,
    output_path: Path,
) -> AuditArtifactResult:
    payload = _read_json(calendar_path, "session calendar")
    issues: list[str] = []
    warnings: list[str] = []
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


def _read_parquet_path(path: Path, label: str) -> pl.DataFrame:
    if path.is_file():
        return pl.read_parquet(path)
    if path.is_dir():
        files = sorted(path.glob("*.parquet"))
        if not files:
            raise ContinuousFuturesError(f"{label} directory has no parquet files: {path}")
        frames = [pl.read_parquet(file) for file in files]
        return frames[0] if len(frames) == 1 else pl.concat(frames, how="diagonal_relaxed")
    raise ContinuousFuturesError(f"{label} path does not exist: {path}")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ContinuousFuturesError(f"{label} does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContinuousFuturesError(f"{label} must contain a JSON object: {path}")
    return payload


def _parse_raw_metadata(raw_metadata: Any, secid: str, warnings: list[str]) -> dict[str, Any]:
    if not raw_metadata:
        return {}
    try:
        payload = json.loads(str(raw_metadata))
    except json.JSONDecodeError:
        warnings.append(f"{secid}: raw_metadata is not valid JSON")
        return {}
    return payload if isinstance(payload, dict) else {}


def _optional_float(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _require_positive(
    item: dict[str, Any],
    field_name: str,
    symbol: str,
    issues: list[str],
) -> None:
    value = _optional_float(item.get(field_name))
    if value is None:
        issues.append(f"{symbol}: {field_name} is required")
    elif value <= 0.0:
        issues.append(f"{symbol}: {field_name} must be positive")


def _date_set(raw_values: Any, field_name: str, issues: list[str]) -> set[str]:
    if raw_values is None:
        return set()
    if not isinstance(raw_values, list):
        issues.append(f"{field_name} must be a list")
        return set()
    parsed = set()
    for raw_value in raw_values:
        value = _optional_date(raw_value)
        if value is None:
            issues.append(f"{field_name} contains invalid date: {raw_value}")
            continue
        parsed.add(value.isoformat())
    return parsed


def _optional_date(*values: Any) -> date | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            continue
    return None


def _path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        _update_digest_from_file(digest, path, path.name)
        return digest.hexdigest()
    if path.is_dir():
        files = sorted(file for file in path.rglob("*") if file.is_file())
        for file in files:
            _update_digest_from_file(digest, file, str(file.relative_to(path)))
        return digest.hexdigest()
    raise ContinuousFuturesError(f"path does not exist: {path}")


def _file_content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _update_digest_from_file(digest: Any, path: Path, label: str) -> None:
    digest.update(label.replace("\\", "/").encode("utf-8"))
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    return datetime.fromisoformat(str(value))


def _weekday_dates_between(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current = date.fromordinal(current.toordinal() + 1)
    return days


def _time_in_range(value: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= value <= end
    return value >= start or value <= end


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[midpoint])
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _path_partitions(path: Path) -> dict[str, str]:
    partitions = {}
    for part in path.parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        partitions[key] = value
    return partitions


def _dataset_type_from_path(path: Path) -> str:
    text = str(path).replace("\\", "/")
    if "/nearest_contract_futures/" in text:
        return "nearest_contract"
    if "/continuous_futures/" in text:
        return "continuous_futures"
    return "unknown"


def _dataset_id(partitions: dict[str, str]) -> str:
    parts = [
        partitions.get("continuous_symbol", "unknown"),
        partitions.get("timeframe", "unknown"),
        partitions.get("adjustment_mode", "unadjusted"),
    ]
    return ":".join(str(part) for part in parts)


def _read_quality_manifest(reports_root: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if reports_root is None:
        return {}
    manifest_path = reports_root / "continuous_futures" / "quality_gate_manifest.json"
    if not manifest_path.exists():
        return {}
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {}
    for item in payload.get("datasets", []):
        if not isinstance(item, dict):
            continue
        dataset = str(item.get("dataset") or "")
        path = str(item.get("path") or "")
        entries[(dataset, path)] = item
        entries[(dataset, str(Path(path)))] = item
        partitions = _path_partitions(Path(path))
        if partitions.get("adjustment_mode"):
            entries[(dataset, str(partitions["adjustment_mode"]))] = item
    return entries


def _find_quality_approval_path(reports_root: Path | None, continuous_symbol: str) -> Path | None:
    if reports_root is None:
        return None
    candidate = (
        reports_root
        / "nearest_contract_futures"
        / continuous_symbol
        / "gap_audit"
        / "quality_approval.json"
    )
    return candidate if candidate.exists() else None


def _to_iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
