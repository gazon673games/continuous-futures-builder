from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from continuous_futures_builder._investigation_candidates import (
    _active_segments,
    _build_pruning_diagnostics,
    _candidate_contract_ids,
    _candidate_frame,
)
from continuous_futures_builder._investigation_core import (
    CANDIDATE_COLUMNS,
    RollInvestigation,
    _as_date,
    _as_float,
    _available_columns,
    _board_from_continuous,
    _continuous_window,
    _find_transition,
    _matching_roll_report_row,
    _single_value,
)
from continuous_futures_builder._investigation_gaps import (
    _candidate_summaries,
    detect_structural_gap,
)
from continuous_futures_builder._investigation_policy import (
    classify_roll_outlier,
)
from continuous_futures_builder._investigation_sources import (
    _load_contract_candles,
    _metadata_by_secid,
    _source_summary,
    _source_window,
)
from continuous_futures_builder.data_access import create_data_access
from continuous_futures_builder.exceptions import InputDataError
from continuous_futures_builder.models import (
    RollInvestigationSummary,
)
from continuous_futures_builder.prefixes import (
    infer_contract_prefix,
    normalize_secid_prefixes,
)


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
