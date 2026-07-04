from __future__ import annotations

from datetime import date, datetime
from typing import Any

import polars as pl

from continuous_futures_builder.models import FuturesContract, SkippedContract


def _norm(value: object) -> str:
    return str(value).strip().upper()


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        if not value.strip():
            return None
        return date.fromisoformat(value[:10])
    raise ValueError(f"Unsupported date value: {value!r}")


def _matches_query(row: dict[str, Any], query: str | None) -> bool:
    if not query:
        return True
    needle = query.casefold()
    fields = ("secid", "shortname", "secname", "assetcode")
    return any(needle in _text(row.get(field)).casefold() for field in fields)


def _matches_asset_code(row: dict[str, Any], asset_code: str | None) -> bool:
    if not asset_code:
        return True
    return _norm(row.get("assetcode")) == _norm(asset_code)


def _matches_secid_prefix(row: dict[str, Any], secid_prefix: str | None) -> bool:
    if not secid_prefix:
        return True
    return _norm(row.get("secid")).startswith(_norm(secid_prefix))


def _contract_from_row(row: dict[str, Any]) -> FuturesContract | SkippedContract:
    try:
        expiration_date = _coerce_date(row.get("expiration_date"))
        last_trade_date = _coerce_date(row.get("last_trade_date"))
    except ValueError as exc:
        return SkippedContract(
            secid=_text(row.get("secid")) or None,
            board=_text(row.get("board")) or None,
            reason=f"invalid expiration metadata: {exc}",
        )

    effective_expiration = expiration_date or last_trade_date
    if effective_expiration is None:
        return SkippedContract(
            secid=_text(row.get("secid")) or None,
            board=_text(row.get("board")) or None,
            reason="missing both expiration_date and last_trade_date",
        )

    secid = _text(row.get("secid")).strip()
    board = _text(row.get("board")).strip()
    if not secid or not board:
        return SkippedContract(
            secid=secid or None,
            board=board or None,
            reason="missing secid or board",
        )

    return FuturesContract(
        secid=secid,
        board=board,
        assetcode=_text(row.get("assetcode")).strip() or None,
        shortname=_text(row.get("shortname")).strip() or None,
        secname=_text(row.get("secname")).strip() or None,
        expiration_date=effective_expiration,
        last_trade_date=last_trade_date,
    )


def filter_contracts(
    metadata: pl.DataFrame,
    query: str | None = None,
    asset_code: str | None = None,
    secid_prefix: str | None = None,
    include_secid: list[str] | None = None,
    exclude_secid: list[str] | None = None,
) -> tuple[list[FuturesContract], list[SkippedContract]]:
    include = {_norm(secid) for secid in include_secid or []}
    exclude = {_norm(secid) for secid in exclude_secid or []}
    selected: list[FuturesContract] = []
    skipped: list[SkippedContract] = []

    for row in metadata.iter_rows(named=True):
        secid_norm = _norm(row.get("secid"))
        forced = secid_norm in include
        if secid_norm in exclude:
            skipped.append(
                SkippedContract(
                    secid=_text(row.get("secid")) or None,
                    board=_text(row.get("board")) or None,
                    reason="excluded by exclude_secid",
                )
            )
            continue

        matches = (
            _matches_query(row, query)
            and _matches_asset_code(row, asset_code)
            and _matches_secid_prefix(row, secid_prefix)
        )
        if not matches and not forced:
            continue

        contract = _contract_from_row(row)
        if isinstance(contract, SkippedContract):
            skipped.append(contract)
        else:
            selected.append(contract)

    deduped: list[FuturesContract] = []
    seen: set[tuple[str, str]] = set()
    for contract in sorted(
        selected,
        key=lambda item: (item.expiration_date, item.secid, item.board),
    ):
        key = (contract.secid, contract.board)
        if key in seen:
            skipped.append(
                SkippedContract(
                    secid=contract.secid,
                    board=contract.board,
                    reason="duplicate secid+board removed",
                )
            )
            continue
        seen.add(key)
        deduped.append(contract)

    return deduped, skipped
