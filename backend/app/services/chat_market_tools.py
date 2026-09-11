"""The market-awareness tools Ask Cay AI calls: news, market breadth, and "why did it move".

WHY THIS FILE EXISTS
--------------------
Two TestFlight-visible failures traced to the same cause. Asked *"Why is NAVN down 22%
today?"* Cay AI restated the price, the volume and the average volume. Asked *"Why is Basic
Materials lagging today?"* — a question **we** generate as a suggestion chip — it answered
*"My tools are designed to analyze individual company stocks rather than entire sectors."*

No prompt in this repo says that. The model was describing its environment accurately: after
the FMP Order Form took `grades` (402, so `analyst_section_available()` strips
`get_analyst_analysis`), a stock or global chat was left holding exactly two tools —
`get_stock_chart_data` and `get_sentiment_analysis`. Sentiment returns *counts and scores*,
never a headline, so the model could not see a single news item; and `sector_performance`
rode on `get_market_overview`, which `_TOOLS_BY_ASSET_TYPE` grants to `INDEX` alone. The
quote was genuinely all it had.

Every capability below already existed and was wired to some OTHER surface — the Updates
sweeper, the Home Screen widget, the research agent. Nothing here is a new data source; it
is the missing wiring.

THE ESCALATION LADDER — the whole cost story
--------------------------------------------
`explain_price_move` answers in three tiers and stops at the first that finds a
company-specific cause:

  1. `widget_movers_service.attribute_ticker_move` — the deterministic detector set
     (earnings / analyst / company news / group move / gap), arithmetic and string matching
     over data already paid for. Free, and it cannot hallucinate.
  2. The ticker's 6h-cached news corpus. Free — FMP's "Market News" package IS on the Order
     Form, unlike the quote and market-performance families.
  3. `price_catalyst_service.get_catalyst` — a grounded Google Search. **This is the only
     paid step in the file**, at roughly $0.035 a call, and it is gated three ways: the move
     must be volatility-relative material, tiers 1-2 must have found nothing
     company-specific, and a durable daily budget must admit it.

⚠️ **The window label MUST stay `"today"`.** It is a cache-identity component
(`_ctx_key`, migration 095) and the Updates sweeper already writes `"today"` rows, so
matching it means chat SHARES that 24h cache and a watchlist name usually costs nothing.
It is also a correctness guard: `daily_move_attribution`'s own header records a measured
case where the cached window was "Last 15 Days" (+42.7%) and printing it under a red daily
move produced "a correct answer to a different question". Any other label re-opens that.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.chat_budget_service import (
    ChatBudgetUnavailable,
    get_chat_budget_service,
)
from app.services.updates_materiality import (
    BAND_EXTREME,
    TIER_EXTREME,
    TIER_UNUSUAL,
)

logger = logging.getLogger(__name__)

# The move tiers that earn a paid search, byte-identical to the Updates sweeper's
# `_CATALYST_TIERS`. Shared deliberately: two surfaces with two thresholds is how one
# starts explaining moves the other calls ordinary, for the same ticker on the same day.
# `BAND_EXTREME` is the fixed-band fallback a thin-history or newly-listed name lands on
# when σ is unavailable — precisely the population most prone to violent moves.
_CATALYST_TIERS = frozenset({TIER_UNUSUAL, TIER_EXTREME, BAND_EXTREME})

# Attribution kinds that already name a company-specific cause. Reaching any of these
# means tier 3 has nothing to add and must not run.
_COMPANY_SPECIFIC_KINDS = frozenset({"earnings", "analyst", "company_news"})

# Bucket key for the GLOBAL daily ceiling on chat-initiated web searches.
#
# Reuses `chat_usage_budget` + the `claim_chat_turn` RPC rather than adding a table:
# that column is a bare uuid with no FK and the RPC takes an arbitrary limit, which is
# exactly how `_ip_budget_bucket` already shares it. So this is durable and atomic across
# Railway instances — unlike the sweeper's in-process `_CATALYST_DAILY_CAP`, whose own
# comment concedes a 2x blast radius across two instances — and needs no migration.
_WEB_SEARCH_BUCKET = str(
    uuid.uuid5(uuid.NAMESPACE_URL, "caydex:chat:web-search-budget")
)

# Headlines handed to the model per call. Small on purpose: a tool result is truncated at
# 8000 chars by `stream_agentic`, and the grounding is re-sent on EVERY turn (chat is not
# Gemini-context-cached), so every article here is re-billed for the rest of the session.
_NEWS_LIMIT = 8
_HEADLINE_CAP = 220
_SUMMARY_CAP = 280

# Industries are a long tail; sectors are all 11. Sending the whole industry list would
# blow the tool-result cap and bury the sectors that answer the question.
_INDUSTRY_EDGE = 5
# Beyond the two ends, how many more moving industries to name, and how small a move still
# counts as "moved". 30 rows of `{industry, change_percent}` is ~1.1KB against an 8000-char
# tool-result cap the payload currently uses under half of.
_INDUSTRY_REST_CAP = 30
_INDUSTRY_MIN_MOVE_PCT = 0.5
_HOT_TICKER_ROWS = 5


def _num(value: Any, digits: int = 2) -> Optional[float]:
    """A finite float, or None. NEVER a coerced zero.

    ⚠️ `x or 0.0` DOES NOT CATCH NaN — NaN is truthy, so `nan or 0.0` is `nan`. It then
    reaches `json.dumps` (which emits the bare token `NaN`, invalid JSON) on its way into a
    tool result the model must parse. This repo has shipped that exact confusion more than
    once, including a NaN winning a `max()` and headlining a widget.

    It matters HERE in particular because `market_movers_service._group_performance` gates
    its buckets on `change is not None`, not on `isfinite` — so one malformed universe row
    makes a whole sector's mean NaN, and this module is where that would leave the backend.

    None is the honest answer: the caller omits the field, and an omitted field reads as
    "not available" rather than as a flat 0.00%.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if math.isfinite(f) else None


