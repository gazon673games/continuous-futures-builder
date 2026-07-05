from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from continuous_futures_builder.contract_chain import build_contract_chain
from continuous_futures_builder.contract_filters import filter_contracts
from continuous_futures_builder.data_access import LocalParquetDataAccess
from continuous_futures_builder.exceptions import ContractChainError
from continuous_futures_builder.prefixes import infer_contract_prefix


def _metadata(rows: list[dict]) -> pl.DataFrame:
    defaults = {
        "source": "fixture",
        "exchange": "MOEX",
        "engine": "futures",
        "market": "forts",
        "board": "RFUD",
        "shortname": "CNY/RUB",
        "secname": "CNY/RUB futures",
        "assetcode": "CNYRUB",
        "instrument_type": "futures",
        "currency": "RUB",
        "lot_size": 1000,
        "raw_metadata": "{}",
        "snapshot_ts": "TEST",
    }
    return pl.DataFrame([{**defaults, **row} for row in rows])


def test_contract_filtering_by_query(fixture_root):
    metadata = LocalParquetDataAccess(fixture_root).load_metadata_snapshot(
        "MOEX", "futures", "forts", "20240101T000000Z"
    )

    contracts, skipped = filter_contracts(metadata, query="CR")

    assert [contract.secid for contract in contracts] == ["CRH4", "CRM4", "CRU4", "CRZ4"]
    assert any(skip.secid == "BADCR" for skip in skipped)


def test_contract_filtering_by_asset_code_and_prefix(fixture_root):
    metadata = LocalParquetDataAccess(fixture_root).load_metadata_snapshot(
        "MOEX", "futures", "forts", "20240101T000000Z"
    )

    by_asset, _ = filter_contracts(metadata, query=None, asset_code="CNYRUB")
    by_prefix, _ = filter_contracts(metadata, query=None, secid_prefix="CR")

    assert [contract.secid for contract in by_asset] == ["CRH4", "CRM4", "CRU4", "CRZ4"]
    assert [contract.secid for contract in by_prefix] == ["CRH4", "CRM4", "CRU4", "CRZ4"]


def test_contract_filtering_by_multiple_prefixes_excludes_non_matches(multi_prefix_data_root):
    metadata = LocalParquetDataAccess(multi_prefix_data_root).load_metadata_snapshot(
        "MOEX", "futures", "forts", "20240101T000000Z"
    )

    contracts, _ = filter_contracts(
        metadata,
        query="CNY",
        secid_prefixes=["CY", "CR"],
    )

    assert [contract.secid for contract in contracts] == [
        "CYH3",
        "CYM3",
        "CRH4",
        "CRM4",
        "CRU4",
        "CRZ4",
    ]
    assert "MYH3" not in {contract.secid for contract in contracts}


def test_contract_prefix_inference_handles_historical_suffixes():
    assert infer_contract_prefix("CYM5_2015") == "CY"
    assert infer_contract_prefix("CRM2") == "CR"


def test_include_and_exclude_secid(fixture_root):
    metadata = LocalParquetDataAccess(fixture_root).load_metadata_snapshot(
        "MOEX", "futures", "forts", "20240101T000000Z"
    )

    contracts, skipped = filter_contracts(
        metadata,
        query="NO_MATCH",
        include_secid=["CRH4", "CRM4"],
        exclude_secid=["CRM4"],
    )

    assert [contract.secid for contract in contracts] == ["CRH4"]
    assert any(skip.secid == "CRM4" and "excluded" in skip.reason for skip in skipped)


def test_missing_expiration_metadata_is_skipped():
    metadata = _metadata(
        [
            {"secid": "CRH4", "expiration_date": date(2024, 3, 15), "last_trade_date": None},
            {"secid": "BADCR", "expiration_date": None, "last_trade_date": None},
        ]
    )

    contracts, skipped = filter_contracts(metadata, query="CR")

    assert [contract.secid for contract in contracts] == ["CRH4"]
    assert skipped[0].reason == "missing both expiration_date and last_trade_date"


def test_chain_sorting_and_duplicate_removal():
    metadata = _metadata(
        [
            {"secid": "CRU4", "expiration_date": date(2024, 9, 16), "last_trade_date": None},
            {"secid": "CRH4", "expiration_date": date(2024, 3, 15), "last_trade_date": None},
            {"secid": "CRM4", "expiration_date": date(2024, 6, 17), "last_trade_date": None},
            {"secid": "CRM4", "expiration_date": date(2024, 6, 17), "last_trade_date": None},
        ]
    )

    result = build_contract_chain(metadata, query="CR")

    assert result.contract_ids == ["CRH4", "CRM4", "CRU4"]
    assert any("duplicate" in warning for warning in result.warnings)


def test_chain_requires_at_least_two_contracts():
    metadata = _metadata(
        [{"secid": "CRH4", "expiration_date": date(2024, 3, 15), "last_trade_date": None}]
    )

    with pytest.raises(ContractChainError):
        build_contract_chain(metadata, query="CR")
