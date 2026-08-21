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
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

# The 13F quarter helpers are the SINGLE source of truth for "which quarter should a
# filer have filed by now" — `latest_filed_13f_quarter` already encodes the statutory
# 45-day lag. Imported rather than re-derived: a second copy of that arithmetic is
# exactly how the two 13F diff paths drifted apart before.
from app.utils.period_labels import (
    _FILING_PERIOD_RE,
    filing_period_display,
    latest_filed_13f_quarter,
)


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

    action = "BOUGHT" if shares_change > 0 else "SOLD"
    return (action, amount)


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
    ``last_activity_date`` is ``MAX(whale_trade_groups.date)`` — the one field whose
    meaning is consistent across both sources (13F filing date / congressional disclosure).
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