def _trim(value: Any, cap: int) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


async def _claim_web_search() -> bool:
    """Admit one chat-initiated grounded search, or refuse.

    FAILS CLOSED, which is the opposite of `_claim_chat_turn_or_error` and deliberate.
    That one fails open because a DB blip must never wall a user out of chat; this one
    guards SPEND, and refusing only drops the turn back to the free tiers — a slightly
    thinner answer, never an error. Failing open here would uncap the one paid path.
    """
    if not getattr(settings, "CHAT_WEB_SEARCH_ENABLED", True):
        return False
    limit = getattr(settings, "CHAT_WEB_SEARCH_DAILY_CAP", 200)
    try:
        count = await asyncio.to_thread(
            get_chat_budget_service().try_claim_turn, _WEB_SEARCH_BUCKET, limit
        )
    except ChatBudgetUnavailable as e:
        logger.warning(
            "chat web-search budget unavailable — failing CLOSED, the turn keeps its "
            "deterministic answer: %s", e,
        )
        return False
    except Exception as e:  # noqa: BLE001 — a budget read must never break a turn
        logger.warning(
            "chat web-search budget raised unexpectedly (%s: %s) — failing closed",
            type(e).__name__, e,
        )
        return False
    if count == -1:
        logger.info("chat web-search daily cap reached (limit=%s)", limit)
        return False
    return True


# ── Tool 1: the ticker's recent news ──────────────────────────────────────────

async def fetch_ticker_news(ticker: str, is_crypto: bool = False) -> Dict[str, Any]:
    """Recent headlines for one symbol — the tool chat never had.

    ``is_crypto`` is REQUIRED at the call site, never defaulted downstream. Defaulting it
    is a defect this codebase has already shipped once: `_fetch_sentiment_data` let it fall
    to False and routed a coin through the equity news feed, so `BTCUSD` came back with zero
    articles and the model reported a confident zero-mention reading for the most-discussed
    asset on the screen.
    """
    sym = (ticker or "").upper().strip()
    if not sym:
        return {"error": "no ticker supplied"}

    from app.services.news_cache_service import get_news_cache_service

    try:
        payload = await get_news_cache_service().get_ticker_news(
            sym, limit=_NEWS_LIMIT, is_crypto=is_crypto
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "chat tool get_ticker_news failed for %s (%s: %s)", sym, type(e).__name__, e
        )
        # An explicit failure, NOT an empty list. "No news today" is a claim, and making
        # it out of a failed upstream call is the same lie `_MarketContext` guards against
        # with its `*_available` flags.
        return {"ticker": sym, "news_available": False,
                "note": "The news feed could not be reached; do not say there is no news."}

    articles: List[Dict[str, Any]] = []
    for row in (payload or {}).get("articles") or []:
        headline = _trim(row.get("headline"), _HEADLINE_CAP)
        if not headline:
            continue
        item: Dict[str, Any] = {"headline": headline}
        if row.get("published_at"):
            item["published_at"] = row["published_at"]
        if row.get("source_name"):
            item["source"] = row["source_name"]
        # Prefer the already-AI-extracted bullets over the raw blurb: they are the same
        # cache the News tab reads, so nothing is re-billed to produce them here.
        bullets = [b for b in (row.get("summary_bullets") or []) if str(b or "").strip()]
        if bullets:
            item["key_points"] = [_trim(b, _SUMMARY_CAP) for b in bullets[:3]]
        elif row.get("summary"):
            item["summary"] = _trim(row["summary"], _SUMMARY_CAP)
        if row.get("sentiment"):
            item["sentiment"] = row["sentiment"]
        articles.append(item)

    return {
        "ticker": sym,
        "news_available": True,
        "article_count": len(articles),
        "articles": articles,
    }


