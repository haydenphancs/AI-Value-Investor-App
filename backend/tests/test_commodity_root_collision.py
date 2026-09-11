"""A commodity ROOT is not a commodity: CL is Colgate-Palmolive on the stock routes.

Phase 4 mapped every commodity screen onto a proxy (an ETF or a FRED series) through
`commodity_service._COMMODITY_PROFILES`, keyed by the FMP futures root (`GC`, `CL`, …).
Two SHARED helpers then matched on `_root(symbol)` — a bare `.replace("USD", "")` — with
no asset-class gate, and both run on ordinary equity tickers:

  * `technical_analysis_service._analysable_symbol` runs on EVERY non-crypto ticker the
    stock TA route receives. CL (Colgate-Palmolive) and NG (NovaGold) were refused as
    "priced from a FRED daily series"; CC (Chemours) and KC (Kingsoft Cloud) as "no longer
    covered"; PL (Planet Labs) silently got its 18 indicators, pivots and Fibonacci levels
    computed on PPLT — the platinum ETF — and served under Planet Labs' name.
  * `news_cache_service._commodity_news_proxies` runs on every watchlist scope the insight
    sweeper refreshes. A watched CL fetched `news/stock?symbols=USO,XLE,CVX,XOM,OXY` and
    cached crude-oil headlines under `ticker=CL`, so Colgate's News tab and Insight card
    carried OPEC coverage.

The commodity screen always sends the PAIR form (`GCUSD` — the detail response's own
`symbol`), which `detect_asset_class` recognises as `commodity`; the bare root never means
a commodity on a shared route. Both helpers now gate on the class.

Also pins the sibling endpoint fix: `/technical-analysis/detail` flattened a typed
`FMPNotEntitledException` into a generic 502 while the gauge route already returned 409.
"""
from __future__ import annotations

import asyncio

import pytest

import app.api.v1.endpoints.stocks as stocks_ep
from app.integrations.fmp import FMPNotEntitledException
from app.services.news_cache_service import _commodity_news_proxies
from app.services.technical_analysis_service import _analysable_symbol

# Real US listings whose ticker equals a commodity root in `_COMMODITY_PROFILES`.
COLLIDING_EQUITIES = ["CL", "NG", "PL", "CC", "KC", "SI", "GC", "PA"]


@pytest.mark.parametrize("sym", COLLIDING_EQUITIES)
def test_a_bare_root_is_analysed_as_itself_on_the_stock_route(sym):
    assert _analysable_symbol(sym) == sym, (
        f"{sym} is a listed equity on the stock TA route; it must not be re-classified "
        "as a commodity"
    )


@pytest.mark.parametrize("sym", COLLIDING_EQUITIES)
def test_a_bare_root_gets_no_commodity_news_proxies(sym):
    assert _commodity_news_proxies(sym) == ""


def test_the_pair_form_still_resolves_to_the_proxy():
    """Anti-vacuity: the commodity screen's own form keeps working."""
    assert _analysable_symbol("GCUSD") == "GLD"
    assert _analysable_symbol("PLUSD") == "PPLT"
    assert _commodity_news_proxies("GCUSD").startswith("GLD")


@pytest.mark.parametrize("sym", ["CLUSD", "NGUSD"])
def test_a_fred_backed_pair_is_still_refused_contractually(sym):
    with pytest.raises(FMPNotEntitledException):
        _analysable_symbol(sym)


def test_a_withdrawn_pair_is_still_refused_contractually():
    with pytest.raises(FMPNotEntitledException):
        _analysable_symbol("KCUSD")


def test_index_symbols_still_resolve_to_their_fund():
    assert _analysable_symbol("^GSPC") == "SPY"


# ── the /detail route keeps the typed refusal ────────────────────────────────

def test_the_detail_route_returns_the_contractual_code_not_a_502(monkeypatch):
    class _Svc:
        async def get_analysis_detail(self, ticker):
            raise FMPNotEntitledException("Coffee (KC) is no longer covered")

    monkeypatch.setattr(stocks_ep, "get_technical_analysis_service", lambda: _Svc())
    resp = asyncio.run(stocks_ep.get_technical_analysis_detail("KCUSD"))
    assert resp.status_code == 409, resp.status_code
    body = bytes(resp.body).decode()
    assert "FMP_NOT_ENTITLED" in body
