"""
Home-screen widget: which ticker moved most TODAY, and why.

Strictly a DAILY surface. That word is the whole design constraint, and getting it
wrong is what the first build did.

READ PATH NEVER CALLS GEMINI — the same invariant `updates.py` and
`news_insight_service` state in their headers, and it matters more here: a widget
refreshes on the OS's schedule, unattended, at a cadence no client can be
rate-limited into respecting.

WHAT WENT WRONG THE FIRST TIME
------------------------------
This service used to serve the Updates card's news `headline` as the reason, because
the grounded catalyst almost never fires (measured 2026-08-14: 12 live cards, zero
with a `price_move`). That shipped lines like *"Archer Aviation explores new markets
and strategic growth"* under a red −5.02% — a generic PR headline that explains
nothing and reads as a cause purely by sitting underneath the number.

Reaching for the grounded catalyst instead was worse. Its only cached ACHR row
described a **+42.7% fifteen-day rally**. Not a wrong-signed answer — a correct answer
to a *different question*. The filter that matters is the WINDOW, not the sign.

THE REBUILD: ATTRIBUTION, NOT GENERATION
----------------------------------------
"Why did it move today" has a small enumerable answer set, and almost none of it needs
a model: earnings today · an analyst action today · a classifiable headline · its
industry moved · the market moved · it gapped at the open · nothing. Each is a dated
fact. `daily_move_attribution` decides; this service only feeds it.

The cost argument, which is the reason this is viable at all:

    industry-performance-snapshot   ONE call, every ticker
    earnings-calendar (t-1 .. t)    ONE call, every ticker
    ^GSPC daily move                already on the batch quote
    open / previousClose gap split  already on the batch quote, pure arithmetic
    grades (analyst actions)        ONE call, HEADLINE TICKER ONLY

So attributing 200 movers costs the same two shared calls as attributing one, against
a per-ticker paid web search in the old design. And because every branch is arithmetic
or a dated record, the output cannot hallucinate.

`cause.kind == "none"` is a real answer and the common one. For ACHR it renders
*"Aerospace & Defense fell 1.2%; ACHR moved far more. No clear catalyst in today's
news."* — which is both true and more useful than any headline available.

RANKING
-------
By continuous z (`updates_materiality.move_z`), not raw percent and not `move_score`.
`move_score` is tier-bucket + raw magnitude, so a Notable +9% (z≈1.1) outranks an
Unusual +3% (z≈2.4) — raw-percent ranking wearing a z-score hat. Rows with no σ cannot
be placed on that axis and sort *after* every row that has one, rather than being
silently treated as z=0.

MARKET MODE RANKS INSIDE THE SWEPT UNIVERSE, ON PURPOSE
-------------------------------------------------------
The obvious source for "biggest mover in the market" is FMP's biggest-gainers list. It
is the wrong source: that population is disjoint from the one with σ and cached cards,
so those movers cannot be z-ranked at all. Market mode ranks the tickers Caydex
actually tracks, and the widget's own description says so.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.schemas.widget import (
    WidgetBasketResponse,
    WidgetCauseResponse,
    WidgetMoveContextResponse,
    WidgetMoverPayload,
    WidgetMoverResponse,
)
from app.services.daily_move_attribution import attribute
from app.services.news_insight_service import get_news_insight_service
from app.services.updates_materiality import classify_move, finite, move_z
from app.services.volatility_cache_service import get_volatility_cache_service
from app.utils.market_hours import ET, session_phase

logger = logging.getLogger(__name__)

MARKET_SCOPE = "__MARKET__"
# The index the sweeper already quotes; its daily move is the "whole market" leg.
MARKET_INDEX_SYMBOL = "^GSPC"

# How many tickers we will rank. The sweeper's own ceiling is 200; matching it means
# the widget can never be asked about a scope the sweeper has not considered.
_MAX_UNIVERSE = 200

# A move must clear this to be considered "moved" for the basket test, when no σ is
# available to judge it properly. 2% mirrors `_NOTABLE_PCT` in updates_materiality.
_BASKET_FALLBACK_PCT = 2.0
# With σ, "moved" means at least a 1σ day.
_BASKET_MIN_Z = 1.0
# Below this many holdings, "they all moved together" is not a factor observation —
# a 2-stock portfolio agrees by coincidence roughly half the time.
_BASKET_MIN_HOLDINGS = 3
_BASKET_MIN_MOVERS = 3
# A sector is only claimed as the driver when this share of the movers share it.
_BASKET_SECTOR_SHARE = 2.0 / 3.0

_MEM_TTL_SECONDS = 60

# How many movers the payload carries. 1 headline + 3 runners-up fills the large
# family without a second round trip; the service already reads their cards.
_RUNNERS_UP = 4

# The two universe-wide snapshots change at most once a day, so an hour of reuse is
# generous and still costs at most 24 calls/day for the whole product.
_CONTEXT_TTL_SECONDS = 3600


# ── Pure helpers (no I/O — exhaustively testable) ─────────────────────



@dataclass
class _MarketContext:
    """Universe-wide facts for one sweep. Two FMP calls, shared by every ticker.

    The whole cost argument for this rebuild lives here: `industry-performance-snapshot`
    and `earnings-calendar` each cover EVERY ticker in one request, and the market's own
    move rides on the batch quote already fetched. So attributing 200 movers costs the
    same two calls as attributing one — versus a per-ticker paid web search, which is
    what the old design did.
    """

    market_change: Optional[float] = None
    # industry name (lowercased) -> today's % change
    industry_changes: Dict[str, float] = field(default_factory=dict)
    # ticker -> industry name, from the SHARED company_profile_cache
    ticker_industry: Dict[str, str] = field(default_factory=dict)
    # ticker -> today's/yesterday's earnings-calendar row
    earnings: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def industry_for(self, ticker: str) -> tuple[Optional[str], Optional[float]]:
        name = self.ticker_industry.get(ticker.upper())
        if not name:
            return None, None
        return name, self.industry_changes.get(name.strip().lower())

    def earnings_for(self, ticker: str) -> Optional[Dict[str, Any]]:
        return self.earnings.get(ticker.upper())


@dataclass(frozen=True)
class RankedMover:
    ticker: str
    change_percent: Optional[float]
    price: Optional[float]
    company_name: Optional[str]
    sigma_daily: Optional[float]
    z: Optional[float]
    tier: Optional[str]
    # Both ride on the batch-quote row already fetched, and nothing else in the repo
    # reads `open` — so the overnight/intraday split is free arithmetic.
    open_price: Optional[float] = None
    previous_close: Optional[float] = None


def rank_movers(rows: Sequence[Dict[str, Any]]) -> List[RankedMover]:
    """Order candidates by how unusual the move is for each ticker.

    A row whose change is missing or non-finite is DROPPED, not ranked as 0.0 —
    an unreadable quote is not a flat day, and this repo has shipped that exact
    confusion more than once (NaN reaching `max()` and winning).

    Rows with a usable σ sort first, by z descending. Rows without σ follow, by
    absolute change descending, because they cannot be placed on the z axis at all
    and pretending otherwise would let an unjudgeable ticker outrank a measured
    one. Ties break on ticker ascending so the widget does not flip between two
    equal movers on consecutive refreshes.
    """
    ranked: List[RankedMover] = []
    for row in rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        change = finite(row.get("change_percent"))
        if change is None:
            continue
        sigma = finite(row.get("sigma_daily"))
        z = move_z(change, sigma)
        ranked.append(
            RankedMover(
                ticker=ticker,
                change_percent=change,
                price=finite(row.get("price")),
                company_name=(row.get("company_name") or None),
                sigma_daily=sigma,
                z=z,
                tier=classify_move(change, sigma, row.get("market_cap")),
                open_price=finite(row.get("open")),
                previous_close=finite(row.get("previous_close")),
            )
        )

    ranked.sort(
        key=lambda m: (
            0 if m.z is not None else 1,           # σ-judged rows first
            -(m.z if m.z is not None else 0.0),    # then most unusual
            -abs(m.change_percent or 0.0),         # then biggest raw move
            m.ticker,                              # then stable
        )
    )
    return ranked


def deterministic_reason(change_percent: Optional[float], z: Optional[float]) -> str:
    """The always-available line. Never wrong, because it only restates arithmetic.

    This is what a mover with no catalyst and no news gets, and it is the reason the
    widget can never be blank. Phrased as a comparison rather than a bare number
    because "−4.8%" alone tells a reader nothing about whether that is remarkable
    for this particular stock.
    """
    pct = finite(change_percent)
    if pct is None:
        return "Price change unavailable right now."
    direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
    if pct == 0:
        return "Flat on the day."
    if z is None:
        return f"{'Up' if pct > 0 else 'Down'} {abs(pct):.1f}% today."
    return (
        f"{'Up' if pct > 0 else 'Down'} {abs(pct):.1f}% today — "
        f"about {z:.1f}× its normal daily range."
    )


def _same_sign(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    if a == 0 or b == 0:
        return False
    return (a > 0) == (b > 0)


def _et_date(value: Any) -> Optional[str]:
    """ET calendar date of a timestamp, as ISO — the app's trading-day bucket."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).date().isoformat()


