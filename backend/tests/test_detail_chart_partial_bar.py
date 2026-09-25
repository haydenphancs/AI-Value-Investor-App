"""F16-2 — daily charts / derived stats must not persist FMP's IN-PROGRESS bar.

FMP `historical-price-eod/full` includes the current session's partial bar. Live
(2026-09-16, read at ~21:35 ET, after the close): ^GSPC header 754.05 / -0.44% while the
1Y chart's last bar was `{2026-09-16, close 759.07, volume 5.9M}` — a ~10-minute snapshot
the first viewer after 09:30 had pinned into Tier-2 as "today's bar" for 12h. The fix has
two halves and each test below pins one:

  * `_settled_bars` drops rows dated after the current close cycle's date (weekday 18:00
    ET) before anything is sliced, aggregated, derived or persisted;
  * the history / derived Tier-1 entries and the `chart:*` / `derived` Tier-2 rows are a
    MISS once the cycle turns, whatever the rolling 12h TTL says — otherwise the settled
    bar would be MISSING for up to 12h instead of wrong.

Index, ETF and commodity carry the fix since F16-2; the stock overview (C7) got it later —
its daily history lives inside the 24h `stock_fundamentals_cache` bundle, so its freshness
half is a close-cycle MISS on that bundle (see the stock section at the bottom).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.services import commodity_service as C
from app.services import etf_service as E
from app.services import index_service as I
from app.services import stock_overview_service as S
from app.utils.market_hours import ET


# ── clock fixtures ────────────────────────────────────────────────────────────

def _utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# Wednesday 2026-09-16. 10:00 ET = 14:00 UTC (mid-session); 16:30 ET = 20:30 UTC (after the
# bell, before FMP has settled the bar); 19:00 ET = 23:00 UTC (settled).
MID_SESSION = _utc(2026, 9, 16, 14)
AFTER_BELL_UNSETTLED = _utc(2026, 9, 16, 20, 30)
SETTLED = _utc(2026, 9, 16, 23)
SATURDAY = _utc(2026, 9, 19, 15)


def _rows(dates, close=100.0):
    return [
        {"date": d, "open": close, "high": close, "low": close, "close": close,
         "volume": 1_000_000}
        for d in dates
    ]


THREE_DAYS = ["2026-09-14", "2026-09-15", "2026-09-16"]


# ── `_settled_bars`: the filter ───────────────────────────────────────────────

@pytest.mark.parametrize("M", [I, E, C, S], ids=["index", "etf", "commodity", "stock"])
def test_mid_session_drops_todays_partial_bar(M):
    out = M._settled_bars(_rows(THREE_DAYS), now=MID_SESSION)
    assert [r["date"] for r in out] == ["2026-09-14", "2026-09-15"]


@pytest.mark.parametrize("M", [I, E, C, S], ids=["index", "etf", "commodity", "stock"])
def test_after_the_bell_but_before_settle_still_drops_it(M):
    """16:02 ET is not "closed" as far as the bar is concerned — FMP is still finalising
    it. The 18:00 close cycle is the boundary, not the 16:00 bell."""
    out = M._settled_bars(_rows(THREE_DAYS), now=AFTER_BELL_UNSETTLED)
    assert [r["date"] for r in out] == ["2026-09-14", "2026-09-15"]


@pytest.mark.parametrize("M", [I, E, C, S], ids=["index", "etf", "commodity", "stock"])
def test_once_settled_todays_bar_is_kept(M):
    out = M._settled_bars(_rows(THREE_DAYS), now=SETTLED)
    assert [r["date"] for r in out] == THREE_DAYS


@pytest.mark.parametrize("M", [I, E, C, S], ids=["index", "etf", "commodity", "stock"])
def test_on_a_weekend_the_cutoff_is_fridays_close(M):
    rows = _rows(["2026-09-17", "2026-09-18", "2026-09-19", "2026-09-21"])  # Thu Fri Sat Mon
    out = M._settled_bars(rows, now=SATURDAY)
    assert [r["date"] for r in out] == ["2026-09-17", "2026-09-18"]


@pytest.mark.parametrize("M", [I, E, C, S], ids=["index", "etf", "commodity", "stock"])
def test_the_filter_is_total_for_malformed_input(M):
    """Empty, None, non-dict rows, a null date, a datetime-stamped date and a stray
    future-dated row: never raises, drops what it cannot trust, keeps the undated row
    (every consumer already tolerates it)."""
    assert M._settled_bars([], now=MID_SESSION) == []
    assert M._settled_bars(None, now=MID_SESSION) == []
    rows = [
        "not-a-row", None, 42,
        {"date": None, "close": 1.0},
        {"date": "2026-09-15 00:00:00", "close": 2.0},   # timestamped, same day
        {"date": "2026-09-16T13:35:00", "close": 3.0},   # today's partial
        {"date": "2027-01-01", "close": 4.0},            # future
    ]
    out = M._settled_bars(rows, now=MID_SESSION)
    assert [r["close"] for r in out] == [1.0, 2.0]


@pytest.mark.parametrize("M", [I, E, C, S], ids=["index", "etf", "commodity", "stock"])
def test_a_duplicate_of_todays_date_is_dropped_with_it(M):
    rows = _rows(["2026-09-15", "2026-09-16", "2026-09-16"])
    assert [r["date"] for r in M._settled_bars(rows, now=MID_SESSION)] == ["2026-09-15"]


# ── freshness: Tier-1 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("M", [I, E, C], ids=["index", "etf", "commodity"])
def test_a_tier1_entry_from_the_previous_cycle_is_a_miss_and_evicted(M, monkeypatch):
    M._cache.clear()
    cycle = time.time() - 60  # the cycle turned a minute ago
    monkeypatch.setattr(M, "current_close_cycle_start",
                        lambda now=None: datetime.fromtimestamp(cycle, tz=timezone.utc))
    key = "x:hist:TEST"
    M._cache[key] = (cycle - 1, ["old"], M._HISTORY_TTL)     # 61s old, 12h TTL
    assert M._cache_get_settled(key) is None, "served a history pulled before the cycle"
    assert key not in M._cache, "stale entry left in place"
    M._cache[key] = (cycle + 1, ["new"], M._HISTORY_TTL)
    assert M._cache_get_settled(key) == ["new"]
    # The rolling ceiling still applies to an entry written inside the cycle.
    M._cache[key] = (time.time() - M._HISTORY_TTL - 1, ["ancient"], M._HISTORY_TTL)
    monkeypatch.setattr(M, "current_close_cycle_start",
                        lambda now=None: datetime.fromtimestamp(0, tz=timezone.utc))
    assert M._cache_get_settled(key) is None
    assert M._cache_get_settled("x:missing") is None
    M._cache.clear()


# ── freshness: Tier-2 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("cls", [I.IndexService, E.ETFService, C.CommodityService], ids=["index", "etf", "commodity"])
def test_chart_and_derived_rows_are_fresh_for_one_close_cycle_not_12h(cls):
    now = SETTLED                                    # Wed 19:00 ET
    cycle = _utc(2026, 9, 16, 22)                     # Wed 18:00 ET
    for cat in ("derived", "chart:1Y:daily", "chart:5Y:weekly"):
        # Written an hour ago but BEFORE the cycle turned: stale.
        assert cls._tier2_is_fresh(cat, cycle - timedelta(minutes=30), now=now) is False, cat
        # Written after the cycle turned: fresh.
        assert cls._tier2_is_fresh(cat, cycle + timedelta(minutes=1), now=now) is True, cat
    # ...and STILL fresh 20h+ later inside the same cycle (a weekend is ~72h): the row
    # describes the same settled bars, so the old 12h rolling TTL must not evict it.
    weekend_now = _utc(2026, 9, 19, 15)               # Sat 11:00 ET
    fri_cycle = _utc(2026, 9, 18, 22)                 # Fri 18:00 ET
    assert cls._tier2_is_fresh("derived", fri_cycle + timedelta(hours=1), now=weekend_now)
    if cls is C.CommodityService:
        # commodity_cache stores the bare category ("chart"), not the range-suffixed key.
        assert cls._tier2_is_fresh("chart", cycle - timedelta(minutes=30), now=now) is False
        assert cls._tier2_is_fresh("chart", cycle + timedelta(minutes=1), now=now) is True


@pytest.mark.parametrize("cls", [I.IndexService, E.ETFService, C.CommodityService], ids=["index", "etf", "commodity"])
def test_slow_sections_keep_the_rolling_12h_ttl(cls):
    now = SETTLED
    for cat in ("fundamentals", "constituents", "fund"):
        assert cls._tier2_is_fresh(cat, now - timedelta(hours=11), now=now) is True
        assert cls._tier2_is_fresh(cat, now - timedelta(hours=13), now=now) is False


# ── end to end: what gets PERSISTED ───────────────────────────────────────────

def _calendar_rows(n=400, partial_close=None):
    """`n` consecutive calendar-day rows ending TODAY (UTC), oldest-first — the shape the
    fakes in test_detail_screen_outliers use. Today's row gets `partial_close` when given,
    so a leak of the in-progress bar is visible as a number, not just a date."""
    today = datetime.now(tz=timezone.utc).date()
    rows = []
    for i in range(n):
        d = today - timedelta(days=n - 1 - i)
        c = 100.0 + i * 0.1
        if i == n - 1 and partial_close is not None:
            c = partial_close
        rows.append({"date": d.isoformat(), "open": c, "high": c, "low": c, "close": c,
                     "volume": 1_000_000})
    return rows


def _cycle_at(day: _dt.date):
    """A `current_close_cycle_start` stub whose cycle began at 18:00 ET on `day`."""
    boundary = datetime(day.year, day.month, day.day, 18, tzinfo=ET).astimezone(timezone.utc)
    return lambda now=None: boundary


def _isolate(monkeypatch, M, cls, store):
    monkeypatch.setattr(cls, "_tier2_get", staticmethod(lambda sym, cat: store.get(f"{sym}:{cat}")))
    monkeypatch.setattr(cls, "_tier2_put",
                        staticmethod(lambda sym, cat, payload: store.__setitem__(f"{sym}:{cat}", payload)))
    M._cache.clear()
    M._inflight.clear()


@pytest.mark.asyncio
async def test_index_1y_chart_persists_through_the_previous_session_mid_session(monkeypatch):
    today = datetime.now(tz=timezone.utc).date()
    rows = _calendar_rows(partial_close=999.0)
    store = {}
    _isolate(monkeypatch, I, I.IndexService, store)
    # Mid-session: the cycle that began yesterday 18:00 ET is the current one.
    monkeypatch.setattr(I, "current_close_cycle_start", _cycle_at(today - timedelta(days=1)))
    svc = I.IndexService.__new__(I.IndexService)

    async def _hist(symbol):
        return list(rows)
    svc._get_history = _hist

    points = await svc._get_chart("^GSPC", "1Y", None)
    persisted = store["^GSPC:chart:1Y:daily"]
    assert persisted[-1]["date"] == (today - timedelta(days=1)).isoformat(), (
        "today's in-progress bar was persisted as a settled daily bar"
    )
    assert all(p["close"] != 999.0 for p in persisted)
    assert points[-1].date == persisted[-1]["date"]

    # Once the cycle turns, today's bar (now settled) is included.
    store.clear()
    monkeypatch.setattr(I, "current_close_cycle_start", _cycle_at(today))
    await svc._get_chart("^GSPC", "1Y", None)
    assert store["^GSPC:chart:1Y:daily"][-1]["date"] == today.isoformat()


@pytest.mark.asyncio
async def test_index_5y_aggregate_excludes_the_partial_bar(monkeypatch):
    today = datetime.now(tz=timezone.utc).date()
    rows = _calendar_rows(n=2000, partial_close=999.0)
    store = {}
    _isolate(monkeypatch, I, I.IndexService, store)
    monkeypatch.setattr(I, "current_close_cycle_start", _cycle_at(today - timedelta(days=1)))
    svc = I.IndexService.__new__(I.IndexService)

    async def _hist(symbol):
        return list(rows)
    svc._get_history = _hist

    await svc._get_chart("^GSPC", "5Y", None)
    bars = store["^GSPC:chart:5Y:weekly"]
    # The current week's bar must not carry the partial close as its close/high.
    assert all(b.get("close") != 999.0 and b.get("high") != 999.0 for b in bars)


@pytest.mark.asyncio
async def test_index_derived_stats_anchor_on_the_settled_close(monkeypatch):
    today = datetime.now(tz=timezone.utc).date()
    rows = _calendar_rows(partial_close=999.0)
    store = {}
    _isolate(monkeypatch, I, I.IndexService, store)
    monkeypatch.setattr(I, "current_close_cycle_start", _cycle_at(today - timedelta(days=1)))
    svc = I.IndexService.__new__(I.IndexService)

    async def _hist(symbol):
        return list(rows)
    svc._get_history = _hist

    derived = await svc._get_derived("^GSPC")
    expected = I.IndexService._derive_from_history(rows[:-1])
    assert derived == expected, "a return / moving average used today's partial bar"
    assert derived["one_month_return"] == expected["one_month_return"] < 100
    assert store["^GSPC:derived"] == expected


@pytest.mark.asyncio
async def test_etf_derived_sma50_and_periods_exclude_the_partial_bar(monkeypatch):
    today = datetime.now(tz=timezone.utc).date()
    rows = _calendar_rows(partial_close=999.0)
    store = {}
    _isolate(monkeypatch, E, E.ETFService, store)
    monkeypatch.setattr(E, "current_close_cycle_start", _cycle_at(today - timedelta(days=1)))
    svc = E.ETFService.__new__(E.ETFService)

    async def _hist(symbol):
        return list(rows)
    svc._get_history = _hist

    derived = await svc._get_derived("SPY")   # SPY: its own benchmark, one history
    assert derived["sma_50"] == pytest.approx(E._sma(rows[:-1], 50))
    assert derived["sma_50"] < 200, "the 999 partial close leaked into the 50-day average"
    assert store["SPY:derived"]["sma_50"] == derived["sma_50"]
    # No period's return may be anchored on the 999 partial close.
    for p in derived["performance_periods"]:
        assert p.get("return_pct") is None or abs(p["return_pct"]) < 100, p


@pytest.mark.asyncio
async def test_etf_1y_chart_persists_through_the_previous_session_mid_session(monkeypatch):
    today = datetime.now(tz=timezone.utc).date()
    rows = _calendar_rows(partial_close=999.0)
    store = {}
    _isolate(monkeypatch, E, E.ETFService, store)
    monkeypatch.setattr(E, "current_close_cycle_start", _cycle_at(today - timedelta(days=1)))
    svc = E.ETFService.__new__(E.ETFService)

    async def _hist(symbol):
        return list(rows)
    svc._get_history = _hist

    bars = await svc._get_chart("SPY", "1Y", None)
    persisted = store["SPY:chart:1Y:daily"]
    assert persisted[-1]["date"] == (today - timedelta(days=1)).isoformat()
    assert bars[-1]["date"] == persisted[-1]["date"]
    assert all(b["close"] != 999.0 for b in persisted)

    store.clear()
    monkeypatch.setattr(E, "current_close_cycle_start", _cycle_at(today))
    await svc._get_chart("SPY", "1Y", None)
    assert store["SPY:chart:1Y:daily"][-1]["date"] == today.isoformat()


@pytest.mark.asyncio
async def test_history_is_refetched_when_the_cycle_turns_not_12h_later(monkeypatch):
    """The other half: a history pulled at 17:59 (partial bar dropped) must not stay
    12h-fresh — the settled bar has to land as soon as the cycle turns."""
    for M, cls, key in ((I, I.IndexService, "idx:hist:^GSPC"), (E, E.ETFService, "etf:hist:SPY")):
        _isolate(monkeypatch, M, cls, {})
        calls = {"n": 0}

        async def _fetch(fmp, symbol):
            calls["n"] += 1
            return _calendar_rows(n=5)

        import app.services.chart_helper as CH
        monkeypatch.setattr(CH, "_fetch_all_daily", _fetch)
        svc = cls.__new__(cls)
        svc.fmp = object()
        sym = key.split(":")[-1]
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        monkeypatch.setattr(M, "current_close_cycle_start", lambda now=None, p=past: p)
        await svc._get_history(sym)
        await svc._get_history(sym)
        assert calls["n"] == 1, f"{key}: not cached within a cycle"
        # The cycle turns (boundary now AFTER the entry's write time).
        future = datetime.now(timezone.utc) + timedelta(seconds=1)
        monkeypatch.setattr(M, "current_close_cycle_start", lambda now=None, f=future: f)
        await svc._get_history(sym)
        assert calls["n"] == 2, f"{key}: served the previous cycle's history"
        M._cache.clear()


# ── commodity: the same defect through the metal proxies (GLD/SLV/PPLT/PALL) ─────────


@pytest.mark.asyncio
async def test_commodity_derived_and_daily_chart_exclude_the_partial_bar(monkeypatch):
    """`_fetch_all_daily` for an ETF-proxied metal carries today's in-progress bar exactly
    like the index/ETF history; the derived section and every persisted daily chart must
    end at the settled close. A FRED series (crude, gas) is a settlement published days
    behind, so the filter is a no-op for it."""
    monkeypatch.setattr(C.time, "time", lambda: MID_SESSION.timestamp())

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return MID_SESSION.astimezone(tz) if tz else MID_SESSION.replace(tzinfo=None)
    monkeypatch.setattr(C, "datetime", _Now)
    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "datetime", _Now)

    C._cache.clear()
    svc = C.CommodityService.__new__(C.CommodityService)
    rows = _calendar_rows(400, partial_close=999.0)

    async def _hist(sym=None):
        return list(rows)
    monkeypatch.setattr(svc, "_get_history", _hist)
    monkeypatch.setattr(svc, "_get_spy_history", _hist)
    monkeypatch.setattr(C.CommodityService, "_tier2_get", staticmethod(lambda key: None))
    persisted = {}
    monkeypatch.setattr(C.CommodityService, "_tier2_put",
                        staticmethod(lambda key, sym, cat, payload: persisted.__setitem__(key, payload)))

    derived = await svc._get_derived("GCUSD")
    assert derived.get("last_close") != 999.0, "the in-progress bar anchored the derived stats"
    bars = await svc._get_chart("GCUSD", "1Y", None)
    assert bars and all(float(b.get("close", 0)) != 999.0 for b in bars)
    assert str(bars[-1].get("date"))[:10] <= C._settled_cutoff_date(MID_SESSION)
    for key, payload in persisted.items():
        if isinstance(payload, list):
            assert all(float(b.get("close", 0)) != 999.0 for b in payload), key


# ── stock overview (C7): the history inside `stock_fundamentals_cache` ─────────────
#
# The stock screen's daily history is not its own cache entry: it rides inside the 24h
# fundamentals bundle (`_fetch_fundamentals`, `historical-price-eod/full` from 1900 to
# today) next to a separately cached SPY series. The first viewer after 09:30 pinned the
# in-progress bar for 24h, and the Performance / Benchmark cards and the 3M-1Y daily chart
# ended on it.


class _StockFMP:
    """Every FMP call the fundamentals fan-out makes. Histories end TODAY (UTC) with a
    tell-tale partial close; everything else is a minimal usable payload."""

    def __init__(self, stock_rows, spy_rows):
        self.stock_rows, self.spy_rows = stock_rows, spy_rows
        self.history_calls = []

    async def get_historical_prices(self, symbol, frm, to):
        self.history_calls.append(symbol)
        return [dict(r) for r in (self.spy_rows if symbol == "SPY" else self.stock_rows)]

    async def get_company_profile(self, ticker):
        return {"companyName": "Apple", "sector": "Technology"}

    def __getattr__(self, name):
        async def _call(*args, **kwargs):
            return [{"stub": name}]
        return _call


class _NoMovers:
    async def get_sector_performance(self):
        return []

    async def get_industry_performance(self):
        return []


def _stock_service(monkeypatch, fmp):
    async def _no_short_interest(ticker):
        return {}

    monkeypatch.setattr(S, "get_short_interest", _no_short_interest)
    monkeypatch.setattr(S, "get_market_movers_service", lambda: _NoMovers())
    S._cache.clear()
    svc = S.StockOverviewService.__new__(S.StockOverviewService)
    svc.fmp = fmp
    svc.supabase = None
    return svc


@pytest.mark.asyncio
async def test_stock_bundle_drops_the_partial_bar_from_both_histories_mid_session(monkeypatch):
    today = datetime.now(tz=timezone.utc).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    stock_rows = _calendar_rows(partial_close=999.0)
    spy_rows = _calendar_rows(partial_close=888.0)
    svc = _stock_service(monkeypatch, _StockFMP(stock_rows, spy_rows))
    monkeypatch.setattr(S, "current_close_cycle_start", _cycle_at(today - timedelta(days=1)))

    bundle = await svc._fetch_fundamentals("AAPL")

    stock, spy = bundle["stock_historical"], bundle["spy_historical"]
    assert stock[-1]["date"] == yesterday, "today's in-progress stock bar was kept"
    assert spy[-1]["date"] == yesterday, "today's in-progress SPY bar was kept"
    assert all(r["close"] != 999.0 for r in stock) and all(r["close"] != 888.0 for r in spy)
    assert bundle[S._SETTLED_THROUGH_KEY] == yesterday

    # Everything derived from the bundle ends on the settled close.
    periods = {p.label: p for p in svc._build_performance_periods(stock, spy)}
    assert periods["1 Month"].change_percent == round(S._compute_return(stock_rows[:-1], 21), 2)
    assert periods["1 Month"].change_percent < 100, "the 999 partial close leaked in"
    assert S._extract_chart_data(stock, "1Y")[-1]["date"] == yesterday
    S._cache.clear()


@pytest.mark.asyncio
async def test_stock_bundle_keeps_todays_bar_once_settled(monkeypatch):
    today = datetime.now(tz=timezone.utc).date()
    svc = _stock_service(monkeypatch, _StockFMP(_calendar_rows(), _calendar_rows()))
    monkeypatch.setattr(S, "current_close_cycle_start", _cycle_at(today))
    bundle = await svc._fetch_fundamentals("AAPL")
    assert bundle["stock_historical"][-1]["date"] == today.isoformat()
    assert bundle["spy_historical"][-1]["date"] == today.isoformat()
    assert bundle[S._SETTLED_THROUGH_KEY] == today.isoformat()
    S._cache.clear()


@pytest.mark.asyncio
async def test_spy_history_is_keyed_on_the_close_cycle_not_the_utc_date(monkeypatch):
    """One SPY pull per cycle, shared across tickers; a new cycle re-pulls it (and ends on
    the newly settled bar) and drops the previous cycle's multi-thousand-row series."""
    today = datetime.now(tz=timezone.utc).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    fmp = _StockFMP(_calendar_rows(), _calendar_rows(partial_close=888.0))
    svc = _stock_service(monkeypatch, fmp)
    monkeypatch.setattr(S, "current_close_cycle_start", _cycle_at(today - timedelta(days=1)))

    await svc._fetch_fundamentals("AAPL")
    await svc._fetch_fundamentals("MSFT")
    assert fmp.history_calls.count("SPY") == 1, "SPY re-pulled inside one close cycle"
    assert f"spy_hist_full:{yesterday}" in S._cache
    assert f"spy_hist_full:{today.isoformat()}" not in S._cache, "keyed on the UTC date"

    monkeypatch.setattr(S, "current_close_cycle_start", _cycle_at(today))
    bundle = await svc._fetch_fundamentals("AAPL")
    assert fmp.history_calls.count("SPY") == 2, "the new cycle served the old SPY series"
    assert bundle["spy_historical"][-1]["date"] == today.isoformat()
    assert f"spy_hist_full:{yesterday}" not in S._cache, "previous cycle's series left resident"
    S._cache.clear()


