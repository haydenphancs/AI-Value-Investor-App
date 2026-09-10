"""A commodity must get REAL news on both surfaces, and a withdrawn one must get none.

Two defects, opposite directions:

1. **`GET /commodities/KC/news` answered 200 for a screen the app refuses to open.** Every
   other commodity route runs `_raise_if_withdrawn`; this one did not, so Coffee returned
   real SBUX/JO articles for a detail screen that raises `FMP_NOT_ENTITLED`.

2. **A starred commodity's Updates timeline was permanently empty.** `news_cache_service`'s
   scope router sent the futures code to `news/stock` — and `news/stock?symbols=GCUSD`
   returns nothing, because FMP has no commodity news feed. The sweeper burned one useless
   call per cycle on it, no Insight card ever generated, and the same asset's own News tab
   (which goes through `/commodities/GC/news`) was full the whole time.

Both surfaces now read ONE map, `commodity_service.COMMODITY_NEWS_TICKERS`.
"""
from __future__ import annotations

import pytest

from app.services.commodity_service import (
    COMMODITY_NEWS_TICKERS,
    _COMMODITY_PROFILES,
    _WITHDRAWN_COMMODITIES,
)
from app.services.news_cache_service import _commodity_news_proxies, is_crypto_scope


def test_every_covered_commodity_has_news_proxies():
    """A covered screen with no proxies would render an empty News tab and an empty
    Updates timeline — the exact silent-empty this fixes."""
    missing = [r for r in _COMMODITY_PROFILES if r not in COMMODITY_NEWS_TICKERS]
    assert missing == [], f"covered commodities with no news proxies: {missing}"


def test_no_withdrawn_commodity_has_news_proxies():
    """Their detail screens refuse, so a news feed routes to a screen that does not exist."""
    overlap = sorted(set(COMMODITY_NEWS_TICKERS) & set(_WITHDRAWN_COMMODITIES))
    assert overlap == [], f"withdrawn roots still carry news proxies: {overlap}"


@pytest.mark.parametrize("scope,expected_head", [
    ("GCUSD", "GLD"), ("SIUSD", "SLV"), ("CLUSD", "USO"), ("NGUSD", "UNG"),
    ("PLUSD", "PPLT"), ("PAUSD", "PALL"),
])
def test_a_commodity_scope_resolves_to_equity_proxies(scope, expected_head):
    got = _commodity_news_proxies(scope)
    assert got, f"{scope} would fall through to news/stock, which returns nothing"
    assert got.split(",")[0] == expected_head
    # ...and every proxy is an entitled equity/ETF, not another futures code.
    from app.integrations.fmp_entitlements import is_blocked_symbol
    for t in got.split(","):
        assert not is_blocked_symbol(t), f"{scope} proxies through a blocked symbol {t}"


@pytest.mark.parametrize("scope", ["KCUSD", "CTUSD", "ZWUSD", "HGUSD"])
def test_a_withdrawn_commodity_scope_has_no_proxies(scope):
    assert _commodity_news_proxies(scope) == ""


@pytest.mark.parametrize("scope", ["AAPL", "BTCUSD", "", "MSFT"])
def test_non_commodities_fall_through_untouched(scope):
    """The branch must not capture equities or crypto — crypto has its own feed."""
    assert _commodity_news_proxies(scope) == ""


def test_crypto_and_commodity_scopes_are_disjoint():
    """`is_crypto_scope` derives its exclusion from `BLOCKED_COMMODITY_SYMBOLS`; if the two
    ever disagree a commodity would be routed to the crypto news feed — the original bug
    this pair of oracles was introduced to fix."""
    for root in list(_COMMODITY_PROFILES) + list(_WITHDRAWN_COMMODITIES):
        scope = f"{root}USD"
        assert not is_crypto_scope(scope), f"{scope} routes to the CRYPTO news feed"


def test_the_endpoint_and_the_scope_router_read_the_same_map():
    """Two copies drift. The endpoint used to own the map privately."""
    from app.api.v1.endpoints.commodities import _COMMODITY_NEWS_TICKERS as endpoint_map

    assert endpoint_map is COMMODITY_NEWS_TICKERS


# ── the WIRING, not just the helper ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_scope_news_fetches_the_proxies_for_a_commodity(monkeypatch):
    """⚠️ Testing `_commodity_news_proxies` alone is not enough, and this caught it: a
    mutation that removed the whole `elif` branch from `refresh_scope_news` left every
    helper assertion above green, because nothing exercised the router.

    The scope router is the only caller — the helper is meaningless without it.
    """
    from app.services import news_cache_service as NCS

    svc = NCS.NewsCacheService.__new__(NCS.NewsCacheService)
    asked: dict = {}

    class _FMP:
        async def get_stock_news(self, symbols, limit=50, from_date=None):
            asked["stock"] = symbols
            return []

        async def get_crypto_news(self, symbol, limit=50):
            asked["crypto"] = symbol
            return []

    svc.fmp = _FMP()
    n = await svc.refresh_scope_news("GCUSD", limit=5)

    assert "crypto" not in asked, "a commodity was routed to the CRYPTO feed"
    assert asked.get("stock") == "GLD,IAU,GOLD,NEM,AEM", (
        f"expected the equity proxies, got {asked.get('stock')!r} — "
        "news/stock?symbols=GCUSD returns nothing"
    )
    assert n == 0        # the fake returns no articles; we only assert what was ASKED


@pytest.mark.asyncio
async def test_refresh_scope_news_still_sends_an_equity_scope_verbatim(monkeypatch):
    """The branch must not capture ordinary tickers."""
    from app.services import news_cache_service as NCS

    svc = NCS.NewsCacheService.__new__(NCS.NewsCacheService)
    asked: dict = {}

    class _FMP:
        async def get_stock_news(self, symbols, limit=50, from_date=None):
            asked["stock"] = symbols
            return []

    svc.fmp = _FMP()
    await svc.refresh_scope_news("AAPL", limit=5)
    assert asked.get("stock") == "AAPL"
