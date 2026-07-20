"""Schema-parity + degradation guards for the four asset news feeds.

`GET /{symbol}/news` and `POST /{symbol}/news/enrich` on stocks, crypto,
indices and commodities now declare `response_model`s, which means a row the
model rejects becomes a 500 for the WHOLE feed instead of one odd article.
Every column these rows come from (`summary_bullets`, `related_tickers`,
`sentiment_confidence`, `ai_processed`) is NULLable, and FMP can return an
article with no title — so the mapping in `app/schemas/news.py` has to absorb
all of that. These tests pin that, plus the exact key set the iOS
`StockNewsArticle` decoder expects.
"""

import pytest

from app.schemas.news import (
    MAX_ENRICH_ARTICLE_IDS,
    EnrichNewsResponse,
    SentimentValue,
    TickerNewsFeedResponse,
    news_article_from_row,
    news_articles_from_rows,
    news_feed_from_payload,
    sanitize_article_ids,
)


# Keys the iOS decoders read (StockRepository.swift: StockNewsArticle
# CodingKeys + TickerNewsFeedResponse + EnrichStockNewsResponse). A silent
# rename here ships as a decode crash.
_IOS_ARTICLE_KEYS = {
    "id",
    "headline",
    "summary",
    "summary_bullets",
    "sentiment",
    "sentiment_confidence",
    "source_name",
    "source_logo_url",
    "published_at",
    "thumbnail_url",
    "article_url",
    "related_tickers",
    "ai_processed",
}


def _cache_row(**overrides):
    """A well-formed `ticker_news_cache` row as `_format_single_row` emits it."""
    row = {
        "id": "6f1c9d64-0000-4000-8000-000000000001",
        "headline": "Chipmaker beats on revenue",
        "summary": "Full article text.",
        "summary_bullets": ["Revenue up 12%", "Guidance raised"],
        "sentiment": "bullish",
        "sentiment_confidence": 82,
        "source_name": "Reuters",
        "source_logo_url": None,
        "published_at": "2026-07-19T12:00:00+00:00",
        "thumbnail_url": "https://img.example/1.png",
        "article_url": "https://example.com/1",
        "related_tickers": ["NVDA", "AMD"],
        "ai_processed": True,
    }
    row.update(overrides)
    return row


# ── Contract shape ────────────────────────────────────────────────────


def test_article_exposes_exactly_the_keys_ios_decodes():
    article = news_article_from_row(_cache_row())
    assert set(article.model_dump().keys()) == _IOS_ARTICLE_KEYS


def test_feed_envelope_keys_match_ios():
    feed = news_feed_from_payload(
        {"articles": [_cache_row()], "ticker": "NVDA", "cached": True,
         "cache_age_seconds": 42},
        ticker="NVDA",
    )
    assert set(feed.model_dump().keys()) == {
        "articles", "ticker", "cached", "cache_age_seconds"
    }
    assert feed.ticker == "NVDA"
    assert feed.cached is True
    assert feed.cache_age_seconds == 42


def test_enrich_response_keys_match_ios():
    body = EnrichNewsResponse(
        ticker="NVDA", articles=news_articles_from_rows([_cache_row()])
    ).model_dump()
    assert set(body.keys()) == {"ticker", "articles"}
    assert set(body["articles"][0].keys()) == _IOS_ARTICLE_KEYS


# ── Happy path is untouched ───────────────────────────────────────────


def test_happy_row_round_trips_unchanged():
    row = _cache_row()
    article = news_article_from_row(row)
    assert article.id == row["id"]
    assert article.headline == row["headline"]
    assert article.summary == row["summary"]
    assert article.summary_bullets == row["summary_bullets"]
    assert article.sentiment_confidence == 82
    assert article.related_tickers == ["NVDA", "AMD"]
    assert article.ai_processed is True
    assert article.published_at == row["published_at"]


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("bullish", SentimentValue.POSITIVE),
        ("bearish", SentimentValue.NEGATIVE),
        ("neutral", SentimentValue.NEUTRAL),
        # Un-enriched rows must keep rendering the Neutral badge iOS shows
        # today — the cache normalizes them to 'neutral', not to null.
        ("", SentimentValue.NEUTRAL),
        (None, None),
        ("wildly off-schema", SentimentValue.NEUTRAL),
    ],
)
def test_sentiment_coercion(stored, expected):
    assert news_article_from_row(_cache_row(sentiment=stored)).sentiment == expected