def _classified_today_news(card: Optional[Dict[str, Any]], today_et: str) -> tuple[list, bool]:
    """Today's headlines, run through the EXISTING catalyst classifier.

    Reuses `_classify_news_catalyst` / `NEWS_CATALYST_KEYWORDS` from the report
    collector so the eleven-tag vocabulary (FDA Approval, M&A, Guidance Cut, …) lives
    in exactly one place. Only the card's own headline is available here, and only when
    the card was generated TODAY — a 48h roll-up is not a same-day catalyst.

    Returns `(classified, had_news)` so the no-cause sentence can distinguish "no news
    at all" from "news, but nothing that explains a move".
    """
    if not isinstance(card, dict):
        return [], False
    if _et_date(card.get("generated_at")) != today_et:
        return [], False
    headline = str(card.get("headline") or "").strip()
    if not headline:
        return [], False

    try:
        from app.services.agents.ticker_report_data_collector import (
            _classify_news_catalyst,
        )

        tag = _classify_news_catalyst(headline, "")
    except Exception as e:
        logger.warning(
            "widget: news classifier unavailable (%s: %s)", type(e).__name__, e
        )
        return [], True
    return ([(tag, headline)] if tag else []), True


def _moved(m: RankedMover) -> bool:
    """Did this holding actually do something, judged against its own volatility."""
    if m.z is not None:
        return m.z >= _BASKET_MIN_Z
    return abs(m.change_percent or 0.0) >= _BASKET_FALLBACK_PCT


