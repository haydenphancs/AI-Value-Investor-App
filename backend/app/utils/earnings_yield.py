"""The earnings yield a MODEL reads beside a P/E (Ask Cay AI, 2026-10-09).

An earnings yield IS the inverse of a P/E, so a yield shown next to a P/E must be the inverse
of THAT P/E. The post-deploy eval (case `follow-up-shape`, 2026-10-09) answered "Microsoft's
current P/E ratio of 34.1 … earnings yield, which is the inverse of the P/E, is 3.36%" — and
1/34.1 is 2.93%. The 34.1 was the screen text in the eval's client context; the 3.36% was the
Price card's yield, 1/29.73 (the card's own P/E that day). Chat carries P/E figures on
different bases: the Key Stats P/E (TTM) is the live price over EPS (TTM) — FMP's daily-close
multiple for a filer whose two currencies differ (`stock_overview_service`) — the Price card's
P/E is the TTM multiple priced when the card was built (`valuation_snapshot_service`, up to
24 h old), and the card's Earnings Yield came from ANOTHER upstream endpoint than its P/E (key
metrics: total net income ÷ current market cap, not per-share earnings), so even the card's own
pair was only inverse while the two agreed — C showed P/E 13.75 beside 8.09% (1/13.75 = 7.27%).
The yield the model reads is therefore always derived here, from the P/E printed beside it —
never relayed.

Display-text in, display-text out: the cards hold formatted strings ("29.73", "Neg.", "—"),
and deriving from the DISPLAYED P/E is what lets the model check 1 ÷ 29.73 = 3.36% itself.
Since Price-card payload v8 (owner decision 2026-10-09) the card itself prints
`earnings_yield_text` of its displayed P/E (`valuation_snapshot_service.build_price_snapshot`),
so for every positive P/E the chat derivation below is a no-op on a fresh card (a loss-maker's
card says "N/A" where chat says "negative (TTM loss)"); it stays the backstop for an older row.
A STORED card the user sees as it is — a research report's Valuation card, read by report chat
(`chat_context_resolver._fundamentals_figures`) — is never re-derived (the report on screen shows
the stored figure): `without_unpaired_yield` leaves its yield out unless it inverts the card's
P/E. Stdlib only (a leaf module, like `peer_wording`): the card builder, the chat enrichment, the
financials tool and the report-chat resolver all import it.
"""

from __future__ import annotations

import logging
import math
import re
from types import SimpleNamespace
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# What a derived yield reads when the P/E is negative ("Neg."): the inverse of a negative
# multiple is a negative yield — the loss, not a gap. Short: the financials tool caps a card
# value at 32 characters.
NEGATIVE_EARNINGS = "negative (TTM loss)"
# What it reads when there is no P/E to invert. A yield from another source (the card's own
# net income ÷ market cap fallback) is never shown in its place: nothing beside it could be
# checked against it, and the next P/E the model sees (a live one) would be paired with it.
NOT_AVAILABLE = "N/A"

_PE_KEY = "pe"
_EY_KEY = "earnings_yield"
# A card row's name when the row has no `metric_key` (a cached row older than the key):
# "P/E" alone or with its peer comparison — "P/E (1.13x sector avg 22)", "P/E (sector avg
# 22)" — never another multiple's label ("P/E (FWD)", "P/E-to-Growth").
_PE_NAME = re.compile(r"^\s*P/E\s*(?:\(\s*(?:\d|sector|industry)|$)")
_EY_NAME = re.compile(r"^\s*Earnings Yield\s*(?:\(\s*(?:\d|sector|industry)|$)")
# A displayed multiple: digits with an optional thousands separator and decimals, an optional
# trailing "x". Anything else ("—", "N/A", "Neg.", "n/m") is not a number.
_MULTIPLE_TEXT = re.compile(r"^\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*[xX]?\s*$")
# "Neg." is what every multiple prints for a negative denominator (`_fmt_ratio`); a raw
# negative number is read the same way.
_NEGATIVE_TEXT = re.compile(r"^\s*(?:neg\.?|negative|-\s*\d[\d,]*(?:\.\d+)?\s*[xX]?)\s*$",
                            re.IGNORECASE)
# The peer comparison inside a percentage row's name (`valuation_snapshot_service.
# _sector_ctx_pct`, then `peer_wording`): "1.13x sector avg 2.97%" / "industry avg". The
# leading "N.NNx" is the company's value over the median — it moves with the value.
_PCT_COMPARISON = re.compile(
    r"(\d+(?:\.\d+)?)x\s+((?:sector|industry)\s+(?:avg|average)\s+)(\d+(?:\.\d+)?)%")
