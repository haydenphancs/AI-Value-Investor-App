"""
Backend ↔ iOS contract for the Updates screen.

A failure here means the iOS app throws a DecodingError in production — the
timeline goes blank or the tab bar loses its pills. This is a guard rail, not a
nice-to-have; do not disable it, fix the schema or the Swift DTO.

The Swift decoders these pin (frontend/ios/ios/Models/UpdatesModels.swift):
    UpdatesTabsResponse   { tabs: [UpdatesTabDTO] }
    UpdatesFeedResponse   { scope, articles, insight, cached, cache_age_seconds }
    AIInsightCardDTO      { scope, headline, bullets, sentiment, badge, … }
    UpdatesArticleDTO     { id, headline, summary, summary_bullets, … }
Note that `APIClient` does NOT use `.convertFromSnakeCase`, so the JSON keys
must be exactly the snake_case names below.
"""

import json
import math

import pytest
from pydantic import ValidationError

from app.api.v1.endpoints.updates import _to_article, _valid_scope
from app.schemas.updates import (
    AIInsightCardResponse,
    EnrichUpdatesNewsResponse,
    UpdatesArticleResponse,
    UpdatesFeedResponse,
    UpdatesTabResponse,
    UpdatesTabsResponse,
)
from app.services.news_cache_service import MARKET_SCOPE

# Exact key sets the Swift CodingKeys declare.
IOS_TAB_KEYS = {
    "scope", "title", "company_name", "change_percent", "logo_url", "is_market_tab",
}
IOS_INSIGHT_KEYS = {
    "scope", "headline", "bullets", "sentiment", "badge", "article_count",
    "generated_at", "is_stale", "refreshing", "ai_generated", "trigger_reason",
}
IOS_ARTICLE_KEYS = {
    "id", "headline", "summary", "summary_bullets", "sentiment",
    "sentiment_confidence", "source_name", "source_logo_url", "published_at",
    "thumbnail_url", "article_url", "related_tickers", "ai_processed",
}
IOS_FEED_KEYS = {
    "scope", "articles", "insight", "cached", "cache_age_seconds",
    "offset", "has_more",
}


# ── key parity ────────────────────────────────────────────────────────

def test_tab_response_exposes_exactly_the_keys_ios_decodes():
    payload = UpdatesTabResponse(scope="AAPL", title="AAPL").model_dump()
    assert set(payload) == IOS_TAB_KEYS


def test_insight_card_exposes_exactly_the_keys_ios_decodes():
    payload = AIInsightCardResponse(
        scope="AAPL", headline="H", bullets=["a", "b"]
    ).model_dump()
    assert set(payload) == IOS_INSIGHT_KEYS


def test_article_exposes_exactly_the_keys_ios_decodes():
    payload = UpdatesArticleResponse(id="1", headline="H").model_dump()
    assert set(payload) == IOS_ARTICLE_KEYS


def test_feed_exposes_exactly_the_keys_ios_decodes():
    payload = UpdatesFeedResponse(scope="AAPL").model_dump()
    assert set(payload) == IOS_FEED_KEYS


@pytest.mark.parametrize("returned,limit,offset,expected", [
    (50, 50, 0, True),      # full page -> maybe more
    (49, 50, 0, False),     # short page -> provably the end
    (0, 50, 50, False),     # empty page -> the end
    (50, 50, 450, True),    # last offset the query param still accepts
    (50, 50, 451, False),   # beyond the ceiling: never advertise an unreachable page
    (50, 50, 500, False),
])
def test_has_more_is_derived_from_a_full_page_and_the_offset_ceiling(
    returned, limit, offset, expected
):
    """Mirrors the expression in endpoints/updates.py::get_updates_feed.

    `has_more` must never promise a page the `offset` Query bound (le=500) would
    reject — the client would loop on a request that 422s.
    """
    has_more = returned >= limit and (offset + limit) <= 500
    assert has_more is expected


def test_pagination_defaults_keep_an_old_client_on_page_zero():
    """`has_more` must default False and `offset` 0.

    An app build that predates pagination decodes neither field. If `has_more`
    defaulted True, a client that DID decode it would chase pages forever on a
    scope with fewer rows than one page.
    """
    feed = UpdatesFeedResponse(scope="AAPL")
    assert feed.offset == 0
    assert feed.has_more is False


