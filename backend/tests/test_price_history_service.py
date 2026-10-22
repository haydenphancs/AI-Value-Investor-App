"""`price_history_service` — one settled full daily history per symbol (plan B1).

Pins the tiers and the shared build (one FMP walk however many callers, a caller that
leaves never cancels it, a cancelled build never strands a joiner), the byte bound, the
overview-bundle seed, `ensure_covers` returning on the newest page while older pages keep
loading, the "a failed page is not the start of history" rule (raise, never cache), the
o/h/l ≤ 0 → None hygiene, and that a stock view plus its history pages cost ONE history
fetch per close cycle.

Hermetic: a fake FMP client serves a synthetic weekday history newest-first in
5,000-row pages, exactly like `historical-price-eod/full`.
"""

from __future__ import annotations

import asyncio
import time
import types
from datetime import date, datetime, timedelta, timezone

import pytest

from app.integrations.fmp import FMPUnavailableException
from app.services import chart_helper as CH
from app.services import price_history_service as PH
from app.services import stock_overview_service as SO

# Friday 2026-10-16 18:00 ET: the frozen close cycle every test runs in.
_CYCLE_START = datetime(2026, 10, 16, 22, 0, tzinfo=timezone.utc)
_CUTOFF = "2026-10-16"
_LAST = date(2026, 10, 16)


def _weekdays_back(n: int, end: date = _LAST) -> list:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _rows(days, *, in_progress: bool = True) -> list:
    rows = []
    for i, d in enumerate(days):
        c = 50.0 + (i % 37) * 0.5
        rows.append({"date": d.isoformat(), "open": c - 0.2, "high": c + 1.0,
                     "low": c - 1.0, "close": c, "volume": 1000.0 + i})
    if in_progress:
        # FMP's /full carries the CURRENT session's partial bar — never part of history.
        rows.append({"date": "2026-10-19", "open": 99.0, "high": 99.0, "low": 99.0,
                     "close": 99.0, "volume": 1.0})
    return rows


class _PagedFMP:
    """`get_historical_prices` over a fixed history: newest `page_rows` of [from, to],
    newest first. `fail_page` answers an FMP error body; `block_page` waits on `release`."""

    def __init__(self, n_rows: int, *, fail_page=None, block_page=None, page_rows=5000):
        self.history = _rows(_weekdays_back(n_rows))
        self.calls = []
        self.fail_page = fail_page
        self.block_page = block_page
        self.page_rows = page_rows
        self.release = asyncio.Event()

    async def get_historical_prices(self, symbol, from_date=None, to_date=None):
        self.calls.append((symbol, from_date, to_date))
        page_no = len(self.calls)
        if self.block_page == page_no:
            await self.release.wait()
        if self.fail_page == page_no:
            return {"Error Message": "Limit Reach . Please upgrade your plan"}
        rows = [r for r in self.history
                if (from_date or "") <= r["date"] <= (to_date or "9999")]
        return list(reversed(rows[-self.page_rows:]))


@pytest.fixture(autouse=True)
def _frozen_cycle(monkeypatch):
    monkeypatch.setattr(PH, "current_close_cycle_start", lambda now=None: _CYCLE_START)
    monkeypatch.setattr(SO, "current_close_cycle_start", lambda now=None: _CYCLE_START)
    monkeypatch.setattr(PH, "_service", None)
    yield


def _svc() -> PH.PriceHistoryService:
    return PH.get_price_history_service()


# ── tiers + the shared build ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_callers_share_one_walk_and_the_next_call_is_a_hit():
    fmp = _PagedFMP(1200)
    svc = _svc()
    a, b = await asyncio.gather(
        svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp),
        svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp),
    )
    assert a is b and len(a) == 1200
    assert len(fmp.calls) == 1, "two concurrent callers ran two FMP walks"
    again = await svc.full_daily(PH.ASSET_STOCK, "intc", fmp=fmp)
    assert again is a and len(fmp.calls) == 1, "a warm history went back to FMP"
    # Settled only: the in-progress 2026-10-19 bar is not history.
    assert date.fromordinal(a.dates[-1]) == _LAST
    assert a.settled_through == _CUTOFF


