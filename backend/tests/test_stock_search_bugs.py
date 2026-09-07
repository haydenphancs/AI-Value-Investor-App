"""Regression tests for two search defects that shipped and were verified live.

Both made a real, popular asset **unfindable**, and neither was caught by the existing
21-test `test_stock_search_classification.py`, because both are about *which upstream
call happens* and *what survives the merge* rather than about classifying a single row.

Hermetic per `.claude/rules/testing.md`: FMP is stubbed, never called.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

import app.api.v1.endpoints.stocks as stocks_module
from app.integrations.fmp import FMPClient


def _row(symbol: str, name: str, exchange: str = "NASDAQ",
         full: str = "NASDAQ Global Select") -> Dict[str, Any]:
    return {"symbol": symbol, "name": name, "currency": "USD",
            "exchange": exchange, "exchangeFullName": full}


# ─────────────────────────────────────────────────────────────────────────────
# Bug 1 — `search-name` fallback suppressed by a junk `search-symbol` hit
# ─────────────────────────────────────────────────────────────────────────────

class _RecordingClient:
    """Stands in for FMPClient, recording which endpoints were hit."""

    def __init__(self, responses: Dict[str, List[Dict[str, Any]]]):
        self.responses = responses
        self.calls: List[str] = []

    async def _make_request(self, endpoint: str, params=None):
        self.calls.append(endpoint)
        if endpoint not in self.responses:
            raise AssertionError(f"unexpected endpoint {endpoint!r}")
        return self.responses[endpoint]

    # Reuse the REAL implementations — a hand-rolled copy would test the copy.
    # `_has_symbol_prefix_match` is a staticmethod; re-wrap it, or assigning the
    # plain function here would bind `self` and shift every argument by one.
    search_stocks = FMPClient.search_stocks
    _has_symbol_prefix_match = staticmethod(FMPClient._has_symbol_prefix_match)


@pytest.mark.asyncio
async def test_name_query_still_searches_by_name_when_symbol_search_returns_junk():
    """THE BUG: searching "Coca" returned nothing at all.

    `search-symbol` matches a substring of the TICKER, so "Coca" hit exactly one row —
    the crypto `PACOCAUSD` ("Pacoca USD"). The old code's `if results:` was truthy on
    it, the `search-name` fallback never ran, and Coca-Cola was unfindable by name.
    """
    client = _RecordingClient({
        "search-symbol": [_row("PACOCAUSD", "Pacoca USD", "CRYPTO", "CCC")],
        "search-name": [_row("KO", "The Coca-Cola Company", "NYSE", "New York Stock Exchange")],
    })
    out = await client.search_stocks("Coca", limit=10)

    assert "search-name" in client.calls, "the name search must run — this is the bug"
    assert "KO" in [r["symbol"] for r in out], "Coca-Cola must be findable by name"


@pytest.mark.asyncio
async def test_real_ticker_query_skips_the_name_search():
    """A genuine prefix match must NOT pay for a second upstream call.

    Search fires on a 300ms debounce from six iOS entry points, so an unconditional
    second call would double the call volume of the app's hottest path.
    """
    client = _RecordingClient({
        "search-symbol": [_row("AAPL", "Apple Inc."), _row("AAPL.DE", "Apple Inc.", "XETRA", "Deutsche Borse")],
    })
    out = await client.search_stocks("AAPL", limit=10)

    assert client.calls == ["search-symbol"], "a prefix hit needs only one call"
    assert [r["symbol"] for r in out] == ["AAPL", "AAPL.DE"]


@pytest.mark.asyncio
async def test_name_results_rank_above_weak_symbol_substring_hits():
    client = _RecordingClient({
        "search-symbol": [_row("PACOCAUSD", "Pacoca USD", "CRYPTO", "CCC")],
        "search-name": [_row("KO", "The Coca-Cola Company", "NYSE", "NYSE")],
    })
    out = await client.search_stocks("Coca", limit=10)
    assert out[0]["symbol"] == "KO", "the relevant name match must come first"
    assert "PACOCAUSD" in [r["symbol"] for r in out], "the weak hit is kept, not dropped"


@pytest.mark.asyncio
async def test_search_degrades_to_symbol_hits_when_the_name_search_fails():
    """An upstream failure on the second call must not lose the first call's results."""
    class _Failing(_RecordingClient):
        async def _make_request(self, endpoint: str, params=None):
            self.calls.append(endpoint)
            if endpoint == "search-name":
                raise RuntimeError("upstream boom")
            return self.responses[endpoint]

    client = _Failing({"search-symbol": [_row("PACOCAUSD", "Pacoca USD", "CRYPTO", "CCC")]})
    out = await client.search_stocks("Coca", limit=10)
    assert [r["symbol"] for r in out] == ["PACOCAUSD"]