def test_every_response_is_json_serializable_with_allow_nan_false():
    # FastAPI serializes with allow_nan=False. A NaN anywhere in the tree is a
    # 500 for the WHOLE screen — the failure mode already on record for the
    # asset-detail tabs.
    feed = UpdatesFeedResponse(
        scope="AAPL",
        articles=[UpdatesArticleResponse(id="1", headline="H")],
        insight=AIInsightCardResponse(scope="AAPL", headline="H", bullets=["a", "b"]),
    )
    json.dumps(feed.model_dump(), allow_nan=False)

    tabs = UpdatesTabsResponse(
        tabs=[UpdatesTabResponse(scope="AAPL", title="AAPL", change_percent=-2.14)]
    )
    json.dumps(tabs.model_dump(), allow_nan=False)


# ── worst-case cache rows through the real mapper ─────────────────────

WORST_CASE_ROWS = [
    # Every nullable column in ticker_news_cache actually NULL.
    {"id": None, "headline": None, "summary": None, "summary_bullets": None,
     "sentiment": None, "sentiment_confidence": None, "source_name": None,
     "source_logo_url": None, "published_at": None, "thumbnail_url": None,
     "article_url": None, "related_tickers": None, "ai_processed": None},
    # Wrong types where the DB (or a future FMP change) could supply them.
    {"id": 12345, "headline": "", "summary_bullets": "not-a-list",
     "related_tickers": "AAPL", "sentiment_confidence": "high",
     "ai_processed": "yes"},
    # Empty dict — every key missing.
    {},
    # Out-of-range confidence, mixed-type arrays.
    {"id": "x", "headline": "H", "sentiment_confidence": 9999,
     "summary_bullets": ["ok", None, 5], "related_tickers": ["AAPL", None, ""],
     "ai_processed": True, "sentiment": "BULLISH"},
    {"id": "y", "headline": "H", "sentiment_confidence": -50, "ai_processed": True,
     "sentiment": "bearish"},
    # Non-finite confidence (FMP emits NaN/Infinity JSON tokens).
    {"id": "z", "headline": "H", "sentiment_confidence": float("nan")},
]


@pytest.mark.parametrize("row", WORST_CASE_ROWS)
def test_worst_case_cache_row_survives_the_mapper_and_validates(row):
    article = _to_article(row)
    UpdatesArticleResponse.model_validate(article.model_dump())
    dumped = article.model_dump()
    assert set(dumped) == IOS_ARTICLE_KEYS
    json.dumps(dumped, allow_nan=False)


@pytest.mark.parametrize("row", WORST_CASE_ROWS)
def test_mapper_never_emits_a_null_for_a_non_optional_swift_field(row):
    # `id`, `headline`, `summary_bullets`, `related_tickers`,
    # `sentiment_confidence` and `ai_processed` are NON-optional in Swift.
    # A null in any of them is an immediate decode crash.
    a = _to_article(row).model_dump()
    assert isinstance(a["id"], str)
    assert isinstance(a["headline"], str)
    assert isinstance(a["summary_bullets"], list)
    assert isinstance(a["related_tickers"], list)
    assert isinstance(a["sentiment_confidence"], int)
    assert isinstance(a["ai_processed"], bool)


@pytest.mark.parametrize("row", WORST_CASE_ROWS)
def test_confidence_is_clamped_to_the_documented_range(row):
    assert 0 <= _to_article(row).sentiment_confidence <= 100


def test_unenriched_article_reports_no_sentiment():
    # The cache normalizes un-analysed rows to 'neutral'. Forwarding that would
    # render a confident badge no model produced, so the mapper blanks it.
    a = _to_article({"id": "1", "headline": "H", "sentiment": "neutral",
                     "ai_processed": False})
    assert a.sentiment is None
    assert a.ai_processed is False


def test_enriched_article_reports_lowercase_sentiment():
    # Article sentiment stays in the LOWERCASE domain (matching ticker_news_cache
    # and the five other screens); only the CARD uses the Capitalized domain.
    a = _to_article({"id": "1", "headline": "H", "sentiment": "Bullish",
                     "ai_processed": True})
    assert a.sentiment == "bullish"


