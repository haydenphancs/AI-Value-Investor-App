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

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

# The 13F quarter helpers are the SINGLE source of truth for "which quarter should a
# filer have filed by now" — `latest_filed_13f_quarter` already encodes the statutory
# 45-day lag. Imported rather than re-derived: a second copy of that arithmetic is
# exactly how the two 13F diff paths drifted apart before.
from app.utils.period_labels import (
    _FILING_PERIOD_RE,
    filing_period_display,
    latest_filed_13f_quarter,
)
# The one 13F comparison vocabulary ("quarter" / "gap" / "first_filing"), shared with the
# Trillion-Dollar Club builder. A pydantic-only module, so importing it here cannot cycle.
from app.schemas.trillion_club import (
    COMPARISON_FIRST_FILING,
    COMPARISON_GAP,
    COMPARISON_QUARTER,
)

logger = logging.getLogger(__name__)


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


# FMP congressional `type` → our action. Lowercased, stripped keys.
#
# SINGLE SOURCE OF TRUTH. This table used to be duplicated in whale_service.py and
# hydrate_whales.py, and BOTH resolved an unrecognised type to "BOUGHT" — so any
# string FMP has not been seen to emit was silently booked as a PURCHASE, inflating
# `total_bought` and able to flip a filing's whole `net_action` from SOLD to BOUGHT.
CONGRESS_ACTION_BY_TYPE: dict = {
    "purchase": "BOUGHT",
    "purchase (partial)": "BOUGHT",
    "sale_full": "SOLD",
    "sale_partial": "SOLD",
    "sale (full)": "SOLD",
    "sale (partial)": "SOLD",
    "sale": "SOLD",
    "sale (full/partial)": "SOLD",
    "exchange": "BOUGHT",
}


def resolve_congress_action(raw_type) -> Optional[str]:
    """Map an FMP congressional trade `type` to BOUGHT / SOLD, or None if unknown.

    Returns **None** rather than defaulting to a direction. The UI renders a hard
    BOUGHT/SOLD badge and sums the trade into a net figure, so guessing here does not
    degrade gracefully — it states something false. Callers skip an unresolvable trade
    and log it, which loses one row rather than mis-stating every aggregate built on it.
    """
    if not isinstance(raw_type, str):
        return None
    key = raw_type.lower().strip()
    if not key:
        return None
    if key in CONGRESS_ACTION_BY_TYPE:
        return CONGRESS_ACTION_BY_TYPE[key]
    # Tolerate unseen decorations ("Sale (Partial) - Spouse") without guessing a
    # direction we have no evidence for: the words purchase/sale are unambiguous.
    if "purchase" in key or key.startswith("buy"):
        return "BOUGHT"
    if "sale" in key or key.startswith("sell"):
        return "SOLD"
    return None


# ── Congressional duplicate handling ────────────────────────────────
#
# `house-latest` / `senate-latest` return a GLOBAL feed of every member (the by-name
# path pulls 7500 rows) which the client then filters by name. It is paginated 30 pages
# at a time from a feed that is being WRITTEN TO, and it genuinely returns the same
# disclosure more than once. Measured 2026-08-21: Gilbert Cisneros 1091 rows / 1032
# distinct, Josh Gottheimer 138 / 136, Nancy Pelosi 21 / 21.
#
# ONE definition of "the same disclosure", shared by the idempotency hash and by BOTH
# aggregations, so they can never disagree about whether a row is a repeat.

# Content fields only. `link` is excluded deliberately: it is a PDF URL the Clerk can
# re-issue, and it says nothing about what was traded.
CONGRESS_TRADE_FIELDS: Tuple[str, ...] = (
    "transactionDate", "disclosureDate", "symbol", "type",
    "amount", "owner", "assetDescription",
)

# The hash covers the N most RECENT disclosures, chosen deterministically by date —
# never the first N as the upstream feed happened to order them.
CONGRESS_HASH_MAX = 50


def congress_trade_identity(trade: Dict[str, Any]) -> str:
    """What makes two congressional disclosures THE SAME filing."""
    return "|".join(str(trade.get(k) or "") for k in CONGRESS_TRADE_FIELDS)


