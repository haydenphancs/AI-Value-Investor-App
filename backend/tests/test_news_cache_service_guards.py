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


# A NAIVE FMP stamp is a wall clock in America/New_York, not UTC — verified live:
# at 09:17 ET (13:17 UTC) the newest publishedDate values read "09:09:00". Because
# `published_at` is `timestamptz`, storing the raw string made Postgres apply the
# SESSION zone (UTC on Railway) and backdate every row by the ET offset. So this
# helper must now NORMALISE, not merely validate. The offset is seasonal — 4h in
# EDT, 5h in EST — which is why these cases pin both.
@pytest.mark.parametrize(
    "raw,expected",
    [
        # Naive FMP space form, EDT (July): 18:00 ET == 22:00 UTC.
        ("2026-07-20 18:00:00", "2026-07-20T22:00:00+00:00"),
        # Naive, EST (January): the SAME wall clock is 23:00 UTC. A fixed offset
        # would get exactly one of these two rows right.
        ("2026-01-20 18:00:00", "2026-01-20T23:00:00+00:00"),
        # Already offset-aware values are OUR OWN writes — converted, never
        # re-interpreted, so the helper is idempotent over a mixed-provenance list.
        ("2026-07-20T18:00:00+00:00", "2026-07-20T18:00:00+00:00"),
        ("2026-07-20T18:00:00Z", "2026-07-20T18:00:00+00:00"),
        ("2026-07-20T18:00:00-04:00", "2026-07-20T22:00:00+00:00"),
        # Date-only is midnight ET, i.e. 04:00 UTC in EDT.
        ("2026-07-20", "2026-07-20T04:00:00+00:00"),
        ("  2026-07-20 18:00:00  ", "2026-07-20T22:00:00+00:00"),  # trimmed
        # Unchanged intent: an unusable value must become None rather than abort
        # the whole 50-row batch upsert.
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


def test_sanitize_published_at_is_idempotent():
    """Re-normalising an already-normalised value must be a no-op.

    `_compute_news_score` sees a MIXED list — raw FMP rows and rows read back from
    Supabase — so a helper that shifted an aware value by the ET offset on every
    pass would walk timestamps backwards each refresh.
    """
    once = _sanitize_published_at("2026-07-20 18:00:00")
    assert _sanitize_published_at(once) == once


def test_naive_stamps_are_not_read_as_utc():
    """The anti-vacuity control for the whole fix.

    If someone reverts the normalisation to `return s`, this is the assertion that
    fails: the stored instant would equal the ET wall clock read as UTC.
    """
    assert _sanitize_published_at("2026-07-20 18:00:00") != "2026-07-20T18:00:00+00:00"


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
    # Normalised to a true UTC instant (18:00 EDT == 22:00 UTC), not echoed back.
    assert by_headline["Good A"] == "2026-07-20T22:00:00+00:00"
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


# ── Proactive window enrichment (shared by the sweeper + pre-warmer) ───


def test_enrichable_ids_selects_only_fresh_real_ids():
    """The pure filter keeps only un-enriched rows with a real DB id — skipping
    already-enriched rows, client-side placeholders, and empty ids."""
    svc = object.__new__(NewsCacheService)
    articles = [
        {"id": "a1", "ai_processed": False},
        {"id": "a2", "ai_processed": True},          # already enriched → skip
        {"id": "temp_x", "ai_processed": False},      # placeholder → skip
        {"id": "raw_y", "ai_processed": False},       # placeholder → skip
        {"id": "sample_z", "ai_processed": False},    # placeholder → skip
        {"id": "unknown_1", "ai_processed": False},   # placeholder → skip
        {"id": "", "ai_processed": False},            # empty id → skip
        {"id": "a3", "ai_processed": False},
    ]
    assert svc._enrichable_ids(articles, cap=25) == ["a1", "a3"]


def test_enrichable_ids_respects_cap():
    svc = object.__new__(NewsCacheService)
    articles = [{"id": f"a{i}", "ai_processed": False} for i in range(30)]
    # Only the freshest `cap` articles are considered.
    assert svc._enrichable_ids(articles, cap=25) == [f"a{i}" for i in range(25)]
    assert svc._enrichable_ids(articles, cap=5) == [f"a{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_enrich_window_calls_enrich_articles_with_filtered_ids():
    svc = object.__new__(NewsCacheService)
    captured = {}

    async def _fake_enrich(scope, ids):
        captured["scope"] = scope
        captured["ids"] = ids
        return [{"id": i, "ai_processed": True} for i in ids]

    svc.enrich_articles = _fake_enrich
    articles = [
        {"id": "a1", "ai_processed": False},
        {"id": "a2", "ai_processed": True},          # skip
        {"id": "temp_x", "ai_processed": False},      # skip
        {"id": "a3", "ai_processed": False},
    ]
    n = await svc.enrich_window("AAPL", articles, cap=25)
    assert captured["scope"] == "AAPL"
    assert captured["ids"] == ["a1", "a3"]
    assert n == 2


@pytest.mark.asyncio
async def test_enrich_window_never_raises_and_no_ops_when_nothing_fresh():
    svc = object.__new__(NewsCacheService)

    async def _boom(scope, ids):
        raise RuntimeError("gemini down")

    svc.enrich_articles = _boom
    # A failing enrichment must degrade to 0, never raise into the caller.
    assert await svc.enrich_window("AAPL", [{"id": "a1", "ai_processed": False}], cap=25) == 0

    called = {"n": 0}

    async def _count(scope, ids):
        called["n"] += 1
        return []

    svc.enrich_articles = _count
    # All rows already enriched → no ids → NO enrich_articles call at all.
    assert await svc.enrich_window("AAPL", [{"id": "a1", "ai_processed": True}], cap=25) == 0
    assert called["n"] == 0
