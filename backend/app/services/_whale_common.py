"""
Shared helpers for whale-trade amount parsing and 13F annual returns.

Ensures the Whales Bought/Sold alert feed (reads Supabase ``whale_trades``),
the per-whale profile view (reads Supabase ``whale_trades``), and the
Ticker Holders tab (computes live from FMP) all agree on dollar amounts
for the same underlying congressional disclosure or 13F filing.

Without these helpers, each call-site implemented its own range parser
and trade-dollar formula, giving different answers for the same trade.

The annual-return section at the bottom exists for the SAME reason, after the
same thing happened a second time: `whale_service._compute_avg_annual_return`
and `hydrate_whales._compute_ytd_return` were independent copies of one formula
and had silently drifted apart — different outlier floors (-100 vs -200) and
different captions ("13F Portfolio CAGR" vs "13F Portfolio Avg.") for the same
number, with the hydration copy being the one that actually runs in production.
"""

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ── Snapshot persistence guard ──────────────────────────────────────

# Keys that live on the in-memory snapshot dict but are NOT columns on the
# whale_filing_snapshots table. `trade_groups` (the full per-filing timeline) is
# synced to the whale_trade_groups TABLE instead; sending it as a column makes
# PostgREST reject the entire upsert (PGRST204) and silently kills the snapshot
# cache tier for congress AND 13F whales.
_SNAPSHOT_NON_COLUMNS = ("trade_groups",)


def snapshot_db_row(snapshot: dict) -> dict:
    """Return a copy of ``snapshot`` safe to upsert into whale_filing_snapshots.

    Strips in-memory-only keys (see ``_SNAPSHOT_NON_COLUMNS``). Keeps the full
    dict callers pass around for downstream syncing / rendering intact.
    """
    return {k: v for k, v in snapshot.items() if k not in _SNAPSHOT_NON_COLUMNS}


# ── Congressional (range-based) ─────────────────────────────────────


def parse_congress_amount_dollars(amount_str: str) -> float:
    """Parse FMP's congressional amount range → midpoint in DOLLARS.

    Politicians report trades in ranges (by law). FMP returns strings like
    ``"$1,001 - $15,000"``. We convert to the range midpoint.

    Handles:
      - Ranges:  ``"$1,001 - $15,000"``    → ``8_000.5``
      - Over-X:  ``"Over 50,000,000"``      → ``75_000_000.0`` (1.5× base)
      - Single:  ``"100000"``               → ``100_000.0``
      - Empty / unparseable                  → ``0.0``
    """
    if not amount_str:
        return 0.0

    # Coerce defensively: FMP normally returns a string bucket, but a stray
    # numeric would raise AttributeError on .replace() and abort the WHOLE
    # congressional rebuild (the loop has no per-row guard).
    clean = str(amount_str).replace("$", "").replace(",", "").strip()

    if " - " in clean:
        parts = clean.split(" - ")
        try:
            low = float(parts[0].strip())
            high = float(parts[1].strip())
            return (low + high) / 2
        except (ValueError, IndexError):
            pass

    if clean.lower().startswith("over "):
        try:
            base = float(clean[5:].strip())
            return base * 1.5
        except ValueError:
            pass

    try:
        return float(clean)
    except ValueError:
        return 0.0


def parse_congress_amount_bounds(
    amount_str: str,
) -> Tuple[float, Optional[float]]:
    """Parse FMP's congressional amount range → ``(low, high)`` DOLLAR bounds.

    Politicians disclose trades ONLY as ranges (by law) — never an exact
    figure. Returns the honest bounds so the UI can show a range instead of
    the fabricated-precision midpoint that :func:`parse_congress_amount_dollars`
    produces (that midpoint is still used internally for sorting / net math).

      - Range:   ``"$1,001 - $15,000"``   → ``(1001.0, 15000.0)``
      - Over-X:  ``"Over $50,000,000"``    → ``(50_000_000.0, None)`` (open high)
      - Single:  ``"100000"``              → ``(100_000.0, 100_000.0)``
      - Empty / unparseable                 → ``(0.0, 0.0)``
    """
    if not amount_str:
        return (0.0, 0.0)

    clean = str(amount_str).replace("$", "").replace(",", "").strip()

    if " - " in clean:
        parts = clean.split(" - ")
        try:
            low = float(parts[0].strip())
            high = float(parts[1].strip())
            return (low, high)
        except (ValueError, IndexError):
            pass

    if clean.lower().startswith("over "):
        try:
            base = float(clean[5:].strip())
            return (base, None)  # open-ended top bucket
        except ValueError:
            pass

    try:
        v = float(clean)
        return (v, v)
    except ValueError:
        return (0.0, 0.0)


def sum_amount_bounds(
    bounds: list,
) -> Tuple[float, Optional[float]]:
    """Sum a list of ``(low, high)`` bounds into a single summed range.

    If ANY high is ``None`` (open-ended "Over $X" bucket), the summed high is
    ``None`` too — the total is open-ended.
    """
    total_low = 0.0
    total_high: Optional[float] = 0.0
    for low, high in bounds:
        total_low += low
        if total_high is not None:
            total_high = None if high is None else total_high + high
    return (total_low, total_high)


