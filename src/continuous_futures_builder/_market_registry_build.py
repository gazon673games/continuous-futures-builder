from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Any

from continuous_futures_builder._market_registry_helpers import (
    _as_datetime,
    _file_content_sha256,
    _median,
    _optional_date,
    _optional_float,
    _parse_raw_metadata,
    _path_sha256,
    _read_parquet_path,
    _time_in_range,
    _utc_now,
    _weekday_dates_between,
    _write_json,
)
from continuous_futures_builder._market_registry_models import (
    ArtifactWriteResult,
)
from continuous_futures_builder.exceptions import ContinuousFuturesError


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
