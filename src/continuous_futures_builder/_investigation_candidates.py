from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder._investigation_sources import (
    _candidate_sort_key,
    _contract_year,
    _expected_quarter_contracts,
)
from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.constants import ADJUSTMENT_UNADJUSTED
from continuous_futures_builder.exceptions import ContinuousFuturesError
from continuous_futures_builder.models import (
    CandidateContractInvestigation,
    ContractCandleSummary,
)
from continuous_futures_builder.prefixes import (
    matches_any_secid_prefix,
)


def _build_pruning_diagnostics(
    *,
    data_root: Path,
    exchange: str,
    engine: str,
    market: str,
    continuous_symbol: str,
    timeframe: str,
    metadata_snapshot_ts: str,
    query: str | None,
    secid_prefixes: list[str],
    roll_days_before: int,
    warnings: list[str],
) -> tuple[dict[str, str], set[str]]:
    try:
        result = ContinuousFuturesBuilder(data_root).build(
            exchange=exchange,
            engine=engine,
            market=market,
            continuous_symbol=continuous_symbol,
            timeframe=timeframe,
            metadata_snapshot_ts=metadata_snapshot_ts,
            query=query,
            secid_prefixes=secid_prefixes,
            roll_days_before=roll_days_before,
            adjustment_mode=ADJUSTMENT_UNADJUSTED,
            save=False,
        )
    except ContinuousFuturesError as exc:
        warnings.append(f"failed to rebuild pruning diagnostics: {exc}")
        return {}, set()

    pruning = {
        secid.upper(): reason
        for secid, reason in zip(result.pruned_contracts, result.pruning_reasons, strict=False)
    }
    return pruning, {secid.upper() for secid in result.empty_active_segments}


def _parse_segment_bound(reason: str, key: str) -> date | None:
    match = re.search(rf"{key}=([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}|None)", reason)
    if match is None or match.group(1) == "None":
        return None
    return date.fromisoformat(match.group(1))


def _active_segments(frame: pl.DataFrame) -> dict[str, dict[str, Any]]:
    if "active_contract" not in frame.columns:
        return {}
    rows = (
        frame.group_by("active_contract", maintain_order=True)
        .agg(
            pl.len().alias("row_count"),
            pl.col("ts").min().alias("min_ts"),
            pl.col("ts").max().alias("max_ts"),
        )
        .iter_rows(named=True)
    )
    return {str(row["active_contract"]).upper(): row for row in rows}


def _candidate_contract_ids(
    *,
    prefix: str,
    anchor_year: int,
    from_contract: str,
    to_contract: str,
    metadata: dict[str, dict[str, Any]],
    summaries: list[ContractCandleSummary],
    pruning: dict[str, str],
) -> list[str]:
    from_year = _contract_year(from_contract, anchor_year) or anchor_year
    to_year = _contract_year(to_contract, anchor_year) or anchor_year
    start_year = min(from_year, to_year, anchor_year) - 1
    end_year = max(from_year, to_year, anchor_year + 2)

    secids = _expected_quarter_contracts(prefix, start_year, end_year)
    secids.update({from_contract.upper(), to_contract.upper()})

    for secid in metadata:
        if not matches_any_secid_prefix(secid, [prefix]):
            continue
        year = _contract_year(secid, anchor_year)
        if year is not None and start_year <= year <= end_year:
            secids.add(secid)

    for summary in summaries:
        secid = summary.secid.upper()
        if not matches_any_secid_prefix(secid, [prefix]):
            continue
        year = _contract_year(secid, anchor_year)
        if year is not None and start_year <= year <= end_year:
            secids.add(secid)

    for secid in pruning:
        if matches_any_secid_prefix(secid, [prefix]):
            secids.add(secid)

    return sorted(secids, key=lambda secid: _candidate_sort_key(secid, anchor_year))


def _candidate_frame(candidates: list[CandidateContractInvestigation]) -> pl.DataFrame:
    if candidates:
        return pl.DataFrame([candidate.model_dump(mode="json") for candidate in candidates])
    return pl.DataFrame(
        schema={
            "secid": pl.Utf8,
            "board": pl.Utf8,
            "metadata_found": pl.Boolean,
            "candles_found": pl.Boolean,
            "row_count": pl.Int64,
            "min_ts": pl.Utf8,
            "max_ts": pl.Utf8,
            "expiration_date": pl.Utf8,
            "would_be_active_segment_start": pl.Utf8,
            "would_be_active_segment_end": pl.Utf8,
            "active_segment_row_count": pl.Int64,
            "pruned": pl.Boolean,
            "pruning_reason": pl.Utf8,
        }
    )


def _segment_row_count(candles: pl.DataFrame, start: date | None, end: date | None) -> int | None:
    if candles.height == 0 or "ts" not in candles.columns:
        return None
    frame = candles.with_columns(pl.col("ts").cast(pl.Date).alias("__ts_date"))
    if start is not None:
        frame = frame.filter(pl.col("__ts_date") >= start)
    if end is not None:
        frame = frame.filter(pl.col("__ts_date") < end)
    return frame.height
