from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from continuous_futures_builder._quality_gate_core import (
    QualityGateManifest,
    QualityGatePolicy,
    QualityGateResult,
    QualityGateStatus,
)
from continuous_futures_builder._quality_gate_metrics import (
    build_quality_gate_metrics,
)
from continuous_futures_builder.quality import build_quality_report


def evaluate_quality_gate(
    path: str | Path,
    policy: QualityGatePolicy | None = None,
) -> QualityGateResult:
    target = Path(path)
    effective_policy = policy or QualityGatePolicy()
    metrics = build_quality_gate_metrics(target)
    quality_report = build_quality_report(target)

    rejected_reasons: list[str] = []
    split_reasons: list[str] = []

    if effective_policy.require_validation_status_ok and metrics.validation_status != "ok":
        rejected_reasons.append(
            f"validation_status {metrics.validation_status} != ok"
            + (f": {metrics.validation_error}" if metrics.validation_error else "")
        )

    if (metrics.duplicate_ts_count or 0) > effective_policy.max_duplicate_ts:
        rejected_reasons.append(
            f"duplicate_ts_count {metrics.duplicate_ts_count} > {effective_policy.max_duplicate_ts}"
        )
    if (metrics.null_price_count or 0) > effective_policy.max_null_price_count:
        rejected_reasons.append(
            f"null_price_count {metrics.null_price_count} > {effective_policy.max_null_price_count}"
        )
    if (metrics.null_active_contract_count or 0) > effective_policy.max_null_active_contract_count:
        rejected_reasons.append(
            f"null_active_contract_count {metrics.null_active_contract_count} > "
            f"{effective_policy.max_null_active_contract_count}"
        )

    if (
        metrics.max_source_coverage_gap_days is not None
        and metrics.max_source_coverage_gap_days > effective_policy.max_source_coverage_gap_days
    ):
        split_reasons.append(
            f"source_coverage_gap_days {metrics.max_source_coverage_gap_days} > "
            f"{effective_policy.max_source_coverage_gap_days}"
            + (
                " at "
                f"{metrics.max_source_coverage_gap_roll_date} "
                f"{' -> '.join(metrics.max_source_coverage_gap_contracts)}"
                if metrics.max_source_coverage_gap_contracts
                else ""
            )
        )

    if (
        metrics.max_abs_roll_gap_pct is not None
        and metrics.max_abs_roll_gap_pct > effective_policy.max_roll_gap_pct
    ):
        split_reasons.append(
            f"roll_gap_pct {metrics.max_abs_roll_gap_pct:.4f} > "
            f"{effective_policy.max_roll_gap_pct:.4f}"
            + (
                " at "
                f"{metrics.max_abs_roll_gap_pct_roll_date} "
                f"{' -> '.join(metrics.max_abs_roll_gap_pct_contracts)}"
                if metrics.max_abs_roll_gap_pct_contracts
                else ""
            )
        )

    if not effective_policy.allow_family_transitions and (metrics.family_transition_count or 0):
        rejected_reasons.append(f"family_transition_count {metrics.family_transition_count} > 0")
    if (
        metrics.max_family_transition_gap_pct is not None
        and metrics.max_family_transition_gap_pct > effective_policy.max_family_transition_gap_pct
    ):
        split_reasons.append(
            f"family_transition_gap_pct {metrics.max_family_transition_gap_pct:.4f} > "
            f"{effective_policy.max_family_transition_gap_pct:.4f}"
        )

    if (
        not effective_policy.allow_empty_active_segments
        and metrics.empty_active_segments is not None
        and metrics.empty_active_segments > 0
    ):
        rejected_reasons.append(f"empty_active_segments {metrics.empty_active_segments} > 0")
    if (
        effective_policy.max_contracts_pruned is not None
        and metrics.contracts_pruned is not None
        and metrics.contracts_pruned > effective_policy.max_contracts_pruned
    ):
        rejected_reasons.append(
            f"contracts_pruned {metrics.contracts_pruned} > {effective_policy.max_contracts_pruned}"
        )

    reasons = [*rejected_reasons, *split_reasons]
    if rejected_reasons:
        status: QualityGateStatus = "rejected"
    elif split_reasons:
        status = "split_required"
    else:
        status = "approved"

    return QualityGateResult(
        dataset=quality_report.continuous_symbol,
        path=target,
        status=status,
        reason_count=len(reasons),
        reasons=reasons,
        metrics=metrics,
        approved_for_research=status == "approved",
    )


def save_quality_gate_manifest(
    results: list[QualityGateResult],
    output: str | Path,
    policy: QualityGatePolicy | None = None,
) -> Path:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = QualityGateManifest(
        generated_at=datetime.now(UTC),
        policy=policy or QualityGatePolicy(),
        datasets=results,
    )
    target.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return target
