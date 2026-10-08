from __future__ import annotations

from continuous_futures_builder.models import (
    CandidateContractInvestigation,
    StructuralGapDiagnostics,
)


def classify_roll_outlier(
    *,
    roll_gap_pct: float | None,
    candidates: list[CandidateContractInvestigation],
    structural_gap: StructuralGapDiagnostics,
) -> tuple[str, list[str], str]:
    has_absent_contracts = any(
        candidate.pruned and not candidate.metadata_found and not candidate.candles_found
        for candidate in candidates
    )
    has_metadata_missing = any(
        not candidate.metadata_found and candidate.candles_found for candidate in candidates
    )
    has_sparse_contracts = any(
        candidate.pruning_category == "sparse_contract"
        or (candidate.metadata_found and not candidate.candles_found)
        for candidate in candidates
    )
    has_empty_segments = any(
        candidate.pruning_category == "empty_active_segment" for candidate in candidates
    ) or any(candidate.active_segment_row_count == 0 for candidate in candidates)
    large_gap = (
        roll_gap_pct is not None and abs(roll_gap_pct) >= structural_gap.roll_gap_pct_threshold
    )

    secondary: list[str] = []
    if has_sparse_contracts:
        secondary.append("sparse_contracts")
    if has_empty_segments:
        secondary.append("empty_active_segment")
    if has_metadata_missing:
        secondary.append("missing_metadata")
    if has_absent_contracts:
        secondary.append("data_gap")
    if large_gap and not secondary:
        secondary.append("liquidity_gap")

    if structural_gap.triggered and (has_absent_contracts or has_sparse_contracts):
        primary = "data_gap"
    elif has_empty_segments and not has_sparse_contracts:
        primary = "empty_active_segment"
    elif has_sparse_contracts:
        primary = "sparse_contracts"
    elif has_metadata_missing:
        primary = "missing_metadata"
    elif large_gap:
        primary = "liquidity_gap"
    else:
        primary = "unknown"

    secondary = [classification for classification in secondary if classification != primary]
    recommendation = _recommend_policy(primary, secondary, structural_gap)
    return primary, secondary, recommendation


def _recommend_policy(
    primary_classification: str,
    secondary_classifications: list[str],
    structural_gap: StructuralGapDiagnostics,
) -> str:
    if primary_classification == "data_gap" and structural_gap.triggered:
        return (
            "C. Split into separate research series: CNYRUB_CONT_OLD for the older "
            "history and CNYRUB_CONT for the post-gap history."
        )
    if structural_gap.triggered:
        return "B. Keep it, but mark a structural gap around the affected transition."
    if primary_classification in {"sparse_contracts", "empty_active_segment", "liquidity_gap"}:
        return "B. Keep it, but mark a structural gap around the affected transition."
    if primary_classification == "unknown" or "missing_metadata" in secondary_classifications:
        return "E. Need more source data from moex_iss_adapter before deciding."
    return "A. Keep CNYRUB_CONT_LONG as one continuous series."
