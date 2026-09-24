"""
Signals Service — builds the Home "App-Exclusive Signals" section
(``HomeDashboardView``): four "signals you won't find on free trackers" cards.

  • Congressional Buys   — most-bought tickers on Capitol Hill (distinct MEMBERS
                           who bought), windowed on the DISCLOSURE date (filings
                           lag 30–45 days, so "this week" = what was just filed).
                           Source: FMP ``senate-latest`` + ``house-latest``.
  • Whale Accumulation   — tickers the 13F whale registry is adding to (distinct
                           FUNDS, deduped by CIK — the registry double-lists a
                           person and their fund on ONE CIK). Source: the
                           daily-hydrated Supabase whale tables (no FMP calls).
  • Earnings Shockers    — biggest EPS beats/misses vs the Street (signed
                           surprise %). Source: FMP ``earnings-calendar``.
  • CEO Buys             — chief executives buying their OWN stock on the open
                           market (Form 4 P-Purchase, common stock), ranked by total
                           DOLLARS bought in the last 30 days of FILINGS (one CEO per
                           company, so a buyer count would be degenerate). Source:
                           FMP ``insider-trading/search`` with no symbol (market-wide,
                           fail-closed pager), gated like Earnings Shockers (NASDAQ/
                           NYSE/AMEX + $250M) plus a price-plausibility band.

Contract (mirrors the Daily Scanners): the backend emits only ranked DATA rows +
raw numbers; the iOS repository supplies the fixed per-card chrome and formats
the display strings. See ``schemas/home_dashboard.py`` (SignalRowResponse etc.).

Caching (CLAUDE.md invariant 4): a 45-min in-memory tier + an ``_inflight`` dedup
future (collapses concurrent cold builds into ONE fetch) + a 24-hour Supabase
``signals_cache`` Tier-2 (survives restarts; the sources move daily/quarterly).
This service rides inside ``get_dashboard()`` and its existing pre-warm loop.

Degradation (CLAUDE.md loud-failure rule): every branch degrades independently —
one source failing → that card is ``None`` (iOS omits it), the others still
render, the dashboard still returns 200. Nothing here ever raises to the caller
(``get_signals`` swallows to empty; ``get_signals_guarded`` also bounds latency).

A branch that FAILED (raised) is different from a branch that is honestly empty,
and the cache treats them differently (developer decision 2026-09-23): a build in
which any branch raised is kept in memory for only ``_SIGNALS_DEGRADED_TTL_SECONDS``
and is NEVER written to the 24 h Supabase tier. Tier 2 is read before any rebuild,
so a persisted partial build used to hide a transiently-failed card for up to a
day. So a branch reports failure by RAISING; ``None`` means "nothing qualified".
"""

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, List, NamedTuple, Optional, Set, Tuple

import re

from app.utils.postgrest_paging import fetch_all_rows
from app.database import get_supabase
from app.integrations.fmp import (
    get_fmp_client,
    FMPClient,
    FMPNotEntitledException,
    FMPUnavailableException,
)
from app.services.earnings_service import _compute_surprise
# Reuse the dashboard's hardened primitives so signals fold class-share variants
# (BRK.B ↔ BRK-B) and reject NaN/Inf exactly like the scanners do. NOTE: the
# dashboard service must import THIS module function-locally to avoid a cycle.
from app.services.home_dashboard_service import (
    _canonical_symbol,
    _finite_float,
    _MOVERS_EXCHANGES,
)
from app.services._whale_common import (
    parse_congress_amount_bounds,
    format_amount_range,
)
from app.services._insider_common import (
    ceo_role_label,
    classify_insider_transaction,
    is_ceo_role,
    is_common_stock,
    normalize_insider_name,
)
from app.schemas.home_dashboard import (
    SignalsGroupResponse,
    SignalGroupResponse,
    SignalRowResponse,
)
from app.schemas.signals_detail import (
    SignalTickerDetailResponse,
    SignalHolderResponse,
)
from pydantic import ValidationError
from app.services.price_service import price_source

logger = logging.getLogger(__name__)


# ── Config ─────────────────────────────────────────────────────────────
_SIGNALS_MEM_TTL_SECONDS = 2700          # 45 min in-memory freshness ceiling
_SIGNALS_SUPABASE_TTL_HOURS = 24         # Tier-2 survives restart; sources daily/quarterly
_SIGNALS_CACHE_KEY = "signals_v4"        # bump to invalidate stale rows on a semantics change —
                                         # v4 (2026-09-23) added the `ceo` card: a v3 row validates
                                         # (the field is optional) but would hide CEO Buys for up to
                                         # its 24h TTL after a deploy. The old row is simply ignored.
_SIGNALS_DEGRADED_TTL_SECONDS = 300      # a build where a branch RAISED: memory only, 5 min, never
                                         # persisted — so the failed card comes back on the next
                                         # rebuild instead of being pinned for 24h by Tier 2.
_SIGNALS_TABLE = "signals_cache"
_SIGNALS_BUILD_TIMEOUT_SECONDS = 8       # never let a cold build block the dashboard

_SIGNAL_ROWS = 10                        # drill-down leaders per card (iOS scrolls the
                                         # expanded list in a bounded box past ~6 rows)

# Congress
# Window on DISCLOSURE date. 30 days (not 14): congressional filings lag 30-45 days
# and cluster, so a 2-week window almost never accumulates ≥2 members on one ticker
# (verified against live data — a 14d window left every ticker at 1 member and hid
# the card). 30d reliably surfaces the mega-caps (AAPL/GOOGL/MSFT ≈ 3 members) while
# staying "this month" fresh. Keep the iOS subtitle in sync with this span.
_CONGRESS_WINDOW_DAYS = 30
_CONGRESS_MIN_MEMBERS = 2                # a "most-bought" headline needs > 1 member

# Whale
_WHALE_MIN_FUNDS = 2                     # a "funds loading up" headline needs > 1 fund

# Earnings
_EARNINGS_WINDOW_DAYS = 7                 # bound staleness to ~the past week (was 10); ranking is
                                          # recency-first so fresher reports always lead the card.
_EARNINGS_MIN_ABS_SURPRISE = 10.0        # |surprise%| ≥ 10 to count as a "shocker"
_EARNINGS_MAX_ABS_SURPRISE = 1000.0      # garbage sanity bound (display is capped on iOS at ±200)
_EARNINGS_MIN_ABS_ESTIMATE = 0.05        # skip near-zero estimates: (actual-est)/|est| explodes when
                                          # est≈0.01 (penny-EPS artifact); a real low bar like NKE's
                                          # $0.11 estimate still clears.
_EARNINGS_QUOTE_CANDIDATES = 40          # over-fetch (was 25) so the exchange + $250M gate below does
                                          # not starve the final list of real large-cap shockers.
_EARNINGS_MIN_MARKET_CAP = 250_000_000   # $250M quality floor (parity with the scanner cards)

# CEO Buys (home E2, 2026-09-23)
_CEO_WINDOW_DAYS = 30                    # FILING-date window: the market learns of a buy when it is
                                         # filed, the pager is ordered by filing date, and an amended
                                         # 4/A re-files under the OLD trade date (parity with congress)
_CEO_FUTURE_SKEW_DAYS = 2                # tolerate a filing dated slightly ahead (parity with congress)
_CEO_MAX_FILING_LAG_DAYS = 30            # trade→filing lag beyond this = a late filing of an old trade,
                                         # not "this month's" buying (Form 4 is due in 2 business days)
_CEO_MIN_TICKER_DOLLARS = 100_000.0      # parity with smart_money_sender.MIN_INSIDER_AMOUNT: live, most
                                         # CEO buys are token $5-20K director-style purchases
_CEO_MAX_ROW_DOLLARS = 5_000_000_000.0   # GARBAGE bound only — a real ~$1B CEO open-market buy exists
                                         # (Sep 2025). Unit errors are caught by three narrower
                                         # checks: the price band (price), `securitiesOwned` (shares)
                                         # and the market-cap share below (the product).
_CEO_PRICE_BAND = 10.0                   # a row's PRICE must sit within ref/10 … ref*10 of the live
                                         # quote (catches cents-for-dollars errors; it cannot see a
                                         # wrong share count — the price is unchanged by one)
_CEO_MAX_MCAP_SHARE = 0.10               # one row's dollars above 10% of the issuer's market cap is
                                         # not a CEO buying stock on the open market; it is a unit error
_CEO_QUOTE_CANDIDATES = 40               # over-fetch before the exchange + $250M gate (like earnings)
_CEO_FETCH_PAGE_SIZE = 1000              # verified live: 1000 rows ≈ 14 days of market-wide P rows
_CEO_FETCH_MAX_PAGES = 10                # 30 days ≈ 3 pages today; the cap RAISES (window uncovered)
_CEO_DETAIL_PAGE_SIZE = 1000             # the per-ticker feed is EVERY insider's buys, not just the
_CEO_DETAIL_MAX_PAGES = 5                # CEO's: 100 × 5 overflowed on a busy name and emptied the
                                         # drill-down beside a card showing "$X bought" (review 2026-09-23)
