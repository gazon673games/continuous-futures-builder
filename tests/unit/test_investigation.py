from __future__ import annotations

import json
import shutil
from datetime import date

import polars as pl

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.investigation import (
    classify_roll_outlier,
    detect_structural_gap,
    investigate_roll,
    save_roll_investigation,
)
from continuous_futures_builder.models import (
    CandidateContractInvestigation,
    SourceContractInvestigation,
    StructuralGapDiagnostics,
)


def _metadata_path(root):
    return (
        root
        / "normalized"
        / "metadata"
        / "exchange=MOEX"
        / "engine=futures"
        / "market=forts"
        / "securities"
        / "snapshot_ts=20240101T000000Z"
        / "part-000.parquet"
    )


def _candle_path(root, secid):
    return (
        root
        / "normalized"
        / "candles"
        / "exchange=MOEX"
        / "engine=futures"
        / "market=forts"
        / "board=RFUD"
        / f"symbol={secid}"
        / "timeframe=1d"
        / "part-000.parquet"
    )


def _build_fixture_output(root):
    result = ContinuousFuturesBuilder(root).build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )
    assert result.output_path is not None
    return result.output_path


def test_investigate_roll_extracts_windows_and_source_summaries(temp_data_root):
    output_path = _build_fixture_output(temp_data_root)

    investigation = investigate_roll(
        data_root=temp_data_root,
        path=output_path,
        roll_date="2024-03-12",
        from_contract="CRH4",
        to_contract="CRM4",
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        window_rows=2,
    )

    assert investigation.continuous_window.height == 5
    assert investigation.summary.continuous_window_row_count == 5
    assert investigation.summary.old_close == 101.0
    assert investigation.summary.new_close == 106.0
    assert set(investigation.continuous_window["active_contract"]) >= {"CRH4", "CRM4"}
    assert investigation.summary.from_contract_summary.row_count == 5
    assert investigation.summary.from_contract_summary.close_used_ts == date(2024, 3, 11)
    assert investigation.summary.to_contract_summary.row_count == 7
    assert investigation.summary.to_contract_summary.close_used_ts == date(2024, 3, 12)

    candidates = {row.secid: row for row in investigation.summary.candidate_contracts}
    assert candidates["CRH4"].metadata_found
    assert candidates["CRH4"].candles_found
    assert candidates["CRM4"].metadata_found
    assert candidates["CRM4"].active_segment_row_count == 4


def test_candidate_summary_includes_pruned_contracts(temp_data_root):
    metadata_path = _metadata_path(temp_data_root)
    metadata = pl.read_parquet(metadata_path)
    duplicate = metadata.filter(pl.col("secid") == "CRM4").with_columns(
        pl.lit("CRM4A").alias("secid")
    )
    pl.concat([metadata, duplicate], how="vertical").write_parquet(metadata_path)

    source = _candle_path(temp_data_root, "CRM4").parent
    target = source.parent.parent / "symbol=CRM4A" / "timeframe=1d"
    shutil.copytree(source, target)
    for copied in target.glob("*.parquet"):
        pl.read_parquet(copied).with_columns(pl.lit("CRM4A").alias("symbol")).write_parquet(copied)
    output_path = _build_fixture_output(temp_data_root)

    investigation = investigate_roll(
        data_root=temp_data_root,
        path=output_path,
        roll_date="2024-03-12",
        from_contract="CRH4",
        to_contract="CRM4",
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        window_rows=2,
    )

    duplicate_candidate = {row.secid: row for row in investigation.summary.candidate_contracts}[
        "CRM4A"
    ]
    assert duplicate_candidate.pruned
    assert duplicate_candidate.metadata_found
    assert duplicate_candidate.candles_found
    assert "duplicate expiration_date" in (duplicate_candidate.pruning_reason or "")


def test_classification_detects_empty_active_segments():
    primary, secondary, recommendation = classify_roll_outlier(
        roll_gap_pct=3.0,
        candidates=[
            CandidateContractInvestigation(
                secid="CYM1",
                metadata_found=True,
                candles_found=True,
                row_count=12,
                active_segment_row_count=0,
                pruned=True,
                pruning_reason=(
                    "CYM1: skipped from rollable chain because active segment is empty "
                    "(start=2020-02-06, end=2021-04-26)"
                ),
            )
        ],
        structural_gap=StructuralGapDiagnostics(
            triggered=False,
            roll_gap_pct_threshold=25.0,
            calendar_days_threshold=90,
        ),
    )

    assert primary == "empty_active_segment"
    assert secondary == []
    assert recommendation.startswith("B.")


def test_structural_gap_detection_handles_large_roll_gap_and_calendar_gap():
    roll_gap_only = detect_structural_gap(
        roll_gap_pct=114.0,
        from_contract="CYH0",
        to_contract="CYM2",
        anchor_year=2020,
        from_summary=SourceContractInvestigation(secid="CYH0"),
        to_summary=SourceContractInvestigation(secid="CYM2"),
        roll_gap_pct_threshold=25.0,
        calendar_days_threshold=90,
    )
    assert roll_gap_only.triggered
    assert roll_gap_only.roll_gap_pct_exceeds_threshold
    assert roll_gap_only.contract_year_gap_detected

    calendar_gap = detect_structural_gap(
        roll_gap_pct=1.0,
        from_contract="CRH4",
        to_contract="CRM4",
        anchor_year=2024,
        from_summary=SourceContractInvestigation(
            secid="CRH4",
            max_ts=date(2024, 3, 1),
        ),
        to_summary=SourceContractInvestigation(
            secid="CRM4",
            min_ts=date(2024, 7, 1),
        ),
        roll_gap_pct_threshold=25.0,
        calendar_days_threshold=90,
    )
    assert calendar_gap.triggered
    assert calendar_gap.source_coverage_gap_exceeds_threshold


def test_large_roll_gap_threshold_does_not_alter_input_parquet(temp_data_root):
    output_path = _build_fixture_output(temp_data_root)
    before = pl.read_parquet(output_path)

    investigation = investigate_roll(
        data_root=temp_data_root,
        path=output_path,
        roll_date="2024-03-12",
        from_contract="CRH4",
        to_contract="CRM4",
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        window_rows=2,
        structural_gap_roll_pct_threshold=1.0,
    )
    after = pl.read_parquet(output_path)

    assert investigation.summary.structural_gap.triggered
    assert investigation.summary.structural_gap.roll_gap_pct_exceeds_threshold
    assert before.equals(after)


def test_save_roll_investigation_writes_artifacts(temp_data_root, tmp_path):
    output_path = _build_fixture_output(temp_data_root)
    investigation = investigate_roll(
        data_root=temp_data_root,
        path=output_path,
        roll_date="2024-03-12",
        from_contract="CRH4",
        to_contract="CRM4",
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        window_rows=2,
    )

    save_result = save_roll_investigation(investigation, tmp_path / "investigation")

    assert save_result.investigation_summary_json.exists()
    assert save_result.continuous_window_csv.exists()
    assert save_result.from_contract_window_csv.exists()
    assert save_result.to_contract_window_csv.exists()
    assert save_result.candidate_contracts_csv.exists()
    saved_summary = json.loads(save_result.investigation_summary_json.read_text())
    assert saved_summary["from_contract"] == "CRH4"
    assert pl.read_csv(save_result.continuous_window_csv).height == 5
