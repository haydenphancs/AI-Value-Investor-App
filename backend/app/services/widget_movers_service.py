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

RANKING — ONE AXIS PER MODE
---------------------------
Market mode ranks by continuous z (`updates_materiality.move_z`), not raw percent and not
`move_score`. `move_score` is tier-bucket + raw magnitude, so a Notable +9% (z≈1.1)
outranks an Unusual +3% (z≈2.4) — raw-percent ranking wearing a z-score hat. Rows with no
σ cannot be placed on that axis and sort *after* every row that has one, rather than being
silently treated as z=0.

Portfolio mode ranks by ABSOLUTE % move (`basis="abs_change"`; z only breaks ties). The
Holdings tile is "my biggest movers", and z there reproduced the user-reported "random"
order exactly — AAPL −2.67% (z 1.78) above ORCL +4.01% (z 1.38) — while every holding
outside the σ-cached top-200 universe, and every crypto pair, always sorted last and was
cut by the payload cap however far it moved.

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
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.database import get_supabase
from app.utils.inflight import fail_shared_future
from app.services._analyst_common import analyst_section_available
from app.services.asset_class import uses_coingecko_price
from app.integrations.fmp import get_fmp_client
from app.schemas.widget import (
    WidgetBasketResponse,
    WidgetCauseResponse,
    WidgetIndexResponse,
    WidgetMarketBriefResponse,
    WidgetMarketContextResponse,
    WidgetMoveContextResponse,
    WidgetMoverPayload,
    WidgetMoverResponse,
)
from app.services.daily_move_attribution import (
    Attribution,
    attribute,
    # Shared so the market band and the cause sentence can never disagree about how a
    # percentage reads. Duplicating the formatting is how two surfaces on the same tile
    # end up rendering the same number differently.
    _dir_word,
    _pct,
)
from app.services.news_insight_service import get_news_insight_service
from app.services.updates_materiality import classify_move, finite, move_z
from app.services.volatility_cache_service import get_volatility_cache_service
from app.utils.market_hours import (
    ET,
    SESSION_REGULAR,
    previous_trading_day,
    session_label,
    session_phase,
    session_trading_date,
)
from app.services.price_service import price_source
from app.services.market_movers_service import get_market_movers_service

logger = logging.getLogger(__name__)

MARKET_SCOPE = "__MARKET__"
# The instrument whose daily move is the "whole market" leg of the attribution.
#
# ⚠️ A SECOND definition of this name — `updates_insight_sweeper` has its own, and they
# must agree. `^GSPC` is outside the FMP licence, so this leg was permanently absent:
# `_MarketContext.for_tickers` set `market_available=False` and every widget card lost
# its "moved with the market" attribution. Re-pointed at the same entitled proxy the
# sweeper uses, so both read one instrument.
MARKET_INDEX_SYMBOL = "SPY"

# The market band, in render order. Labels live HERE, not on the client: an already
# installed widget cannot learn a new index's display name without an app update.
#
# These ride the universe batch quote (`get_batch_quotes_bulk` chunks at 300, the
# universe is capped at 200), so the whole band costs ZERO additional FMP calls — it is
# in fact one call FEWER than before, because `^GSPC` used to be fetched separately.
# Labels stay SERVER-side (see above): an already-installed widget cannot learn a new
# display name, so re-pointing these ships without an app update — which is exactly what
# makes the honest relabel affordable here.
_INDEX_SYMBOLS: List[Tuple[str, str]] = [
    (MARKET_INDEX_SYMBOL, "S&P 500 ETF"),
    ("ONEQ", "Nasdaq ETF"),   # same label as the Home Market Pulse tile (2026-09-23)
    ("DIA", "Dow ETF"),
]

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

# Which universe the movers were drawn from. Sent so the tile can SAY it. It was added
# when an empty active group fell back to market data at the endpoint; that fallback is
# gone (an empty group is now an explicit `holdings_count=0` portfolio payload), but
# installed builds still caption a scope mismatch with it.
_SCOPE_MARKET = "The stocks Caydex tracks"
_SCOPE_PORTFOLIO = "Your holdings"

# `_cache` is keyed by user id + ticker set, so its key space is unbounded across users.
# Every sibling service in this repo bounds its cache; this one did not, and a widget is
# polled unattended by WidgetKit rather than driven by someone looking at a screen.
_CACHE_MAX_ENTRIES = 2000

# How many movers the payload carries: 1 headline + (_RUNNERS_UP - 1) runners-up.
#
# Six, not four. The medium family now renders a ranked column beside the headline and
# the large family lists five, and both were previously showing ONE name on a tile with
# room for several — `runners_up` was fetched on every payload and rendered only by
# Large. Widening this costs NOTHING upstream: the movers all come from the single batch
# quote already made, and `get_cards` is one batched Supabase select regardless of how
# many scopes it is handed.
_RUNNERS_UP = 6

# Portfolio mode's top gainers / top losers: at most this many per side, the headline
# excluded. The Large Holdings tile renders two columns of five; Medium shows one of each.
_TOP_MOVERS = 5

# A change below this DISPLAYS as 0.00% — the client's `isFlat`, which rounds to the two
# decimals it prints. It is the line between "up", "down" and "flat" in the portfolio
# counts, so the counts can never disagree with the badges drawn beside them.
_FLAT_PCT = 0.005

# The Market tile's short labels — Small/Medium draw only the %, in a 2×3 grid where
# "Russell 2000 ETF" does not fit. Kept beside, not inside, `_INDEX_SYMBOLS` (2-tuples a
# test pins) and the Home pulse list (whose `name` is the honest fund name, used next to
# a PRICE). A short label is never drawn beside a price: "S&P 500 · $651" reads as the
# index being off by 10x.
_MARKET_ASSET_SHORT_LABELS: Dict[str, str] = {
    "SPY": "S&P 500",
    "ONEQ": "Nasdaq",
    "DIA": "Dow",
    "IWM": "Russell 2000",
    "GLD": "Gold",
    "BTCUSD": "Bitcoin",
}

# Ceiling on reading the Bitcoin pulse tile. It is served from the Home strip's 600 s
# cache; on a miss it is a CoinGecko quote plus a sparkline, and a hung upstream must not
# hold the whole Market payload (and every caller deduped behind it) for the 30 s httpx
# timeout. The read is SHIELDED, so a slow fetch still fills the cache for the next build.
_CRYPTO_TILE_TIMEOUT_SECONDS = 5.0

# How many sessions' contexts `_ctx_cache` keeps. Two sessions coexist only around a
# rollover (a pre-market build still describing yesterday beside a fresh one), so a few
# slots stop the two builds evicting each other without growing without bound.
_CTX_CACHE_MAX_SESSIONS = 3

# The universe-wide snapshots change at most once a day, so an hour of reuse is generous
# and still costs at most 24 calls/day for the whole product.
#
# ⚠️ This must NOT cover anything with an intraday value. The index quote used to sit
# here and was therefore frozen for an hour while every stock's own change was refetched
# each minute — see `_fetch_market_context`, where the indices now ride the universe
# batch quote instead.
_CONTEXT_TTL_SECONDS = 3600

# FMP's SILENT row cap on `earnings-calendar`: a larger answer is cut, NEWEST dates kept.
# The same number as `earnings_window_service._TRUNCATION_ROWS` (a test pins them equal).
# A single-day response this large is probably truncated too, so it is logged as an ERROR.
_EARNINGS_TRUNCATION_ROWS = 4000


# ── Pure helpers (no I/O — exhaustively testable) ─────────────────────



