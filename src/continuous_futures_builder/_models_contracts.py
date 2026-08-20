from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class FuturesContract(BaseModel):
    secid: str
    board: str
    assetcode: str | None = None
    shortname: str | None = None
    secname: str | None = None
    expiration_date: date
    last_trade_date: date | None = None


class SkippedContract(BaseModel):
    secid: str | None = None
    board: str | None = None
    reason: str


class ContractCandleSummary(BaseModel):
    secid: str
    board: str
    row_count: int
    min_ts: date | datetime | None = None
    max_ts: date | datetime | None = None


class ContractChainResult(BaseModel):
    contracts: list[FuturesContract]
    skipped: list[SkippedContract] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def contract_ids(self) -> list[str]:
        return [contract.secid for contract in self.contracts]


class ContractFamilyTransition(BaseModel):
    from_contract: str
    to_contract: str
    from_prefix: str
    to_prefix: str
    transition_date: date | datetime
    roll_date: date
    old_close: float | None = None
    new_close: float | None = None
    roll_gap: float | None = None
    gap_percent: float | None = None
    warning: str | None = None


class PrunedContract(BaseModel):
    secid: str | None = None
    board: str | None = None
    category: str
    reason: str
    start: date | None = None
    end: date | None = None


class RollReportRow(BaseModel):
    roll_id: str | None = None
    roll_date: date | datetime | None = None
    from_contract: str
    to_contract: str
    from_prefix: str
    to_prefix: str
    is_family_transition: bool
    old_close: float | None = None
    new_close: float | None = None
    roll_gap: float | None = None
    roll_gap_pct: float | None = None
    adjustment_factor_before: float | None = None
    adjustment_factor_after: float | None = None
    rows_before_roll: int | None = None
    rows_after_roll: int | None = None
    warning: str | None = None


class RollReportSaveResult(BaseModel):
    output_dir: Path
    roll_report_parquet: Path
    roll_report_csv: Path
    quality_summary_json: Path


class SourceContractInvestigation(BaseModel):
    secid: str
    board: str | None = None
    row_count: int = 0
    min_ts: date | datetime | None = None
    max_ts: date | datetime | None = None
    close_used_ts: date | datetime | None = None
    close_used: float | None = None
    window_row_count: int = 0


class CandidateContractInvestigation(BaseModel):
    secid: str
    board: str | None = None
    metadata_found: bool = False
    candles_found: bool = False
    row_count: int | None = None
    min_ts: date | datetime | None = None
    max_ts: date | datetime | None = None
    expiration_date: date | datetime | None = None
    would_be_active_segment_start: date | datetime | None = None
    would_be_active_segment_end: date | datetime | None = None
    active_segment_row_count: int | None = None
    pruned: bool = False
    pruning_reason: str | None = None


class StructuralGapDiagnostics(BaseModel):
    triggered: bool
    roll_gap_pct_threshold: float
    calendar_days_threshold: int
    roll_gap_pct_exceeds_threshold: bool = False
    source_coverage_calendar_gap_days: int | None = None
    source_coverage_gap_exceeds_threshold: bool = False
    contract_year_gap: int | None = None
    contract_year_gap_detected: bool = False
    reasons: list[str] = Field(default_factory=list)


class RollInvestigationSaveResult(BaseModel):
    output_dir: Path
    investigation_summary_json: Path
    continuous_window_csv: Path
    from_contract_window_csv: Path
    to_contract_window_csv: Path
    candidate_contracts_csv: Path


class RollInvestigationSummary(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    data_root: Path
    roll_date: date
    from_contract: str
    to_contract: str
    roll_gap: float | None = None
    roll_gap_pct: float | None = None
    old_close: float | None = None
    new_close: float | None = None
    old_close_ts: date | datetime | None = None
    new_close_ts: date | datetime | None = None
    continuous_window_row_count: int
    from_contract_summary: SourceContractInvestigation
    to_contract_summary: SourceContractInvestigation
    candidate_contracts: list[CandidateContractInvestigation] = Field(default_factory=list)
    primary_classification: str
    secondary_classifications: list[str] = Field(default_factory=list)
    recommendation: str
    structural_gap: StructuralGapDiagnostics
    warnings: list[str] = Field(default_factory=list)
    save_result: RollInvestigationSaveResult | None = None


class RollEvent(BaseModel):
    roll_id: str
    from_contract: str
    to_contract: str
    roll_date: date
    from_expiration_date: date
    to_expiration_date: date