# ── Tool 2: the market's own day ──────────────────────────────────────────────

async def fetch_market_snapshot() -> Dict[str, Any]:
    """Sector + industry breadth, today's biggest movers, and the Updates AI market card.

    Every leg is FREE and already cached. Sector and industry performance are derived from
    one entitled `company-screener` sweep (FMP's own `sector-performance-snapshot` is 402
    under the Order Form); the movers reuse Home's ranker, quality gate included; and the
    market card is a pure cache read of the row the Updates sweeper already writes daily,
    catalyst and citations included.

    Legs degrade INDEPENDENTLY and say so. An absent key means "could not check", never
    "nothing happened" — the model must not be able to infer a flat market from an outage.
    """
    from app.services.market_movers_service import get_market_movers_service
    from app.services.news_cache_service import MARKET_SCOPE
    from app.services.news_insight_service import get_news_insight_service

    movers = get_market_movers_service()
    results = await asyncio.gather(
        movers.get_sector_performance(),
        movers.get_industry_performance(),
        movers.get_scanner_inputs(),
        get_news_insight_service().get_cards([MARKET_SCOPE]),
        return_exceptions=True,
    )
    sectors, industries, scanner, cards = results
    out: Dict[str, Any] = {}

    if isinstance(sectors, BaseException):
        logger.warning("chat tool: sector performance unavailable: %s: %s",
                       type(sectors).__name__, sectors)
    else:
        rows = []
        for r in (sectors or []):
            pct = _num(r.get("changesPercentage"))
            if pct is None:
                # Dropped, not zeroed: "this sector was flat" is a claim, and making it out
                # of one malformed universe row is the same lie as reporting no news after a
                # failed read.
                logger.warning("chat tool: dropping sector %r with unusable change",
                               r.get("sector"))
                continue
            rows.append({"sector": r.get("sector"), "change_percent": pct,
                         "companies": r.get("constituents")})
        if rows:
            out["sectors"] = rows

    if isinstance(industries, BaseException):
        logger.warning("chat tool: industry performance unavailable: %s: %s",
                       type(industries).__name__, industries)
    elif industries:
        def _rows(src_rows) -> List[Dict[str, Any]]:
            out_rows = []
            for r in src_rows:
                pct = _num(r.get("changesPercentage"))
                if pct is None:
                    continue
                out_rows.append({"industry": r.get("industry"), "sector": r.get("sector"),
                                 "change_percent": pct})
            return out_rows
        # Sorted % descending by `_group_performance`, so the two ends ARE the story.
        leading = _rows(industries[:_INDUSTRY_EDGE])
        lagging = _rows(industries[-_INDUSTRY_EDGE:])
        if leading:
            out["leading_industries"] = leading
        if lagging:
            out["lagging_industries"] = lagging

        # Everything else that MOVED, name and percentage only.
        #
        # The top and bottom five tell the day's story, but they are five of ~150 — so a user
        # naming any other industry ("what caused copper to drop?") hit a tool that knew the
        # answer existed and could not see it, and Cay AI said it had no information. Ranked
        # by ABSOLUTE change so the ones worth asking about survive the cap in both
        # directions; an industry that did not move is one whose honest answer is "it moved
        # normally", which needs no row here.
        shown = {r["industry"] for r in leading + lagging}
        rest = []
        for r in sorted(industries, key=lambda x: -abs(_num(x.get("changesPercentage")) or 0.0)):
            name = r.get("industry")
            pct = _num(r.get("changesPercentage"))
            if not name or name in shown or pct is None or abs(pct) < _INDUSTRY_MIN_MOVE_PCT:
                continue
            rest.append({"industry": name, "change_percent": pct})
            if len(rest) >= _INDUSTRY_REST_CAP:
                break
        if rest:
            out["other_industries_that_moved"] = rest

    if isinstance(scanner, BaseException):
        logger.warning("chat tool: universe unavailable: %s: %s",
                       type(scanner).__name__, scanner)
    else:
        out.update(_hot_tickers(scanner))

    if isinstance(cards, BaseException):
        logger.warning("chat tool: market insight card unavailable: %s: %s",
                       type(cards).__name__, cards)
    else:
        card = (cards or {}).get(MARKET_SCOPE)
        if card:
            out["market_story"] = _card_digest(card)

    if not out:
        return {"error": "No market data could be read right now."}
    return out