@dataclass
class _MarketContext:
    """Universe-wide facts for one sweep. Two FMP calls, shared by every ticker.

    The whole cost argument for this rebuild lives here: `industry-performance-snapshot`
    and `earnings-calendar` each cover EVERY ticker in one request, and the market's own
    move rides on the batch quote already fetched. So attributing 200 movers costs the
    same two calls as attributing one — versus a per-ticker paid web search, which is
    what the old design did.

    ⚠️ TREAT INSTANCES HELD IN `_ctx_cache` AS IMMUTABLE. This object is shared by every
    concurrent caller in the cache window, and the iOS app fires BOTH widget routes at
    once on every launch and foreground. An earlier version assigned `ctx.ticker_industry`
    per request directly onto the cached instance, with further `await`s afterwards — so
    the market build and the portfolio build erased each other's industry map and the
    market tile reported "no catalyst" for a plainly sector-driven move. Use
    `for_tickers()` to derive a per-request view instead.

    THE `*_available` FLAGS EXIST BECAUSE SILENCE IS NOT A FINDING. Each leg degrades
    independently, which is right, but an empty `industry_changes` is ambiguous: it means
    either "checked, nothing to report" or "the call failed". `describe_no_cause` asserts
    a NEGATIVE ("No clear catalyst in today's news"), and asserting a negative you never
    actually checked is a lie on someone's Home Screen. So availability travels with the
    data.
    """

    market_change: Optional[float] = None
    # industry name (lowercased) -> today's % change
    industry_changes: Dict[str, float] = field(default_factory=dict)
    # ticker -> industry name, from the SHARED company_profile_cache
    ticker_industry: Dict[str, str] = field(default_factory=dict)
    # ticker -> today's/yesterday's earnings-calendar row
    earnings: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # (sector name, today's % change), in the snapshot's own order.
    sector_changes: List[Tuple[str, float]] = field(default_factory=list)
    # lowercase sector name -> ISO date of the session its change describes. The
    # context is cached for an hour, so a band filled in the last minutes before the
    # open carries FRIDAY's sector averages until ~10:30; `for_tickers` drops rows whose
    # stamp is not the request's session, mirroring `industry_for`'s fail-closed gate.
    sector_dates: Dict[str, str] = field(default_factory=dict)

    # Which legs actually answered. False ⇒ we know nothing, NOT "nothing happened".
    industry_available: bool = False
    earnings_available: bool = False
    sector_available: bool = False
    # Set when the index quote was readable. Unlike the three above this is filled in
    # per-request from the universe batch quote rather than from the hourly cache — the
    # market's own intraday percentage is the one thing here that must never be an hour
    # old, since it is the denominator of the "moved with the market" test.
    market_available: bool = False
    # ET date (YYYY-MM-DD) the industry snapshot itself describes. FMP's
    # `industry-performance-snapshot` walks back to the last trading day, so on a Monday
    # morning it legitimately returns FRIDAY's numbers — which must not be compared
    # against a live Monday quote. None ⇒ unknown, and the comparison is refused.
    # Per-industry session stamps, keyed by lowercased industry name. A scalar here made
    # one thinly-traded industry disarm attribution for every card — see `_industries()`.
    industry_dates: Dict[str, str] = field(default_factory=dict)
    # True when the news-card read succeeded. Same reasoning as the flags above: a failed
    # Supabase read must not become "no company news today".
    news_available: bool = True
    # The ET session being attributed (YYYY-MM-DD). Compared against
    # `industry_snapshot_date` to refuse a cross-session comparison.
    session_date: Optional[str] = None
    # symbol -> batch-quote row for each index, filled per request (never cached).
    index_rows: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def for_tickers(
        self,
        ticker_industry: Dict[str, str],
        session_date: str,
        index_rows: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> "_MarketContext":
        """A per-request view over the shared universe-wide facts.

        Shallow by design: the universe-wide maps are read-only after the fetch, so
        sharing them is safe and copying them per request would be pure waste. Only the
        per-request fields are replaced.

        `session_date` is set HERE rather than at fetch time because the context is cached
        for up to an hour and the session can roll over inside that window — a context
        filled at 19:59 ET and read at 20:01 belongs to a different session date.

        `index_rows` likewise: they come from the CALLER'S batch quote, so they are as
        fresh as the payload rather than as old as the hourly cache.

        ⚠️ AND THEY ARE SESSION-GATED HERE, like the sectors below. One batch can carry two
        sessions (`drop_prior_session_movers`), and the index rows were the only rows that
        escaped every gate: pre-market, SPY could print Tuesday's −0.18% beside ONEQ's
        sub-cent drift off Tuesday's stored close, stamped WEDNESDAY, under one "Tue close"
        label — the screenshot's "Nasdaq 0.00%" — and the same SPY row is the "moved with
        the market" denominator. A row stamped with another session keeps its price and
        loses its change (so `market_available` goes False for a mismatched SPY). An
        UNSTAMPED row (the profile fallback) fails open, the same rule the movers follow.
        """
        rows = _session_gated_index_rows(index_rows or {}, session_date)
        head = rows.get(MARKET_INDEX_SYMBOL) or {}
        market_change = finite(head.get("changePercentage"))
        # Sector breadth is served from the hourly context cache with no date on the
        # payload; only rows stamped with THIS session may feed "3 of 11 sectors up".
        # A row with no stamp fails closed, exactly like `industry_for`.
        sectors = [
            (name, chg) for name, chg in self.sector_changes
            if self.sector_dates.get(name.strip().lower()) == session_date
        ]
        return replace(
            self,
            ticker_industry=ticker_industry,
            session_date=session_date,
            index_rows=rows,
            market_change=market_change,
            market_available=market_change is not None,
            sector_changes=sectors,
            sector_available=self.sector_available and bool(sectors),
        )

    def industry_for(self, ticker: str) -> tuple[Optional[str], Optional[float]]:
        """The ticker's industry and TODAY's move for it — or the name alone.

        Returning `(name, None)` rather than `(name, stale_number)` is the point. Every
        other dated input in the attribution chain is age-gated (earnings by
        `_EARNINGS_MAX_AGE_DAYS`, grades by `_GRADE_MAX_AGE_DAYS`, news by the card's ET
        date); the industry figure was the only one that was not, so a Friday snapshot
        served on Monday morning printed "Aerospace & Defense fell 1.2% TODAY" and could
        manufacture a whole SECTOR cause out of the previous session.

        `FMPClient._latest_perf_snapshot` walks back to the last trading day by design, so
        receiving an older date is normal operation, not an error — which is exactly why
        it has to be checked rather than assumed.
        """
        name = self.ticker_industry.get(ticker.upper())
        if not name:
            return None, None
        if not self.industry_available:
            return name, None
        # FAIL CLOSED on an unknown date. This used to short-circuit on the first clause,
        # so a source that stamped no date disarmed the whole gate silently — which is
        # exactly what happened when FMP's dated snapshot (402, outside the licence) was
        # replaced by screener-derived rows carrying no `date` at all. The gate then read
        # green while printing a previous session's move as "today", the one sentence its
        # docstring says it exists to prevent. Same posture as `industry_available`: a
        # thing we cannot verify is not a thing we assert.
        stamp = self.industry_dates.get(name.strip().lower())
        if (
            not stamp
            or not self.session_date
            or stamp != self.session_date
        ):
            return name, None
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
    # `open` is NOT on the licensed batch quote (`price_service._shape` emits no `open`
    # since the `quote` family went 402), so `open_price` is None and the gap split is
    # dormant; `previous_close` is real. Kept so a licensed `open` re-enables it.
    open_price: Optional[float] = None
    previous_close: Optional[float] = None
    # ISO date of the session `change_percent` describes (`price_service` stamps it as
    # `changeSession`). None on a row without a change or from an older shape.
    change_session: Optional[str] = None
    # "etf" | "crypto" | "stock" from the quote's own flags; None when the quote had none.
    asset_type: Optional[str] = None


@dataclass
class MoveExplanation:
    """One ticker's same-day story: the deterministic cause plus the facts that gate it.

    `tier` carries `classify_move`'s vocabulary (Typical/Notable/Unusual/Extreme on the
    σ path, flat/notable/extreme on the fixed-band fallback), which is the SAME set the
    Updates sweeper gates its paid catalyst on. Callers deciding whether a move earns a
    web search must read this rather than re-deriving a threshold — two surfaces with
    two thresholds is how one of them starts explaining moves the other calls ordinary.
    """

    ticker: str
    company_name: Optional[str]
    change_percent: Optional[float]
    price: Optional[float]
    tier: Optional[str]
    z: Optional[float]
    industry_name: Optional[str]
    industry_change_percent: Optional[float]
    market_change_percent: Optional[float]
    attribution: Attribution
    # The session the change belongs to — "today", or "on Fri" pre-market Monday — and its
    # ISO date. `attribute()` already received the word; consumers (Ask Cay AI's
    # `explain_price_move`) did not, so their own lines said "today" beside an explanation
    # that said "on Fri", and a paid "today" search was bought for a prior session's move.
    session_word: str = "today"
    session_date: Optional[str] = None


_RANK_BASES = ("z", "abs_change")


def rank_movers(rows: Sequence[Dict[str, Any]], *, basis: str = "z") -> List[RankedMover]:
    """Order candidates — by how unusual the move is (`basis="z"`, the default), or by
    how big it is (`basis="abs_change"`, portfolio mode).

    A row whose change is missing or non-finite is DROPPED, not ranked as 0.0 —
    an unreadable quote is not a flat day, and this repo has shipped that exact
    confusion more than once (NaN reaching `max()` and winning).

    `"z"`: rows with a usable σ sort first, by z descending. Rows without σ follow, by
    absolute change descending, because they cannot be placed on the z axis at all
    and pretending otherwise would let an unjudgeable ticker outrank a measured
    one.

    `"abs_change"`: |change| descending, then z descending (σ-less rows last among
    equal moves), so a σ-less −15% holding leads a σ-judged −0.3% one. This is what
    the Holdings tile promises — "my biggest movers" — and the z order there read as
    random (module header, RANKING).

    Either way, ties break on ticker ascending so the widget does not flip between two
    equal movers on consecutive refreshes.
    """
    if basis not in _RANK_BASES:
        raise ValueError(f"rank_movers: unknown basis {basis!r} (expected one of {_RANK_BASES})")
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
                change_session=(str(row.get("change_session"))[:10] or None)
                if row.get("change_session") else None,
                asset_type=(str(row.get("asset_type")) or None)
                if row.get("asset_type") else None,
            )
        )

    if basis == "abs_change":
        ranked.sort(
            key=lambda m: (
                -abs(m.change_percent or 0.0),         # biggest move first
                0 if m.z is not None else 1,           # then σ-judged rows
                -(m.z if m.z is not None else 0.0),    # then most unusual
                m.ticker,                              # then stable
            )
        )
        return ranked

    ranked.sort(
        key=lambda m: (
            0 if m.z is not None else 1,           # σ-judged rows first
            -(m.z if m.z is not None else 0.0),    # then most unusual
            -abs(m.change_percent or 0.0),         # then biggest raw move
            m.ticker,                              # then stable
        )
    )
    return ranked


def _parsed_session(stamp: Optional[str]) -> Optional[date]:
    if not stamp:
        return None
    try:
        return date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return None


def _off_session(row: Dict[str, Any], session_date: Optional[str]) -> bool:
    """True when `row` carries a readable `changeSession` that is NOT `session_date`.

    Fails OPEN on an unstamped (or unreadable) row — the profile fallback carries no stamp,
    and the movers keep such rows too — and on an unknown payload session.
    """
    stamp = _parsed_session(row.get("changeSession"))
    want = _parsed_session(session_date)
    return stamp is not None and want is not None and stamp != want


def _session_gated_index_rows(
    rows: Dict[str, Dict[str, Any]], session_date: Optional[str],
) -> Dict[str, Dict[str, Any]]:
    """`rows` with the change blanked on every row stamped with another session.

    Pure, and never mutates its input: the rows are the caller's batch-quote dicts. The
    price is kept — it is a real, current price — and only the session-relative number is
    withheld, which iOS already renders as the label alone (`change_percent` None).
    """
    out: Dict[str, Dict[str, Any]] = {}
    for sym, row in rows.items():
        if isinstance(row, dict) and _off_session(row, session_date):
            logger.info(
                "widget: index row %s is stamped %s but the payload describes %s — its "
                "change is withheld (price kept)",
                sym, row.get("changeSession"), session_date,
            )
            gated = dict(row)
            gated["changePercentage"] = None
            gated["change"] = None
            out[sym] = gated
        else:
            out[sym] = row
    return out


def _is_round_the_clock(ticker: str) -> bool:
    """True for an asset with no session at all — a CoinGecko-priced crypto pair.

    Its change is a ROLLING 24-hour move, so it neither belongs to an equity session nor
    can be stale relative to one. It must therefore be exempt from BOTH halves of the
    session machinery below: it cannot set `newest_session` (stamping it with today would
    age out every legitimately Friday-stamped equity at Monday pre-market — the very
    mis-drop `drop_prior_session_movers` exists to prevent), and it can never be dropped
    as a prior-session row.
    """
    return uses_coingecko_price(ticker or "")


def newest_session(ranked: Sequence[RankedMover]) -> Optional[date]:
    """The most recent `changeSession` stamp in the batch, or None if none is stamped."""
    stamps = [
        d for d in (
            _parsed_session(m.change_session)
            for m in ranked if not _is_round_the_clock(m.ticker)
        ) if d
    ]
    return max(stamps) if stamps else None


def drop_prior_session_movers(
    ranked: Sequence[RankedMover],
    *,
    phase: Optional[str] = None,
) -> Tuple[List[RankedMover], List[RankedMover]]:
    """Split `ranked` into (rows of the tile's session, rows stamped with ANOTHER session).

    One batch of quotes can legitimately carry two sessions: `price_service` stamps a
    row with the stored close's date whenever the quote still equals that close — a
    halted ticker, or one that has not printed yet — and with the live session
    otherwise. Ranking ignores the stamp, so a Friday −10% halted name could head Monday's
    tile above Monday's real movers, and the ONE `session_label` / `session_word` the
    payload carries would then mislabel every runner. A row stamped with another session
    is not this session's mover; it is dropped here and named in the log. Rows without a
    stamp (older wire shape) are kept.

    WHICH session is the tile's depends on `phase` (`market_hours.session_phase()`, passed
    in by the builders — this function never reads the clock):

    * `None` or `"regular"` — the NEWEST stamp wins, as it always has. With the tape open,
      one live print is real news and every older stamp is a halted / unprinted name.
    * any other phase (pre-market, after hours, closed) — the newest stamp wins only when
      it covers at least half of the stamped rows; otherwise the PLURALITY session does
      (ties to the newer). Pre-market the screener still reports yesterday's close, and a
      single row whose price drifted a sub-cent off its stored close is stamped TODAY —
      under newest-wins that one ~0% row evicted every real prior-session mover and the
      tile headlined it as "Pre-market". Rows NEWER than the chosen session are dropped
      too: `_session_of` re-derives the session from the rows that survive, so one
      surviving Wednesday row would relabel all of Tuesday's movers as the live session.
    """
    sessions = [
        _parsed_session(m.change_session)
        for m in ranked if not _is_round_the_clock(m.ticker)
    ]
    counts = Counter(d for d in sessions if d is not None)
    if not counts:
        return list(ranked), []
    chosen = max(counts)
    if phase is not None and phase != SESSION_REGULAR:
        if counts[chosen] * 2 < sum(counts.values()):
            chosen = max(counts, key=lambda d: (counts[d], d))
    current: List[RankedMover] = []
    stale: List[RankedMover] = []
    for m in ranked:
        if _is_round_the_clock(m.ticker):
            # Never stale: an asset that never closes has no prior session to lag.
            current.append(m)
            continue
        stamped = _parsed_session(m.change_session)
        (stale if stamped is not None and stamped != chosen else current).append(m)
    return current, stale


