import polars as pl
import pytest

from continuous_futures_builder.data_access import LocalParquetDataAccess
from continuous_futures_builder.exceptions import InputDataError
from continuous_futures_builder.models import ContractCandleSummary


def test_summary_keeps_relaxed_part_schema_and_timestamp_bounds(temp_data_root):
    access = LocalParquetDataAccess(temp_data_root)
    path = access.candles_path("MOEX", "futures", "forts", "RFUD", "CRH4", "1d")
    file = path / "part-000.parquet"
    frame = pl.read_parquet(file)
    frame.head(2).with_columns(pl.lit("extra").alias("note")).write_parquet(file)
    frame.slice(2).write_parquet(path / "part-001.parquet")
    expected = ContractCandleSummary(
        secid="CRH4",
        board="RFUD",
        row_count=frame.height,
        min_ts=frame["ts"].min(),
        max_ts=frame["ts"].max(),
    )
    summaries = access.list_contract_candle_summaries(
        "MOEX", "futures", "forts", "1d", include_secid=["CRH4"]
    )
    assert summaries == [expected]
    assert (
        access.load_contract_candles("MOEX", "futures", "forts", "RFUD", "CRH4", "1d").height
        == frame.height
    )


def test_summary_still_rejects_missing_canonical_columns(temp_data_root):
    access = LocalParquetDataAccess(temp_data_root)
    path = access.candles_path("MOEX", "futures", "forts", "RFUD", "CRH4", "1d")
    file = path / "part-000.parquet"
    pl.read_parquet(file).drop("close").write_parquet(file)
    with pytest.raises(InputDataError, match="missing required columns: close"):
        access.list_contract_candle_summaries("MOEX", "futures", "forts", "1d")
