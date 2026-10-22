"""`GET /{stocks|etfs|indices|commodities|crypto}/{symbol}/chart/history` (plan B2).

Per router: the account gate and the fan-out limit, the EXCLUSIVE `before` cursor and its
format, `limit` bounds, `has_more` false only at the true first bar, an empty or failed
source answered as a typed error (never `has_more: false`, never the report-pipeline
fallback), each asset's symbol / session rule (index → proxy, commodity → ETF ref with
the regular session, FRED → no intraday, crypto → base symbol and the plan cap), and the
intraday grid (two cursors in one window share one FMP call).

Hermetic: every service the history service reaches is replaced; the auth dependency is
overridden with a fresh user id per test so the per-user limiters never collide.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.dependencies import UserIdRateLimitChecker, get_current_user_id
from app.integrations.fmp import FMPRateLimitException
from app.main import app
from app.services import price_history_service as PH

_CYCLE_START = datetime(2026, 10, 16, 22, 0, tzinfo=timezone.utc)
_LAST = date(2026, 10, 16)

_ROUTES = {
    "stock": "/api/v1/stocks/INTC/chart/history",
    "etf": "/api/v1/etfs/SPY/chart/history",
    "index": "/api/v1/indices/%5EGSPC/chart/history",
    "commodity": "/api/v1/commodities/GC/chart/history",
    "crypto": "/api/v1/crypto/BTC/chart/history",
}


def _weekdays_back(n, end=_LAST):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _daily_rows(n):
    return [{"date": d.isoformat(), "open": 10.0 + i % 7, "high": 12.0 + i % 7,
             "low": 9.0 + i % 7, "close": 11.0 + i % 7, "volume": 100.0}
            for i, d in enumerate(_weekdays_back(n))]


class _HistoryFMP:
    """FMP for a stock: daily history (newest first) and hourly intraday bars from
    `intraday_from` on, 09:30-15:30 each weekday."""

    def __init__(self, n_daily=2000, intraday_from=date(2026, 6, 1)):
        self.daily = _daily_rows(n_daily)
        self.intraday_from = intraday_from
        self.daily_calls = []
        self.intraday_calls = []

    async def get_historical_prices(self, symbol, from_date=None, to_date=None):
        self.daily_calls.append((symbol, from_date, to_date))
        rows = [r for r in self.daily if (from_date or "") <= r["date"] <= (to_date or "9999")]
        return list(reversed(rows[-5000:]))

    async def get_intraday_prices(self, symbol, **kw):
        self.intraday_calls.append((symbol, kw))
        d = max(date.fromisoformat(kw["from_date"]), self.intraday_from)
        end = date.fromisoformat(kw["to_date"])
        out = []
        while d <= end:
            if d.weekday() < 5:
                for h in range(7):
                    out.append({"date": f"{d.isoformat()} {9 + h:02d}:30:00", "open": 1.0,
                                "high": 2.0, "low": 0.5, "close": 1.5 + h, "volume": 10.0})
            d += timedelta(days=1)
        return list(reversed(out))


class _ServiceStub:
    """Stands in for the ETF / index / commodity service: its strict history read and the
    FMP client its intraday pages use."""

    def __init__(self, rows=None, exc=None):
        self.rows = _daily_rows(1500) if rows is None else rows
        self.exc = exc
        self.history_calls = []
        self.fmp = _HistoryFMP()

    async def _get_history_strict(self, symbol):
        self.history_calls.append(symbol)
        if self.exc is not None:
            raise self.exc
        return list(self.rows)


class _CryptoStub:
    def __init__(self, daily=None, hourly=None):
        today = datetime.now(timezone.utc).date()
        self.daily = daily if daily is not None else [
            {"date": (today - timedelta(days=730 - i)).isoformat(), "close": 100.0 + i,
             "volume": 5.0} for i in range(731)
        ]
        now = datetime.now(timezone.utc).replace(minute=7, second=13, microsecond=0)
        self.hourly = hourly if hourly is not None else [
            {"date": (now - timedelta(hours=2160 - i)).strftime("%Y-%m-%d %H:%M:%S"),
             "close": 200.0 + i, "volume": 1.0} for i in range(2161)
        ]
        self.calls = []

    @staticmethod
    def _history_days_cap():
        return 730

    async def _cg_history(self, symbol, days, *, intraday=False):
        self.calls.append((symbol, days, intraday))
        return list(self.hourly if intraday else self.daily)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(PH, "current_close_cycle_start", lambda now=None: _CYCLE_START)
    monkeypatch.setattr(PH, "_service", None)
    app.dependency_overrides[get_current_user_id] = lambda: f"u-{uuid.uuid4()}"
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)


@pytest.fixture
def stubs(monkeypatch):
    from app.api.v1.endpoints import stocks
    from app.services import commodity_service, crypto_service, etf_service, index_service

    s = {
        "stock": _HistoryFMP(),
        "etf": _ServiceStub(),
        "index": _ServiceStub(),
        "commodity": _ServiceStub(),
        "crypto": _CryptoStub(),
    }
    monkeypatch.setattr(stocks, "get_fmp_client", lambda: s["stock"])
    monkeypatch.setattr(etf_service, "get_etf_service", lambda: s["etf"])
    monkeypatch.setattr(index_service, "get_index_service", lambda: s["index"])
    monkeypatch.setattr(commodity_service, "get_commodity_service", lambda: s["commodity"])
    monkeypatch.setattr(crypto_service, "get_crypto_service", lambda: s["crypto"])
    return s


# ── the gates ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("asset", sorted(_ROUTES))
def test_a_signed_out_caller_is_refused_before_any_data_is_read(asset, stubs, monkeypatch):
    monkeypatch.setattr(PH, "_service", None)
    app.dependency_overrides.pop(get_current_user_id, None)
    r = TestClient(app).get(_ROUTES[asset], params={"interval": "daily", "before": "2026-01-02"})
    assert r.status_code == 401, r.text
    assert r.json()["error_code"] == "AUTH_REQUIRED"
    assert not stubs["stock"].daily_calls and not stubs["etf"].history_calls
    assert not stubs["crypto"].calls


def _route_for(path_suffix: str):
    for route in app.routes:
        if getattr(route, "path", "").endswith(path_suffix):
            return route
    raise AssertionError(f"no route ending {path_suffix}")


def _checker_buckets(dependant) -> set:
    found = set()
    for dep in dependant.dependencies:
        call = dep.call
        if isinstance(call, UserIdRateLimitChecker):
            found.add(call.bucket)
        found |= _checker_buckets(dep)
    return found


@pytest.mark.parametrize("prefix", ["stocks/{ticker}", "etfs/{symbol}", "indices/{symbol}",
                                    "commodities/{symbol}", "crypto/{symbol}"])
def test_every_history_route_carries_the_fanout_limit(prefix):
    route = _route_for(f"/api/v1/{prefix}/chart/history")
    buckets = _checker_buckets(route.dependant)
    assert "market_fanout" in buckets, buckets
    assert "market" in buckets, "the router-level market limit must still apply"


@pytest.mark.parametrize("asset", sorted(_ROUTES))
def test_an_exhausted_fanout_bucket_answers_429(asset, client, stubs, monkeypatch):
    from app import dependencies

    real = dependencies.rate_limiter.is_allowed
    monkeypatch.setattr(
        dependencies.rate_limiter, "is_allowed",
        lambda key, *a, **k: False if key.startswith("market_fanout:") else real(key, *a, **k),
    )
    r = client.get(_ROUTES[asset], params={"interval": "daily", "before": "2026-01-02"})
    assert r.status_code == 429, r.text


# ── the cursor and the page ─────────────────────────────────────────────────

@pytest.mark.parametrize("asset", ["stock", "etf", "index", "commodity"])
def test_before_is_exclusive_and_bars_come_oldest_first(asset, client, stubs):
    days = _weekdays_back(1500)
    cursor = days[1000]
    r = client.get(_ROUTES[asset], params={"interval": "daily", "before": cursor.isoformat(),
                                           "limit": 50})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"symbol", "interval", "bars", "has_more", "settled_through",
                         "reached_start"}
    dates = [b["date"] for b in body["bars"]]
    assert len(dates) == 50 and dates == sorted(dates)
    assert dates[-1] == days[999].isoformat(), "the bar AT the cursor must not repeat"
    assert dates[0] == days[950].isoformat(), "pages are a contiguous slice"
    assert body["has_more"] is True and body["interval"] == "daily"
    assert body["settled_through"] == "2026-10-16"
    assert body["reached_start"] is False


@pytest.mark.parametrize("asset", sorted(_ROUTES))
@pytest.mark.parametrize("before", ["2026/01/02", "2026-13-01", "2026-01-02 10:00:00",
                                    "yesterday", "2999-01-01"])
def test_a_malformed_or_future_daily_cursor_is_a_422(asset, before, client, stubs):
    r = client.get(_ROUTES[asset], params={"interval": "daily", "before": before})
    assert r.status_code == 422, r.text
    assert r.json()["error_code"] == "INVALID_INPUT"


@pytest.mark.parametrize("asset", ["stock", "etf", "index", "commodity", "crypto"])
def test_an_intraday_cursor_must_be_a_full_timestamp(asset, client, stubs):
    r = client.get(_ROUTES[asset], params={"interval": "1hour", "before": "2026-10-14"})
    assert r.status_code == 422, r.text
    assert r.json()["error_code"] == "INVALID_INPUT"


@pytest.mark.parametrize("asset", sorted(_ROUTES))
@pytest.mark.parametrize("limit", [0, -1, 5001])
def test_limit_is_bounded(asset, limit, client, stubs):
    r = client.get(_ROUTES[asset], params={"interval": "daily", "before": "2026-01-02",
                                           "limit": limit})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("asset,bad", [("stock", "4hour"), ("etf", "quarterly"),
                                       ("crypto", "5min"), ("crypto", "15min"),
                                       ("index", "1min")])
def test_an_unpageable_interval_is_a_422(asset, bad, client, stubs):
    r = client.get(_ROUTES[asset], params={"interval": bad, "before": "2026-01-02"})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("asset", ["stock", "etf", "index", "commodity"])
def test_has_more_is_false_only_where_the_page_reaches_the_first_bar(asset, client, stubs):
    days = _weekdays_back(1500 if asset != "stock" else 2000)
    first = days[0].isoformat()
    cursor = days[30].isoformat()
    short = client.get(_ROUTES[asset], params={"interval": "daily", "before": cursor,
                                               "limit": 10}).json()
    assert short["has_more"] is True and len(short["bars"]) == 10
    assert short["reached_start"] is False
    reach = client.get(_ROUTES[asset], params={"interval": "daily", "before": cursor,
                                               "limit": 500}).json()
    assert reach["has_more"] is False and reach["bars"][0]["date"] == first
    assert len(reach["bars"]) == 30
    # The source's first row IS the symbol's first bar: the client may show it.
    assert reach["reached_start"] is True
    past = client.get(_ROUTES[asset], params={"interval": "daily", "before": first}).json()
    assert past == {**past, "bars": [], "has_more": False, "reached_start": True}


@pytest.mark.parametrize("asset", ["etf", "index", "commodity"])
def test_an_empty_source_is_a_typed_error_never_the_start_of_history(asset, client, stubs):
    stubs[asset].rows = []
    r = client.get(_ROUTES[asset], params={"interval": "daily", "before": "2026-01-02"})
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["error_code"] == "FMP_UNAVAILABLE"
    assert "has_more" not in body


def test_an_empty_stock_or_crypto_source_is_a_typed_error(client, stubs):
    stubs["stock"].daily = []
    r = client.get(_ROUTES["stock"], params={"interval": "daily", "before": "2026-01-02"})
    assert r.status_code == 502 and r.json()["error_code"] == "FMP_UNAVAILABLE", r.text
    stubs["crypto"].daily = []
    r = client.get(_ROUTES["crypto"], params={"interval": "daily", "before": "2026-01-02"})
    assert r.status_code == 502 and r.json()["error_code"] == "COINGECKO_UNAVAILABLE", r.text


@pytest.mark.parametrize("asset", ["etf", "index", "commodity"])
def test_a_non_fmp_error_is_never_the_report_pipeline_code(asset, client, stubs):
    stubs[asset].exc = RuntimeError("something unexpected")
    r = client.get(_ROUTES[asset], params={"interval": "weekly", "before": "2026-01-02"})
    body = r.json()
    assert body["error_code"] == "FMP_UNAVAILABLE", body
    assert body["error_code"] != "REPORT_GENERATION_FAILED"


def test_a_known_upstream_failure_keeps_its_own_code(client, stubs):
    stubs["etf"].exc = FMPRateLimitException("429 from FMP", retry_after="30")
    r = client.get(_ROUTES["etf"], params={"interval": "daily", "before": "2026-01-02"})
    assert r.json()["error_code"] == "FMP_RATE_LIMITED", r.text


def test_a_withdrawn_commodity_is_refused_with_the_contractual_code(client, stubs):
    r = client.get("/api/v1/commodities/KC/chart/history",
                   params={"interval": "daily", "before": "2026-01-02"})
    assert r.json()["error_code"] == "FMP_NOT_ENTITLED", r.text
    assert not stubs["commodity"].history_calls


# ── per-asset symbol and session rules ──────────────────────────────────────

def test_an_index_pages_its_proxy(client, stubs):
    r = client.get(_ROUTES["index"], params={"interval": "1hour",
                                             "before": "2026-10-14 10:30:00"})
    assert r.status_code == 200, r.text
    assert r.json()["symbol"] == "^GSPC"
    assert {c[0] for c in stubs["index"].fmp.intraday_calls} == {"SPY"}
    daily = client.get(_ROUTES["index"], params={"interval": "daily", "before": "2026-01-02"})
    assert daily.status_code == 200
    assert stubs["index"].history_calls == ["^GSPC"], "the index service maps the proxy itself"


def test_a_commodity_pages_its_etf_ref_on_the_regular_session(client, stubs):
    r = client.get(_ROUTES["commodity"], params={"interval": "1hour",
                                                 "before": "2026-10-14 10:30:00"})
    assert r.status_code == 200, r.text
    calls = stubs["commodity"].fmp.intraday_calls
    assert {c[0] for c in calls} == {"GLD"}
    assert all("extended" not in kw for _sym, kw in calls)
    client.get(_ROUTES["commodity"], params={"interval": "daily", "before": "2026-01-02"})
    assert stubs["commodity"].history_calls == ["GCUSD"]


def test_a_fred_commodity_has_no_intraday_page_and_says_so(client, stubs):
    r = client.get("/api/v1/commodities/CL/chart/history",
                   params={"interval": "1hour", "before": "2026-10-14 10:30:00"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bars"] == [] and body["has_more"] is False
    assert body["reached_start"] is False
    assert not stubs["commodity"].fmp.intraday_calls


def test_an_etf_ignores_extended_hours_and_a_stock_honours_it(client, stubs):
    client.get(_ROUTES["etf"], params={"interval": "1hour", "before": "2026-10-14 10:30:00",
                                       "extended_hours": "true"})
    assert all("extended" not in kw for _s, kw in stubs["etf"].fmp.intraday_calls)
    client.get(_ROUTES["stock"], params={"interval": "1hour", "before": "2026-10-14 10:30:00",
                                         "extended_hours": "true"})
    assert stubs["stock"].intraday_calls
    assert all(kw.get("extended") is True for _s, kw in stubs["stock"].intraday_calls)
    assert all(kw.get("strict") is True for _s, kw in stubs["stock"].intraday_calls)


def test_crypto_daily_pages_end_at_the_plan_cap(client, stubs):
    series = stubs["crypto"].daily
    cursor = series[20]["date"]
    body = client.get(_ROUTES["crypto"], params={"interval": "daily", "before": cursor}).json()
    assert body["has_more"] is False and body["bars"][0]["date"] == series[0]["date"]
    assert len(body["bars"]) == 20 and body["settled_through"] is None
    assert ("BTC", 730, False) in stubs["crypto"].calls
    # The plan cap stops paging but is not the coin's first bar: its leftmost ~210 bars
    # are warm-up, so the client keeps its indicator floor.
    assert body["reached_start"] is False


def test_crypto_hourly_pages_keep_hourly_spacing_at_the_seam(client, stubs):
    hourly = stubs["crypto"].hourly
    before = hourly[1000]["date"]
    # A range bar a few minutes after a 90-day-series point: that point is NOT returned.
    seam = (datetime.strptime(before, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=10))
    body = client.get(_ROUTES["crypto"], params={
        "interval": "1hour", "before": seam.strftime("%Y-%m-%d %H:%M:%S"), "limit": 24,
    }).json()
    assert body["interval"] == "1hour"
    assert body["bars"][-1]["date"] == hourly[999]["date"], "a near-duplicate hour leaked in"
    assert len(body["bars"]) == 24 and body["has_more"] is True
    assert body["reached_start"] is False
    assert ("BTC", 90, True) in stubs["crypto"].calls


def test_a_crypto_pair_on_the_stocks_route_is_served_from_coingecko_hourly(client, stubs):
    hourly = stubs["crypto"].hourly
    r = client.get("/api/v1/stocks/BTCUSD/chart/history",
                   params={"interval": "15min", "before": hourly[500]["date"], "limit": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "BTCUSD" and body["interval"] == "1hour"
    assert not stubs["stock"].daily_calls and not stubs["stock"].intraday_calls


# ── the intraday grid ───────────────────────────────────────────────────────

def test_two_cursors_in_one_window_share_one_fmp_call(client, stubs):
    start = PH._window_start(date(2026, 9, 10), "1hour")
    a = start + timedelta(days=20)
    b = start + timedelta(days=30)
    for d in (a, b):
        while d.weekday() >= 5:
            d += timedelta(days=1)
    r1 = client.get(_ROUTES["stock"], params={"interval": "1hour", "limit": 3,
                                              "before": f"{a.isoformat()} 12:30:00"})
    r2 = client.get(_ROUTES["stock"], params={"interval": "1hour", "limit": 3,
                                              "before": f"{b.isoformat()} 14:30:00"})
    assert r1.status_code == r2.status_code == 200
    assert len(stubs["stock"].intraday_calls) == 1, stubs["stock"].intraday_calls


def test_intraday_pages_end_after_empty_windows_and_never_stall_a_cursor(client, stubs):
    fmp = stubs["stock"]
    first_day = fmp.intraday_from
    body = client.get(_ROUTES["stock"], params={
        "interval": "1hour", "before": f"{first_day.isoformat()} 11:30:00", "limit": 1000,
    }).json()
    assert body["bars"][0]["date"] == f"{first_day.isoformat()} 09:30:00"
    assert body["has_more"] is False, "two empty windows end the intraday series"
    assert body["reached_start"] is False, "the provider's depth is not a listing date"
    # Paging from the first bar itself: nothing older, and a definitive end.
    again = client.get(_ROUTES["stock"], params={
        "interval": "1hour", "before": body["bars"][0]["date"],
    }).json()
    assert again["bars"] == [] and again["has_more"] is False


def test_a_recent_listings_first_intraday_bar_is_the_start(client, stubs):
    """Intraday paging that ends on empty windows over a stock whose DAILY history begins on
    the same day has reached the listing: the chart may show those first bars."""
    fmp = stubs["stock"]
    first_day = fmp.intraday_from
    fmp.daily = [r for r in fmp.daily if r["date"] >= first_day.isoformat()]
    body = client.get(_ROUTES["stock"], params={
        "interval": "1hour", "before": f"{first_day.isoformat()} 11:30:00", "limit": 1000,
    }).json()
    assert body["has_more"] is False and body["bars"][0]["date"].startswith(first_day.isoformat())
    assert body["reached_start"] is True


def test_the_look_back_bound_is_never_a_listing_and_costs_no_daily_read(client, stubs, monkeypatch):
    """Paging that stops at the intraday look-back bound is the provider's depth: it answers
    reached_start false without reading the daily history (only an end on EMPTY windows can
    be a listing, and only that end pays for the check)."""
    monkeypatch.setitem(PH._INTRADAY_MAX_LOOKBACK_DAYS, "1hour", 20)
    today = datetime.now(timezone.utc).date()
    before = today - timedelta(days=5)
    while before.weekday() >= 5:
        before -= timedelta(days=1)
    body = client.get(_ROUTES["stock"], params={
        "interval": "1hour", "before": f"{before.isoformat()} 11:30:00", "limit": 1000,
    }).json()
    assert body["has_more"] is False and body["reached_start"] is False
    assert not stubs["stock"].daily_calls, stubs["stock"].daily_calls


def test_a_failed_listing_check_keeps_the_intraday_floor(client, stubs):
    """The daily read behind the listing check failing must not fail the page, and must
    answer reached_start false (the chart keeps its warm-up floor), never a guess."""
    fmp = stubs["stock"]
    first_day = fmp.intraday_from
    fmp.daily = []
    r = client.get(_ROUTES["stock"], params={
        "interval": "1hour", "before": f"{first_day.isoformat()} 11:30:00", "limit": 1000,
    })
    assert r.status_code == 200, r.text
    assert r.json()["has_more"] is False and r.json()["reached_start"] is False


def test_an_intraday_page_with_has_more_always_carries_a_bar(client, stubs):
    body = client.get(_ROUTES["stock"], params={
        "interval": "5min", "before": "2026-10-14 09:30:00", "limit": 2,
    }).json()
    assert body["has_more"] is True and len(body["bars"]) == 2


def test_a_page_never_hands_back_its_own_cursor_with_has_more(client, stubs, monkeypatch):
    """With more empty windows allowed than one page's window budget, a page holding no
    bars must keep scanning to a definite end — `bars: [], has_more: true` would return
    the client its own cursor forever."""
    monkeypatch.setattr(PH, "_INTRADAY_EMPTY_WINDOWS_TO_END", 5)
    first_day = stubs["stock"].intraday_from
    body = client.get(_ROUTES["stock"], params={
        "interval": "1hour", "before": f"{first_day.isoformat()} 09:30:00",
    }).json()
    assert not (body["bars"] == [] and body["has_more"]), body
    assert body == {**body, "bars": [], "has_more": False}


def test_rounding_a_page_never_rewrites_the_cached_window(client, stubs):
    """An index rounds its intraday closes to 2 dp; the window rows it shares are the
    cache's own and must stay exact."""
    fmp = stubs["index"].fmp
    real = fmp.get_intraday_prices

    async def _precise(symbol, **kw):
        return [{**r, "close": 1.23456} for r in await real(symbol, **kw)]

    fmp.get_intraday_prices = _precise
    body = client.get(_ROUTES["index"], params={"interval": "1hour", "limit": 5,
                                                "before": "2026-10-14 10:30:00"}).json()
    assert {b["close"] for b in body["bars"]} == {1.23}
    svc = PH.get_price_history_service()
    cached = [e for e in svc._windows._items.values()]
    assert cached, "the window was not cached"
    assert all(r["close"] == 1.23456 for entry, _n in cached for r in entry.value)