# `_bundle_is_current` — the freshness rule for both tiers, on a frozen clock.

def test_stock_bundle_freshness_is_one_close_cycle():
    cur = {S._SETTLED_THROUGH_KEY: "2026-09-15"}          # Tue's settled session
    # Wed 10:00 ET (cycle = Tue 18:00 ET = 22:00 UTC): a bundle cut at Tue, written Wed.
    assert S._bundle_is_current(cur, _utc(2026, 9, 16, 13), now=MID_SESSION) is True
    # Written Tue 17:00 ET, BEFORE the cycle turned — stale, even though it is 21h old.
    assert S._bundle_is_current(cur, _utc(2026, 9, 15, 21), now=MID_SESSION) is False
    # Wed 19:00 ET: the Wed cycle started at 18:00, so the Wed-morning bundle is stale.
    assert S._bundle_is_current(cur, _utc(2026, 9, 16, 13), now=SETTLED) is False
    # A bundle fetched across the boundary: cut at the OLD date, stamped in the new cycle.
    assert S._bundle_is_current(cur, _utc(2026, 9, 16, 22, 1), now=SETTLED) is False
    assert S._bundle_is_current({S._SETTLED_THROUGH_KEY: "2026-09-16"},
                                _utc(2026, 9, 16, 22, 1), now=SETTLED) is True
    # A pre-fix row (no stamp, possibly carrying the partial bar) is never current.
    assert S._bundle_is_current({}, _utc(2026, 9, 16, 13), now=MID_SESSION) is False
    assert S._bundle_is_current(None, _utc(2026, 9, 16, 13), now=MID_SESSION) is False
    # Weekend: Friday's bundle holds through Saturday.
    fri = {S._SETTLED_THROUGH_KEY: "2026-09-18"}
    assert S._bundle_is_current(fri, _utc(2026, 9, 18, 23), now=SATURDAY) is True


