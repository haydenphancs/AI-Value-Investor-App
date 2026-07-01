"""
Signals Service — builds the Home "App-Exclusive Signals" section
(``HomeDashboardView``): three "signals you won't find on free trackers" cards.

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
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import re

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client, FMPClient
from app.services.earnings_service import _compute_surprise
# Reuse the dashboard's hardened primitives so signals fold class-share variants
# (BRK.B ↔ BRK-B) and reject NaN/Inf exactly like the scanners do. NOTE: the
# dashboard service must import THIS module function-locally to avoid a cycle.
from app.services.home_dashboard_service import _canonical_symbol, _finite_float
from app.services._whale_common import (
    parse_congress_amount_bounds,
    format_amount_range,
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

logger = logging.getLogger(__name__)


# ── Config ─────────────────────────────────────────────────────────────
_SIGNALS_MEM_TTL_SECONDS = 2700          # 45 min in-memory freshness ceiling
_SIGNALS_SUPABASE_TTL_HOURS = 24         # Tier-2 survives restart; sources daily/quarterly
_SIGNALS_CACHE_KEY = "signals_v2"        # bump to invalidate stale rows written by the
                                         # pre-30d-window / pre-10-rows code — the old
                                         # "signals" row (congress=null, 5 whale) lingers
                                         # for its 24h TTL but is now ignored, so a redeploy
                                         # serves fresh data immediately (self-healing).
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
_EARNINGS_WINDOW_DAYS = 10
_EARNINGS_MIN_ABS_SURPRISE = 10.0        # |surprise%| ≥ 10 to count as a "shocker"
_EARNINGS_MAX_ABS_SURPRISE = 1000.0      # cap penny-EPS blowups (est 0.01 → thousands %)
_EARNINGS_QUOTE_CANDIDATES = 25          # rank this many, then apply the market-cap floor
_EARNINGS_MIN_MARKET_CAP = 250_000_000   # $250M quality floor (parity with the scanner cards)

_BAD_SYMBOLS = {"", "--", "N/A", "NA", "NONE"}

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
    """Rank recent US reporters by |EPS surprise %| (beats AND misses), value SIGNED.

    Reuses ``earnings_service._compute_surprise`` (returns ``None`` for a zero
    estimate → skipped). Foreign listings (dotted symbols) are dropped; rows
    missing actual/estimate are skipped; a symbol reported twice keeps the
    larger-magnitude surprise; ``|surprise|`` outside [min_abs, max_abs] is dropped
    (upper cap kills penny-EPS artifacts). Returns ``None`` when nothing clears the
    threshold. NOTE: no market-cap filter here (calendar rows carry none) — the
    caller ``_build_earnings`` over-ranks candidates then applies the $250M floor.
    """
    if not isinstance(calendar, list):
        return None

    best: Dict[str, Dict[str, Any]] = {}
    latest_dates: Dict[str, str] = {}
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
        surprise = _compute_surprise(actual, estimate)
        if surprise is None:
            continue
        surprise = surprise + 0.0  # collapse a possible signed -0.0 → 0.0
        if not (min_abs <= abs(surprise) <= max_abs):
            continue
        date_str = str(row.get("date") or "")[:10]
        # Track the latest QUALIFYING report date per symbol independently of which
        # surprise we keep, so as_of reflects the freshest report — not merely the
        # date of the largest-magnitude one (a symbol can report twice in a window).
        if date_str and date_str > latest_dates.get(sym, ""):
            latest_dates[sym] = date_str
        cur = best.get(sym)
        if cur is None or abs(surprise) > abs(cur["surprise"]):
            best[sym] = {"surprise": surprise}

    if not best:
        return None

    ranked = sorted(
        best.items(),
        key=lambda kv: (-abs(kv[1]["surprise"]), -kv[1]["surprise"], kv[0]),
    )
    entries = [
        SignalRowResponse(
            rank=i + 1, symbol=sym, name="", value=round(e["surprise"], 2) + 0.0,
        )
        for i, (sym, e) in enumerate(ranked[:top_n])
    ]
    as_of = max(
        (latest_dates.get(sym, "") for sym, _ in ranked[:top_n]), default=""
    ) or None
    return SignalGroupResponse(kind="earnings", entries=entries, as_of_date=as_of)


# ── Service ─────────────────────────────────────────────────────────────


class SignalsService:
    """Builds the three App-Exclusive Signal cards from FMP + the whale registry."""

    # Class-level so the cache/dedup are shared across requests (mirrors scanners).
    _cache: Dict[str, Tuple[float, SignalsGroupResponse]] = {}
    _inflight: Dict[str, asyncio.Future] = {}
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
        if cached is not None and (time.time() - cached[0]) < _SIGNALS_MEM_TTL_SECONDS:
            logger.debug("Signals served from in-memory cache")
            return cached[1]

        inflight = self._inflight.get(_SIGNALS_CACHE_KEY)
        if inflight is not None:
            logger.debug("Signals joining in-flight build")
            return await inflight

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
            else:
                try:
                    result = await self._build()
                    # Cache/persist ONLY a build that produced ≥1 group — never pin a
                    # transient triple-failure (all-None) for 45 min / 24 h.
                    if result.congress or result.whale or result.earnings:
                        self._cache[_SIGNALS_CACHE_KEY] = (time.time(), result)
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

    # ── Build (3 branches, degrade independently) ─────────────────────

    async def _build(self) -> SignalsGroupResponse:
        congress, whale, earnings = await asyncio.gather(
            self._build_congress(),
            self._build_whale(),
            self._build_earnings(),
            return_exceptions=True,
        )

        def _unwrap(res: Any, step: str) -> Optional[SignalGroupResponse]:
            if isinstance(res, BaseException):
                logger.warning(
                    "Signal %s failed: %s: %s", step, type(res).__name__, res
                )
                return None
            return res

        return SignalsGroupResponse(
            congress=_unwrap(congress, "congress"),
            whale=_unwrap(whale, "whale"),
            earnings=_unwrap(earnings, "earnings"),
        )

    async def _build_congress(self) -> Optional[SignalGroupResponse]:
        senate, house = await asyncio.gather(
            self.fmp.get_senate_latest(1000),
            self.fmp.get_house_latest(1000),
        )
        # Both integration methods self-swallow FMP errors → []. Empty is the
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
                sb.table("whales")
                .select("id, cik, last_hydrated_at")
                .eq("data_source", "13f")
                .limit(2000)
                .execute()
                .data
                or []
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
                sb.table("whale_holdings")
                .select("whale_id, ticker, company_name, change_percent")
                .gt("change_percent", 0)
                # Explicit high limit: PostgREST caps at 1000 rows by default, which
                # (25 whales × up-to-30 holdings) is close today and WILL truncate as
                # the registry grows — silently dropping funds from the count.
                .limit(10000)
                .execute()
                .data
                or []
            )
            as_of = max(hydrated) if hydrated else None
            return _aggregate_whale(holdings, cik_map, as_of=as_of)
        except Exception as exc:  # noqa: BLE001 — degrade this card, never the dashboard
            logger.warning(
                "Whale Accumulation query failed: %s: %s", type(exc).__name__, exc
            )
            return None

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
        quotes = await self.fmp.get_batch_quotes(symbols)
        qmap = {
            _canonical_symbol(q.get("symbol")): q
            for q in quotes
            if isinstance(q, dict) and q.get("symbol")
        }

        kept: List[SignalRowResponse] = []
        for e in candidates.entries:  # already ranked by |surprise| desc
            market_cap = _finite_float(qmap.get(e.symbol, {}).get("marketCap"))
            if market_cap is None or market_cap < _EARNINGS_MIN_MARKET_CAP:
                continue
            kept.append(e)
            if len(kept) >= _SIGNAL_ROWS:
                break

        if not kept:
            logger.info(
                "Earnings Shockers: no candidate cleared the $%dM market-cap floor "
                "— omitting card", _EARNINGS_MIN_MARKET_CAP // 1_000_000,
            )
            return None

        entries = [
            SignalRowResponse(rank=i + 1, symbol=e.symbol, name=e.name, value=e.value)
            for i, e in enumerate(kept)
        ]
        return SignalGroupResponse(
            kind="earnings", entries=entries, as_of_date=candidates.as_of_date
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
        """WHO bought/added `ticker` behind the whale/congress signal, WHEN, HOW
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
            return await inflight

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
                sb.table("whales")
                .select("id, name, cik, last_hydrated_at")
                .eq("data_source", "13f")
                .limit(2000)
                .execute()
                .data
                or []
            )
            wmap: Dict[Any, Dict[str, str]] = {}   # whale_id -> {name, cik(dedup key)}
            hydrated: List[str] = []
            for w in whales:
                wid = w.get("id")
                if wid is None:
                    continue
                cik = (w.get("cik") or "").strip()
                # Dedup key = CIK — a person and their fund share one 13F filing/CIK
                # (Ray Dalio ↔ Bridgewater). Null CIK → per-whale sentinel so it
                # stays distinct. Same rule the card counts by.
                wmap[wid] = {"name": w.get("name") or "", "cik": cik or f"nocik:{wid}"}
                hd = w.get("last_hydrated_at")
                if hd:
                    hydrated.append(str(hd)[:10])

            # Class-share tickers store either delimiter; match both forms.
            variants = list({sym, sym.replace("-", ".")})
            holdings = (
                sb.table("whale_holdings")
                .select("whale_id, ticker, allocation, change_percent")
                .in_("ticker", variants)
                .gt("change_percent", 0)
                .limit(5000)
                .execute()
                .data
                or []
            )
            trades = (
                sb.table("whale_trades")
                # NOTE: disclosure_date is congress-only (migration 076) — always NULL
                # for 13F, so it's not selected here (avoids that coupling); the 13F
                # `date` IS the filing date, which iOS renders as "Filed …".
                .select("whale_id, ticker, action, trade_type, amount, date")
                .in_("ticker", variants)
                .eq("action", "BOUGHT")
                .limit(5000)
                .execute()
                .data
                or []
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
                    subtitle="13F fund",
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
        )
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
