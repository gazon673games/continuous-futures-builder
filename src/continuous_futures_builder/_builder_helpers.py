from __future__ import annotations


def _norm(value: str) -> str:
    return value.strip().upper()


def _date_from_ts(value: object) -> object:
    if hasattr(value, "date"):
        return value.date()
    return value
