from __future__ import annotations

import re
from collections.abc import Iterable

FUTURES_MONTH_CODES = "FGHJKMNQUVXZ"


def _prefix_items(value: object) -> Iterable[str]:
    if value is None:
        return ()
    if isinstance(value, str):
        return value.split(",")
    if isinstance(value, Iterable):
        items: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                items.extend(item.split(","))
            else:
                items.append(str(item))
        return items
    return (str(value),)


def normalize_secid_prefixes(
    secid_prefix: object = None,
    secid_prefixes: object = None,
) -> list[str]:
    prefixes: list[str] = []
    seen: set[str] = set()
    for raw in (*_prefix_items(secid_prefix), *_prefix_items(secid_prefixes)):
        prefix = raw.strip().upper()
        if not prefix or prefix in seen:
            continue
        prefixes.append(prefix)
        seen.add(prefix)
    return prefixes


def matches_any_secid_prefix(secid: object, prefixes: Iterable[str]) -> bool:
    secid_norm = str(secid).strip().upper()
    return any(secid_norm.startswith(prefix) for prefix in prefixes)


def infer_contract_prefix(secid: object, known_prefixes: object = None) -> str:
    secid_norm = str(secid).strip().upper()
    for prefix in sorted(
        normalize_secid_prefixes(secid_prefixes=known_prefixes),
        key=len,
        reverse=True,
    ):
        if secid_norm.startswith(prefix):
            return prefix

    match = re.fullmatch(
        rf"([A-Z]+?)[{FUTURES_MONTH_CODES}]\d+[A-Z]?(?:_[A-Z0-9]+)?",
        secid_norm,
    )
    if match:
        return match.group(1)

    leading_letters = re.match(r"([A-Z]+)", secid_norm)
    return leading_letters.group(1) if leading_letters else secid_norm