@pytest.mark.asyncio
async def test_a_caller_that_leaves_does_not_cancel_the_shared_build():
    fmp = _PagedFMP(800, block_page=1)
    svc = _svc()
    leaver = asyncio.create_task(svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp))
    stayer = asyncio.create_task(svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp))
    await asyncio.sleep(0.01)
    leaver.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaver
    fmp.release.set()
    cols = await asyncio.wait_for(stayer, timeout=2.0)
    assert len(cols) == 800 and len(fmp.calls) == 1


@pytest.mark.asyncio
async def test_a_cancelled_build_fails_its_joiners_instead_of_stranding_them():
    """The CancelledError arm: without it the shared future stays pending forever."""
    fmp = _PagedFMP(800, block_page=1)
    svc = _svc()
    joiner = asyncio.create_task(svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp))
    await asyncio.sleep(0.01)
    assert svc._tasks, "the build should be running as its own task"
    for task in list(svc._tasks):
        task.cancel()
    with pytest.raises(RuntimeError, match="cancelled"):
        await asyncio.wait_for(joiner, timeout=2.0)
    assert not svc._inflight, "a dead build left its key behind — no later caller could start one"


# ── the byte bound ──────────────────────────────────────────────────────────

def test_the_lru_is_bounded_by_bytes_and_evicts_the_least_recent():
    lru = PH._ByteLRU(1000)
    lru.put("a", "A", 400)
    lru.put("b", "B", 400)
    assert lru.get("a") == "A"          # touch: "b" is now the least recent
    lru.put("c", "C", 400)
    assert lru.get("b") is None and lru.get("a") == "A" and lru.get("c") == "C"
    assert lru.total_bytes == 800
    lru.put("huge", "H", 5000)          # bigger than the whole budget: served, not cached
    assert lru.get("huge") is None and lru.total_bytes == 800


def test_columns_cost_a_fraction_of_the_row_dicts():
    cols = PH.PriceColumns.from_rows(_rows(_weekdays_back(12_000)), settled_through=_CUTOFF,
                                     complete=True)
    assert len(cols) == 12_001  # 12,000 history rows + the (unfiltered) in-progress one
    # ~48 bytes a row as columns, against ~460 for FMP's row dicts.
    assert cols.nbytes < 12_001 * 60, cols.nbytes


# ── the overview-bundle seed ────────────────────────────────────────────────

def _install_bundle(monkeypatch, fmp, rows, *, stamp=_CUTOFF, ticker="INTC"):
    monkeypatch.setattr(SO, "_service", types.SimpleNamespace(fmp=fmp))
    bundle = {"stock_historical": rows, SO._SETTLED_THROUGH_KEY: stamp}
    monkeypatch.setitem(SO._cache, f"fundamentals:{ticker}", (time.time(), bundle))


@pytest.mark.asyncio
async def test_a_short_bundle_history_is_the_whole_history_at_zero_fmp_calls(monkeypatch):
    fmp = _PagedFMP(3000)
    seed = CH.settled_bars(fmp.history, _CUTOFF)
    _install_bundle(monkeypatch, fmp, seed)
    cols = await _svc().full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp)
    assert len(cols) == 3000 and cols.complete
    assert fmp.calls == [], "the bundle already held the whole history"


