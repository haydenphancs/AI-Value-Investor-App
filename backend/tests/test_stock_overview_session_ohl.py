"""Key Statistics — Open / Day High / Day Low (TestFlight E4, build 1.0 (8)).

`/stable/quote` is unlicensed and the profile-backed quote row that replaced it carries no
`open` / `dayHigh` / `dayLow`, so the three rows read "—" for every stock, all day. The
overview now derives them from the two entitled sources — the session's EOD row
(`historical-price-eod/full`, date-gated) and the session's own regular-hours bars — for
the session the header price describes.

Pure helpers over plain dicts, then the real service with `svc.fmp` faked (the seam every
overview test uses) and the clock patched at the binding `stock_overview_service` reads.
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from app.services import stock_overview_service as sos
from app.services.stock_overview_service import (
    StockOverviewService,
    _cache,
    _ohl_fields,
    _session_ohl_from_bars,
    _session_ohl_from_eod,
)

TODAY = "2026-09-16"
YESTERDAY = "2026-09-15"


def _row(d, o=332.53, h=335.48, l=330.70, c=332.41):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": 1}


def _bar(t, o=1.0, h=1.0, l=1.0, day=TODAY):
    return {"date": f"{day} {t}:00", "open": o, "high": h, "low": l, "close": o, "volume": 1}


# ── EOD row ──────────────────────────────────────────────────────────────────

def test_todays_row_yields_open_high_low():
    out = _session_ohl_from_eod([_row(TODAY), _row(YESTERDAY, 300, 301, 299)], TODAY)
    assert out == {"open": 332.53, "dayHigh": 335.48, "dayLow": 330.70}


def test_a_previous_session_row_is_not_todays_range():
    """The T-1 row must never be printed under today's labels."""
    assert _session_ohl_from_eod([_row(YESTERDAY)], TODAY) == {}


def test_legacy_dict_shape_is_accepted():
    assert _session_ohl_from_eod({"historical": [_row(TODAY)]}, TODAY)["open"] == 332.53


def test_non_finite_and_non_positive_fields_are_omitted_not_zeroed():
    assert _session_ohl_from_eod([_row(TODAY, o=float("nan"), h=0, l=-1)], TODAY) == {}
    out = _session_ohl_from_eod([_row(TODAY, o=float("nan"))], TODAY)
    assert set(out) == {"dayHigh", "dayLow"}
    assert all(math.isfinite(v) for v in out.values())


def test_inverted_range_is_dropped():
    out = _session_ohl_from_eod([_row(TODAY, o=315.0, h=310.0, l=320.0)], TODAY)
    assert "dayHigh" not in out and "dayLow" not in out


def test_an_open_outside_the_range_is_dropped_but_the_range_kept():
    out = _session_ohl_from_eod([_row(TODAY, o=400.0, h=335.48, l=330.70)], TODAY)
    assert out == {"dayHigh": 335.48, "dayLow": 330.70}


@pytest.mark.parametrize("rows", [[], [{}], [{"date": None}], None, "garbage", {"historical": "x"}])
def test_empty_and_malformed_eod_rows(rows):
    assert _session_ohl_from_eod(rows, TODAY) == {}


def test_ohl_fields_never_emit_zero_as_a_fact():
    assert _ohl_fields(0, 0, 0) == {}
    assert _ohl_fields("abc", None, float("inf")) == {}


# ── intraday bars ────────────────────────────────────────────────────────────

def test_bars_use_the_first_regular_bar_as_open_and_the_session_extremes():
    bars = [
        _bar("04:00", o=1.0, h=50.0, l=0.5),      # pre-market: excluded from all three
        _bar("09:30", o=10.0, h=12.0, l=9.5),
        _bar("12:00", o=11.0, h=13.0, l=9.0),
        _bar("15:55", o=11.5, h=12.5, l=11.0),
        _bar("16:00", o=11.5, h=99.0, l=0.1),     # after-hours: excluded
        _bar("10:00", o=10.5, h=10.8, l=10.2, day=YESTERDAY),
    ]
    assert _session_ohl_from_bars(bars, TODAY) == {"open": 10.0, "dayHigh": 13.0, "dayLow": 9.0}


def test_bars_are_sorted_before_picking_the_open():
    bars = [_bar("12:00", o=11.0, h=11.5, l=10.5), _bar("09:30", o=10.0, h=10.4, l=9.8)]
    assert _session_ohl_from_bars(bars, TODAY)["open"] == 10.0


def test_a_single_bar_session():
    assert _session_ohl_from_bars([_bar("09:30", o=10.0, h=10.4, l=9.9)], TODAY) == {
        "open": 10.0, "dayHigh": 10.4, "dayLow": 9.9,
    }


