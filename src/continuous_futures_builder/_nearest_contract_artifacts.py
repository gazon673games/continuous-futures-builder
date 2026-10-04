from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder._gap_diagnostics_core import SessionGapRow, SessionGapSummary
from continuous_futures_builder._gap_diagnostics_output import _write_session_gap_reports
from continuous_futures_builder._nearest_contract_models import (
    NearestContractBuildConfig,
    NearestContractBuildResult,
    NearestContractQuality,
    NearestRollBoundary,
)
from continuous_futures_builder._nearest_contract_quality import (
    _dataset_output_path,
    _reports_dir,
    _write_json,
    _write_output,
)
from continuous_futures_builder._nearest_contract_rolls import (
    _latest_snapshot,
    _matches_asset_code,
    _raise_for_missing_expiration_metadata,
    _roll_calendar_frame,
)
from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.exceptions import (
    ContractChainError,
    InputDataError,
)
from continuous_futures_builder.models import ContractChainResult, FuturesContract
from continuous_futures_builder.prefixes import normalize_secid_prefixes


def _save_artifacts(
    frame: pl.DataFrame,
    config: NearestContractBuildConfig,
    metadata_snapshot_ts: str,
    quality: NearestContractQuality,
    boundaries: list[NearestRollBoundary],
    coverage: pl.DataFrame,
    session_gap_rows: list[SessionGapRow],
    session_gap_summary: SessionGapSummary,
) -> dict[str, Path]:
    output_path = _dataset_output_path(config)
    reports_dir = _reports_dir(config)
    reports_dir.mkdir(parents=True, exist_ok=True)

    _write_output(frame, output_path, output_root=config.output_root, overwrite=config.overwrite)

    roll_calendar = _roll_calendar_frame(boundaries)
    session_gap_result = _write_session_gap_reports(
        session_gap_rows, session_gap_summary, reports_dir
    )
    roll_calendar_csv = reports_dir / "roll_calendar.csv"
    roll_calendar_parquet = reports_dir / "roll_calendar.parquet"
    quality_json = reports_dir / "quality.json"
    contract_coverage_csv = reports_dir / "contract_coverage.csv"
    contract_coverage_parquet = reports_dir / "contract_coverage.parquet"
    build_summary_json = reports_dir / "build_summary.json"

    roll_calendar.write_csv(roll_calendar_csv)
    roll_calendar.write_parquet(roll_calendar_parquet)
    coverage.write_csv(contract_coverage_csv)
    coverage.write_parquet(contract_coverage_parquet)
    quality_payload = quality.model_dump(mode="json")
    _write_json(quality_json, quality_payload)

    summary_payload = {
        "output_path": str(output_path),
        "reports_dir": str(reports_dir),
        "metadata_snapshot_ts": metadata_snapshot_ts,
        **quality_payload,
    }
    _write_json(build_summary_json, summary_payload)

    return {
        "output_path": output_path,
        "reports_dir": reports_dir,
        "build_summary_json": build_summary_json,
        "roll_calendar_csv": roll_calendar_csv,
        "roll_calendar_parquet": roll_calendar_parquet,
        "session_gaps_csv": session_gap_result.session_gaps_csv,
        "session_gaps_parquet": session_gap_result.session_gaps_parquet,
        "session_gap_summary_json": session_gap_result.session_gap_summary_json,
        "quality_json": quality_json,
        "contract_coverage_csv": contract_coverage_csv,
        "contract_coverage_parquet": contract_coverage_parquet,
    }


class NearestContractFuturesBuilder:
    def __init__(self, data_root: str | Path, *, output_root: str | Path = "data") -> None:
        self.data_root = Path(data_root)
        self.output_root = Path(output_root)
        self.builder = ContinuousFuturesBuilder(self.data_root, output_root=self.output_root)

    def build(self, **kwargs: Any) -> NearestContractBuildResult:
        from continuous_futures_builder._nearest_contract_build import (
            build_nearest_contract_dataset,
        )

        config = NearestContractBuildConfig(
            data_root=self.data_root,
            output_root=self.output_root,
            **kwargs,
        )
        return build_nearest_contract_dataset(config, builder=self.builder)


def _load_chain(
    builder: ContinuousFuturesBuilder,
    config: NearestContractBuildConfig,
) -> tuple[str, ContractChainResult, list[FuturesContract], list[str], list[str]]:
    snapshot_ts = config.metadata_snapshot_ts or _latest_snapshot(builder, config)
    prefixes = normalize_secid_prefixes(secid_prefixes=config.secid_prefixes)
    chain = builder.build_chain(
        exchange=config.exchange,
        engine=config.engine,
        market=config.market,
        metadata_snapshot_ts=snapshot_ts,
        query=config.query,
        asset_code=None,
        secid_prefixes=prefixes,
        include_secid=config.include_secid,
        exclude_secid=config.exclude_secid,
        timeframe=config.timeframe,
    )
    _raise_for_missing_expiration_metadata(chain)

    warnings = list(chain.warnings)
    skipped: list[str] = [
        f"{item.secid or '<unknown>'}: {item.reason}"
        for item in chain.skipped
        if "missing both expiration_date and last_trade_date" not in item.reason
        and "invalid expiration metadata" not in item.reason
    ]
    selected = [
        contract
        for contract in chain.contracts
        if contract.board == config.board and _matches_asset_code(contract, config.asset_code)
    ]
    for contract in chain.contracts:
        if contract not in selected:
            skipped.append(
                f"{contract.secid}: skipped by board/asset filter "
                f"(board={contract.board}, assetcode={contract.assetcode})"
            )

    selected = sorted(
        selected,
        key=lambda contract: (contract.expiration_date, contract.secid, contract.board),
    )
    if len(selected) < 2:
        raise ContractChainError(
            "Nearest-contract chain must contain at least two eligible contracts. "
            f"Found {len(selected)}."
        )
    return snapshot_ts, chain, selected, skipped, warnings


def _load_candles(
    builder: ContinuousFuturesBuilder,
    config: NearestContractBuildConfig,
    contracts: list[FuturesContract],
) -> tuple[dict[str, pl.DataFrame], list[FuturesContract], list[str]]:
    candles_by_contract: dict[str, pl.DataFrame] = {}
    loaded_contracts: list[FuturesContract] = []
    skipped: list[str] = []
    for contract in contracts:
        try:
            frame = builder.data_access.load_contract_candles(
                exchange=config.exchange,
                engine=config.engine,
                market=config.market,
                board=contract.board,
                symbol=contract.secid,
                timeframe=config.timeframe,
            )
        except InputDataError as exc:
            skipped.append(f"{contract.secid}: missing candles ({exc})")
            continue
        if frame.height == 0:
            skipped.append(f"{contract.secid}: zero candle rows")
            continue
        candles_by_contract[contract.secid] = frame.sort("ts")
        loaded_contracts.append(contract)

    if len(loaded_contracts) < 2:
        raise InputDataError(
            "Nearest-contract build needs at least two contracts with candle data. "
            f"Found {len(loaded_contracts)}."
        )
    return candles_by_contract, loaded_contracts, skipped