def detect_basket(
    holdings: Sequence[RankedMover], sectors: Dict[str, Optional[str]]
) -> Optional[WidgetBasketResponse]:
    """The correlated-move case: several holdings moving together for one reason.

    Returns None far more often than not, and every one of those refusals is
    deliberate — a group claim that is not really a group is worse than silence,
    because it invents a shared cause the user will act on.

    Refuses when:

    * the portfolio is tiny — with 2 holdings, "both fell" happens by coincidence
      about half the time and says nothing about a factor;
    * fewer than 3 holdings actually moved;
    * the movers disagree on direction — that is not a shared driver, it is a normal
      day, and the honest read is "no single story";
    * **the portfolio is single-sector.** This one is subtle and is the trap worth
      naming: if every holding is Technology, then "all your movers are Technology"
      is a fact about the *portfolio*, not about the market. Claiming tech as the
      driver there is circular. A factor claim requires the portfolio to have had
      something else it could have moved instead.

    A mover whose sector is unknown counts toward breadth but never toward a sector
    claim — bucketing unknowns together and reporting "all Other moved" would
    manufacture a factor out of missing data.
    """
    usable = [m for m in holdings if m.change_percent is not None]
    if len(usable) < _BASKET_MIN_HOLDINGS:
        return None

    movers = [m for m in usable if _moved(m)]
    if len(movers) < _BASKET_MIN_MOVERS:
        return None

    ups = [m for m in movers if (m.change_percent or 0) > 0]
    downs = [m for m in movers if (m.change_percent or 0) < 0]
    if ups and downs:
        return None
    group = ups or downs
    if len(group) < _BASKET_MIN_MOVERS:
        return None
    direction = "up" if ups else "down"

    avg = sum(m.change_percent or 0.0 for m in group) / len(group)
    tickers = sorted(m.ticker for m in group)

    # Sector claim, only when it is not an artifact of a concentrated portfolio.
    portfolio_sectors = {
        s for s in (sectors.get(m.ticker) for m in usable) if s
    }
    counts: Dict[str, int] = {}
    for m in group:
        sec = sectors.get(m.ticker)
        if sec:
            counts[sec] = counts.get(sec, 0) + 1

    factor_kind: Optional[str] = None
    factor_label: Optional[str] = None
    if counts and len(portfolio_sectors) >= 2:
        top_sector, top_n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        if top_n >= math.ceil(len(group) * _BASKET_SECTOR_SHARE):
            factor_kind, factor_label = "sector", top_sector

    verb = "rose" if direction == "up" else "fell"
    if factor_label:
        text = (
            f"{len(group)} of your {len(usable)} holdings {verb} together — "
            f"mostly {factor_label}, averaging {avg:+.1f}%."
        )
    else:
        text = (
            f"{len(group)} of your {len(usable)} holdings {verb} together, "
            f"averaging {avg:+.1f}% — no single sector driving it."
        )

    return WidgetBasketResponse(
        direction=direction,
        moved_count=len(group),
        total_count=len(usable),
        factor_kind=factor_kind,
        factor_label=factor_label,
        average_change_percent=round(avg, 2),
        tickers=tickers,
        text=text,
    )