@pytest.mark.asyncio
async def test_a_full_page_seed_pages_only_the_older_remainder(monkeypatch):
    fmp = _PagedFMP(7200)
    # The bundle's single from-1900 call: the newest 5,000 rows, settled → 4,999 + filter.
    newest = list(reversed(fmp.history))[:5000][::-1]
    seed = CH.settled_bars(newest, _CUTOFF)
    assert len(seed) == 4999, "the settled cut drops the in-progress bar from a full page"
    _install_bundle(monkeypatch, fmp, seed)
    cols = await _svc().full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp)
    assert len(cols) == 7200 and cols.complete
    assert len(fmp.calls) == 1, fmp.calls
    symbol, from_date, to_date = fmp.calls[0]
    earliest = date.fromisoformat(seed[0]["date"])
    assert from_date == "1900-01-01"
    assert to_date == (earliest - timedelta(days=1)).isoformat()
    # Contiguous: every weekday from the first to the last row, no hole at the seam.
    days = [date.fromordinal(o) for o in cols.dates]
    assert days == _weekdays_back(7200)


@pytest.mark.asyncio
async def test_a_bundle_from_another_client_or_cycle_is_never_a_seed(monkeypatch):
    fmp = _PagedFMP(900)
    other = _PagedFMP(900)
    _install_bundle(monkeypatch, other, CH.settled_bars(other.history, _CUTOFF)[:10])
    cols = await _svc().full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp)
    assert len(cols) == 900 and len(fmp.calls) == 1

    PH._service = None
    fmp2 = _PagedFMP(900)
    _install_bundle(monkeypatch, fmp2, CH.settled_bars(fmp2.history, _CUTOFF)[:10],
                    stamp="2026-10-15")
    cols = await _svc().full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp2)
    assert len(cols) == 900 and len(fmp2.calls) == 1, "a previous cycle's bundle seeded"


# ── ensure_covers returns on the newest page ────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_covers_returns_on_page_one_while_page_two_keeps_loading():
    fmp = _PagedFMP(7000, block_page=2)
    svc = _svc()
    want = _weekdays_back(4000)[0]           # well inside the newest 5,000 rows
    partial = await asyncio.wait_for(svc.ensure_covers(fmp, "INTC", want), timeout=2.0)
    assert not partial.complete and len(partial) == 4999
    assert len(fmp.calls) == 2, "page two should be in flight behind the early return"
    assert svc._inflight, "the background walk stopped when ensure_covers returned"

    fmp.release.set()
    full = await asyncio.wait_for(svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp), timeout=2.0)
    assert full.complete and len(full) == 7000
    assert len(fmp.calls) == 2, "the full history re-fetched instead of joining the walk"


@pytest.mark.asyncio
async def test_ensure_covers_waits_for_older_pages_when_page_one_is_not_enough():
    fmp = _PagedFMP(7000)
    cols = await _svc().ensure_covers(fmp, "INTC", _weekdays_back(6500)[0])
    assert cols.complete and len(cols) == 7000


# ── a failed page is not the start of history ───────────────────────────────

@pytest.mark.asyncio
async def test_a_page_two_error_body_raises_and_is_never_cached():
    fmp = _PagedFMP(7000, fail_page=2)
    svc = _svc()
    with pytest.raises(FMPUnavailableException, match="page 2"):
        await svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp)
    assert len(svc._histories) == 0, "a truncated history was cached"
    fmp.fail_page = None
    cols = await svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp)
    assert len(cols) == 7000 and len(fmp.calls) == 4


@pytest.mark.asyncio
async def test_fetch_all_daily_raises_on_a_non_list_page_instead_of_truncating():
    fmp = _PagedFMP(7000, fail_page=2)
    with pytest.raises(FMPUnavailableException):
        await CH._fetch_all_daily(fmp, "INTC")
    # The legacy dict shape still parses, and an empty page still ends the walk.
    assert CH._daily_page_rows({"historical": [{"date": "2026-01-02", "close": 1}]}, "X", 1)
    assert CH._daily_page_rows([], "X", 1) == []
    for junk in ({"Error Message": "nope"}, "oops", None, 5):
        with pytest.raises(FMPUnavailableException):
            CH._daily_page_rows(junk, "X", 1)