def format_amount_short(value: float) -> str:
    """Compact dollar label with no sign: ``$8K`` / ``$1.5M`` / ``$2.34B``.

    Rolls up to the next unit when rounding would render a four-digit mantissa
    in the lower unit (999_600 → ``$1.0M``, not ``$1000K``)."""
    amt = abs(value)
    if amt >= 1_000_000_000 or round(amt / 1_000_000, 1) >= 1000:
        return f"${amt / 1_000_000_000:.2f}B"
    if amt >= 1_000_000 or round(amt / 1_000, 0) >= 1000:
        return f"${amt / 1_000_000:.1f}M"
    if amt >= 1_000:
        return f"${amt / 1_000:.0f}K"
    return f"${amt:.0f}"


def format_amount_range(low: float, high: Optional[float]) -> str:
    """Format a summed congressional dollar RANGE for display.

      - Open-ended high (``None``)  → ``"$50M+"``
      - Collapsed (``low == high``) → ``"$8K"``
      - Otherwise                   → ``"$50K – $250K"``
    """
    if high is None:
        return f"{format_amount_short(low)}+"
    if abs(high - low) < 1.0:
        return format_amount_short(low)
    return f"{format_amount_short(low)} – {format_amount_short(high)}"


# ── 13F Institutional (shares × implied price) ─────────────────────


def calc_13f_trade_dollars(
    curr_shares: float,
    curr_value: float,
    prev_shares: float,
    prev_value: float,
    min_amount: float = 1_000.0,
) -> Tuple[Optional[str], float]:
    """Compute institutional trade action + dollar size between two quarters.

    Uses ``shares_change × implied_price`` to strip out stock-price
    appreciation — otherwise a holder who sold shares during a rally could
    appear to have "bought" because their position's dollar value grew.

    Same formula as ``_build_institutional_activities`` in holders_service,
    so alert amounts match what the Ticker Holders tab shows.

    Returns ``(action, amount)``:
      - ``action``: ``"BOUGHT"`` | ``"SOLD"`` | ``None`` (below threshold)
      - ``amount``: absolute dollar value (always positive when non-None)
    """
    shares_change = curr_shares - prev_shares

    # Prefer the current quarter's implied price; fall back to prev
    # (useful for "Closed" positions where curr is zero/empty).
    implied_price = 0.0
    if curr_shares > 0 and curr_value > 0:
        implied_price = curr_value / curr_shares
    elif prev_shares > 0 and prev_value > 0:
        implied_price = prev_value / prev_shares

    if implied_price <= 0:
        return (None, 0.0)

    amount = abs(shares_change) * implied_price

    if amount < min_amount:
        return (None, 0.0)

    action = "BOUGHT" if shares_change > 0 else "SOLD"
    return (action, amount)


# ── 13F annual return (CAGR) ─────────────────────────────────────────
#
# ONE implementation, because two of them drifted. See the module docstring.
#
# WHAT THIS NUMBER IS, precisely — the info sheet on the Whale Profile screen
# has to be able to say this truthfully:
#   * It chains FMP's own year-over-year performance figures for the filer's
#     13F sleeve. It is NOT the manager's fund return, not net of fees, and not
#     what an investor in that fund earned.
#   * It covers US-listed long equity only. Bonds, cash, private companies,
#     foreign listings, shorts and most derivatives never appear on a 13F.
#   * It is therefore never a statement about the person's total wealth.

# A "CAGR" needs at least two compounded calendar years. Below this we report
# `insufficient_history` rather than a number.
#
# This threshold is the fix for a real defect: the old code, finding no
# December-31 rows, fell back to a SINGLE latest 1-year return and still
# labelled it "13F Portfolio CAGR". A one-year figure presented as a compound
# annual growth rate is simply a false statement about the data.
MIN_CAGR_YEARS = 2

# A yearly return <= -100% is impossible for a long-only 13F sleeve (you cannot
# lose more than everything), and >= 500% is treated as corrupt upstream data.
#
# The floor is -100, NOT -200. `hydrate_whales` used -200, which admits
# impossible values — and because an EVEN count of sub-(-100) values multiplies
# to a spuriously POSITIVE product, they slip past the `product > 0` guard and
# emerge as a plausible-looking positive CAGR.
YEAR_RETURN_FLOOR = -100.0
YEAR_RETURN_CEIL = 500.0

# Return statuses. `insufficient_history` and `unavailable` are deliberately
# DISTINCT: the first means "we read the data and it isn't enough", the second
# means "we could not read it". Only the first may overwrite a stored value —
# see the persistence rule in whale_service / hydrate_whales.
RETURN_OK = "ok"
RETURN_INSUFFICIENT = "insufficient_history"
RETURN_UNAVAILABLE = "unavailable"

SOURCE_13F = "13f_avg"
SOURCE_STOCK = "stock_cagr"

_YEAR_END_RE = re.compile(r"^(\d{4})-12-31")