def _hot_tickers(scanner_inputs: Any) -> Dict[str, Any]:
    """Today's biggest movers, through Home's ranker rather than a re-derived sort.

    That ranker already carries the quality gate, joins class-share symbols whose profile
    spelling differs from the mover list, and drops the signed zero that paints a loser
    green — none of which is obvious, and all of which a hand-rolled `sorted()` here would
    silently lose.
    """
    try:
        profile_map, change_map = scanner_inputs
    except (TypeError, ValueError):
        return {}
    if not profile_map or not change_map:
        return {}

    from app.services.home_dashboard_service import _movers_from_universe

    out: Dict[str, Any] = {}
    for key, positive in (("top_gainers", True), ("top_losers", False)):
        try:
            rows = _movers_from_universe(
                profile_map, change_map, positive=positive, rows=_HOT_TICKER_ROWS
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("chat tool: mover ranking failed (%s: %s)",
                           type(e).__name__, e)
            continue
        # `_movers_from_universe` returns `ScannerRowResponse` MODELS, not dicts — a
        # `.get()` here would raise on every row and silently blank both leaderboards.
        picked = []
        for r in rows or []:
            sym = str(getattr(r, "symbol", "") or "").upper()
            if not sym:
                continue
            pct = _num(getattr(r, "change_percent", None))
            if pct is None:
                continue
            picked.append({"symbol": sym, "name": getattr(r, "name", None),
                           "change_percent": pct})
        if picked:
            out[key] = picked
    return out


def _card_digest(card: Dict[str, Any]) -> Dict[str, Any]:
    """The Updates screen's AI market card, compacted for a tool result.

    This is the "integrate with AI Insights" half: the same headline, bullets and cited
    catalyst the user already sees on Updates, so chat and that screen tell one story.
    """
    digest: Dict[str, Any] = {}
    if card.get("headline"):
        digest["headline"] = _trim(card["headline"], _HEADLINE_CAP)
    bullets = [_trim(b, _SUMMARY_CAP) for b in (card.get("bullets") or [])[:5]]
    bullets = [b for b in bullets if b]
    if bullets:
        digest["points"] = bullets
    pm = card.get("price_move") or {}
    if pm.get("reason"):
        digest["why_the_market_moved"] = {
            "tag": pm.get("catalyst_tag"),
            "reason": _trim(pm["reason"], _SUMMARY_CAP),
        }
    digest["as_of"] = card.get("generated_at")
    return digest


# ── Tool 3: why did it move today ─────────────────────────────────────────────

async def explain_price_move(ticker: str, is_crypto: bool = False) -> Dict[str, Any]:
    """Today's move for one symbol, explained — the escalation ladder.

    Tier 1 and 2 are free and always run. Tier 3 is the only paid step and is gated on
    all three of: a volatility-relative MATERIAL move, tiers 1-2 having found no
    company-specific cause, and the durable daily budget admitting it.
    """
    sym = (ticker or "").upper().strip()
    if not sym:
        return {"error": "no ticker supplied"}

    from app.services.widget_movers_service import get_widget_movers_service

    # ── Tier 1: deterministic ────────────────────────────────────────────────
    try:
        exp = await get_widget_movers_service().attribute_ticker_move(sym)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "chat tool explain_price_move: attribution failed for %s (%s: %s)",
            sym, type(e).__name__, e,
        )
        exp = None

    if exp is None:
        # `attribute_ticker_move` returns None only when the move is UNREADABLE. Saying
        # "flat" here would be the `describe_no_cause` lie in a different costume.
        return {
            "ticker": sym,
            "move_readable": False,
            "note": (
                "Today's move for this symbol could not be read. Say so plainly rather "
                "than describing it as unchanged."
            ),
        }

    a = exp.attribution
    out: Dict[str, Any] = {
        "ticker": sym,
        "company_name": exp.company_name,
        "move_readable": True,
        "change_percent": _num(exp.change_percent),
        # How abnormal this is FOR THIS TICKER — the single most useful framing the model
        # is missing today, and the reason a 3% day is routine for one name and historic
        # for another.
        "unusualness": exp.tier,
        "sigma_multiple": round(exp.z, 2) if exp.z is not None else None,
        "cause_kind": a.kind.value,
        "cause": a.tag,
        "explanation": a.detail,
        "how_unusual": _unusualness_note(exp.tier, exp.z),
    }
    conflict = _direction_conflict(a.tag, exp.change_percent)
    if conflict:
        out["important"] = conflict
    if exp.industry_name is not None:
        out["industry"] = exp.industry_name
        ind = _num(exp.industry_change_percent)
        if ind is not None:
            out["industry_change_percent"] = ind
    mkt = _num(exp.market_change_percent)
    if mkt is not None:
        out["market_change_percent"] = mkt

    # ── Tier 2: the cached news corpus ───────────────────────────────────────
    news = await fetch_ticker_news(sym, is_crypto=is_crypto)
    if news.get("news_available"):
        out["recent_news"] = news.get("articles") or []
    else:
        out["news_available"] = False

    # ── Tier 3: the paid grounded search ─────────────────────────────────────
    catalyst = await _maybe_web_catalyst(sym, exp, a.kind.value)
    if catalyst is not None:
        out["web_research"] = catalyst

    # ── The answer of last resort, so there is never a dead end ──────────────
    #
    # Requested directly after a follow-up chip Cay AI had PROPOSED came back "I don't have
    # specific information on what caused copper to drop today." A question the product puts
    # in the user's mouth must never be answered with a shrug: either a reason, or the honest
    # shape of the day — "it moved the way it normally moves", "no clear catalyst in the
    # news". Both of those ARE answers; "I don't know" is not.
    #
    # Only emitted when nothing upstream found a cause, so it can never talk over a real one.
    if a.kind.value == "none" and not out.get("web_research"):
        out["no_single_catalyst"] = True
        out["bottom_line"] = _bottom_line(exp, news)
    return out