def test_bars_with_nan_high_fall_back_to_the_finite_ones():
    bars = [_bar("09:30", o=10.0, h=float("nan"), l=9.5), _bar("09:35", o=10.1, h=10.6, l=9.7)]
    assert _session_ohl_from_bars(bars, TODAY) == {"open": 10.0, "dayHigh": 10.6, "dayLow": 9.5}


@pytest.mark.parametrize("bars", [[], None, "x", [{"date": TODAY, "open": 1}], [_bar("04:00")], [_bar("09:30", day=YESTERDAY)]])
def test_bars_without_a_regular_session_print_give_nothing(bars):
    assert _session_ohl_from_bars(bars, TODAY) == {}


# ── the rows render through the real key-statistics builder ─────────────────

def _stats(quote):
    svc = StockOverviewService.__new__(StockOverviewService)
    _flat, groups = svc._build_key_statistics(quote, {}, [], [], quote["price"], income_quarterly=[])
    return {item.label: item.value for g in groups for item in g.statistics}


def test_key_statistics_render_the_merged_range():
    s = _stats({"price": 332.41, "open": 332.53, "dayHigh": 335.48, "dayLow": 330.70})
    assert (s["Open"], s["Day High"], s["Day Low"]) == ("332.53", "335.48", "330.70")


def test_key_statistics_keep_the_dash_without_the_keys():
    s = _stats({"price": 332.41})
    assert (s["Open"], s["Day High"], s["Day Low"]) == ("—", "—", "—")


# ── the service: sources, gates, caching ────────────────────────────────────

class _FMP:
    def __init__(self, eod=None, bars=None, raise_eod=None):
        self.eod, self.bars, self.raise_eod = eod, bars, raise_eod
        self.eod_calls, self.bar_calls = [], []

    async def get_historical_prices(self, symbol, from_date, to_date):
        self.eod_calls.append((symbol, from_date, to_date))
        if self.raise_eod:
            raise self.raise_eod
        return self.eod or []

    async def get_intraday_prices(self, symbol, interval="5min", from_date=None, to_date=None, extended=False):
        self.bar_calls.append((symbol, interval, from_date, to_date))
        return self.bars or []


def _svc(fmp) -> StockOverviewService:
    _cache.clear()
    svc = StockOverviewService.__new__(StockOverviewService)
    svc.fmp = fmp
    return svc


def _clock(monkeypatch, phase, session=date(2026, 9, 16)):
    monkeypatch.setattr(sos, "session_phase", lambda now=None: phase)
    monkeypatch.setattr(sos, "session_trading_date", lambda now=None: session)


@pytest.mark.asyncio
async def test_after_the_close_the_eod_row_is_used_over_a_short_window(monkeypatch):
    _clock(monkeypatch, sos.SESSION_AFTERHOURS)
    fmp = _FMP(eod=[_row(TODAY)])
    out = await _svc(fmp)._get_session_ohl("AAPL")
    assert out == {"open": 332.53, "dayHigh": 335.48, "dayLow": 330.70}
    assert fmp.eod_calls == [("AAPL", "2026-09-09", TODAY)]
    assert fmp.bar_calls == []


@pytest.mark.asyncio
async def test_during_the_session_bars_in_hand_are_used_before_any_call(monkeypatch):
    _clock(monkeypatch, sos.SESSION_REGULAR)
    fmp = _FMP(eod=[_row(YESTERDAY)])
    chart = [_bar("09:30", o=10.0, h=12.0, l=9.5), _bar("10:00", o=11.0, h=13.0, l=9.0)]
    out = await _svc(fmp)._get_session_ohl("AAPL", chart_data=chart)
    assert out == {"open": 10.0, "dayHigh": 13.0, "dayLow": 9.0}
    assert fmp.eod_calls == [] and fmp.bar_calls == []


@pytest.mark.asyncio
async def test_during_the_session_without_a_row_or_bars_one_intraday_fetch_is_made(monkeypatch):
    _clock(monkeypatch, sos.SESSION_REGULAR)
    fmp = _FMP(eod=[_row(YESTERDAY)], bars=[_bar("09:30", o=10.0, h=12.0, l=9.5)])
    out = await _svc(fmp)._get_session_ohl("AAPL", chart_data=[])   # a daily chart holds no bars
    assert out == {"open": 10.0, "dayHigh": 12.0, "dayLow": 9.5}
    assert fmp.bar_calls == [("AAPL", "5min", TODAY, TODAY)]