@dataclass(frozen=True)
class AnnualReturn:
    """The annual-return tile's value AND its provenance.

    Provenance travels with the number on purpose. The screen previously showed
    a bare percent whose window was unknowable — a 2-year and a 10-year figure
    rendered identically — and whose absence rendered as a confident green
    "+0.0%". Both are only fixable if the caller can see `window_years` and
    `status`.
    """

    value: Optional[float]          # percent; None whenever status != RETURN_OK
    window_years: Optional[int]     # calendar years compounded; None unless OK
    source: str                     # SOURCE_13F | SOURCE_STOCK | ""
    status: str                     # RETURN_OK | RETURN_INSUFFICIENT | RETURN_UNAVAILABLE

    @property
    def is_ok(self) -> bool:
        return self.status == RETURN_OK


def _usable_year_returns(perf_list: Sequence[Dict[str, Any]]) -> Dict[int, float]:
    """Pure: {calendar_year: return_pct} for in-range December-31 rows.

    Keyed by YEAR rather than accumulated into a list because FMP can return
    more than one row for the same year-end. A duplicate would be compounded
    twice AND inflate the exponent's denominator, quietly changing the answer.
    """
    out: Dict[int, float] = {}
    for row in perf_list:
        if not isinstance(row, dict):
            continue
        match = _YEAR_END_RE.match(str(row.get("date") or ""))
        if not match:
            continue
        raw = row.get("performancePercentage1year")
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        if not (YEAR_RETURN_FLOOR < value < YEAR_RETURN_CEIL):
            continue
        out[int(match.group(1))] = value
    return out


def compute_13f_cagr(perf_list: Optional[Sequence[Dict[str, Any]]]) -> AnnualReturn:
    """Compound the filer's year-end 13F returns into a true CAGR.

    Returns RETURN_UNAVAILABLE when there is nothing to read (an upstream miss),
    and RETURN_INSUFFICIENT when there is data but fewer than MIN_CAGR_YEARS
    usable calendar years. Callers must treat those differently: only the latter
    is a judgement about the whale, and only the latter may clear a stored value.
    """
    if not perf_list or not isinstance(perf_list, (list, tuple)):
        return AnnualReturn(None, None, "", RETURN_UNAVAILABLE)

    by_year = _usable_year_returns(perf_list)
    if len(by_year) < MIN_CAGR_YEARS:
        return AnnualReturn(None, None, "", RETURN_INSUFFICIENT)

    product = math.prod(1 + r / 100 for r in by_year.values())
    if product <= 0:
        # Total loss or corrupt input — a CAGR is undefined, not zero.
        return AnnualReturn(None, None, "", RETURN_INSUFFICIENT)

    cagr = (product ** (1 / len(by_year)) - 1) * 100
    if not math.isfinite(cagr):
        return AnnualReturn(None, None, "", RETURN_INSUFFICIENT)

    return AnnualReturn(round(cagr, 2), len(by_year), SOURCE_13F, RETURN_OK)


def compute_ticker_cagr(
    max_return_pct: Optional[float], years: Optional[float]
) -> AnnualReturn:
    """Annualize an associated public vehicle's since-inception price change.

    Used for the five whales with an `associated_ticker` (BRK-A, PSH.L, ARKK,
    IEP, MKL). NOT a 13F number at all — it is that vehicle's SHARE PRICE, so
    the UI must name the ticker rather than implying it describes the sleeve in
    the tile beside it. Price-return only; no dividends.
    """
    try:
        pct = float(max_return_pct)  # type: ignore[arg-type]
        span = float(years)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return AnnualReturn(None, None, "", RETURN_UNAVAILABLE)

    if not math.isfinite(pct) or not math.isfinite(span) or span <= 0:
        return AnnualReturn(None, None, "", RETURN_UNAVAILABLE)

    growth = 1 + pct / 100
    if growth <= 0:
        return AnnualReturn(None, None, "", RETURN_INSUFFICIENT)

    cagr = (growth ** (1 / span) - 1) * 100
    if not math.isfinite(cagr):
        return AnnualReturn(None, None, "", RETURN_INSUFFICIENT)

    return AnnualReturn(round(cagr, 1), int(span), SOURCE_STOCK, RETURN_OK)


def return_label_for(source: str, ticker: Optional[str] = None) -> str:
    """The ONLY producer of the annual-return caption.

    Centralised because the two call sites had drifted to different strings for
    the same computation ("13F Portfolio CAGR" vs "13F Portfolio Avg."), so the
    caption a user saw depended on which code path happened to refresh the row.
    """
    if source == SOURCE_STOCK and ticker:
        return f"{ticker} CAGR"
    if source == SOURCE_13F:
        return "13F Portfolio CAGR"
    return ""


def unavailable_return_label(status: str) -> str:
    """Caption for a tile with no believable number.

    Sent as `return_label` so that ALREADY-SHIPPED clients — which render that
    string verbatim under a green "+0.0%" and cannot be taught the em-dash —
    at least stop captioning that zero as a "13F Portfolio CAGR".
    """
    if status == RETURN_UNAVAILABLE:
        return "Return data unavailable"
    return "Not enough history"