def _bottom_line(exp: Any, news: Dict[str, Any]) -> str:
    """Arithmetic plus the news status — never wrong, and never empty.

    Reuses `deterministic_reason`, the widget's never-blank line, so the two surfaces phrase
    "how big was this, really" identically. It is a comparison rather than a bare number
    because "-4.8%" alone tells a reader nothing about whether that is remarkable for this
    particular stock.
    """
    from app.services.widget_movers_service import deterministic_reason

    move = deterministic_reason(exp.change_percent, exp.z)
    if not news.get("news_available"):
        # A failed read is NOT "no news". Asserting a negative nobody checked is the lie the
        # `*_available` flags exist to prevent.
        return f"{move} Today's news could not be checked, so do not say there was none."
    if not (news.get("articles") or []):
        return f"{move} No company news was published today."
    return f"{move} No single catalyst stands out in today's news."


# The two `daily_move_attribution` tags whose direction can CONTRADICT the price move.
# Pinned by `test_chat_market_tools.py` against that module, so a reword there fails a test
# rather than silently disabling the note below.
_BEAT_TAG = "Earnings Beat"
_MISS_TAG = "Earnings Miss"


def _direction_conflict(tag: Optional[str], change: Optional[float]) -> Optional[str]:
    """Flag an earnings reaction that runs OPPOSITE to the headline number.

    Measured live on 2026-09-10: NAVN beat EPS by 21.9% and fell 21.75% the same day. The
    detector is right — earnings IS the event — but handing the model "Earnings Beat" as the
    cause of a 22% fall invites the answer "it dropped because it beat estimates", which is
    nonsense and would read as the model not understanding the market.

    `daily_move_attribution` is deliberately left alone: it is a pure, heavily-tested module
    whose consumer (the Home Screen widget) renders a tag and a detail line, and a beat is
    genuinely what happened. What is missing is only the CONTRAST, and the contrast is a
    property of this answer, not of the attribution.
    """
    if change is None or not tag:
        return None
    if tag == _BEAT_TAG and change < 0:
        return (
            "The company BEAT estimates yet the stock FELL. Do not say it dropped because "
            "of the beat. The market reacted to something the headline number does not "
            "capture — guidance, margins, a segment miss, or an already-high valuation. "
            "Look in recent_news for which, and say plainly if it is not there."
        )
    if tag == _MISS_TAG and change > 0:
        return (
            "The company MISSED estimates yet the stock ROSE. Do not say it rose because of "
            "the miss. Look in recent_news for what offset it — guidance, a buyback, or a "
            "miss that was smaller than feared — and say plainly if it is not there."
        )
    return None


