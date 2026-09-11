"""The CoinGecko chart path honours the interval the picker shows.

The FMP branch always passed `interval` to `fetch_chart_data`; the CoinGecko branch
resolved it and then ignored it, so "Weekly" / "Monthly" on a crypto 1Y or 2Y chart drew
daily bars under a weekly label — and the indicator pass computed MA(20) over 20 days
while the label implied 20 months. Both CoinGecko daily branches now resample through
`chart_helper._aggregate_prices`, the same helper every other asset class uses.
Close-only rows aggregate to close-only bars: open/high/low stay None, nothing invented.

Hermetic: every collaborator is stubbed.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import chart_helper as ch
from app.services import crypto_service as cs


def _daily(n: int, end: date | None = None):
    end = end or date.today()
    out = []
    for i in range(n, 0, -1):
        d = end - timedelta(days=i)
        out.append({"date": d.isoformat(), "close": 100.0 + i, "volume": 1.0e6,
                    "open": None, "high": None, "low": None})
    return out


@pytest.fixture
def svc(monkeypatch):
    cs._cache.clear()
    monkeypatch.setattr(cs, "get_supabase", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_fmp_client", lambda: None, raising=True)
    s = cs.CryptoService()

    daily = _daily(120)

    async def fake_history(symbol, days, intraday=False):
        return list(daily)

    async def fake_fundamentals(symbol):
        return {"id": "bitcoin", "name": "Bitcoin",
                "market_data": {"current_price": {"usd": 79_000.0}, "circulating_supply": 1.0,
                                "total_supply": 1.0, "max_supply": 1.0}}

    async def fake_related(symbols):
        return []

    async def fake_band(symbol):
        return (None, None)

    async def fake_snapshots(*a, **k):
        return []

    monkeypatch.setattr(s, "_cg_history", fake_history)
    monkeypatch.setattr(s, "_get_coin_fundamentals", fake_fundamentals)
    monkeypatch.setattr(s, "_cg_related_quotes", fake_related)
    monkeypatch.setattr(s, "_cg_52_week_band", fake_band)
    monkeypatch.setattr(s, "_build_snapshots", fake_snapshots)

    class _NoFMP:
        async def get_historical_prices(self, *a, **k):
            return []

    s.fmp = _NoFMP()
    yield s
    cs._cache.clear()


@pytest.mark.asyncio
async def test_weekly_on_the_detail_draws_weekly_bars(svc):
    daily = await svc.get_crypto_detail("BTC", chart_range="3M", interval="daily")
    weekly = await svc.get_crypto_detail("BTC", chart_range="3M", interval="weekly")
    assert len(daily.chart_data) > 60
    assert 10 <= len(weekly.chart_data) <= 20, len(weekly.chart_data)
    # Every weekly bar is a Monday-anchored bucket's last close; dates strictly increase.
    dates = [r["date"] for r in weekly.chart_data]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)


@pytest.mark.asyncio
async def test_monthly_on_the_detail_draws_monthly_bars(svc):
    # `monthly` is allowed on 1Y+ (`ALLOWED_INTERVALS`); the fixture holds 120 days.
    monthly = await svc.get_crypto_detail("BTC", chart_range="1Y", interval="monthly")
    assert 3 <= len(monthly.chart_data) <= 6, len(monthly.chart_data)


@pytest.mark.asyncio
async def test_a_disallowed_interval_falls_back_to_the_range_default(svc):
    """`resolve_interval` still gates: monthly is not offered on 3M, so it is daily."""
    out = await svc.get_crypto_detail("BTC", chart_range="3M", interval="monthly")
    assert len(out.chart_data) > 60


@pytest.mark.asyncio
async def test_aggregated_crypto_bars_invent_no_ohlc(svc):
    weekly = await svc.get_crypto_detail("BTC", chart_range="3M", interval="weekly")
    assert all(r.get("open") is None and r.get("high") is None and r.get("low") is None
               for r in weekly.chart_data)
    assert all(r["close"] > 0 for r in weekly.chart_data)


@pytest.mark.asyncio
async def test_the_chart_endpoint_path_honours_weekly_too(monkeypatch):
    cs._cache.clear()
    daily = _daily(120)

    class _Svc:
        async def _cg_history(self, base, days, intraday=False):
            return list(daily)

    monkeypatch.setattr(cs, "get_crypto_service", lambda: _Svc())
    weekly = await ch._fetch_crypto_chart_data("BTCUSD", "3M", "weekly")
    daily_rows = await ch._fetch_crypto_chart_data("BTCUSD", "3M", "daily")
    assert 0 < len(weekly) < len(daily_rows)
    assert all(r.get("high") is None for r in weekly)


@pytest.mark.asyncio
@pytest.mark.parametrize("range_code, interval", [("1D", "5min"), ("1W", "1hour"), ("1W", "1hour")])
async def test_intraday_ranges_are_never_aggregated(monkeypatch, range_code, interval):
    """5-minute / hourly bars are what CoinGecko serves for 1D / 1W; nothing to resample.

    Behavioural: the intraday rows must come back EXACTLY as `_cg_history` served them
    — same count, same closes — with `intraday=True` requested. (The previous version
    only asserted a local variable named `intraday_days` existed, which moving the
    aggregation above the early return would not have disturbed.)"""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bars = [{"date": (now - timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M:%S"),
             "close": 70_000.0 + i, "volume": 1.0} for i in range(300)][::-1]
    seen = {}

    class _Svc:
        async def _cg_history(self, base, days, intraday=False):
            seen["intraday"] = intraday
            seen["days"] = days
            return list(bars)

    monkeypatch.setattr(cs, "get_crypto_service", lambda: _Svc())
    out = await ch._fetch_crypto_chart_data("BTCUSD", range_code, interval)
    assert seen["intraday"] is True and seen["days"] == {"1D": 1, "1W": 7}[range_code]
    assert [r["close"] for r in out] == [r["close"] for r in bars], "intraday bars were resampled"


@pytest.mark.asyncio
async def test_a_weekly_request_on_a_daily_range_is_aggregated_but_a_daily_one_is_not(monkeypatch):
    """Control for the test above: the daily branch DOES aggregate when asked."""
    from datetime import date, timedelta
    today = date.today()
    daily = [{"date": (today - timedelta(days=i)).isoformat(), "close": 100.0 + i, "volume": 1.0}
             for i in range(60)][::-1]

    class _Svc:
        async def _cg_history(self, base, days, intraday=False):
            assert intraday is False
            return list(daily)

    monkeypatch.setattr(cs, "get_crypto_service", lambda: _Svc())
    as_daily = await ch._fetch_crypto_chart_data("BTCUSD", "3M", "daily")
    as_weekly = await ch._fetch_crypto_chart_data("BTCUSD", "3M", "weekly")
    assert len(as_daily) == 60 and 0 < len(as_weekly) < 60