@pytest.mark.asyncio
async def test_an_empty_source_raises_and_is_never_cached():
    fmp = _PagedFMP(0)
    svc = _svc()
    with pytest.raises(PH.EmptyPriceHistory):
        await svc.full_daily(PH.ASSET_STOCK, "NEWCO", fmp=fmp)
    assert len(svc._histories) == 0


@pytest.mark.asyncio
async def test_the_etf_screen_still_degrades_while_the_strict_read_raises(monkeypatch):
    from app.services import etf_service as ES

    svc = ES.ETFService.__new__(ES.ETFService)
    svc.fmp = _PagedFMP(7000, fail_page=2)
    ES._cache.clear()
    ES._inflight.clear()
    try:
        assert await svc._get_history("SPY") == [], "the screen's read must stay fail-soft"
        svc.fmp.fail_page = 4
        with pytest.raises(FMPUnavailableException):
            await svc._get_history_strict("SPY")
        assert not any(k.startswith("etf:hist:") for k in ES._cache), "a failure was cached"
    finally:
        ES._cache.clear()
        ES._inflight.clear()


# ── hygiene: missing prints are None, never fabricated ──────────────────────

def test_non_positive_open_high_low_are_none_not_zero():
    rows = CH._normalize_prices([
        {"date": "2026-10-14", "open": 0, "high": -1, "low": 0.0, "close": 10, "volume": -5},
        {"date": "2026-10-15", "open": 9.5, "high": 11, "low": 9, "close": 10, "volume": 0},
    ])
    assert rows[0]["open"] is None and rows[0]["high"] is None and rows[0]["low"] is None
    assert rows[0]["volume"] is None, "a negative volume is malformed, not a volume"
    assert rows[1]["volume"] == 0.0, "a reported zero volume (a halt) is kept"
    cols = PH.PriceColumns.from_rows(
        [{"date": "2026-10-14", "open": 0, "high": 0, "low": -3, "close": 10}],
        settled_through=None, complete=True,
    )
    out = cols.rows()[0]
    assert out["open"] is None and out["high"] is None and out["low"] is None
    assert out["volume"] is None and out["close"] == 10


def test_a_close_only_bucket_has_no_volume_not_a_zero_one():
    fred = [{"date": f"2026-10-{d:02d}", "close": 70.0 + d} for d in (12, 13, 14, 15, 16)]
    week = CH._aggregate_prices(fred, "weekly")
    assert len(week) == 1 and week[0]["volume"] is None
    assert week[0]["open"] is None and week[0]["high"] is None
    assert week[0]["close"] == 86.0 and week[0]["date"] == "2026-10-16"
    mixed = CH._aggregate_prices(
        [{"date": "2026-10-12", "close": 1, "volume": 0}, {"date": "2026-10-13", "close": 2}],
        "weekly",
    )
    assert mixed[0]["volume"] == 0.0


def test_a_capped_series_drops_its_partial_leading_bucket_and_a_listing_does_not():
    daily = [{"date": (date(2026, 1, 7) + timedelta(days=i)).isoformat(), "close": 1.0 + i,
              "volume": 1.0} for i in range(30)]          # starts on a Wednesday
    weekly = CH._aggregate_prices(daily, "weekly")
    trimmed = CH.drop_partial_leading_bucket(weekly, daily, "weekly")
    assert trimmed == weekly[1:]
    monday_start = daily[5:]                                # 2026-01-12 is a Monday
    weekly2 = CH._aggregate_prices(monday_start, "weekly")
    assert CH.drop_partial_leading_bucket(weekly2, monday_start, "weekly") == weekly2


# ── one history fetch per close cycle for a view plus its pages ─────────────

