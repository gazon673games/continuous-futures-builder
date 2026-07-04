from __future__ import annotations

from typer.testing import CliRunner

from continuous_futures_builder.cli import app


def test_cli_chain_build_and_validate(temp_data_root):
    runner = CliRunner()

    chain = runner.invoke(
        app,
        [
            "chain",
            "--data-root",
            str(temp_data_root),
            "--exchange",
            "MOEX",
            "--engine",
            "futures",
            "--market",
            "forts",
            "--metadata-snapshot-ts",
            "20240101T000000Z",
            "--query",
            "CR",
        ],
    )
    assert chain.exit_code == 0, chain.output
    assert "contracts_used: CRH4, CRM4, CRU4, CRZ4" in chain.output

    build = runner.invoke(
        app,
        [
            "build",
            "--data-root",
            str(temp_data_root),
            "--exchange",
            "MOEX",
            "--engine",
            "futures",
            "--market",
            "forts",
            "--continuous-symbol",
            "CNYRUB_CONT",
            "--timeframe",
            "1d",
            "--metadata-snapshot-ts",
            "20240101T000000Z",
            "--query",
            "CR",
            "--roll-rule",
            "fixed_days_before_expiration",
            "--roll-days-before",
            "3",
            "--adjustment-mode",
            "back_adjusted",
            "--save",
            "--overwrite",
        ],
    )
    assert build.exit_code == 0, build.output
    assert "row_count: 14" in build.output
    assert "roll_count: 3" in build.output

    output_path = (
        temp_data_root
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
    validate = runner.invoke(app, ["validate", "--path", str(output_path)])

    assert validate.exit_code == 0, validate.output
    assert "valid:" in validate.output
    assert "row_count: 14" in validate.output