def _iso_now() -> str:
    """No fractional seconds — Swift's `.iso8601` strategy rejects them."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Service ───────────────────────────────────────────────────────────


class WidgetMoversService:
    """Two-tier cache + in-flight dedup, per CLAUDE.md invariant #4.

    The in-memory tier is short (60s) because a widget's value is freshness, and the
    upstream is cheap: everything below is either a batched Supabase read or one FMP
    batch-quote shared by every caller in that window.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[float, WidgetMoverPayload]] = {}
        self._inflight: Dict[str, asyncio.Future] = {}
        self._ctx_cache: Optional[Tuple[float, _MarketContext]] = None

    # ── public ───────────────────────────────────────────────────────

    async def get_market_mover(self) -> WidgetMoverPayload:
        return await self._cached("market", self._build_market)

    async def get_portfolio_mover(self, user_id: str, tickers: Sequence[str]) -> WidgetMoverPayload:
        key = "portfolio:" + ",".join(sorted({t.upper() for t in tickers if t}))
        return await self._cached(key, lambda: self._build_portfolio(user_id, tickers))

    # ── cache plumbing ───────────────────────────────────────────────

    async def _cached(self, key: str, build) -> WidgetMoverPayload:
        hit = self._cache.get(key)
        if hit and (time.monotonic() - hit[0]) < _MEM_TTL_SECONDS:
            return hit[1]

        existing = self._inflight.get(key)
        if existing is not None:
            return await asyncio.shield(existing)

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            payload = await build()
            self._cache[key] = (time.monotonic(), payload)
            if not fut.done():
                fut.set_result(payload)
            return payload
        except BaseException as e:
            # Waiters must not hang when the leader is cancelled — the shape
            # news_cache_service uses, and the one the Learn services got wrong.
            if not fut.done():
                fut.set_exception(e)
            raise
        finally:
            self._inflight.pop(key, None)

    # ── builders ─────────────────────────────────────────────────────

    async def _build_market(self) -> WidgetMoverPayload:
        tickers = await self._swept_universe()
        ranked, cards = await self._rank_and_read(tickers)
        ctx = await self._market_context([m.ticker for m in ranked])
        grades = await self._head_grades(ranked)
        return self._payload(
            mode="market", ranked=ranked, cards=cards, ctx=ctx,
            basket=None, head_grades=grades,
        )

    async def _build_portfolio(
        self, user_id: str, tickers: Sequence[str]
    ) -> WidgetMoverPayload:
        ranked, cards = await self._rank_and_read(tickers)
        symbols = [m.ticker for m in ranked]
        ctx, sectors, grades = await asyncio.gather(
            self._market_context(symbols),
            self._sectors(user_id, symbols),
            self._head_grades(ranked),
            return_exceptions=True,
        )
        if isinstance(ctx, BaseException):
            logger.warning("widget: market context failed: %s", ctx)
            ctx = _MarketContext()
        if isinstance(sectors, BaseException):
            logger.warning("widget: sector read failed: %s", sectors)
            sectors = {}
        if isinstance(grades, BaseException):
            logger.warning("widget: grades read failed: %s", grades)
            grades = None

        return self._payload(
            mode="portfolio", ranked=ranked, cards=cards, ctx=ctx,
            basket=detect_basket(ranked, sectors), head_grades=grades,
        )

    # ── same-day context (the whole cost story) ──────────────────────

    async def _market_context(self, tickers: Sequence[str]) -> "_MarketContext":
        """Two universe-wide FMP calls plus one batched Supabase read.

        Cached for an hour because none of it changes intraday in a way that would
        alter an attribution: an industry's daily % drifts, and today's earnings
        calendar is fixed by the opening bell.
        """
        hit = self._ctx_cache
        if hit and (time.monotonic() - hit[0]) < _CONTEXT_TTL_SECONDS:
            ctx = hit[1]
        else:
            ctx = await self._fetch_market_context()
            self._ctx_cache = (time.monotonic(), ctx)

        # The ticker→industry map is per-request (it depends on which tickers we are
        # attributing), but it is one batched select against a shared cache table.
        ctx.ticker_industry = await self._industries(tickers)
        return ctx

    async def _fetch_market_context(self) -> "_MarketContext":
        fmp = get_fmp_client()
        today = datetime.now(ET).date()

        async def _industry_perf():
            rows = await fmp.get_industry_performance()
            out: Dict[str, float] = {}
            for r in rows or []:
                name = str(r.get("industry") or "").strip().lower()
                chg = finite(r.get("changesPercentage") or r.get("averageChange"))
                if name and chg is not None:
                    out[name] = chg
            return out

        async def _earnings():
            rows = await fmp.get_earnings_calendar(
                (today - timedelta(days=1)).isoformat(), today.isoformat()
            )
            out: Dict[str, Dict[str, Any]] = {}
            for r in rows or []:
                sym = str(r.get("symbol") or "").upper()
                if sym:
                    out.setdefault(sym, r)
            return out

        async def _market():
            rows = await fmp.get_batch_quotes_bulk([MARKET_INDEX_SYMBOL])
            for r in rows or []:
                if str(r.get("symbol") or "").upper() == MARKET_INDEX_SYMBOL:
                    return finite(r.get("changePercentage"))
            return None

        industry, earnings, market = await asyncio.gather(
            _industry_perf(), _earnings(), _market(), return_exceptions=True
        )
        ctx = _MarketContext()
        # Each leg degrades independently: losing the earnings calendar must not cost
        # us the industry attribution.
        if isinstance(industry, dict):
            ctx.industry_changes = industry
        else:
            logger.warning("widget: industry snapshot failed: %s", industry)
        if isinstance(earnings, dict):
            ctx.earnings = earnings
        else:
            logger.warning("widget: earnings calendar failed: %s", earnings)
        if not isinstance(market, BaseException):
            ctx.market_change = market
        else:
            logger.warning("widget: market quote failed: %s", market)
        return ctx

    async def _head_grades(
        self, ranked: Sequence[RankedMover]
    ) -> Optional[List[Dict[str, Any]]]:
        """Analyst actions for the HEADLINE ticker only.

        The one per-ticker call in the whole attribution chain, and the widget renders
        exactly one headline — so it is 1 call, not 200. `get_grades` has no
        market-wide sibling wired, which is why this is scoped so tightly.
        """
        if not ranked:
            return None
        try:
            return await get_fmp_client().get_grades(ranked[0].ticker, limit=10)
        except Exception as e:
            logger.warning(
                "widget: grades unavailable for %s (%s: %s)",
                ranked[0].ticker, type(e).__name__, e,
            )
            return None

    async def _industries(self, tickers: Sequence[str]) -> Dict[str, str]:
        """ticker -> industry, from the SHARED company_profile_cache.

        Shared, not per-user: `watchlist_items.industry` is sparsely populated and
        differs between users holding the same stock, which would give two people
        different explanations for the same market day.
        """
        syms = [t.upper() for t in dict.fromkeys(tickers) if t]
        if not syms:
            return {}

        def _query() -> Dict[str, str]:
            out: Dict[str, str] = {}
            try:
                res = (
                    get_supabase()
                    .table("company_profile_cache")
                    .select("ticker, profile_json")
                    .in_("ticker", syms)
                    .execute()
                )
                for r in res.data or []:
                    prof = r.get("profile_json") or {}
                    ind = str(prof.get("industry") or "").strip()
                    if ind:
                        out[str(r["ticker"]).upper()] = ind
            except Exception as e:
                logger.warning(
                    "widget: industry map read failed (%s: %s)", type(e).__name__, e
                )
            return out

        return await asyncio.to_thread(_query)

    def _build_mover(
        self,
        m: RankedMover,
        *,
        cards: Dict[str, Optional[Dict[str, Any]]],
        ctx: "_MarketContext",
        today: date,
        today_iso: str,
        grade_rows: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> WidgetMoverResponse:
        classified, had_news = _classified_today_news(cards.get(m.ticker), today_iso)
        industry = ctx.industry_for(m.ticker)

        a = attribute(
            ticker=m.ticker,
            change_percent=m.change_percent,
            today=today,
            z=m.z,
            open_price=m.open_price,
            previous_close=m.previous_close,
            industry_name=industry[0],
            industry_change_percent=industry[1],
            market_change_percent=ctx.market_change,
            earnings_row=ctx.earnings_for(m.ticker),
            grade_rows=grade_rows,
            classified_news=classified,
            had_news=had_news,
        )
        # `attribute` returns None only for an unreadable move, which `rank_movers`
        # has already filtered out — but degrade rather than crash if that changes.
        mc = a.context if a else None
        return WidgetMoverResponse(
            ticker=m.ticker,
            company_name=m.company_name,
            change_percent=m.change_percent,
            price=m.price,
            tier=m.tier,
            z=round(m.z, 2) if m.z is not None else None,
            cause=WidgetCauseResponse(
                kind=(a.kind.value if a else "none"),
                tag=(a.tag if a else None),
                detail=(a.detail if a else deterministic_reason(m.change_percent, m.z)),
            ),
            context=WidgetMoveContextResponse(
                change_percent=m.change_percent or 0.0,
                z=round(m.z, 2) if m.z is not None else None,
                gap_percent=round(mc.gap_percent, 2) if mc and mc.gap_percent is not None else None,
                intraday_percent=(
                    round(mc.intraday_percent, 2)
                    if mc and mc.intraday_percent is not None else None
                ),
                gap_dominant=bool(mc.gap_dominant) if mc else False,
                industry_name=industry[0],
                industry_change_percent=(
                    round(industry[1], 2) if industry[1] is not None else None
                ),
                market_change_percent=(
                    round(ctx.market_change, 2) if ctx.market_change is not None else None
                ),
            ),
        )

    def _payload(
        self,
        *,
        mode: str,
        ranked: Sequence[RankedMover],
        cards: Dict[str, Optional[Dict[str, Any]]],
        ctx: "_MarketContext",
        basket: Optional[WidgetBasketResponse],
        head_grades: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> WidgetMoverPayload:
        today = datetime.now(ET).date()
        today_iso = today.isoformat()

        head: Optional[WidgetMoverResponse] = None
        runners: List[WidgetMoverResponse] = []
        if ranked:
            head = self._build_mover(
                ranked[0], cards=cards, ctx=ctx, today=today,
                today_iso=today_iso, grade_rows=head_grades,
            )
            # Runners-up get everything except the per-ticker grades lookup, which is
            # the only paid call in the chain and is spent on the headline alone.
            runners = [
                self._build_mover(
                    m, cards=cards, ctx=ctx, today=today, today_iso=today_iso
                )
                for m in ranked[1:_RUNNERS_UP]
            ]

        return WidgetMoverPayload(
            mode=mode,
            as_of=_iso_now(),
            market_session=session_phase(),
            headline_mover=head,
            basket=basket,
            runners_up=runners,
        )

    # ── data access (all best-effort; a widget degrades, never 500s) ──

    async def _swept_universe(self) -> List[str]:
        """The tickers the sweeper considers — the only ones with σ and cards."""

        def _query() -> List[str]:
            try:
                res = get_supabase().rpc(
                    "get_top_watchlist_tickers", {"n": _MAX_UNIVERSE}
                ).execute()
                return [
                    str(r["ticker"]).upper() for r in (res.data or []) if r.get("ticker")
                ]
            except Exception as e:
                logger.warning(
                    "widget: swept universe unreadable (%s: %s) — market mode will "
                    "fall back to the market story",
                    type(e).__name__, e,
                )
                return []

        rows = await asyncio.to_thread(_query)
        return [t for t in rows if t != MARKET_SCOPE]

    async def _rank_and_read(
        self, tickers: Sequence[str]
    ) -> Tuple[List[RankedMover], Dict[str, Optional[Dict[str, Any]]]]:
        symbols = [t.upper() for t in dict.fromkeys(tickers) if t]
        if not symbols:
            return [], {}

        quotes, sigmas = await asyncio.gather(
            self._quotes(symbols),
            get_volatility_cache_service().get_sigmas_bulk(symbols),
            return_exceptions=True,
        )
        if isinstance(quotes, BaseException):
            logger.warning("widget: quote fetch failed: %s: %s", type(quotes).__name__, quotes)
            quotes = {}
        if isinstance(sigmas, BaseException):
            logger.warning("widget: sigma read failed: %s: %s", type(sigmas).__name__, sigmas)
            sigmas = {}

        rows = []
        for sym in symbols:
            q = quotes.get(sym) or {}
            rows.append(
                {
                    "ticker": sym,
                    "change_percent": q.get("changePercentage"),
                    "price": q.get("price"),
                    "company_name": q.get("name"),
                    "market_cap": q.get("marketCap"),
                    "sigma_daily": sigmas.get(sym),
                    "open": q.get("open"),
                    "previous_close": q.get("previousClose"),
                }
            )

        ranked = rank_movers(rows)
        # Only the head needs a card today, but reading the top few keeps the door
        # open for a large-family widget listing runners-up without a second round
        # trip — and `get_cards` is one batched select regardless.
        cards = await get_news_insight_service().get_cards([m.ticker for m in ranked[:5]])
        return ranked, cards

    async def _quotes(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        rows = await get_fmp_client().get_batch_quotes_bulk(symbols)
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows or []:
            sym = str(r.get("symbol") or "").upper()
            if sym:
                out[sym] = r
        return out

    async def _sectors(
        self, user_id: str, tickers: Sequence[str]
    ) -> Dict[str, Optional[str]]:
        """Sector per ticker, from a SHARED source first.

        `watchlist_items.sector` is per-user and sparsely populated — measured
        2026-08-14, ACHR carries a sector for one user and NULL for another, and
        AAPL has none at all. Grouping off it alone would give two people holding
        the same stock different explanations for the same market day, which is the
        kind of inconsistency nobody can debug from a screenshot. So
        `company_profile_cache` (ticker-keyed, shared) wins, and the user's own row
        only fills gaps.
        """
        syms = [t.upper() for t in dict.fromkeys(tickers) if t]
        if not syms:
            return {}

        def _query() -> Dict[str, Optional[str]]:
            sb = get_supabase()
            out: Dict[str, Optional[str]] = {s: None for s in syms}
            try:
                res = (
                    sb.table("company_profile_cache")
                    .select("ticker, profile_json")
                    .in_("ticker", syms)
                    .execute()
                )
                for r in res.data or []:
                    prof = r.get("profile_json") or {}
                    sec = str(prof.get("sector") or "").strip()
                    if sec:
                        out[str(r["ticker"]).upper()] = sec
            except Exception as e:
                logger.warning(
                    "widget: shared sector read failed (%s: %s) — falling back to "
                    "the caller's own watchlist rows",
                    type(e).__name__, e,
                )
            try:
                res = (
                    sb.table("watchlist_items")
                    .select("ticker, sector")
                    .eq("user_id", user_id)
                    .in_("ticker", syms)
                    .execute()
                )
                for r in res.data or []:
                    sym = str(r.get("ticker") or "").upper()
                    sec = str(r.get("sector") or "").strip()
                    if sym in out and out[sym] is None and sec:
                        out[sym] = sec
            except Exception as e:
                logger.warning(
                    "widget: per-user sector read failed: %s: %s", type(e).__name__, e
                )
            return out

        return await asyncio.to_thread(_query)


_service: Optional[WidgetMoversService] = None


def get_widget_movers_service() -> WidgetMoversService:
    global _service
    if _service is None:
        _service = WidgetMoversService()
    return _service