@pytest.mark.asyncio
async def test_a_stock_view_plus_history_pages_cost_one_history_fetch(monkeypatch):
    """The overview bundle already fetched the history (its ONE call). The 5Y weekly chart
    and two history pages must add none."""
    fmp = _PagedFMP(3000)
    _install_bundle(monkeypatch, fmp, CH.settled_bars(fmp.history, _CUTOFF))
    svc = _svc()
    weekly = await CH.fetch_chart_data(fmp, "INTC", "5Y", "weekly")
    assert weekly
    daily_page = await svc.history_page(PH.ASSET_STOCK, "INTC", "daily", before="2020-01-02",
                                        fmp=fmp)
    weekly_page = await svc.history_page(PH.ASSET_STOCK, "INTC", "weekly", before="2018-01-05",
                                         fmp=fmp)
    assert daily_page["bars"] and weekly_page["bars"]
    assert fmp.calls == [], f"the view's own history fetch was repeated: {fmp.calls}"


@pytest.mark.asyncio
async def test_without_a_bundle_the_chart_and_pages_share_one_walk():
    fmp = _PagedFMP(3000)
    svc = _svc()
    await CH.fetch_chart_data(fmp, "INTC", "5Y", "weekly")
    await CH.fetch_chart_data(fmp, "INTC", "ALL", "monthly")
    await svc.history_page(PH.ASSET_STOCK, "INTC", "daily", before="2020-01-02", fmp=fmp)
    await svc.history_page(PH.ASSET_STOCK, "INTC", "monthly", before="2016-01-29", fmp=fmp)
    assert len(fmp.calls) == 1, fmp.calls


@pytest.mark.asyncio
async def test_the_history_is_refetched_when_the_close_cycle_turns(monkeypatch):
    fmp = _PagedFMP(600)
    svc = _svc()
    await svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp)
    nxt = _CYCLE_START + timedelta(days=3)          # Monday 18:00 ET
    monkeypatch.setattr(PH, "current_close_cycle_start", lambda now=None: nxt)
    await svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp)
    assert len(fmp.calls) == 2, "a previous cycle's history was served after the close"


@pytest.mark.asyncio
async def test_the_bucket_memo_runs_the_aggregation_once_per_history(monkeypatch):
    fmp = _PagedFMP(2000)
    svc = _svc()
    calls = []
    real = CH._aggregate_prices

    def _counting(rows, period):
        calls.append(period)
        return real(rows, period)

    monkeypatch.setattr(CH, "_aggregate_prices", _counting)
    for before in ("2025-01-03", "2023-01-06", "2021-01-08"):
        await svc.history_page(PH.ASSET_STOCK, "INTC", "weekly", before=before, fmp=fmp)
    assert calls == ["weekly"], calls


# ── the fetch_chart_data result cache ───────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_identical_chart_fetches_share_one_upstream_call():
    """`/overview/core`, `/overview` and `/chart` ask for the same intraday bars at once."""
    calls = []

    class _Intraday:
        async def get_intraday_prices(self, symbol, **kw):
            calls.append(kw)
            await asyncio.sleep(0.01)
            return [{"date": "2026-10-16 10:00:00", "open": 1, "high": 1, "low": 1,
                     "close": 1, "volume": 1}]

    fmp = _Intraday()
    a, b, c = await asyncio.gather(*(CH.fetch_chart_data(fmp, "NVDA", "1D") for _ in range(3)))
    assert a == b == c and len(calls) == 1
    a[0]["close"] = 999.0
    again = await CH.fetch_chart_data(fmp, "NVDA", "1D")
    assert again[0]["close"] == 1, "a caller's edit leaked into the shared cached rows"
    await CH.fetch_chart_data(fmp, "NVDA", "1D", extended_hours=True)
    assert len(calls) == 2, "extended hours must be its own cache entry"


# ── hardening: seams, junk, cursors, precision ──────────────────────────────

