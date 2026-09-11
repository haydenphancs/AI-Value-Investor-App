"""A BARE coin ticker is the listed security everywhere the app ROUTES or METERS.

Migration 160 stores every coin in the pair form (`BTCUSD`), so a bare `BTC` / `LTC` /
`BCH` / `ATOM` / `LINK` row can only be the listed security of that name (the Grayscale
Bitcoin Mini Trust ETF, LTC Properties — a REIT, Banco de Chile, Atomera, Interlink
Electronics). `detect_asset_class` still called those bare tickers "crypto" by default, for
a chart-window rationale that predates the migration, and three consumers routed on it:

  * `notification_kinds.ticker_route` filled `asset_type` from it, and
    `push_dispatch_service.resolve_route_asset_type` "upgraded" a sender's `stock` to it —
    so a push about LTC Properties' EARNINGS opened the Litecoin screen at Litecoin's price.
  * `price_alert_service.evaluate_once(only_round_the_clock=True)` kept them in the
    overnight universe and re-quoted/re-persisted a REIT every minute all night.
  * `tracking_service` gave the ETF's sparkline a 24/7 window unlike every equity beside it.

The classifier now calls a bare coin ticker "stock" unless the caller opts in
(`include_bare_coins=True` — chat, which only describes). Pinned at the CALLERS, because
testing the leaf alone let the 2,277x BTC bug ship last time.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import notification_kinds as nk
from app.services.asset_class import detect_asset_class, uses_coingecko_price
from app.services.price_alert_service import PriceAlertService

COLLIDING = ["BTC", "ETH", "XRP", "LTC", "BCH", "ATOM", "LINK"]


@pytest.mark.parametrize("sym", COLLIDING)
def test_a_notification_route_for_a_bare_ticker_targets_the_security(sym):
    route = nk.ticker_route(nk.KIND_TICKER_MOVE, sym)
    assert route["asset_type"] == "stock", route
    assert route["ticker"] == sym


def test_a_pair_route_still_targets_the_coin():
    assert nk.ticker_route(nk.KIND_TICKER_MOVE, "BTCUSD")["asset_type"] == "crypto"


@pytest.mark.parametrize("sym", COLLIDING)
def test_the_dispatcher_never_upgrades_a_bare_ticker_to_crypto(sym):
    from app.services.push_dispatch_service import PushDispatchService

    svc = object.__new__(PushDispatchService)
    svc._is_etf = lambda _s: False
    assert svc.resolve_route_asset_type(sym, "stock") in (None, "stock")


class _NoQuotes:
    def __init__(self):
        self.calls = 0

    async def get_quotes_list(self, tickers):
        self.calls += 1
        return []


@pytest.mark.asyncio
async def test_the_overnight_alert_cycle_keeps_only_coingecko_priced_pairs():
    seen: list = []
    svc = PriceAlertService()
    svc.price = _NoQuotes()
    universe = ["LTC", "BTC", "BTCUSD", "ETHUSD", "AAPL", "GCUSD"]
    with patch.object(PriceAlertService, "_active_universe", lambda self: list(universe)), \
         patch.object(PriceAlertService, "_active_rules",
                      lambda self, tickers: seen.append(sorted(tickers)) or []):
        await svc.evaluate_once(only_round_the_clock=True)
    assert seen == [["BTCUSD", "ETHUSD"]], seen


@pytest.mark.asyncio
async def test_an_overnight_cycle_with_only_bare_securities_makes_no_quote_call():
    svc = PriceAlertService()
    quotes = _NoQuotes()
    svc.price = quotes
    with patch.object(PriceAlertService, "_active_universe", lambda self: ["LTC", "BTC"]), \
         patch.object(PriceAlertService, "_active_rules", lambda self, tickers: []):
        await svc.evaluate_once(only_round_the_clock=True)
    assert quotes.calls == 0


def test_the_filter_and_the_router_agree_on_every_bare_symbol():
    """`uses_coingecko_price` is the source question; the classifier now agrees by default."""
    from app.integrations.coingecko import SYMBOL_TO_COINGECKO_ID
    from app.services.asset_class import _BARE_CRYPTO_SYMBOLS
    for s in _BARE_CRYPTO_SYMBOLS:
        assert detect_asset_class(s) == "stock"
        assert uses_coingecko_price(s) is False
        if s in SYMBOL_TO_COINGECKO_ID:
            assert uses_coingecko_price(f"{s}USD") is True
