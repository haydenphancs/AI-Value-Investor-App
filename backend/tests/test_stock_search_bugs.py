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
from app.services import stock_search_service


@pytest.fixture(autouse=True)
def _no_active_listing_directory(monkeypatch):
    """The handler tests here pin the crypto/equity merge, not liveness: None is the
    fail-open path, and without it the search would schedule a real list fetch."""
    monkeypatch.setattr(stock_search_service, "get_active_listings", lambda: None)


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
        async def search_stocks(self, q, limit, **kwargs):
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


def _ios_search_result_id(row) -> str:
    """Python re-implementation of `StockSearchResult.id` (StockRepository.swift):
    `"\\(ticker)_\\(type ?? "stock")"` — `ticker` decodes from `symbol`; ONLY a nil type
    defaults. The Swift side is pinned by tests/test_ios_search_result_identity.py; keep
    the two in step."""
    return f"{row.symbol}_{row.type if row.type is not None else 'stock'}"


@pytest.mark.asyncio
@pytest.mark.parametrize("coin,equity_name,equity_exchange_full,expected_equity_type", [
    ("BTC", "Grayscale Bitcoin Mini Trust", "NYSE American", "etf"),
    ("ETH", "Grayscale Ethereum Mini Trust", "NYSE American", "etf"),
    ("SOL", "Emeren Group Ltd", "NYSE", "stock"),
])
async def test_a_ticker_collision_is_two_rows_the_ios_identity_can_tell_apart(
    monkeypatch, coin, equity_name, equity_exchange_full, expected_equity_type
):
    """The twin rows the carve-out above produces must be exactly two, with DIFFERENT
    types — because that type is what the iOS `Identifiable` id is built from.

    THE BUG (2026-09-17, reproduced on the simulator): `StockSearchResult.id` was the bare
    ticker, so the coin and the ETF were ONE `ForEach` child. SwiftUI drew the ETF row as
    an empty slot and routed the tap on "BTC · Bitcoin · CRYPTO" to the ETF screen. The
    fix is on the iOS side (id = symbol + type); this test pins the backend half of the
    contract: a second stock-side path that ever emitted two rows of the SAME type for one
    symbol would collide again, and nothing else would notice.
    """
    class _Stub:
        async def search_stocks(self, q, limit, **kwargs):
            return [_row(coin, equity_name, "AMEX", equity_exchange_full)]

    monkeypatch.setattr(stocks_module, "get_fmp_client", lambda: _Stub())
    out = await stocks_module.search_stocks(q=coin, limit=10)

    twins = [r for r in out if r.symbol == coin]
    assert [r.type for r in twins] == ["crypto", expected_equity_type], twins
    assert len({(r.symbol, r.type) for r in out}) == len(out), (
        "(symbol, type) must be unique across the whole response — keyed on the RAW symbol, "
        "which is what the iOS id reads (the handler's stock dedup is case-exact too)"
    )
    ids = [_ios_search_result_id(r) for r in out]
    assert len(ids) == len(set(ids)), (
        f"iOS Identifiable ids collide: {ids} — SwiftUI renders duplicate ForEach ids as an "
        "empty row and routes the tap to the wrong twin (BTC → ETF screen)"
    )
    # The bug's shape, so this is not vacuous: the OLD bare-symbol identity DID collide.
    assert len({r.symbol for r in out}) < len(out)


