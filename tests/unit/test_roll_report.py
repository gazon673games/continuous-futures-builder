from __future__ import annotations

from datetime import date

import polars as pl

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.roll_report import (
    build_roll_report,
    filter_roll_report,
    save_roll_diagnostics,
)


def test_roll_report_extraction_and_top_gap_sorting(temp_data_root):
    result = ContinuousFuturesBuilder(temp_data_root).build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )

    assert result.output_path is not None
    rows = build_roll_report(result.output_path)
    top_rows = filter_roll_report(rows, top=1)

    assert len(rows) == 3
    assert rows[0].roll_date == date(2024, 3, 12)
    assert rows[0].from_contract == "CRH4"
    assert rows[0].to_contract == "CRM4"
    assert rows[0].roll_gap == 5.0
    assert top_rows[0].roll_date == date(2024, 6, 12)
    assert top_rows[0].roll_gap == 9.0


def test_roll_report_detects_family_transition(multi_prefix_data_root):
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
    rows = build_roll_report(result.output_path)
    family_rows = filter_roll_report(rows, family_transitions_only=True)

    assert len(family_rows) == 1
    assert family_rows[0].from_contract == "CYM3"
    assert family_rows[0].to_contract == "CRH4"
    assert family_rows[0].is_family_transition
    assert family_rows[0].roll_gap == 4.0


def test_save_roll_diagnostics_writes_artifacts(temp_data_root, tmp_path, monkeypatch):
    result = ContinuousFuturesBuilder(temp_data_root).build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )

    assert result.output_path is not None
    reads = []
    read_parquet = pl.read_parquet

    def record_read(path, **kwargs):
        reads.append(path)
        return read_parquet(path, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", record_read)
    save_result = save_roll_diagnostics(result.output_path, tmp_path / "reports")

    assert save_result.roll_report_csv.exists()
    assert save_result.roll_report_parquet.exists()
    assert save_result.quality_summary_json.exists()
    assert reads == [result.output_path]
    assert pl.read_parquet(save_result.roll_report_parquet).height == 3