# Our ticker grammar after `_canonical_symbol` (BRK.B → BRK-B). ≤ 11 chars, inside the
# drill-down endpoint's 12-char limit, so every card ticker is tappable.
_CEO_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,6}(-[A-Z0-9]{1,3})?$")

_BAD_SYMBOLS = {"", "--", "N/A", "NA", "NONE"}

# The cards, in one place: `_build`'s gather, its failure set and the logs all come from this.
_SIGNAL_STEPS: Tuple[str, ...] = ("congress", "whale", "earnings", "ceo")

# Per-ticker drill-down (tap a signal ticker → who bought it). On-demand, so a
# short in-memory tier + inflight dedup is enough (no Supabase tier).
_DETAIL_TTL_SECONDS = 600                 # 10 min
_DETAIL_ROWS = 25                         # cap the holder list (screen scrolls)


def _norm_name(name: Any) -> str:
    """Normalized name key for matching a congress member to the registry.

    Lowercase, strip punctuation, collapse whitespace — but ORDER-PRESERVING (NOT
    token-sorted). Sorting tokens made "Robert J. Smith" and "J. Robert Smith"
    collide, which could deep-link a tap to the WRONG politician's profile. FMP
    rows and the registry both use "First Last" order, so an order-sensitive key
    matches them without that collision. A miss just leaves the row non-tappable
    (still shows the trade), never an error."""
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", str(name or "").lower()).split())


def _congress_role(district: str, chamber: str) -> str:
    """Format a member's role for display (mirrors holders_service._format_district):
    "Senator (KY)" / "Representative (TX-11)"."""
    if not district:
        return "Senator" if chamber == "senate" else "Representative"
    if chamber == "senate":
        return f"Senator ({district})"
    m = re.match(r"([A-Za-z]{2})(\d+)", district)
    if m:
        return f"Representative ({m.group(1).upper()}-{m.group(2)})"
    return f"Representative ({district})"


def _whale_row_rank(r: SignalHolderResponse) -> Tuple[float, float, str]:
    """Sort key for whale drill-down rows (smaller = stronger): $ est desc (nulls
    last), then allocation desc, then name asc. Also used to pick the best row per
    CIK when a fund is registered under both a person and a firm name."""
    return (
        -(r.amount_est if r.amount_est is not None else -1.0),
        -(r.allocation_percent or 0.0),
        r.name,
    )


# ── Pure helpers (unit-tested without network / Supabase) ──────────────


def _parse_iso_date(s: Any) -> Optional[datetime]:
    """Parse a ``YYYY-MM-DD`` prefix into an aware UTC datetime; ``None`` on failure."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _congress_member_key(row: Dict[str, Any], chamber: str) -> str:
    """Stable per-member identity so a member who files several buys of the same
    ticker is counted ONCE. Name-based (chamber-scoped), with an ``office``/
    ``district`` fallback. ``""`` when the member can't be identified (row skipped)."""
    last = (row.get("lastName") or row.get("last_name") or "").strip().lower()
    first = (row.get("firstName") or row.get("first_name") or "").strip().lower()
    if last or first:
        return f"{chamber}|{last}|{first}"
    office = (
        row.get("office") or row.get("representative") or row.get("senator")
        or row.get("district") or ""
    ).strip().lower()
    return f"{chamber}|{office}" if office else ""


def _aggregate_congress(
    senate: Any,
    house: Any,
    *,
    now: Optional[datetime] = None,
    window_days: int = _CONGRESS_WINDOW_DAYS,
    top_n: int = _SIGNAL_ROWS,
) -> Optional[SignalGroupResponse]:
    """Rank tickers by DISTINCT congress members who bought them (disclosure window).

    BUY-side only (mirrors ``holders_service._build_congress_activities``: purchase /
    buy / exchange). Windowed on the disclosure date; if NO row carries a parseable
    date (degenerate feed), keep all buys so the card stays alive. Returns ``None``
    (card omitted) when there are no qualifying buys, nothing in the window, or the
    top ticker is below the ``_CONGRESS_MIN_MEMBERS`` floor — honest, never padded.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    buys: List[Tuple[str, str, str, str, Optional[datetime]]] = []  # sym, member, name, date_str, date_obj
    dates_seen = 0
    for chamber, trades in (("senate", senate), ("house", house)):
        if not isinstance(trades, list):
            continue
        for row in trades:
            if not isinstance(row, dict):
                continue
            ttype = (row.get("type") or "").lower()
            if not ("purchase" in ttype or "buy" in ttype or "exchange" in ttype):
                continue
            sym = _canonical_symbol(row.get("symbol"))
            if sym in _BAD_SYMBOLS:
                continue
            member = _congress_member_key(row, chamber)
            if not member:
                continue
            date_str = str(
                row.get("disclosureDate") or row.get("dateReceived")
                or row.get("date") or row.get("transactionDate") or ""
            )[:10]
            date_obj = _parse_iso_date(date_str)
            if date_obj is not None:
                dates_seen += 1
            name = (row.get("assetDescription") or "").strip()
            buys.append((sym, member, name, date_str, date_obj))

    if not buys:
        return None

    if dates_seen:
        windowed = [
            b for b in buys
            if b[4] is not None and -2 <= (now - b[4]).days <= window_days
        ]
        if not windowed:
            return None  # dates exist but nothing recent → honest empty
    else:
        windowed = buys  # degenerate: no parseable dates anywhere → keep card alive

    agg: Dict[str, Dict[str, Any]] = {}
    for sym, member, name, date_str, _ in windowed:
        e = agg.setdefault(sym, {"members": set(), "name": "", "date": ""})
        e["members"].add(member)
        if name and not e["name"]:
            e["name"] = name
        if date_str and date_str > e["date"]:
            e["date"] = date_str

    ranked = sorted(agg.items(), key=lambda kv: (-len(kv[1]["members"]), kv[0]))
    if not ranked or len(ranked[0][1]["members"]) < _CONGRESS_MIN_MEMBERS:
        return None

    entries = [
        SignalRowResponse(
            rank=i + 1, symbol=sym, name=e["name"], value=float(len(e["members"])),
        )
        for i, (sym, e) in enumerate(ranked[:top_n])
    ]
    as_of = max((e["date"] for _, e in ranked if e["date"]), default="") or None
    return SignalGroupResponse(kind="congress", entries=entries, as_of_date=as_of)


def _aggregate_whale(
    holding_rows: Any,
    whale_cik_map: Dict[Any, str],
    *,
    as_of: Optional[str] = None,
    top_n: int = _SIGNAL_ROWS,
) -> Optional[SignalGroupResponse]:
    """Rank tickers by DISTINCT 13F funds adding to them (deduped by CIK).

    ``whale_cik_map`` maps a 13F ``whale_id`` → its dedup key (CIK, or a
    ``nocik:<id>`` sentinel when the CIK is null so it never collapses with other
    null-CIK whales). Only whales present in that map are counted (13F-only). A
    holding counts as "adding" when its QoQ ``change_percent`` > 0. Returns ``None``
    when nothing qualifies or the top ticker is below ``_WHALE_MIN_FUNDS``.
    """
    if not isinstance(holding_rows, list):
        return None

    agg: Dict[str, Dict[str, Any]] = {}
    for row in holding_rows:
        if not isinstance(row, dict):
            continue
        dedup = whale_cik_map.get(row.get("whale_id"))
        if dedup is None:
            continue  # not a 13F whale in the registry
        cp = _finite_float(row.get("change_percent"))
        if cp is None or cp <= 0:
            continue
        sym = _canonical_symbol(row.get("ticker"))
        if sym in _BAD_SYMBOLS:
            continue
        e = agg.setdefault(sym, {"funds": set(), "name": ""})
        e["funds"].add(dedup)
        cn = (row.get("company_name") or "").strip()
        if cn and not e["name"]:
            e["name"] = cn

    ranked = sorted(agg.items(), key=lambda kv: (-len(kv[1]["funds"]), kv[0]))
    if not ranked or len(ranked[0][1]["funds"]) < _WHALE_MIN_FUNDS:
        return None

    entries = [
        SignalRowResponse(
            rank=i + 1, symbol=sym, name=e["name"], value=float(len(e["funds"])),
        )
        for i, (sym, e) in enumerate(ranked[:top_n])
    ]
    return SignalGroupResponse(kind="whale", entries=entries, as_of_date=as_of)


def _aggregate_earnings(
    calendar: Any,
    *,
    min_abs: float = _EARNINGS_MIN_ABS_SURPRISE,
    max_abs: float = _EARNINGS_MAX_ABS_SURPRISE,
    top_n: int = _SIGNAL_ROWS,
) -> Optional[SignalGroupResponse]:
    """Rank recent US reporters FRESHEST-FIRST, then by |EPS surprise %| within a day.

    Reuses ``earnings_service._compute_surprise``. A row is skipped when: the symbol
    is foreign (dotted, e.g. ZOO.L), actual/estimate is missing, ``|estimate|`` is
    below ``_EARNINGS_MIN_ABS_ESTIMATE`` (near-zero denominators explode the %), or
    ``|surprise|`` is outside [min_abs, max_abs]. A symbol reporting twice keeps its
    MOST-RECENT report. Ranking is (date desc, |surprise| desc) so the card leads with
    the latest shockers, not a stale big one. Returns ``None`` when nothing qualifies.
    NOTE: no exchange/market-cap filter here (calendar rows carry none) — the caller
    ``_build_earnings`` over-ranks candidates then applies the exchange + $250M gate.
    """
    if not isinstance(calendar, list):
        return None

    best: Dict[str, Dict[str, Any]] = {}
    for row in calendar:
        if not isinstance(row, dict):
            continue
        raw_symbol = str(row.get("symbol") or "")
        # Drop non-US listings: FMP's earnings-calendar spans global exchanges and
        # tags foreign tickers with a "." suffix (ZOO.L, 005930.KS). US commons are
        # dot-free. This also drops US class shares (BRK.B) — acceptable, they're
        # never "shockers". Done on the RAW symbol, BEFORE _canonical_symbol folds
        # "." → "-" (which would otherwise disguise a foreign ticker as US).
        if "." in raw_symbol:
            continue
        sym = _canonical_symbol(raw_symbol)
        if sym in _BAD_SYMBOLS:
            continue
        actual = _finite_float(row.get("epsActual"))
        if actual is None:
            actual = _finite_float(row.get("eps"))
        estimate = _finite_float(row.get("epsEstimated"))
        if estimate is None:
            estimate = _finite_float(row.get("epsEstimate"))
        if actual is None or estimate is None:
            continue
        # Near-zero estimates make (actual - est)/|est| explode into meaningless
        # thousands-of-% "surprises" (the est≈0.01 penny-EPS artifact). Skip them at
        # the source; a genuine low bar like NKE's $0.11 estimate still clears.
        if abs(estimate) < _EARNINGS_MIN_ABS_ESTIMATE:
            continue
        surprise = _compute_surprise(actual, estimate)
        if surprise is None:
            continue
        surprise = surprise + 0.0  # collapse a possible signed -0.0 → 0.0
        if not (min_abs <= abs(surprise) <= max_abs):
            continue
        date_str = str(row.get("date") or "")[:10]
        # Keep the FRESHEST report per symbol (a name can appear twice near a quarter
        # boundary): recency drives the card, so a newer report supersedes an older
        # one; a same-date tie keeps the larger-magnitude surprise.
        cur = best.get(sym)
        if (
            cur is None
            or date_str > cur["date"]
            or (date_str == cur["date"] and abs(surprise) > abs(cur["surprise"]))
        ):
            best[sym] = {"surprise": surprise, "date": date_str}

    if not best:
        return None

    # Freshest-first: most-recent report date leads, then larger |surprise| within a
    # day (reverse=True makes BOTH descending). Older reports only fill lower slots
    # when there aren't enough fresh ones — so the card reflects THIS week, not last.
    ranked = sorted(
        best.items(),
        key=lambda kv: (kv[1]["date"], abs(kv[1]["surprise"])),
        reverse=True,
    )
    entries = [
        SignalRowResponse(
            rank=i + 1, symbol=sym, name="", value=round(e["surprise"], 2) + 0.0,
        )
        for i, (sym, e) in enumerate(ranked[:top_n])
    ]
    as_of = max(
        (e["date"] for _, e in ranked[:top_n]), default=""
    ) or None
    return SignalGroupResponse(kind="earnings", entries=entries, as_of_date=as_of)


def _earnings_quote_ok(quote: Any) -> bool:
    """Quality gate for an earnings-shocker candidate, from its FMP ``/quote`` row.

    Mirrors the Daily Scanners' bar so the card can't fill with OTC/penny junk: the
    symbol must trade on a major US exchange (drops OTC lines like TCYSF/BKRRF whose
    caps clear $250M but are illiquid foreign ordinaries) AND clear the $250M
    market-cap floor. A missing/NaN cap is rejected via ``_finite_float``.
    """
    if not isinstance(quote, dict):
        return False
    if str(quote.get("exchange") or "").upper() not in _MOVERS_EXCHANGES:
        return False
    market_cap = _finite_float(quote.get("marketCap"))
    return market_cap is not None and market_cap >= _EARNINGS_MIN_MARKET_CAP


# ── CEO Buys (pure; the drill-down reuses exactly the card's filters) ──────────


class _CeoBuy(NamedTuple):
    """One qualifying Form 4 line: a CEO's open-market purchase of common stock."""

    symbol: str
    reporter: str          # identity key: reportingCik, else the normalised name
    name_raw: str
    title_raw: str
    filing_date: str       # YYYY-MM-DD (the window key)
    transaction_date: str  # YYYY-MM-DD, or "" when FMP's is unparseable
    shares: float
    price: float
    dollars: float
    ownership: str         # "D" (direct) / "I" (indirect) / ""
    form_type: str         # "4" or "4/A"


