from __future__ import annotations

from continuous_futures_builder.storage import output_path_for_mode


def test_output_path_building(tmp_path):
    path = output_path_for_mode(
        tmp_path,
        exchange="MOEX",
        engine="futures",
        market="forts",
        continuous_symbol="CNYRUB_CONT",
        timeframe="1d",
        adjustment_mode="back_adjusted",
    )

    assert path == (
        tmp_path
        / "derived"
        / "continuous_futures"
        / "exchange=MOEX"
        / "engine=futures"
        / "market=forts"
        / "continuous_symbol=CNYRUB_CONT"
        / "timeframe=1d"
        / "adjustment_mode=back_adjusted"
        / "part-000.parquet"
    )