@pytest.mark.asyncio
async def test_premarket_serves_the_last_completed_session(monkeypatch):
    """`session_trading_date()` flips to today at 04:00 ET; the header still describes
    yesterday's close until 09:30, so the range must be yesterday's too."""
    _clock(monkeypatch, sos.SESSION_PREMARKET, session=date(2026, 9, 16))
    fmp = _FMP(eod=[_row(YESTERDAY, 300, 301, 299)], bars=[_bar("04:30")])
    out = await _svc(fmp)._get_session_ohl("AAPL", chart_data=[_bar("04:30", o=5, h=5, l=5)])
    assert out == {"open": 300, "dayHigh": 301, "dayLow": 299}
    assert fmp.eod_calls[0][2] == YESTERDAY
    assert fmp.bar_calls == [], "no intraday fetch while the session is not live"


@pytest.mark.asyncio
async def test_between_midnight_and_the_premarket_the_last_session_is_still_served(monkeypatch):
    # `session_trading_date()` already returns the previous trading day while closed.
    _clock(monkeypatch, sos.SESSION_CLOSED, session=date(2026, 9, 15))
    fmp = _FMP(eod=[_row(YESTERDAY, 300, 301, 299)])
    out = await _svc(fmp)._get_session_ohl("AAPL")
    assert out["open"] == 300 and fmp.eod_calls[0][2] == YESTERDAY


@pytest.mark.asyncio
async def test_a_weekend_shows_fridays_session(monkeypatch):
    _clock(monkeypatch, sos.SESSION_CLOSED, session=date(2026, 9, 11))   # a Friday
    fmp = _FMP(eod=[_row("2026-09-11", 327.45, 336.22, 326.3)])
    out = await _svc(fmp)._get_session_ohl("AAPL")
    assert out == {"open": 327.45, "dayHigh": 336.22, "dayLow": 326.3}


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["^GSPC", "GCUSD", "BTCUSD", "EURUSD", ""])
async def test_blocked_and_crypto_symbols_never_reach_fmp(monkeypatch, symbol):
    _clock(monkeypatch, sos.SESSION_REGULAR)
    fmp = _FMP(eod=[_row(TODAY)])
    assert await _svc(fmp)._get_session_ohl(symbol) == {}
    assert fmp.eod_calls == [] and fmp.bar_calls == []


@pytest.mark.asyncio
async def test_an_upstream_failure_is_swallowed_and_not_cached(monkeypatch):
    _clock(monkeypatch, sos.SESSION_AFTERHOURS)
    fmp = _FMP(raise_eod=RuntimeError("429"))
    svc = _svc(fmp)
    assert await svc._get_session_ohl("AAPL") == {}
    assert await svc._get_session_ohl("AAPL") == {}
    assert len(fmp.eod_calls) == 2, "a failure must not be cached as a result"


@pytest.mark.asyncio
async def test_a_legitimate_empty_result_is_cached(monkeypatch):
    _clock(monkeypatch, sos.SESSION_PREMARKET)
    fmp = _FMP(eod=[])
    svc = _svc(fmp)
    assert await svc._get_session_ohl("AAPL") == {}
    assert await svc._get_session_ohl("AAPL") == {}
    assert len(fmp.eod_calls) == 1


@pytest.mark.asyncio
async def test_the_overview_merges_ohl_without_mutating_the_shared_quote(monkeypatch):
    """`price_service` caches the quote row by reference; the merge must build a new dict."""
    from tests.test_stock_overview_event_loop import _neutralise_upstreams, _service

    _clock(monkeypatch, sos.SESSION_AFTERHOURS)
    shared_quote = {"price": 332.41}
    seen = {}

    async def _fake_fundamentals(ticker):
        return {"profile": {"companyName": "Apple", "sector": "Technology"}}

    async def _fake_volatile(ticker, chart_range, interval, extended_hours, **kwargs):
        return {"quote": shared_quote, "chart_data": []}

    async def _fake_ohl(ticker, volatile=None, chart_data=None):
        if volatile is not None:
            seen["volatile"] = await volatile
        return {"open": 332.53, "dayHigh": 335.48, "dayLow": 330.70}

    async def _empty_list():
        return []

    class _NoSnapshot:
        async def _none(self, ticker):
            return None
        get_profitability_snapshot = _none
        get_growth_snapshot = _none
        get_valuation_snapshot = _none
        get_health_snapshot = _none
        get_ownership_snapshot = _none

    for module_name, factory in (
        ("profitability_snapshot_service", "get_profitability_snapshot_service"),
        ("growth_snapshot_service", "get_growth_snapshot_service"),
        ("valuation_snapshot_service", "get_valuation_snapshot_service"),
        ("health_snapshot_service", "get_health_snapshot_service"),
        ("ownership_snapshot_service", "get_ownership_snapshot_service"),
    ):
        module = __import__(f"app.services.{module_name}", fromlist=[factory])
        monkeypatch.setattr(module, factory, lambda: _NoSnapshot())

    svc = _service()
    _neutralise_upstreams(monkeypatch, svc)
    monkeypatch.setattr(svc, "_get_fundamentals", _fake_fundamentals)
    monkeypatch.setattr(svc, "_get_volatile", _fake_volatile)
    monkeypatch.setattr(svc, "_get_session_ohl", _fake_ohl)
    monkeypatch.setattr(svc, "_upsert_company_profile_db", lambda ticker, payload: None)
    movers = sos.get_market_movers_service()
    monkeypatch.setattr(movers, "get_sector_performance", _empty_list)
    monkeypatch.setattr(movers, "get_industry_performance", _empty_list)

    async def _no_related(ticker):
        return []
    monkeypatch.setattr(svc, "_build_related_tickers", _no_related)

    resp = await svc.get_overview("AAPL", "3M", None, False)

    stats = {item.label: item.value for g in resp.key_statistics_groups for item in g.statistics}
    assert (stats["Open"], stats["Day High"], stats["Day Low"]) == ("332.53", "335.48", "330.70")
    assert "open" not in shared_quote, "the cached quote row was mutated"
    assert seen["volatile"]["quote"] is shared_quote, "the OHL task must be handed the volatile result"


