from __future__ import annotations

from typer.testing import CliRunner

from continuous_futures_builder.cli import app


def test_cli_chain_build_and_validate(temp_data_root):
    runner = CliRunner()

    snapshots = runner.invoke(
        app,
        [
            "snapshots",
            "--data-root",
            str(temp_data_root),
            "--exchange",
            "MOEX",
            "--engine",
            "futures",
            "--market",
            "forts",
        ],
    )
    assert snapshots.exit_code == 0, snapshots.output
    assert "20240101T000000Z" in snapshots.output

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
    assert "assetcode: CNYRUB" in chain.output
    assert "row_count: 5" in chain.output

    output_root = temp_data_root.parent / "derived-output"
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
            "--output-root",
            str(output_root),
        ],
    )
    assert build.exit_code == 0, build.output
    assert "row_count: 14" in build.output
    assert "roll_count: 3" in build.output

    output_path = (
        output_root
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

    quality = runner.invoke(app, ["quality", "--path", str(output_path)])

    assert quality.exit_code == 0, quality.output
    assert "status: ok" in quality.output
    assert "contract_count: 4" in quality.output
    assert "roll_count: 3" in quality.output
    assert "max_abs_roll_gap_roll_date:" in quality.output

    roll_report = runner.invoke(app, ["roll-report", "--path", str(output_path), "--top", "2"])

    assert roll_report.exit_code == 0, roll_report.output
    assert "roll_date | from_contract | to_contract" in roll_report.output
    assert "CRH4 | CRM4" in roll_report.output

    investigation = runner.invoke(
        app,
        [
            "investigate-roll",
            "--data-root",
            str(temp_data_root),
            "--path",
            str(output_path),
            "--roll-date",
            "2024-03-12",
            "--from-contract",
            "CRH4",
            "--to-contract",
            "CRM4",
            "--metadata-snapshot-ts",
            "20240101T000000Z",
            "--query",
            "CR",
            "--window-rows",
            "2",
            "--structural-gap-roll-pct-threshold",
            "1",
        ],
    )

    assert investigation.exit_code == 0, investigation.output
    assert "primary_classification:" in investigation.output
    assert "continuous_window:" in investigation.output
    assert "candidate_contracts:" in investigation.output


def test_cli_accepts_repeated_secid_prefix(multi_prefix_data_root):
    runner = CliRunner()

    chain = runner.invoke(
        app,
        [
            "chain",
            "--data-root",
            str(multi_prefix_data_root),
            "--exchange",
            "MOEX",
            "--engine",
            "futures",
            "--market",
            "forts",
            "--metadata-snapshot-ts",
            "20240101T000000Z",
            "--query",
            "CNY",
            "--secid-prefix",
            "CY",
            "--secid-prefix",
            "CR",
        ],
    )

    assert chain.exit_code == 0, chain.output
    assert "contracts_used: CYH3, CYM3, CRH4, CRM4, CRU4, CRZ4" in chain.output
    assert "MYH3" not in chain.output
    assert "CYM3 -> CRH4 (CY -> CR)" in chain.output