def dedupe_congress_trades(raw_trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop byte-identical repeats, preserving first-seen order.

    ⚠️ NOT cosmetic. Counting a repeat twice corrupts the portfolio, and not only by
    inflating it. Both aggregations sum every row into a per-symbol running value and
    then drop non-positive positions, so a SALE disclosed twice can drive a real
    position to zero and DELETE it. Measured on Josh Gottheimer: one duplicated IFNNY
    sale cancelled a genuine $8,000 holding and the ticker vanished — 25 positions
    served where 26 were real. On the serve path the same repeats inflated his whole
    portfolio to $1,828,012 against a true $1,062,009, with MSFT counted exactly twice.
    """
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for t in raw_trades:
        if not isinstance(t, dict):
            continue
        key = congress_trade_identity(t)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def congressional_raw_hash(raw_trades: List[Dict[str, Any]]) -> str:
    """Stable idempotency hash for one member's disclosures.

    ⚠️ Why this is not `sha256(json.dumps(raw_trades[:50], sort_keys=True))`, which is
    what both callers used to do:

    1. `sort_keys=True` sorts keys *inside* each dict — it does NOT order the LIST. The
       name is a trap. Any reshuffle of the feed changed the hash.
    2. `[:50]` took the first 50 *as the feed ordered them*, so a new disclosure by ANY
       OTHER member shifted the window and changed this member's hash.

    Measured 2026-08-21: the five House members re-ran the full `_persist` write path on
    every sweep (01:46, 02:00 and 02:15 the same night, 7.6-14.8s each) while every
    Senate member correctly skipped — the House feed simply churns faster. Ted Cruz's
    stored hash was byte-identical across two months; no House member's ever matched a
    fresh fetch.

    The fix is fourfold: identities from content fields only, DEDUPED (a shifting
    duplicate count is the same class of false change), SORTED so order cannot matter,
    and the newest `CONGRESS_HASH_MAX` taken *after* sorting so an old trade ageing out
    of the shared fetch window is not a change either. Sorting is lexical on a string
    that starts with `transactionDate` (ISO `YYYY-MM-DD`), so it is chronological and
    `[-N:]` is the newest N.

    Members with fewer than `CONGRESS_HASH_MAX` filings are fully covered, so for them
    an ageing-out IS a change — a documented trade-off, not an oversight.
    """
    ids = sorted({
        congress_trade_identity(t) for t in raw_trades if isinstance(t, dict)
    })
    return hashlib.sha256(
        json.dumps(ids[-CONGRESS_HASH_MAX:]).encode()
    ).hexdigest()


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


# Sentinel: the split and the real flow cannot be separated, so emit NOTHING.
# Distinct from "no restatement needed" (which returns prev_shares unchanged).
SPLIT_SUPPRESS = object()

#: How many tickers a single 13F diff may look up corporate actions for.
#:
#: Lives here for the reason this module exists: `hydrate_whales` and `whale_service`
#: both bound the same fan-out, and a second copy of the number is how the annual-return
#: formula drifted. Each lookup costs TWO price-series fetches (a derived split reads
#: `/full` and `/non-split-adjusted`), and `whale_service._diff_quarters` runs on a user
#: request — so an entire restated book, where every position looks like a share
#: multiple, would otherwise fan out unbounded FMP calls inside one request.
MAX_SPLIT_LOOKUPS = 25


def restate_prev_shares_for_split(
    prev_shares: float, curr_shares: float, split_ratio: float
):
    """Put the previous quarter's share count on the current basis, or suppress.

    Returns the restated ``prev_shares``, ``prev_shares`` unchanged when the ratio does
    not apply, or :data:`SPLIT_SUPPRESS` when split and real flow are inseparable.

    ONE implementation, because there were THREE and they had already drifted twice.
    ``holders_service._compute_quarter_flow`` is the most-evolved of them and is the
    behaviour encoded here.

    ``ratio_obs = curr_shares / prev_shares`` mixes the split with real trading. The
    classifier compares it against two hypotheses — H0 "the feed is already split
    adjusted" (predicts ``ratio_obs ~ 1.0``) and H1 "the feed is raw" (predicts
    ``ratio_obs ~ ratio``) — and asks which is nearer.

    ⚠️ **The midpoint test is direction-dependent.** For a forward split the count
    INFLATES so H1 sits above H0 and the test is ``>=``. For a REVERSE split (1:10 →
    ratio 0.1) the count SHRINKS, H1 sits BELOW H0, and the test must flip to ``<=``.
    Both whale paths carried the ``>=`` form unconditionally, so on any reverse-split
    quarter an ordinary ``ratio_obs ~ 1.0`` fell into the ambiguous branch — which in
    `hydrate_whales` restated and **fabricated a large BOUGHT out of nothing**, the exact
    failure this codebase has already shipped once.

    ⚠️ **Ambiguity means SUPPRESS, never restate.** `calc_13f_trade_dollars` turns a bad
    `prev_shares` into a WRONG-SIGN trade, not merely a wrong magnitude — a holder who
    bought reads as a seller. A missing bar is recoverable; a fabricated multi-million
    dollar BOUGHT that feeds an alert is not. "No bar, not garbage."
    """
    if not split_ratio or split_ratio == 1.0:
        return prev_shares
    if prev_shares <= 0 or curr_shares <= 0:
        return prev_shares

    ratio_obs = curr_shares / prev_shares

    # Clean split signature — the residual is the real flow.
    if abs(ratio_obs - split_ratio) <= 0.15 * split_ratio:
        return prev_shares * split_ratio

    midpoint = (1.0 + split_ratio) / 2.0
    jumped_toward_split = (
        ratio_obs >= midpoint if split_ratio > 1.0 else ratio_obs <= midpoint
    )
    if jumped_toward_split:
        return SPLIT_SUPPRESS

    # The count did NOT move toward the split: spinoff / ADR-ratio change / a count
    # FMP's /splits mislabels. Keep the raw diff.
    return prev_shares


def is_implausible_share_flow(shares_change: float, curr_shares: float) -> bool:
    """Magnitude backstop, ported from ``holders_service._compute_quarter_flow``.

    A quarterly net change cannot plausibly exceed ~half the shares HELD. Anything at or
    above that is a corporate action or a data artifact, not flow. This catches a bad
    restatement regardless of which branch produced it — a backstop that does not depend
    on classifying the split correctly in the first place.
    """
    if curr_shares <= 0:
        return False
    if not math.isfinite(shares_change) or not math.isfinite(curr_shares):
        return True
    return abs(shares_change) >= 0.5 * curr_shares


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

    # A share count that did not move is not a trade in either direction. Reachable only
    # where `min_amount == 0.0` — which is exactly `holders_service`'s Recent Activities
    # call — so a perfect-wash quarter was rendered as a SALE of $0.
    if shares_change == 0:
        return (None, 0.0)

    action = "BOUGHT" if shares_change > 0 else "SOLD"
    return (action, amount)


# ── 13F share positions: the ONE input both quarter diffs read ─────
#
# FMP's `institutional-ownership/extract` returns one row per 13F information-table entry,
# so one stock can arrive as several rows:
#   * put / call rows (`putCallShare`) carrying the UNDERLYING's symbol, share count and
#     notional value — an option position, not shares the fund owns;
#   * a principal-amount line (`sharesType` PRN: a convertible or bond);
#   * the same CUSIP once per manager / investment discretion (a multi-manager filer lists
#     one stock under several managers);
#   * a 13F-HR/A amendment's rows next to the original filing's.
# Both 13F diff writers (`whale_service._diff_quarters`, `hydrate_whales._diff_quarters`)
# keyed these by symbol with the LAST row winning, so a fund's put position — or one
# manager's slice — overwrote the stock's own share count, and the quarter's "trade" was the
# difference between two unrelated rows. That fed `whale_trades`, the whale profiles' trade
# cards and the Home Whale Accumulation card. Read the extract the way
# `trillion_club.builder.normalize_rows` does instead, with one deliberate difference: a
# MISSING `sharesType` is kept (older rows and the diff fixtures carry none, and dropping
# them would turn every such position into an exit).
#
# The HOLDINGS read the same positions (`thirteen_f_holdings`; owner decision 2026-10-09).
# Both holdings builders used to sum EVERY row per symbol, and a 13F reports an option at
# the value of its UNDERLYING shares — so a put (a bet against the stock) showed as a large
# long "holding", and an options-heavy book's "13F Equity Portfolio" figure, allocations,
# change_percent, AI summaries and trade weights were mostly option notional. Measured with
# `scripts/measure_13f_option_notional.py`.

# Bumped whenever the 13F DERIVATION — the diff, and since v3 the holdings — gives a
# different answer for the SAME raw filing. It is folded into `thirteen_f_raw_hash`, which
# the nightly hydrator compares with the stored snapshot's to decide "already derived", so
# a bump re-derives every fund's latest quarter once, in the next sweep, with no manual step.
#   v2 (2026-10-09): option / principal rows dropped, per-manager rows summed, the latest
#                    accession replaces a position (`thirteen_f_share_positions`).
#   v3 (2026-10-09): holdings, the portfolio value, change_percent and the diffs' allocation
#                    denominators read those share positions too (`thirteen_f_holdings`).
#   v4 (2026-10-09): a quarter is diffed ONLY with the adjacent previous quarter; a gap or a
#                    first filing writes no trades (`select_13f_comparison`), and the
#                    comparison basis + the previous positions join the hash.
THIRTEEN_F_DIFF_VERSION = 4


def _finite_or_zero(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def is_13f_non_share_row(row: Mapping[str, Any]) -> bool:
    """True for an explicit put/call row or an explicit non-``SH`` (``PRN``) row.

    A missing ``putCallShare`` / ``sharesType`` is NOT evidence of either: it is read as a
    share row, exactly as before this rule existed."""
    put_call = str(row.get("putCallShare") or "").strip().lower()
    if put_call in ("put", "call"):
        return True
    shares_type = str(row.get("sharesType") or "").strip().upper()
    return bool(shares_type) and shares_type != "SH"


def thirteen_f_share_positions(raw: Any) -> Dict[str, Dict[str, Any]]:
    """``{SYMBOL: {"symbol", "name", "value", "shares"}}`` — one filing's SHARE positions.

    * a non-dict row, or one with an empty / ``--`` symbol (``symbol``, else
      ``tickercusip``), is skipped, as the diffs always did;
    * an explicit Put/Call or non-``SH`` row is skipped (``is_13f_non_share_row``);
    * within one accession, rows for the same position (its CUSIP, else its symbol) are
      SUMMED — one row per manager / discretion;
    * across accessions the LATEST accession wins per position (a 13F-HR/A restates it).
      Accessions come from ``link`` / ``finalLink``; rows without one share a group keyed
      by ``filingDate``, ordered oldest first;
    * positions are then summed per symbol (two CUSIPs can resolve to one ticker).

    ``value`` and ``shares`` coerce NaN / Inf / garbage to 0 (the diffs' ``_finite_float``
    rule); ``shares`` is an ``int``; ``name`` is ``securityName`` → ``companyName`` → the
    symbol, as before. Never raises on a malformed row.
    """
    # Imported here, not at module top: `trillion_club.rules` is pure and light, but this
    # module is imported by nearly every whale/holders path and must stay cycle-free.
    from app.services.trillion_club.rules import accession_from_link, normalize_cusip

    groups: Dict[str, Dict[str, Dict[str, Any]]] = {}
    order: Dict[str, Tuple[str, str, str]] = {}
    for r in raw if isinstance(raw, (list, tuple)) else ():
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol") or r.get("tickercusip") or "").strip().upper()
        if not sym or sym == "--":
            continue
        if is_13f_non_share_row(r):
            continue
        accession = accession_from_link(r.get("link")) or accession_from_link(r.get("finalLink"))
        filed = str(r.get("filingDate") or "")[:10]
        key = accession or f"~unknown:{filed}"
        rank = (filed, str(r.get("acceptedDate") or ""), accession or "")
        order[key] = rank if key not in order else min(order[key], rank)
        position = normalize_cusip(r.get("securityCusip")) or f"sym:{sym}"
        value = _finite_or_zero(r.get("value"))
        shares = _finite_or_zero(r.get("sharesNumber") or r.get("shares"))
        bucket = groups.setdefault(key, {})
        seen = bucket.get(position)
        if seen is None:
            bucket[position] = {
                "symbol": sym,
                "name": r.get("securityName") or r.get("companyName") or sym,
                "value": value,
                "shares": shares,
            }
        else:
            seen["value"] += value
            seen["shares"] += shares
            if seen["name"] == sym:
                # A later row may carry the issuer name the first one lacked.
                seen["name"] = r.get("securityName") or r.get("companyName") or sym

    latest: Dict[str, Dict[str, Any]] = {}
    for key in sorted(order, key=lambda k: order[k]):   # oldest first: a later accession overwrites
        latest.update(groups[key])

    out: Dict[str, Dict[str, Any]] = {}
    for pos in latest.values():
        cur = out.get(pos["symbol"])
        if cur is None:
            out[pos["symbol"]] = dict(pos)
        else:
            cur["value"] += pos["value"]
            cur["shares"] += pos["shares"]
    for pos in out.values():
        pos["shares"] = int(pos["shares"])
    return out


def thirteen_f_holdings(raw: Any) -> List[Dict[str, Any]]:
    """One 13F filing's HOLDINGS: its share positions (`thirteen_f_share_positions`) with a
    positive value, one row per symbol, largest first (ties by ticker).

    Each row: ``ticker``, ``company_name``, ``logo_url`` (None — enrichment fills it),
    ``value``, ``shares``, ``allocation`` (% of the SHARE book, 2 dp) and ``change_percent``
    (0.0 — each writer sets it against the previous quarter). ``[]`` when no position has a
    positive value. Put/call and non-``SH`` rows are never part of a holding, its value or
    any allocation denominator; one row per symbol keeps `whale_holdings`' UNIQUE(whale_id,
    ticker), and a position cannot go negative, so allocation stays inside its 0..100 CHECK.
    Both holdings builders (`whale_service._build_holdings`,
    `hydrate_whales._build_13f_holdings`) return exactly this.
    """
    positions = [p for p in thirteen_f_share_positions(raw).values() if p["value"] > 0]
    total = sum(p["value"] for p in positions)
    if total <= 0:
        return []
    holdings = [
        {
            "ticker": p["symbol"],
            "company_name": p["name"],
            "logo_url": None,
            "value": p["value"],
            "shares": p["shares"],
            "allocation": round(p["value"] / total * 100, 2),
            "change_percent": 0.0,
        }
        for p in positions
    ]
    holdings.sort(key=lambda h: (-h["value"], h["ticker"]))
    return holdings


def thirteen_f_book_value(positions: Mapping[str, Mapping[str, Any]]) -> float:
    """The allocation denominator for a ``thirteen_f_share_positions`` map: the sum of its
    POSITIVE positions — the same total `thirteen_f_holdings` divides by, so a diff's
    ``previous_allocation`` / ``new_allocation`` match the holdings' allocations."""
    return sum(p["value"] for p in positions.values() if p["value"] > 0)


