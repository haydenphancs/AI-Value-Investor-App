"""Guards for `news_cache_service` degradation paths.

Covers the outlier fixes from the news deep-check:
  * F6  — a single bad `published_at` must not abort the whole batch upsert.
  * F8  — `get_cached_bulk` must add the stable `id` tiebreak.
  * F9  — a crypto scope must refresh through FMP's crypto news source.
  * F10 — `sentiment_confidence` is clamped to [0, 100] at the source.
Plus unit coverage of the pure helpers that back them.
"""

import asyncio
import types

import pytest

from app.services.news_cache_service import (
    NewsCacheService,
    _clamp_confidence,
    _sanitize_published_at,
    is_crypto_scope,
)


# ── Pure helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-07-20 18:00:00", "2026-07-20 18:00:00"),  # FMP space form
        ("2026-07-20T18:00:00+00:00", "2026-07-20T18:00:00+00:00"),  # ISO
        ("2026-07-20T18:00:00Z", "2026-07-20T18:00:00Z"),  # ISO w/ Z
        ("2026-07-20", "2026-07-20"),  # date-only
        ("  2026-07-20 18:00:00  ", "2026-07-20 18:00:00"),  # trimmed
        ("", None),  # empty → would abort a real upsert
        ("   ", None),  # whitespace only
        ("not-a-date", None),  # garbage
        ("2026-13-45", None),  # invalid calendar values
        (None, None),  # missing
        (1721500000, None),  # non-string (epoch int)
        ({"d": 1}, None),  # non-string (dict)
    ],
)
def test_sanitize_published_at(raw, expected):
    assert _sanitize_published_at(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (50, 50), (0, 0), (100, 100),
        (150, 100), (400, 100), (-5, 0),  # out of range → clamped
        ("82", 82), (82.7, 82),           # coerced
        ("n/a", 0), (None, 0), ({}, 0),   # garbage → 0
    ],
)
def test_clamp_confidence(raw, expected):
    assert _clamp_confidence(raw) == expected


@pytest.mark.parametrize(
    "scope,expected",
    [
        ("BTCUSD", True), ("ETHUSDT", True), ("btcusd", True),
        ("AAPL", False), ("SPY", False), ("^GSPC", False),
        ("GCUSD", False), ("SIUSD", False),  # commodities, not crypto
        ("USD", False), ("", False), (None, False),
    ],
)
def test_is_crypto_scope(scope, expected):
    assert is_crypto_scope(scope) is expected


# ── Fakes ─────────────────────────────────────────────────────────────


class _FakeQuery:
    def __init__(self, sup):
        self.sup = sup

    def select(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def range(self, *a, **k): return self
    def update(self, *a, **k): return self

    def order(self, col, **k):
        self.sup.orders.append(col)
        return self

    def upsert(self, rows, **k):
        self.sup.upserted = rows
        # Emulate Postgres RETURNING: echo each row with a generated id.
        self.sup._data = [{**r, "id": f"db-{i}"} for i, r in enumerate(rows)]
        return self

    def execute(self):
        return types.SimpleNamespace(data=self.sup._data)


class _FakeSupabase:
    def __init__(self, rows=None):
        self.orders = []
        self.upserted = None
        self._data = [] if rows is None else rows

    def table(self, _name):
        return _FakeQuery(self)


class _StubService(NewsCacheService):
    """NewsCacheService without real clients (bypasses __init__)."""

    def __init__(self):
        self.supabase = _FakeSupabase()
        self.gemini = None
        self._inflight = {}
        self.fmp_calls = []
        outer = self

        class _FMP:
            async def get_stock_news(self, ticker=None, limit=10, from_date=None,
                                     to_date=None, page=0):
                outer.fmp_calls.append(("stock", ticker))
                return [_raw("s1", "Stock story")]

            async def get_crypto_news(self, ticker=None, limit=10, page=0):
                outer.fmp_calls.append(("crypto", ticker))
                return [_raw("c1", "Crypto story")]

        self.fmp = _FMP()


def _raw(url, title, when="2026-07-20 18:00:00", symbol=None):
    return {
        "url": url, "title": title, "publishedDate": when,
        "symbol": symbol, "publisher": "Test", "text": "body", "image": None,
    }


# ── F6: one bad date must not poison the batch ────────────────────────


def test_bad_published_at_does_not_poison_the_batch():
    svc = _StubService()
    raw = [
        _raw("u1", "Good A", when="2026-07-20 18:00:00"),
        _raw("u2", "Bad empty", when=""),        # would abort a real upsert
        _raw("u3", "Garbage", when="not-a-date"),
        _raw("u4", "None date", when=None),
    ]
    out = svc._build_and_cache_rows("AAPL", raw, 50, "AAPL", "test")

    # Every article kept a REAL db id — none degraded to a temp_ placeholder.
    assert out and all(not a["id"].startswith("temp_") for a in out)

    # Every upserted row carries a Postgres-valid timestamp or None.
    by_headline = {r["headline"]: r["published_at"] for r in svc.supabase.upserted}
    assert by_headline["Good A"] == "2026-07-20 18:00:00"
    assert by_headline["Bad empty"] is None
    assert by_headline["Garbage"] is None
    assert by_headline["None date"] is None


# ── F8: get_cached_bulk stable id tiebreak ────────────────────────────


def test_get_cached_bulk_orders_by_id_tiebreak():
    svc = _StubService()  # fake supabase returns [] → single page
    svc.get_cached_bulk(["AAPL", "MSFT"], per_scope_limit=25)
    # published_at first, then id — without the id tiebreak, rows tying on
    # published_at across a page boundary get skipped/duplicated.
    assert "published_at" in svc.supabase.orders
    assert "id" in svc.supabase.orders
    assert svc.supabase.orders.index("published_at") < svc.supabase.orders.index("id")


# ── F9: crypto scope refreshes via the crypto source ──────────────────


def test_refresh_scope_news_uses_crypto_source_for_crypto_scope():
    svc = _StubService()
    written = asyncio.run(svc.refresh_scope_news("BTCUSD"))
    assert ("crypto", "BTCUSD") in svc.fmp_calls
    assert ("stock", "BTCUSD") not in svc.fmp_calls
    assert written == 1


def test_refresh_scope_news_uses_stock_source_for_equity_scope():
    svc = _StubService()
    asyncio.run(svc.refresh_scope_news("AAPL"))
    assert ("stock", "AAPL") in svc.fmp_calls
    assert not any(kind == "crypto" for kind, _ in svc.fmp_calls)


# ── F10: confidence clamped through the enrichment map ────────────────


def test_map_enrichments_clamps_confidence():
    parsed = [
        {"index": 0, "bullets": ["a", "b"], "sentiment": "bullish", "confidence": 400},
        {"index": 1, "bullets": ["c", "d"], "sentiment": "neutral", "confidence": -20},
    ]
    mapped = NewsCacheService._map_enrichments(parsed, 2)
    assert mapped[0]["confidence"] == 100
    assert mapped[1]["confidence"] == 0
