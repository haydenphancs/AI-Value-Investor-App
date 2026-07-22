"""Cross-screen news parity — the user's hard invariant.

A given ticker's news must render IDENTICALLY across the Updates screen and the
ticker / ETF / crypto detail News tab. Both surfaces read the SAME
`ticker_news_cache` rows (via `_format_single_row`) but through two different
final mappers:

    Updates screen   →  updates.py::_to_article        → UpdatesArticleResponse
    detail News tab  →  news.py::news_article_from_row → TickerNewsArticleResponse

Historically these disagreed on un-enriched articles — Updates hid the badge,
the detail tab showed a confident 'Neutral' — and TWO green test suites locked
in the contradiction. This test feeds one row to BOTH mappers and asserts they
render the same LOGICAL sentiment (and, crucially, agree on whether a badge is
shown at all). It is the regression guard for that class of divergence.
"""

import pytest

from app.api.v1.endpoints.updates import _to_article
from app.schemas.news import SentimentValue, news_article_from_row


def _row(**overrides):
    """A row shaped like `_format_single_row` emits (both mappers consume it)."""
    row = {
        "id": "6f1c9d64-0000-4000-8000-000000000001",
        "headline": "Chipmaker beats on revenue",
        "summary": "Full article text.",
        "summary_bullets": ["Revenue up 12%", "Guidance raised"],
        "sentiment": "bullish",          # lowercase, as _format_single_row emits
        "sentiment_confidence": 82,
        "source_name": "Reuters",
        "source_logo_url": "https://img.example/logo.png",
        "published_at": "2026-07-19T12:00:00+00:00",
        "thumbnail_url": "https://img.example/1.png",
        "article_url": "https://example.com/1",
        "related_tickers": ["NVDA", "AMD"],
        "ai_processed": True,
    }
    row.update(overrides)
    return row


def _updates_polarity(article):
    """Canonical polarity for the Updates wire value (lowercase str | None)."""
    return {None: None, "bullish": "up", "bearish": "down", "neutral": "flat"}[
        article.sentiment
    ]


def _detail_polarity(article):
    """Canonical polarity for the detail wire value (SentimentValue | None)."""
    return {
        None: None,
        SentimentValue.POSITIVE: "up",
        SentimentValue.NEGATIVE: "down",
        SentimentValue.NEUTRAL: "flat",
    }[article.sentiment]


@pytest.mark.parametrize(
    "ai_processed,sentiment,expected",
    [
        # Enriched rows: same polarity on both screens.
        (True, "bullish", "up"),
        (True, "bearish", "down"),
        (True, "neutral", "flat"),
        # Un-enriched rows: NO badge on EITHER screen (the invariant that broke).
        (False, "neutral", None),
        (False, "bullish", None),
        (False, "", None),
        (False, None, None),
        # Enriched but the model returned no sentiment: hidden on both.
        (True, None, None),
        # Enriched but an EMPTY sentiment string — both mappers must still hide it
        # (this is the asymmetry the two mappers previously disagreed on).
        (True, "", None),
    ],
)
def test_both_screens_render_the_same_sentiment(ai_processed, sentiment, expected):
    row = _row(ai_processed=ai_processed, sentiment=sentiment)
    updates = _to_article(row)
    detail = news_article_from_row(row)

    up = _updates_polarity(updates)
    dp = _detail_polarity(detail)

    # Same logical sentiment...
    assert up == dp == expected, (
        f"divergence: updates={updates.sentiment!r} detail={detail.sentiment!r} "
        f"(ai_processed={ai_processed}, stored={sentiment!r})"
    )
    # ...and they agree on whether a badge is shown at all.
    assert (updates.sentiment is None) == (detail.sentiment is None)


def test_shared_fields_match_across_screens():
    """The rest of the rendered row must also match: same id, headline, bullets,
    related tickers, source logo, article url."""
    row = _row()
    u = _to_article(row)
    d = news_article_from_row(row)
    assert u.id == d.id
    assert u.headline == d.headline
    assert list(u.summary_bullets) == list(d.summary_bullets)
    assert list(u.related_tickers) == list(d.related_tickers)
    assert u.article_url == d.article_url
    assert u.source_logo_url == d.source_logo_url == "https://img.example/logo.png"
    assert u.sentiment_confidence == d.sentiment_confidence


def test_both_mappers_survive_non_string_upstream_fields():
    """One malformed upstream field must not 500 the whole feed.

    On a cold FMP miss the raw row reaches ``_to_article`` before any DB
    round-trip, and FMP occasionally returns a non-string where a string is
    expected (e.g. ``image`` as a JSON array, a numeric ``publisher``). Handing
    that straight to a Pydantic v2 ``Optional[str]`` raises ``ValidationError``
    — a 500 for the WHOLE Updates feed over one degraded article — while the
    SAME shared row still renders on the detail News tab (its mapper coerces via
    ``_opt_str``). Both mappers must coerce, so the two screens stay in parity
    and neither 500s. Regression guard for the Updates-side ValidationError.
    """
    row = _row(
        thumbnail_url=["https://img.example/1.png"],   # list, not str
        article_url={"u": "https://example.com/1"},     # dict
        source_name=123,                                # int
        summary=["some", "text"],                       # list
        source_logo_url=42,                             # int
    )
    # Neither raises (before the fix, _to_article raised ValidationError here).
    u = _to_article(row)
    d = news_article_from_row(row)

    # Every Optional[str] field is coerced to a str (or None) on BOTH screens —
    # never a list/dict/int that Pydantic would reject.
    for art in (u, d):
        for field in (
            "summary", "source_name", "source_logo_url",
            "published_at", "thumbnail_url", "article_url",
        ):
            v = getattr(art, field)
            assert v is None or isinstance(v, str), f"{field}={v!r} not coerced on {art!r}"

    # A genuinely absent date coerces to None on both, not a crash.
    none_date = _to_article(_row(published_at=None))
    assert none_date.published_at is None
    assert news_article_from_row(_row(published_at=None)).published_at is None