def thirteen_f_share_rows(raw: Any) -> List[Dict[str, Any]]:
    """``thirteen_f_share_positions`` as a row list (``symbol`` / ``value`` / ``shares``) for
    the split resolver (`thirteen_f_splits.resolve_13f_split_adjustments`), which must pick
    its split SUSPECTS from the same positions the diff then restates — a put row or one
    manager's slice would otherwise decide which tickers get a split lookup."""
    return list(thirteen_f_share_positions(raw).values())


def thirteen_f_raw_hash(
    raw: Any, *, basis: Optional[str] = None, previous_raw: Any = None,
) -> str:
    """The 13F snapshot's ``raw_hash``: the raw extract AND ``THIRTEEN_F_DIFF_VERSION`` —
    plus, from the writers, what the quarter was compared WITH.

    Both 13F writers stamp it; the hydrator skips a quarter whose stored hash is equal.
    Folding the version in is what makes a diff fix reach rows that were already derived.
    ``basis`` (``ThirteenFComparison.basis``) and the previous quarter's share positions
    make it re-derive when the comparison changes under an unchanged extract: FMP listing
    the adjacent quarter later (a confidential book disclosed, a late filing), or a
    13F-HR/A restating the previous quarter. The positions, not the raw rows, so FMP's row
    order and fields the diff never reads cannot churn the hash; values are whole dollars
    (float sums can differ in the last bit by row order, and the diff's floor is $1,000)."""
    payload = json.dumps(raw, sort_keys=True, default=str)
    head = f"13f-diff-v{THIRTEEN_F_DIFF_VERSION}"
    if basis is not None or previous_raw is not None:
        prev = sorted(
            (sym, int(p["shares"]), round(p["value"]))
            for sym, p in thirteen_f_share_positions(previous_raw).items()
        )
        head += f"\nbasis={basis or ''}\nprevious={json.dumps(prev)}"
    return hashlib.sha256(f"{head}\n{payload}".encode()).hexdigest()