@pytest.mark.asyncio
async def test_a_daily_page_joins_the_range_chart_with_no_gap_and_no_overlap():
    """The client prepends a page to the range bars it holds: together they must be the
    whole settled history, every weekday once."""
    fmp = _PagedFMP(2000)
    svc = _svc()
    rng = await CH.fetch_chart_data(fmp, "INTC", "1Y", "daily")
    page = await svc.history_page(PH.ASSET_STOCK, "INTC", "daily", before=rng[0]["date"],
                                  limit=5000, fmp=fmp)
    assert page["has_more"] is False
    joined = [b["date"] for b in page["bars"]] + [r["date"] for r in rng]
    settled = [r["date"] for r in CH.settled_bars(fmp.history, _CUTOFF)]
    assert joined[: len(settled)] == settled, "a hole or a repeat at the seam"
    assert joined[len(settled):] == ["2026-10-19"], "only the range's in-progress bar is extra"


@pytest.mark.asyncio
@pytest.mark.parametrize("range_code,interval", [("1Y", "weekly"), ("5Y", "monthly")])
async def test_a_bucket_page_plus_the_range_chart_is_the_all_range_series(range_code, interval):
    fmp = _PagedFMP(3000)
    svc = _svc()
    rng = await CH.fetch_chart_data(fmp, "INTC", range_code, interval)
    page = await svc.history_page(PH.ASSET_STOCK, "INTC", interval, before=rng[0]["date"],
                                  fmp=fmp)
    everything = await CH.fetch_chart_data(fmp, "INTC", "ALL", interval)
    assert page["bars"] + rng == everything, "pages and ranges disagree about a bucket"
    assert page["has_more"] is False


@pytest.mark.asyncio
async def test_a_mid_bucket_cursor_excludes_the_bucket_it_falls_in():
    fmp = _PagedFMP(400)
    svc = _svc()
    wednesday = date(2026, 9, 16)
    page = await svc.history_page(PH.ASSET_STOCK, "INTC", "weekly",
                                  before=wednesday.isoformat(), limit=1, fmp=fmp)
    # That week's bucket is dated Friday 2026-09-18 (≥ the cursor): the page is the week
    # before, dated its own Friday.
    assert [b["date"] for b in page["bars"]] == ["2026-09-11"]
    assert page["has_more"] is True


def test_junk_rows_never_reach_the_columns():
    cols = PH.PriceColumns.from_rows([
        "not a row", None, {"close": 5}, {"date": "2026-02-30", "close": 5},
        {"date": "2026-10-13", "close": float("nan")}, {"date": "2026-10-13", "close": -1},
        {"date": "2026-10-14", "close": "12.5", "open": float("inf"), "volume": "x"},
        {"date": "2026-10-15", "close": 10.0}, {"date": "2026-10-15", "close": 11.0},
    ], settled_through=None, complete=True)
    rows = cols.rows()
    assert [r["date"] for r in rows] == ["2026-10-14", "2026-10-15"]
    assert rows[0] == {"date": "2026-10-14", "open": None, "high": None, "low": None,
                       "close": 12.5, "volume": None}
    assert rows[1]["close"] == 11.0, "on a repeated date the later (newer page's) row wins"


@pytest.mark.asyncio
async def test_a_dateless_row_in_a_full_page_does_not_end_the_walk():
    fmp = _PagedFMP(7000)
    real = fmp.get_historical_prices

    async def _with_junk(symbol, from_date=None, to_date=None):
        page = await real(symbol, from_date, to_date)
        return page + [{"close": 1.0}] if len(page) == 5000 else page

    fmp.get_historical_prices = _with_junk
    rows = await CH._fetch_all_daily(fmp, "INTC", from_date="1900-01-01")
    assert len([r for r in rows if r.get("date")]) == 7001, "paging stopped at the junk row"