# ── B4: the light crypto chart route and the typed /stocks chart error ──────

def _real_crypto_service(monkeypatch, cg_history):
    from app.api.v1.endpoints import crypto as crypto_ep
    from app.services import crypto_service as cs

    svc = cs.CryptoService.__new__(cs.CryptoService)
    svc._cg_history = cg_history
    monkeypatch.setattr(crypto_ep, "get_crypto_service", lambda: svc)
    return svc


def test_the_crypto_chart_route_serves_one_range_including_2y(client, monkeypatch):
    stub = _CryptoStub()
    _real_crypto_service(monkeypatch, stub._cg_history)
    r = client.get("/api/v1/crypto/BTCUSD/chart", params={"range": "2Y", "interval": "weekly"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"symbol", "prices"} and body["symbol"] == "BTC"
    assert body["prices"] and set(body["prices"][0]) == {"date", "open", "high", "low",
                                                         "close", "volume"}
    one_week = client.get("/api/v1/crypto/BTC/chart", params={"range": "1W"}).json()
    assert ("BTC", 16, True) in stub.calls, "1W asks for its visible week plus the warm-up"
    assert one_week["prices"]


@pytest.mark.parametrize("exc,code", [
    ("unavailable", "COINGECKO_UNAVAILABLE"),
    ("ratelimit", "COINGECKO_RATE_LIMITED"),
    ("runtime", "COINGECKO_UNAVAILABLE"),
])
def test_a_crypto_chart_failure_is_typed(client, monkeypatch, exc, code):
    from app.integrations.coingecko import (
        CoinGeckoRateLimitException,
        CoinGeckoUnavailableException,
    )

    errors = {
        "unavailable": CoinGeckoUnavailableException("503 from CoinGecko"),
        "ratelimit": CoinGeckoRateLimitException("429 from CoinGecko"),
        "runtime": RuntimeError("unexpected"),
    }

    async def _boom(symbol, days, *, intraday=False):
        raise errors[exc]

    _real_crypto_service(monkeypatch, _boom)
    r = client.get("/api/v1/crypto/BTC/chart", params={"range": "3M"})
    body = r.json()
    assert body["error_code"] == code, body
    assert {"error_code", "message", "user_message"} <= set(body)


def test_an_unclassified_stock_chart_failure_is_typed_not_a_bare_502(client, monkeypatch):
    from app.api.v1.endpoints import stocks

    class _Broken:
        async def get_historical_prices(self, *a, **k):
            raise RuntimeError("something unexpected")

    monkeypatch.setattr(stocks, "get_fmp_client", lambda: _Broken())
    r = client.get("/api/v1/stocks/ZZZZ/chart", params={"range": "3M"})
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["error_code"] == "FMP_UNAVAILABLE", body
    assert "detail" not in body, "the bare HTTPException body is gone"


def test_the_stock_chart_route_answers_the_documented_shape(client, stubs):
    r = client.get("/api/v1/stocks/INTC/chart", params={"range": "5Y", "interval": "monthly"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"symbol", "prices"} and body["symbol"] == "INTC"
    assert body["prices"] and set(body["prices"][0]) == {"date", "open", "high", "low",
                                                         "close", "volume"}
