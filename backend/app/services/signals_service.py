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

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client, FMPClient
from app.services.earnings_service import _compute_surprise
# Reuse the dashboard's hardened primitives so signals fold class-share variants
# (BRK.B ↔ BRK-B) and reject NaN/Inf exactly like the scanners do. NOTE: the
# dashboard service must import THIS module function-locally to avoid a cycle.
from app.services.home_dashboard_service import _canonical_symbol, _finite_float
from app.schemas.home_dashboard import (
    SignalsGroupResponse,
    SignalGroupResponse,
    SignalRowResponse,
)
from pydantic import ValidationError

logger = logging.getLogger(__name__)


# ── Config ─────────────────────────────────────────────────────────────
_SIGNALS_MEM_TTL_SECONDS = 2700          # 45 min in-memory freshness ceiling
_SIGNALS_SUPABASE_TTL_HOURS = 24         # Tier-2 survives restart; sources daily/quarterly
_SIGNALS_CACHE_KEY = "signals"
_SIGNALS_TABLE = "signals_cache"
_SIGNALS_BUILD_TIMEOUT_SECONDS = 8       # never let a cold build block the dashboard

_SIGNAL_ROWS = 5                         # drill-down leaders per card

# Congress
_CONGRESS_WINDOW_DAYS = 14               # window on DISCLOSURE date
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


# ── Singleton ─────────────────────────────────────────────────────────

_service: Optional[SignalsService] = None


def get_signals_service() -> SignalsService:
    global _service
    if _service is None:
        _service = SignalsService()
    return _service
