"""Five more "an unknown number is None, never 0.0" sites, found by the 2026-09-11 sweep.

`price_service` (Phase 1) hands consumers PRESENT keys holding `None` when a price or a
day change is genuinely unknown — that is the whole point of invariant 1 there. Four
consumers were still folding that None into a published number, and one twin raised the
wrong exception class:

  * Home Skeptical Money — `price_f = _finite_float(q.get("price")) or 0.0` published a
    `$0.00` row for a halted / just-listed name that cleared the market-cap floor.
  * Legacy `/home/feed` market cards — `float(quote.get("price") or 0)` → a `$0.00 0.00%`
    card; the S&P headline printed "Markets Trade Sideways Near 0" and called SPY's share
    price "the S&P 500".
  * `market_movers_service.get_universe` — a Supabase blip on the close map was cached
    as an all-None universe for the full TTL (a cached failure byte-identical to "the
    snapshot table is empty"), and `_select_all_closes` paginated with `.range()` and
    no ORDER BY, so a sweep overlapping the hourly upsert could skip or repeat symbols.
  * `commodity_service.get_commodity_core` — a price-less core raised a bare ValueError,
    classified REPORT_GENERATION_FAILED (502, "we broke"), while the index twin had
    already been moved to `FMPUnavailableException`.

Hermetic: every upstream is a stub.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

import pytest

import app.services.home_dashboard_service as hd
import app.services.home_service as hs
import app.services.market_movers_service as mm
from app.api.error_response import ErrorCode, classify_exception
from app.integrations.fmp import FMPUnavailableException
from app.services.market_movers_service import MarketMoversService


# ── Skeptical Money ──────────────────────────────────────────────────────────

class _PS:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows

    async def get_quotes_list(self, symbols):
        return [r for r in self.rows if r["symbol"] in symbols]


def _short_universe_of(monkeypatch, symbols: List[str], quotes: List[Dict[str, Any]]):
    monkeypatch.setattr(hd, "_load_short_universe", lambda: list(symbols))

    async def _si(ticker):
        return {"shares_short": 1_000_000, "short_percent_of_float": 60.0,
                "settlement_date": "2026-09-10"}

    monkeypatch.setattr(hd, "get_short_interest", _si)
    monkeypatch.setattr(hd, "_short_data_is_fresh", lambda si: True)
    monkeypatch.setattr(hd, "price_source", lambda owner=None: _PS(quotes))

    async def _no_sparks(self, lists):
        return None

    monkeypatch.setattr(hd.HomeDashboardService, "_attach_rank1_sparks", _no_sparks)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_price", [None, 0, 0.0, -1.0, float("nan")])
async def test_a_heavily_shorted_name_without_a_real_price_is_not_published_at_zero(monkeypatch, bad_price):
    _short_universe_of(monkeypatch, ["WOLF"], [
        {"symbol": "WOLF", "name": "Wolfspeed", "price": bad_price,
         "marketCap": 4e8, "changesPercentage": -2.1},
    ])
    out = await hd.HomeDashboardService()._build_shorts()
    assert out is None or all(e.price > 0 for e in out.entries), out


@pytest.mark.asyncio
async def test_a_healthy_shorted_name_is_still_published(monkeypatch):
    """Anti-vacuity control for the test above."""
    _short_universe_of(monkeypatch, ["WOLF"], [
        {"symbol": "WOLF", "name": "Wolfspeed", "price": 7.25,
         "marketCap": 4e8, "changesPercentage": -2.1},
    ])
    out = await hd.HomeDashboardService()._build_shorts()
    assert out is not None and [e.symbol for e in out.entries] == ["WOLF"]
    assert out.entries[0].price == 7.25


# ── legacy /home/feed cards ──────────────────────────────────────────────────

class _HomePS:
    def __init__(self, quote):
        self.quote = quote

    async def get_quote(self, symbol):
        return self.quote


@pytest.mark.asyncio
@pytest.mark.parametrize("quote", [
    {"symbol": "SPY", "price": None, "changesPercentage": None},
    {"symbol": "SPY", "price": 650.0, "changesPercentage": None},
    {"symbol": "SPY", "price": 0.0, "changesPercentage": 1.0},
])
async def test_a_market_card_with_an_unknown_number_is_dropped_not_zeroed(monkeypatch, quote):
    monkeypatch.setattr(hs, "price_source", lambda owner=None: _HomePS(quote))

    async def _spark(self, symbol):
        return []

    monkeypatch.setattr(hs.HomeService, "_get_sparkline", _spark)
    svc = hs.HomeService()
    # ⚠️ NO `hasattr` + `pytest.skip`. This used to disable ITSELF on a rename, turning a
    # missing builder into a silent green for all three parametrised cases — the exact
    # "a vacuous guard is worse than no guard" shape of `.claude/rules/testing.md` §3.
    assert hasattr(svc, "_get_market_tickers"), (
        "HomeService._get_market_tickers is gone — RE-POINT this guard, do not let the "
        "fabricated-zero sweep quietly stop covering the market cards"
    )
    hs._cache.pop("market_tickers", None)
    cards = await svc._get_market_tickers()

    # ⚠️ AND NO BARE `all(...)`. Both assertions below are vacuously true on an empty list,
    # so a builder that returned [] for ANY reason — a swallowed exception included —
    # satisfied them while proving nothing. The control at the end is what makes the
    # dropped-card assertions mean something.
    assert all(c.price > 0 for c in cards)
    assert all(c.symbol != "SPY" for c in cards), "the SPY card was published from an unknown number"

    # CONTROL, in the same test so it cannot drift away from it: with a healthy quote the
    # SAME builder DOES publish a SPY card. If this stops holding, the assertions above
    # are passing on an empty list.
    hs._cache.pop("market_tickers", None)
    monkeypatch.setattr(
        hs, "price_source",
        lambda owner=None: _HomePS({"symbol": "SPY", "price": 651.2, "changesPercentage": 0.8}),
    )
    healthy = await svc._get_market_tickers()
    assert healthy, "the builder returns nothing even for a healthy quote — the drop "\
        "assertions above are vacuous"
    assert any(c.symbol == "SPY" and c.price > 0 for c in healthy), [c.symbol for c in healthy]


@pytest.mark.asyncio
async def test_the_market_headline_names_the_fund_and_refuses_an_unknown_move(monkeypatch):
    monkeypatch.setattr(hs, "price_source",
                        lambda owner=None: _HomePS({"symbol": "SPY", "price": 651.2, "changesPercentage": 0.8}))
    svc = hs.HomeService()
    hs._cache.pop("market_insight", None) if hasattr(hs, "_cache") else None

    # Skip the Supabase table read that precedes the fallback.
    monkeypatch.setattr(hs, "get_supabase", lambda: (_ for _ in ()).throw(RuntimeError("no db")), raising=False)
    insight = await svc._get_market_insight()
    assert insight is not None
    assert "S&P 500 ETF" in insight.headline
    assert "SPY" in " ".join(insight.bullet_points)
    assert "651.20" in " ".join(insight.bullet_points)

    hs._cache.pop("market_insight", None) if hasattr(hs, "_cache") else None
    monkeypatch.setattr(hs, "price_source",
                        lambda owner=None: _HomePS({"symbol": "SPY", "price": 651.2, "changesPercentage": None}))
    assert await hs.HomeService()._get_market_insight() is None


# ── market movers: a close-map outage is served once, not cached ─────────────

def _screener(symbol: str, price: float) -> Dict[str, Any]:
    return {"symbol": symbol, "companyName": f"{symbol} Inc.", "price": price,
            "marketCap": 5e9, "volume": 1e6, "avgVolume": 5e5, "sector": "Technology",
            "industry": "Semiconductors", "exchangeShortName": "NASDAQ",
            "isEtf": False, "isFund": False}


@pytest.mark.asyncio
async def test_a_close_map_outage_is_not_cached_for_the_ttl(monkeypatch):
    mm._cache.clear()

    class _PS:
        async def _get_universe(self):
            return {"AAPL": _screener("AAPL", 100.0)}

    monkeypatch.setattr(mm, "price_source", lambda owner=None: _PS())
    calls = {"n": 0}

    def _closes():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("supabase 520")
        return {"AAPL": {"symbol": "AAPL", "close": 95.0, "previous_close": 90.0,
                         "trade_date": "2026-09-10"}}

    monkeypatch.setattr(MarketMoversService, "_select_all_closes", staticmethod(_closes))
    svc = MarketMoversService()
    first = await svc.get_universe()
    assert first["AAPL"]["changePercentage"] is None          # degraded once, honestly
    # Inside the short degraded window the failing full-table read is NOT re-run — a
    # herd guard, so an outage costs one paged select per window, not one per request.
    held = await svc.get_universe()
    assert calls["n"] == 1 and held["AAPL"]["changePercentage"] is None
    t0 = time.time()
    monkeypatch.setattr(mm.time, "time", lambda: t0 + mm._DEGRADED_UNIVERSE_TTL + 1)
    second = await svc.get_universe()
    assert calls["n"] == 2, "the outage was cached past its window — the close map was never retried"
    assert second["AAPL"]["changePercentage"] == pytest.approx((100.0 / 95.0 - 1) * 100)
    mm._cache.clear()


def test_the_degraded_window_is_much_shorter_than_the_healthy_ttl():
    assert mm._DEGRADED_UNIVERSE_TTL <= mm._UNIVERSE_TTL / 2


@pytest.mark.asyncio
async def test_a_healthy_universe_is_still_memoised(monkeypatch):
    """Anti-vacuity: the fix must not turn every call into a Supabase sweep."""
    mm._cache.clear()

    class _PS:
        async def _get_universe(self):
            return {"AAPL": _screener("AAPL", 100.0)}

    monkeypatch.setattr(mm, "price_source", lambda owner=None: _PS())
    calls = {"n": 0}

    def _closes():
        calls["n"] += 1
        return {"AAPL": {"symbol": "AAPL", "close": 95.0, "previous_close": 90.0,
                         "trade_date": "2026-09-10"}}

    monkeypatch.setattr(MarketMoversService, "_select_all_closes", staticmethod(_closes))
    svc = MarketMoversService()
    await svc.get_universe()
    await svc.get_universe()
    assert calls["n"] == 1
    mm._cache.clear()


def test_the_close_sweep_orders_its_pages(monkeypatch):
    seen: List[str] = []

    class _Q:
        def __init__(self):
            self._ordered = False

        def select(self, *_a):
            return self

        def order(self, col, **_k):
            seen.append(f"order:{col}")
            return self

        def range(self, a, b):
            seen.append("range")
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    class _SB:
        def table(self, _name):
            return _Q()

    monkeypatch.setattr(mm, "get_supabase", lambda: _SB())
    MarketMoversService._select_all_closes()
    assert seen[:2] == ["order:symbol", "range"], seen


# ── commodity core raises the typed exception like its index twin ────────────

@pytest.mark.asyncio
async def test_a_priceless_commodity_core_raises_the_upstream_exception_not_valueerror(monkeypatch):
    from app.services import commodity_service as cs

    svc = cs.CommodityService()

    async def _quote(self, fmp_symbol):
        return {"symbol": fmp_symbol, "price": None}

    async def _chart(self, *a, **k):
        return []

    monkeypatch.setattr(cs.CommodityService, "_get_quote", _quote)
    monkeypatch.setattr(cs.CommodityService, "_get_chart", _chart)
    with pytest.raises(FMPUnavailableException) as info:
        await svc.get_commodity_core("GC", chart_range="1D")
    assert not isinstance(info.value, ValueError)
    code, _ = classify_exception(info.value)
    assert code is ErrorCode.FMP_UNAVAILABLE
