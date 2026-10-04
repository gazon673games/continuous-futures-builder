from __future__ import annotations

from continuous_futures_builder._nearest_contract_artifacts import (
    _load_candles,
    _load_chain,
    _save_artifacts,
)
from continuous_futures_builder._nearest_contract_models import (
    NEAREST_DATASET_TYPE,
    NEAREST_QUALITY_REJECTED,
    NearestContractBuildConfig,
    NearestContractBuildResult,
)
from continuous_futures_builder._nearest_contract_quality import (
    _build_quality,
    _coverage_frame,
)
from continuous_futures_builder._nearest_contract_rolls import (
    _timeframe_minutes,
)
from continuous_futures_builder._nearest_contract_series import (
    _build_roll_calendar,
    _stitch_nearest_contracts,
)
from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.exceptions import (
    ContinuousSeriesValidationError,
)
from continuous_futures_builder.nearest_gap_diagnostics import (
    diagnose_session_gaps,
)


def build_nearest_contract_dataset(
    config: NearestContractBuildConfig,
    *,
    builder: ContinuousFuturesBuilder | None = None,
) -> NearestContractBuildResult:
    effective_builder = builder or ContinuousFuturesBuilder(
        config.data_root,
        output_root=config.output_root,
    )
    snapshot_ts, _chain, contracts, chain_skipped, chain_warnings = _load_chain(
        effective_builder, config
    )
    candles_by_contract, loaded_contracts, candle_skipped = _load_candles(
        effective_builder,
        config,
        contracts,
    )
    boundaries, active_contracts, roll_warnings = _build_roll_calendar(
        loaded_contracts,
        candles_by_contract,
        config.roll_days_before_expiration,
    )
    frame = _stitch_nearest_contracts(config, active_contracts, boundaries, candles_by_contract)
    contracts_used = frame.get_column("active_contract").unique(maintain_order=True).to_list()
    used_contracts = set(contracts_used)
    inactive_contracts = [
        f"{contract.secid}: not active before available data ends"
        for contract in loaded_contracts
        if contract.secid not in used_contracts
    ]
    contracts_skipped = [*chain_skipped, *candle_skipped, *inactive_contracts]
    session_gap_rows, session_gap_summary = diagnose_session_gaps(
        frame,
        bar_minutes=_timeframe_minutes(config.timeframe),
        session_model=config.session_model,
    )
    quality = _build_quality(
        frame,
        config,
        boundaries,
        contracts_skipped,
        preexisting_warnings=[*chain_warnings, *roll_warnings],
        contract_count=len(loaded_contracts),
        session_gap_summary=session_gap_summary,
    )
    if quality.status == NEAREST_QUALITY_REJECTED:
        raise ContinuousSeriesValidationError("; ".join(quality.errors))

    coverage = _coverage_frame(loaded_contracts, frame, candles_by_contract, contracts_skipped)
    paths = _save_artifacts(
        frame,
        config,
        snapshot_ts,
        quality,
        boundaries,
        coverage,
        session_gap_rows,
        session_gap_summary,
    )

    return NearestContractBuildResult(
        output_path=paths["output_path"],
        reports_dir=paths["reports_dir"],
        build_summary_json=paths["build_summary_json"],
        roll_calendar_csv=paths["roll_calendar_csv"],
        roll_calendar_parquet=paths["roll_calendar_parquet"],
        session_gaps_csv=paths["session_gaps_csv"],
        session_gaps_parquet=paths["session_gaps_parquet"],
        session_gap_summary_json=paths["session_gap_summary_json"],
        quality_json=paths["quality_json"],
        contract_coverage_csv=paths["contract_coverage_csv"],
        contract_coverage_parquet=paths["contract_coverage_parquet"],
        continuous_symbol=config.continuous_symbol,
        timeframe=config.timeframe,
        metadata_snapshot_ts=snapshot_ts,
        adjustment_mode=config.adjustment_mode,
        dataset_type=NEAREST_DATASET_TYPE,
        row_count=quality.row_count,
        min_ts=quality.min_ts,
        max_ts=quality.max_ts,
        contracts_used=quality.contracts_used,
        contracts_skipped=contracts_skipped,
        roll_count=quality.roll_count,
        quality_status=quality.status,
        warnings=quality.warnings,
    )
