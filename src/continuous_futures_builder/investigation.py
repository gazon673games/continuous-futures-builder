from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from continuous_futures_builder.builder import ContinuousFuturesBuilder
from continuous_futures_builder.constants import ADJUSTMENT_UNADJUSTED
from continuous_futures_builder.data_access import ContinuousFuturesDataAccess, create_data_access
from continuous_futures_builder.exceptions import ContinuousFuturesError, InputDataError
from continuous_futures_builder.models import (
    CandidateContractInvestigation,
    ContractCandleSummary,
    RollInvestigationSaveResult,
    RollInvestigationSummary,
    RollReportRow,
    SourceContractInvestigation,
    StructuralGapDiagnostics,
)
from continuous_futures_builder.prefixes import (
    FUTURES_MONTH_CODES,
    infer_contract_prefix,
    matches_any_secid_prefix,
    normalize_secid_prefixes,
)
from continuous_futures_builder.roll_report import build_roll_report

CONTINUOUS_WINDOW_COLUMNS = (
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "active_contract",
    "source_contract_symbol",
    "roll_id",
    "roll_flag",
    "roll_date",
    "adjustment_mode",
    "adjustment_factor",
)
CANDLE_WINDOW_COLUMNS = ("ts", "open", "high", "low", "close", "volume", "value")
CANDIDATE_COLUMNS = (
    "secid",
    "metadata_found",
    "candles_found",
    "row_count",
    "min_ts",
    "max_ts",
    "would_be_active_segment_start",
    "would_be_active_segment_end",
    "active_segment_row_count",
    "pruned",
    "pruning_reason",
)
QUARTER_MONTH_CODES = ("H", "M", "U", "Z")


@dataclass
class RollInvestigation:
    summary: RollInvestigationSummary
    continuous_window: pl.DataFrame
    from_contract_window: pl.DataFrame
    to_contract_window: pl.DataFrame
    candidate_contracts_frame: pl.DataFrame


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _single_value(frame: pl.DataFrame, column: str, default: str) -> str:
    if column not in frame.columns:
        return default
    values = frame.get_column(column).drop_nulls().unique().to_list()
    if not values:
        return default
    if len(values) == 1:
        return str(values[0])
    return default


def _available_columns(frame: pl.DataFrame, columns: tuple[str, ...]) -> list[str]:
    return [column for column in columns if column in frame.columns]


def _find_transition(
    frame: pl.DataFrame,
    *,
    roll_date: date,
    from_contract: str,
    to_contract: str,
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    indexed = frame.sort("ts").with_row_index("__row_nr")
    previous: dict[str, Any] | None = None
    for row in indexed.iter_rows(named=True):
        if previous is None:
            previous = row
            continue

        row_roll_date = _as_date(row.get("roll_date")) or _as_date(row.get("ts"))
        row_ts = _as_date(row.get("ts"))
        if (
            str(previous.get("active_contract")) == from_contract
            and str(row.get("active_contract")) == to_contract
            and (row_roll_date == roll_date or row_ts == roll_date)
        ):
            return int(row["__row_nr"]), previous, row
        previous = row

    msg = (
        f"Could not find transition {from_contract} -> {to_contract} "
        f"on roll_date={roll_date} in continuous output"
    )
    raise InputDataError(msg)


def _matching_roll_report_row(
    path: str | Path,
    *,
    roll_date: date,
    from_contract: str,
    to_contract: str,
) -> RollReportRow | None:
    for row in build_roll_report(path):
        if (
            _as_date(row.roll_date) == roll_date
            and row.from_contract == from_contract
            and row.to_contract == to_contract
        ):
            return row
    return None


def _continuous_window(frame: pl.DataFrame, roll_index: int, window_rows: int) -> pl.DataFrame:
    sorted_frame = frame.sort("ts")
    start = max(0, roll_index - window_rows)
    length = min(sorted_frame.height - start, window_rows * 2 + 1)
    return sorted_frame.slice(start, length).select(
        _available_columns(sorted_frame, CONTINUOUS_WINDOW_COLUMNS)
    )


def _board_from_continuous(frame: pl.DataFrame, secid: str) -> str | None:
    if "active_board" not in frame.columns or "active_contract" not in frame.columns:
        return None
    values = (
        frame.filter(pl.col("active_contract") == secid)
        .get_column("active_board")
        .drop_nulls()
        .unique()
        .to_list()
    )
    return str(values[0]) if values else None


def _empty_candle_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "ts": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "value": pl.Float64,
        }
    )


def _summary_for_secid(
    summaries: list[ContractCandleSummary],
    secid: str,
) -> ContractCandleSummary | None:
    matches = [summary for summary in summaries if summary.secid.upper() == secid.upper()]
    if not matches:
        return None
    return max(matches, key=lambda summary: summary.row_count)


