"""The sweeper's bulk corpus read must carry every column the subject filter reads.

`get_cached_bulk` projects an explicit column list. Until 2026-09-20 that list omitted
`related_tickers`, so inside the sweeper `article_is_about` saw `tags == []` for every
row: the lead-tag rule — the one documented as matching 608/608 live articles — never
fired there, every ticker's corpus leaned on its NAME appearing in the headline, and a
coin (whose pair symbol is never in prose) could only qualify by name. The endpoint's
`_get_cached` is `select("*")`, so the two readers disagreed about the same rows.

Also pins the pre-warmer's crypto routing: it was the one writer that fetched a coin
through `news/stock` (seen live: `news/stock?symbols=ETHUSD` at 02:22Z) while the
endpoint and the sweeper used `news/crypto` under the SAME cache key.
"""

from __future__ import annotations


import pytest

from app.services.news_cache_service import NewsCacheService

# Every column the sweeper-side consumers read off a bulk row: the subject filter
# (`article_is_about`), the window helper, the fingerprint (`corpus_article_ids`),
# the prompt builder and the sources list.
_SWEEPER_READS = {
    "id", "external_id", "headline", "summary", "published_at", "article_url",
    "source_name", "related_tickers", "ai_processed", "sentiment", "ticker",
}


class _Query:
    def __init__(self, table, rows):
        self._table = table
        self._rows = rows

    def select(self, cols):
        self._table.selected.append(cols)
        return self

    def in_(self, col, values):
        return self

    def gte(self, col, value):
        return self

    def order(self, col, **kw):
        return self

    def range(self, lo, hi):
        return self

    def execute(self):
        class _R:
            data = self._rows
        return _R()


class _Table:
    def __init__(self, rows):
        self.rows = rows
        self.selected = []

    def query(self):
        return _Query(self, self.rows)


class _Supabase:
    def __init__(self, rows):
        self._table = _Table(rows)

    def table(self, name):
        assert name == "ticker_news_cache"
        return self._table.query()

    @property
    def selected(self):
        return self._table.selected


def _service(rows):
    svc = object.__new__(NewsCacheService)
    svc.supabase = _Supabase(rows)
    return svc


def _projected_columns(svc) -> set:
    grouped = svc.get_cached_bulk(["ETHUSD", "AAPL"], 25)
    assert svc.supabase.selected, "no select() reached the fake — the read did not run"
    cols = {c.strip() for c in svc.supabase.selected[0].split(",")}
    return cols, grouped


def test_get_cached_bulk_selects_related_tickers():
    cols, _ = _projected_columns(_service([]))
    assert "related_tickers" in cols


def test_the_bulk_projection_carries_every_column_the_sweeper_reads():
    cols, _ = _projected_columns(_service([]))
    missing = _SWEEPER_READS - cols
    assert not missing, f"bulk projection is missing {sorted(missing)}"


def test_a_projected_row_reaches_the_subject_filter_with_its_tags():
    """End to end through the real bulk grouping + the real predicate: a row whose
    ONLY signal is the lead tag survives for its scope and is rejected for a peer."""
    from app.services.news_insight_service import filter_to_subject

    row = {
        "id": "r1", "ticker": "ETHUSD", "external_id": "x1",
        "headline": "Whales accumulate ahead of the upgrade",   # no symbol, no name
        "summary": None, "sentiment": None, "ai_processed": False,
        "published_at": "2026-09-20T12:00:00+00:00", "article_url": "https://e/x",
        "source_name": "e", "related_tickers": ["ETHUSD", "BTCUSD"],
    }
    _, grouped = _projected_columns(_service([row]))
    assert [r["id"] for r in grouped["ETHUSD"]] == ["r1"]
    assert filter_to_subject(grouped["ETHUSD"], "ETHUSD") == [row]
    assert filter_to_subject(grouped["ETHUSD"], "BTCUSD") == []


# ── the pre-warmer routes a coin through the crypto feed ─────────────────────

class _Rpc:
    def __init__(self, tickers):
        self._tickers = tickers

    def execute(self):
        class _R:
            data = [{"ticker": t} for t in self._tickers]
        return _R()


class _RpcSupabase:
    def __init__(self, tickers):
        self._tickers = tickers

    def rpc(self, name, params):
        assert name == "get_top_watchlist_tickers"
        return _Rpc(self._tickers)


@pytest.mark.asyncio
async def test_pre_warm_routes_crypto_through_the_crypto_feed():
    svc = object.__new__(NewsCacheService)
    svc.supabase = _RpcSupabase(["BTCUSD", "AAPL", "GCUSD", "ETHUSDT"])
    calls = []

    async def _get_ticker_news(t, limit=50, is_crypto=False, offset=0):
        calls.append((t, is_crypto))
        return {"articles": []}

    async def _get_market_news(**kw):
        return {"articles": []}

    async def _enrich_window(scope, articles, cap=0):
        return 0

    svc.get_ticker_news = _get_ticker_news
    svc.get_market_news = _get_market_news
    svc.enrich_window = _enrich_window

    # Four tickers fit one batch of five, so the inter-batch sleep never runs.
    await svc.pre_warm_popular_tickers(top_n=20)

    assert dict(calls) == {
        "BTCUSD": True,    # a coin → news/crypto, like the endpoint and the sweeper
        "AAPL": False,
        "GCUSD": False,    # a commodity pair is NOT crypto (BLOCKED_COMMODITY_SYMBOLS)
        "ETHUSDT": True,
    }