def _ceo_reporter_key(row: Dict[str, Any]) -> str:
    """Stable per-person identity: the SEC reporting CIK, else the normalised name.
    ``""`` when neither identifies anyone (``normalize_insider_name`` answers "Insider")."""
    cik = row.get("reportingCik")
    if cik is not None and not isinstance(cik, bool):
        text = str(cik).strip()
        if text and text.lower() not in {"none", "null", "0"}:
            return f"cik:{text}"
    name = row.get("reportingName")
    if isinstance(name, str) and name.strip():
        normalized = normalize_insider_name(name).lower()
        if normalized and normalized != "insider":
            return f"name:{normalized}"
    return ""


def _extract_ceo_buys(
    rows: Any, *, now: Optional[datetime] = None, window_days: int = _CEO_WINDOW_DAYS
) -> List[_CeoBuy]:
    """Filter FMP insider rows to CEO open-market common-stock buys filed in the window,
    then de-duplicate. Pure — no network; every malformed row is skipped, never fatal.

    Filters, in order: a dict; ``transactionType`` a P (open-market purchase); acquisition
    ``A`` and a Form 4 when those fields are present; a usable ticker (``BRK.B`` folds to
    ``BRK-B``); common/ordinary stock; a sitting CEO; finite shares > 0 and price > 0 with
    a sane dollar product; a parseable FILING date inside ``[-skew, window]``; a trade date
    (when parseable) no later than the filing + 1 day and no more than the lag bound before
    it; an identifiable reporter.

    De-duplication, per (symbol, reporter, trade date, ownership):
      * an amendment SUPERSEDES: when a 4/A is present, only the rows of the most recently
        filed amendment count — a corrected 4/A must replace the original, not add to it;
      * the same (shares, price) line reported on two different filings counts once
        (the earliest filing). Identical lines on ONE filing are distinct fills and all
        count (FMP rows carry no line number; the pager already drops page-shift repeats).
    """
    if not isinstance(rows, list):
        return []
    now = now or datetime.now(timezone.utc)
    kept: List[_CeoBuy] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tx = row.get("transactionType")
        if not isinstance(tx, str) or classify_insider_transaction(tx) != "Informative Buy":
            continue
        acq = row.get("acquisitionOrDisposition")
        if acq not in (None, "") and str(acq).strip().upper() != "A":
            continue
        form = row.get("formType")
        form_type = str(form).strip().upper() if form not in (None, "") else "4"
        if not form_type.startswith("4"):
            continue
        raw_symbol = row.get("symbol")
        if not isinstance(raw_symbol, str):
            continue
        symbol = _canonical_symbol(raw_symbol.strip())
        if symbol in _BAD_SYMBOLS or not _CEO_SYMBOL_RE.match(symbol):
            continue
        if not is_common_stock(row.get("securityName")):
            continue
        title = row.get("typeOfOwner")
        if not is_ceo_role(title):
            continue
        shares = _finite_float(row.get("securitiesTransacted"))
        price = _finite_float(row.get("price"))
        if shares is None or price is None or shares <= 0 or price <= 0:
            continue
        # A purchase can never exceed the holding it produced (`securitiesOwned` is the
        # post-transaction position). A larger figure is a share-count unit error — e.g. the
        # holding typed into the shares column — which the PRICE band cannot see.
        owned = _finite_float(row.get("securitiesOwned"))
        if owned is not None and owned > 0 and shares > owned * 1.0001:
            continue
        dollars = _finite_float(shares * price)
        if dollars is None or dollars > _CEO_MAX_ROW_DOLLARS:
            continue
        filed = _parse_iso_date(row.get("filingDate"))
        if filed is None:
            continue  # the window AND the pager key — no keep-everything fallback here
        age = (now - filed).days
        if not (-_CEO_FUTURE_SKEW_DAYS <= age <= window_days):
            continue
        traded = _parse_iso_date(row.get("transactionDate"))
        if traded is not None:
            if traded > filed + timedelta(days=1):
                continue
            if (filed - traded).days > _CEO_MAX_FILING_LAG_DAYS:
                continue
        reporter = _ceo_reporter_key(row)
        if not reporter:
            continue
        own = row.get("directOrIndirect")
        kept.append(_CeoBuy(
            symbol=symbol,
            reporter=reporter,
            name_raw=str(row.get("reportingName") or ""),
            title_raw=title,
            filing_date=filed.strftime("%Y-%m-%d"),
            transaction_date=traded.strftime("%Y-%m-%d") if traded else "",
            shares=shares,
            price=price,
            dollars=dollars,
            ownership=str(own).strip().upper() if isinstance(own, str) else "",
            form_type=form_type,
        ))

    groups: Dict[Tuple[str, str, str, str], List[_CeoBuy]] = {}
    for b in kept:
        key = (b.symbol, b.reporter, b.transaction_date or b.filing_date, b.ownership)
        groups.setdefault(key, []).append(b)

    out: List[_CeoBuy] = []
    for members in groups.values():
        amendments = [b for b in members if "/A" in b.form_type]
        if amendments:
            latest = max(b.filing_date for b in amendments)
            amended = [b for b in amendments if b.filing_date == latest]
            originals = [b for b in members if "/A" not in b.form_type]
            if len(amended) >= len(originals):
                # A full restatement: the latest amendment replaces the day outright.
                members = amended
            else:
                # A PARTIAL 4/A — an omitted line added, or one line corrected. Replacing
                # the whole day would wipe the purchases it never restated, so drop only the
                # original lines it corrects (same size at a new price, or same price at a
                # new size) and keep the rest.
                def _corrected(o: _CeoBuy) -> bool:
                    return any(
                        (round(a.shares, 4) == round(o.shares, 4))
                        != (round(a.price, 4) == round(o.price, 4))
                        for a in amended
                    )
                members = [o for o in originals if not _corrected(o)] + amended
        by_line: Dict[Tuple[float, float], List[_CeoBuy]] = {}
        for b in members:
            by_line.setdefault((round(b.shares, 4), round(b.price, 4)), []).append(b)
        for line in by_line.values():
            first_filing = min(b.filing_date for b in line)
            out.extend(b for b in line if b.filing_date == first_filing)
    return out