# `classify_move` returns two vocabularies — the σ tiers and the fixed-band fallback — and
# both are opaque labels chosen for a fingerprint, not for a reader. The model sees them raw
# otherwise, and "extreme" alone does not convey that it is measured against THIS ticker's own
# history rather than against some absolute percentage.
_UNUSUALNESS_NOTES = {
    "Typical": "an ordinary day for this ticker — inside its normal daily range",
    "Notable": "bigger than a normal day for this ticker",
    "Unusual": "much bigger than a normal day for this ticker",
    "Extreme": "far bigger than a normal day for this ticker",
    "notable": "a notable move, judged on price alone",
    "extreme": "a very large move, judged on price alone",
    "flat": "a small move — the kind of ordinary up-and-down any stock has",
    "unknown": "not measurable",
}


def _unusualness_note(tier: Optional[str], z: Optional[float]) -> Optional[str]:
    note = _UNUSUALNESS_NOTES.get(tier or "")
    if note is None:
        return None
    if z is not None:
        # The σ path: say how many standard deviations, which is the whole point of the tier.
        return f"{note} ({z:.1f}x its typical daily swing)"
    # The fallback path has no σ row, so the lowercase band labels above deliberately say
    # "judged on price alone" — claiming a σ multiple we never computed would be a lie.
    return note


async def _maybe_web_catalyst(
    sym: str, exp: Any, cause_kind: str
) -> Optional[Dict[str, Any]]:
    """The one paid path in this file, behind three independent gates.

    Order matters and is a cost decision, not a style one: the CACHE is consulted before
    the budget, so a row the Updates sweeper already paid for is served without consuming
    a unit. Reversing those two would let one popular ticker exhaust the daily cap while
    costing nothing.
    """
    if cause_kind in _COMPANY_SPECIFIC_KINDS:
        # Tiers 1-2 already named a company-specific cause. Paying to second-guess a
        # dated fact with a web search is how a good deterministic answer gets talked
        # over by a vaguer one.
        return None
    if exp.tier not in _CATALYST_TIERS:
        # An ordinary day for this ticker. There is usually no catalyst to find, and
        # searching for one invites the model to manufacture significance.
        return None
    change = exp.change_percent
    if change is None or change == 0:
        # Guard against paying to explain a phantom +0.0% — a live defect in the Updates
        # sweeper before `_maybe_price_move` gained the same check.
        return None

    from app.services.price_catalyst_service import get_price_catalyst_service

    svc = get_price_catalyst_service()
    try:
        # ⚠️ `"today"` — see this module's header. It is both the cache key shared with
        # the Updates sweeper and the guard against answering a daily question with a
        # multi-day window's narrative.
        cached = await svc.get_catalyst(sym, change, "today", cache_only=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("chat tool: catalyst cache read failed for %s (%s: %s)",
                       sym, type(e).__name__, e)
        cached = None
    if cached is not None:
        return _catalyst_digest(cached, paid=False)

    if not await _claim_web_search():
        return None

    try:
        fresh = await svc.get_catalyst(sym, change, "today")
    except Exception as e:  # noqa: BLE001
        logger.warning("chat tool: grounded catalyst failed for %s (%s: %s)",
                       sym, type(e).__name__, e)
        return None
    return _catalyst_digest(fresh, paid=True) if fresh else None


def _catalyst_digest(catalyst: Dict[str, Any], *, paid: bool) -> Optional[Dict[str, Any]]:
    """Shape a catalyst for the model, keeping its citations.

    `get_catalyst` degrades to `{tag: None, reason: <broad-market line>, sources: []}` when
    the search found nothing citable — a real answer, not a failure, and one worth passing
    through so the model can say "no single company-specific catalyst" with authority
    instead of guessing.
    """
    reason = _trim(catalyst.get("reason"), _SUMMARY_CAP)
    if not reason:
        return None
    digest: Dict[str, Any] = {"reason": reason, "from_web_search": True, "freshly_searched": paid}
    if catalyst.get("tag"):
        digest["catalyst"] = catalyst["tag"]
    sources = []
    for s in (catalyst.get("sources") or [])[:5]:
        publisher = str((s or {}).get("publisher") or "").strip()
        title = _trim((s or {}).get("title"), _HEADLINE_CAP)
        if publisher or title:
            sources.append({"publisher": publisher or None, "title": title})
    if sources:
        digest["sources"] = sources
    return digest