# Below this a yield prints as "0.00%", which reads as zero earnings.
_SMALLEST_SHOWN_PCT = 0.005
# Relative gap between the card's own yield and 1 / its P/E past which the card is logged as
# drifted (rounding alone moves the second decimal by well under 1%).
_DRIFT_WARN = 0.05


def _attr(obj: Any, name: str) -> Any:
    """`obj.name` for a model or namespace, `obj[name]` for a dict (a cached payload)."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def parse_multiple(text: Any) -> Optional[float]:
    """A displayed multiple ("29.73", "1,234.50", "29.7x") as a finite POSITIVE float, else
    None — a placeholder, "Neg.", zero, a bool, a non-string or anything unreadable. Pure."""
    if not isinstance(text, str):
        return None
    match = _MULTIPLE_TEXT.match(text)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", "") + (match.group(2) or ""))
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def is_negative_text(text: Any) -> bool:
    """True for the "Neg." a multiple prints when its denominator is negative (or a raw
    negative number). Pure."""
    return isinstance(text, str) and bool(_NEGATIVE_TEXT.match(text))


def earnings_yield_text(pe_text: Any) -> str:
    """The earnings yield of a P/E as DISPLAYED: "3.36%" (100 ÷ P/E, two decimals; "below
    0.01%" for a multiple so large it would print 0.00%), `NEGATIVE_EARNINGS` for "Neg.", and
    `NOT_AVAILABLE` for anything else. Pure; never raises."""
    pe = parse_multiple(pe_text)
    if pe is not None:
        pct = 100.0 / pe
        if not math.isfinite(pct):
            return NOT_AVAILABLE
        return "below 0.01%" if pct < _SMALLEST_SHOWN_PCT else f"{pct:.2f}%"
    if is_negative_text(pe_text):
        return NEGATIVE_EARNINGS
    return NOT_AVAILABLE


def _pct_value(text: Any) -> Optional[float]:
    """"3.36%" → 3.36; None for anything else (a placeholder, "negative (TTM loss)", a non-string)."""
    match = re.match(r"^\s*(\d+(?:\.\d+)?)%\s*$", text) if isinstance(text, str) else None
    return float(match.group(1)) if match else None


def _log_rebase(ticker: Optional[str], old_value: Any, value: str, pe_text: Any) -> None:
    """Say when a REAL card yield was replaced. Loud (WARNING) when it sat more than
    `_DRIFT_WARN` from its own P/E's inverse: the card's two upstream fields disagree, so the
    app's Price card shows a P/E and a yield that do not invert. A card that showed no yield
    ("N/A": a loss, or no P/E) replaced nothing and logs nothing."""
    old = _pct_value(old_value)
    if old is None:
        return
    new = _pct_value(value)
    if new is not None and new > 0 and abs(old - new) / new > _DRIFT_WARN:
        logger.warning(
            "[earnings-yield-drift] %s: the Price card's earnings yield %s is not 1 / its P/E %s "
            "(that is %s) — the card's two upstream fields disagree; chat shows the inverse",
            ticker or "?", old_value, pe_text, value,
        )
    else:
        logger.info(
            "[earnings-yield-rebased] %s: card earnings yield %r -> %r (1 / the card's P/E %r)",
            ticker or "?", old_value, value, pe_text,
        )


def _rebased_name(name: str, value: str) -> str:
    """The row name with its "N.NNx <peer> avg M%" recomputed for `value`; with no numeric
    value, the "N.NNx " is dropped and the median kept (the card's own form for a row with no
    value). A name with no comparison is returned unchanged."""
    match = _PCT_COMPARISON.search(name)
    if not match:
        return name
    pct, median = _pct_value(value), float(match.group(3))
    # A multiple that would print "0.00x" (a P/E in the thousands) is left out, as the card does.
    if pct is not None and median > 0 and round(pct / median, 2) > 0:
        ratio = f"{pct / median:.2f}x " + match.group(2) + match.group(3) + "%"
    else:
        ratio = match.group(2) + match.group(3) + "%"
    return name[:match.start()] + ratio + name[match.end():]


def _is_row(metric: Any, key: str, pattern: "re.Pattern[str]") -> bool:
    """A snapshot row (`metric_key` / `name`) or a stored report card's row (`history_key` /
    `label`): the key decides when present, else the name."""
    metric_key = _attr(metric, "metric_key") or _attr(metric, "history_key")
    if isinstance(metric_key, str) and metric_key:
        return metric_key == key
    name = _attr(metric, "name") or _attr(metric, "label")
    return isinstance(name, str) and bool(pattern.match(name))


def with_derived_earnings_yield(metrics: Any, ticker: Optional[str] = None) -> List[Any]:
    """A snapshot card's metric rows with its Earnings Yield re-derived from the card's own
    P/E (`earnings_yield_text`), its peer comparison recomputed to match. Every other row is
    returned as it came (same object); the yield row becomes a namespace with the same fields
    (`name`, `value`, `metric_key`, `score`, `peer_level`). A card with no yield row is
    returned unchanged; one with a yield but no P/E row reads `NOT_AVAILABLE`. Never raises:
    on an unexpected failure the yield row is left OUT (logged), never relayed unpaired."""
    rows = list(metrics) if isinstance(metrics, (list, tuple)) else []
    try:
        pe_text = next((_attr(m, "value") for m in rows if _is_row(m, _PE_KEY, _PE_NAME)), None)
        out: List[Any] = []
        for metric in rows:
            if not _is_row(metric, _EY_KEY, _EY_NAME):
                out.append(metric)
                continue
            old_value, name = _attr(metric, "value"), str(_attr(metric, "name") or "")
            value = earnings_yield_text(pe_text)
            if value != old_value:
                _log_rebase(ticker, old_value, value, pe_text)
                name = _rebased_name(name, value)
            out.append(SimpleNamespace(
                name=name, value=value, metric_key=_attr(metric, "metric_key"),
                score=_attr(metric, "score"), peer_level=_attr(metric, "peer_level"),
            ))
        return out
    except Exception as e:  # noqa: BLE001 — a malformed card must not cost the whole block
        # Fail closed: the card's own yield is never relayed unpaired.
        logger.warning("earnings yield derivation failed for %s (%s: %s) — the card's earnings "
                       "yield row is left out", ticker or "?", type(e).__name__, e)
        return [m for m in rows if not _safe_is_yield_row(m)]


# How far a STORED yield may sit from 1 / the same card's P/E and still be its inverse: the
# display rounding of both figures plus a relative 1.5% (AAPL's pre-v8 2.61% beside P/E 38.43,
# 1/38.43 = 2.60%, stays; C's 8.09% beside 13.75, 1/13.75 = 7.27%, does not).
_PAIR_REL_TOL = 0.015
_PAIR_ABS_TOL = 0.005


def is_inverse_pair(pe_text: Any, yield_text: Any) -> bool:
    """True when `yield_text` ("3.36%") is the inverse of `pe_text` ("29.73") within display
    rounding (`_PAIR_REL_TOL` relative plus half a printed hundredth). Pure; never raises."""
    pe, pct = parse_multiple(pe_text), _pct_value(yield_text)
    if pe is None or pct is None:
        return False
    expected = 100.0 / pe
    return math.isfinite(expected) and abs(pct - expected) <= expected * _PAIR_REL_TOL + _PAIR_ABS_TOL


def without_unpaired_yield(metrics: Any, ticker: Optional[str] = None) -> List[Any]:
    """A STORED card's rows as they are, minus an earnings-yield row whose figure is not the
    inverse of the same card's P/E (`is_inverse_pair`) — for a card the user sees exactly as
    stored (a research report's Valuation card, read by report chat).

    Never re-derived here: the report on screen shows the stored figure and report chat must not
    contradict it. Never relayed either: a report built before Price-card payload v8 (2026-10-09)
    can hold FMP's net income ÷ market cap beside the P/E (C: 13.75 and 8.09%), and the model reads
    a yield beside a P/E as its inverse. A yield row with no figure ("N/A") is kept, and so is every
    other row (same objects). Never raises: on a failure the yield row is left out (logged)."""
    rows = list(metrics) if isinstance(metrics, (list, tuple)) else []
    try:
        pe_text = next((_attr(m, "value") for m in rows if _is_row(m, _PE_KEY, _PE_NAME)), None)
        out: List[Any] = []
        for metric in rows:
            if _is_row(metric, _EY_KEY, _EY_NAME):
                value = _attr(metric, "value")
                if _pct_value(value) is not None and not is_inverse_pair(pe_text, value):
                    logger.info(
                        "[earnings-yield-unpaired] %s: a stored card's earnings yield %r is not "
                        "1 / its P/E %r — left out of what the model reads", ticker or "?",
                        value, pe_text,
                    )
                    continue
            out.append(metric)
        return out
    except Exception as e:  # noqa: BLE001 — a malformed card must not cost the whole block
        logger.warning("earnings yield pairing check failed for %s (%s: %s) — the stored card's "
                       "earnings yield row is left out", ticker or "?", type(e).__name__, e)
        return [m for m in rows if not _safe_is_yield_row(m)]


def _safe_is_yield_row(metric: Any) -> bool:
    try:
        return _is_row(metric, _EY_KEY, _EY_NAME)
    except Exception:  # noqa: BLE001 — unreadable rows are kept; the yield row is the risk
        return False
