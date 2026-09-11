"""Guards that certified the leaf but not the WIRING, replaced with behavioural pins.

Each of these existed, was green, and would have stayed green through the exact
regression it was written for — the vacuity shapes `.claude/rules/testing.md` §3 names:
a name-presence scan satisfied by an import line, a source scan bound to the whole file,
a test that evaluates its own literal, and a leaf test with no caller.

  * bare-coin routing at the price_service CALL SITE (the 2,277x BTC bug shipped through
    a leaf-only test of `uses_coingecko_price`);
  * the index quote is asked for the PROXY (the old scan matched the variable *name*
    `proxy`, so `proxy = symbol` stayed green);
  * an ETF-backed commodity follows the EQUITY session (the old scan matched the
    `session_phase` import line);
  * `_usd_opt` reads are bound to `get_crypto_detail`, not the whole 2,400-line module;
  * the analyst card asks the manifest BEFORE spending a blocked call (a shadowing
    `section_available = True` kept the substring scan green).

Hermetic: every upstream is a fake, patched at the binding the code actually resolves.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any, Dict, List

import pytest

from _price_fakes import FakeCoinGecko


# ── 1. bare coin → FMP, pair → CoinGecko, at the CALL SITE ───────────────────

class _FMP:
    def __init__(self):
        self.profile_calls: List[str] = []

    async def get_company_profile(self, ticker):
        self.profile_calls.append(ticker)
        return [{"symbol": ticker, "price": 42.01, "change": 0.2, "changePercentage": 0.5,
                 "companyName": "LTC Properties"}]


@pytest.mark.asyncio
async def test_a_bare_coin_ticker_is_priced_by_fmp_and_the_pair_by_coingecko(monkeypatch):
    from app.services import price_service as ps

    fmp = _FMP()
    cg = FakeCoinGecko({"LTC": 53.93})
    monkeypatch.setattr(ps, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr("app.integrations.coingecko.get_coingecko_client", lambda: cg)
    ps._cache.clear()
    svc = ps.PriceService()

    reit = await svc.get_quote("LTC")
    assert reit["price"] == 42.01 and fmp.profile_calls == ["LTC"]
    assert cg.markets_calls == [], "the REIT went to CoinGecko"

    coin = await svc.get_quote("LTCUSD")
    assert coin["price"] == 53.93
    assert fmp.profile_calls == ["LTC"], "the coin went to FMP"
    assert cg.markets_calls, "the coin never reached CoinGecko"
    ps._cache.clear()


# ── 2. the index quote is asked for the proxy ────────────────────────────────

@pytest.mark.asyncio
async def test_the_index_quote_is_requested_for_the_fund(monkeypatch):
    from app.services import index_service as isv

    asked: List[str] = []

    class _PS:
        async def get_quote(self, symbol):
            asked.append(symbol)
            return {"symbol": symbol, "price": 650.0, "change": 1.0, "changePercentage": 0.15}

    monkeypatch.setattr(isv, "price_source", lambda owner=None: _PS())
    isv._cache.clear()
    svc = isv.IndexService.__new__(isv.IndexService)
    q = await svc._get_quote("^GSPC")
    assert asked == ["SPY"], asked
    assert q["price"] == 650.0
    isv._cache.clear()


# ── 3. an ETF-backed commodity follows the equity session ────────────────────

@pytest.mark.parametrize("phase, expected", [("closed", "Market Closed"), ("regular", "Market Open"),
                                             ("premarket", "Market Open")])
def test_a_metal_screen_reports_the_equity_session(monkeypatch, phase, expected):
    from app.services import commodity_service as cs
    from app.utils import market_hours as mh

    monkeypatch.setattr(mh, "session_phase", lambda now=None: phase)
    assert cs._commodity_market_status("GCUSD") == expected


def test_a_fred_screen_is_always_closed(monkeypatch):
    from app.services import commodity_service as cs
    from app.utils import market_hours as mh

    monkeypatch.setattr(mh, "session_phase", lambda now=None: "regular")
    assert cs._commodity_market_status("CLUSD") == "Market Closed"


# ── 4. _usd_opt reads, bound to the builder ──────────────────────────────────

_MUST_BE_OPTIONAL = ["high_24h", "low_24h", "total_volume", "market_cap", "fully_diluted_valuation"]


def _detail_code() -> str:
    from app.services.crypto_service import CryptoService
    src = textwrap.dedent(inspect.getsource(CryptoService.get_crypto_detail))
    tree = ast.parse(src)
    # docstring off, then unparse so comments are gone too
    fn = tree.body[0]
    if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(getattr(fn.body[0], "value", None), ast.Constant):
        fn.body = fn.body[1:]
    return ast.unparse(tree)


@pytest.mark.parametrize("field", _MUST_BE_OPTIONAL)
def test_absent_market_fields_are_read_optionally_inside_the_builder(field):
    code = _detail_code()
    assert f"_usd_opt('{field}')" in code or f'_usd_opt("{field}")' in code, field
    assert f"_usd('{field}')" not in code and f'_usd("{field}")' not in code, field
    # ...and never re-floored after the read.
    assert f"_usd_opt('{field}') or 0" not in code and f'_usd_opt("{field}") or 0' not in code


# ── 5. the analyst card asks the manifest before a blocked call ──────────────

@pytest.mark.asyncio
async def test_an_unlicensed_analyst_section_spends_no_blocked_call(monkeypatch):
    from app.services import analyst_service as asv

    calls: List[str] = []

    class _FMP:
        async def get_grades(self, *a, **k):
            calls.append("grades"); return []

        async def get_price_target_consensus(self, *a, **k):
            calls.append("ptc"); return {}

        async def get_analyst_estimates(self, *a, **k):
            calls.append("estimates"); return []

    class _PS:
        async def get_quote(self, symbol):
            return {"symbol": symbol, "price": 100.0}

    monkeypatch.setattr(asv, "analyst_section_available", lambda: False)
    monkeypatch.setattr(asv, "price_source", lambda owner=None: _PS())
    svc = asv.AnalystService()
    svc.fmp = _FMP()
    if hasattr(asv, "_cache"):
        asv._cache.clear()
    out = await svc.get_analysis("AAPL")
    assert "grades" not in calls and "ptc" not in calls, calls
    assert out.section_available is False


@pytest.mark.asyncio
async def test_a_licensed_analyst_section_does_spend_them(monkeypatch):
    """Control: the gate is live in both directions."""
    from app.services import analyst_service as asv

    calls: List[str] = []

    class _FMP:
        async def get_grades(self, *a, **k):
            calls.append("grades"); return []

        async def get_price_target_consensus(self, *a, **k):
            calls.append("ptc"); return {}

        async def get_analyst_estimates(self, *a, **k):
            calls.append("estimates"); return []

    class _PS:
        async def get_quote(self, symbol):
            return {"symbol": symbol, "price": 100.0}

    monkeypatch.setattr(asv, "analyst_section_available", lambda: True)
    monkeypatch.setattr(asv, "price_source", lambda owner=None: _PS())
    svc = asv.AnalystService()
    svc.fmp = _FMP()
    if hasattr(asv, "_cache"):
        asv._cache.clear()
    await svc.get_analysis("AAPL")
    assert "grades" in calls