# ── 13F comparison: which quarter the latest filing is diffed WITH ──────────────────────
#
# A 13F is diffed ONLY with the ADJACENT previous quarter — the rule the Trillion-Dollar
# Club builder already follows (`diff_13f_positions(comparison=...)`). Both whale writers
# used `whale_service._find_previous_quarter`, which returned the most recent EARLIER entry
# of FMP's `institutional-ownership/dates` list, so a quarter missing from that list booked
# every share change across the hole as trades of the latest quarter. Found 2026-10-09:
# Norges Bank files its Q1 and Q3 books under SEC confidential treatment
# (`isConfidentialOmitted`, a near-empty public table) and discloses each about a year later
# in a 13F-HR/A, so FMP lists neither while the Q2 / Q4 filing after it is the latest —
# 2026-Q2 was diffed with 2025-Q4 and six months of changes went out as Q2 trades, pushes
# and Home-card "adds". Owner decision 2026-10-09 (option A): a quarter with no adjacent
# previous quarter on file — a gap, or a first filing — writes NO trades; its holdings are
# shown, change_percent is not compared, and the summaries say so
# (`uncompared_13f_behavior` / `uncompared_13f_sentiment`). Measured with
# `scripts/measure_13f_quarter_gaps.py` (1 of 45 registry filers on 2026-10-09).

_QUARTER_END_MMDD = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}


def _previous_quarter(yq: Tuple[int, int]) -> Tuple[int, int]:
    year, quarter = yq
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def _period_label(yq: Tuple[int, int]) -> str:
    return f"{yq[0]}-Q{yq[1]}"


def _quarter_end(yq: Tuple[int, int]) -> date:
    month, day = (int(x) for x in _QUARTER_END_MMDD[yq[1]].split("-"))
    return date(yq[0], month, day)


