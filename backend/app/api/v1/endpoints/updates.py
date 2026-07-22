"""
Updates screen endpoints — the iOS `UpdatesView` tab.

  GET  /api/v1/updates/tabs                     filter pills (watchlist + Market)
  GET  /api/v1/updates/feed?scope=…             timeline + AI insight, one call
  POST /api/v1/updates/news/enrich              on-demand per-article AI

DESIGN NOTE — the read path never calls Gemini.
Insight cards are produced exclusively by the background sweeper
(`services/updates_insight_sweeper.py`). These handlers only read caches, so the
screen's latency is independent of LLM latency. A scope with no card yet gets a
deterministic, LLM-free fallback built from the real headlines, flagged
``ai_generated=false`` + ``refreshing=true`` so the client neither claims AI
authorship nor waits forever.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from app.api.error_response import (
    ErrorCode,
    error_response_from_exception,
    make_error_response,
)
from app.database import get_supabase
from app.dependencies import StandardRateLimit, get_current_user_or_guest
from app.integrations.fmp import get_fmp_client
from app.schemas.updates import (
    AIInsightCardResponse,
    EnrichUpdatesNewsRequest,
    EnrichUpdatesNewsResponse,
    UpdatesArticleResponse,
    UpdatesFeedResponse,
    UpdatesTabResponse,
    UpdatesTabsResponse,
)
from app.services.news_cache_service import (
    MARKET_SCOPE,
    get_news_cache_service,
    is_crypto_scope,
)
from app.services.news_insight_service import (
    CORPUS_WINDOW_HOURS,
    articles_within_window,
    get_news_insight_service,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Ticker symbols are short; anything longer is malformed input, not a real scope.
_MAX_SCOPE_LEN = 32
# Watchlist pills shown in the tab bar. More than this and the strip is unusable.
_MAX_TABS = 30


def _valid_scope(scope: str) -> bool:
    if not scope or len(scope) > _MAX_SCOPE_LEN:
        return False
    if scope == MARKET_SCOPE:
        return True
    # FMP symbols: letters, digits, and . - ^ = (indices, crypto pairs, futures).
    return all(c.isalnum() or c in ".-^=" for c in scope)


# ── Tabs ──────────────────────────────────────────────────────────────

@router.get("/tabs", response_model=UpdatesTabsResponse)
async def get_updates_tabs(
    user: dict = Depends(get_current_user_or_guest),
):
    """Filter pills for the Updates tab bar: 'Market' plus the user's watchlist.

    Deliberately lighter than ``GET /tracking/assets``, which also builds
    sparklines, alerts and portfolio math this strip would discard. One
    ``batch-quote`` call resolves every pill's change % (plus the index) —
    not one call per ticker.
    """
    user_id = user["id"]
    supabase = get_supabase()

    def _read_watchlist() -> List[Dict[str, Any]]:
        result = (
            supabase.table("watchlist_items")
            .select("ticker, company_name, logo_url, asset_type, added_at")
            .eq("user_id", user_id)
            .order("added_at", desc=True)
            .limit(_MAX_TABS)
            .execute()
        )
        return result.data or []

    try:
        rows = await asyncio.to_thread(_read_watchlist)
    except Exception as e:
        logger.error(
            "Updates tabs: watchlist read failed for user=%s: %s: %s",
            user_id, type(e).__name__, e, exc_info=True,
        )
        # The Market tab alone is still a usable screen — degrade rather than
        # failing the whole tab bar.
        rows = []

    tickers = [
        str(r["ticker"]).upper()
        for r in rows
        if r.get("ticker") and _valid_scope(str(r["ticker"]).upper())
    ]
    tickers = list(dict.fromkeys(tickers))

    quotes: Dict[str, Dict[str, Any]] = {}
    try:
        fmp = get_fmp_client()
        # Watchlist tickers only — the Market pill carries no change %, so
        # MARKET_INDEX_SYMBOL would be a quote nobody reads. `_bulk` returns []
        # for an empty list, so a user with no watchlist skips the call entirely.
        for q in await fmp.get_batch_quotes_bulk(tickers):
            sym = q.get("symbol")
            if sym:
                quotes[str(sym).upper()] = q
    except Exception as e:
        # Non-fatal: pills render without a change %, which iOS handles by
        # hiding the label. A fabricated 0.0% would be worse than none.
        logger.warning(
            "Updates tabs: quote fetch failed (%s: %s) — rendering without change %%",
            type(e).__name__, e,
        )

    def _change(sym: str) -> Optional[float]:
        raw = (quotes.get(sym) or {}).get("changePercentage")
        try:
            f = float(raw)
        except (TypeError, ValueError):
            return None
        # FMP emits NaN/Infinity tokens for thin or just-listed symbols; those
        # serialize to invalid JSON under allow_nan=False and 500 the screen.
        return f if f == f and abs(f) != float("inf") else None

    tabs = [
        UpdatesTabResponse(
            scope=MARKET_SCOPE,
            title="Market",
            company_name="S&P 500",
            # No change % on the Market pill. This tab is GENERAL market news —
            # `news/general-latest` plus coverage of SPY/QQQ/DIA/^GSPC/^IXIC —
            # so there is no single instrument whose move it reports. Pinning
            # the S&P's number to it claims a precision the tab does not have,
            # and sits inches from ticker pills where the % means exactly one
            # thing. iOS hides the label when this is null.
            change_percent=None,
            is_market_tab=True,
        )
    ]
    by_ticker = {str(r["ticker"]).upper(): r for r in rows if r.get("ticker")}
    for t in tickers:
        row = by_ticker.get(t, {})
        tabs.append(
            UpdatesTabResponse(
                scope=t,
                title=t,
                company_name=row.get("company_name"),
                change_percent=_change(t),
                logo_url=row.get("logo_url"),
                is_market_tab=False,
            )
        )

    logger.info(
        "Updates tabs for user=%s: %d pills (%d quoted)",
        user_id, len(tabs), len(quotes),
    )
    return UpdatesTabsResponse(tabs=tabs)


# ── Feed ──────────────────────────────────────────────────────────────

@router.get("/feed", response_model=UpdatesFeedResponse)
async def get_updates_feed(
    scope: str = Query(MARKET_SCOPE, description="'__MARKET__' or a ticker symbol"),
    limit: int = Query(50, ge=1, le=50),
    # Page-through of retained history. The sweeper refreshes on a 96h lookback,
    # so a busy scope holds several days of rows while one page shows well under
    # a day — without this the client could never reach yesterday. Bounded so a
    # scripted deep-offset scan cannot make Postgres walk an unbounded range.
    offset: int = Query(0, ge=0, le=500),
    # Public, but throttled: a cache MISS costs a live FMP call, and an
    # unrecognised scope never caches, so an unthrottled loop over junk scopes
    # would drain the FMP quota. The sweeper's spend ceilings do not cover this
    # path — they govern Gemini, not FMP.
    _rate_limit=StandardRateLimit,
):
    """One tab's content: the news timeline plus its AI Insights card.

    Bundled into a single request because the client needs both to render a tab,
    and two round-trips on every tab switch is a visible stall on cellular.
    """
    scope = scope.strip().upper() if scope != MARKET_SCOPE else MARKET_SCOPE
    if not _valid_scope(scope):
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Invalid scope: {scope!r}",
            user_message="That feed isn't available.",
            details={"scope": scope},
        )

    news = get_news_cache_service()
    insights = get_news_insight_service()

    try:
        if scope == MARKET_SCOPE:
            feed = await news.get_market_news(limit=limit, offset=offset)
        else:
            # Crypto symbols must go to FMP's `news/crypto`; `news/stock` returns
            # nothing for BTCUSD, so a crypto watchlist row would render a pill
            # with a permanently empty feed.
            feed = await news.get_ticker_news(
                scope, limit=limit, is_crypto=is_crypto_scope(scope),
                offset=offset,
            )
    except Exception as e:
        logger.error(
            "Updates feed failed for scope=%s: %s: %s",
            scope, type(e).__name__, e, exc_info=True,
        )
        return error_response_from_exception(e, ticker=scope, step="updates_feed")

    raw_articles = feed.get("articles") or []
    articles = [_to_article(a) for a in raw_articles]

    # Insight: cache read only. Never blocks on Gemini.
    #
    # Page 0 ONLY. The card sits above the timeline and the client already has
    # it; re-sending it on every scroll page would cost a Supabase read per page
    # and — worse — a page-2 fallback card would be built from page 2's
    # articles, i.e. a summary of yesterday's news replacing today's.
    insight: Optional[AIInsightCardResponse] = None
    if offset == 0:
        # Only surface an Insights card when the scope actually has news within
        # the badge window (CORPUS_WINDOW_HOURS). With nothing recent, show NO
        # card at all — neither the AI card nor the headline fallback — rather
        # than summarising stale news under a "48h" label. The timeline below
        # still renders whatever cached articles exist.
        cutoff = datetime.now(timezone.utc) - timedelta(hours=CORPUS_WINDOW_HOURS)
        recent = articles_within_window(raw_articles, cutoff)
        if recent:
            try:
                cards = await insights.get_cards([scope])
                card = cards.get(scope)
                if card is None:
                    # No AI card yet (cold scope, or the sweeper hasn't reached
                    # it). Serve the honest headline list, built ONLY from the
                    # in-window articles so it cannot claim "48h" over old news.
                    # The card's `refreshing` flag says whether the sweeper is
                    # actually awake to replace it — outside market hours it is
                    # not, and promising otherwise is what pinned the UI on
                    # "Catching up…" all weekend.
                    card = insights.build_fallback_card(scope, recent)
                if card is not None:
                    insight = AIInsightCardResponse(**card)
            except Exception as e:
                # An unavailable insight must never take down the timeline.
                logger.warning(
                    "Updates insight read failed for scope=%s: %s: %s",
                    scope, type(e).__name__, e,
                )

    # A full page implies there may be another; a short page is provably the
    # end. Also stop at the `offset` ceiling so the client cannot chase a page
    # the query parameter would reject.
    has_more = len(articles) >= limit and (offset + limit) <= 500

    logger.info(
        "Updates feed scope=%s articles=%d offset=%d has_more=%s cached=%s insight=%s",
        scope, len(articles), offset, has_more, feed.get("cached"),
        "ai" if (insight and insight.ai_generated)
        else "fallback" if insight else "none",
    )
    return UpdatesFeedResponse(
        scope=scope,
        articles=articles,
        insight=insight,
        cached=bool(feed.get("cached")),
        cache_age_seconds=feed.get("cache_age_seconds"),
        offset=offset,
        has_more=has_more,
    )


# ── Enrichment ────────────────────────────────────────────────────────

@router.post("/news/enrich", response_model=EnrichUpdatesNewsResponse)
async def enrich_updates_news(
    body: EnrichUpdatesNewsRequest,
    # Throttled: this is the one public path that can trigger a paid Gemini call.
    _rate_limit=StandardRateLimit,
):
    """AI-enrich specific timeline articles (bullets + sentiment), on demand.

    Delegates to the existing shared enrichment path — there is deliberately no
    second AI implementation for this screen.
    """
    scope = body.scope.strip().upper() if body.scope != MARKET_SCOPE else MARKET_SCOPE
    if not _valid_scope(scope):
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Invalid scope: {scope!r}",
            user_message="That feed isn't available.",
            details={"scope": scope},
        )

    # Drop client-side placeholder ids (temp_/raw_) — they are not DB rows, so
    # sending them would just widen the IN () clause for nothing.
    ids = [
        i for i in dict.fromkeys(body.article_ids or [])
        if i and not i.startswith(("temp_", "raw_", "sample_"))
    ]
    if not ids:
        return EnrichUpdatesNewsResponse(scope=scope, articles=[])
    if len(ids) > 50:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Too many article_ids: {len(ids)} (max 50)",
            user_message="Too many articles requested at once.",
            details={"count": len(ids)},
        )

    try:
        enriched = await get_news_cache_service().enrich_articles(scope, ids)
    except Exception as e:
        logger.error(
            "Updates enrichment failed for scope=%s (%d ids): %s: %s",
            scope, len(ids), type(e).__name__, e, exc_info=True,
        )
        return error_response_from_exception(e, ticker=scope, step="updates_enrich")

    return EnrichUpdatesNewsResponse(
        scope=scope, articles=[_to_article(a) for a in enriched]
    )


# ── Mapping ───────────────────────────────────────────────────────────

def _opt_str(value: Any) -> Optional[str]:
    """Coerce any non-None value to ``str``; pass None through.

    Every ``Optional[str]`` field below is fed a raw cache value that, on a
    cold FMP miss, may be a non-string (FMP occasionally returns a JSON array
    for ``image`` or a number for a field). Handing that straight to a Pydantic
    v2 ``Optional[str]`` raises ``ValidationError`` — a 500 for the WHOLE feed
    because of one degraded article — and the SAME shared row still renders on
    the ticker-detail News tab, which coerces via the identical helper in
    ``schemas/news.py``. Coerce here so one bad row degrades to a valid string
    instead of taking down the timeline.
    """
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _to_article(row: Dict[str, Any]) -> UpdatesArticleResponse:
    """Map a cache row to the API shape, guarding every field iOS decodes.

    ``sentiment`` stays None when the article has not been AI-enriched: the
    cache stores a normalized 'neutral' for un-analysed rows, and forwarding
    that would render a confident badge no model produced.
    """
    ai_processed = bool(row.get("ai_processed"))
    sentiment = row.get("sentiment")
    if not ai_processed or sentiment in (None, ""):
        sentiment = None
    else:
        sentiment = str(sentiment).strip().lower()

    bullets = row.get("summary_bullets") or []
    if not isinstance(bullets, list):
        bullets = []

    related = row.get("related_tickers") or []
    if not isinstance(related, list):
        related = []

    try:
        confidence = int(row.get("sentiment_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0

    return UpdatesArticleResponse(
        id=str(row.get("id") or ""),
        headline=str(row.get("headline") or ""),
        summary=_opt_str(row.get("summary")),
        summary_bullets=[str(b) for b in bullets if isinstance(b, str)],
        sentiment=sentiment,
        sentiment_confidence=max(0, min(100, confidence)),
        source_name=_opt_str(row.get("source_name")),
        source_logo_url=_opt_str(row.get("source_logo_url")),
        published_at=_opt_str(row.get("published_at")),
        thumbnail_url=_opt_str(row.get("thumbnail_url")),
        article_url=_opt_str(row.get("article_url")),
        related_tickers=[str(t) for t in related if t],
        ai_processed=ai_processed,
    )
