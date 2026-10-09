"""ISO currency codes from a feed value, and the money prefix a figure in that currency gets.

ONE rule for every reader of an FMP currency field (`reportedCurrency` on a statement,
`currency` on a profile, the report's `revenue_engine.reporting_currency`, the Caydex fair
value's `currency`). It used to be three private copies with three different rules (2026-10-08
review): the report's collector refused "usd" that the Overview read as "USD", the Overview
upper-cased BEFORE its ASCII check (so "ßU" became "SSU", a valid-looking code), and report
chat accepted only an already-canonical value. The same feed value must never read as "USD" on
one surface and unknown on another.

Pure; no I/O; safe to import anywhere (integrations included).
"""

from __future__ import annotations

import re
from typing import Any, Optional

_CODE_RE = re.compile(r"[A-Z]{3}")

__all__ = ["currency_code", "money_prefix"]


def currency_code(raw: Any) -> Optional[str]:
    """An ISO-4217-shaped code ("USD", "TWD") or None — never a guess, never defaulted to USD.

    Trimmed, then exactly three characters, then ASCII, then upper-cased, then three letters
    A–Z: " usd " → "USD", "Twd" → "TWD". Anything else is unknown: None, a bool, a number,
    "US$", "N/A", "", "USDT", a huge string (one strip, then the length check), a non-ASCII
    letter ("ÜSD"). ASCII is checked BEFORE upper-casing because some letters upper-case INTO
    ASCII and change length ("ß".upper() == "SS", "ﬁ".upper() == "FI"): "ßU" must not become
    the code "SSU". Pure.
    """
    if not isinstance(raw, str):
        return None
    code = raw.strip()
    if len(code) != 3 or not code.isascii():
        return None
    code = code.upper()
    return code if _CODE_RE.fullmatch(code) else None


def money_prefix(currency: Any) -> str:
    """What goes before a compact amount: ``"$"`` for US dollars AND for an unknown currency
    (every surface's behaviour before reporting currencies were carried — a figure with no
    known currency keeps reading as it always has), else the code and a space (``"TWD "``),
    so a non-USD filer's figure is never dressed as dollars ("TWD 1.2T", never "$1.2T"). Pure.
    """
    code = currency_code(currency)
    return "$" if code in (None, "USD") else f"{code} "
