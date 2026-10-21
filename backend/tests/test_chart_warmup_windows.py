"""Indicator warm-up and window completeness for the shared chart fetcher (plan B3).

  * intraday: ≥ 210 bars of warm-up before the range at every pageable bar size (a flat
    7 days left MA(200) blank on 15m/30m/1h), while 1min/5min keep their 7-day window;
  * stock 5Y monthly is no longer cut at FMP's 5,000-row cap (MA(200) needs ~22 years);
  * a weekly/monthly chart's OLDEST bucket is complete (bucketed from the full daily
    history, then windowed — never from a mid-week window start);
  * crypto 1W carries ≥ 199 hourly warm-up bars; crypto weekly is bucketed from the full
    730-day series; the detail, the light chart route and the shared fetcher draw the
    SAME rows;
  * FRED commodities read 12,000 observations (CL/NG back to their first print).

Hermetic: fake FMP / CoinGecko / FRED clients only.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import chart_helper as CH
from app.services import price_history_service as PH
from app.utils.market_hours import US_MARKET_EARLY_CLOSES, is_trading_day

# Bars per session, written out independently of `chart_helper._session_bars`.
_REGULAR = {"15min": 26, "30min": 13, "1hour": 7}
_REGULAR_EARLY = {"15min": 14, "30min": 7, "1hour": 4}
_EXTENDED = {"15min": 64, "30min": 32, "1hour": 16}
_EXTENDED_EARLY = {"15min": 52, "30min": 26, "1hour": 13}


def _bars_between(start: date, end: date, interval: str, extended: bool) -> int:
    """Bars FMP serves for sessions in [start, end)."""
    total, d = 0, start
    while d < end:
        if is_trading_day(d):
            early = (d.year, d.month, d.day) in US_MARKET_EARLY_CLOSES
            table = (_EXTENDED_EARLY if early else _EXTENDED) if extended else (
                _REGULAR_EARLY if early else _REGULAR)
            total += table[interval]
        d += timedelta(days=1)
    return total


@pytest.fixture(autouse=True)
def _fresh_history_service(monkeypatch):
    monkeypatch.setattr(PH, "_service", None)


@pytest.mark.parametrize("interval", ["15min", "30min", "1hour"])
@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("range_start", [
    date(2026, 10, 17),   # a Saturday (1D's "today - 3 days" lands anywhere)
    date(2026, 11, 30),   # just after Thanksgiving and its half-day
    date(2026, 12, 28),   # just after the Christmas Eve half-day
    date(2027, 1, 4),     # over the New Year closure
])
def test_intraday_warmup_holds_210_bars_before_the_range(interval, extended, range_start):
    start = CH._intraday_warmup_from(range_start, interval, extended)
    assert start <= range_start - timedelta(days=7), "the 7-day floor was dropped"
    bars = _bars_between(start, range_start, interval, extended)
    assert bars >= 210, f"{interval} ext={extended}: only {bars} warm-up bars"


@pytest.mark.parametrize("interval", ["1min", "5min"])
def test_the_short_intervals_keep_their_seven_day_window(interval):
    for start in (date(2026, 10, 17), date(2026, 11, 30), date(2026, 12, 28)):
        assert CH._intraday_warmup_from(start, interval, False) == start - timedelta(days=7)


class _RecordingIntraday:
    def __init__(self):
        self.calls = []

    async def get_intraday_prices(self, symbol, **kw):
        self.calls.append(kw)
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("range_code,interval", [("1D", "1hour"), ("1D", "15min"),
                                                 ("1W", "30min"), ("1W", "1hour")])
async def test_fetch_chart_data_asks_fmp_for_the_bar_count_warmup(range_code, interval):
    fmp = _RecordingIntraday()
    await CH.fetch_chart_data(fmp, "NVDA", range_code, interval)
    asked = date.fromisoformat(fmp.calls[-1]["from_date"])
    range_start = date.fromisoformat(CH.compute_date_range(range_code)[0])
    assert asked == CH._intraday_warmup_from(range_start, interval, False)
    assert _bars_between(asked, range_start, interval, False) >= 210


@pytest.mark.asyncio
async def test_the_five_minute_chart_window_is_unchanged():
    fmp = _RecordingIntraday()
    await CH.fetch_chart_data(fmp, "NVDA", "1D", "5min")
    range_start = date.fromisoformat(CH.compute_date_range("1D")[0])
    assert fmp.calls[-1]["from_date"] == (range_start - timedelta(days=7)).isoformat()


# ── stock weekly / monthly come from the full settled history ────────────────

class _PagedDaily:
    """A weekday history ending yesterday, newest first, 5,000 rows a page."""

    def __init__(self, years: float, volume=1.0):
        end = datetime.now(timezone.utc).date() - timedelta(days=1)
        n = int(years * 261)
        days, d = [], end
        while len(days) < n:
            if d.weekday() < 5:
                days.append(d)
            d -= timedelta(days=1)
        days.reverse()
        self.rows = [{"date": x.isoformat(), "open": 10.0 + i % 9, "high": 20.0, "low": 5.0,
                      "close": 11.0 + i % 9, "volume": volume} for i, x in enumerate(days)]
        self.calls = []

    async def get_historical_prices(self, symbol, from_date=None, to_date=None):
        self.calls.append((from_date, to_date))
        rows = [r for r in self.rows if (from_date or "") <= r["date"] <= (to_date or "9999")]
        return list(reversed(rows[-5000:]))


@pytest.mark.asyncio
async def test_five_year_monthly_is_not_cut_at_the_row_cap():
    fmp = _PagedDaily(26)                      # ~6,800 rows: two FMP pages
    bars = await CH.fetch_chart_data(fmp, "AAPL", "5Y", "monthly")
    window_start = CH.compute_date_range("5Y")[0]
    warmup = [b for b in bars if b["date"] < window_start]
    assert len(warmup) >= 200, f"only {len(warmup)} monthly bars before the 5Y window"
    assert len(fmp.calls) == 2, fmp.calls


@pytest.mark.asyncio
async def test_the_overview_five_year_monthly_chart_is_the_same_untruncated_series():
    """`/overview` builds non-daily ranges through the same fetcher."""
    from app.services.stock_overview_service import StockOverviewService
    from tests._price_fakes import PriceFromFMPFake

    fmp = _PagedDaily(26)
    svc = StockOverviewService.__new__(StockOverviewService)
    svc.fmp = fmp
    svc.price = PriceFromFMPFake(fmp)
    vol = await svc._get_volatile("AAPL", "5Y", "monthly", False)
    window_start = CH.compute_date_range("5Y")[0]
    assert len([b for b in vol["chart_data"] if b["date"] < window_start]) >= 200


@pytest.mark.asyncio
@pytest.mark.parametrize("range_code,interval", [("1Y", "weekly"), ("6M", "weekly"),
                                                 ("2Y", "monthly")])
async def test_the_oldest_bucket_on_the_chart_is_complete(range_code, interval):
    fmp = _PagedDaily(12, volume=1.0)          # every session's volume is 1
    bars = await CH.fetch_chart_data(fmp, "AAPL", range_code, interval)
    first = date.fromisoformat(bars[0]["date"])
    if interval == "weekly":
        bucket_start = first - timedelta(days=first.weekday())
        bucket_end = bucket_start + timedelta(days=6)
    else:
        bucket_start = first.replace(day=1)
        bucket_end = first
    sessions = [r for r in fmp.rows if bucket_start.isoformat() <= r["date"] <= bucket_end.isoformat()]
    assert bars[0]["volume"] == float(len(sessions)), (
        f"the first {interval} bucket holds {bars[0]['volume']} of its {len(sessions)} sessions"
    )
    assert bars[0]["open"] == sessions[0]["open"], "the bucket's open is not its first session's"


# ── crypto ───────────────────────────────────────────────────────────────────

class _CGStub:
    """`_cg_history`: hourly points for `days` (intraday) or one daily row per day."""

    def __init__(self, daily_start_offset: int = 0):
        self.calls = []
        self.now = datetime.now(timezone.utc).replace(minute=11, second=0, microsecond=0)
        # Days the daily series starts AFTER the 730-day cap (0-2 still reads as capped).
        self.daily_start_offset = daily_start_offset

    @staticmethod
    def _history_days_cap():
        return 730

    async def _cg_history(self, symbol, days, *, intraday=False):
        self.calls.append((symbol, days, intraday))
        if intraday:
            return [{"date": (self.now - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S"),
                     "close": 60_000.0 + h * 3.7, "volume": 9.0} for h in range(days * 24, -1, -1)]
        today = self.now.date()
        return [{"date": (today - timedelta(days=k)).isoformat(), "close": 50_000.0 + k * 1.3,
                 "volume": 7.0}
                for k in range(min(days, 730) - self.daily_start_offset, -1, -1)]


@pytest.mark.asyncio
async def test_crypto_one_week_carries_199_warmup_bars(monkeypatch):
    from app.services import crypto_service as cs

    stub = _CGStub()
    monkeypatch.setattr(cs, "get_crypto_service", lambda: stub)
    rows = await CH._fetch_crypto_chart_data("BTCUSD", "1W", "1hour")
    visible_from = (stub.now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    warmup = [r for r in rows if r["date"] < visible_from]
    assert len(warmup) >= 199, len(warmup)
    assert stub.calls[-1] == ("BTC", 16, True)


@pytest.mark.asyncio
async def test_crypto_weekly_is_bucketed_from_the_whole_capped_series(monkeypatch):
    from app.services import crypto_service as cs

    # Start the capped series mid-week whatever weekday today is, so its first week is
    # PARTIAL — the case the drop exists for (a Monday start would make it vacuous).
    today = datetime.now(timezone.utc).date()
    offset = next(k for k in (0, 1, 2) if (today - timedelta(days=730 - k)).weekday() != 0)
    stub = _CGStub(daily_start_offset=offset)
    monkeypatch.setattr(cs, "get_crypto_service", lambda: stub)
    weekly = await CH._fetch_crypto_chart_data("BTCUSD", "3M", "weekly")
    assert stub.calls[-1] == ("BTC", 730, False)
    assert len(weekly) >= 100, f"{len(weekly)} weekly bars from 730 days"
    first = date.fromisoformat(weekly[0]["date"])
    assert weekly[0]["volume"] == 7.0 * 7, "the oldest weekly bucket is incomplete"
    assert first.weekday() == 6, "a complete crypto week ends on Sunday"


@pytest.fixture
def crypto_svc(monkeypatch):
    """A real CryptoService with every collaborator stubbed (the
    `test_crypto_chart_interval_honoured` fixture), sharing one `_CGStub` series."""
    from app.services import crypto_service as cs

    cs._cache.clear()
    monkeypatch.setattr(cs, "get_supabase", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_fmp_client", lambda: None, raising=True)
    svc = cs.CryptoService()
    stub = _CGStub()

    async def fake_fundamentals(symbol):
        return {"id": "bitcoin", "name": "Bitcoin",
                "market_data": {"current_price": {"usd": 60_000.0}, "circulating_supply": 1.0,
                                "total_supply": 1.0, "max_supply": 1.0}}

    async def _none(*a, **k):
        return []

    async def fake_band(symbol):
        return (None, None)

    monkeypatch.setattr(svc, "_cg_history", stub._cg_history)
    monkeypatch.setattr(svc, "_get_coin_fundamentals", fake_fundamentals)
    monkeypatch.setattr(svc, "_cg_related_quotes", _none)
    monkeypatch.setattr(svc, "_cg_52_week_band", fake_band)
    monkeypatch.setattr(svc, "_build_snapshots", _none)

    class _NoFMP:
        async def get_historical_prices(self, *a, **k):
            return []

    svc.fmp = _NoFMP()
    monkeypatch.setattr(cs, "get_crypto_service", lambda: svc)
    yield svc
    cs._cache.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("range_code,interval", [("1W", "1hour"), ("3M", "daily"),
                                                 ("1Y", "weekly"), ("2Y", "monthly"),
                                                 ("1D", "5min")])
async def test_the_three_crypto_surfaces_draw_identical_rows(crypto_svc, range_code, interval):
    detail = await crypto_svc.get_crypto_detail("BTC", chart_range=range_code, interval=interval)
    light = await crypto_svc.get_crypto_chart("BTC", chart_range=range_code, interval=interval)
    shared = await CH._fetch_crypto_chart_data(
        "BTCUSD", range_code, CH.resolve_interval(range_code, interval)
    )
    assert detail.chart_data, f"{range_code} {interval} drew nothing"
    assert detail.chart_data == light["prices"] == shared


# ── FRED ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fred_commodities_read_twelve_thousand_observations(monkeypatch):
    from app.integrations import fred
    from app.services import commodity_service as CS

    assert CS._FRED_OBSERVATION_LIMIT == 12000
    seen = []

    class _Fred:
        is_configured = True

        async def get_observations(self, series_id, limit=None, **kw):
            seen.append((series_id, limit))
            return []

    monkeypatch.setattr(fred, "get_fred_client", lambda: _Fred())
    svc = CS.CommodityService.__new__(CS.CommodityService)
    await svc._fred_observations("DCOILWTICO")
    assert seen == [("DCOILWTICO", 12000)]
