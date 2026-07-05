from __future__ import annotations

import polars as pl

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.quality import build_quality_report


def test_quality_report_counts_contracts_and_rolls(temp_data_root):
    result = ContinuousFuturesBuilder(temp_data_root).build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )

    assert result.output_path is not None
    report = build_quality_report(result.output_path)

    assert report.status == "ok"
    assert report.contracts_used == ["CRH4", "CRM4", "CRU4", "CRZ4"]
    assert report.contract_count == 4
    assert report.roll_count == 3
    assert report.max_abs_roll_gap is not None
    assert report.max_abs_roll_gap_roll_date == report.roll_dates[1]
    assert report.max_abs_roll_gap_contracts == ["CRM4", "CRU4"]


def test_quality_report_includes_family_transitions(multi_prefix_data_root):
    result = ContinuousFuturesBuilder(multi_prefix_data_root).build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CNY",
        secid_prefixes=["CY", "CR"],
        continuous_symbol="CNYRUB_CONT_LONG",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )

    assert result.output_path is not None
    report = build_quality_report(result.output_path)

    assert report.status == "ok"
    assert report.prefixes_used == ["CY", "CR"]
    assert report.families_used == ["CY", "CR"]
    assert report.family_transition_count == 1
    assert report.max_abs_roll_gap_roll_date is not None
    assert report.family_transitions[0].from_contract == "CYM3"
    assert report.family_transitions[0].to_contract == "CRH4"
    assert report.max_abs_family_transition_gap == 4.0


def test_quality_report_detects_duplicate_ts(temp_data_root, tmp_path):
    result = ContinuousFuturesBuilder(temp_data_root).build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )
    assert result.output_path is not None
    frame = pl.read_parquet(result.output_path)
    duplicate_path = tmp_path / "duplicate.parquet"
    frame.vstack(frame.head(1)).sort("ts").write_parquet(duplicate_path)

    report = build_quality_report(duplicate_path)

    assert report.status == "failed"
    assert report.duplicate_ts_count == 1
    assert any("duplicate ts" in warning for warning in report.warnings)


def test_quality_report_detects_null_active_contract(temp_data_root, tmp_path):
    result = ContinuousFuturesBuilder(temp_data_root).build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )
    assert result.output_path is not None
    frame = (
        pl.read_parquet(result.output_path)
        .with_row_index("__row")
        .with_columns(
            pl.when(pl.col("__row") == 0)
            .then(None)
            .otherwise(pl.col("active_contract"))
            .alias("active_contract")
        )
        .drop("__row")
    )
    null_path = tmp_path / "null-active.parquet"
    frame.write_parquet(null_path)

    report = build_quality_report(null_path)

    assert report.status == "failed"
    assert report.null_active_contract_count == 1
    assert any("null active_contract" in warning for warning in report.warnings)