@pytest.mark.asyncio
async def test_each_asset_page_carries_its_own_range_responses_precision(monkeypatch):
    from app.services import etf_service, index_service

    class _Stub:
        def __init__(self):
            self.fmp = None

        async def _get_history_strict(self, symbol):
            return [{"date": d.isoformat(), "close": 101.23456, "volume": 1.0}
                    for d in _weekdays_back(300)]

    monkeypatch.setattr(etf_service, "get_etf_service", lambda: _Stub())
    monkeypatch.setattr(index_service, "get_index_service", lambda: _Stub())
    svc = _svc()
    etf_daily = await svc.history_page(PH.ASSET_ETF, "SPY", "daily", before="2026-10-01")
    etf_weekly = await svc.history_page(PH.ASSET_ETF, "SPY", "weekly", before="2026-10-01")
    idx_weekly = await svc.history_page(PH.ASSET_INDEX, "^GSPC", "weekly", before="2026-10-01")
    assert etf_daily["bars"][-1]["close"] == 101.23, "ETF daily bars are 2 dp"
    assert etf_weekly["bars"][-1]["close"] == 101.23456, "ETF buckets are served unrounded"
    assert idx_weekly["bars"][-1]["close"] == 101.23, "index bars are 2 dp everywhere"


@pytest.mark.asyncio
async def test_the_strict_intraday_client_raises_where_the_default_answers_empty():
    from app.integrations.fmp import FMPClient

    client = FMPClient.__new__(FMPClient)

    async def _error_body(self, endpoint, params=None):
        return {"Error Message": "Limit Reach"}

    original = FMPClient._make_request
    FMPClient._make_request = _error_body
    try:
        assert await client.get_intraday_prices("AAPL", "1hour", "2026-10-01", "2026-10-02") == []
        with pytest.raises(FMPUnavailableException):
            await client.get_intraday_prices("AAPL", "1hour", "2026-10-01", "2026-10-02",
                                             strict=True)
    finally:
        FMPClient._make_request = original


@pytest.mark.asyncio
async def test_an_intraday_window_error_is_an_error_not_the_end_of_the_series():
    class _Failing:
        async def get_intraday_prices(self, symbol, **kw):
            raise FMPUnavailableException("FMP returned 503")

    with pytest.raises(FMPUnavailableException):
        await _svc().history_page(PH.ASSET_STOCK, "INTC", "1hour",
                                  before="2026-10-14 10:30:00", fmp=_Failing())


@pytest.mark.asyncio
async def test_a_build_abandoned_by_another_loop_is_never_joined():
    svc = _svc()
    fmp = _PagedFMP(300)
    other = asyncio.new_event_loop()
    try:
        stale = other.create_future()
        svc._inflight[svc._history_key(PH.ASSET_STOCK, "INTC", fmp)] = stale
        cols = await asyncio.wait_for(svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp), 2.0)
        assert len(cols) == 300
    finally:
        other.close()


@pytest.mark.asyncio
async def test_a_fred_page_is_close_only_with_no_invented_volume(monkeypatch):
    from app.services import commodity_service

    class _Fred:
        fmp = None

        async def _get_history_strict(self, symbol):
            return [{"date": d.isoformat(), "close": 70.0 + i % 5}
                    for i, d in enumerate(_weekdays_back(400))]

    monkeypatch.setattr(commodity_service, "get_commodity_service", lambda: _Fred())
    svc = _svc()
    for interval in ("daily", "weekly", "monthly"):
        page = await svc.history_page(PH.ASSET_COMMODITY, "CL", interval, before="2026-10-01")
        assert page["bars"]
        for bar in page["bars"]:
            assert bar["open"] is None and bar["high"] is None and bar["low"] is None
            assert bar["volume"] is None, f"{interval} invented a volume for FRED spot"