def _rank_ceo_buys(
    buys: List[_CeoBuy],
    *,
    top_n: int = _SIGNAL_ROWS,
    names: Optional[Dict[str, str]] = None,
    min_ticker_dollars: float = _CEO_MIN_TICKER_DOLLARS,
) -> Optional[SignalGroupResponse]:
    """Σ dollars per ticker → the ranked card, or ``None`` when nothing clears the floor.

    Order: total dollars desc, then the latest filing desc, then symbol asc (total
    determinism). ``as_of_date`` is the latest filing over ALL ``buys`` — how fresh the
    feed is, not just the leaders.
    """
    if not buys:
        return None
    totals: Dict[str, float] = {}
    latest: Dict[str, str] = {}
    for b in buys:
        totals[b.symbol] = totals.get(b.symbol, 0.0) + b.dollars
        if b.filing_date > latest.get(b.symbol, ""):
            latest[b.symbol] = b.filing_date
    qualifying = [
        s for s, total in totals.items()
        if math.isfinite(total) and total >= min_ticker_dollars
    ]
    qualifying.sort()
    qualifying.sort(key=lambda s: latest[s], reverse=True)
    qualifying.sort(key=lambda s: totals[s], reverse=True)
    names = names or {}
    entries = [
        SignalRowResponse(
            rank=i + 1,
            symbol=sym,
            name=names.get(sym, ""),
            value=round(totals[sym], 2) + 0.0,
        )
        for i, sym in enumerate(qualifying[: max(0, top_n)])
    ]
    if not entries:
        return None
    return SignalGroupResponse(
        kind="ceo", entries=entries, as_of_date=max(b.filing_date for b in buys)
    )


def _aggregate_ceo_buys(
    rows: Any,
    *,
    now: Optional[datetime] = None,
    window_days: int = _CEO_WINDOW_DAYS,
    top_n: int = _SIGNAL_ROWS,
) -> Optional[SignalGroupResponse]:
    """The pure end-to-end aggregation (extract → de-dup → rank), before the quote gate."""
    return _rank_ceo_buys(_extract_ceo_buys(rows, now=now, window_days=window_days), top_n=top_n)


def _ceo_price_plausible(
    row_price: Any, ref_price: Any, band: float = _CEO_PRICE_BAND
) -> bool:
    """Is a Form 4 line's price within ``ref/band … ref*band`` of the live quote?

    ``True`` when there is no usable reference (missing / non-finite / ≤ 0) — an
    unverifiable row is not evidence of a unit error. A non-finite row price is not
    plausible."""
    row = _finite_float(row_price)
    if row is None or row <= 0:
        return False
    ref = _finite_float(ref_price)
    if ref is None or ref <= 0:
        return True
    return ref / band <= row <= ref * band


# ── Tier redaction (App-Exclusive Signals are Pro/Max — entitlements.signals_unlocked) ──

_MASK_CHAR = "•"
_MASK_MIN_LEN = 2
_MASK_MAX_LEN = 5


def _mask_symbol(symbol: Any) -> str:
    """A same-length bullet mask for a withheld ticker (``"HONA"`` → ``"••••"``).

    Length is preserved (clamped to 2–5) purely so the blurred chip keeps the width it
    would have had; a ticker's LENGTH is not the paid information, the ticker is. A
    missing/blank/non-str symbol still yields a mask rather than "" so the row never
    renders a naked stat with an empty slot above it.
    """
    text = symbol.strip() if isinstance(symbol, str) else ""
    length = min(max(len(text) or 4, _MASK_MIN_LEN), _MASK_MAX_LEN)
    return _MASK_CHAR * length


def _redact_group(
    group: Optional[SignalGroupResponse], tier_required: str
) -> Optional[SignalGroupResponse]:
    """One card, with its ticker withheld. Returns a NEW object; never mutates ``group``."""
    if group is None:
        return None

    entries = list(group.entries or [])
    if not entries:
        # Nothing to withhold. Flagging an empty card as locked would be a lie the UI
        # can't render anyway — iOS omits a card with no entries either way.
        return group.model_copy(deep=True)

    top = entries[0]
    return SignalGroupResponse(
        kind=group.kind,
        # ONE masked entry, not ten. iOS derives the headline from entries[0] and a
        # locked row can't expand, so the other nine rows are pure leak surface with
        # nothing to render them. `value` survives verbatim — it is the stat the card
        # shows ("3 members buying"), and it names no ticker.
        entries=[
            SignalRowResponse(
                rank=top.rank,
                symbol=_mask_symbol(top.symbol),
                name="",
                value=top.value,
            )
        ],
        as_of_date=group.as_of_date,
        is_locked=True,
        tier_required=tier_required,
        locked_count=len(entries),
    )


def redact_signals(
    groups: SignalsGroupResponse, tier_required: str
) -> SignalsGroupResponse:
    """Return a NEW response with every card's ticker masked, for a locked caller.

    ⚠️ **Must never mutate ``groups``.** The argument is normally the object held in
    the class-level ``SignalsService._cache`` — ONE instance shared by every caller for
    45 minutes. Redacting it in place would strip the tickers for every PAYING user
    until the next rebuild, and the damage would survive long past the request that
    caused it. Every field here is copied into fresh models for that reason; there is a
    regression test (``test_signals_entitlement.py``) that asserts the input is intact
    after a call.
    """
    # Every field of the model, not a hand-written list: a card added later cannot
    # slip past the lock (test_signals_entitlement pins this against model_fields).
    return SignalsGroupResponse(**{
        field: _redact_group(getattr(groups, field), tier_required)
        for field in SignalsGroupResponse.model_fields
    })


def _has_any_group(result: SignalsGroupResponse) -> bool:
    """≥1 card present — iterates the model's fields so a new card is counted without
    anyone remembering to add it here (the old check named the three by hand)."""
    return any(getattr(result, f) is not None for f in SignalsGroupResponse.model_fields)


# ── Service ─────────────────────────────────────────────────────────────