@pytest.mark.asyncio
async def test_the_core_path_never_fetches_the_session_range(monkeypatch):
    """Driven, not scanned: a core paint that reached the session-range fetch would raise."""
    import ast, inspect
    src = inspect.getsource(StockOverviewService.get_overview_core)
    tree = ast.parse(src.lstrip() if not src.startswith("async def") else src)
    calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert "_get_session_ohl" not in calls, "the fast core paint must stay a single call"


# ── the production seam: the volatile Task, and the today-session gate ──────

@pytest.mark.asyncio
async def test_the_volatile_task_supplies_the_bars_in_hand(monkeypatch):
    """Production passes `volatile=vol_task` (an asyncio Task), not `chart_data=`. The
    helper must read `chart_data` from the settled task and skip every FMP call."""
    import asyncio
    _clock(monkeypatch, sos.SESSION_REGULAR)
    fmp = _FMP(eod=[_row(YESTERDAY)], bars=[_bar("09:30", o=99.0, h=99.0, l=99.0)])
    chart = [_bar("09:30", o=10.0, h=12.0, l=9.5), _bar("10:00", o=11.0, h=13.0, l=9.0)]

    async def _vol():
        return {"quote": {"price": 11.0}, "chart_data": chart}

    fut = asyncio.ensure_future(_vol())
    out = await _svc(fmp)._get_session_ohl("AAPL", volatile=fut)
    assert out == {"open": 10.0, "dayHigh": 13.0, "dayLow": 9.0}
    assert fmp.eod_calls == [] and fmp.bar_calls == []


@pytest.mark.asyncio
async def test_a_failing_volatile_task_falls_through_to_the_eod_row(monkeypatch):
    import asyncio
    _clock(monkeypatch, sos.SESSION_AFTERHOURS)
    fmp = _FMP(eod=[_row(TODAY)])

    async def _boom():
        raise RuntimeError("volatile failed")

    fut = asyncio.ensure_future(_boom())
    out = await _svc(fmp)._get_session_ohl("AAPL", volatile=fut)
    assert out == {"open": 332.53, "dayHigh": 335.48, "dayLow": 330.70}


@pytest.mark.asyncio
async def test_a_half_day_afternoon_still_uses_todays_bars(monkeypatch):
    """13:00–16:00 on a half-day: `session_phase()` is already `closed` while the session
    date is today. The gate is "does the target describe today's session", not the phase."""
    _clock(monkeypatch, sos.SESSION_CLOSED, session=date(2026, 9, 16))
    fmp = _FMP(eod=[_row(YESTERDAY)], bars=[_bar("09:30", o=10.0, h=12.0, l=9.5)])
    out = await _svc(fmp)._get_session_ohl("AAPL", chart_data=[])
    assert out == {"open": 10.0, "dayHigh": 12.0, "dayLow": 9.5}
    assert fmp.bar_calls == [("AAPL", "5min", TODAY, TODAY)]


@pytest.mark.asyncio
async def test_after_2000_before_the_eod_row_lands_todays_bars_are_used(monkeypatch):
    _clock(monkeypatch, sos.SESSION_CLOSED, session=date(2026, 9, 16))
    fmp = _FMP(eod=[_row(YESTERDAY)])
    chart = [_bar("09:30", o=10.0, h=12.0, l=9.5), _bar("15:55", o=11.0, h=13.0, l=9.0)]
    out = await _svc(fmp)._get_session_ohl("AAPL", chart_data=chart)
    assert out == {"open": 10.0, "dayHigh": 13.0, "dayLow": 9.0}
    assert fmp.bar_calls == []