class _Rows:
    def __init__(self, rows):
        self.data = rows


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        assert name == "stock_fundamentals_cache"
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return _Rows(self._rows)


def test_a_tier2_row_cached_before_the_close_cycle_is_a_miss(monkeypatch):
    """Frozen cycle one hour ago. A 2h-old row is well inside the 24h TTL and is STILL a
    miss — even with a stamp that matches the current cycle — because it was written
    before the cycle turned. The row written after it, stamped for this cycle, is a hit."""
    now = datetime.now(timezone.utc)
    cycle = now - timedelta(hours=1)
    monkeypatch.setattr(S, "current_close_cycle_start", lambda now=None: cycle)
    stamp = cycle.astimezone(ET).date().isoformat()
    svc = S.StockOverviewService.__new__(S.StockOverviewService)

    def _row(age, bundle):
        return [{"response_json": bundle, "cached_at": (now - age).isoformat()}]

    good = {"profile": {"companyName": "Apple"}, S._SETTLED_THROUGH_KEY: stamp}
    svc.supabase = _FakeTable(_row(timedelta(hours=2), good))
    assert svc._check_fundamentals_db("AAPL") is None, "served a row from the previous cycle"

    svc.supabase = _FakeTable(_row(timedelta(minutes=30), good))
    assert svc._check_fundamentals_db("AAPL") == good

    # Inside the cycle but cut at an older session / never stamped (pre-fix code): miss.
    for bundle in ({**good, S._SETTLED_THROUGH_KEY: "2000-01-03"},
                   {"profile": {"companyName": "Apple"}}):
        svc.supabase = _FakeTable(_row(timedelta(minutes=30), bundle))
        assert svc._check_fundamentals_db("AAPL") is None

    # The 24h ceiling still applies inside a (long, weekend) cycle.
    weekend_cycle = now - timedelta(hours=30)
    monkeypatch.setattr(S, "current_close_cycle_start", lambda now=None: weekend_cycle)
    old_stamp = {**good, S._SETTLED_THROUGH_KEY: weekend_cycle.astimezone(ET).date().isoformat()}
    svc.supabase = _FakeTable(_row(timedelta(hours=25), old_stamp))
    assert svc._check_fundamentals_db("AAPL") is None