class SignalsService:
    """Builds the four App-Exclusive Signal cards from FMP + the whale registry."""

    # Class-level so the cache/dedup are shared across requests (mirrors scanners).
    _cache: Dict[str, Tuple[float, SignalsGroupResponse]] = {}
    _inflight: Dict[str, asyncio.Future] = {}
    # Keys whose in-memory entry came from a DEGRADED build (a branch raised): they use
    # `_SIGNALS_DEGRADED_TTL_SECONDS` and were never written to Tier 2.
    _degraded_keys: Set[str] = set()
    # Per-(kind, ticker) drill-down cache + dedup.
    _detail_cache: Dict[str, Tuple[float, SignalTickerDetailResponse]] = {}
    _detail_inflight: Dict[str, asyncio.Future] = {}

    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()

    # ── Public API ────────────────────────────────────────────────────

    async def get_signals(self) -> SignalsGroupResponse:
        """Signals, cache-aside (45-min in-mem → 24h Supabase) + in-flight dedup.

        Never re-raises: a build failure returns empty groups and is NOT cached, so
        the next request retries (keeps awaiters unpoisoned and the shielded
        background build — see ``get_signals_guarded`` — from leaking an exception).
        """
        cached = self._cache.get(_SIGNALS_CACHE_KEY)
        ttl = (
            _SIGNALS_DEGRADED_TTL_SECONDS
            if _SIGNALS_CACHE_KEY in self._degraded_keys
            else _SIGNALS_MEM_TTL_SECONDS
        )
        if cached is not None and (time.time() - cached[0]) < ttl:
            logger.debug("Signals served from in-memory cache")
            return cached[1]

        inflight = self._inflight.get(_SIGNALS_CACHE_KEY)
        if inflight is not None:
            logger.debug("Signals joining in-flight build")
            return await asyncio.shield(inflight)

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[_SIGNALS_CACHE_KEY] = fut
        try:
            # Tier 2: a fresh Supabase row (survives restarts) BEFORE a rebuild. A
            # read failure is a cache MISS (→ proceed to rebuild), NOT a reason to
            # blank the card — keep the two tiers' failures independent.
            try:
                result = await asyncio.to_thread(self._read_supabase_cache)
            except Exception as exc:  # noqa: BLE001 — read failure → miss, then rebuild
                logger.warning(
                    "Signals Tier-2 read failed: %s: %s", type(exc).__name__, exc
                )
                result = None

            if result is not None:
                logger.debug("Signals served from Supabase cache")
                self._cache[_SIGNALS_CACHE_KEY] = (time.time(), result)
                self._degraded_keys.discard(_SIGNALS_CACHE_KEY)
            else:
                try:
                    result, failed = await self._build()
                    # Cache ONLY a build that produced ≥1 group — never pin a transient
                    # total failure (all-None). PERSIST only a build in which no branch
                    # raised: Tier 2 is read before every rebuild, so a persisted partial
                    # build would hide the failed card for up to 24 h.
                    if _has_any_group(result):
                        self._cache[_SIGNALS_CACHE_KEY] = (time.time(), result)
                        if failed:
                            self._degraded_keys.add(_SIGNALS_CACHE_KEY)
                            logger.warning(
                                "Signals build DEGRADED (failed: %s) — serving the other "
                                "cards from memory for %ds; NOT persisting to Tier 2",
                                ", ".join(sorted(failed)), _SIGNALS_DEGRADED_TTL_SECONDS,
                            )
                        else:
                            self._degraded_keys.discard(_SIGNALS_CACHE_KEY)
                            await asyncio.to_thread(self._write_supabase_cache, result)
                except Exception as exc:  # noqa: BLE001 — build failed → empty (not cached)
                    logger.warning(
                        "Signals build failed: %s: %s", type(exc).__name__, exc
                    )
                    result = SignalsGroupResponse()  # empty; not cached → retries
            if not fut.done():
                fut.set_result(result)
            return result
        except BaseException as exc:
            # CancelledError (a BaseException) on shutdown must still settle the
            # future, or a joined request hangs forever. Mirrors get_scanners.
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(_SIGNALS_CACHE_KEY, None)

    async def get_signals_guarded(self) -> SignalsGroupResponse:
        """Await signals up to a hard timeout. ``asyncio.shield`` ensures a timeout
        never CANCELS the shared build (it keeps running and caches for the next
        request) — we just ship the dashboard without signals this round, serving
        the last cached value (any age) if we have one."""
        try:
            return await asyncio.wait_for(
                asyncio.shield(self.get_signals()),
                _SIGNALS_BUILD_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # TimeoutError or anything unexpected
            cached = self._cache.get(_SIGNALS_CACHE_KEY)
            if cached is not None:
                logger.info(
                    "Signals build slow (%s); serving last cached (age=%.0fs)",
                    type(exc).__name__, time.time() - cached[0],
                )
                return cached[1]
            logger.warning(
                "Signals not ready this build (no cache yet): %s: %s",
                type(exc).__name__, exc,
            )
            return SignalsGroupResponse()

    # ── Build (4 branches, degrade independently) ─────────────────────

    async def _build(self) -> Tuple[SignalsGroupResponse, FrozenSet[str]]:
        """Every card in parallel. Returns the groups AND the steps that RAISED.

        ``None`` from a branch is an honest "nothing qualified"; an exception is a
        failure — the card is omitted either way, but only a failure marks the build
        degraded (``get_signals`` then refuses to persist it).
        """
        builders = {
            "congress": self._build_congress,
            "whale": self._build_whale,
            "earnings": self._build_earnings,
            "ceo": self._build_ceo,
        }
        results = await asyncio.gather(
            *(builders[step]() for step in _SIGNAL_STEPS), return_exceptions=True
        )
        groups: Dict[str, Optional[SignalGroupResponse]] = {}
        failed: Set[str] = set()
        for step, res in zip(_SIGNAL_STEPS, results):
            if isinstance(res, BaseException):
                logger.warning(
                    "Signal %s failed: %s: %s", step, type(res).__name__, res
                )
                failed.add(step)
                groups[step] = None
            else:
                groups[step] = res
        return SignalsGroupResponse(**groups), frozenset(failed)

    async def _build_congress(self) -> Optional[SignalGroupResponse]:
        senate, house = await asyncio.gather(
            self.fmp.get_senate_latest(1000),
            self.fmp.get_house_latest(1000),
            return_exceptions=True,
        )
        # `return_exceptions` so ONE chamber failing cannot leave the other's
        # task un-retrieved, and so the incomplete case is handled here rather
        # than by the outer gather's generic warning.
        for label, res in (("senate", senate), ("house", house)):
            if isinstance(res, BaseException):
                # The card's headline is a COUNT ("N members buying"). A
                # truncated feed would under-count it while looking complete, so
                # omit the card rather than publish a number we know is short.
                logger.warning(
                    "Congressional Buys: %s feed incomplete (%s: %s) — omitting "
                    "card rather than publishing an under-count",
                    label, type(res).__name__, res,
                )
                # RAISE, not `return None`: an incomplete feed is a FAILURE, and the
                # build must know it so it is not persisted to the 24 h tier.
                raise res
        # Both methods self-swallow non-partial FMP errors → []. Empty is the
        # honest-empty case (no disclosures), not a failure.
        if not senate and not house:
            logger.info("Congressional Buys: no disclosures returned — omitting card")
            return None
        return _aggregate_congress(senate or [], house or [])

    async def _build_whale(self) -> Optional[SignalGroupResponse]:
        # Supabase SDK is sync → run the whole query+aggregate off the event loop.
        return await asyncio.to_thread(self._query_and_aggregate_whale)

    def _query_and_aggregate_whale(self) -> Optional[SignalGroupResponse]:
        # Wrapped so a Supabase failure degrades THIS branch with a whale-specific
        # marker (rather than surfacing only as a generic "signal whale failed" from
        # _build's gather) — honoring the "each branch degrades independently" rule.
        try:
            sb = get_supabase()
            whales = (
                fetch_all_rows(
                    lambda: sb.table("whales")
                    .select("id, cik, last_hydrated_at")
                    .eq("data_source", "13f"),
                    order_by="id",
                    what="signals: 13F whale roster",
                )
            )
            if not whales:
                logger.info(
                    "Whale Accumulation: no 13F whales in registry — omitting card"
                )
                return None

            cik_map: Dict[Any, str] = {}
            hydrated: List[str] = []
            for w in whales:
                wid = w.get("id")
                if wid is None:
                    continue
                cik = (w.get("cik") or "").strip()
                # Null/blank CIK → per-whale sentinel so it stays its OWN distinct
                # fund rather than collapsing all null-CIK whales into one.
                cik_map[wid] = cik if cik else f"nocik:{wid}"
                hd = w.get("last_hydrated_at")
                if hd:
                    hydrated.append(str(hd)[:10])

            holdings = (
                fetch_all_rows(
                    lambda: sb.table("whale_holdings")
                    .select("whale_id, ticker, company_name, change_percent")
                    .gt("change_percent", 0),
                    # `id`, NOT `whale_id`. The helper's contract requires a unique-ish
                    # sort key, and `whale_id` repeats up to 30 times (one row per holding
                    # per whale, `whale_service._sync_to_whale_tables`). This is the ONE
                    # read here that genuinely crosses 1,000 rows, so the page boundary
                    # lands INSIDE a tie group: each query orders the tie independently, so
                    # a row can land on both pages or on neither. A dropped row under-counts
                    # "N funds adding" — and under `_WHALE_MIN_FUNDS` the Accumulation card
                    # disappears. The old `.limit(10000)` truncated deterministically; an
                    # unordered pager fails nondeterministically, which is worse.
                    order_by="id",
                    what="signals: whale_holdings accumulation",
                )
            )
            # The old `.limit(10000)` did NOT lift PostgREST's ~1,000-row cap — the very
            # truncation its own comment warned about was already happening: the registry
            # is 45 13F whales × up to 30 holdings, so the `change_percent > 0` subset
            # crosses 1,000 and "N funds adding" under-counted, unordered and silently.
            as_of = max(hydrated) if hydrated else None
            return _aggregate_whale(holdings, cik_map, as_of=as_of)
        except Exception as exc:  # noqa: BLE001 — degrade this card, never the dashboard
            logger.warning(
                "Whale Accumulation query failed: %s: %s", type(exc).__name__, exc
            )
            # Re-raise so `_build` records a FAILURE (card omitted, build not persisted)
            # rather than an honest "no fund is adding" `None`.
            raise

    async def _build_earnings(self) -> Optional[SignalGroupResponse]:
        now = datetime.now(timezone.utc)
        to_date = now.strftime("%Y-%m-%d")
        from_date = (now - timedelta(days=_EARNINGS_WINDOW_DAYS)).strftime("%Y-%m-%d")
        # get_earnings_calendar RAISES on FMP failure (unlike the congress methods)
        # → propagates to _build's return_exceptions → earnings None + a warning.
        calendar = await self.fmp.get_earnings_calendar(from_date, to_date)
        # Rank a bounded candidate set, THEN enforce the same $250M quality floor the
        # movers/volume/shorts cards use. Earnings-calendar rows carry no market cap,
        # so quote the top candidates and drop micro-caps (mirrors _build_shorts) —
        # otherwise a $30M name with a huge beat outranks the real large-cap shockers.
        candidates = _aggregate_earnings(
            calendar or [], top_n=_EARNINGS_QUOTE_CANDIDATES
        )
        if candidates is None or not candidates.entries:
            return None

        symbols = [e.symbol for e in candidates.entries]
        quotes = await price_source(self).get_quotes_list(symbols)
        qmap = {
            _canonical_symbol(q.get("symbol")): q
            for q in (quotes or [])
            if isinstance(q, dict) and q.get("symbol")
        }
        if not qmap:
            # Candidates exist but not ONE quote came back: a quote outage, not "no
            # shocker cleared the floor". Returning None here would be persisted to the
            # 24 h tier as an honest empty and hide the card for a day; raising marks
            # the build degraded instead (mirrors _build_ceo).
            raise FMPUnavailableException(
                f"Earnings Shockers: no quotes returned for {len(symbols)} candidate(s)"
            )

        kept: List[SignalRowResponse] = []
        for e in candidates.entries:  # already ranked freshest-first
            quote = qmap.get(e.symbol, {})
            if not _earnings_quote_ok(quote):
                continue
            # Fill the company name from the quote already in hand so the card reads
            # "Nike", not a bare "NKE" (iOS strips the legal suffix for display).
            kept.append(
                SignalRowResponse(
                    rank=len(kept) + 1,
                    symbol=e.symbol,
                    name=str(quote.get("name") or ""),
                    value=e.value,
                )
            )
            if len(kept) >= _SIGNAL_ROWS:
                break

        if not kept:
            logger.info(
                "Earnings Shockers: no candidate cleared the exchange + $%dM floor "
                "— omitting card", _EARNINGS_MIN_MARKET_CAP // 1_000_000,
            )
            return None

        return SignalGroupResponse(
            kind="earnings", entries=kept, as_of_date=candidates.as_of_date
        )

    async def _build_ceo(self) -> Optional[SignalGroupResponse]:
        """CEO Buys: market-wide Form 4 CEO purchases → top tickers by dollars bought.

        ``None`` = honestly nothing qualified (or the feed is not on the Order Form —
        a permanent contract condition, not a retryable failure). Every other failure
        RAISES so the build is marked degraded and not persisted.
        """
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=_CEO_WINDOW_DAYS)).strftime("%Y-%m-%d")
        try:
            rows = await self.fmp.get_insider_trades_since(
                since,
                transaction_type="P-Purchase",
                page_size=_CEO_FETCH_PAGE_SIZE,
                max_pages=_CEO_FETCH_MAX_PAGES,
            )
        except FMPNotEntitledException as exc:
            logger.warning("CEO Buys: insider feed not entitled (%s) — omitting card", exc)
            return None

        if not rows:
            # Thirty days of market-wide open-market purchases is never empty (~2,000 rows
            # live). An empty answer is an upstream problem — raise so the build is marked
            # degraded instead of persisting "no CEO bought anything" for 24 h.
            raise FMPUnavailableException(
                "CEO Buys: the market-wide insider feed returned 0 rows for a 30-day window"
            )

        buys = _extract_ceo_buys(rows, now=now)
        candidates = _rank_ceo_buys(buys, top_n=_CEO_QUOTE_CANDIDATES)
        if candidates is None:
            logger.info(
                "CEO Buys: %d insider row(s), %d CEO buy(s), none clears $%.0fK — omitting card",
                len(rows), len(buys), _CEO_MIN_TICKER_DOLLARS / 1000,
            )
            return None

        symbols = [e.symbol for e in candidates.entries]
        quotes = await price_source(self).get_quotes_list(symbols)
        qmap = {
            _canonical_symbol(q.get("symbol")): q
            for q in (quotes or [])
            if isinstance(q, dict) and q.get("symbol")
        }
        if not qmap:
            # Candidates exist but not ONE quote came back: an outage, not "nothing
            # qualified" — raise so the degraded build is not pinned for 24 h.
            raise FMPUnavailableException(
                f"CEO Buys: no quotes returned for {len(symbols)} candidate(s)"
            )

        gated = {sym: q for sym, q in qmap.items() if _earnings_quote_ok(q)}
        kept: List[_CeoBuy] = []
        implausible: List[str] = []
        for b in buys:
            quote = gated.get(b.symbol)
            if quote is None:
                continue
            if not _ceo_price_plausible(b.price, quote.get("price")):
                implausible.append(f"{b.symbol}@{b.price:g} vs {quote.get('price')}")
                continue
            market_cap = _finite_float(quote.get("marketCap"))
            if market_cap is not None and market_cap > 0 and b.dollars > _CEO_MAX_MCAP_SHARE * market_cap:
                implausible.append(f"{b.symbol} ${b.dollars:,.0f} vs cap ${market_cap:,.0f}")
                continue
            kept.append(b)
        if implausible:
            logger.warning(
                "CEO Buys: dropped %d row(s) whose price is outside ×%g of the live quote, "
                "or whose dollars exceed %d%% of the market cap (likely a unit error): %s",
                len(implausible), _CEO_PRICE_BAND, int(_CEO_MAX_MCAP_SHARE * 100),
                "; ".join(implausible[:10]),
            )

        names = {sym: str(q.get("name") or "") for sym, q in gated.items()}
        final = _rank_ceo_buys(kept, top_n=_SIGNAL_ROWS, names=names)
        if final is None:
            logger.info(
                "CEO Buys: no candidate cleared the exchange + $%dM floor — omitting card",
                _EARNINGS_MIN_MARKET_CAP // 1_000_000,
            )
            return None
        return SignalGroupResponse(
            kind="ceo", entries=final.entries, as_of_date=candidates.as_of_date
        )

    # ── Supabase Tier-2 (best-effort) ─────────────────────────────────

    def _read_supabase_cache(self) -> Optional[SignalsGroupResponse]:
        """Return a fresh cached payload, or ``None`` (miss / stale / parse error)."""
        try:
            sb = get_supabase()
            now_iso = datetime.now(timezone.utc).isoformat()
            res = (
                sb.table(_SIGNALS_TABLE)
                .select("data, expires_at")
                .eq("cache_key", _SIGNALS_CACHE_KEY)
                .gt("expires_at", now_iso)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            if not rows:
                return None
            return SignalsGroupResponse.model_validate(rows[0]["data"])
        except ValidationError as exc:
            # A row that no longer matches the schema is likely real corruption / a
            # schema drift — surface at ERROR (not the transient-failure WARNING) so
            # it's visible in alerting; return None to rebuild from source.
            logger.error(
                "Signals Tier-2 row failed schema validation (possible corruption): %s",
                exc,
            )
            return None
        except Exception as exc:
            logger.warning(
                "Signals Tier-2 read failed: %s: %s", type(exc).__name__, exc
            )
            return None

    def _write_supabase_cache(self, result: SignalsGroupResponse) -> None:
        """Best-effort write-through; a failure only warns (in-mem tier still serves)."""
        try:
            sb = get_supabase()
            now = datetime.now(timezone.utc)
            expires = now + timedelta(hours=_SIGNALS_SUPABASE_TTL_HOURS)
            sb.table(_SIGNALS_TABLE).upsert(
                {
                    "cache_key": _SIGNALS_CACHE_KEY,
                    "data": json.loads(result.model_dump_json()),
                    "computed_at": now.isoformat(),
                    "expires_at": expires.isoformat(),
                },
                on_conflict="cache_key",
            ).execute()
        except Exception as exc:
            logger.warning(
                "Signals Tier-2 write failed: %s: %s", type(exc).__name__, exc
            )

    # ── Per-ticker drill-down (tap a signal ticker → who bought it) ────

    async def get_ticker_detail(
        self, kind: str, ticker: str
    ) -> SignalTickerDetailResponse:
        """WHO bought/added `ticker` behind the whale/congress/ceo signal, WHEN, HOW
        MUCH. Cache-aside (10 min) + in-flight dedup. Never raises: any failure
        degrades to an empty holder list (iOS shows an honest empty state)."""
        kind = (kind or "").strip().lower()
        sym = _canonical_symbol(ticker)
        key = f"{kind}:{sym}"

        cached = self._detail_cache.get(key)
        if cached is not None and (time.time() - cached[0]) < _DETAIL_TTL_SECONDS:
            return cached[1]

        inflight = self._detail_inflight.get(key)
        if inflight is not None:
            return await asyncio.shield(inflight)

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._detail_inflight[key] = fut
        try:
            try:
                result = await self._build_ticker_detail(kind, sym)
                self._detail_cache[key] = (time.time(), result)
            except Exception as exc:  # noqa: BLE001 — drill-down must never 500 the screen
                logger.warning(
                    "Signal detail %s/%s failed: %s: %s",
                    kind, sym, type(exc).__name__, exc,
                )
                result = SignalTickerDetailResponse(symbol=sym, kind=kind)
            if not fut.done():
                fut.set_result(result)
            return result
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._detail_inflight.pop(key, None)

    async def _build_ticker_detail(
        self, kind: str, sym: str
    ) -> SignalTickerDetailResponse:
        # Header (best-effort): company + price + market cap for the tappable ticker
        # header. A profile failure degrades to symbol-only, never fatal.
        company_name, price, market_cap = "", None, None
        try:
            # Query FMP profile with the canonical DASH form (`sym`). Verified live:
            # /stable/profile resolves "BRK-B"/"BF-B" but returns nothing for the dot
            # form "BRK.B" — so the canonical symbol is correct as-is here.
            prof = await self.fmp.get_company_profile(sym)
            if isinstance(prof, dict):
                company_name = prof.get("companyName") or ""
                price = _finite_float(prof.get("price"))
                market_cap = _finite_float(prof.get("marketCap") or prof.get("mktCap"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Signal detail profile %s failed: %s: %s", sym, type(exc).__name__, exc
            )

        if kind == "whale":
            holders, as_of = await asyncio.to_thread(self._detail_whale_rows, sym)
        elif kind == "congress":
            holders, as_of = await self._detail_congress_rows(sym)
        elif kind == "ceo":
            holders, as_of = await self._detail_ceo_rows(sym, ref_price=price, market_cap=market_cap)
        else:
            holders, as_of = [], None

        return SignalTickerDetailResponse(
            symbol=sym, kind=kind, company_name=company_name,
            price=price, market_cap=market_cap, as_of_date=as_of, holders=holders,
        )

    def _detail_whale_rows(
        self, sym: str
    ) -> Tuple[List[SignalHolderResponse], Optional[str]]:
        """Our registry 13F funds adding this ticker — SAME source as the card, so
        the list matches the "N funds adding" count and every fund is tappable."""
        try:
            sb = get_supabase()
            whales = (
                fetch_all_rows(
                    lambda: sb.table("whales")
                    .select("id, name, cik, firm_name, last_hydrated_at")
                    .eq("data_source", "13f"),
                    order_by="id",
                    what="signals: 13F whale roster (detail)",
                )
            )
            wmap: Dict[Any, Dict[str, str]] = {}   # whale_id -> {name, firm, cik(dedup key)}
            hydrated: List[str] = []
            for w in whales:
                wid = w.get("id")
                if wid is None:
                    continue
                cik = (w.get("cik") or "").strip()
                # Dedup key = CIK — kept as a safety net even after migration 080
                # merged person↔fund rows (null CIK still needs the per-whale
                # sentinel to stay distinct). Same rule the card counts by.
                wmap[wid] = {
                    "name": w.get("name") or "",
                    # strip: a whitespace-only firm (bad row edit) must fall
                    # through to the "13F fund" subtitle, not render blank.
                    "firm": (w.get("firm_name") or "").strip(),
                    "cik": cik or f"nocik:{wid}",
                }
                hd = w.get("last_hydrated_at")
                if hd:
                    hydrated.append(str(hd)[:10])

            # Class-share tickers store either delimiter; match both forms.
            variants = list({sym, sym.replace("-", ".")})
            holdings = (
                fetch_all_rows(
                    lambda: sb.table("whale_holdings")
                    .select("whale_id, ticker, allocation, change_percent")
                    .in_("ticker", variants)
                    .gt("change_percent", 0),
                    # Unique key — see the accumulation read above. Single-page today
                    # thanks to the ticker filter, so this is latent rather than live.
                    order_by="id",
                    what="signals: holders of this ticker",
                )
            )
            # NOTE: disclosure_date is congress-only (migration 076) — always NULL for
            # 13F, so it's not selected here (avoids that coupling); the 13F `date` IS
            # the filing date, which iOS renders as "Filed …".
            trades = (
                fetch_all_rows(
                    lambda: sb.table("whale_trades")
                    .select("whale_id, ticker, action, trade_type, amount, date")
                    .in_("ticker", variants)
                    .eq("action", "BOUGHT"),
                    # `date` is a TEXT column on which every fund filing in the same
                    # quarter ties — not a paging key. Ordering is irrelevant to this
                    # caller's result anyway: the most-recent-per-whale pick below is a
                    # full scan of `trades` in Python, so the read only has to be COMPLETE.
                    order_by="id",
                    what="signals: BOUGHT trades for this ticker",
                )
            )
            # MOST-RECENT BOUGHT trade per whale (latest filing date; tie-break larger $)
            # → the "how much" + "when". Using the latest (not the largest-$) keeps
            # amount + date COHERENT (same trade) and shows the freshest activity.
            tmap: Dict[Any, Dict[str, Any]] = {}
            for t in trades:
                wid = t.get("whale_id")
                if wid not in wmap:
                    continue
                tdate_str = str(t.get("date") or "")[:10]
                amt = _finite_float(t.get("amount")) or 0.0
                cur = tmap.get(wid)
                if cur is None or (tdate_str, amt) > (cur["_date"], cur["_amt"]):
                    tmap[wid] = {**t, "_date": tdate_str, "_amt": amt}

            # Dedup by CIK so a fund registered under BOTH a person and a firm name
            # (Ray Dalio ↔ Bridgewater) appears ONCE — matching the card's distinct-
            # fund count. Keep the strongest row per CIK.
            best_by_cik: Dict[str, SignalHolderResponse] = {}
            for h in holdings:
                info = wmap.get(h.get("whale_id"))
                if info is None:
                    continue  # not a 13F registry whale
                cp = _finite_float(h.get("change_percent"))
                if cp is None or cp <= 0:
                    continue
                wid = h.get("whale_id")
                tr = tmap.get(wid)
                amount_est = _finite_float(tr.get("amount")) if tr else None
                is_new = ((tr.get("trade_type") or "") == "New") if tr else None
                tdate = (str(tr.get("date"))[:10] or None) if (tr and tr.get("date")) else None
                candidate = SignalHolderResponse(
                    whale_id=str(wid),
                    name=info["name"],
                    # Person-fronted whales carry their firm here ("Bridgewater
                    # Associates" under "Ray Dalio") so the name never appears
                    # without the firm; generic label only when no firm exists.
                    subtitle=info["firm"] or "13F fund",
                    transaction_date=tdate,
                    # disclosure_date stays None for 13F (congress-only; iOS falls back
                    # to transaction_date for the "Filed …" label).
                    allocation_percent=_finite_float(h.get("allocation")),
                    allocation_change=round(cp, 2),
                    is_new_position=is_new,
                    amount_est=amount_est,
                    action="BOUGHT",
                )
                key = info["cik"]
                existing = best_by_cik.get(key)
                if existing is None or _whale_row_rank(candidate) < _whale_row_rank(existing):
                    best_by_cik[key] = candidate

            rows = sorted(best_by_cik.values(), key=_whale_row_rank)
            as_of = max(hydrated) if hydrated else None
            return rows[:_DETAIL_ROWS], as_of
        except Exception as exc:  # noqa: BLE001
            # RE-RAISE (don't swallow to []): a Supabase failure must propagate so
            # get_ticker_detail returns an UNCACHED empty response and the next tap
            # retries — swallowing to [] here would pin an empty screen for the 10-min
            # cache TTL even after Supabase recovers. A genuine "no funds adding"
            # returns [] normally above (and is legitimately cached).
            logger.warning(
                "Whale detail for %s failed: %s: %s", sym, type(exc).__name__, exc
            )
            raise

    async def _detail_congress_rows(
        self, sym: str
    ) -> Tuple[List[SignalHolderResponse], Optional[str]]:
        """Members who bought this ticker — SAME FMP feed + 30d disclosure window as
        the card. ONE row per DISTINCT MEMBER (so the count matches the card's
        "N members buying"), showing their most recent filing. Tappable only for
        the ~8 politicians in our registry."""
        senate, house = await asyncio.gather(
            self.fmp.get_senate_latest(1000),
            self.fmp.get_house_latest(1000),
            return_exceptions=True,
        )
        # This list must match the card's distinct-member count. A truncated feed
        # would silently drop members from the drill-down while the card claims a
        # higher number, so an incomplete fetch yields NO rows, not short ones.
        for label, res in (("senate", senate), ("house", house)):
            if isinstance(res, BaseException):
                logger.warning(
                    "Congress detail for %s: %s feed incomplete (%s: %s) — "
                    "returning no rows rather than a short list",
                    sym, label, type(res).__name__, res,
                )
                # RAISE, not `return [], None`: get_ticker_detail caches a normal
                # return for the 10-min TTL, so a transient FMP failure would pin
                # "no members bought" on screen. Raising yields an UNCACHED empty
                # response and the next tap retries (same contract as whale / ceo).
                raise res
        reg = await asyncio.to_thread(self._congress_registry_map)
        now = datetime.now(timezone.utc)

        # Keep the most-recent-disclosure row PER MEMBER (chamber+identity) — mirrors
        # the card's distinct-member count, so the drill-down never shows a member twice.
        best: Dict[Tuple[str, str], Tuple[str, SignalHolderResponse]] = {}
        for chamber, trades in (("senate", senate), ("house", house)):
            if not isinstance(trades, list):
                continue
            for row in trades:
                if not isinstance(row, dict):
                    continue
                if _canonical_symbol(row.get("symbol")) != sym:
                    continue
                ttype = (row.get("type") or "").lower()
                if not ("purchase" in ttype or "buy" in ttype or "exchange" in ttype):
                    continue
                dstr = str(
                    row.get("disclosureDate") or row.get("dateReceived")
                    or row.get("date") or row.get("transactionDate") or ""
                )[:10]
                dobj = _parse_iso_date(dstr)
                if dobj is not None and not (-2 <= (now - dobj).days <= _CONGRESS_WINDOW_DAYS):
                    continue
                member = _congress_member_key(row, chamber)
                if not member:
                    continue
                mkey = (chamber, member)
                prev = best.get(mkey)
                if prev is not None and dstr <= prev[0]:
                    continue  # keep the member's most recent filing (first-seen on ties)
                first = (row.get("firstName") or row.get("first_name") or "").strip()
                last = (row.get("lastName") or row.get("last_name") or "").strip()
                name = (f"{first} {last}".strip()) or (row.get("office") or "").strip()
                if not name:
                    continue
                tdate = str(row.get("transactionDate") or "")[:10]
                low, high = parse_congress_amount_bounds(row.get("amount") or "")
                amount_range = format_amount_range(low, high) if (low or high) else None
                best[mkey] = (dstr, SignalHolderResponse(
                    whale_id=reg.get((chamber, _norm_name(name))),
                    name=name,
                    subtitle=_congress_role((row.get("district") or "").strip(), chamber),
                    transaction_date=tdate or None,
                    disclosure_date=dstr or None,
                    amount_range=amount_range,
                    owner=((row.get("owner") or "").strip() or None),
                    action="BOUGHT",
                ))

        entries = [row for _, row in best.values()]
        entries.sort(key=lambda r: (r.disclosure_date or ""), reverse=True)
        as_of = max((r.disclosure_date for r in entries if r.disclosure_date), default=None)
        return entries[:_DETAIL_ROWS], as_of

    async def _detail_ceo_rows(
        self, sym: str, *, ref_price: Optional[float] = None, market_cap: Optional[float] = None
    ) -> Tuple[List[SignalHolderResponse], Optional[str]]:
        """The CEO purchases behind this ticker's CEO Buys row — the SAME filters and
        price band as the card, so the rows add up to the card's dollar figure. One row
        per (CEO, trade day), summed across that day's fills.

        No try/except on purpose (the same contract as the whale and congress branches):
        an FMP failure must propagate so ``get_ticker_detail`` returns an UNCACHED empty
        response and the next tap retries. A genuine "no CEO buys" returns ``[]`` and is
        legitimately cached.
        """
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=_CEO_WINDOW_DAYS)).strftime("%Y-%m-%d")
        rows = await self.fmp.get_insider_trades_since(
            since,
            transaction_type="P-Purchase",
            symbol=sym,
            page_size=_CEO_DETAIL_PAGE_SIZE,
            max_pages=_CEO_DETAIL_MAX_PAGES,
        )
        candidates = [b for b in _extract_ceo_buys(rows, now=now) if b.symbol == sym]
        price = _finite_float(ref_price)
        cap = _finite_float(market_cap)
        if candidates and (price is None or price <= 0):
            # The profile header failed, but here the price is not decoration: it is the
            # reference the card's plausibility band ran against. Use the card's own source;
            # without ANY reference, raise (uncached) rather than list rows the card rejected.
            quotes = await price_source(self).get_quotes_list([sym])
            quote = next((q for q in quotes or [] if isinstance(q, dict)
                          and _canonical_symbol(q.get("symbol")) == sym), None)
            price = _finite_float(quote.get("price")) if quote else None
            cap = cap if cap is not None else (_finite_float(quote.get("marketCap")) if quote else None)
            if price is None or price <= 0:
                raise FMPUnavailableException(
                    f"CEO detail {sym}: no reference price for the plausibility band"
                )
        buys = [
            b for b in candidates
            if _ceo_price_plausible(b.price, price)
            and not (cap is not None and cap > 0 and b.dollars > _CEO_MAX_MCAP_SHARE * cap)
        ]
        grouped: Dict[Tuple[str, str], List[_CeoBuy]] = {}
        for b in buys:
            grouped.setdefault((b.reporter, b.transaction_date or b.filing_date), []).append(b)

        holders: List[SignalHolderResponse] = []
        for (_, day), fills in grouped.items():
            first = fills[0]
            dollars = sum(b.dollars for b in fills)
            shares = sum(b.shares for b in fills)
            holders.append(SignalHolderResponse(
                whale_id=None,
                name=normalize_insider_name(first.name_raw),
                subtitle=ceo_role_label(first.title_raw),
                transaction_date=first.transaction_date or None,
                disclosure_date=max(b.filing_date for b in fills),
                amount_est=round(dollars, 2) + 0.0 if math.isfinite(dollars) else None,
                shares=round(shares, 4) + 0.0 if math.isfinite(shares) else None,
                action="BOUGHT",
            ))
        holders.sort(key=lambda h: h.name)
        holders.sort(key=lambda h: -(h.amount_est or 0.0))
        holders.sort(key=lambda h: h.transaction_date or h.disclosure_date or "", reverse=True)
        as_of = max((b.filing_date for b in buys), default=None)
        return holders[:_DETAIL_ROWS], as_of

    def _congress_registry_map(self) -> Dict[Tuple[str, str], str]:
        """(chamber, normalized-name) → whale_id for our tracked politicians (~8).

        Chamber-scoped + order-sensitive keys prevent deep-linking a tap to the WRONG
        member (a Senate and a House "John Smith", or First/Last permutations). A
        genuine collision (two registry rows → one key) is logged and the FIRST kept,
        so a bad registry entry is visible rather than silently shadowing another."""
        try:
            sb = get_supabase()
            rows = (
                sb.table("whales")
                .select("id, name, fmp_name, data_source")
                .in_("data_source", ["congressional_house", "congressional_senate"])
                .limit(500)
                .execute()
                .data
                or []
            )
            m: Dict[Tuple[str, str], str] = {}
            for r in rows:
                wid = r.get("id")
                if wid is None:
                    continue
                chamber = "house" if "house" in (r.get("data_source") or "") else "senate"
                for nm in (r.get("fmp_name"), r.get("name")):
                    k = _norm_name(nm)
                    if not k:
                        continue
                    key = (chamber, k)
                    prev = m.get(key)
                    if prev is not None and prev != str(wid):
                        logger.warning(
                            "Congress registry name collision on %s: keeping whale_id=%s, "
                            "ignoring %s (name=%r)", key, prev, wid, r.get("name"),
                        )
                        continue
                    m[key] = str(wid)
            return m
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Congress registry map failed: %s: %s", type(exc).__name__, exc
            )
            return {}


# ── Singleton ─────────────────────────────────────────────────────────

_service: Optional[SignalsService] = None


def get_signals_service() -> SignalsService:
    global _service
    if _service is None:
        _service = SignalsService()
    return _service
