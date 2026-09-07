"""`price_service` — the quote-family replacement.

Covers the two invariants that matter more than the happy path:

  1. An unknown day change is ``None``, never ``0.0``. This repo has shipped the
     fabricated-zero bug more than once (the ETF "Well Diversified" badge on no data;
     `index_service` painting ``$0.00`` under a live market-status badge), and here the
     trigger is ordinary — migration 157 not yet applied, or a symbol with no stored
     close. A 0.00% day change on a user's own holdings is a wrong number, not a blank.
  2. Unlicensed symbols never reach FMP, and never reach the close snapshot. `batch-eod`
     serves ^GSPC / GCUSD / BTCUSD / EURUSD even though the per-symbol endpoint 402s them,
     so the filter is the only thing standing between us and ingesting data we did not buy.

Hermetic: FMP and Supabase are both stubbed.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Dict, List

import pytest

import app.services.price_service as ps_module
from app.services.price_service import PriceService, _cache, _finite


@pytest.fixture(autouse=True)
def _clear_cache():
    _cache.clear()
    yield
    _cache.clear()


def _screener_row(symbol: str, price: float, **over: Any) -> Dict[str, Any]:
    row = {"symbol": symbol, "companyName": f"{symbol} Inc.", "price": price,
           "volume": 1_000_000, "avgVolume": 2_000_000, "marketCap": 5_000_000_000,
           "exchangeShortName": "NASDAQ", "isEtf": False, "isFund": False}
    row.update(over)
    return row


class _FakeFMP:
    def __init__(self, screener=None, profiles=None, eod=None):
        self.screener_rows = screener or []
        self.profiles = profiles or {}
        self.eod_rows = eod or []
        self.profile_calls: List[str] = []

    async def get_company_screener(self, **kw):
        return self.screener_rows if kw.get("page", 0) == 0 else []

    async def get_company_profile(self, ticker):
        self.profile_calls.append(ticker)
        return self.profiles.get(ticker.upper(), {})

    async def get_batch_eod(self, trade_date):
        return self.eod_rows


def _install(monkeypatch, fake: _FakeFMP):
    monkeypatch.setattr(ps_module, "get_fmp_client", lambda: fake)
    return fake


# ── shaping ────────────────────────────────────────────────────────────────────────

def test_output_is_quote_shaped_with_both_percentage_spellings():
    """39 call sites read these keys directly; 32 use one spelling and 25 the other."""
    row = PriceService._shape(
        symbol="AAPL", name="Apple Inc.", price=100.0, previous_close=95.0,
        change=5.0, change_pct=5.263, volume=1.0, avg_volume=2.0,
        market_cap=3.0, exchange="NASDAQ",
    )
    for key in ("symbol", "name", "price", "change", "changePercentage",
                "changesPercentage", "previousClose", "volume", "avgVolume",
                "marketCap", "exchange"):
        assert key in row, f"consumers read {key!r}"
    assert row["changePercentage"] == row["changesPercentage"]


@pytest.mark.parametrize("value,expected", [
    (1.5, 1.5), ("2.5", 2.5), (0, 0.0), (-3, -3.0),
    (None, None), ("", None), ("abc", None), ([], None), ({}, None),
    (float("nan"), None), (float("inf"), None), (float("-inf"), None),
    (True, None), (False, None),
])
def test_finite_rejects_every_non_number(value, expected):
    """NaN defeats both `<= 0` guards and `except (TypeError, ValueError)`."""
    assert _finite(value) == expected


# ── single symbol ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_quote_carries_change_and_derives_previous_close(monkeypatch):
    _install(monkeypatch, _FakeFMP(profiles={"AAPL": {
        "symbol": "AAPL", "companyName": "Apple Inc.", "price": 319.97,
        "change": -8.24, "changePercentage": -2.51059, "volume": 39_606_884,
        "averageVolume": 52_997_011, "marketCap": 4_699_513_299_320, "exchange": "NASDAQ",
    }}))
    q = await PriceService().get_quote("aapl")
    assert q["symbol"] == "AAPL" and q["price"] == 319.97
    assert q["changePercentage"] == -2.51059
    # profile has no previousClose field; price - change reconstructs it. Verified
    # against the real batch-eod close for the prior session: 328.21.
    assert q["previousClose"] == pytest.approx(328.21, abs=0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["^GSPC", "GCUSD", "BTCUSD", "EURUSD"])
async def test_blocked_symbols_return_none_without_calling_fmp(monkeypatch, symbol):
    fake = _install(monkeypatch, _FakeFMP())
    assert await PriceService().get_quote(symbol) is None
    assert fake.profile_calls == [], "an unlicensed symbol must not reach FMP at all"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, [], None])
async def test_empty_profile_yields_none_not_a_zero_quote(monkeypatch, payload):
    fake = _FakeFMP()
    fake.profiles = {"AAPL": payload}
    _install(monkeypatch, fake)
    assert await PriceService().get_quote("AAPL") is None


@pytest.mark.asyncio
async def test_upstream_failure_degrades_to_none(monkeypatch):
    class _Boom(_FakeFMP):
        async def get_company_profile(self, ticker):
            raise RuntimeError("upstream down")
    _install(monkeypatch, _Boom())
    assert await PriceService().get_quote("AAPL") is None


@pytest.mark.asyncio
async def test_blank_symbol_is_not_a_request(monkeypatch):
    fake = _install(monkeypatch, _FakeFMP())
    for bad in ["", "   ", None]:
        assert await PriceService().get_quote(bad) is None
    assert fake.profile_calls == []


# ── batch ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_without_stored_closes_reports_unknown_change_not_zero(monkeypatch):
    """THE INVARIANT. Before migration 157 lands, every batch row hits this path."""
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 319.97)]))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda syms: []))

    out = await PriceService().get_quotes(["AAPL"])
    q = out["AAPL"]
    assert q["price"] == 319.97, "the price is known and must still be served"
    assert q["change"] is None, "0.0 here would be a fabricated day change"
    assert q["changePercentage"] is None
    assert q["changesPercentage"] is None
    assert q["previousClose"] is None


@pytest.mark.asyncio
async def test_batch_computes_change_against_the_stored_close(monkeypatch):
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 110.0)]))
    monkeypatch.setattr(PriceService, "_select_closes",
                        staticmethod(lambda syms: [{"symbol": "AAPL", "close": 100.0}]))
    q = (await PriceService().get_quotes(["AAPL"]))["AAPL"]
    assert q["previousClose"] == 100.0
    assert q["change"] == pytest.approx(10.0)
    assert q["changePercentage"] == pytest.approx(10.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_close", [0, -5, float("nan"), float("inf"), None, "x"])
async def test_a_bad_previous_close_never_produces_a_division_or_a_zero(monkeypatch, bad_close):
    """A zero close would be a ZeroDivisionError; a NaN would poison the percentage."""
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 110.0)]))
    monkeypatch.setattr(PriceService, "_select_closes",
                        staticmethod(lambda syms: [{"symbol": "AAPL", "close": bad_close}]))
    q = (await PriceService().get_quotes(["AAPL"]))["AAPL"]
    assert q["price"] == 110.0
    assert q["change"] is None and q["changePercentage"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_price", [float("nan"), float("inf"), None, "x"])
async def test_a_row_with_no_real_price_is_dropped_entirely(monkeypatch, bad_price):
    """Better absent than present-at-zero — the caller drops the tile."""
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", bad_price)]))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda syms: []))
    assert "AAPL" not in await PriceService().get_quotes(["AAPL"])


@pytest.mark.asyncio
async def test_batch_excludes_unlicensed_symbols(monkeypatch):
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 100.0)]))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda syms: []))
    out = await PriceService().get_quotes(["AAPL", "^GSPC", "BTCUSD", "GCUSD"])
    assert set(out) == {"AAPL"}


@pytest.mark.asyncio
async def test_symbol_missing_from_the_universe_falls_back_to_the_single_path(monkeypatch):
    """The screener covers actively-traded US listings above the cap — not everything."""
    fake = _install(monkeypatch, _FakeFMP(
        screener=[_screener_row("AAPL", 100.0)],
        profiles={"SHOP.TO": {"symbol": "SHOP.TO", "companyName": "Shopify",
                              "price": 180.0, "change": 1.0, "changePercentage": 0.56,
                              "exchange": "TSX"}},
    ))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda syms: []))
    out = await PriceService().get_quotes(["AAPL", "SHOP.TO"])
    assert set(out) == {"AAPL", "SHOP.TO"}
    assert fake.profile_calls == ["SHOP.TO"], "only the miss falls through"


@pytest.mark.asyncio
async def test_empty_input_makes_no_upstream_call(monkeypatch):
    fake = _install(monkeypatch, _FakeFMP())
    assert await PriceService().get_quotes([]) == {}
    assert await PriceService().get_quotes(["", "  ", None]) == {}
    assert fake.profile_calls == []


@pytest.mark.asyncio
async def test_supabase_outage_still_serves_prices(monkeypatch):
    """A missing table (pre-migration) or a DB blip must not blank the whole screen."""
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 100.0)]))
    def _boom(symbols): raise RuntimeError("PGRST205 relation missing")
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(_boom))
    q = (await PriceService().get_quotes(["AAPL"]))["AAPL"]
    assert q["price"] == 100.0 and q["changePercentage"] is None


# ── daily ingest ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_drops_every_unlicensed_symbol(monkeypatch):
    """🔴 The compliance filter. batch-eod serves these; no purchased package covers them."""
    captured: List[Dict[str, Any]] = []
    _install(monkeypatch, _FakeFMP(eod=[
        {"symbol": "AAPL", "date": "2026-09-04", "close": 319.97, "volume": 1},
        {"symbol": "^GSPC", "date": "2026-09-04", "close": 7718.6, "volume": 1},
        {"symbol": "GCUSD", "date": "2026-09-04", "close": 4476.6, "volume": 1},
        {"symbol": "BTCUSD", "date": "2026-09-04", "close": 79675.12, "volume": 1},
        {"symbol": "EURUSD", "date": "2026-09-04", "close": 1.1, "volume": 1},
    ]))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: captured.extend(p) or len(p)))
    written = await PriceService().refresh_close_snapshot("2026-09-04")
    assert written == 1
    assert [r["symbol"] for r in captured] == ["AAPL"], (
        "an unlicensed close must never be persisted — this filter is the only thing "
        "preventing ingest through FMP's own enforcement gap"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("close", [0, -1, float("nan"), float("inf"), None, "junk"])
async def test_ingest_rejects_unusable_closes(monkeypatch, close):
    captured: List[Dict[str, Any]] = []
    _install(monkeypatch, _FakeFMP(eod=[{"symbol": "AAPL", "date": "2026-09-04", "close": close}]))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: captured.extend(p) or len(p)))
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 0
    assert captured == []


@pytest.mark.asyncio
async def test_ingest_survives_an_upstream_failure(monkeypatch):
    class _Boom(_FakeFMP):
        async def get_batch_eod(self, trade_date):
            raise RuntimeError("upstream down")
    _install(monkeypatch, _Boom())
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 0


@pytest.mark.asyncio
async def test_ingest_of_an_empty_session_writes_nothing(monkeypatch):
    """A market holiday returns no rows; the previous snapshot must survive untouched."""
    _install(monkeypatch, _FakeFMP(eod=[]))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: pytest.fail("must not write")))
    assert await PriceService().refresh_close_snapshot("2026-09-07") == 0


@pytest.mark.parametrize("today,expected", [
    (date(2026, 9, 8), "2026-09-07"),   # Tue -> Mon
    (date(2026, 9, 7), "2026-09-04"),   # Mon -> Fri (skips the weekend)
    (date(2026, 9, 6), "2026-09-04"),   # Sun -> Fri
    (date(2026, 9, 5), "2026-09-04"),   # Sat -> Fri
])
def test_last_trading_day_skips_weekends(today, expected):
    assert PriceService._last_trading_day(today) == expected
