from __future__ import annotations

from typing import Any

import polars as pl

from continuous_futures_builder._investigation_candidates import (
    _parse_segment_bound,
    _segment_row_count,
)
from continuous_futures_builder._investigation_core import (
    _as_date,
    _empty_candle_frame,
)
from continuous_futures_builder._investigation_sources import (
    _candidate_sort_key,
    _contract_year,
)
from continuous_futures_builder.models import (
    CandidateContractInvestigation,
    ContractCandleSummary,
    SourceContractInvestigation,
    StructuralGapDiagnostics,
)


def _candidate_summaries(
    *,
    secids: list[str],
    prefix: str,
    anchor_year: int,
    metadata: dict[str, dict[str, Any]],
    summaries: list[ContractCandleSummary],
    pruning: dict[str, str],
    empty_active_segments: set[str],
    active_segments: dict[str, dict[str, Any]],
    candles_by_secid: dict[str, pl.DataFrame],
) -> list[CandidateContractInvestigation]:
    candidates: list[CandidateContractInvestigation] = []
    summary_by_secid = {summary.secid.upper(): summary for summary in summaries}

    for secid in secids:
        metadata_row = metadata.get(secid)
        candle_summary = summary_by_secid.get(secid)
        active_segment = active_segments.get(secid)
        pruning_reason = pruning.get(secid)
        metadata_found = metadata_row is not None
        candles_found = candle_summary is not None and candle_summary.row_count > 0
        board = None
        expiration_date = None
        if metadata_row is not None:
            board = (
                str(metadata_row.get("board")) if metadata_row.get("board") is not None else None
            )
            expiration_date = metadata_row.get("expiration_date") or metadata_row.get(
                "last_trade_date"
            )
        if board is None and candle_summary is not None:
            board = candle_summary.board
        if expiration_date is None and candle_summary is not None:
            expiration_date = candle_summary.max_ts

        active_start = active_segment.get("min_ts") if active_segment is not None else None
        active_end = active_segment.get("max_ts") if active_segment is not None else None
        active_row_count = int(active_segment["row_count"]) if active_segment is not None else None
        if pruning_reason and "active segment is empty" in pruning_reason:
            active_start = _parse_segment_bound(pruning_reason, "start")
            active_end = _parse_segment_bound(pruning_reason, "end")
            active_row_count = _segment_row_count(
                candles_by_secid.get(secid, _empty_candle_frame()),
                _as_date(active_start),
                _as_date(active_end),
            )
            if active_row_count is None:
                active_row_count = 0

        pruned = pruning_reason is not None
        if not metadata_found and not candles_found:
            pruned = True
            pruning_reason = (
                "not present in metadata snapshot or candle catalog; generated expected "
                f"{prefix} quarterly candidate around {anchor_year}"
            )
        elif not metadata_found and candles_found and pruning_reason is None:
            pruning_reason = "metadata missing; candle partition exists"
        elif metadata_found and not candles_found and pruning_reason is None:
            pruned = True
            pruning_reason = "metadata exists but no candle partition was found"

        if secid in empty_active_segments:
            pruned = True

        candidates.append(
            CandidateContractInvestigation(
                secid=secid,
                board=board,
                metadata_found=metadata_found,
                candles_found=candles_found,
                row_count=candle_summary.row_count if candle_summary is not None else None,
                min_ts=candle_summary.min_ts if candle_summary is not None else None,
                max_ts=candle_summary.max_ts if candle_summary is not None else None,
                expiration_date=expiration_date,
                would_be_active_segment_start=active_start,
                would_be_active_segment_end=active_end,
                active_segment_row_count=active_row_count,
                pruned=pruned,
                pruning_reason=pruning_reason,
            )
        )

    return sorted(
        candidates,
        key=lambda candidate: _candidate_sort_key(candidate.secid, anchor_year),
    )


def _calendar_gap_days(
    from_summary: SourceContractInvestigation,
    to_summary: SourceContractInvestigation,
) -> int | None:
    from_max = _as_date(from_summary.max_ts)
    to_min = _as_date(to_summary.min_ts)
    if from_max is None or to_min is None:
        return None
    return max(0, (to_min - from_max).days)


def detect_structural_gap(
    *,
    roll_gap_pct: float | None,
    from_contract: str,
    to_contract: str,
    anchor_year: int,
    from_summary: SourceContractInvestigation,
    to_summary: SourceContractInvestigation,
    roll_gap_pct_threshold: float = 25.0,
    calendar_days_threshold: int = 90,
) -> StructuralGapDiagnostics:
    reasons: list[str] = []
    roll_gap_exceeded = roll_gap_pct is not None and abs(roll_gap_pct) >= roll_gap_pct_threshold
    if roll_gap_exceeded:
        reasons.append(
            f"roll_gap_pct {roll_gap_pct:.2f}% exceeds threshold {roll_gap_pct_threshold:.2f}%"
        )

    coverage_gap_days = _calendar_gap_days(from_summary, to_summary)
    coverage_gap_exceeded = (
        coverage_gap_days is not None and coverage_gap_days >= calendar_days_threshold
    )
    if coverage_gap_exceeded:
        reasons.append(
            f"source coverage calendar gap {coverage_gap_days} days exceeds threshold "
            f"{calendar_days_threshold} days"
        )

    from_year = _contract_year(from_contract, anchor_year)
    to_year = _contract_year(to_contract, anchor_year)
    contract_year_gap = None
    contract_year_gap_detected = False
    if from_year is not None and to_year is not None:
        contract_year_gap = to_year - from_year
        contract_year_gap_detected = contract_year_gap > 1
        if contract_year_gap_detected:
            reasons.append(
                f"contract year jumps from {from_year} to {to_year} ({contract_year_gap} years)"
            )

    return StructuralGapDiagnostics(
        triggered=bool(reasons),
        roll_gap_pct_threshold=roll_gap_pct_threshold,
        calendar_days_threshold=calendar_days_threshold,
        roll_gap_pct_exceeds_threshold=roll_gap_exceeded,
        source_coverage_calendar_gap_days=coverage_gap_days,
        source_coverage_gap_exceeds_threshold=coverage_gap_exceeded,
        contract_year_gap=contract_year_gap,
        contract_year_gap_detected=contract_year_gap_detected,
        reasons=reasons,
    )
