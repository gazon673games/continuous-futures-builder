from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder.exceptions import ContinuousFuturesError


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