def thirteen_f_dates_row_quarter(row: Any) -> Optional[Tuple[Tuple[int, int], str]]:
    """One ``institutional-ownership/dates`` row → ``((year, quarter), date)``, or None.

    ``year`` / ``quarter`` are FMP's own fields; a row missing them falls back to its
    ``date`` (``YYYY-MM-DD``, the quarter END). The writers used to default a missing
    quarter to 1 — a quarter nobody filed. ``date`` becomes the trade group's ``date`` (its
    key, compared as ``YYYY-MM-DD`` text by its readers), so it is returned in exactly that
    form — ``""`` when the row has none or it does not parse (``fromisoformat`` also takes
    ``20260630``, which would sort apart from every dashed key). A bool is not a number
    here. Never raises."""
    if not isinstance(row, Mapping):
        return None
    parsed_date = _parse_iso_date(str(row.get("date") or "").strip()[:10])
    filed = parsed_date.date().isoformat() if parsed_date is not None else ""
    y, q = row.get("year"), row.get("quarter")
    yq: Optional[Tuple[int, int]] = None
    if not isinstance(y, bool) and not isinstance(q, bool):
        try:
            yq = (int(y), int(q))
        except (TypeError, ValueError, OverflowError):
            yq = None
    if yq is None or yq[1] not in (1, 2, 3, 4) or not 1900 <= yq[0] <= 9998:
        if parsed_date is None or not 1900 <= parsed_date.year <= 9998:
            return None
        yq = (parsed_date.year, (parsed_date.month - 1) // 3 + 1)
    return yq, filed


@dataclass(frozen=True)
class ThirteenFComparison:
    """What a 13F filer's latest quarter is compared with (`select_13f_comparison`)."""

    latest: Tuple[int, int]                 # the newest quarter FMP lists
    latest_date: str                        # its listed `date`, else the quarter's last day
    comparison: str                         # COMPARISON_QUARTER | _GAP | _FIRST_FILING
    previous: Optional[Tuple[int, int]]     # the ADJACENT quarter; set only for _QUARTER
    older: Optional[Tuple[int, int]]        # the newest listed quarter before `latest`
    skipped_rows: int = 0                   # dates rows with no usable quarter

    @property
    def compared(self) -> bool:
        return self.comparison == COMPARISON_QUARTER

    @property
    def period(self) -> str:
        """``"2026-Q2"`` — the `whale_filing_snapshots.filing_period` of the latest quarter."""
        return _period_label(self.latest)

    @property
    def adjacent(self) -> Tuple[int, int]:
        """The quarter before ``latest``, listed or not."""
        return _previous_quarter(self.latest)

    @property
    def basis(self) -> str:
        """What the derivation compared with, for `thirteen_f_raw_hash`: the hash changes
        when FMP lists the adjacent quarter later, or a first filing gains an older one."""
        if self.comparison == COMPARISON_QUARTER and self.previous is not None:
            return f"{COMPARISON_QUARTER}:{_period_label(self.previous)}"
        return self.comparison


def select_13f_comparison(
    filing_dates: Any, *, today: Optional[date] = None,
) -> Optional[ThirteenFComparison]:
    """Decide a filer's latest 13F quarter and what it is compared with, from FMP's
    ``institutional-ownership/dates`` list. ``None`` when no row names a usable quarter.

    * ``latest`` is the NEWEST listed quarter — never ``dates[0]`` on trust (the writers
      used to take the list order as given). A quarter that has not ENDED by ``today``
      (UTC) is skipped as unusable: no 13F can describe it, and under "newest wins" one
      such row would otherwise stand in for the real latest filing;
    * ``quarter`` when the ADJACENT previous quarter is listed — the only comparison that
      may produce trades;
    * ``gap`` when only older quarters are listed, ``first_filing`` when none is: both mean
      NOT COMPARED (no trades, no change_percent), exactly as `diff_13f_positions` reads
      them. Rows repeating a quarter keep the first listed ``date``.
    """
    today = today or datetime.now(timezone.utc).date()
    listed: Dict[Tuple[int, int], str] = {}
    skipped = 0
    for row in filing_dates if isinstance(filing_dates, (list, tuple)) else ():
        parsed = thirteen_f_dates_row_quarter(row)
        if parsed is None or _quarter_end(parsed[0]) > today:
            skipped += 1
            continue
        yq, filed = parsed
        if not listed.get(yq):
            listed[yq] = filed
    if not listed:
        return None
    latest = max(listed)
    adjacent = _previous_quarter(latest)
    older = max((yq for yq in listed if yq < latest), default=None)
    if adjacent in listed:
        comparison, previous = COMPARISON_QUARTER, adjacent
    else:
        comparison = COMPARISON_GAP if older is not None else COMPARISON_FIRST_FILING
        previous = None
    latest_date = listed[latest] or f"{latest[0]}-{_QUARTER_END_MMDD[latest[1]]}"
    return ThirteenFComparison(
        latest=latest, latest_date=latest_date, comparison=comparison,
        previous=previous, older=older, skipped_rows=skipped,
    )


def uncompared_13f_note(selection: ThirteenFComparison) -> str:
    """Why a quarter's changes are not shown — the clause both writers' summaries end on."""
    if selection.comparison == COMPARISON_GAP:
        missing = filing_period_display(_period_label(selection.adjacent))
        return f"no 13F holdings are on file for {missing}, the quarter before"
    return "this is the first 13F on file"


def uncompared_13f_behavior(sectors: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    """``behavior_summary`` for a quarter that was NOT compared with the one before.

    iOS renders "This whale is currently <action> <primaryFocus> and <secondaryAction>
    <secondaryFocus>". The no-trades text ("Holding existing positions and Maintaining
    portfolio allocation") claims nothing changed, which is false when nothing was
    compared — this one describes the holdings only. "Holding" is a directionless verb,
    so iOS paints it neutral."""
    top = ""
    if sectors and isinstance(sectors[0], Mapping):
        top = str(sectors[0].get("name") or "").strip()
    return {
        "action": "Holding",
        "primaryFocus": "its disclosed positions",
        "secondaryAction": "Concentrated in",
        "secondaryFocus": top.lower() if top else "various sectors",
    }


def uncompared_13f_sentiment(
    holdings: Sequence[Mapping[str, Any]],
    sectors: Sequence[Mapping[str, Any]],
    selection: ThirteenFComparison,
) -> str:
    """``sentiment_text`` for a quarter that was NOT compared with the one before: the
    holdings as filed, then why no change is shown — never "stable positioning with no
    significant changes", which is a claim about a comparison that was not made. Shared
    by both writers so the live profile and the nightly sweep store the same text (no
    model call: there is no activity for one to describe)."""
    tickers = ", ".join(
        str(h.get("ticker")) for h in list(holdings)[:5]
        if isinstance(h, Mapping) and h.get("ticker")
    )
    top = ""
    if sectors and isinstance(sectors[0], Mapping):
        top = str(sectors[0].get("name") or "").strip()
    first = f"Portfolio concentrated in {top or 'various sectors'}"
    first += f" with top positions in {tickers}." if tickers else "."
    return f"{first} Changes since the previous quarter are not shown: {uncompared_13f_note(selection)}."


def generate_trade_summary(
    buys: List[Dict], sells: List[Dict], net_action: str
) -> str:
    """One-line trade group summary. Rendered verbatim under the trade-group card.

    ⚠️ Order matters, and the old order made two branches unreachable: with
    ``sells == []`` the first test is ``len(buys) > 0``, which any single buy satisfies,
    so "Pure buying activity" could never be reached and a lone purchase was announced
    as "Heavy accumulation with 1 buys" — wrong in register AND ungrammatical. The
    one-sided cases are therefore checked FIRST, and the ratio tests now require enough
    trades for "heavy" to mean something.
    """
    n_buys, n_sells = len(buys), len(sells)

    if n_buys and not n_sells:
        return f"Pure buying activity with {n_buys} {positions_word(n_buys)}"
    if n_sells and not n_buys:
        return f"Pure selling activity with {n_sells} {positions_word(n_sells)}"
    if n_buys >= 3 and n_buys > n_sells * 2:
        return f"Heavy accumulation with {n_buys} buys"
    if n_sells >= 3 and n_sells > n_buys * 2:
        return f"Significant reduction with {n_sells} sells"
    return "Portfolio rebalancing"


def positions_word(n: int) -> str:
    """"position" / "positions" — a count of 1 must not read "1 positions"."""
    return "position" if n == 1 else "positions"


# ── 13F position diff by CUSIP, share counts only (Trillion-Dollar Club) ─────────────
#
# The whale `_diff_quarters` copies answer "what did this INVESTOR trade" in dollars
# (BOUGHT/SOLD). A company's 13F needs a different, stricter answer: which positions are
# newly reported, gone, up or down in SHARES — never a dollar "trade", and never "bought":
# most of Q2 2026's new rows were IPO conversions (SpaceX listed 2026-06-12 and appeared
# at Alphabet, NVIDIA and AMD). The split machinery is REUSED from above, not copied:
# `restate_prev_shares_for_split`, `SPLIT_SUPPRESS`, `is_implausible_share_flow`.

_DIFF_CUSIP_RE = re.compile(r"^[0-9A-Z]{9}$")
# Order the change rows are listed in (unchanged rows are counted, never listed).
_DIFF_ROW_ORDER = (
    "newly_reported", "increased", "decreased", "no_longer_reported", "corporate_action",
)
_DIFF_COMPARISONS = ("quarter", "first_filing", "gap")


def _diff_finite(value: Any, *, positive: bool) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):   # float(10**400) overflows: garbage
        return None
    if not math.isfinite(f):
        return None
    if positive and f <= 0:
        return None
    return f


def _diff_iso_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _diff_positions(rows: Optional[Sequence[Dict[str, Any]]], side: str) -> Dict[str, Dict[str, Any]]:
    """Validated positions keyed by CUSIP. A row without a CUSIP or a positive, finite
    share count cannot be compared and is skipped (logged). Duplicate CUSIPs — which the
    builder's per-accession normalisation should already have merged — are summed."""
    out: Dict[str, Dict[str, Any]] = {}
    skipped = duplicates = 0
    for r in rows or ():
        if not isinstance(r, dict):
            skipped += 1
            continue
        raw_cusip = r.get("cusip")
        cusip = raw_cusip.strip().upper() if isinstance(raw_cusip, str) else ""
        shares = _diff_finite(r.get("shares"), positive=True)
        if not _DIFF_CUSIP_RE.match(cusip) or shares is None:
            skipped += 1
            continue
        raw_symbol = r.get("symbol")
        symbol = raw_symbol.strip().upper() if isinstance(raw_symbol, str) else ""
        entry = {
            "cusip": cusip,
            "symbol": symbol if symbol and symbol != "--" else None,
            "name": (r.get("name") or "").strip() if isinstance(r.get("name"), str) else "",
            "shares": shares,
            "value": _diff_finite(r.get("value"), positive=False),
            "weight": _diff_finite(r.get("weight"), positive=False),
            "ipo_date": _diff_iso_date(r.get("ipo_date")),
        }
        seen = out.get(cusip)
        if seen is None:
            out[cusip] = entry
            continue
        duplicates += 1
        seen["shares"] += shares
        for k in ("value", "weight"):
            if seen[k] is not None and entry[k] is not None:
                seen[k] += entry[k]
            else:
                seen[k] = seen[k] if seen[k] is not None else entry[k]
        seen["symbol"] = seen["symbol"] or entry["symbol"]
        seen["name"] = seen["name"] or entry["name"]
    if skipped:
        logger.warning(
            "diff_13f_positions: skipped %d %s row(s) with no CUSIP or no positive, finite "
            "share count", skipped, side,
        )
    if duplicates:
        logger.warning(
            "diff_13f_positions: %d duplicate CUSIP row(s) in the %s filing were summed — "
            "the caller should normalise per accession first", duplicates, side,
        )
    return out


def _diff_split_ratio(split_ratios: Mapping[str, float], *symbols: Optional[str]) -> Optional[float]:
    for s in symbols:
        if not s:
            continue
        r = _diff_finite((split_ratios or {}).get(s), positive=True)
        if r is not None and abs(r - 1.0) > 1e-9:
            return r
    return None


def _diff_both(
    c: Dict[str, Any], p: Dict[str, Any], split_ratios: Mapping[str, float],
    unclassified: Set[str],
) -> Tuple[str, float, Optional[float]]:
    """``(change, prev_shares, share_change)`` for a position present in both quarters."""
    prev_shares = p["shares"]
    ratio = _diff_split_ratio(split_ratios, c["symbol"], p["symbol"])
    if ratio is not None:
        restated = restate_prev_shares_for_split(prev_shares, c["shares"], ratio)
        if restated is SPLIT_SUPPRESS:
            # The split and the real flow cannot be separated: say so, never guess.
            return "corporate_action", prev_shares, None
        prev_shares = restated
    share_change = c["shares"] - prev_shares
    flagged = any(s in (unclassified or set()) for s in (c["symbol"], p["symbol"]) if s)
    if flagged and is_implausible_share_flow(share_change, c["shares"]):
        return "corporate_action", prev_shares, None
    # Under one share is fractional-share noise from a restated count (a 3:2 split of an
    # odd holding), not a change — raw 13F counts are whole numbers.
    if abs(share_change) < 1.0:
        return "unchanged", prev_shares, 0.0
    return ("increased" if share_change > 0 else "decreased"), prev_shares, share_change


def diff_13f_positions(
    curr: Sequence[Dict[str, Any]],
    prev: Optional[Sequence[Dict[str, Any]]],
    *,
    split_ratios: Mapping[str, float],
    unclassified: Set[str],
    comparison: str,
    prev_ipo_cutoff: Optional[date],
    prev_period: Optional[str] = None,
) -> Dict[str, Any]:
    """Quarter-over-quarter SHARE changes between two 13F filings, keyed by CUSIP.

    ``curr`` / ``prev`` are normalised holdings (``cusip``, ``symbol``, ``name``,
    ``shares``, ``value``, ``weight``, ``ipo_date``), one per CUSIP. ``split_ratios`` and
    ``unclassified`` are SYMBOL-keyed (``thirteen_f_splits.resolve_13f_split_adjustments``)
    and are looked up through each row's symbol. ``comparison`` is decided by the caller,
    explicitly — ``"quarter"`` only when ``prev`` IS the adjacent quarter:

    * ``"first_filing"`` / ``"gap"`` — nothing to compare: ``rows`` is ``[]`` and every
      count is 0 (a non-adjacent quarter would book months of history as one change).
      ⚠️ Those zeros mean NOT COMPARED, not "no changes". A reader must key off
      ``comparison`` and show counts, per-holding change labels or "no share-count
      changes" ONLY for ``"quarter"`` — a ``"gap"`` quarter rendered as "unchanged" is a
      false statement about share counts that were never compared. ``prev_period`` is
      whatever the caller passed for a gap (``None`` when it only knew that an older
      filing exists), and always ``None`` for a first filing.
    * ``"quarter"`` — every CUSIP is classified ``newly_reported`` (with ``newly_listed``
      when its ``ipo_date`` is after ``prev_ipo_cutoff``, i.e. it went public after the
      previous period ended; unknown ``ipo_date`` -> never ``newly_listed``),
      ``no_longer_reported``, ``increased``, ``decreased``, ``unchanged`` or
      ``corporate_action`` (a split the counts cannot be separated from, or an implausible
      move next to an unclassified corporate action). A second pass joins a lone
      "no longer reported" row and a lone "newly reported" row that share a symbol — a
      CUSIP re-key, not an exit and an entry.

    Returns the ``trillion_club_filings.changes`` object: ``comparison``, ``prev_period``,
    ``counts`` (all six outcomes) and ``rows`` (never ``unchanged``). Raises ``ValueError``
    for an unknown ``comparison`` or for ``"quarter"`` without a usable previous filing or
    current filing — an empty side would book the whole book as new, or as gone.
    """
    if comparison not in _DIFF_COMPARISONS:
        raise ValueError(f"diff_13f_positions: unknown comparison {comparison!r}")
    counts = {k: 0 for k in (
        "newly_reported", "increased", "decreased", "no_longer_reported", "unchanged",
        "corporate_action",
    )}
    if comparison != "quarter":
        return {
            "comparison": comparison,
            "prev_period": None if comparison == "first_filing" else prev_period,
            "counts": counts,
            "rows": [],
        }

    cur_pos = _diff_positions(curr, "current")
    prev_pos = _diff_positions(prev, "previous")
    if not cur_pos or not prev_pos:
        raise ValueError(
            "diff_13f_positions: comparison='quarter' needs usable positions on both sides "
            f"(current={len(cur_pos)}, previous={len(prev_pos)}) — an empty side would "
            "book the whole filing as newly reported or as no longer reported"
        )

    # outcome per CUSIP: (change, row-dict, sort value)
    outcome: Dict[str, Tuple[str, Dict[str, Any], float]] = {}

    def _row(c: Optional[Dict[str, Any]], p: Optional[Dict[str, Any]], change: str,
             prev_shares: Optional[float], share_change: Optional[float]) -> Dict[str, Any]:
        src = c or p
        newly_listed = bool(
            change == "newly_reported" and c is not None and c["ipo_date"] is not None
            and prev_ipo_cutoff is not None and c["ipo_date"] > prev_ipo_cutoff
        )
        return {
            "cusip": src["cusip"],
            "symbol": (c and c["symbol"]) or (p and p["symbol"]) or None,
            "name": (c and c["name"]) or (p and p["name"]) or "",
            "change": change,
            "newly_listed": newly_listed,
            "shares": c["shares"] if c else None,
            "prev_shares": prev_shares,
            "share_change": share_change,
            "value": c["value"] if c else None,
            "weight": c["weight"] if c else None,
        }

    def _sort_value(c: Optional[Dict[str, Any]], p: Optional[Dict[str, Any]]) -> float:
        for side in (c, p):
            if side is not None and side["value"] is not None:
                return side["value"]
        return 0.0

    for cusip in set(cur_pos) | set(prev_pos):
        c, p = cur_pos.get(cusip), prev_pos.get(cusip)
        if c is not None and p is not None:
            change, prev_shares, share_change = _diff_both(c, p, split_ratios, unclassified)
            outcome[cusip] = (change, _row(c, p, change, prev_shares, share_change), _sort_value(c, p))
        elif c is not None:
            outcome[cusip] = ("newly_reported", _row(c, None, "newly_reported", None, None), _sort_value(c, None))
        else:
            outcome[cusip] = ("no_longer_reported", _row(None, p, "no_longer_reported", p["shares"], None), _sort_value(None, p))

    # Second pass: a CUSIP re-key (same symbol, one row gone, one row new) is one position.
    new_by_sym: Dict[str, List[str]] = {}
    gone_by_sym: Dict[str, List[str]] = {}
    for cusip, (change, row, _v) in outcome.items():
        if row["symbol"] and change == "newly_reported":
            new_by_sym.setdefault(row["symbol"], []).append(cusip)
        elif row["symbol"] and change == "no_longer_reported":
            gone_by_sym.setdefault(row["symbol"], []).append(cusip)
    for sym, new_cusips in new_by_sym.items():
        gone_cusips = gone_by_sym.get(sym, [])
        if len(new_cusips) != 1 or len(gone_cusips) != 1:
            if gone_cusips:
                logger.warning(
                    "diff_13f_positions: symbol %s has %d new and %d gone CUSIP(s) — "
                    "ambiguous, not joined as a re-key", sym, len(new_cusips), len(gone_cusips),
                )
            continue
        nc, gc = new_cusips[0], gone_cusips[0]
        c, p = cur_pos[nc], prev_pos[gc]
        change, prev_shares, share_change = _diff_both(c, p, split_ratios, unclassified)
        logger.info(
            "diff_13f_positions: joined %s CUSIP re-key %s -> %s as %s", sym, gc, nc, change,
        )
        del outcome[gc]
        outcome[nc] = (change, _row(c, p, change, prev_shares, share_change), _sort_value(c, p))

    rows: List[Tuple[int, float, str, Dict[str, Any]]] = []
    for cusip, (change, row, sort_value) in outcome.items():
        counts[change] += 1
        if change != "unchanged":
            rows.append((_DIFF_ROW_ORDER.index(change), -sort_value, cusip, row))
    rows.sort(key=lambda t: (t[0], t[1], t[2]))
    return {
        "comparison": comparison,
        "prev_period": prev_period,
        "counts": counts,
        "rows": [r for *_k, r in rows],
    }

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


# ── Activity / dormancy disclosure ───────────────────────────────────────────
#
# A tracked filer can stop producing data at any time: a fund deregisters or drops below
# the $100M 13F threshold, a politician retires or simply stops trading. Without a signal
# the app renders that as a BROKEN screen — Michael Burry's profile served a confident
# $1.37B portfolio and +11.06% return next to zero holdings and zero trades, with the
# only hint being a "Q3 2025" tile caption.
#
# ⚠️ TWO RULES THIS MODULE EXISTS TO ENFORCE.
#
# 1. 13F staleness is counted in MISSED FILING QUARTERS, never in days. A 13F is due 45
#    days after quarter end, so EVERY healthy filer is ~51 days stale the moment a
#    quarter closes — a day-based threshold flags all 45 of them. `latest_filed_13f_quarter`
#    already encodes the lag; this reuses it rather than doing new date maths.
#
# 2. Congress is NOT 13F. A member who does not trade files nothing, so silence is not
#    evidence of retirement. `ACTIVITY_DORMANT` is unreachable for a congressional filer
#    by construction — the strongest thing we may say about a sitting senator is the DATE
#    of their last disclosure.
ACTIVITY_CURRENT = "current"      # filing on the expected cadence
ACTIVITY_LATE = "late"            # 13F only: missed exactly 1 expected quarter
ACTIVITY_DORMANT = "dormant"      # 13F only: missed >= 2 — has stopped filing
ACTIVITY_QUIET = "quiet"          # congress only: nothing disclosed in >= QUIET_DAYS
ACTIVITY_NONE = "none"            # nothing has ever been disclosed
ACTIVITY_UNKNOWN = ""             # not computed / legacy row -> render nothing

# A congressional filer is "quiet" only after ~6 months. Deliberately generous: the STOCK
# Act requires a report within 30-45 days OF A TRADE, so a long gap is ordinary for a
# member who does not trade much, and calling that "inactive" would be a false statement
# about a real named person.
CONGRESS_QUIET_DAYS = 180

DATA_SOURCE_13F = "13f"


@dataclass(frozen=True)
class Activity:
    """Whether a filer is still producing data, and the sentence that says so.

    Mirrors `AnnualReturn`: the provenance travels with the claim so a caller cannot
    render the status and the caption from two different computations.
    """

    status: str                     # one of the ACTIVITY_* constants
    label: str                      # user-facing sentence; "" when nothing to say
    as_of: Optional[str]            # the date/quarter the label refers to, or None

    @property
    def is_current(self) -> bool:
        return self.status in (ACTIVITY_CURRENT, ACTIVITY_UNKNOWN)

    @property
    def needs_disclosure(self) -> bool:
        """True when the UI should show a chip/notice at all."""
        return self.status not in (ACTIVITY_CURRENT, ACTIVITY_UNKNOWN)


def _quarters_between(newer: Tuple[int, int], older: Tuple[int, int]) -> int:
    """Signed count of calendar quarters from `older` to `newer`. Handles year rollover."""
    return (newer[0] - older[0]) * 4 + (newer[1] - older[1])


def compute_activity(
    data_source: Optional[str],
    last_filing_period: Optional[str] = None,
    last_activity_date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Activity:
    """Classify a filer's activity from PERSISTED data only.

    ⚠️ Deliberately takes no live probe result. Every FMP method in `integrations/fmp.py`
    swallows its exception and returns `[]`, so "empty" is indistinguishable from a 429,
    a plan downgrade, a timeout, or an unknown CIK — and `_fetch_congress_pages` will even
    return a partially truncated list with no error. A "N consecutive empty probes ⇒
    dormant" rule would therefore mark real whales dormant during an FMP outage. What we
    already HOLD cannot be corrupted by an outage, so that is what this reads.

    ``last_filing_period`` is a 13F quarter (``"2026-Q2"``); congressional snapshots key on
    a wall-clock month (``"2026-08"``) and must NOT parse as a quarter, or a politician
    would be rendered as having filed a 13F they never file.
    ``last_activity_date`` is ``MAX(whale_trade_groups.date)``: for a congressional filer
    the disclosure date, for a 13F filer the QUARTER END the filing reports (FMP's
    institutional-ownership ``date``) — never the day the 13F was filed.
    """
    # `str(...)` before `.strip()`: this reads a Supabase row where a column could be any
    # JSON scalar, and `(123 or "").strip()` is an AttributeError, not a fallback.
    is_13f = str(data_source or "").strip().lower() == DATA_SOURCE_13F

    if is_13f:
        quarter = _parse_filing_period(last_filing_period)
        if quarter is None:
            # No usable quarter on file. Fall through to the date-based statement rather
            # than claiming dormancy we cannot evidence.
            return _activity_from_date(last_activity_date, now, is_13f=True)
        expected = latest_filed_13f_quarter(now=now)
        behind = _quarters_between(expected, quarter)
        as_of = filing_period_display(last_filing_period or "") or None
        if behind <= 0:
            return Activity(ACTIVITY_CURRENT, "", as_of)
        if behind == 1:
            # One quarter can simply be a late filer or an NT 13F; it is not evidence
            # that they have stopped.
            return Activity(ACTIVITY_LATE, f"Last filed {as_of}" if as_of else "", as_of)
        return Activity(ACTIVITY_DORMANT, f"Last filed {as_of}" if as_of else "", as_of)

    return _activity_from_date(last_activity_date, now, is_13f=False)


def _activity_from_date(
    last_activity_date: Optional[str], now: Optional[datetime], *, is_13f: bool
) -> Activity:
    """Date-based classification, used for congress and as the 13F fallback."""
    parsed = _parse_iso_date(last_activity_date)
    if parsed is None:
        return Activity(
            ACTIVITY_NONE,
            "No filings on record" if is_13f else "No trades disclosed yet",
            None,
        )
    ref = (now or datetime.now(timezone.utc))
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    days = (ref - parsed).days
    pretty = parsed.strftime("%b %Y")
    if days < CONGRESS_QUIET_DAYS:
        return Activity(ACTIVITY_CURRENT, "", pretty)
    # NOT "dormant" and NOT "inactive": a member who does not trade files nothing, and
    # this says nothing about whether they still hold office.
    return Activity(ACTIVITY_QUIET, f"No trades disclosed since {pretty}", pretty)


def _parse_filing_period(period: Optional[str]) -> Optional[Tuple[int, int]]:
    """``"2026-Q2"`` -> ``(2026, 2)``. Anything else -> None (incl. congress's ``YYYY-MM``)."""
    if not isinstance(period, str):
        return None
    m = _FILING_PERIOD_RE.match(period.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    """``"2026-06-30"`` -> an aware datetime, or None. Never raises."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip()[:10])
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc)


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