@pytest.mark.parametrize("query,rows,expected", [
    ("AAPL", [_row("AAPL", "Apple Inc.")], True),
    ("aapl", [_row("AAPL", "Apple Inc.")], True),          # case-insensitive
    (" AAPL ", [_row("AAPL", "Apple Inc.")], True),        # padded
    ("Coca", [_row("PACOCAUSD", "Pacoca USD")], False),    # substring, not prefix
    ("", [_row("AAPL", "Apple Inc.")], False),             # empty query
    ("AAPL", [], False),                                   # no rows
    ("AAPL", [None, "junk", 42], False),                   # malformed rows
    ("AAPL", [{"name": "no symbol key"}], False),
])
def test_prefix_predicate_outliers(query, rows, expected):
    assert FMPClient._has_symbol_prefix_match(query, rows) is expected


# ─────────────────────────────────────────────────────────────────────────────
# Bug 2 — the three largest cryptos dropped by the ticker-collision filter
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("coin,equity_name,equity_type", [
    ("BTC", "Grayscale Bitcoin Mini Trust", "NYSE American"),
    ("ETH", "Grayscale Ethereum Mini Trust", "NYSE American"),
    ("SOL", "Emeren Group Ltd", "NYSE"),
])
async def test_major_coins_survive_a_ticker_collision(monkeypatch, coin, equity_name, equity_type):
    """THE BUG: BTC, ETH and SOL were unfindable.

    `_CRYPTO_NAMES` is keyed on the bare ticker, and each of these is ALSO a real US
    listing. The collision filter dropped the coin unconditionally, so searching "BTC"
    returned the BTC *ETF* and never Bitcoin. DOGE was unaffected only because no equity
    shares its ticker — which is exactly why a spot check missed this.
    """
    class _Stub:
        async def search_stocks(self, q, limit):
            return [_row(coin, equity_name, "AMEX", equity_type)]

    monkeypatch.setattr(stocks_module, "get_fmp_client", lambda: _Stub())
    out = await stocks_module.search_stocks(q=coin, limit=10)

    types = {(r.symbol, r.type) for r in out}
    assert (coin, "crypto") in types, f"{coin} the cryptocurrency must be findable"
    assert any(s == coin and t != "crypto" for s, t in types), (
        "the equity must ALSO survive — the original invariant was that a real company "
        "is never shadowed, and keeping both is what satisfies it"
    )
    assert out[0].symbol == coin and out[0].type == "crypto", (
        "an exact-symbol coin ranks first; typing BTC means Bitcoin"
    )


@pytest.mark.asyncio
async def test_non_exact_crypto_is_still_dropped_when_an_equity_owns_the_ticker(monkeypatch):
    """The carve-out is EXACT-match only — the original shadowing guard still holds.

    Searching "STXM" must not drag in the STX coin just because it substring-matches
    the crypto map; only a symbol the user typed exactly earns the exemption.
    """
    class _Stub:
        async def search_stocks(self, q, limit):
            return [_row("STX", "Seagate Technology"), _row("STXM", "Some ETF")]

    monkeypatch.setattr(stocks_module, "get_fmp_client", lambda: _Stub())
    out = await stocks_module.search_stocks(q="STXM", limit=10)

    assert ("STX", "crypto") not in {(r.symbol, r.type) for r in out}, (
        "a crypto that merely substring-matches must stay suppressed by the equity"
    )