def test_bullets_and_tickers_are_stripped_of_non_strings():
    a = _to_article({
        "id": "1", "headline": "H", "ai_processed": True,
        "summary_bullets": ["good", None, 7, "also good"],
        "related_tickers": ["AAPL", None, "", "MSFT"],
    })
    assert a.summary_bullets == ["good", "also good"]
    assert a.related_tickers == ["AAPL", "MSFT"]


# ── card domain ───────────────────────────────────────────────────────

@pytest.mark.parametrize("sentiment", ["Bullish", "Bearish", "Neutral"])
def test_card_sentiment_uses_the_capitalized_domain(sentiment):
    # iOS MarketSentiment raw values are Capitalized. The ARTICLE domain
    # (positive/negative/neutral) is deliberately different — do not unify them
    # without changing both Swift enums.
    card = AIInsightCardResponse(
        scope="AAPL", headline="H", bullets=["a", "b"], sentiment=sentiment
    )
    assert card.sentiment == sentiment


def test_card_defaults_are_safe_for_a_missing_field():
    card = AIInsightCardResponse(scope="AAPL", headline="H")
    assert card.bullets == []
    assert card.sentiment == "Neutral"
    assert card.ai_generated is True
    assert card.is_stale is False
    assert card.refreshing is False
    assert "AI Summary" in card.badge


def test_feed_insight_is_optional():
    # A scope with no card yet must not fail the whole feed.
    feed = UpdatesFeedResponse(scope="AAPL", articles=[], insight=None)
    assert feed.insight is None
    json.dumps(feed.model_dump(), allow_nan=False)


def test_enrich_response_shape():
    r = EnrichUpdatesNewsResponse(
        scope="AAPL", articles=[UpdatesArticleResponse(id="1", headline="H")]
    )
    assert set(r.model_dump()) == {"scope", "articles"}


# ── scope validation ──────────────────────────────────────────────────

@pytest.mark.parametrize("scope", [
    MARKET_SCOPE, "AAPL", "BRK.B", "BTCUSD", "^GSPC", "RDS-A", "GCUSD", "A",
])
def test_valid_scopes_are_accepted(scope):
    assert _valid_scope(scope)


@pytest.mark.parametrize("scope", [
    "", "../etc/passwd", "AAPL; DROP TABLE", "A" * 33, "AA PL", "AAPL/../X",
    "<script>", "AAPL%00", "AA\nPL",
])
def test_malformed_scopes_are_rejected(scope):
    assert not _valid_scope(scope)


def test_tab_change_percent_is_optional_not_zero():
    # An unavailable quote must render as "no change shown", never as a
    # fabricated 0.0% (which reads as "flat" — a claim we cannot make).
    tab = UpdatesTabResponse(scope="AAPL", title="AAPL", change_percent=None)
    assert tab.change_percent is None


def test_the_market_tab_carries_no_change_percent():
    """The Market pill is GENERAL market news — `news/general-latest` plus
    SPY/QQQ/DIA/^GSPC/^IXIC coverage — so no single instrument's move describes
    it. It previously showed the S&P's %, which claims a precision the tab does
    not have while sitting inches from ticker pills where the % means exactly
    one thing. iOS hides the label when this is null (`formattedChange`
    returns nil), so nulling it here is the whole fix.
    """
    tab = UpdatesTabResponse(
        scope="__MARKET__", title="Market", company_name="S&P 500",
        change_percent=None, is_market_tab=True,
    )
    assert tab.change_percent is None
    assert tab.is_market_tab is True
    # The field must stay in the contract — iOS decodes it for ticker pills.
    assert "change_percent" in tab.model_dump()


def test_non_finite_change_percent_would_break_json_and_must_be_filtered_upstream():
    # Documents WHY endpoints/updates.py::_change filters NaN/Inf before
    # constructing the model: Pydantic accepts them, FastAPI's serializer
    # does not.
    tab = UpdatesTabResponse(scope="X", title="X", change_percent=float("nan"))
    with pytest.raises(ValueError):
        json.dumps(tab.model_dump(), allow_nan=False)