def _load_contract_candles(
    data_access: ContinuousFuturesDataAccess,
    *,
    exchange: str,
    engine: str,
    market: str,
    timeframe: str,
    secid: str,
    board: str | None,
    summaries: list[ContractCandleSummary],
    warnings: list[str],
) -> tuple[pl.DataFrame, str | None]:
    summary = _summary_for_secid(summaries, secid)
    effective_board = board or (summary.board if summary is not None else None)
    if effective_board is None:
        warnings.append(f"{secid}: no board found in continuous output or candle catalog")
        return _empty_candle_frame(), None

    try:
        return (
            data_access.load_contract_candles(
                exchange=exchange,
                engine=engine,
                market=market,
                board=effective_board,
                symbol=secid,
                timeframe=timeframe,
            ),
            effective_board,
        )
    except ContinuousFuturesError as exc:
        warnings.append(f"{secid}: failed to load source candles: {exc}")
        return _empty_candle_frame(), effective_board


def _source_window(
    candles: pl.DataFrame,
    *,
    roll_date: date,
    side: str,
    window_rows: int,
) -> pl.DataFrame:
    if candles.height == 0 or "ts" not in candles.columns:
        return _empty_candle_frame()
    frame = candles.sort("ts").with_columns(pl.col("ts").cast(pl.Date).alias("__ts_date"))
    if side == "from":
        window = frame.filter(pl.col("__ts_date") <= roll_date).tail(window_rows)
    else:
        window = frame.filter(pl.col("__ts_date") >= roll_date).head(window_rows)
    return window.drop("__ts_date").select(_available_columns(window, CANDLE_WINDOW_COLUMNS))


def _source_summary(
    *,
    secid: str,
    board: str | None,
    candles: pl.DataFrame,
    close_used_ts: object,
    close_used: float | None,
    window: pl.DataFrame,
) -> SourceContractInvestigation:
    return SourceContractInvestigation(
        secid=secid,
        board=board,
        row_count=candles.height,
        min_ts=candles.get_column("ts").min()
        if candles.height and "ts" in candles.columns
        else None,
        max_ts=candles.get_column("ts").max()
        if candles.height and "ts" in candles.columns
        else None,
        close_used_ts=_as_date(close_used_ts),
        close_used=close_used,
        window_row_count=window.height,
    )


