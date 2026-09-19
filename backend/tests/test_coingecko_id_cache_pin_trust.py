"""Tier 2b of `resolve_coin_id` honours only pins it can trust (F18-3).

The exact-symbol rule (test_coingecko_id_resolution_exact_only.py) stopped NEW fuzzy pins,
but the rows the old `coins[0]` fallback had written to `crypto_coin_id_cache` between
2026-03-28 and 2026-09-13 have no TTL and were still served unconditionally, so another
coin's price kept being answered under the requested symbol after the fix. The reader now
ignores any row stamped before the rule shipped or older than the TTL, and the upsert stamps
`cached_at` explicitly (the column DEFAULT only fires on INSERT, so a refreshed pin would
otherwise keep its fuzzy-era stamp forever).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations import coingecko as cg


_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
_FRESH = (_NOW - timedelta(days=1)).isoformat()
_FUZZY_ERA = "2026-06-01T00:00:00+00:00"          # before the exact-symbol rule
_PAST_TTL = (_NOW - timedelta(days=31)).isoformat()


def _client(db_row, search_payload=None):
    c = cg.CoinGeckoClient.__new__(cg.CoinGeckoClient)
    c._dynamic_id_cache = {}
    c._unresolved_until = {}
    c._make_request = AsyncMock(return_value=search_payload or {"coins": []})
    c._upsert_coin_id_db = lambda *a, **k: None

    class _Res:
        data = [db_row] if db_row is not None else []

    table = MagicMock()
    table.select.return_value.eq.return_value.limit.return_value.execute.return_value = _Res()
    sb = MagicMock()
    sb.table.return_value = table
    c._sb = sb
    return c, sb


@pytest.fixture
def supabase(monkeypatch):
    """Route `get_supabase()` (function-scoped import inside the reader) to the test double."""
    holder = {}
    import app.database as db
    monkeypatch.setattr(db, "get_supabase", lambda: holder["sb"])
    return holder


# ── _coin_id_row_is_trusted: the pure decision ─────────────────────────────

@pytest.mark.parametrize("stamp, trusted", [
    (_FRESH, True),
    (_FUZZY_ERA, False),                                     # predates the rule
    (_PAST_TTL, False),                                      # inside the rule era, past TTL
    ((_NOW + timedelta(hours=1)).isoformat(), False),        # future stamp = not a pin
    ((_NOW - timedelta(days=1)).replace(tzinfo=None).isoformat(), True),   # naive = UTC
    ((_NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), True),     # trailing Z
    (None, False),
    ("", False),
    ("   ", False),
    ("not a date", False),
    (12345, False),
])
def test_row_trust_decision(stamp, trusted):
    row = {"coingecko_id": "x", "cached_at": stamp}
    assert cg.CoinGeckoClient._coin_id_row_is_trusted(row, now=_NOW) is trusted


def test_the_ttl_boundary_is_inclusive_and_one_second_past_it_is_not():
    later = cg.CoinGeckoClient._COIN_ID_PIN_VALID_FROM + timedelta(days=40)
    at = {"coingecko_id": "x", "cached_at": (later - timedelta(days=30)).isoformat()}
    past = {"coingecko_id": "x", "cached_at": (later - timedelta(days=30, seconds=1)).isoformat()}
    assert cg.CoinGeckoClient._coin_id_row_is_trusted(at, now=later) is True
    assert cg.CoinGeckoClient._coin_id_row_is_trusted(past, now=later) is False


def test_the_cutoff_is_after_the_exact_symbol_rule_shipped():
    # 7f0b1fd3 landed 2026-09-13 22:26 -0600 (= 2026-09-14 04:26 UTC); anything earlier
    # may be a fuzzy pin. The cutoff must sit after that instant, and not drift years out.
    cutoff = cg.CoinGeckoClient._COIN_ID_PIN_VALID_FROM
    assert cutoff >= datetime(2026, 9, 14, 4, 27, tzinfo=timezone.utc)
    assert cutoff <= datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert cg.CoinGeckoClient._COIN_ID_PIN_TTL <= timedelta(days=90)


# ── resolve_coin_id through the DB tier ────────────────────────────────────

@pytest.mark.asyncio
async def test_a_fuzzy_era_db_pin_is_ignored_and_the_exact_search_runs(supabase):
    c, sb = _client({"coingecko_id": "big-coin", "cached_at": _FUZZY_ERA},
                    search_payload={"coins": [
                        {"id": "big-coin", "symbol": "BIG", "name": "Big Coin XXX", "market_cap_rank": 3},
                    ]})
    supabase["sb"] = sb
    assert await c.resolve_coin_id("XXX") is None          # not 'big-coin'
    c._make_request.assert_awaited_once()
    assert "XXX" not in c._dynamic_id_cache


@pytest.mark.asyncio
async def test_a_fuzzy_era_pin_is_replaced_by_the_exact_match(supabase):
    c, sb = _client({"coingecko_id": "big-coin", "cached_at": _FUZZY_ERA},
                    search_payload={"coins": [
                        {"id": "big-coin", "symbol": "BIG", "name": "Big", "market_cap_rank": 3},
                        {"id": "abc", "symbol": "ABC", "name": "ABC", "market_cap_rank": 40},
                    ]})
    supabase["sb"] = sb
    assert await c.resolve_coin_id("ABC") == "abc"
    assert c._dynamic_id_cache["ABC"] == "abc"


@pytest.mark.asyncio
async def test_a_trusted_db_pin_is_served_without_a_search_call(supabase):
    c, sb = _client({"coingecko_id": "abc", "cached_at": _FRESH})
    supabase["sb"] = sb
    assert await c.resolve_coin_id("ABC") == "abc"
    c._make_request.assert_not_awaited()
    assert c._dynamic_id_cache["ABC"] == "abc"


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [
    {"coingecko_id": "abc", "cached_at": _PAST_TTL},
    {"coingecko_id": "abc", "cached_at": None},
    {"coingecko_id": "abc"},
    {"coingecko_id": "", "cached_at": _FRESH},
    {"coingecko_id": None, "cached_at": _FRESH},
    None,
])
async def test_untrusted_or_shapeless_rows_fall_through_to_search(supabase, row):
    c, sb = _client(row)
    supabase["sb"] = sb
    assert await c.resolve_coin_id("ABC") is None
    c._make_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_db_read_failure_degrades_to_search_not_an_error(supabase):
    c, sb = _client({"coingecko_id": "abc", "cached_at": _FRESH})
    sb.table.side_effect = RuntimeError("520 edge")
    supabase["sb"] = sb
    assert await c.resolve_coin_id("ABC") is None
    c._make_request.assert_awaited_once()


# ── the upsert stamps cached_at explicitly ─────────────────────────────────

def test_upsert_stamps_cached_at_now(supabase):
    c, sb = _client(None)
    supabase["sb"] = sb
    before = datetime.now(timezone.utc)
    c._upsert_coin_id_db = cg.CoinGeckoClient._upsert_coin_id_db.__get__(c)
    c._upsert_coin_id_db("ABC", "abc", "ABC")
    payload = sb.table.return_value.upsert.call_args.args[0]
    assert payload["symbol"] == "ABC" and payload["coingecko_id"] == "abc"
    stamped = datetime.fromisoformat(payload["cached_at"])
    assert stamped.tzinfo is not None
    assert before - timedelta(seconds=5) <= stamped <= datetime.now(timezone.utc) + timedelta(seconds=5)
    # and the stamp it writes is one the reader will trust on the next read
    assert cg.CoinGeckoClient._coin_id_row_is_trusted(payload)


def test_upsert_failure_is_logged_not_raised(supabase, caplog):
    c, sb = _client(None)
    sb.table.side_effect = RuntimeError("42501")
    supabase["sb"] = sb
    c._upsert_coin_id_db = cg.CoinGeckoClient._upsert_coin_id_db.__get__(c)
    with caplog.at_level("WARNING", logger="app.integrations.coingecko"):
        c._upsert_coin_id_db("ABC", "abc", "ABC")
    assert any("Coin ID cache write failed for ABC" in r.getMessage() for r in caplog.records)


# ── W2 B-3: a "no such coin" answer converges instead of paying one /search per miss ──


@pytest.mark.asyncio
async def test_an_unresolvable_symbol_is_searched_once_per_ttl(supabase, monkeypatch):
    """The distrusted fuzzy-era rows are, by construction, the symbols `/search` has no
    exact match for — so each feed poll re-read the row, skipped it, and paid a `/search`,
    forever. A no-match answer is now memoised in memory for `_UNRESOLVED_TTL_SECONDS`."""
    c, sb = _client({"coingecko_id": "big-coin", "cached_at": _FUZZY_ERA},
                    search_payload={"coins": [{"id": "big-coin", "symbol": "BIG", "name": "Big", "market_cap_rank": 3}]})
    supabase["sb"] = sb
    clock = {"t": 1000.0}
    monkeypatch.setattr(cg.time, "monotonic", lambda: clock["t"])
    for _ in range(5):
        assert await c.resolve_coin_id("XXX") is None
    assert c._make_request.await_count == 1, "one search, then the memo answers"
    assert sb.table.return_value.select.call_count == 1, "the distrusted row is not re-read either"
    clock["t"] += cg.CoinGeckoClient._UNRESOLVED_TTL_SECONDS + 1
    assert await c.resolve_coin_id("XXX") is None
    assert c._make_request.await_count == 2, "re-asked once the memo expired (a newly listed coin appears)"


@pytest.mark.asyncio
async def test_an_empty_search_result_is_memoised_too(supabase):
    c, sb = _client(None, search_payload={"coins": []})
    supabase["sb"] = sb
    assert await c.resolve_coin_id("NOPE") is None
    assert await c.resolve_coin_id("NOPE") is None
    assert c._make_request.await_count == 1


@pytest.mark.asyncio
async def test_a_search_transport_failure_is_not_memoised(supabase):
    """A 429 / 5xx is not an answer: the next call must try again."""
    c, sb = _client(None)
    c._make_request = AsyncMock(side_effect=cg.CoinGeckoException("429"))
    supabase["sb"] = sb
    assert await c.resolve_coin_id("NOPE") is None
    assert await c.resolve_coin_id("NOPE") is None
    assert c._make_request.await_count == 2
    assert "NOPE" not in c._unresolved_until


@pytest.mark.asyncio
async def test_a_memoised_miss_does_not_shadow_a_later_exact_match(supabase, monkeypatch):
    c, sb = _client(None, search_payload={"coins": []})
    supabase["sb"] = sb
    clock = {"t": 1000.0}
    monkeypatch.setattr(cg.time, "monotonic", lambda: clock["t"])
    assert await c.resolve_coin_id("NEW") is None
    c._make_request = AsyncMock(return_value={"coins": [{"id": "new-coin", "symbol": "NEW", "name": "New", "market_cap_rank": 50}]})
    clock["t"] += cg.CoinGeckoClient._UNRESOLVED_TTL_SECONDS + 1
    assert await c.resolve_coin_id("NEW") == "new-coin"
