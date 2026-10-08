from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from continuous_futures_builder._models_contracts import (
    ContractFamilyTransition,
    PrunedContract,
    RollEvent,
)
from continuous_futures_builder.constants import (
    ADJUSTMENT_UNADJUSTED,
    DEFAULT_CONTINUOUS_SYMBOL,
    DEFAULT_ENGINE,
    DEFAULT_EXCHANGE,
    DEFAULT_MARKET,
    DEFAULT_ROLL_DAYS_BEFORE,
    DEFAULT_ROLL_RULE,
    DEFAULT_TIMEFRAME,
    SUPPORTED_ADJUSTMENT_MODES,
    SUPPORTED_ROLL_RULES,
)


class RollCalendarResult(BaseModel):
    events: list[RollEvent]
    warnings: list[str] = Field(default_factory=list)


class ContinuousFuturesBuildConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    data_root: Path
    output_root: Path | None = None
    exchange: str = DEFAULT_EXCHANGE
    engine: str = DEFAULT_ENGINE
    market: str = DEFAULT_MARKET
    continuous_symbol: str = DEFAULT_CONTINUOUS_SYMBOL
    timeframe: str = DEFAULT_TIMEFRAME
    metadata_snapshot_ts: str
    query: str | None = "CNY"
    asset_code: str | None = None
    secid_prefix: str | None = None
    secid_prefixes: list[str] = Field(default_factory=list)
    include_secid: list[str] = Field(default_factory=list)
    exclude_secid: list[str] = Field(default_factory=list)
    roll_rule: str = DEFAULT_ROLL_RULE
    roll_days_before: int = DEFAULT_ROLL_DAYS_BEFORE
    adjustment_mode: str = ADJUSTMENT_UNADJUSTED

    @field_validator("data_root", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | Path) -> Path:
        return Path(value)

    @field_validator("output_root", mode="before")
    @classmethod
    def _coerce_optional_path(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        return Path(value)

    @field_validator("roll_rule")
    @classmethod
    def _validate_roll_rule(cls, value: str) -> str:
        if value not in SUPPORTED_ROLL_RULES:
            supported = ", ".join(sorted(SUPPORTED_ROLL_RULES))
            msg = f"Unsupported roll_rule={value!r}; supported values: {supported}"
            raise ValueError(msg)
        return value

    @field_validator("roll_days_before")
    @classmethod
    def _validate_roll_days_before(cls, value: int) -> int:
        if value < 1:
            raise ValueError("roll_days_before must be >= 1")
        return value

    @field_validator("adjustment_mode")
    @classmethod
    def _validate_adjustment_mode(cls, value: str) -> str:
        if value not in SUPPORTED_ADJUSTMENT_MODES:
            supported = ", ".join(sorted(SUPPORTED_ADJUSTMENT_MODES))
            msg = f"Unsupported adjustment_mode={value!r}; supported values: {supported}"
            raise ValueError(msg)
        return value


class ContinuousFuturesBuildResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    output_path: Path | None = None
    row_count: int
    start_ts: date | datetime | None = None
    end_ts: date | datetime | None = None
    contracts_in_chain: int = 0
    contracts_used: list[str]
    contracts_pruned: int = 0
    pruned_contracts: list[str] = Field(default_factory=list)
    pruning_reasons: list[str] = Field(default_factory=list)
    pruning_details: list[PrunedContract] = Field(default_factory=list)
    empty_active_segments: list[str] = Field(default_factory=list)
    roll_count: int
    prefixes_used: list[str] = Field(default_factory=list)
    families_used: list[str] = Field(default_factory=list)
    family_transition_count: int = 0
    family_transitions: list[ContractFamilyTransition] = Field(default_factory=list)
    max_abs_family_transition_gap: float | None = None
    warnings: list[str] = Field(default_factory=list)


class ContinuousFuturesQualityReport(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    continuous_symbol: str | None = None
    adjustment_mode: str | None
    row_count: int
    min_ts: date | datetime | None = None
    max_ts: date | datetime | None = None
    contracts_used: list[str] = Field(default_factory=list)
    contract_count: int
    prefixes_used: list[str] = Field(default_factory=list)
    families_used: list[str] = Field(default_factory=list)
    roll_count: int
    roll_dates: list[date | datetime] = Field(default_factory=list)
    family_transition_count: int = 0
    family_transitions: list[ContractFamilyTransition] = Field(default_factory=list)
    gaps_count: int
    duplicate_ts_count: int
    null_active_contract_count: int
    null_price_count: int
    max_abs_roll_gap: float | None = None
    max_abs_roll_gap_roll_date: date | datetime | None = None
    max_abs_roll_gap_contracts: list[str] = Field(default_factory=list)
    max_abs_family_transition_gap: float | None = None
    adjustment_factor_min: float | None = None
    adjustment_factor_max: float | None = None
    status: str
    warnings: list[str] = Field(default_factory=list)
