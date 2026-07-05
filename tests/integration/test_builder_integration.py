from __future__ import annotations

from datetime import date

import polars as pl

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.storage import ContinuousFuturesStorage


def test_build_unadjusted_and_back_adjusted_from_fixtures(temp_data_root):
    builder = ContinuousFuturesBuilder(temp_data_root)

    unadjusted = builder.build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )
    adjusted = builder.build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="back_adjusted",
        save=True,
        overwrite=True,
    )

    assert unadjusted.row_count == 14
    assert adjusted.row_count == 14
    assert unadjusted.roll_count == 3
    assert adjusted.roll_count == 3
    assert unadjusted.contracts_used == ["CRH4", "CRM4", "CRU4", "CRZ4"]
    assert adjusted.contracts_used == ["CRH4", "CRM4", "CRU4", "CRZ4"]
    assert unadjusted.output_path is not None
    assert adjusted.output_path is not None
    ContinuousFuturesStorage(temp_data_root).validate_path(unadjusted.output_path)
    ContinuousFuturesStorage(temp_data_root).validate_path(adjusted.output_path)

    adjusted_frame = pl.read_parquet(adjusted.output_path)
    closes = {
        row["ts"]: row["close"]
        for row in adjusted_frame.select("ts", "close").iter_rows(named=True)
    }
    assert closes[date(2024, 3, 11)] == closes[date(2024, 3, 12)]


def test_builder_can_write_to_separate_output_root(temp_data_root, tmp_path):
    output_root = tmp_path / "derived-output"
    builder = ContinuousFuturesBuilder(temp_data_root, output_root=output_root)

    result = builder.build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )

    assert result.output_path is not None
    assert output_root in result.output_path.parents
    assert result.output_path.exists()
    ContinuousFuturesStorage(output_root).validate_path(result.output_path)


def test_builder_chain_preview_from_fixture(temp_data_root):
    chain = ContinuousFuturesBuilder(temp_data_root).build_chain(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
    )

    assert chain.contract_ids == ["CRH4", "CRM4", "CRU4", "CRZ4"]
    assert any("BADCR" in warning for warning in chain.warnings)


def test_builder_builds_multi_prefix_chain_and_family_diagnostics(multi_prefix_data_root):
    builder = ContinuousFuturesBuilder(multi_prefix_data_root)

    unadjusted = builder.build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CNY",
        secid_prefixes=["CY", "CR"],
        continuous_symbol="CNYRUB_CONT_LONG",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )
    adjusted = builder.build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CNY",
        secid_prefixes=["CY", "CR"],
        continuous_symbol="CNYRUB_CONT_LONG",
        adjustment_mode="back_adjusted",
        save=True,
        overwrite=True,
    )

    assert unadjusted.contracts_used == ["CYH3", "CYM3", "CRH4", "CRM4", "CRU4", "CRZ4"]
    assert adjusted.contracts_used == unadjusted.contracts_used
    assert unadjusted.prefixes_used == ["CY", "CR"]
    assert unadjusted.family_transition_count == 1
    transition = unadjusted.family_transitions[0]
    assert transition.from_contract == "CYM3"
    assert transition.to_contract == "CRH4"
    assert transition.from_prefix == "CY"
    assert transition.to_prefix == "CR"
    assert transition.roll_gap == 4.0
    assert unadjusted.max_abs_family_transition_gap == 4.0
    assert adjusted.output_path is not None
    ContinuousFuturesStorage(multi_prefix_data_root).validate_path(adjusted.output_path)


def test_builder_augments_chain_from_existing_candle_partitions(temp_data_root):
    metadata_path = (
        temp_data_root
        / "normalized"
        / "metadata"
        / "exchange=MOEX"
        / "engine=futures"
        / "market=forts"
        / "securities"
        / "snapshot_ts=20240101T000000Z"
        / "part-000.parquet"
    )
    metadata = pl.read_parquet(metadata_path).filter(pl.col("secid").is_in(["CRU4", "CRZ4"]))
    metadata.write_parquet(metadata_path)

    chain = ContinuousFuturesBuilder(temp_data_root).build_chain(
        metadata_snapshot_ts="20240101T000000Z",
        query="CNY",
        secid_prefix="CR",
    )

    assert chain.contract_ids == ["CRH4", "CRM4", "CRU4", "CRZ4"]
    assert any("augmented 2 contract(s)" in warning for warning in chain.warnings)
