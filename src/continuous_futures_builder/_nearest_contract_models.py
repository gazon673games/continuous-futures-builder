from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from continuous_futures_builder.constants import (
    ADJUSTMENT_UNADJUSTED,
)
from continuous_futures_builder.exceptions import (
    InputDataError,
)
from continuous_futures_builder.nearest_gap_diagnostics import (
    IntradaySessionModel,
)

NEAREST_DATASET_TYPE = "nearest_contract"
NEAREST_FUTURES_DATASET_DIR = "nearest_contract_futures"
NEAREST_QUALITY_OK = "ok"
NEAREST_QUALITY_WARNING = "warning"
NEAREST_QUALITY_REJECTED = "rejected"
LARGE_ROLL_GAP_PERCENT = 5.0
SOURCE_COVERAGE_GAP_WARNING_MINUTES = 3 * 24 * 60

NEAREST_OUTPUT_COLUMNS = (
    "source",
    "exchange",
    "engine",
    "market",
    "board",
    "symbol",
    "continuous_symbol",
    "timeframe",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "currency",
    "active_contract",
    "active_board",
    "source_contract_symbol",
    "roll_id",
    "roll_flag",
    "is_roll_boundary",
    "roll_date",
    "adjustment_mode",
    "adjustment_factor",
    "dataset_type",
)


class NearestContractBuildConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    data_root: Path
    output_root: Path
    exchange: str = "MOEX"
    engine: str = "futures"
    market: str = "forts"
    board: str = "RFUD"
    continuous_symbol: str = "CNYRUB_NEAREST_5M"
    timeframe: str = "5m"
    metadata_snapshot_ts: str | None = None
    query: str | None = None
    asset_code: str | None = "CNY"
    secid_prefixes: list[str] = Field(default_factory=list)
    include_secid: list[str] = Field(default_factory=list)
    exclude_secid: list[str] = Field(default_factory=list)
    roll_days_before_expiration: int = 3
    adjustment_mode: str = ADJUSTMENT_UNADJUSTED
    overwrite: bool = False
    reports_root: Path | None = None
    session_model: IntradaySessionModel | None = None

    @field_validator("data_root", "output_root", "reports_root", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        return Path(value)

    @field_validator("roll_days_before_expiration")
    @classmethod
    def _validate_roll_days_before_expiration(cls, value: int) -> int:
        if value < 0:
            raise ValueError("roll_days_before_expiration must be >= 0")
        return value

    @field_validator("adjustment_mode")
    @classmethod
    def _validate_adjustment_mode(cls, value: str) -> str:
        if value != ADJUSTMENT_UNADJUSTED:
            raise ValueError("nearest-contract intraday datasets support only unadjusted prices")
        return value


class NearestRollBoundary(BaseModel):
    roll_id: str
    old_contract: str
    new_contract: str
    roll_date: date
    effective_roll_ts: date | datetime
    old_expiration_date: date
    new_expiration_date: date
    old_last_ts: date | datetime | None = None
    new_first_ts: date | datetime | None = None
    gap_minutes_between_contracts: float | None = None
    old_close_near_roll: float | None = None
    new_close_near_roll: float | None = None
    price_gap: float | None = None
    price_gap_pct: float | None = None
    warning: str | None = None


class NearestContractQuality(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    continuous_symbol: str
    timeframe: str
    adjustment_mode: str
    dataset_type: str
    row_count: int
    min_ts: date | datetime | None = None
    max_ts: date | datetime | None = None
    contract_count: int
    active_contract_count: int
    roll_count: int
    duplicate_ts_count: int
    null_price_count: int
    null_active_contract_count: int
    max_gap_minutes: float | None = None
    large_gap_count: int
    total_timestamp_gaps: int = 0
    expected_session_break_count: int = 0
    expected_overnight_or_calendar_break_count: int = 0
    roll_transition_gap_count: int = 0
    unexpected_intraday_gap_count: int = 0
    unknown_gap_count: int = 0
    max_unexpected_intraday_gap_minutes: float | None = None
    top_unexpected_gaps: list[dict[str, Any]] = Field(default_factory=list)
    session_model: dict[str, Any] | None = None
    roll_boundaries: list[dict[str, Any]] = Field(default_factory=list)
    contracts_used: list[str] = Field(default_factory=list)
    contracts_skipped: list[str] = Field(default_factory=list)
    status: str
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class NearestContractBuildResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    output_path: Path
    reports_dir: Path
    build_summary_json: Path
    roll_calendar_csv: Path
    roll_calendar_parquet: Path
    session_gaps_csv: Path
    session_gaps_parquet: Path
    session_gap_summary_json: Path
    quality_json: Path
    contract_coverage_csv: Path
    contract_coverage_parquet: Path
    continuous_symbol: str
    timeframe: str
    metadata_snapshot_ts: str
    adjustment_mode: str
    dataset_type: str
    row_count: int
    min_ts: date | datetime | None = None
    max_ts: date | datetime | None = None
    contracts_used: list[str] = Field(default_factory=list)
    contracts_skipped: list[str] = Field(default_factory=list)
    roll_count: int
    quality_status: str
    warnings: list[str] = Field(default_factory=list)


def _norm(value: object) -> str:
    return str(value).strip().upper()


def _date_from_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise InputDataError(f"Unsupported date value: {value!r}")


def _datetime_like(value: object) -> date | datetime:
    if isinstance(value, datetime | date):
        return value
    if isinstance(value, str):
        text = value.replace("Z", "")
        if "T" in text or " " in text:
            return datetime.fromisoformat(text)
        return date.fromisoformat(text[:10])
    raise InputDataError(f"Unsupported timestamp value: {value!r}")


def _as_datetime(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)
