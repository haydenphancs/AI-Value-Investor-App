"""`resolve_coin_id` never pins a fuzzy `/search` hit.

`/search` matches coin NAMES by substring. With no exact symbol match the old code took
`coins[0]` — some unrelated, higher-ranked coin — cached it in memory AND upserted it into
`crypto_coin_id_cache` with no TTL, so that coin's price, market cap and supply were served
under the requested symbol for every user and every restart until the row was deleted by
hand. FMP lists thousands of `XXXUSD` pairs CoinGecko does not know under that symbol.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.integrations import coingecko as cg


def _client():
    c = cg.CoinGeckoClient.__new__(cg.CoinGeckoClient)
    c._dynamic_id_cache = {}
    c._check_coin_id_db = lambda symbol: None
    c._upsert_coin_id_db = lambda *a, **k: (_ for _ in ()).throw(AssertionError("upsert"))
    return c


@pytest.mark.asyncio
async def test_no_exact_symbol_match_is_unresolved_and_never_cached(monkeypatch):
    c = _client()
    c._make_request = AsyncMock(return_value={"coins": [
        {"id": "big-coin", "symbol": "BIG", "name": "Big Coin XXX", "market_cap_rank": 3},
        {"id": "xxx-token", "symbol": "XXXT", "name": "xxx token", "market_cap_rank": 900},
    ]})
    upserts = []
    monkeypatch.setattr(c, "_upsert_coin_id_db", lambda *a: upserts.append(a))
    assert await c.resolve_coin_id("XXX") is None
    await asyncio.sleep(0)               # let any fire-and-forget executor call surface
    assert upserts == [] and "XXX" not in c._dynamic_id_cache


@pytest.mark.asyncio
async def test_an_exact_match_wins_by_market_cap_rank(monkeypatch):
    c = _client()
    c._make_request = AsyncMock(return_value={"coins": [
        {"id": "wrapped-abc", "symbol": "abc", "name": "Wrapped ABC", "market_cap_rank": 500},
        {"id": "abc", "symbol": "ABC", "name": "ABC", "market_cap_rank": 40},
        {"id": "abc-classic", "symbol": "ABC", "name": "ABC Classic", "market_cap_rank": None},
    ]})
    upserts = []
    monkeypatch.setattr(c, "_upsert_coin_id_db", lambda *a: upserts.append(a))
    assert await c.resolve_coin_id("ABC") == "abc"
    assert c._dynamic_id_cache["ABC"] == "abc"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"coins": []}, {}, None, {"coins": [{"id": "x"}]}])
async def test_empty_or_shapeless_search_is_unresolved(payload):
    c = _client()
    c._make_request = AsyncMock(return_value=payload)
    assert await c.resolve_coin_id("ZZZ") is None
