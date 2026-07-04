from __future__ import annotations

import socket

import polars as pl
import pytest

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.constants import OUTPUT_COLUMNS
from continuous_futures_builder.exceptions import InputDataError
from continuous_futures_builder.storage import ContinuousFuturesStorage


def test_continuous_output_schema_and_traceability(temp_data_root):
    builder = ContinuousFuturesBuilder(temp_data_root)

    result = builder.build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )

    assert result.output_path is not None
    frame = pl.read_parquet(result.output_path)
    assert frame.columns == list(OUTPUT_COLUMNS)
    assert frame["active_contract"].null_count() == 0
    assert frame["source_contract_symbol"].null_count() == 0
    assert frame.get_column("adjustment_mode").unique().to_list() == ["unadjusted"]


def test_adjusted_and_unadjusted_outputs_are_separate(temp_data_root):
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

    assert unadjusted.output_path is not None
    assert adjusted.output_path is not None
    assert unadjusted.output_path != adjusted.output_path
    assert unadjusted.output_path.exists()
    assert adjusted.output_path.exists()


def test_adapter_normalized_paths_are_not_overwritten(temp_data_root):
    candle_path = (
        temp_data_root
        / "normalized"
        / "candles"
        / "exchange=MOEX"
        / "engine=futures"
        / "market=forts"
        / "board=RFUD"
        / "symbol=CRH4"
        / "timeframe=1d"
        / "part-000.parquet"
    )
    before = candle_path.read_bytes()
    builder = ContinuousFuturesBuilder(temp_data_root)

    builder.build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="back_adjusted",
        save=True,
        overwrite=True,
    )

    assert candle_path.read_bytes() == before


def test_storage_refuses_adapter_candle_path(temp_data_root):
    builder = ContinuousFuturesBuilder(temp_data_root)
    builder.build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="unadjusted",
        save=True,
        overwrite=True,
    )
    valid_path = (
        temp_data_root
        / "derived"
        / "continuous_futures"
        / "exchange=MOEX"
        / "engine=futures"
        / "market=forts"
        / "continuous_symbol=CNYRUB_CONT"
        / "timeframe=1d"
        / "adjustment_mode=unadjusted"
        / "part-000.parquet"
    )
    frame = pl.read_parquet(valid_path)
    bad_path = temp_data_root / "normalized" / "candles" / "exchange=MOEX" / "part-000.parquet"

    with pytest.raises(InputDataError, match="adapter path"):
        ContinuousFuturesStorage(temp_data_root).write(frame, bad_path, overwrite=True)


def test_no_network_access_required(temp_data_root, monkeypatch):
    def fail_socket(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", fail_socket)

    result = ContinuousFuturesBuilder(temp_data_root).build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode="back_adjusted",
    )

    assert result.row_count > 0
