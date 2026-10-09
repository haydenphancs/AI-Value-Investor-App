"""An inactive listing's profile change is a PAST session's move, never today's.

2026-10-08: Energy Transfer (ET), Dillard's (DDS) and USA Compression (USAC) moved their
primary listing from NYSE to the Texas Stock Exchange on 2026-10-05, and FMP carries no TXSE
prices. Read-only probes that day: `historical-price-eod/light` ends at 2026-10-05, there is
no intraday bar on 10-07 or 10-08, and `/stable/profile` still reads exchange NYSE with
`isActivelyTrading: false`, price and change frozen at the 10-05 close. The screener universe
asks for actively trading rows only, so every quote for them fell back to the profile, which
has no session stamp, and DDS's +6.30% from 10-05 was served as today's move: on Tracking, the
widget, the detail header, percent-move alerts and the insight sweeper.

The rows below are the probe's own values (profile fields only, no secrets).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

import pytest

import app.services.price_service as ps_module
from app.services import session_pricing
from app.services.price_service import (
    PriceService,
    _cache as _price_cache,
    profile_change_is_current,
    session_change_percent,
)
from app.services.stock_overview_service import StockOverviewService, _cache as _overview_cache
from _price_fakes import PriceFromFMPFake

# /stable/profile, 2026-10-08 (the NYSE → TXSE move took effect 2026-10-05).
ET_PROFILE: Dict[str, Any] = {
    "symbol": "ET", "companyName": "Energy Transfer LP", "exchange": "NYSE",
    "exchangeFullName": "New York Stock Exchange", "price": 20.72, "change": 0.25,
    "changePercentage": 1.2213, "volume": 7204304, "averageVolume": 8832498,
    "marketCap": 71300835200, "range": "16.18-21.84", "isActivelyTrading": False,
    "isEtf": False, "isFund": False,
}
DDS_PROFILE: Dict[str, Any] = {
    "symbol": "DDS", "companyName": "Dillard's, Inc.", "exchange": "NYSE", "price": 695.35,
    "change": 41.24, "changePercentage": 6.30475, "volume": 90514, "averageVolume": 152207,
    "marketCap": 10858577951, "range": "503.06-741.98", "isActivelyTrading": False,
    "isEtf": False, "isFund": False,
}
# Still actively trading on 2026-10-08 (due to move 2026-10-13): its change is current.
OBK_PROFILE: Dict[str, Any] = {
    "symbol": "OBK", "companyName": "Origin Bancorp, Inc.", "exchange": "NYSE", "price": 52.47,
    "change": 0.15, "changePercentage": 0.2867, "volume": 232332, "averageVolume": 164802,
    "marketCap": 1621538127, "range": "32.125-55.57", "isActivelyTrading": True,
    "isEtf": False, "isFund": False,
}


@pytest.fixture(autouse=True)
def _clear_caches():
    # A cold `session_pricing` registry: nothing proves these symbols trade, so the flag
    # decides (the registry override is pinned in test_session_pricing.py).
    session_pricing._reset_for_tests()
    _price_cache.clear()
    _overview_cache.clear()
    yield
    session_pricing._reset_for_tests()
    _price_cache.clear()
    _overview_cache.clear()


# ── the predicate ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("profile, current", [
    ({"isActivelyTrading": False}, False),
    ({"isActivelyTrading": "false"}, False),
    ({"isActivelyTrading": " FALSE "}, False),
    ({"isActivelyTrading": True}, True),
    ({"isActivelyTrading": "true"}, True),
    ({"isActivelyTrading": None}, True),     # absent/unknown flag: unchanged behaviour
    ({}, True),
    ({"isActivelyTrading": 0}, True),        # only an explicit false counts
    (None, True),
    ([], True),
    ("ET", True),
])
def test_profile_change_is_current(profile, current):
    assert profile_change_is_current(profile) is current


# ── price_service: single, strict and batch ───────────────────────────────────

@pytest.mark.parametrize("row", [ET_PROFILE, DDS_PROFILE])
def test_an_inactive_profile_keeps_its_price_and_drops_its_change(row):
    q = PriceService._from_profile(dict(row))
    assert q["price"] == row["price"], "the last real print is kept"
    for key in ("change", "changePercentage", "changesPercentage", "previousClose"):
        assert q[key] is None, f"{key} must be unknown for an inactive listing, got {q[key]!r}"
    # The 52-week band is not a session figure; it stays.
    assert q["yearLow"] < q["yearHigh"]
    assert session_change_percent(q) is None, "a percent-move alert must hold, not fire"


def test_an_active_profile_change_is_unchanged():
    q = PriceService._from_profile(dict(OBK_PROFILE))
    assert q["change"] == pytest.approx(0.15)
    assert q["changePercentage"] == pytest.approx(0.2867)
    assert q["previousClose"] == pytest.approx(52.47 - 0.15)


def test_a_profile_without_the_flag_keeps_its_change():
    row = {k: v for k, v in OBK_PROFILE.items() if k != "isActivelyTrading"}
    q = PriceService._from_profile(row)
    assert q["changePercentage"] == pytest.approx(0.2867)


def test_an_inactive_profile_with_no_price_is_still_all_unknown():
    q = PriceService._from_profile({**DDS_PROFILE, "price": 0, "change": 0})
    assert q["price"] is None and q["change"] is None and q["changePercentage"] is None


class _FakeFMP:
    def __init__(self, screener: List[Dict[str, Any]], profiles: Dict[str, Dict[str, Any]]):
        self.screener_rows = screener
        self.profiles = profiles
        self.profile_calls: List[str] = []

    async def get_company_screener(self, **kw):
        return self.screener_rows if kw.get("page", 0) == 0 else []

    async def get_company_profile(self, ticker):
        self.profile_calls.append(ticker.upper())
        return dict(self.profiles.get(ticker.upper(), {}))


def _install(monkeypatch, fake: _FakeFMP) -> _FakeFMP:
    monkeypatch.setattr(ps_module, "get_fmp_client", lambda: fake)
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda syms: []))
    return fake


@pytest.mark.asyncio
async def test_get_quote_and_strict_drop_the_stale_change(monkeypatch):
    _install(monkeypatch, _FakeFMP([], {"ET": ET_PROFILE}))
    for getter in (PriceService().get_quote, PriceService().get_quote_strict):
        _price_cache.clear()
        q = await getter("ET")
        assert q["price"] == 20.72
        assert q["change"] is None and q["changePercentage"] is None


@pytest.mark.asyncio
async def test_batch_fallback_for_a_symbol_the_screener_omits(monkeypatch):
    """THE reported path: ET is not in the actively-trading screener universe, so the batch
    falls back to its profile. AAPL rides the screener as before."""
    aapl = {"symbol": "AAPL", "companyName": "Apple Inc.", "price": 340.42, "volume": 1.0,
            "avgVolume": 2.0, "marketCap": 5e12, "exchangeShortName": "NASDAQ",
            "isEtf": False, "isFund": False, "isActivelyTrading": True}
    fake = _install(monkeypatch, _FakeFMP([aapl], {"ET": ET_PROFILE, "DDS": DDS_PROFILE}))

    out = await PriceService().get_quotes(["ET", "DDS", "AAPL"])

    assert sorted(fake.profile_calls) == ["DDS", "ET"], "only the omitted symbols fall back"
    for sym, price in (("ET", 20.72), ("DDS", 695.35)):
        assert out[sym]["price"] == price
        assert out[sym]["changePercentage"] is None, f"{sym}: a 10-05 move served as today's"
        assert session_change_percent(out[sym]) is None
    assert out["AAPL"]["price"] == 340.42


# ── the detail header (core and full builders) ────────────────────────────────

class _OverviewFMP:
    def __init__(self, quote, profile):
        self._quote, self._profile = quote, profile

    async def get_stock_price_quote(self, ticker):
        return dict(self._quote)

    async def get_company_profile(self, ticker):
        return dict(self._profile)

    def __getattr__(self, name):
        async def _forbidden(*a, **k):
            raise AssertionError(f"core path must not call fmp.{name}()")
        return _forbidden


@pytest.mark.asyncio
@pytest.mark.parametrize("profile, known", [(ET_PROFILE, False), (OBK_PROFILE, True)])
async def test_core_header_does_not_fall_back_to_an_inactive_profiles_change(profile, known):
    # The quote leg is what price_service now emits for the profile: price, no change.
    quote = {k: v for k, v in PriceService._from_profile(dict(profile)).items()}
    svc = StockOverviewService()
    svc.fmp = _OverviewFMP(quote, profile)  # type: ignore[assignment]
    svc.price = PriceFromFMPFake(svc.fmp)
    resp = await svc.get_overview_core(profile["symbol"], chart_range="3M")
    assert resp.change_known is known
    assert resp.current_price == profile["price"]
    if not known:
        assert resp.price_change == 0.0 and resp.price_change_percent == 0.0
    assert math.isfinite(resp.price_change_percent)


@pytest.mark.parametrize("profile, known", [(DDS_PROFILE, False), (OBK_PROFILE, True)])
def test_full_header_does_not_fall_back_to_an_inactive_profiles_change(profile, known):
    quote = PriceService._from_profile(dict(profile))
    resp = StockOverviewService()._build_full_response(
        profile["symbol"],
        {"profile": {"sector": "Consumer Cyclical", **profile}},
        {"quote": quote, "chart_data": []},
        "1D", "5min", False,
    )
    assert resp.change_known is known
    if not known:
        assert resp.price_change == 0.0 and resp.price_change_percent == 0.0


# ── /stocks/{ticker}: the detail screen's fallback when /overview fails ───────

class _DetailFMP:
    def __init__(self, profile):
        self._profile = profile

    async def get_company_profile(self, ticker):
        return dict(self._profile)

    async def get_shares_float(self, ticker):
        return {}

    async def get_analyst_estimates(self, ticker, period="annual", limit=5):
        return []

    async def get_institutional_ownership_summary(self, ticker):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("profile, known", [(DDS_PROFILE, False), (OBK_PROFILE, True)])
async def test_the_stock_details_fallback_drops_an_inactive_profiles_change(monkeypatch, profile, known):
    """Review 2026-10-08: with the quote paths fixed, iOS's fallback (`/stocks/{t}`) still
    served the raw profile change — the stale DDS +6.30% came back through it."""
    from app.api.v1.endpoints import stocks

    async def no_short_interest(ticker):
        return {}

    monkeypatch.setattr(stocks, "get_fmp_client", lambda: _DetailFMP(profile))
    monkeypatch.setattr(stocks, "get_short_interest", no_short_interest)
    resp = await stocks.get_stock_details(profile["symbol"])
    assert resp["price"] == profile["price"]
    for key in ("changes", "change_percent"):
        if known:
            assert resp[key] is not None
        else:
            assert resp.get(key) is None, f"{key} must be unknown for an inactive listing"
