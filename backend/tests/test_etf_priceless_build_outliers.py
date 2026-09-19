"""F16-4 outliers — the ETF build's last-close recovery on a priceless quote.

The fix (etf_service `_build_etf_detail`, 2026-09-17) is pinned on its happy path in
test_detail_screen_outliers.py (recovers the last close; raises with no history; one
close is a price but not a day's move). These are the inputs that path did not picture:
a quote that RETURNS `price: None` instead of raising (`price_service._from_profile` on a
halted listing), a history whose trailing rows are NaN / null / non-dict / zero, an
`adjClose`-only row, and the 30-second `get_etf_quote` projection that used to merge the
zero over a live header on iOS. Every case: never `current_price == 0.0`, never a NaN on
the wire, `change_known` honest.
"""
from __future__ import annotations

import math

import pytest

from app.integrations.fmp import FMPUnavailableException
from app.services import etf_service as E


def _svc(quote, history):
    """A real ETFService with only its fan-out stubbed — the price recovery is live."""
    E._cache.clear()
    E._inflight.clear()
    svc = E.ETFService.__new__(E.ETFService)

    async def _quote(symbol): return dict(quote) if isinstance(quote, dict) else quote
    async def _fund(symbol): return {"profile": {"companyName": "Test ETF"}}
    async def _chart(symbol, chart_range, interval, fast_only=False): return []
    async def _related(symbol): return []
    async def _hist(symbol): return list(history)
    async def _derived(symbol, index_tracked=""):
        return {"performance_periods": [], "benchmark_summary": None, "sma_50": None}
    async def _hook(**kw): return kw.get("fallback") or "hook"

    class _NoCorporateActions:
        """The dividend-calendar leg reaches FMP through `corporate_actions_source(self)`;
        without this seam the build makes (blocked) network calls and measures a degraded
        path, not the one under test."""
        async def get_ex_dividend_dates(self, *a, **k): return []

    svc._get_quote, svc._get_fundamentals, svc._get_chart = _quote, _fund, _chart
    svc._get_related, svc._get_history, svc._get_derived = _related, _hist, _derived
    svc._generate_hook_text = _hook
    svc.corporate_actions = _NoCorporateActions()
    return svc


def _row(date, close, **extra):
    return {"date": date, "open": close, "high": close, "low": close, "close": close,
            "volume": 100, **extra}


GOOD = [_row("2026-09-14", 100.0), _row("2026-09-15", 101.0), _row("2026-09-16", 102.0)]


@pytest.mark.asyncio
async def test_a_quote_that_returns_price_none_recovers_the_last_close():
    """`_from_profile` yields `{symbol, price: None}` for a halted listing — no exception,
    so the old `_get_quote` degrade never fired and `_finite_num(None)` made it $0.00."""
    resp = await _svc({"symbol": "SPY", "price": None}, GOOD)._build_etf_detail("SPY", "3M", None)
    assert resp.current_price == pytest.approx(102.0)
    assert resp.change_known is True
    assert resp.price_change == pytest.approx(1.0)
    assert resp.price_change_percent == pytest.approx(round(1.0 / 101.0 * 100, 4))
    # `nav` defaults to `price` and surfaces as the "NAV" key statistic — the fallback
    # sits above it, so the row must show the recovered close, never "$0.00".
    nav_rows = [s for s in resp.key_statistics if s.label == "NAV"]
    assert nav_rows and "0.00" not in nav_rows[0].value, nav_rows


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_price", [0, -1.0, float("nan"), float("inf"), "abc"])
async def test_a_quote_with_an_unusable_price_recovers_the_last_close(bad_price):
    resp = await _svc({"symbol": "SPY", "price": bad_price}, GOOD)._build_etf_detail("SPY", "3M", None)
    assert resp.current_price == pytest.approx(102.0)
    assert math.isfinite(resp.price_change) and math.isfinite(resp.price_change_percent)


@pytest.mark.asyncio
async def test_trailing_junk_rows_are_skipped_to_the_last_two_real_closes():
    history = GOOD + [
        _row("2026-09-17", float("nan")),
        _row("2026-09-18", None),
        _row("2026-09-19", 0.0),
        _row("2026-09-20", -3.0),
        "not-a-row",
        None,
        {"date": "2026-09-21"},           # no close at all
    ]
    resp = await _svc({}, history)._build_etf_detail("SPY", "3M", None)
    assert resp.current_price == pytest.approx(102.0)
    assert resp.change_known is True
    assert resp.price_change == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_adj_close_only_rows_are_a_valid_close():
    history = [{"date": "2026-09-15", "adjClose": 50.0}, {"date": "2026-09-16", "adjClose": 55.0}]
    resp = await _svc({}, history)._build_etf_detail("SPY", "3M", None)
    assert resp.current_price == pytest.approx(55.0)
    assert resp.price_change == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_a_history_of_only_junk_is_the_typed_upstream_error():
    history = [_row("2026-09-16", float("nan")), {"date": "2026-09-17"}, "junk", None]
    with pytest.raises(FMPUnavailableException):
        await _svc({}, history)._build_etf_detail("SPY", "3M", None)


@pytest.mark.asyncio
async def test_one_real_close_behind_junk_is_a_price_but_not_a_move():
    history = [_row("2026-09-15", None), _row("2026-09-16", 77.0), _row("2026-09-17", float("inf"))]
    resp = await _svc({}, history)._build_etf_detail("SPY", "3M", None)
    assert resp.current_price == pytest.approx(77.0)
    assert resp.change_known is False
    assert resp.price_change == 0.0 and resp.price_change_percent == 0.0


@pytest.mark.asyncio
async def test_a_real_quote_is_untouched_by_the_fallback():
    quote = {"symbol": "SPY", "price": 512.0, "change": -2.5, "changePercentage": -0.49,
             "previousClose": 514.5}
    resp = await _svc(quote, GOOD)._build_etf_detail("SPY", "3M", None)
    assert resp.current_price == 512.0
    assert resp.price_change == -2.5 and resp.change_known is True


@pytest.mark.asyncio
async def test_the_30s_quote_projection_never_ships_a_zero():
    """`get_etf_quote` projects the full build; on iOS `merged(into:)` writes
    `currentPrice` unconditionally, so a zero here would overwrite a live header."""
    svc = _svc({"symbol": "SPY", "price": None}, GOOD)
    light = await svc.get_etf_quote("SPY")
    assert light.current_price == pytest.approx(102.0) and light.current_price > 0
    assert light.change_known is True

    dead = _svc({}, [])
    with pytest.raises(FMPUnavailableException):
        await dead.get_etf_quote("SPY")