# ── completeness (2026-11-12) ─────────────────────────────────────────────────
# A failed or partial bulk read used to be indistinguishable from "no news": the loop
# logs and stops, keeping the pages read so far. Since web-only Insight cards turn an
# EMPTY corpus into a card, the answer now says whether every needed page was read.

import logging  # noqa: E402

from app.services.news_cache_service import BulkCorpus  # noqa: E402


class _PagedQuery:
    def __init__(self, svc):
        self._svc = svc
        self._lo = 0

    def select(self, cols):
        return self

    def in_(self, col, values):
        return self

    def gte(self, col, value):
        return self

    def order(self, col, **kw):
        return self

    def range(self, lo, hi):
        self._lo = lo
        return self

    def execute(self):
        self._svc.offsets.append(self._lo)
        page = self._svc.pages[min(self._lo // 1000, len(self._svc.pages) - 1)]
        if isinstance(page, BaseException):
            raise page

        class _R:
            data = page
        return _R()


class _PagedSupabase:
    """One answer per 1000-row page: a list of rows, or an exception to raise. The last
    entry repeats for every later page (the runaway case)."""

    def __init__(self, pages):
        self.pages = pages
        self.offsets = []

    def table(self, name):
        assert name == "ticker_news_cache"
        return _PagedQuery(self)


def _rows(ticker, n, start=0):
    return [{"id": f"{ticker}-{start + i}", "ticker": ticker, "external_id": f"{ticker}{start + i}",
             "headline": f"{ticker} story {start + i}", "related_tickers": [ticker],
             "published_at": "2026-11-12T12:00:00+00:00"} for i in range(n)]


def _paged(pages, max_pages=None):
    svc = object.__new__(NewsCacheService)
    svc.supabase = _PagedSupabase(pages)
    if max_pages is not None:
        svc._MAX_BULK_PAGES = max_pages
    return svc


def test_a_short_read_is_complete():
    svc = _paged([_rows("AAPL", 3)])
    got = svc.get_cached_bulk(["AAPL", "JOBY"], 25)
    assert isinstance(got, BulkCorpus) and got.complete is True
    assert [r["id"] for r in got["AAPL"]] == ["AAPL-0", "AAPL-1", "AAPL-2"]
    assert "JOBY" not in got                      # a real "no news", from a complete read


def test_an_empty_table_is_a_complete_empty_answer():
    got = _paged([[]]).get_cached_bulk(["JOBY"], 25)
    assert got == {} and got.complete is True


def test_no_scopes_is_a_complete_empty_answer():
    got = _paged([RuntimeError("never read")]).get_cached_bulk(["", ""], 25)
    assert got == {} and got.complete is True


def test_an_error_on_the_first_page_is_incomplete(caplog):
    caplog.set_level(logging.WARNING)
    got = _paged([RuntimeError("connection reset")]).get_cached_bulk(["AAPL", "JOBY"], 25)
    assert got == {} and got.complete is False
    assert "Bulk cache lookup failed" in caplog.text


def test_an_error_on_a_later_page_keeps_the_rows_but_is_incomplete():
    # Page 1 is full of AAPL (a busy scope); JOBY's rows would be on page 2, which fails.
    svc = _paged([_rows("AAPL", 1000), RuntimeError("timeout")])
    got = svc.get_cached_bulk(["AAPL", "JOBY"], 25)
    assert got.complete is False
    assert len(got["AAPL"]) == 25 and "JOBY" not in got
    assert svc.supabase.offsets == [0, 1000]


def test_every_scope_full_on_a_full_page_stops_early_and_is_complete():
    svc = _paged([_rows("AAPL", 500) + _rows("JOBY", 500), RuntimeError("never read")])
    got = svc.get_cached_bulk(["AAPL", "JOBY"], 25)
    assert got.complete is True
    assert len(got["AAPL"]) == 25 and len(got["JOBY"]) == 25
    assert svc.supabase.offsets == [0]            # page 2 was not needed


def test_the_runaway_guard_is_incomplete_and_says_so(caplog):
    caplog.set_level(logging.WARNING)
    # Every page is full of AAPL, so JOBY is never filled and no read is short.
    svc = _paged([_rows("AAPL", 1000)], max_pages=3)
    got = svc.get_cached_bulk(["AAPL", "JOBY"], 25)
    assert got.complete is False
    assert svc.supabase.offsets == [0, 1000, 2000]
    assert "3-page guard" in caplog.text


def test_a_zero_page_guard_reads_nothing_and_is_incomplete():
    svc = _paged([_rows("AAPL", 3)], max_pages=0)
    got = svc.get_cached_bulk(["AAPL"], 25)
    assert got == {} and got.complete is False and svc.supabase.offsets == []


def test_the_bulk_answer_is_still_a_plain_dict_to_every_caller():
    got = _paged([_rows("AAPL", 2)]).get_cached_bulk(["AAPL"], 25)
    assert isinstance(got, dict)
    assert got == {"AAPL": _rows("AAPL", 2)}
    assert got.get("JOBY", []) == [] and list(got) == ["AAPL"]


@pytest.mark.parametrize("flag, expected", [
    (True, True), (False, False), ("yes", False), (1, False), (None, False),
])
def test_bulk_corpus_complete_is_strictly_true(flag, expected):
    assert BulkCorpus({"A": []}, complete=flag).complete is expected
    assert BulkCorpus().complete is False         # the default is incomplete
