"""
Home-screen widget: the biggest mover and why, for Market or a user's portfolio.

READ PATH NEVER CALLS GEMINI. This is the same invariant `updates.py` and
`news_insight_service` state in their headers, and it matters more here, not less:
a widget refreshes on the OS's schedule, unattended, at a cadence the client cannot
be rate-limited into respecting. Everything below composes rows that the insight
sweeper already paid for. One widget refresh costs at most one FMP batch-quote and
a handful of batched Supabase selects.

WHAT THIS SERVICE IS ACTUALLY SOLVING
-------------------------------------
Not "fetch the reason" — the reason usually does not exist. Measured against the
live database on 2026-08-14: 12 insight cards, **zero** with a `price_move`. The
grounded catalyst only generates at z >= 2, a bar inherited from push
notifications, where the sweeper's own comment says only Unusual/Extreme "earns an
interruption". A widget is a *pull* surface, so it needs an answer every time it is
looked at, and most of those times no catalyst exists.

So the job is **degrading honestly**: pick the most unusual move, then say the
strongest true thing available about it, and label which of the three that is. See
`WidgetReasonKind` in `schemas/widget.py`.

RANKING
-------
By continuous z (`updates_materiality.move_z`), not raw percent and not
`move_score`. `move_score` is tier-bucket + raw magnitude, so a Notable +9%
(z≈1.1) outranks an Unusual +3% (z≈2.4) — raw-percent ranking wearing a z-score
hat. Rows with no σ cannot be compared on that axis and sort *after* every row that
has one, rather than being silently treated as z=0.

MARKET MODE RANKS INSIDE THE SWEPT UNIVERSE, ON PURPOSE
-------------------------------------------------------
The obvious source for "biggest mover in the market" is
`home_dashboard_service._movers_from_universe()` (FMP biggest-gainers/losers). It is
the wrong source here, because that population is **disjoint** from the one with
explanations: σ, insight cards and catalysts exist only for
`get_top_watchlist_tickers(...)`. An FMP mover would arrive with no σ (so it cannot
be z-ranked at all — the product decision was volatility-relative) and no reason,
every single day. So market mode ranks the tickers Caydex actually tracks and says
so via `universe_label`, rather than implying it scanned the whole tape.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.schemas.updates import SourceRefResponse
from app.schemas.widget import (
    WidgetBasketResponse,
    WidgetMoverPayload,
    WidgetMoverResponse,
    WidgetReasonKind,
    WidgetReasonResponse,
)
from app.services.news_insight_service import get_news_insight_service
from app.services.updates_materiality import classify_move, finite, move_z
from app.services.volatility_cache_service import get_volatility_cache_service
from app.utils.market_hours import ET, session_phase

logger = logging.getLogger(__name__)

MARKET_SCOPE = "__MARKET__"

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


# ── Pure helpers (no I/O — exhaustively testable) ─────────────────────


@dataclass(frozen=True)
class RankedMover:
    ticker: str
    change_percent: Optional[float]
    price: Optional[float]
    company_name: Optional[str]
    sigma_daily: Optional[float]
    z: Optional[float]
    tier: Optional[str]


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


def resolve_reason(
    card: Optional[Dict[str, Any]],
    live_change_percent: Optional[float],
    today_et: str,
) -> WidgetReasonResponse:
    """Pick the strongest TRUE statement available, and label its provenance.

    Order: grounded catalyst → news headline → arithmetic. The two guards on the
    catalyst are what stop the widget lying:

    * **Sign.** `price_move` is written when the card regenerates, and the card can
      outlive the move. A stored −3% reason under a live +5% quote is not stale, it
      is wrong — it explains a fall while the widget prints a rise.
    * **Session.** `price_catalyst_cache` keys on `(ticker, window_label, direction)`
      with a 24h TTL and the sweeper always passes ``window_label="today"``, which
      carries no date. So Thursday afternoon's reason is servable on Friday morning
      for a same-direction move. Migration 136 fixes the key; until then this
      read-side check is the only thing standing between the widget and a
      confidently wrong "why". Keep it even after 136 — defence in depth on a
      surface the user reads at a glance and cannot interrogate.
    """
    if isinstance(card, dict):
        pm = card.get("price_move")
        if isinstance(pm, dict):
            text = str(pm.get("reason") or "").strip()
            stored_change = finite(pm.get("change_percent"))
            fresh = _et_date(card.get("generated_at")) == today_et
            if text and fresh and _same_sign(stored_change, live_change_percent):
                tag = str(pm.get("catalyst_tag") or "").strip() or None
                return WidgetReasonResponse(
                    kind=WidgetReasonKind.CATALYST,
                    text=text,
                    catalyst_tag=tag,
                    sources=_sources_from_card(card),
                )

        headline = str(card.get("headline") or "").strip()
        if headline and card.get("ai_generated", True):
            # No sources: these belong to the news roll-up, and attaching them to a
            # line that makes no causal claim would make it look sourced-as-a-cause.
            return WidgetReasonResponse(kind=WidgetReasonKind.CONTEXT, text=headline)

    return WidgetReasonResponse(
        kind=WidgetReasonKind.NONE,
        text=deterministic_reason(live_change_percent, None),
    )


def _sources_from_card(card: Dict[str, Any]) -> List[SourceRefResponse]:
    out: List[SourceRefResponse] = []
    for s in (card.get("sources") or [])[:3]:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or "").strip()
        if not title:
            continue
        out.append(SourceRefResponse(title=title, url=str(s.get("url") or "")))
    return out


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
        market_card = (await get_news_insight_service().get_cards([MARKET_SCOPE])).get(
            MARKET_SCOPE
        )
        story = None
        if isinstance(market_card, dict):
            story = str(market_card.get("headline") or "").strip() or None

        return self._payload(
            mode="market",
            ranked=ranked,
            cards=cards,
            market_story=story,
            basket=None,
            universe_label="Tracked by Caydex",
        )

    async def _build_portfolio(
        self, user_id: str, tickers: Sequence[str]
    ) -> WidgetMoverPayload:
        ranked, cards = await self._rank_and_read(tickers)
        sectors = await self._sectors(user_id, [m.ticker for m in ranked])
        market_card = (await get_news_insight_service().get_cards([MARKET_SCOPE])).get(
            MARKET_SCOPE
        )
        story = None
        if isinstance(market_card, dict):
            story = str(market_card.get("headline") or "").strip() or None

        return self._payload(
            mode="portfolio",
            ranked=ranked,
            cards=cards,
            market_story=story,
            basket=detect_basket(ranked, sectors),
            universe_label=None,
        )

    def _payload(
        self,
        *,
        mode: str,
        ranked: Sequence[RankedMover],
        cards: Dict[str, Optional[Dict[str, Any]]],
        market_story: Optional[str],
        basket: Optional[WidgetBasketResponse],
        universe_label: Optional[str],
    ) -> WidgetMoverPayload:
        phase = session_phase()
        today = datetime.now(ET).date().isoformat()

        head: Optional[WidgetMoverResponse] = None
        if ranked:
            top = ranked[0]
            head = WidgetMoverResponse(
                ticker=top.ticker,
                company_name=top.company_name,
                change_percent=top.change_percent,
                price=top.price,
                tier=top.tier,
                z=round(top.z, 2) if top.z is not None else None,
                reason=resolve_reason(cards.get(top.ticker), top.change_percent, today),
            )

        return WidgetMoverPayload(
            mode=mode,
            as_of=_iso_now(),
            market_session=phase,
            is_stale=phase == "closed",
            headline_mover=head,
            basket=basket,
            market_story=market_story,
            universe_label=universe_label,
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