@pytest.mark.asyncio
async def test_non_exact_crypto_is_still_dropped_when_an_equity_owns_the_ticker(monkeypatch):
    """The carve-out is EXACT-match only — the original shadowing guard still holds.

    Searching "STXM" must not drag in the STX coin just because it substring-matches
    the crypto map; only a symbol the user typed exactly earns the exemption.
    """
    class _Stub:
        async def search_stocks(self, q, limit, **kwargs):
            return [_row("STX", "Seagate Technology"), _row("STXM", "Some ETF")]

    monkeypatch.setattr(stocks_module, "get_fmp_client", lambda: _Stub())
    out = await stocks_module.search_stocks(q="STXM", limit=10)

    assert ("STX", "crypto") not in {(r.symbol, r.type) for r in out}, (
        "a crypto that merely substring-matches must stay suppressed by the equity"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bug 3 — a prefix hit the endpoint DISCARDS suppressed the name search (2026-09-25)
# ─────────────────────────────────────────────────────────────────────────────
# Same family as Bug 1, found by the duplicate-row sweep: "sirius" came back from
# `search-symbol` as only SIRIUSUSD (a crypto the endpoint drops), and "micro" / "bank" /
# "3m" as only foreign rows (MICRO.BK, BANK.L, 3MF.AX). Each is a genuine PREFIX hit, so
# `search-name` never ran — and every one returned ZERO US results. Sirius XM, Microsoft
# and Micron were unfindable by name. Driven through the handler with the REAL
# `FMPClient.search_stocks`, because the rule lives in the endpoint and is applied inside
# the integration: testing either half alone would miss the wiring.

@pytest.mark.asyncio
@pytest.mark.parametrize("query,symbol_rows,name_rows,expected", [
    ("sirius",
     [_row("SIRIUSUSD", "FIRST USD", "CRYPTO", "CCC")],
     [_row("SIRI", "Sirius XM Holdings Inc.")],
     "SIRI"),
    ("micro",
     [_row("MICRO.BK", "Micro Leasing PCL", "SET", "Thailand"),
      _row("MICROSE.BO", "Micro Sec", "BSE", "Bombay")],
     [_row("MSFT", "Microsoft Corporation"), _row("MU", "Micron Technology, Inc.")],
     "MSFT"),
    ("3m",
     [_row("3MF.AX", "3M Foo", "ASX", "ASX")],
     [_row("MMM", "3M Company", "NYSE", "New York Stock Exchange")],
     "MMM"),
])
async def test_a_discarded_prefix_hit_does_not_suppress_the_name_search(
    monkeypatch, query, symbol_rows, name_rows, expected
):
    client = _RecordingClient({"search-symbol": symbol_rows, "search-name": name_rows})
    monkeypatch.setattr(stocks_module, "get_fmp_client", lambda: client)
    out = await stocks_module.search_stocks(q=query, limit=10)

    assert client.calls == ["search-symbol", "search-name"], "the name search must run"
    assert expected in [r.symbol for r in out], f"{expected} must be findable by name"


@pytest.mark.asyncio
@pytest.mark.parametrize("query,symbol_rows", [
    ("AAPL", [_row("AAPL", "Apple Inc."), _row("AAPL.DE", "Apple Inc.", "XETRA", "Xetra")]),
    # An exact coin the endpoint serves from its own map IS a ticker hit — one call.
    ("DOGE", [_row("DOGEUSD", "Dogecoin USD", "CRYPTO", "CCC")]),
])
async def test_a_kept_prefix_hit_still_costs_one_call(monkeypatch, query, symbol_rows):
    client = _RecordingClient({"search-symbol": symbol_rows})
    monkeypatch.setattr(stocks_module, "get_fmp_client", lambda: client)
    await stocks_module.search_stocks(q=query, limit=10)
    assert client.calls == ["search-symbol"]


def test_the_prefix_rule_is_optional_and_applied_per_row():
    rows = [_row("BANK.L", "Bank plc", "LSE", "London"), _row("BANKX", "Bank Fund", "NASDAQ")]
    assert FMPClient._has_symbol_prefix_match("bank", rows) is True, "None = old behaviour"
    only_us = lambda r: "." not in r["symbol"]  # noqa: E731
    assert FMPClient._has_symbol_prefix_match("bank", rows, only_us) is True
    assert FMPClient._has_symbol_prefix_match("bank", rows[:1], only_us) is False