def select_payload_movers(
    ranked: Sequence[RankedMover],
) -> Tuple[Optional[RankedMover], List[RankedMover], List[RankedMover], List[RankedMover]]:
    """(headline, runners-up, top gainers, top losers) — the ONE selection rule.

    Shared by `_payload`, which renders these, and `_rank_and_read`, which reads their
    news cards, so a mover can never be rendered without the card read that backs its
    "no company news" claim (an unread card is UNCHECKED, and says so).

    * ONE ROW PER TICKER, and the headline never repeats below itself. `rank_movers`
      keeps duplicates ("dedup is the caller's job"), and iOS renders these with
      `ForEach(id: \\.ticker)` — duplicate ids are undefined behaviour in SwiftUI, on a
      Home Screen, with no way for the user to recover.
    * Runners-up keep the ranking order, `_RUNNERS_UP - 1` of them.
    * Gainers: change > 0 and not flat, largest first; losers: change < 0 and not flat,
      most negative first; ≤ `_TOP_MOVERS` each, headline excluded, ties by ticker.
    """
    unique: List[RankedMover] = []
    seen: set = set()
    for m in ranked:
        if m.ticker in seen:
            continue
        seen.add(m.ticker)
        unique.append(m)
    if not unique:
        return None, [], [], []
    head, rest = unique[0], unique[1:]
    priced = [m for m in rest if m.change_percent is not None]
    gainers = sorted(
        (m for m in priced if m.change_percent > 0 and not _is_flat(m.change_percent)),
        key=lambda m: (-m.change_percent, m.ticker),
    )[:_TOP_MOVERS]
    losers = sorted(
        (m for m in priced if m.change_percent < 0 and not _is_flat(m.change_percent)),
        key=lambda m: (m.change_percent, m.ticker),
    )[:_TOP_MOVERS]
    return head, rest[: _RUNNERS_UP - 1], gainers, losers


def _is_flat(change: float) -> bool:
    return abs(change) < _FLAT_PCT


def direction_counts(ranked: Sequence[RankedMover]) -> Tuple[int, int, int]:
    """(up, down, flat) over the ranked rows, one per ticker. Flat is `_FLAT_PCT`."""
    up = down = flat = 0
    seen: set = set()
    for m in ranked:
        if m.ticker in seen or m.change_percent is None:
            continue
        seen.add(m.ticker)
        if _is_flat(m.change_percent):
            flat += 1
        elif m.change_percent > 0:
            up += 1
        else:
            down += 1
    return up, down, flat


def deterministic_reason(
    change_percent: Optional[float], z: Optional[float], session_word: str = "today",
) -> str:
    """The always-available line. Never wrong, because it only restates arithmetic.

    This is what a mover with no catalyst and no news gets, and it is the reason the
    widget can never be blank. Phrased as a comparison rather than a bare number
    because "−4.8%" alone tells a reader nothing about whether that is remarkable
    for this particular stock.

    `session_word` names the session the change belongs to — "today", or "on Fri" when
    the payload is describing a prior session's move (pre-market, weekend). Saying
    "today" for Friday's move was the cross-session claim the industry gate exists to
    suppress, printed by the headline itself.
    """
    pct = finite(change_percent)
    if pct is None:
        return "Price change unavailable right now."
    if pct == 0:
        return "Flat on the day." if session_word == "today" else f"Flat {session_word}."
    if z is None:
        return f"{'Up' if pct > 0 else 'Down'} {abs(pct):.1f}% {session_word}."
    return (
        f"{'Up' if pct > 0 else 'Down'} {abs(pct):.1f}% {session_word} — "
        f"about {z:.1f}× its normal daily range."
    )


def _group_change(row: Dict[str, Any]) -> Optional[float]:
    """The sector/industry move on a performance row, or None when there isn't one.

    Written out rather than `finite(r.get("changesPercentage") or r.get("averageChange"))`
    because `0.0` is FALSY: a group that closed exactly flat fell through to the
    second key, which the entitled substitute
    (`market_movers_service._group_performance`) does not emit at all, so `finite(None)`
    returned None and the group was DROPPED from the context instead of reported flat.
    Reachable: the substitute publishes `round(mean, 4)`, and a small industry can land
    on 0.0000 exactly. A missing group silently removes it from attribution — the same
    class of "absent reads as no-signal" bug the `*_available` flags below exist for.
    """
    for key in ("changesPercentage", "averageChange"):
        if key in row:
            value = finite(row.get(key))
            if value is not None:
                return value
    return None


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


def _market_brief(
    card: Optional[Dict[str, Any]], today_et: str
) -> Optional["WidgetMarketBriefResponse"]:
    """The one-sentence read on the whole market, or None.

    Market mode answers "what is the market doing"; Holdings mode answers "what moved
    most of mine". Two different questions, and the Market tile used to answer the wrong
    one — a biggest-mover list where the reader wanted the state of the tape.

    ⚠️ SESSION-GATED, and that is the entire reason this is a function rather than a
    field copy. `WidgetMoverPayload` used to refuse a market headline outright because
    the `__MARKET__` roll-up "is not today-scoped": its corpus window is 24-96h and its
    hard TTL is 96h, so on a Monday morning the freshest stored card can still be
    Friday's. Rendering that as the market right now is the same class of error as the
    ACHR "+42.7% fifteen-day rally" the daily-scope rebuild removed — confident, wrong,
    and on a surface the reader cannot interrogate.

    So the gate is identical to `_classified_today_news`': the card must have been
    generated in the session the rest of the payload describes. Off-session ⇒ None ⇒ the
    tile shows the index numbers alone, which are always current.
    """
    if not isinstance(card, dict):
        return None
    if _et_date(card.get("generated_at")) != today_et:
        return None
    headline = str(card.get("headline") or "").strip()
    if not headline:
        return None
    sentiment = card.get("sentiment")
    return WidgetMarketBriefResponse(
        headline=headline,
        sentiment=str(sentiment).strip() or None if sentiment else None,
        generated_at=card.get("generated_at") or None,
    )


def _classified_today_news(
    card: Optional[Dict[str, Any]], today_et: str
) -> tuple[list, bool, bool]:
    """Today's headlines, run through the EXISTING catalyst classifier.

    Reuses `_classify_news_catalyst` / `NEWS_CATALYST_KEYWORDS` from the report
    collector so the eleven-tag vocabulary (FDA Approval, M&A, Guidance Cut, …) lives
    in exactly one place. Only the card's own headline is available here, and only when
    the card was generated TODAY — a 48h roll-up is not a same-day catalyst.

    Returns `(classified, had_news, checked)` — THREE states, not two:

    * `checked=False` — there is no insight row for this ticker at all. `get_cards`
      returns None for a scope it has never stored or whose card expired, and that is
      NOT the same as "the sweeper looked and found nothing". It is most reachable in
      portfolio mode, where holdings are the caller's own and need not be inside the
      swept top-200 universe at all — so the tile would confidently announce "No company
      news today" about a ticker nothing has ever examined.
    * `checked=True, had_news=False` — a card exists but is not from today's session.
      The sweeper HAS covered this ticker and has nothing current, which is a real
      finding and may be stated.
    * `checked=True, had_news=True` — news exists; whether it explains the move is the
      classifier's business.

    A card generated AFTER the session is the fourth case, and it is `checked=False`.
    Pre-market the payload describes yesterday's session, while the sweeper's news pass
    (04:00 onwards) has already REPLACED yesterday's card with this morning's (one row per
    scope). That newer card says nothing about yesterday, and yesterday's is gone — so
    "No company news on Tue." would be a confident negative about a day we can no longer
    see. ISO dates compare correctly as strings.
    """
    if not isinstance(card, dict):
        return [], False, False
    card_day = _et_date(card.get("generated_at"))
    if card_day is not None and card_day > today_et:
        # Newer than the session described — unchecked, not a negative (docstring).
        return [], False, False
    if _et_date(card.get("generated_at")) != today_et:
        # Covered, nothing from today's session — a genuine negative.
        return [], False, True
    headline = str(card.get("headline") or "").strip()
    if not headline:
        return [], False, True

    try:
        from app.services.agents.ticker_report_data_collector import (
            _classify_news_catalyst,
        )

        tag = _classify_news_catalyst(headline, "")
    except Exception as e:
        logger.warning(
            "widget: news classifier unavailable (%s: %s)", type(e).__name__, e
        )
        return [], True, True
    return ([(tag, headline)] if tag else []), True, True


def _classified_rolling_news(
    card: Optional[Dict[str, Any]], calendar_iso: str
) -> tuple[list, bool, bool]:
    """`_classified_today_news` for a ROUND-THE-CLOCK row — gated on `calendar_iso`, the ET
    calendar day its rolling 24 h window ends, never on the EQUITY session.

    An asset that never closes has no equity session to be checked against. On a Saturday
    the payload's session is Friday while the crypto off-hours news pass has already
    written Saturday's BTCUSD card: the equity gate saw a card NEWER than the session,
    called it unchecked, and the catalyst in it was never classified — "Today's news could
    not be checked." beside a live move all weekend, and every weekday pre-market hour.

    Same three states, one stricter rule: a card from any OTHER day — older (at 00:30 ET
    last evening's card still sits inside the rolling 24 h), newer (clock skew) or
    undated — is UNCHECKED, never the confident negative "No company news today". Only a
    card from that very day is classified, by the one shared classifier.
    """
    if not isinstance(card, dict):
        return [], False, False
    if _et_date(card.get("generated_at")) != calendar_iso:
        return [], False, False
    return _classified_today_news(card, calendar_iso)


def build_market_context(
    index_rows: Dict[str, Dict[str, Any]],
    sectors: Sequence[Tuple[str, float]],
    *,
    sector_available: bool,
) -> Optional[WidgetMarketContextResponse]:
    """The tape, as the tile leads with it. Pure — no I/O, exhaustively testable.

    Returns None when NOTHING was readable, so the widget simply omits the band and
    leads with the mover exactly as it did before this existed. It never emits a band
    of zeroes: "the market was flat and no sector moved" is a claim, and making it out
    of two failed upstream calls is the same class of lie as `describe_no_cause`
    announcing "no company news" after a Supabase error.

    Breadth is counted over SECTORS because they are a real population — all 11, always
    present — so "8 of 11 sectors up" is a defined statistic. Counting greens in FMP's
    biggest-gainers list would not be: that is a top-50 cut, and its denominator means
    nothing.
    """
    indices: List[WidgetIndexResponse] = []
    for symbol, label in _INDEX_SYMBOLS:
        row = index_rows.get(symbol) or {}
        chg = finite(row.get("changePercentage"))
        price = finite(row.get("price"))
        if chg is None and price is None:
            continue
        indices.append(
            WidgetIndexResponse(
                symbol=symbol,
                label=label,
                change_percent=round(chg, 2) if chg is not None else None,
                price=round(price, 2) if price is not None else None,
                # Every band symbol is an entitled ETF proxy (see `_INDEX_SYMBOLS`).
                asset_type="etf",
            )
        )

    usable = [(n, c) for n, c in (sectors or []) if n and c is not None] if sector_available else []

    breadth_up: Optional[int] = None
    breadth_total: Optional[int] = None
    lead_name = lead_chg = lag_name = lag_chg = None
    if usable:
        breadth_total = len(usable)
        breadth_up = sum(1 for _, c in usable if c > 0)
        ordered = sorted(usable, key=lambda kv: kv[1], reverse=True)
        lead_name, lead_chg = ordered[0]
        lag_name, lag_chg = ordered[-1]
        # One sector cannot be both the best and the worst. With a single readable
        # sector, naming a leader AND a laggard would print the same row twice.
        if len(ordered) < 2:
            lag_name = lag_chg = None

    if not indices and breadth_total is None:
        return None

    # The sentence, built once, server-side — same posture as `basket.text`, so the
    # wording can never contradict the numbers rendered beside it.
    parts: List[str] = []
    head = next((i for i in indices if i.change_percent is not None), None)
    if head is not None:
        c = head.change_percent
        # `_dir_word` answers "rose" or "fell"; neither is true of a flat tape, and
        # "fell 0.0%" is the kind of self-contradicting phrase this file exists to avoid.
        parts.append(
            f"{head.label} flat" if abs(c) < 0.05
            else f"{head.label} {_dir_word(c)} {_pct(c)}"
        )
    if breadth_up is not None and breadth_total:
        parts.append(f"{breadth_up} of {breadth_total} sectors up")
    if lead_name and lead_chg is not None and lead_chg > 0:
        parts.append(f"{lead_name} leads {_pct(lead_chg)}")

    return WidgetMarketContextResponse(
        indices=indices,
        breadth_up=breadth_up,
        breadth_total=breadth_total,
        leading_sector=lead_name,
        leading_sector_change_percent=round(lead_chg, 2) if lead_chg is not None else None,
        lagging_sector=lag_name,
        lagging_sector_change_percent=round(lag_chg, 2) if lag_chg is not None else None,
        text=" · ".join(parts) if parts else None,
    )


