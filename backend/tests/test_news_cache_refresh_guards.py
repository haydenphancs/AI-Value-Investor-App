"""
Sweeper-refresh guards for the SHARED `ticker_news_cache`.

This table is read by the Updates screen AND by the Ticker / Crypto / Index /
Commodity detail News tabs AND by SentimentService. The insight sweeper rewrites
it every 15 minutes for ~200 scopes, so a mistake in the refresh row shape is a
silent, cross-screen data-loss event. Three such regressions shipped and are
pinned here.
"""

import inspect

import pytest

from app.services.news_cache_service import (
    CACHE_TTL_HOURS,
    MARKET_INDEX_SYMBOLS,
    REFRESH_LOOKBACK_HOURS,
    NewsCacheService,
)


class _Stub(NewsCacheService):
    """No network/DB clients; captures what would be upserted."""

    def __init__(self):
        self.rows = None
        self.supabase = self
        self.gemini = None
        self.fmp = None
        self._inflight = {}

    # Supabase surface used by _build_and_cache_rows
    def table(self, _n): return self
    def upsert(self, rows, **_k):
        self.rows = rows
        return self
    def execute(self):
        return type("R", (), {"data": []})()


def _raw(url="https://x/1", symbol="AAPL"):
    return {
        "url": url, "title": "T", "text": "body", "symbol": symbol,
        "publishedDate": "2026-07-20 12:00:00", "publisher": "P", "image": None,
    }


def _row(ingest_only):
    svc = _Stub()
    svc._build_and_cache_rows(
        cache_key="AAPL", raw_articles=[_raw()], limit=10,
        fallback_ticker="AAPL", label="test", ingest_only=ingest_only,
    )
    assert svc.rows, "nothing upserted"
    return svc.rows[0]


# ── ingest_only must not clobber anything enrichment owns ─────────────

AI_OWNED = ["summary_bullets", "sentiment", "sentiment_confidence", "ai_processed", "ai_model"]


@pytest.mark.parametrize("col", AI_OWNED)
def test_refresh_never_rewrites_an_ai_column(col):
    """PostgREST merge-duplicates issues DO UPDATE SET for every key present.

    Including these on a refresh reset enrichment users already paid Gemini for,
    every 15 minutes, on a cache five screens share.
    """
    assert col not in _row(ingest_only=True)
    assert col in _row(ingest_only=False)      # a first fetch DOES initialise them


def test_refresh_never_rewrites_related_tickers():
    """`related_tickers` is enrichment-owned once Gemini has merged into it.

    FMP's raw `symbol` list does not contain the extra symbols Gemini extracts,
    so re-writing it on every refresh stripped the related-ticker chips
    permanently — `ai_processed` stays true, so nothing ever re-enriches.
    """
    assert "related_tickers" not in _row(ingest_only=True)
    assert _row(ingest_only=False)["related_tickers"] == ["AAPL"]


def test_refresh_never_rewrites_cached_at():
    """SentimentService derives a 4-hour staleness check from max(cached_at).

    Re-stamping it every 15 minutes meant that check never tripped and the
    14-day sentiment corpus was never rebuilt.
    """
    assert "cached_at" not in _row(ingest_only=True)
    assert "cached_at" in _row(ingest_only=False)


def test_refresh_DOES_restamp_expires_at():
    # This is the one field a refresh must write — it is what keeps an
    # already-cached article alive instead of aging out of the 6h window.
    assert "expires_at" in _row(ingest_only=True)


# ── the refresh window must not narrow the cache ──────────────────────

def test_refresh_lookback_covers_the_cold_fetch_span():
    """A refresh narrower than the cold fetch decays the cache to today-only.

    At 6 hours, `from_date` resolved to TODAY, so the refresh re-stamped only
    today's articles while every older row aged out at its 6h expires_at. Since
    `_get_cached` filters on expires_at and `get_ticker_news` short-circuits on
    any non-empty result, the full multi-day fetch never ran again — the Ticker
    Detail News tab collapsed from ~50 articles to the handful published today.
    """
    assert REFRESH_LOOKBACK_HOURS >= 72, "must span a cold fetch (~3-4 days)"
    assert REFRESH_LOOKBACK_HOURS > CACHE_TTL_HOURS


def test_sweeper_does_not_pass_a_narrower_window_or_limit():
    from app.services import updates_insight_sweeper as sweeper
    src = inspect.getsource(sweeper)
    call = src.split("refresh_scope_news(")[1].split(")")[0]
    assert "lookback_hours" not in call, "sweeper must not narrow the refresh window"
    assert "limit=" not in call, "a smaller limit shrinks the cached set over time"


# ── market corpus composition ─────────────────────────────────────────

def test_market_index_basket_covers_sp500_and_nasdaq():
    syms = MARKET_INDEX_SYMBOLS.upper()
    assert "SPY" in syms and "^GSPC" in syms
    assert "QQQ" in syms and "^IXIC" in syms