@pytest.mark.asyncio
async def test_the_tier1_bundle_is_refetched_when_the_cycle_turns(monkeypatch):
    """The 1h memory tier must not outlive the close cycle either: a bundle cached at
    17:59 would otherwise serve its (settled-bar-less) history until 18:59."""
    S._cache.clear()
    svc = S.StockOverviewService.__new__(S.StockOverviewService)
    calls = {"n": 0}

    async def _fetch(ticker):
        calls["n"] += 1
        return {
            "profile": {"companyName": "Apple"},
            "stock_historical": [{"date": "2026-09-15", "close": 1.0}],
            "key_metrics": [{}],
            S._SETTLED_THROUGH_KEY: S._settled_cutoff_date(),
        }

    monkeypatch.setattr(svc, "_check_fundamentals_db", lambda ticker: None)
    monkeypatch.setattr(svc, "_fetch_fundamentals", _fetch)
    monkeypatch.setattr(svc, "_upsert_fundamentals_db", lambda ticker, data: None)

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr(S, "current_close_cycle_start", lambda now=None: past)
    await svc._get_fundamentals("AAPL")
    await svc._get_fundamentals("AAPL")
    assert calls["n"] == 1, "not cached within a cycle"

    future = datetime.now(timezone.utc) + timedelta(seconds=1)
    monkeypatch.setattr(S, "current_close_cycle_start", lambda now=None: future)
    await svc._get_fundamentals("AAPL")
    assert calls["n"] == 2, "served the previous cycle's bundle from memory"
    assert S._cache.get("fundamentals:AAPL") is not None, "the refetched bundle was not cached"
    S._cache.clear()