def _stamp_only_movers(index_rows: Dict[str, Dict[str, Any]]) -> List[RankedMover]:
    """Session-stamp carriers for `_session_of` when no mover was ranked. Pure.

    Never ranked, never rendered — only `change_session` and `ticker` are read, by
    `newest_session`. Rows without a stamp contribute nothing.
    """
    out: List[RankedMover] = []
    for sym, row in (index_rows or {}).items():
        stamp = row.get("changeSession") if isinstance(row, dict) else None
        if not stamp:
            continue
        out.append(RankedMover(
            ticker=str(sym).upper(), change_percent=None, price=None, company_name=None,
            sigma_daily=None, z=None, tier=None, change_session=str(stamp)[:10],
        ))
    return out


def _quote_asset_type(symbol: str, quote: Dict[str, Any]) -> Optional[str]:
    """The holding's class from the batch quote's OWN flags — no extra call, no name guess.

    `price_service` shapes every row with an `isEtf` boolean, so its absence means the row
    came from somewhere that does not classify; that answers None rather than "stock".
    """
    if _is_round_the_clock(symbol):
        return "crypto"
    if not isinstance(quote, dict) or "isEtf" not in quote:
        return None
    return "etf" if quote.get("isEtf") else "stock"


def build_market_assets(
    index_rows: Dict[str, Dict[str, Any]],
    session_date: Optional[str],
    crypto_tile: Optional[Any] = None,
) -> List[WidgetIndexResponse]:
    """The Market tile's grid: the Home Market Pulse, in the Home strip's order. Pure.

    The equities are `home_dashboard_service._PULSE_SYMBOLS`, imported rather than copied
    so the widget and Home can never list different assets or call them different names;
    their quotes ride the batch `_rank_and_read` already makes (free). Bitcoin comes last,
    from the pulse's own crypto tile (`HomeDashboardService.get_crypto_pulse_tile`).

    The same honesty rules as the band: a change stamped with a session other than
    `session_date` is withheld (the price is kept), a non-finite one is withheld, and an
    unmeasured crypto change (`change_known=False`) is None — never the 0.0 the pulse
    keeps on the wire for its shipped builds. An asset with neither a price nor a change
    is omitted rather than drawn as a blank.
    """
    # Function-local: the pulse list lives in a heavyweight module, and the widget only
    # needs it here.
    from app.services.home_dashboard_service import _PULSE_SYMBOLS

    out: List[WidgetIndexResponse] = []
    for cfg in _PULSE_SYMBOLS:
        sym = str(cfg.get("symbol") or "").upper()
        row = index_rows.get(sym) if sym else None
        if not isinstance(row, dict):
            continue
        price = finite(row.get("price"))
        if price is not None and price <= 0:
            price = None
        chg = finite(row.get("changePercentage"))
        if chg is not None and _off_session(row, session_date):
            chg = None
        if price is None and chg is None:
            continue
        out.append(
            WidgetIndexResponse(
                symbol=sym,
                label=str(cfg.get("name") or sym),
                # `+ 0.0` collapses −0.0, as the pulse does: "-0.00%" in green otherwise.
                change_percent=(round(chg, 2) + 0.0) if chg is not None else None,
                price=round(price, 2) if price is not None else None,
                short_label=_MARKET_ASSET_SHORT_LABELS.get(sym),
                asset_type=(str(cfg.get("type")) or None) if cfg.get("type") else None,
            )
        )

    if crypto_tile is not None:
        sym = str(getattr(crypto_tile, "symbol", "") or "").upper()
        price = finite(getattr(crypto_tile, "price", None))
        if price is not None and price <= 0:
            price = None
        chg = (
            finite(getattr(crypto_tile, "change_percent", None))
            if getattr(crypto_tile, "change_known", True) else None
        )
        if sym and (price is not None or chg is not None):
            out.append(
                WidgetIndexResponse(
                    symbol=sym,
                    label=str(getattr(crypto_tile, "name", "") or sym),
                    change_percent=(round(chg, 2) + 0.0) if chg is not None else None,
                    price=round(price, 2) if price is not None else None,
                    short_label=_MARKET_ASSET_SHORT_LABELS.get(sym),
                    # Its change is a rolling 24 h — no session to gate on, none to label.
                    rolling_24h=True,
                    asset_type=str(getattr(crypto_tile, "type", "") or "crypto"),
                )
            )
    return out


def _market_payload_is_cacheable(payload: WidgetMoverPayload) -> bool:
    """A Market payload worth reusing for the 60 s TTL: it has assets or a brief."""
    return bool(payload.market_assets) or payload.market_brief is not None


def _better_earnings_row(candidate: Dict[str, Any], incumbent: Dict[str, Any]) -> bool:
    """Prefer the row that actually REPORTED, then the more recent one.

    The window is two days wide, so a symbol can appear twice — and `setdefault` kept
    whichever one FMP happened to serialise first. That is arbitrary, and it is
    arbitrary in a way that matters: a row carrying `epsActual` is an event that
    happened, while a row without one is a schedule. Picking the schedule over the
    result loses the beat/miss and can suppress the attribution entirely.
    """
    cand_reported = finite(candidate.get("epsActual")) is not None
    inc_reported = finite(incumbent.get("epsActual")) is not None
    if cand_reported != inc_reported:
        return cand_reported
    return str(candidate.get("date") or "") > str(incumbent.get("date") or "")


def _moved(m: RankedMover) -> bool:
    """Did this holding actually do something, judged against its own volatility."""
    if m.z is not None:
        return m.z >= _BASKET_MIN_Z
    return abs(m.change_percent or 0.0) >= _BASKET_FALLBACK_PCT