# ── Degraded rows must NOT 500 the feed ───────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [
        # Every NULLable column at once (fresh DB row, nothing enriched yet).
        {"summary_bullets": None, "related_tickers": None,
         "sentiment_confidence": None, "ai_processed": None, "sentiment": None,
         "summary": None},
        # FMP article with no title — `headline` is REQUIRED on the wire.
        {"headline": None},
        {},  # baseline
        # jsonb columns holding the wrong type entirely.
        {"summary_bullets": {"oops": 1}, "related_tickers": "NVDA"},
        # Non-string members inside the jsonb arrays.
        {"summary_bullets": ["ok", 5, None], "related_tickers": ["NVDA", 7, ""]},
        # Confidence arriving as a float / string / garbage.
        {"sentiment_confidence": 82.7},
        {"sentiment_confidence": "82"},
        {"sentiment_confidence": "n/a"},
        # Out-of-range confidence from a mis-behaving model.
        {"sentiment_confidence": 400},
        {"sentiment_confidence": -5},
        # Non-string url/id values.
        {"id": 123, "thumbnail_url": 42},
        # Missing keys entirely.
        {"id": None, "headline": None, "summary_bullets": None},
    ],
)
def test_degraded_rows_still_validate(overrides):
    article = news_article_from_row(_cache_row(**overrides))
    assert isinstance(article.id, str)
    assert isinstance(article.headline, str)
    assert all(isinstance(b, str) for b in article.summary_bullets)
    assert all(isinstance(t, str) and t for t in article.related_tickers)
    assert 0 <= article.sentiment_confidence <= 100
    assert isinstance(article.ai_processed, bool)


def test_empty_row_validates():
    article = news_article_from_row({})
    assert article.id == ""
    assert article.headline == ""
    assert article.summary_bullets == []
    assert article.ai_processed is False


def test_non_dict_rows_are_skipped_not_fatal():
    rows = [_cache_row(), None, "junk", 7, {"headline": "bare"}]
    assert len(news_articles_from_rows(rows)) == 2
    assert news_articles_from_rows(None) == []
    assert news_articles_from_rows("nope") == []


# ── Service envelope guards ───────────────────────────────────────────


def test_malformed_envelope_falls_back_to_endpoint_key():
    feed = news_feed_from_payload(None, ticker="COMMODITY_GC")
    assert isinstance(feed, TickerNewsFeedResponse)
    assert feed.ticker == "COMMODITY_GC"
    assert feed.articles == []
    assert feed.cached is False
    assert feed.cache_age_seconds is None


def test_envelope_cache_age_coercion():
    assert news_feed_from_payload(
        {"cache_age_seconds": "300"}, ticker="X"
    ).cache_age_seconds == 300
    assert news_feed_from_payload(
        {"cache_age_seconds": "soon"}, ticker="X"
    ).cache_age_seconds is None
    # The fallback path legitimately reports an unknown age.
    assert news_feed_from_payload(
        {"cache_age_seconds": None}, ticker="X"
    ).cache_age_seconds is None


def test_envelope_articles_wrong_type_degrades_to_empty():
    feed = news_feed_from_payload({"articles": {"a": 1}, "ticker": "X"}, ticker="X")
    assert feed.articles == []


# ── Enrichment id sanitation ──────────────────────────────────────────


def test_placeholder_ids_are_dropped():
    # temp_/raw_ are handed out by the cache service itself when no DB id
    # exists; sample_ comes from iOS mock data. None is a row key.
    assert sanitize_article_ids(["temp_0", "raw_3", "sample_1"]) == []


def test_ids_dedup_and_drop_non_strings():
    assert sanitize_article_ids(["a", "a", "b", "", None, 7, {"id": "c"}]) == ["a", "b"]


def test_ids_non_list_is_empty():
    assert sanitize_article_ids("abc") == []
    assert sanitize_article_ids(None) == []


def test_id_cap_is_a_real_bound():
    # The endpoints reject anything above this; keep the constant honest so a
    # single request can't fan out into an unbounded Gemini batch.
    assert MAX_ENRICH_ARTICLE_IDS == 50
    assert len(sanitize_article_ids([f"id-{i}" for i in range(80)])) == 80
