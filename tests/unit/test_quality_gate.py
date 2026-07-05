from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.cli import app
from continuous_futures_builder.constants import (
    ADJUSTMENT_UNADJUSTED,
    DERIVED_SOURCE,
    OUTPUT_COLUMNS,
)
from continuous_futures_builder.quality_gate import (
    evaluate_quality_gate,
    load_quality_gate_policy,
    save_quality_gate_manifest,
)


def _build_fixture_output(temp_data_root: Path) -> Path:
    result = ContinuousFuturesBuilder(temp_data_root).build(
        metadata_snapshot_ts="20240101T000000Z",
        query="CR",
        adjustment_mode=ADJUSTMENT_UNADJUSTED,
        save=True,
        overwrite=True,
    )
    assert result.output_path is not None
    return result.output_path


def _continuous_row(
    *,
    ts: date,
    close: float,
    active_contract: str,
    roll_flag: bool = False,
    roll_date: date | None = None,
) -> dict[str, object]:
    return {
        "source": DERIVED_SOURCE,
        "exchange": "MOEX",
        "engine": "futures",
        "market": "forts",
        "continuous_symbol": "TEST_CONT",
        "timeframe": "1d",
        "ts": ts,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
        "value": close * 1000.0,
        "currency": "RUB",
        "active_contract": active_contract,
        "active_board": "RFUD",
        "source_contract_symbol": active_contract,
        "roll_id": f"roll-{active_contract}" if roll_flag else None,
        "roll_flag": roll_flag,
        "roll_date": roll_date,
        "adjustment_mode": ADJUSTMENT_UNADJUSTED,
        "adjustment_factor": 0.0,
    }


def _write_continuous(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).select(OUTPUT_COLUMNS).write_parquet(path)
    return path


def _split_by_source_gap_path(tmp_path: Path) -> Path:
    return _write_continuous(
        tmp_path / "source-gap.parquet",
        [
            _continuous_row(ts=date(2024, 1, 1), close=100.0, active_contract="ABH4"),
            _continuous_row(
                ts=date(2024, 3, 1),
                close=101.0,
                active_contract="ABM4",
                roll_flag=True,
                roll_date=date(2024, 3, 1),
            ),
        ],
    )


def _split_by_roll_gap_path(tmp_path: Path) -> Path:
    return _write_continuous(
        tmp_path / "roll-gap.parquet",
        [
            _continuous_row(ts=date(2024, 1, 1), close=100.0, active_contract="ABH4"),
            _continuous_row(
                ts=date(2024, 1, 2),
                close=150.0,
                active_contract="ABM4",
                roll_flag=True,
                roll_date=date(2024, 1, 2),
            ),
        ],
    )


def test_quality_gate_approves_fixture_dataset(temp_data_root):
    result = evaluate_quality_gate(_build_fixture_output(temp_data_root))

    assert result.status == "approved"
    assert result.approved_for_research is True
    assert result.reason_count == 0
    assert result.metrics.validation_status == "ok"


def test_quality_gate_rejects_duplicate_timestamps(temp_data_root, tmp_path):
    output_path = _build_fixture_output(temp_data_root)
    duplicate_path = tmp_path / "duplicate.parquet"
    frame = pl.read_parquet(output_path)
    frame.vstack(frame.head(1)).sort("ts").write_parquet(duplicate_path)

    result = evaluate_quality_gate(duplicate_path)

    assert result.status == "rejected"
    assert result.approved_for_research is False
    assert result.metrics.duplicate_ts_count == 1
    assert any("duplicate_ts_count" in reason for reason in result.reasons)


def test_quality_gate_rejects_null_prices(temp_data_root, tmp_path):
    output_path = _build_fixture_output(temp_data_root)
    null_price_path = tmp_path / "null-price.parquet"
    frame = (
        pl.read_parquet(output_path)
        .with_row_index("__row")
        .with_columns(
            pl.when(pl.col("__row") == 0).then(None).otherwise(pl.col("close")).alias("close")
        )
        .drop("__row")
    )
    frame.write_parquet(null_price_path)

    result = evaluate_quality_gate(null_price_path)

    assert result.status == "rejected"
    assert result.metrics.null_price_count == 1
    assert any("null_price_count" in reason for reason in result.reasons)


def test_quality_gate_requires_split_for_source_coverage_gap(tmp_path):
    result = evaluate_quality_gate(_split_by_source_gap_path(tmp_path))

    assert result.status == "split_required"
    assert result.metrics.max_source_coverage_gap_days == 60
    assert any("source_coverage_gap_days" in reason for reason in result.reasons)


def test_quality_gate_requires_split_for_roll_gap(tmp_path):
    result = evaluate_quality_gate(_split_by_roll_gap_path(tmp_path))

    assert result.status == "split_required"
    assert result.metrics.max_abs_roll_gap_pct == 50.0
    assert any("roll_gap_pct" in reason for reason in result.reasons)


def test_quality_gate_custom_policy_file_overrides_threshold(tmp_path):
    target = _split_by_roll_gap_path(tmp_path)
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "max_roll_gap_pct: 100.0",
                "max_source_coverage_gap_days: 30",
            ]
        ),
        encoding="utf-8",
    )

    result = evaluate_quality_gate(target, load_quality_gate_policy(policy_path))

    assert result.status == "approved"
    assert result.approved_for_research is True


def test_quality_gate_manifest_marks_approved_for_research(temp_data_root, tmp_path):
    approved = evaluate_quality_gate(_build_fixture_output(temp_data_root))
    split = evaluate_quality_gate(_split_by_roll_gap_path(tmp_path))
    manifest_path = save_quality_gate_manifest(
        [approved, split],
        tmp_path / "quality_gate_manifest.json",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert [dataset["status"] for dataset in manifest["datasets"]] == [
        "approved",
        "split_required",
    ]
    assert [dataset["approved_for_research"] for dataset in manifest["datasets"]] == [
        True,
        False,
    ]


def test_quality_gate_cli_exit_codes(temp_data_root, tmp_path):
    runner = CliRunner()
    approved = runner.invoke(
        app,
        ["quality-gate", "--path", str(_build_fixture_output(temp_data_root))],
    )
    split = runner.invoke(
        app,
        ["quality-gate", "--path", str(_split_by_roll_gap_path(tmp_path))],
    )

    assert approved.exit_code == 0, approved.output
    assert "status: approved" in approved.output
    assert split.exit_code == 1, split.output
    assert "status: split_required" in split.output


def test_quality_gate_cli_saves_manifest_for_paths(temp_data_root, tmp_path):
    approved_path = _build_fixture_output(temp_data_root)
    split_path = _split_by_roll_gap_path(tmp_path)
    policy_path = tmp_path / "policy.yaml"
    output_path = tmp_path / "manifest.json"
    policy_path.write_text("max_roll_gap_pct: 25.0\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "quality-gate",
            "--paths",
            str(approved_path),
            "--paths",
            str(split_path),
            "--policy",
            str(policy_path),
            "--save",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 1, result.output
    assert "manifest_path:" in result.output
    manifest = json.loads(output_path.read_text(encoding="utf-8"))
    assert [dataset["status"] for dataset in manifest["datasets"]] == [
        "approved",
        "split_required",
    ]