def _metadata_by_secid(
    data_access: ContinuousFuturesDataAccess,
    *,
    exchange: str,
    engine: str,
    market: str,
    metadata_snapshot_ts: str,
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    try:
        metadata = data_access.load_metadata_snapshot(
            exchange=exchange,
            engine=engine,
            market=market,
            snapshot_ts=metadata_snapshot_ts,
        )
    except ContinuousFuturesError as exc:
        warnings.append(f"failed to load metadata snapshot {metadata_snapshot_ts}: {exc}")
        return {}
    return {str(row["secid"]).upper(): row for row in metadata.iter_rows(named=True)}


def _contract_year(secid: str, anchor_year: int) -> int | None:
    secid_norm = secid.strip().upper()
    suffix_match = re.search(r"_(\d{4})$", secid_norm)
    if suffix_match:
        return int(suffix_match.group(1))

    match = re.fullmatch(rf"[A-Z]+?[{FUTURES_MONTH_CODES}](\d+)[A-Z]?", secid_norm)
    if not match:
        return None

    year_code = match.group(1)
    if len(year_code) >= 4:
        return int(year_code[-4:])
    if len(year_code) != 1:
        return None

    digit = int(year_code)
    decade = (anchor_year // 10) * 10
    candidates = [decade - 10 + digit, decade + digit, decade + 10 + digit]
    return min(candidates, key=lambda candidate: abs(candidate - anchor_year))


def _month_order(secid: str) -> int:
    secid_norm = secid.strip().upper()
    match = re.fullmatch(rf"[A-Z]+?([{FUTURES_MONTH_CODES}])\d+[A-Z]?(?:_[A-Z0-9]+)?", secid_norm)
    if not match:
        return 99
    code = match.group(1)
    if code in QUARTER_MONTH_CODES:
        return QUARTER_MONTH_CODES.index(code)
    return FUTURES_MONTH_CODES.index(code)


def _candidate_sort_key(secid: str, anchor_year: int) -> tuple[int, int, str]:
    year = _contract_year(secid, anchor_year)
    return (year if year is not None else 9999, _month_order(secid), secid)


def _expected_quarter_contracts(prefix: str, start_year: int, end_year: int) -> set[str]:
    return {
        f"{prefix}{month_code}{year % 10}"
        for year in range(start_year, end_year + 1)
        for month_code in QUARTER_MONTH_CODES
    }


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


def classify_roll_outlier(
    *,
    roll_gap_pct: float | None,
    candidates: list[CandidateContractInvestigation],
    structural_gap: StructuralGapDiagnostics,
) -> tuple[str, list[str], str]:
    has_absent_contracts = any(
        candidate.pruned
        and candidate.pruning_reason is not None
        and "not present in metadata snapshot or candle catalog" in candidate.pruning_reason
        for candidate in candidates
    )
    has_metadata_missing = any(
        not candidate.metadata_found and candidate.candles_found for candidate in candidates
    )
    has_sparse_contracts = any(
        candidate.pruning_reason is not None
        and (
            "no candle exists before expiration" in candidate.pruning_reason
            or "no candle partition" in candidate.pruning_reason
        )
        for candidate in candidates
    )
    has_empty_segments = any(
        candidate.pruning_reason is not None
        and "active segment is empty" in candidate.pruning_reason
        for candidate in candidates
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


def investigate_roll(
    *,
    data_root: str | Path,
    path: str | Path,
    roll_date: str | date,
    from_contract: str,
    to_contract: str,
    metadata_snapshot_ts: str,
    exchange: str | None = None,
    engine: str | None = None,
    market: str | None = None,
    timeframe: str | None = None,
    query: str | None = "CNY",
    secid_prefix: list[str] | None = None,
    roll_days_before: int = 3,
    window_rows: int = 20,
    structural_gap_roll_pct_threshold: float = 25.0,
    structural_gap_calendar_days_threshold: int = 90,
) -> RollInvestigation:
    target_path = Path(path)
    target_data_root = Path(data_root)
    target_roll_date = _as_date(roll_date)
    if target_roll_date is None:
        raise InputDataError("roll_date is required")

    continuous = pl.read_parquet(target_path).sort("ts")
    target_exchange = exchange or _single_value(continuous, "exchange", "MOEX")
    target_engine = engine or _single_value(continuous, "engine", "futures")
    target_market = market or _single_value(continuous, "market", "forts")
    target_timeframe = timeframe or _single_value(continuous, "timeframe", "1d")
    continuous_symbol = _single_value(continuous, "continuous_symbol", "CNYRUB_CONT_LONG")

    roll_index, previous_row, roll_row = _find_transition(
        continuous,
        roll_date=target_roll_date,
        from_contract=from_contract,
        to_contract=to_contract,
    )
    roll_report_row = _matching_roll_report_row(
        target_path,
        roll_date=target_roll_date,
        from_contract=from_contract,
        to_contract=to_contract,
    )
    continuous_window = _continuous_window(continuous, roll_index, window_rows)

    warnings: list[str] = []
    data_access = create_data_access(target_data_root)
    active_prefixes = [
        infer_contract_prefix(contract)
        for contract in continuous.get_column("active_contract")
        .drop_nulls()
        .unique(maintain_order=True)
        .to_list()
    ]
    prefixes = normalize_secid_prefixes(secid_prefixes=secid_prefix or active_prefixes)
    from_prefix = infer_contract_prefix(from_contract, prefixes)

    source_summaries = data_access.list_contract_candle_summaries(
        exchange=target_exchange,
        engine=target_engine,
        market=target_market,
        timeframe=target_timeframe,
        secid_prefixes=prefixes,
        include_secid=[from_contract, to_contract],
    )
    from_candles, from_board = _load_contract_candles(
        data_access,
        exchange=target_exchange,
        engine=target_engine,
        market=target_market,
        timeframe=target_timeframe,
        secid=from_contract,
        board=_board_from_continuous(continuous, from_contract),
        summaries=source_summaries,
        warnings=warnings,
    )
    to_candles, to_board = _load_contract_candles(
        data_access,
        exchange=target_exchange,
        engine=target_engine,
        market=target_market,
        timeframe=target_timeframe,
        secid=to_contract,
        board=_board_from_continuous(continuous, to_contract),
        summaries=source_summaries,
        warnings=warnings,
    )
    from_window = _source_window(
        from_candles,
        roll_date=target_roll_date,
        side="from",
        window_rows=window_rows,
    )
    to_window = _source_window(
        to_candles,
        roll_date=target_roll_date,
        side="to",
        window_rows=window_rows,
    )

    from_summary = _source_summary(
        secid=from_contract,
        board=from_board,
        candles=from_candles,
        close_used_ts=previous_row.get("ts"),
        close_used=_as_float(previous_row.get("close")),
        window=from_window,
    )
    to_summary = _source_summary(
        secid=to_contract,
        board=to_board,
        candles=to_candles,
        close_used_ts=roll_row.get("ts"),
        close_used=_as_float(roll_row.get("close")),
        window=to_window,
    )

    pruning, empty_active_segments = _build_pruning_diagnostics(
        data_root=target_data_root,
        exchange=target_exchange,
        engine=target_engine,
        market=target_market,
        continuous_symbol=continuous_symbol,
        timeframe=target_timeframe,
        metadata_snapshot_ts=metadata_snapshot_ts,
        query=query,
        secid_prefixes=prefixes,
        roll_days_before=roll_days_before,
        warnings=warnings,
    )
    metadata = _metadata_by_secid(
        data_access,
        exchange=target_exchange,
        engine=target_engine,
        market=target_market,
        metadata_snapshot_ts=metadata_snapshot_ts,
        warnings=warnings,
    )

    candidate_ids = _candidate_contract_ids(
        prefix=from_prefix,
        anchor_year=target_roll_date.year,
        from_contract=from_contract,
        to_contract=to_contract,
        metadata=metadata,
        summaries=source_summaries,
        pruning=pruning,
    )
    candles_by_candidate = {
        from_contract.upper(): from_candles,
        to_contract.upper(): to_candles,
    }
    candidates = _candidate_summaries(
        secids=candidate_ids,
        prefix=from_prefix,
        anchor_year=target_roll_date.year,
        metadata=metadata,
        summaries=source_summaries,
        pruning=pruning,
        empty_active_segments=empty_active_segments,
        active_segments=_active_segments(continuous),
        candles_by_secid=candles_by_candidate,
    )
    candidates_frame = _candidate_frame(candidates)

    roll_gap = roll_report_row.roll_gap if roll_report_row is not None else None
    roll_gap_pct = roll_report_row.roll_gap_pct if roll_report_row is not None else None
    structural_gap = detect_structural_gap(
        roll_gap_pct=roll_gap_pct,
        from_contract=from_contract,
        to_contract=to_contract,
        anchor_year=target_roll_date.year,
        from_summary=from_summary,
        to_summary=to_summary,
        roll_gap_pct_threshold=structural_gap_roll_pct_threshold,
        calendar_days_threshold=structural_gap_calendar_days_threshold,
    )
    primary, secondary, recommendation = classify_roll_outlier(
        roll_gap_pct=roll_gap_pct,
        candidates=candidates,
        structural_gap=structural_gap,
    )

    summary = RollInvestigationSummary(
        path=target_path,
        data_root=target_data_root,
        roll_date=target_roll_date,
        from_contract=from_contract,
        to_contract=to_contract,
        roll_gap=roll_gap,
        roll_gap_pct=roll_gap_pct,
        old_close=roll_report_row.old_close if roll_report_row is not None else None,
        new_close=roll_report_row.new_close if roll_report_row is not None else None,
        old_close_ts=_as_date(previous_row.get("ts")),
        new_close_ts=_as_date(roll_row.get("ts")),
        continuous_window_row_count=continuous_window.height,
        from_contract_summary=from_summary,
        to_contract_summary=to_summary,
        candidate_contracts=candidates,
        primary_classification=primary,
        secondary_classifications=secondary,
        recommendation=recommendation,
        structural_gap=structural_gap,
        warnings=warnings,
    )
    return RollInvestigation(
        summary=summary,
        continuous_window=continuous_window,
        from_contract_window=from_window,
        to_contract_window=to_window,
        candidate_contracts_frame=candidates_frame.select(
            _available_columns(candidates_frame, CANDIDATE_COLUMNS)
        ),
    )


def save_roll_investigation(
    investigation: RollInvestigation,
    output_dir: str | Path,
) -> RollInvestigationSaveResult:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    summary_path = target_dir / "investigation_summary.json"
    continuous_path = target_dir / "continuous_window.csv"
    from_path = target_dir / "from_contract_window.csv"
    to_path = target_dir / "to_contract_window.csv"
    candidates_path = target_dir / "candidate_contracts.csv"

    investigation.continuous_window.write_csv(continuous_path)
    investigation.from_contract_window.write_csv(from_path)
    investigation.to_contract_window.write_csv(to_path)
    investigation.candidate_contracts_frame.write_csv(candidates_path)

    save_result = RollInvestigationSaveResult(
        output_dir=target_dir,
        investigation_summary_json=summary_path,
        continuous_window_csv=continuous_path,
        from_contract_window_csv=from_path,
        to_contract_window_csv=to_path,
        candidate_contracts_csv=candidates_path,
    )
    investigation.summary = investigation.summary.model_copy(update={"save_result": save_result})
    summary_path.write_text(
        json.dumps(investigation.summary.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return save_result