@pytest.mark.asyncio
async def test_a_superseded_build_never_strands_the_newer_builds_waiters():
    """`clear()` (or another loop's abandoned build) leaves an OLD build running beside a
    NEW one for the same key. The old one finishing must not consume the new one's
    progress, or the new build's completion never wakes its `ensure_covers` waiter."""
    fmp = _PagedFMP(3000)
    gates = [asyncio.Event(), asyncio.Event()]
    real = fmp.get_historical_prices

    async def _gated(symbol, from_date=None, to_date=None):
        gate = gates[len(fmp.calls)]
        rows = await real(symbol, from_date, to_date)
        await gate.wait()
        return rows

    fmp.get_historical_prices = _gated
    svc = _svc()
    old = asyncio.create_task(svc.full_daily(PH.ASSET_STOCK, "INTC", fmp=fmp))
    await asyncio.sleep(0.01)
    svc.clear()
    # Older than the whole history: this waiter needs the COMPLETED build.
    newer = asyncio.create_task(svc.ensure_covers(fmp, "INTC", date(1900, 1, 1)))
    await asyncio.sleep(0.01)
    gates[0].set()
    assert len(await asyncio.wait_for(old, 2.0)) == 3000
    gates[1].set()
    cols = await asyncio.wait_for(newer, 2.0)
    assert cols.complete and len(cols) == 3000


# ── FMP's per-request cap and the verified intraday depth (probed 2026-10-21) ──

def test_every_paging_window_fits_in_one_fmp_request():
    """A window longer than one FMP request would come back with its oldest days missing;
    the stitch would heal it, but at a request per cap's worth — size them to fit."""
    for interval, days in PH._INTRADAY_WINDOW_DAYS.items():
        assert days - 1 <= CH.FMP_INTRADAY_SPAN_DAYS[interval], interval


class _ShortAnswerIntraday:
    """Hourly-stamped bars for every weekday in [from, to], but each request answered with
    only its newest `cap_days` — a cap SMALLER than the paging window."""

    def __init__(self, cap_days: int):
        self.cap_days = cap_days
        self.calls = []

    async def get_intraday_prices(self, symbol, **kw):
        self.calls.append(kw)
        to = date.fromisoformat(kw["to_date"])
        frm = max(date.fromisoformat(kw["from_date"]), to - timedelta(days=self.cap_days))
        out, d = [], frm
        while d <= to:
            if d.weekday() < 5:
                for m in range(9 * 60 + 30, 16 * 60, 15):
                    out.append({"date": f"{d.isoformat()} {m // 60:02d}:{m % 60:02d}:00",
                                "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
                                "volume": 1.0})
            d += timedelta(days=1)
        return list(reversed(out))


@pytest.mark.asyncio
async def test_a_window_fmp_answers_short_is_completed_not_left_with_a_hole():
    from app.utils.market_hours import is_trading_day

    fmp = _ShortAnswerIntraday(cap_days=4)          # 14-day 15-minute windows, 4-day answers
    page = await _svc().history_page(PH.ASSET_STOCK, "INTC", "15min",
                                     before="2026-09-30 10:00:00", limit=5000, fmp=fmp)
    days = sorted({b["date"][:10] for b in page["bars"]})
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    d, missing = first, []
    while d <= last:
        if is_trading_day(d) and d.isoformat() not in days:
            missing.append(d.isoformat())
        d += timedelta(days=1)
    assert missing == [], f"sessions lost inside a window: {missing}"
    assert len(fmp.calls) > PH._MAX_WINDOWS_PER_PAGE, "the short answers were never completed"
    for call in fmp.calls:
        assert call.get("strict") is True, "a stitched page read must stay strict"


@pytest.mark.asyncio
async def test_paging_stops_at_the_verified_depth_without_asking_fmp():
    class _Never:
        async def get_intraday_prices(self, symbol, **kw):
            raise AssertionError("asked FMP past the verified depth")

    assert set(PH._INTRADAY_MAX_LOOKBACK_DAYS.values()) == {5475}, "probed: ≥ 15 years"
    too_old = date.today() - timedelta(days=5475 + 120)
    page = await _svc().history_page(PH.ASSET_STOCK, "INTC", "1hour",
                                     before=f"{too_old.isoformat()} 10:30:00", fmp=_Never())
    assert page["bars"] == [] and page["has_more"] is False
    assert page.get("reached_start") is False, "the look-back bound is not the listing"