def detect_basket(
    holdings: Sequence[RankedMover],
    sectors: Dict[str, Optional[str]],
    *,
    holdings_count: Optional[int] = None,
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

    `holdings_count` is the size of the portfolio the user OWNS — the requested tickers,
    when the caller knows it. "4 of your 8 holdings" was printed for a 12-holding group
    because funds, unpriced and prior-session rows never reach `holdings`; the sentence
    states a count the user can see is wrong. The thresholds above still judge the
    readable rows only. Without it the denominator is the readable rows, as before.
    """
    usable = [m for m in holdings if m.change_percent is not None]
    if len(usable) < _BASKET_MIN_HOLDINGS:
        return None
    # Never below the readable rows: a caller's stale or partial count cannot make the
    # sentence say "5 of your 3".
    denominator = max(holdings_count or 0, len(usable))

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
            f"{len(group)} of your {denominator} holdings {verb} together — "
            f"mostly {factor_label}, averaging {avg:+.1f}%."
        )
    else:
        text = (
            f"{len(group)} of your {denominator} holdings {verb} together, "
            f"averaging {avg:+.1f}% — no single sector driving it."
        )

    return WidgetBasketResponse(
        direction=direction,
        moved_count=len(group),
        # The SAME number the sentence prints, so the two can never disagree.
        total_count=denominator,
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


def _et_calendar_day() -> date:
    """The ET calendar date right now — what "today" means to the person reading."""
    from app.utils.market_hours import ET
    return datetime.now(ET).date()


class QuoteSourceUnavailable(Exception):
    """The quote SOURCE failed for a one-symbol attribution (not: this symbol has no row)."""


class WidgetMoversService:
    """Two-tier cache + in-flight dedup, per CLAUDE.md invariant #4.

    The in-memory tier is short (60s) because a widget's value is freshness, and the
    upstream is cheap: everything below is either a batched Supabase read or one FMP
    batch-quote shared by every caller in that window.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[float, WidgetMoverPayload]] = {}
        self._inflight: Dict[str, asyncio.Future] = {}
        # session ISO date -> (monotonic stamp, context). Keyed by SESSION because the
        # earnings window is a function of it — see `_market_context`.
        self._ctx_cache: Dict[str, Tuple[float, _MarketContext]] = {}

    # ── public ───────────────────────────────────────────────────────

    async def get_market_mover(self) -> WidgetMoverPayload:
        # A payload with neither assets nor a brief is the Market tile with nothing to draw
        # — every leg failed. Pinning that for 60 s would serve a blank tile to every
        # WidgetKit wake in the window; the next caller retries instead.
        return await self._cached(
            "market", self._build_market, cacheable=_market_payload_is_cacheable,
        )

    async def get_portfolio_mover(self, user_id: str, tickers: Sequence[str]) -> WidgetMoverPayload:
        # KEYED ON THE USER, not just the ticker set.
        #
        # The build is not a pure function of the tickers: `_build_portfolio` calls
        # `_sectors(user_id, ...)`, which fills sector gaps from the CALLER'S OWN
        # `watchlist_items.sector` rows. Two users holding the same names therefore
        # shared a cached `basket.factor_label` derived from one of them's private
        # annotations. Ticker-only keying also becomes an outright financial-data leak
        # the moment anything per-user (a weighting, a value) enters this payload.
        #
        # The cost is losing cross-user dedup for portfolio mode; collision on an exact
        # 30-ticker set is vanishingly rare, so in practice nothing is lost.
        key = "portfolio:" + user_id + ":" + ",".join(
            sorted({t.upper() for t in tickers if t})
        )
        return await self._cached(key, lambda: self._build_portfolio(user_id, tickers))

    async def attribute_ticker_move(self, ticker: str) -> Optional["MoveExplanation"]:
        """Same-day attribution for ONE arbitrary ticker — the widget's engine, made callable.

        Exists so that "why did this move today" has exactly ONE implementation. Ask Cay AI
        needs the same answer the Home Screen widget shows, and re-deriving it in the chat
        layer would give the two surfaces different explanations for the same market day —
        the precise inconsistency `_sectors` documents and rejects a few hundred lines below.

        The composition is `_build_mover`'s, minus the ranking: `_rank_and_read` for the
        quote + σ + news card, `_market_context` for the industry / earnings / market legs
        (1h cached, shared with the widget), then the pure `attribute()`.

        Returns None when the move is unreadable — an unusable quote is not a flat day, and
        the caller must be able to tell those apart. `CauseKind.NONE` is a real answer and
        is returned normally. Raises `QuoteSourceUnavailable` when the QUOTE SOURCE failed
        (as opposed to this one symbol having no usable row): the batch always carries the
        SPY/QQQ/DIA band, so a batch with no index row at all is an outage, not a miss —
        and an outage is the caller's UPSTREAM failure (refundable), a miss is not.
        """
        sym = (ticker or "").upper().strip()
        if not sym:
            return None
        try:
            ranked, cards, news_available, index_rows = await self._rank_and_read([sym])
        except Exception as e:
            logger.warning(
                "attribution: rank/read failed for %s (%s: %s)", sym, type(e).__name__, e
            )
            raise QuoteSourceUnavailable(f"{type(e).__name__}: {e}") from e
        if not ranked:
            if not index_rows:
                # `price_service.get_quotes` folds a universe failure into `{}`, so the
                # only trace of an outage is that even the index band came back empty.
                logger.warning(
                    "attribution: quote source returned nothing for %s AND the index band "
                    "— treating as an outage, not an unreadable symbol", sym,
                )
                raise QuoteSourceUnavailable("quote source returned no rows (index band empty)")
            # `rank_movers` drops a row whose change is missing or non-finite.
            return None
        m = ranked[0]

        # THE ROW'S session, not the wall clock — the same derivation the widget payload
        # uses. Ask Cay AI and the Home Screen widget must give the same answer for the
        # same market day, and this path asked `session_trading_date()` directly: at 07:30
        # ET Monday that is MONDAY, while the screener is still reporting Friday's close,
        # so the news and earnings detectors were queried for a day the move did not happen
        # on and returned the confident negative "no company news today".
        cal_day = _et_calendar_day()
        today, today_iso, session_word = self._session_of(
            [m], session_trading_date(), cal_day,
        )
        # ⚠️ NOT for the market proxy itself. `ctx.market_change` IS
        # `index_rows[MARKET_INDEX_SYMBOL]["changePercentage"]` — the very quote
        # `m.change_percent` came from — so for SPY the ratio is exactly 1.0 and
        # `detect_group_move` answers "The market fell 1.6% today; SPY moved with it."
        # Ask Cay AI renders that verbatim. Passing None lets it fall through to a real
        # catalyst or an honest CauseKind.NONE. (The single-symbol path reaches the
        # band at all because `_rank_and_read(["SPY"])` is an all-band list, which the
        # ranking exclusion deliberately yields on rather than return nothing.)
        market_leg_off = sym.upper() == MARKET_INDEX_SYMBOL.upper()
        # A 24/7 asset's move is always its own rolling 24 hours — the same per-row
        # override `_build_mover` applies on the widget path, ALL of it: the word, the
        # market leg (a closed equity session cannot be what a live coin "moved with"),
        # and the news gate (the coin's own ET calendar day, never the equity session —
        # on a Saturday that is Friday, and Saturday's card read as unchecked).
        rolling = _is_round_the_clock(sym)
        if rolling:
            if session_word != "today":
                market_leg_off = True
            session_word = "today"
        ctx = await self._market_context([sym], index_rows, today_iso)
        if rolling:
            classified, had_news, card_checked = _classified_rolling_news(
                cards.get(sym), cal_day.isoformat()
            )
        else:
            classified, had_news, card_checked = _classified_today_news(
                cards.get(sym), today_iso
            )
        industry = ctx.industry_for(sym)
        a = attribute(
            ticker=sym,
            change_percent=m.change_percent,
            # ⚠️ PASS THE WORD. `attribute`'s default is `session_word="today"`, and that
            # word is what `detect_group_move` and `describe_no_cause` print. Deriving the
            # session here and then dropping it left Ask Cay AI narrating a Friday move as
            # "today" while the widget — which passes it explicitly — said "on Fri", i.e.
            # the two surfaces disagreed about the same market day, which is the exact
            # divergence this function's docstring says it exists to prevent.
            session_word=session_word,
            today=today,
            z=m.z,
            open_price=m.open_price,
            previous_close=m.previous_close,
            industry_name=industry[0],
            industry_change_percent=industry[1],
            # SPY itself, or a 24/7 row beside a closed equity session — see above.
            market_change_percent=None if market_leg_off else ctx.market_change,
            earnings_row=ctx.earnings_for(sym),
            # `grades` is 402 under the Order Form, so the analyst detector is inert.
            # Passing None is honest; `_head_grades` would spend a guaranteed failure.
            grade_rows=None,
            classified_news=classified,
            had_news=had_news,
            news_checked=ctx.news_available and news_available and card_checked,
        )
        if a is None:
            return None
        return MoveExplanation(
            ticker=sym,
            company_name=m.company_name,
            change_percent=m.change_percent,
            price=m.price,
            tier=m.tier,
            z=m.z,
            industry_name=industry[0],
            industry_change_percent=industry[1],
            market_change_percent=ctx.market_change,
            attribution=a,
            session_word=session_word,
            session_date=today_iso,
        )

    # ── cache plumbing ───────────────────────────────────────────────

    async def _cached(
        self,
        key: str,
        build,
        cacheable: Optional[Callable[[WidgetMoverPayload], bool]] = None,
    ) -> WidgetMoverPayload:
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
            # Joiners of THIS build still share it either way; `cacheable` only decides
            # whether the next caller in the TTL gets it too (default: always).
            if cacheable is None or cacheable(payload):
                self._evict_expired()
                self._cache[key] = (time.monotonic(), payload)
            else:
                logger.warning(
                    "widget: %s payload not cached — nothing renderable was built "
                    "(every leg failed); the next refresh rebuilds", key.split(":", 1)[0],
                )
            if not fut.done():
                fut.set_result(payload)
            return payload
        except BaseException as e:
            # Waiters must not hang when the leader is cancelled — the shape
            # news_cache_service uses, and the one the Learn services got wrong.
            fail_shared_future(fut, e)
            raise
        finally:
            self._inflight.pop(key, None)

    def _evict_expired(self) -> None:
        """Drop entries no reader can still use, once the map gets large.

        Only runs past the ceiling, so the common path stays a plain dict insert. Entries
        older than the TTL can never be returned by `_cached` anyway — they are pure
        retention, and with a per-user key the map would otherwise grow for the life of
        the process.
        """
        if len(self._cache) <= _CACHE_MAX_ENTRIES:
            return
        cutoff = time.monotonic() - _MEM_TTL_SECONDS
        stale = [k for k, (stamp, _) in self._cache.items() if stamp < cutoff]
        for k in stale:
            self._cache.pop(k, None)
        if len(self._cache) > _CACHE_MAX_ENTRIES:
            # Everything is live and we are still over: shed the oldest rather than let
            # the map grow without bound.
            for k, _ in sorted(self._cache.items(), key=lambda kv: kv[1][0])[
                : len(self._cache) - _CACHE_MAX_ENTRIES
            ]:
                self._cache.pop(k, None)
        logger.info(
            "widget: cache swept, %d entries remain (dropped %d expired)",
            len(self._cache), len(stale),
        )

    # ── builders ─────────────────────────────────────────────────────

    async def _build_market(self) -> WidgetMoverPayload:
        tickers = await self._swept_universe()
        # The legacy z-ranked universe movers stay: every INSTALLED build renders
        # `headline_mover` / `runners_up` in Market mode. The current tile draws
        # `market_assets` instead, which ride the same batch quote (`index_rows`).
        ranked, cards, news_ok, index_rows = await self._rank_and_read(
            tickers, phase=session_phase(),
        )
        # A failed universe read leaves `ranked` empty, and `_session_of` then falls back to
        # the LIVE session — pre-market that is TODAY, while every band and pulse row is still
        # stamped with yesterday's close, so `build_market_assets` withheld every change and
        # the grid drew bare prices under "Pre-market". Date it by those rows' own stamps.
        # The same plurality rule the movers get: one ETF row already stamped with the next
        # session must not re-date the four still on yesterday's close.
        session_basis = list(ranked) or drop_prior_session_movers(
            _stamp_only_movers(index_rows), phase=session_phase(),
        )[0]
        _, session_iso, _ = self._session_of(session_basis, session_trading_date())
        ctx = await self._market_context([m.ticker for m in ranked], index_rows, session_iso)
        ctx = replace(ctx, news_available=news_ok)
        # Three independent best-effort reads, concurrently: each already degrades to
        # None on its own, and none should add its latency to the others.
        grades, market_card, crypto_tile = await asyncio.gather(
            self._head_grades(ranked),
            self._market_card(),
            self._crypto_tile(),
            return_exceptions=True,
        )
        if isinstance(grades, BaseException):
            logger.warning("widget: grades read failed: %s: %s", type(grades).__name__, grades)
            grades = None
        if isinstance(market_card, BaseException):
            logger.warning(
                "widget: market roll-up read failed: %s: %s",
                type(market_card).__name__, market_card,
            )
            market_card = None
        if isinstance(crypto_tile, BaseException):
            logger.warning(
                "widget: Bitcoin tile read failed: %s: %s",
                type(crypto_tile).__name__, crypto_tile,
            )
            crypto_tile = None
        return self._payload(
            mode="market", ranked=ranked, cards=cards, ctx=ctx,
            basket=None, head_grades=grades,
            scope_label=_SCOPE_MARKET,
            market_card=market_card,
            crypto_tile=crypto_tile,
            session_basis=session_basis,
        )

    async def _crypto_tile(self) -> Optional[Any]:
        """The Home Market Pulse's Bitcoin tile, for the Market tile's grid — or None.

        ⚠️ NEVER `price_service` directly. CoinGecko Basic is 100k calls/MONTH, and the Home
        strip deliberately serves this tile from its own 600 s cache for exactly that
        reason (see `home_dashboard_service._PULSE_SYMBOLS`); a widget path quoting BTC on
        its own 60 s cycle would be a second consumer of the same budget. Market mode only.
        """
        from app.services.home_dashboard_service import get_home_dashboard_service

        try:
            return await asyncio.wait_for(
                asyncio.shield(get_home_dashboard_service().get_crypto_pulse_tile()),
                timeout=_CRYPTO_TILE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "widget: Bitcoin tile not ready within %.0fs — omitted from the Market "
                "tile this build (the fetch continues and fills the shared cache)",
                _CRYPTO_TILE_TIMEOUT_SECONDS,
            )
            return None
        except Exception as e:
            logger.warning(
                "widget: Bitcoin tile unavailable (%s: %s) — omitted from the Market tile",
                type(e).__name__, e,
            )
            return None

    async def _market_card(self) -> Optional[Dict[str, Any]]:
        """The `__MARKET__` roll-up behind the Market tile's headline.

        BEST-EFFORT, and separate from the per-ticker `get_cards` in `_rank_and_read`
        because that read is shared with portfolio mode, which must not pay for it.
        One batched Supabase select; a failure costs the headline and nothing else.
        Market mode only.
        """
        try:
            cards = await get_news_insight_service().get_cards([MARKET_SCOPE])
            return cards.get(MARKET_SCOPE)
        except Exception as e:
            logger.warning(
                "widget: market roll-up unavailable (%s: %s) — the tile will lead with "
                "the index numbers instead",
                type(e).__name__, e,
            )
            return None

    async def _build_portfolio(
        self, user_id: str, tickers: Sequence[str]
    ) -> WidgetMoverPayload:
        # The tile's denominator — every holding asked about, whether or not it prices.
        requested = len({str(t).upper().strip() for t in tickers if t and str(t).strip()})
        # |%| ranking, and the market band ranks like any holding: the Holdings tile no
        # longer draws the band, and the counts must cover EVERY holding — a user who holds
        # SPY wants to see SPY. The self-attribution guard lives in `_build_mover`.
        ranked, cards, news_ok, index_rows = await self._rank_and_read(
            tickers, basis="abs_change", exclude_band=False, phase=session_phase(),
        )
        # DEGRADED, as opposed to "your holdings have no price today": the quote leg failed.
        # `index_rows` rides the same batch as the holdings, so a batch that returned no
        # index row at all is an outage (the same tell `attribute_ticker_move` uses); an
        # empty ranking with SPY also unreadable is one too. The client keeps its last good
        # snapshot over a degraded one, and must never be told "no prices" by an outage.
        spy_change = finite((index_rows.get(MARKET_INDEX_SYMBOL) or {}).get("changePercentage"))
        # ⚠️ SPY proves only that the EQUITY leg answered. 24/7 pairs are priced by a
        # SEPARATE source (CoinGecko), which `price_service` degrades on its own and folds
        # into "no row" — a 429 on the monthly quota, a 5xx. So a group with nothing
        # rankable but coins, while SPY prints, shipped an authoritative "No prices for
        # your 3 holdings today" over the user's good snapshot for as long as the quota
        # stayed exhausted. A requested coin with NO coin in the ranking is that outage.
        # (Accepted cost: a coin CoinGecko cannot resolve reads as degraded — the last good
        # snapshot is kept — rather than as "no price". The safer wrong answer.)
        crypto_requested = any(
            _is_round_the_clock(str(t).upper().strip()) for t in tickers if t
        )
        crypto_dark = crypto_requested and not any(
            _is_round_the_clock(m.ticker) for m in ranked
        )
        degraded = (not index_rows) or (not ranked and (spy_change is None or crypto_dark))
        if degraded:
            logger.warning(
                "widget: portfolio quote leg degraded for user=%s (%d holdings, %d ranked, "
                "index rows %s, crypto leg dark=%s) — movers and holdings_count withheld",
                user_id, requested, len(ranked), sorted(index_rows)[:6], crypto_dark,
            )
        # A DEGRADED build is MOVER-LESS. With the equity leg down and CoinGecko up, the
        # ranking still held the coins, so the payload headlined BTCUSD +0.8% as the group's
        # "biggest mover" while NVDA may have fallen 8% — and a payload with a headline is
        # content, so the client replaced its good snapshot with it. No headline and no
        # count is the shape every build (installed ones too) refuses over a good snapshot.
        movers: List[RankedMover] = [] if degraded else list(ranked)
        symbols = [m.ticker for m in movers]
        _, session_iso, _ = self._session_of(movers, session_trading_date())
        ctx, sectors, grades = await asyncio.gather(
            self._market_context(symbols, index_rows, session_iso),
            self._sectors(user_id, symbols),
            # `_head_grades([])` returns before any call — no paid lookup for a headline
            # that will not be drawn.
            self._head_grades(movers),
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
        ctx = replace(ctx, news_available=news_ok)

        return self._payload(
            mode="portfolio", ranked=movers, cards=cards, ctx=ctx,
            basket=(
                None if degraded
                else detect_basket(movers, sectors, holdings_count=requested)
            ),
            head_grades=None if degraded else grades,
            scope_label=_SCOPE_PORTFOLIO,
            holdings_count=None if degraded else requested,
        )

    # ── same-day context (the whole cost story) ──────────────────────

    async def _market_context(
        self,
        tickers: Sequence[str],
        index_rows: Optional[Dict[str, Dict[str, Any]]],
        session_date: str,
    ) -> "_MarketContext":
        """Two universe-wide FMP calls plus one batched Supabase read.

        Cached for an hour because none of it changes intraday in a way that would
        alter an attribution: an industry's daily % drifts, and today's earnings
        calendar is fixed by the opening bell.

        Cached PER SESSION. The earnings window is `previous_trading_day(session)..session`,
        so a context fetched for Tuesday is the wrong answer for Wednesday: one shared slot
        filled at 04:01 Wednesday (window Tue..Wed) served pre-market builds describing
        TUESDAY for an hour, and Monday's after-close print — the cause of Tuesday's move —
        was never even fetched.
        """
        session_day = _parsed_session(session_date)
        if session_day is None:
            # Never expected: every caller passes `_session_of`'s ISO date. The gates in
            # `for_tickers` still compare against `session_date` itself (and so fail
            # closed); only the earnings window needs a day, so name the substitute loudly.
            session_day = session_trading_date()
            logger.warning(
                "widget: unreadable session %r for the market context — earnings window "
                "taken from the live session %s", session_date, session_day,
            )
        key = session_day.isoformat()
        hit = self._ctx_cache.get(key)
        if hit and (time.monotonic() - hit[0]) < _CONTEXT_TTL_SECONDS:
            shared = hit[1]
        else:
            shared = await self._fetch_market_context(session_day)
            # Do NOT pin a context in which every leg failed. Caching it turns one bad
            # minute upstream into a full hour of "no clear catalyst in today's news" —
            # a confident negative produced entirely by an outage.
            if (
                shared.industry_available
                or shared.earnings_available
                or shared.sector_available
            ):
                self._ctx_cache[key] = (time.monotonic(), shared)
                if len(self._ctx_cache) > _CTX_CACHE_MAX_SESSIONS:
                    # Oldest SESSION first — the live one is always the newest key.
                    for old in sorted(self._ctx_cache)[: len(self._ctx_cache) - _CTX_CACHE_MAX_SESSIONS]:
                        self._ctx_cache.pop(old, None)
            else:
                logger.warning(
                    "widget: every market-context leg failed for session %s — not "
                    "caching, will retry", key,
                )

        # The ticker→industry map is per-request (it depends on which tickers we are
        # attributing), but it is one batched select against a shared cache table.
        industry_map = await self._industries(tickers)

        # `company_profile_cache` is populated lazily on ticker-detail views, so a
        # newer or less-visited name can be missing — measured: ACHR and JOBY were,
        # and without an industry the widget loses its most useful comparison. Fill
        # the gap for the HEADLINE ticker only: one FMP call, spent on the one line
        # the reader actually looks at. Runners-up degrade to no industry line.
        head = tickers[0].upper() if tickers else None
        if head and head not in industry_map:
            name = await self._industry_for_one(head)
            if name:
                industry_map[head] = name

        # Derive a per-request VIEW. Never write onto the shared cached instance: the
        # iOS app fires both widget routes concurrently, and an earlier version assigned
        # `ctx.ticker_industry` in place with further awaits afterwards — so the two
        # builds erased each other's map and the market tile reported "no catalyst" for
        # a plainly sector-driven move.
        # The session the PAYLOAD describes (the head's `changeSession`, see
        # `_session_of`) — not the wall clock — so the industry and sector gates compare
        # like with like: pre-market Monday, Friday's industry move for Friday's change.
        # ⚠️ NO `or session_trading_date()` FALLBACK. `session_date` used to be
        # `Optional[str] = None` resolved that way, i.e. the gate FAILED OPEN to the wall
        # clock — and one of the three call sites relied on it, so a pre-market Monday
        # attribution compared Friday's change against Monday's sector rows. A required
        # parameter makes that a signature error instead of a silent mis-gate.
        return shared.for_tickers(industry_map, session_date, index_rows)

    async def _industry_for_one(self, ticker: str) -> Optional[str]:
        try:
            rows = await get_fmp_client().get_company_profiles_batch([ticker])
        except Exception as e:
            logger.warning(
                "widget: profile lookup failed for %s (%s: %s)",
                ticker, type(e).__name__, e,
            )
            return None
        for r in rows or []:
            ind = str((r or {}).get("industry") or "").strip()
            if ind:
                return ind
        return None

    async def _fetch_market_context(self, session_day: date) -> "_MarketContext":
        fmp = get_fmp_client()
        # The SESSION being attributed (the payload's, from `_session_of`), so the earnings
        # window matches the day the detectors gate on. It used to be the wall clock's
        # `session_trading_date()`, which is right on a Saturday but wrong every pre-market:
        # at 06:50 Wednesday the payload describes TUESDAY while the window was Tue..Wed, so
        # Monday's after-close print never arrived. Trading days, not calendar days: a
        # Monday session needs Friday's after-close row (Sun..Mon held nothing).
        today = session_day
        window_start = previous_trading_day(session_day)
        # (Before either fix: with the raw wall clock, a Saturday build fetched Fri..Sat
        # while `attribute()` was gating on Friday — so a Thursday-evening report, inside
        # the t-1 window for a Friday session, was never even fetched.)

        async def _industry_perf():
            rows = await get_market_movers_service().get_industry_performance()
            out: Dict[str, float] = {}
            # FMP stamps every row with the session it describes, and
            # `_latest_perf_snapshot` deliberately walks back to the last trading day —
            # so on a Monday morning these are legitimately FRIDAY's numbers. Keep the
            # date; `industry_for` refuses to print a cross-session comparison as "today".
            #
            # `date` comes from `_group_performance`, which stamps the session its members'
            # changes actually describe. It is NOT `session_trading_date()` and NOT the
            # close snapshot's `trade_date` — either alone is wrong half the time. The
            # producer derives it the same way `_pick_denominator` chooses: a price that
            # has moved off the stored close belongs to the live session; a price still
            # equal to it means the change is the one that close ended.
            # 🔴 PER-INDUSTRY dates, not one scalar for the whole batch.
            #
            # This used to keep only the FIRST row's date — and `rows` is sorted by
            # `changesPercentage` DESCENDING, so the scalar was whichever session the day's
            # TOP-GAINING industry happened to describe. `industry_for` then fails closed on
            # `industry_snapshot_date != session_date` for **all** industries.
            #
            # Concretely: mid-session, the top-ranked industry is one whose members are
            # mostly untraded so far, so their prices still equal the stored close and
            # `_group_performance` correctly stamps them with the PREVIOUS trade date. Every
            # widget card in that cycle then loses its industry line — including industries
            # whose own stamp is today — for the full hourly context-cache window.
            #
            # The producer already computes a date per row; this just stops discarding it.
            dates: Dict[str, str] = {}
            for r in rows or []:
                name = str(r.get("industry") or "").strip().lower()
                chg = _group_change(r)
                if name and chg is not None:
                    out[name] = chg
                    d = str(r.get("date") or "").strip()
                    if d:
                        dates[name] = d[:10]
            return out, dates

        async def _earnings():
            # ONE CALL PER TRADING DAY, never one multi-day window. FMP cuts an
            # `earnings-calendar` answer at 4,000 rows and keeps the NEWEST dates, so on a
            # peak-season Monday a Fri..Mon request dropped Friday — the after-close prints
            # that explain Monday's moves, i.e. exactly the rows this window exists to read.
            # A day missing is a missed cause; it never becomes an invented one.
            days = list(dict.fromkeys((window_start, today)))
            answers = await asyncio.gather(
                *(
                    fmp.get_earnings_calendar(
                        from_date=d.isoformat(), to_date=d.isoformat()
                    )
                    for d in days
                ),
                return_exceptions=True,
            )
            out: Dict[str, Dict[str, Any]] = {}
            failures: List[BaseException] = []
            for d, rows in zip(days, answers):
                iso = d.isoformat()
                if rows is None:
                    rows = []
                if isinstance(rows, BaseException) or not isinstance(rows, list):
                    err = (
                        rows if isinstance(rows, BaseException)
                        else TypeError(
                            f"earnings calendar for {iso} returned "
                            f"{type(rows).__name__}, not a list"
                        )
                    )
                    failures.append(err)
                    logger.warning(
                        "widget: earnings calendar for %s failed (%s: %s) — that day's "
                        "prints are missing from the context",
                        iso, type(err).__name__, err,
                    )
                    continue
                if len(rows) >= _EARNINGS_TRUNCATION_ROWS:
                    logger.error(
                        "widget: earnings calendar for %s returned %d rows — at FMP's "
                        "silent cap, so it is probably TRUNCATED (newest kept) and some "
                        "of that day's prints are missing",
                        iso, len(rows),
                    )
                for r in rows:
                    # Only under its own date: a lenient upstream (or a fake) that answers
                    # with other days must not leak them into this one.
                    if not isinstance(r, dict) or str(r.get("date") or "")[:10] != iso:
                        continue
                    sym = str(r.get("symbol") or "").upper()
                    if not sym:
                        continue
                    prior = out.get(sym)
                    if prior is None or _better_earnings_row(r, prior):
                        out[sym] = r
            if failures and len(failures) == len(days):
                # EVERY day failed: raise, so `earnings_available` stays False and the
                # empty dict is never mistaken for "nobody reported".
                raise failures[-1]
            return out

        # NOTE: there is deliberately NO index leg here any more.
        #
        # `^GSPC`'s intraday `changePercentage` used to be fetched in this function and
        # therefore cached for an hour alongside the earnings calendar — while every
        # stock's own change was refetched every 60s. `_moved_with_group` then compared a
        # ≤60s-old stock move against an up-to-3600s-old market move inside a ratio band
        # of [0.6, 1.7], which is exactly the test mismatched timestamps corrupt. The
        # indices now ride the universe batch quote in `_rank_and_read`: one call FEWER
        # than before, and as fresh as the payload itself.

        async def _sector_perf():
            # ONE shared call in the normal case — the dated snapshot covers all 11
            # sectors, for every user, and lands in the hourly context cache.
            #
            # ⚠️ `get_sector_performance` has an ETF FALLBACK (`_sector_perf_from_etfs`)
            # that fans out ~2 calls per sector ETF when the snapshot is unavailable. That
            # is ~22 calls, not 1. It is bounded — the hourly cache caps it at 24 rounds a
            # day for the whole product, and it is still O(1) in user count — but it is
            # why this leg must stay inside `_ctx_cache` and must never be moved onto the
            # 60-second payload path.
            rows = await get_market_movers_service().get_sector_performance()
            out: List[Tuple[str, float]] = []
            dates: Dict[str, str] = {}
            for r in rows or []:
                name = str(r.get("sector") or "").strip()
                chg = _group_change(r)
                if name and chg is not None:
                    out.append((name, chg))
                    stamp = str(r.get("date") or "")[:10]
                    if stamp:
                        dates[name.strip().lower()] = stamp
            return out, dates

        industry, earnings, sectors = await asyncio.gather(
            _industry_perf(), _earnings(), _sector_perf(), return_exceptions=True
        )
        ctx = _MarketContext()
        # Each leg degrades independently: losing the earnings calendar must not cost
        # us the industry attribution.
        #
        # The `*_available` flag is set ONLY on success, and is what stops a failure
        # being reported downstream as a checked negative. An empty dict from a
        # successful call ("no industries moved enough to report") and an empty dict
        # from a 429 are the same value; only the flag tells them apart.
        if isinstance(industry, tuple):
            ctx.industry_changes, ctx.industry_dates = industry
            ctx.industry_available = True
        else:
            logger.warning(
                "widget: industry snapshot failed (%s: %s) — industry comparison "
                "will be omitted rather than reported as absent",
                type(industry).__name__, industry,
            )
        if isinstance(earnings, dict):
            ctx.earnings = earnings
            ctx.earnings_available = True
        else:
            logger.warning(
                "widget: earnings calendar failed (%s: %s)",
                type(earnings).__name__, earnings,
            )
        if isinstance(sectors, tuple):
            ctx.sector_changes, ctx.sector_dates = sectors
            ctx.sector_available = True
        else:
            logger.warning(
                "widget: sector snapshot failed (%s: %s) — breadth will be omitted "
                "rather than reported as zero",
                type(sectors).__name__, sectors,
            )
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
        if not analyst_section_available():
            # `grades` is outside the signed FMP Order Form (402). One guaranteed failure
            # per widget refresh, logged as a warning, to arrive at None. Skip it.
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
        calendar_day: date,
        grade_rows: Optional[Sequence[Dict[str, Any]]] = None,
        session_word: str = "today",
        aged: bool = False,
    ) -> WidgetMoverResponse:
        # `calendar_day` is the ET calendar day of the build (clock-injected by `_payload`):
        # the day a 24/7 row's rolling window ends, which gates its news card and names its
        # aged wording. An equity row never reads it — `today` is its session.
        #
        # ⚠️ NOT for the market proxy itself — the same guard `attribute_ticker_move`
        # applies. `ctx.market_change` IS SPY's own change, so a SPY row (portfolio mode
        # ranks the band like any holding) would read "The market fell 1.6% today; SPY
        # moved with it." None lets it fall through to a real catalyst or an honest NONE.
        market_leg = (
            None if m.ticker.upper() == MARKET_INDEX_SYMBOL.upper() else ctx.market_change
        )
        # PER-ROW session word. A round-the-clock asset's move is always its own rolling
        # 24 hours, whatever session the equities in the same batch are reporting — and
        # equally, an equity row keeps the equity word even when the tile's HEAD is a
        # crypto pair. One word for the whole tile made one of those two wrong.
        rolling = _is_round_the_clock(m.ticker)
        if rolling:
            if session_word != "today":
                # The equity leg describes a CLOSED session (a weekend, overnight): a live
                # rolling 24 h move cannot have "moved with the market" that is not open,
                # and the sentence would say so with the word "today".
                market_leg = None
            session_word = "today"
            # ITS OWN news gate: the ET calendar day the rolling window ends, not the equity
            # session — on a Saturday that is Friday, and Saturday's card read as unchecked.
            classified, had_news, card_checked = _classified_rolling_news(
                cards.get(m.ticker), calendar_day.isoformat()
            )
        else:
            classified, had_news, card_checked = _classified_today_news(
                cards.get(m.ticker), today_iso
            )
        industry = ctx.industry_for(m.ticker)

        inputs: Dict[str, Any] = dict(
            ticker=m.ticker,
            change_percent=m.change_percent,
            today=today,
            z=m.z,
            open_price=m.open_price,
            previous_close=m.previous_close,
            industry_name=industry[0],
            industry_change_percent=industry[1],
            market_change_percent=market_leg,
            earnings_row=ctx.earnings_for(m.ticker),
            grade_rows=grade_rows,
            classified_news=classified,
            had_news=had_news,
            # Both must hold: the batched read has to have succeeded AND this particular
            # ticker has to have an insight row behind it.
            news_checked=ctx.news_available and card_checked,
        )
        a = attribute(**inputs, session_word=session_word)
        # `attribute` returns None only for an unreadable move, which `rank_movers`
        # has already filtered out — but degrade rather than crash if that changes.
        mc = a.context if a else None

        # The SAME attribution, worded for a reader on a later day. Pure and I/O-free, so
        # the second pass costs nothing; HEADLINE only (the one line the tile spells out).
        #
        # A 24/7 row gets one too, named for the ET CALENDAR day of the build — never the
        # equity session (Monday pre-market that is Friday, and "on Fri" would mislabel a
        # live Monday move). Without it a crypto headline on Tuesday's snapshot, still on
        # the Home Screen Wednesday, fell back to the "today"-worded `detail` under a
        # "Tue close" footer — and the |%| ranking makes coins the most common headline.
        detail_aged: Optional[str] = None
        if aged:
            aged_word = f"on {(calendar_day if rolling else today).strftime('%a')}"
            b = attribute(**inputs, session_word=aged_word)
            detail_aged = (
                b.detail if b else deterministic_reason(m.change_percent, m.z, aged_word)
            )
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
                detail=(
                    a.detail if a
                    else deterministic_reason(m.change_percent, m.z, session_word)
                ),
                detail_aged=detail_aged,
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
            rolling_24h=rolling,
            asset_type=m.asset_type,
        )

    @staticmethod
    def _session_of(
        ranked: Sequence[RankedMover], live_session: date, calendar_day: Optional[date] = None,
    ) -> Tuple[date, str, str]:
        """The session the ranked changes describe: (date, iso, wording).

        `calendar_day` is the ET calendar date the words are spoken on. The WORD is
        "today" only when the stamped session IS that day: on a Saturday the live session
        is Friday and every stamp is Friday, and `stamped >= live_session` alone worded
        Friday's move "today" — the model said "TSLA is down 4% today" all weekend, and
        the tier-3 catalyst gate (which keys on the word) bought a fresh web search for a
        session that had ended the day before. The returned DATE is unchanged: every
        detector is gated on the session, not on the wording. Clock-injected, like
        `live_session` — callers pass the day; this function never reads the wall clock.

        Batch quotes carry `changeSession` (stamped by `price_service` with the same
        derivation the movers universe uses). At 07:30 ET Monday the screener still
        reports Friday's close, so every change is FRIDAY's move — and this used to be
        labelled with `session_trading_date()` (Monday), so the tile said "Down 4.8%
        today" under "Pre-market 7:30 AM ET", gated earnings/news on Monday's rows, and
        attached a Monday BMO print as the cause of Friday's move. The NEWEST stamp in
        the batch is authoritative — `drop_prior_session_movers` has already removed
        rows stamped older than it, so every stamped row agrees; a batch with no stamp
        at all (older shape) keeps the live session.
        """
        # ⚠️ THE DATE IS ALWAYS THE EQUITY ONE. A 24/7 head does get the word "today" —
        # labelling a crypto pair's rolling 24-hour move "on Fri" asserts a session that
        # asset does not have — but that override belongs on the ROW, not here: this
        # function's return also carries the date every detector is gated on. Returning
        # the live session for a crypto head moved that date too, so on a Monday
        # pre-market with the screener still on Friday's close, the equity RUNNERS
        # narrated Friday's −4.8% as "…today", `_classified_today_news` was keyed on
        # Monday and matched nothing (printing the confident negative "No company news
        # today"), and the tile's own label presented a Friday move as Monday's — which
        # is verbatim the cross-session bug this function exists to kill. `_build_mover`
        # now applies the 24/7 word per row.
        stamped = newest_session(ranked) or live_session
        if stamped >= live_session:
            if calendar_day is None or live_session == calendar_day:
                return live_session, live_session.isoformat(), "today"
            # A weekend / holiday: the freshest session is real, but it is not today.
            return live_session, live_session.isoformat(), f"on {live_session.strftime('%a')}"
        return stamped, stamped.isoformat(), f"on {stamped.strftime('%a')}"

    def _payload(
        self,
        *,
        mode: str,
        ranked: Sequence[RankedMover],
        cards: Dict[str, Optional[Dict[str, Any]]],
        ctx: "_MarketContext",
        basket: Optional[WidgetBasketResponse],
        head_grades: Optional[Sequence[Dict[str, Any]]] = None,
        scope_label: Optional[str] = None,
        market_card: Optional[Dict[str, Any]] = None,
        holdings_count: Optional[int] = None,
        crypto_tile: Optional[Any] = None,
        session_basis: Optional[Sequence[RankedMover]] = None,
    ) -> WidgetMoverPayload:
        """Assemble the payload. `holdings_count` (portfolio: the requested holdings, None
        when the build is DEGRADED) and `crypto_tile` (market: the Home pulse's Bitcoin
        tile) are each read by one mode only. `session_basis` dates the payload when
        `ranked` cannot (market mode after a failed universe read); default: `ranked`."""
        # The SESSION date, not the wall clock. Every detector below is gated on this:
        # earnings rows, analyst grades and news cards are all stamped with a trading
        # day. Using `datetime.now(ET).date()` meant that on a Saturday — when the quotes
        # being described are Friday's close — nothing could match, so every detector
        # went dark AND the tile still asserted "No company news today." A confident
        # negative produced by asking about the wrong day.
        live_session = session_trading_date()
        # Read ONCE and handed down: the session wording and every 24/7 row's news gate and
        # aged wording must agree on what day it is, even across a midnight mid-build.
        calendar_day = _et_calendar_day()
        today, today_iso, session_word = self._session_of(
            ranked if session_basis is None else session_basis,
            live_session, calendar_day,
        )

        portfolio = mode == "portfolio"
        # ONE ROW PER TICKER, the headline never repeating below itself — see
        # `select_payload_movers`, the rule `_rank_and_read`'s card read also follows.
        head_m, runner_ms, gainer_ms, loser_ms = select_payload_movers(ranked)

        def _mover(m: RankedMover) -> WidgetMoverResponse:
            # Everything except the per-ticker grades lookup, which is the only paid call
            # in the chain and is spent on the headline alone.
            return self._build_mover(
                m, cards=cards, ctx=ctx, today=today, today_iso=today_iso,
                calendar_day=calendar_day, session_word=session_word,
            )

        head: Optional[WidgetMoverResponse] = None
        if head_m is not None:
            head = self._build_mover(
                head_m, cards=cards, ctx=ctx, today=today,
                today_iso=today_iso, calendar_day=calendar_day,
                grade_rows=head_grades, session_word=session_word,
                aged=True,
            )
        runners = [_mover(m) for m in runner_ms]

        # Portfolio only: the counts and the two columns. Never in market mode, which
        # describes a universe, not the reader's holdings.
        gainers: List[WidgetMoverResponse] = []
        losers: List[WidgetMoverResponse] = []
        up = down = flat = None
        if portfolio:
            gainers = [_mover(m) for m in gainer_ms]
            losers = [_mover(m) for m in loser_ms]
            if holdings_count is not None:
                # Withheld on a DEGRADED build along with the count they are read
                # against: "0 up, 0 down" from an outage is not a finding.
                up, down, flat = direction_counts(ranked)

        return WidgetMoverPayload(
            mode=mode,
            as_of=_iso_now(),
            market_session=session_phase(),
            # The session these numbers describe, and the sentence that names it. The
            # client re-derives an aged label from `session_date` at RENDER time, which
            # is how a Friday snapshot read on Sunday says "Fri close" instead of
            # silently presenting Friday's −5% as today's.
            session_date=today_iso,
            session_label=(
                session_label() if today == live_session
                else f"{today.strftime('%a')} close"
            ),
            scope_label=scope_label,
            # Gated on the SAME session date every other detector uses, so an
            # off-session roll-up is dropped rather than labelled.
            market_brief=_market_brief(market_card, today_iso),
            market_context=build_market_context(
                ctx.index_rows, ctx.sector_changes,
                sector_available=ctx.sector_available,
            ),
            headline_mover=head,
            basket=basket,
            runners_up=runners,
            holdings_count=holdings_count if portfolio else None,
            up_count=up,
            down_count=down,
            flat_count=flat,
            top_gainers=gainers,
            top_losers=losers,
            market_assets=(
                build_market_assets(ctx.index_rows, today_iso, crypto_tile)
                if mode == "market" else []
            ),
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
        self,
        tickers: Sequence[str],
        *,
        basis: str = "z",
        exclude_band: bool = True,
        phase: Optional[str] = None,
    ) -> Tuple[List[RankedMover], Dict[str, Optional[Dict[str, Any]]], bool, Dict[str, Dict[str, Any]]]:
        """Quote, rank and card-read `tickers`: (ranked, cards, news_available, index_rows).

        `basis` is `rank_movers`'; `exclude_band=False` lets SPY/ONEQ/DIA rank like any
        holding (portfolio mode — the Holdings tile no longer draws the band); `phase` is
        `drop_prior_session_movers`' (the builders pass `session_phase()`; None keeps
        newest-wins). The one-argument form is `attribute_ticker_move`'s and must keep
        working with these defaults.

        `index_rows` carries the band AND the Home pulse equities (IWM, GLD), quoted in the
        SAME batch: `build_market_context` reads the band, `build_market_assets` the pulse.
        """
        symbols = [s for s in dict.fromkeys(str(t).upper().strip() for t in tickers if t) if s]
        # The Home Market Pulse equities, imported so the Market tile can never list
        # different assets from Home. Function-local: a heavyweight module.
        from app.services.home_dashboard_service import _PULSE_SYMBOLS

        index_syms = [s for s, _ in _INDEX_SYMBOLS]
        extra_syms = list(dict.fromkeys(
            index_syms + [str(cfg.get("symbol") or "").upper() for cfg in _PULSE_SYMBOLS]
        ))
        extra_syms = [s for s in extra_syms if s]

        if not symbols:
            # A failed universe read must not cost the band and the Market grid too: they
            # do not depend on the universe. One batch of the ETFs alone, best-effort.
            try:
                quotes = await self._quotes(extra_syms)
            except Exception as e:
                logger.warning(
                    "widget: index-only quote failed: %s: %s — no band, no market assets",
                    type(e).__name__, e,
                )
                quotes = {}
            return [], {}, True, {s: quotes[s] for s in extra_syms if quotes.get(s)}

        # The indices ride along on the SAME request — `batch-quote` chunks at 300 and the
        # universe is capped at 200, so this is free. In market mode they are excluded from
        # ranking below; an index is not a "mover" the market tile can attribute.
        quotes, sigmas = await asyncio.gather(
            self._quotes(symbols + [s for s in extra_syms if s not in symbols]),
            get_volatility_cache_service().get_sigmas_bulk(symbols),
            return_exceptions=True,
        )
        if isinstance(quotes, BaseException):
            logger.warning("widget: quote fetch failed: %s: %s", type(quotes).__name__, quotes)
            quotes = {}
        if isinstance(sigmas, BaseException):
            logger.warning("widget: sigma read failed: %s: %s", type(sigmas).__name__, sigmas)
            sigmas = {}

        # The "Nasdaq 0.00%" investigation: a band ETF reading ~0 is either a genuinely
        # flat session or `price_service`'s close-equality denominator picking the wrong
        # base after a sub-cent drift. Log the three inputs that tell them apart.
        for s in index_syms:
            q = quotes.get(s) or {}
            chg = finite(q.get("changePercentage"))
            if chg is not None and abs(chg) < _FLAT_PCT:
                logger.info(
                    "widget: band ETF %s reports a ~0%% change (%r): price=%r "
                    "previousClose=%r changeSession=%r",
                    s, chg, q.get("price"), q.get("previousClose"), q.get("changeSession"),
                )

        candidates: List[Dict[str, Any]] = []
        funds: List[str] = []
        for sym in symbols:
            q = quotes.get(sym) or {}
            if q.get("isFund"):
                # An open-end mutual fund prints ONE NAV a day and can never be "today's
                # mover". The universe sweep no longer carries funds, but a watchlisted fund
                # still reaches here through the `/stable/profile` fallback — UNSTAMPED
                # (`_from_profile` has no `changeSession`), so it would rank instead of
                # being dropped as prior-session. Refused here, on the flag, not the name.
                funds.append(sym)
                continue
            candidates.append(
                {
                    "ticker": sym,
                    "change_session": q.get("changeSession"),
                    "change_percent": q.get("changePercentage"),
                    "price": q.get("price"),
                    "company_name": q.get("name"),
                    "market_cap": q.get("marketCap"),
                    "sigma_daily": sigmas.get(sym),
                    "open": q.get("open"),
                    "previous_close": q.get("previousClose"),
                    "asset_type": _quote_asset_type(sym, q),
                }
            )
        if funds:
            logger.info(
                "widget: %d open-end fund row(s) excluded from the ranking (no intraday "
                "print): %s", len(funds), ", ".join(funds[:10]),
            )

        def _rank(rows: List[Dict[str, Any]]) -> Tuple[List[RankedMover], List[RankedMover]]:
            return drop_prior_session_movers(rank_movers(rows, basis=basis), phase=phase)

        # THE MARKET BAND CANNOT ALSO BE THE MOVER — in market mode, and in the single-
        # symbol attribution path that shares this default. `market_change` is literally
        # `index_rows[MARKET_INDEX_SYMBOL]`'s own change, so a SPY headline attributes SPY's
        # move to itself ("The market fell 1.6% today; SPY moved with it."), and the band is
        # the row the tile already draws, so any of them headlining prints the same number
        # twice. (Portfolio mode passes `exclude_band=False`: its tile no longer draws the
        # band, and `_build_mover` withholds the market leg from SPY instead.)
        band = {s.upper() for s in index_syms} if exclude_band else set()
        ranked, stale = _rank([r for r in candidates if r["ticker"] not in band])
        if not ranked and any(r["ticker"] in band for r in candidates):
            # ONLY when something else can carry the tile — and decided on the RANKED
            # output, not on the requested symbols. Deciding on symbols emptied the widget
            # for {SPY, a mutual fund} or {SPY, a delisted name}: the other symbol kept the
            # band excluded, then was itself dropped as a fund / an unpriced row, and
            # `headline_mover=None` renders every iOS family's empty state. A
            # self-referential headline is a smaller wrong than a blank tile.
            logger.info(
                "widget: nothing outside the market band is rankable (%s) — ranking the "
                "band rather than serving an empty tile",
                ", ".join(sorted(symbols))[:120],
            )
            ranked, stale = _rank(candidates)
        if stale:
            logger.warning(
                "widget: %d row(s) stamped with a session other than the tile's dropped "
                "from the ranking (halted / not yet printed / outside the pre-market "
                "plurality): %s",
                len(stale),
                ", ".join(f"{m.ticker}@{m.change_session}" for m in stale[:10]),
            )

        # Cards for exactly the movers the payload can render — headline, runners-up and
        # the portfolio gainer / loser columns, by the SAME selection rule `_payload`
        # uses. A rendered mover without its card read would be UNCHECKED ("could not
        # check the news") although a card exists. `get_cards` is one batched select
        # regardless of how many scopes it is handed.
        #
        # BEST-EFFORT. This read is the least important thing on the tile and it used to
        # be the most dangerous: unwrapped, one Supabase hiccup propagated to the route's
        # catch-all and returned the EMPTY payload — discarding the mover, the price, the
        # σ multiple and the industry comparison that were all already in hand. A widget
        # whose news lookup failed should lose its news line, not its contents.
        head_m, runner_ms, gainer_ms, loser_ms = select_payload_movers(ranked)
        card_syms = list(dict.fromkeys(
            m.ticker for m in ([head_m] if head_m else []) + runner_ms + gainer_ms + loser_ms
        ))
        cards: Dict[str, Optional[Dict[str, Any]]] = {}
        news_available = True
        if card_syms:
            try:
                insights = get_news_insight_service()
                cards = await insights.get_cards(card_syms)
                # A card from an earlier day than a FAILED attempt on newer news is not "no
                # news today": the sweeper had news and could not write a card (since v8 the
                # price guard refuses a big mover whose coverage is all price talk). Read it
                # as unchecked, never as "No company news today."
                behind = getattr(insights, "scopes_failed_after_their_card", None)
                if cards and callable(behind):
                    for scope in await behind(cards):
                        cards[scope] = None
            except Exception as e:
                news_available = False
                logger.warning(
                    "widget: news cards unavailable (%s: %s) — the tile will say it could "
                    "not check, not that there was nothing",
                    type(e).__name__, e,
                )
        index_rows = {s: (quotes.get(s) or {}) for s in extra_syms if quotes.get(s)}
        return ranked, cards, news_available, index_rows

    async def _quotes(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        rows = await price_source(self).get_quotes_list(symbols)
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
