"""Budget levers for the closed-app Holdings tile (holdings-widget spec §3, §6, §7; 2026-11-27).

WidgetKit builds a Holdings tile ~35-47 times a weekday per device once the extension can
fetch for itself. Four levers keep that inside the upstream budgets:

  L1  the widget CRYPTO BOARD (`price_service.get_crypto_board_quotes`): one process-local
      table of ≤250 coins, refreshed in ONE `/coins/markets` call (`per_page=250`) at most
      once per `max_age` for every user together — ≤144 CoinGecko calls a day at the 600 s
      default (owner: "Every 10 minutes"), against ~141k a month without it;
  L2  the head-industry memo (`_industry_for_one`): no per-build FMP `/profile` call;
  L3  the tiered single-quote TTL: 15 min only inside the US closed window, the 30 s rule
      in every live phase (so nothing app-visible changes while a session trades);
  +   ONE INFO line per extension build naming the calls it caused.

Each test asserts the DEGRADED behaviour too: a coin the board cannot price is ABSENT (the
tile counts it "no price"), never a fabricated number; an outage is not retried faster than
the window; the app's own path is byte-for-byte unchanged.

Hermetic: CoinGecko, FMP and every Supabase-backed leg are stubbed.
"""

from __future__ import annotations

import asyncio
import logging
import time
import types
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

import pytest

import app.integrations.coingecko as cg_module
import app.services.price_service as ps
import app.services.widget_movers_service as wm
from app.config import settings
from app.services.price_service import PriceService, counting_upstream_calls
from app.services.widget_movers_service import (
    WidgetMoversService,
    _MarketContext,
    _payload_card_scopes,
    _payload_key,
)

ET = ZoneInfo("America/New_York")


# ── fixtures and fakes ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """The board, the quote cache and the in-flight map are process-wide."""
    ps._reset_crypto_board()
    ps._cache.clear()
    ps._inflight.clear()
    monkeypatch.setattr(settings, "WIDGET_CRYPTO_MAX_AGE_SECONDS", 600)
    monkeypatch.setattr(settings, "WIDGET_CRYPTO_BOARD_MAX_COINS", 250)
    monkeypatch.setattr(settings, "WIDGET_CRYPTO_BOARD_PER_USER", 20)
    yield
    ps._reset_crypto_board()
    ps._cache.clear()
    ps._inflight.clear()


class _Clock:
    def __init__(self, t: float = 10_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(ps, "_board_clock", c)
    return c


class _CG:
    """A CoinGecko stand-in that behaves like `/coins/markets` where it matters here:

    * rows are keyed by coin ID (MATIC and POL share one), returned in a different order;
    * the DEFAULT page is 100 rows — a call naming more ids without `per_page` loses the
      rest, which is the bug `per_page=250` exists for;
    * `no_price` coins resolve and come back with `current_price: None`.
    """

    def __init__(
        self,
        prices: Optional[Dict[str, float]] = None,
        *,
        no_price: Sequence[str] = (),
        shared_ids: Optional[Dict[str, str]] = None,
        fail: bool = False,
        empty: bool = False,
        delay: float = 0.0,
    ) -> None:
        self.prices = {k.upper(): v for k, v in (prices or {}).items()}
        self.no_price = {s.upper() for s in no_price}
        self.shared_ids = {k.upper(): v for k, v in (shared_ids or {}).items()}
        self.fail = fail
        self.empty = empty
        self.delay = delay
        self.markets_calls: List[Dict[str, Any]] = []
        self.resolve_calls: List[str] = []

    def _id(self, base: str) -> Optional[str]:
        if base in self.shared_ids:
            return self.shared_ids[base]
        if base in self.prices or base in self.no_price:
            return f"id-{base.lower()}"
        return None

    async def resolve_coin_id(self, symbol: str) -> Optional[str]:
        base = (symbol or "").strip().upper()
        self.resolve_calls.append(base)
        return self._id(base)

    async def get_markets(self, symbols, *, sparkline: bool = False, per_page=None):
        self.markets_calls.append({"symbols": list(symbols), "per_page": per_page})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise cg_module.CoinGeckoUnavailableException("coingecko down")
        if self.empty:
            return []
        rows, seen = [], set()
        for sym in symbols:
            base = (sym or "").strip().upper()
            cid = self._id(base)
            if cid is None or cid in seen:
                continue
            seen.add(cid)
            if base in self.no_price:
                rows.append({"id": cid, "symbol": base.lower(), "name": base, "current_price": None})
                continue
            price = self.prices[base]
            rows.append({
                "id": cid, "symbol": base.lower(), "name": f"{base} coin",
                "current_price": price, "price_change_24h": price * 0.02,
                "price_change_percentage_24h": 2.0, "total_volume": 1e6,
                "market_cap": price * 1e3,
            })
        rows.reverse()
        return rows[: (per_page or 100)]


def _install_cg(monkeypatch, fake: _CG) -> _CG:
    # `price_service` imports `get_coingecko_client` INSIDE its functions, so the SOURCE
    # module's binding is the one to patch (testing.md, "patch the binding the caller uses").
    monkeypatch.setattr(cg_module, "get_coingecko_client", lambda: fake)
    return fake


def _coins(n: int, start: int = 1) -> List[str]:
    return [f"C{i:04d}USD" for i in range(start, start + n)]


def _prices_for(symbols: Sequence[str], price: float = 10.0) -> Dict[str, float]:
    return {s[:-3]: price for s in symbols}


async def _board(symbols, user: str = "u1", max_age: Any = 600):
    return await PriceService().get_crypto_board_quotes(symbols, max_age, user)


def _called_symbols(fake: _CG) -> List[List[str]]:
    return [c["symbols"] for c in fake.markets_calls]


# ── 0. `get_markets(per_page=)` — other callers unchanged ─────────────────────────────


@pytest.mark.asyncio
async def test_get_markets_sends_per_page_only_when_asked(monkeypatch):
    client = cg_module.CoinGeckoClient()
    sent: List[Dict[str, Any]] = []

    async def _request(endpoint, params=None):
        sent.append({"endpoint": endpoint, **(params or {})})
        return []

    monkeypatch.setattr(client, "_make_request", _request)
    await client.get_markets(["BTC", "ETH"])
    await client.get_markets(["BTC"], per_page=250)
    assert "per_page" not in sent[0], "every existing caller keeps today's exact request"
    assert sent[0] == {
        "endpoint": "coins/markets", "vs_currency": "usd", "ids": "bitcoin,ethereum",
        "price_change_percentage": "24h", "sparkline": "false",
    }
    assert sent[1]["per_page"] == 250


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, 251, 1000, True, False, "250", 2.5, float("nan")])
async def test_get_markets_refuses_a_bad_per_page_before_any_io(monkeypatch, bad):
    client = cg_module.CoinGeckoClient()
    touched: List[str] = []

    async def _request(endpoint, params=None):
        touched.append(endpoint)
        return []

    async def _resolve(symbol):
        touched.append("resolve")
        return "bitcoin"

    monkeypatch.setattr(client, "_make_request", _request)
    monkeypatch.setattr(client, "resolve_coin_id", _resolve)
    with pytest.raises(ValueError):
        await client.get_markets(["BTC"], per_page=bad)
    assert touched == [], "a caller bug must cost nothing upstream"


def test_the_board_chunk_is_coingeckos_largest_page():
    assert ps._BOARD_CHUNK_IDS == cg_module.MARKETS_MAX_PER_PAGE == 250


def test_the_board_defaults_match_the_settings_defaults():
    fields = type(settings).model_fields
    assert ps._BOARD_DEFAULT_MAX_AGE == fields["WIDGET_CRYPTO_MAX_AGE_SECONDS"].default
    assert ps._BOARD_DEFAULT_MAX_COINS == fields["WIDGET_CRYPTO_BOARD_MAX_COINS"].default
    assert ps._BOARD_DEFAULT_PER_USER == fields["WIDGET_CRYPTO_BOARD_PER_USER"].default


# ── 1. L1 the board: reuse, one call per window, markers ──────────────────────────────


@pytest.mark.asyncio
async def test_per_symbol_reuse_across_users_and_sets(monkeypatch, clock):
    fake = _install_cg(monkeypatch, _CG({"BTC": 90_000.0, "ETH": 3_000.0}))
    a = await _board(["BTCUSD", "ETHUSD"], user="u1")
    assert set(a) == {"BTCUSD", "ETHUSD"}
    clock.advance(30)
    b = await _board(["BTCUSD"], user="u2")
    c = await _board(["ETHUSD", "BTCUSD"], user="u3")
    assert len(fake.markets_calls) == 1, "a different SET of known coins costs nothing"
    assert b["BTCUSD"]["price"] == 90_000.0 and c["ETHUSD"]["price"] == 3_000.0
    assert a["BTCUSD"]["symbol"] == "BTCUSD", "keyed and stamped with the symbol asked for"
    assert a["BTCUSD"]["changePercentage"] == a["BTCUSD"]["changesPercentage"] == 2.0


@pytest.mark.asyncio
async def test_a_101_coin_union_is_one_call_with_per_page_250(monkeypatch, clock):
    """Six users, ≤20 coins each, 101 distinct coins. The window's refresh names all 101 in
    ONE call with per_page=250 — without it the default 100-row page drops a coin."""
    coins = _coins(101)
    fake = _install_cg(monkeypatch, _CG(_prices_for(coins)))
    groups = [coins[i:i + 20] for i in range(0, 101, 20)]
    assert len(groups) == 6 and max(len(g) for g in groups) <= 20
    for n, group in enumerate(groups):
        await _board(group, user=f"u{n}")
    assert len(fake.markets_calls) == 1, "the first build refreshed; the rest wait for the window"
    clock.advance(601)
    out = await _board(groups[-1], user="u5")
    assert len(fake.markets_calls) == 2
    last = fake.markets_calls[-1]
    assert last["per_page"] == 250 and len(last["symbols"]) == 101
    assert set(out) == set(groups[-1])
    # Every coin of the union is priced now, with no further call.
    for n, group in enumerate(groups):
        assert set(await _board(group, user=f"u{n}")) == set(group)
    assert len(fake.markets_calls) == 2


@pytest.mark.asyncio
async def test_a_no_price_coin_costs_exactly_one_call_per_window(monkeypatch, clock):
    fake = _install_cg(monkeypatch, _CG({"BTC": 90_000.0}, no_price=["DEAD"]))
    out = await _board(["BTCUSD", "DEADUSD"])
    assert set(out) == {"BTCUSD"}, "a coin with no price is ABSENT, never $0.00"
    assert ps._CRYPTO_BOARD.entries["DEADUSD"][1] is None, "a stamped NO_PRICE marker"
    for step in (10, 200, 389):          # t = 10, 210, 599: all inside the window
        clock.advance(step)
        assert set(await _board(["DEADUSD"])) == set()
    assert len(fake.markets_calls) == 1, "the marker is a hit — no re-ask on every build"
    clock.advance(2)                     # t = 601
    await _board(["DEADUSD"])
    assert len(fake.markets_calls) == 2
    clock.advance(300)
    await _board(["DEADUSD", "BTCUSD"])
    assert len(fake.markets_calls) == 2


@pytest.mark.asyncio
async def test_all_unknown_coins_never_reach_coins_markets(monkeypatch, clock):
    fake = _install_cg(monkeypatch, _CG({}))
    assert await _board(["NOPEUSD", "ZZZUSD"]) == {}
    assert fake.markets_calls == [], "nothing resolved, nothing to ask"
    assert ps._CRYPTO_BOARD.entries["NOPEUSD"] == (clock.t, None)
    resolves = len(fake.resolve_calls)
    clock.advance(100)
    assert await _board(["NOPEUSD"]) == {}
    assert len(fake.resolve_calls) == resolves, "an unresolvable coin is a marker hit too"


@pytest.mark.asyncio
async def test_empty_and_blank_requests_touch_nothing(monkeypatch, clock):
    fake = _install_cg(monkeypatch, _CG({"BTC": 1.0}))
    for empty in ([], ["", "  ", None], None):
        assert await _board(empty) == {}
    assert fake.markets_calls == [] and fake.resolve_calls == []
    assert ps.crypto_board_size() == 0


@pytest.mark.asyncio
async def test_duplicates_and_mixed_case_are_one_coin(monkeypatch, clock):
    fake = _install_cg(monkeypatch, _CG({"BTC": 90_000.0}))
    out = await _board(["btcusd", "BTCUSD", " BtcUsd ", "BTCUSD"])
    assert list(out) == ["BTCUSD"]
    assert ps.crypto_board_size() == 1
    assert _called_symbols(fake) == [["BTC"]]


@pytest.mark.asyncio
async def test_a_bare_string_is_one_symbol_not_its_letters(monkeypatch, clock):
    _install_cg(monkeypatch, _CG({"BTC": 90_000.0}))
    assert set(await _board("BTCUSD")) == {"BTCUSD"}
    assert ps.crypto_board_size() == 1


@pytest.mark.asyncio
async def test_two_symbols_on_one_coin_id_are_asked_once_and_both_answered(monkeypatch, clock):
    fake = _install_cg(monkeypatch, _CG(
        {"MATIC": 0.5, "POL": 0.5},
        shared_ids={"MATIC": "polygon-ecosystem-token", "POL": "polygon-ecosystem-token"},
    ))
    out = await _board(["MATICUSD", "POLUSD"])
    assert set(out) == {"MATICUSD", "POLUSD"}
    assert out["MATICUSD"]["symbol"] == "MATICUSD" and out["POLUSD"]["symbol"] == "POLUSD"
    assert len(fake.markets_calls) == 1 and len(fake.markets_calls[0]["symbols"]) == 1


# ── 2. Admission: ≤20 per user, LRU past 250, off-board never fetched ────────────────


@pytest.mark.asyncio
async def test_a_user_admits_at_most_twenty_coins_in_request_order(monkeypatch, clock):
    coins = _coins(30)
    fake = _install_cg(monkeypatch, _CG(_prices_for(coins)))
    with counting_upstream_calls() as counters:
        out = await _board(coins)
    assert list(out) == coins[:20], "the first twenty, in the order asked"
    assert ps.crypto_board_size() == 20
    asked = {s for call in _called_symbols(fake) for s in call}
    assert not asked & {c[:-3] for c in coins[20:]}, "a coin past the cap is never fetched"
    assert counters == {"coingecko": 1, "off_board": 10}


@pytest.mark.asyncio
async def test_off_board_coins_never_trigger_a_call(monkeypatch, clock):
    coins = _coins(25)
    fake = _install_cg(monkeypatch, _CG(_prices_for(coins)))
    await _board(coins[:20])
    assert len(fake.markets_calls) == 1
    clock.advance(10)
    # The same twenty are fresh; the five past the cap are not on the board — no call.
    out = await _board(coins)
    assert set(out) == set(coins[:20])
    assert len(fake.markets_calls) == 1


@pytest.mark.asyncio
async def test_the_board_evicts_the_least_recently_requested_past_250(monkeypatch, clock):
    coins = _coins(260)
    _install_cg(monkeypatch, _CG(_prices_for(coins)))
    for n in range(13):                                   # 13 users × 20 = 260 coins
        await _board(coins[n * 20:(n + 1) * 20], user=f"u{n}")
    members = list(ps._CRYPTO_BOARD.members)
    assert len(members) == 250
    assert members[:5] == coins[10:15], "the ten least-recently requested went first"
    assert not set(coins[:10]) & set(members)
    # A request TOUCHES its coins: u0's ten survivors are now the most recent, so the next
    # admission evicts the next-oldest (u1's first coin) instead of one of them.
    await _board(coins[10:20], user="u0")
    await _board(_coins(1, start=999), user="u99")
    assert coins[10] in ps._CRYPTO_BOARD.members
    assert coins[20] not in ps._CRYPTO_BOARD.members
    assert len(ps._CRYPTO_BOARD.members) == 250


@pytest.mark.asyncio
async def test_the_entries_stay_bounded_at_twice_the_board(monkeypatch, clock):
    monkeypatch.setattr(settings, "WIDGET_CRYPTO_BOARD_MAX_COINS", 5)
    monkeypatch.setattr(settings, "WIDGET_CRYPTO_BOARD_PER_USER", 5)
    coins = _coins(40)
    _install_cg(monkeypatch, _CG(_prices_for(coins)))
    for n in range(8):
        clock.advance(601)
        await _board(coins[n * 5:(n + 1) * 5], user=f"u{n}")
    assert len(ps._CRYPTO_BOARD.members) == 5
    assert len(ps._CRYPTO_BOARD.entries) <= 10
    assert set(ps._CRYPTO_BOARD.members) <= set(ps._CRYPTO_BOARD.entries), (
        "pruning drops non-members first, never a member's row"
    )


@pytest.mark.asyncio
async def test_a_per_user_cap_above_the_board_admits_only_the_board(monkeypatch, clock):
    monkeypatch.setattr(settings, "WIDGET_CRYPTO_BOARD_MAX_COINS", 3)
    coins = _coins(6)
    _install_cg(monkeypatch, _CG(_prices_for(coins)))
    out = await _board(coins)
    assert list(out) == coins[:3] and ps.crypto_board_size() == 3


@pytest.mark.asyncio
async def test_251_coins_refresh_in_two_chunks_of_at_most_250(monkeypatch, clock):
    monkeypatch.setattr(settings, "WIDGET_CRYPTO_BOARD_MAX_COINS", 1000)
    monkeypatch.setattr(settings, "WIDGET_CRYPTO_BOARD_PER_USER", 300)
    coins = _coins(251)
    fake = _install_cg(monkeypatch, _CG(_prices_for(coins)))
    with counting_upstream_calls() as counters:
        out = await _board(coins)
    assert len(out) == 251
    assert sorted(len(c["symbols"]) for c in fake.markets_calls) == [1, 250]
    assert {c["per_page"] for c in fake.markets_calls} == {250}
    assert counters["coingecko"] == 2


@pytest.mark.asyncio
async def test_a_coin_admitted_mid_window_waits_for_the_next_window(monkeypatch, clock):
    """The price of a hard one-call-per-window budget: a coin new to the board is unpriced
    (absent) until the next window — never a second call inside this one."""
    fake = _install_cg(monkeypatch, _CG({"BTC": 1.0, "SOL": 2.0}))
    await _board(["BTCUSD"], user="u1")
    clock.advance(60)
    with counting_upstream_calls() as counters:
        assert await _board(["SOLUSD"], user="u2") == {}
    assert counters == {"board_stale": 1}
    assert len(fake.markets_calls) == 1
    clock.advance(541)                                  # 601 s after the first refresh
    assert set(await _board(["SOLUSD"], user="u2")) == {"SOLUSD"}
    assert len(fake.markets_calls) == 2
    assert sorted(fake.markets_calls[-1]["symbols"]) == ["BTC", "SOL"], "the whole board"


# ── 3. Outage, concurrency, cancellation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_outage_is_not_retried_inside_the_window(monkeypatch, clock):
    fake = _install_cg(monkeypatch, _CG({"BTC": 90_000.0}))
    assert set(await _board(["BTCUSD"])) == {"BTCUSD"}
    clock.advance(400)
    fake.fail = True
    # Still fresh (400 s): served, no call.
    assert set(await _board(["BTCUSD"])) == {"BTCUSD"}
    clock.advance(201)                                  # 601 s: stale → one attempt, fails
    assert await _board(["BTCUSD"]) == {}, "a stale row is never served past max_age"
    assert len(fake.markets_calls) == 2
    assert ps._CRYPTO_BOARD.entries["BTCUSD"][0] == 10_000.0, "a failure stamps nothing"
    for _ in range(5):
        clock.advance(60)
        assert await _board(["BTCUSD"]) == {}
    assert len(fake.markets_calls) == 2, "an outage waits for the next window, not 1/build"
    fake.fail = False
    clock.advance(301)                                  # 601 s after the failed attempt
    assert set(await _board(["BTCUSD"])) == {"BTCUSD"}
    assert len(fake.markets_calls) == 3


@pytest.mark.asyncio
async def test_an_empty_answer_for_named_coins_is_no_answer(monkeypatch, clock):
    """A page with not one row (a malformed or refused body) must not mark every coin
    NO_PRICE for a window: they stay unstamped, and the gate still bounds the retries."""
    fake = _install_cg(monkeypatch, _CG({"BTC": 1.0, "ETH": 2.0}, empty=True))
    assert await _board(["BTCUSD", "ETHUSD"]) == {}
    assert "BTCUSD" not in ps._CRYPTO_BOARD.entries
    clock.advance(30)
    await _board(["BTCUSD"])
    assert len(fake.markets_calls) == 1


@pytest.mark.asyncio
async def test_a_resolver_error_leaves_the_coin_unstamped_not_marked(monkeypatch, clock):
    fake = _CG({"BTC": 1.0, "ETH": 2.0})
    real = fake.resolve_coin_id

    async def _flaky(symbol):
        if symbol.upper() == "ETH":
            raise RuntimeError("supabase blip")
        return await real(symbol)

    fake.resolve_coin_id = _flaky
    _install_cg(monkeypatch, fake)
    out = await _board(["BTCUSD", "ETHUSD"])
    assert set(out) == {"BTCUSD"}
    assert "ETHUSD" not in ps._CRYPTO_BOARD.entries, "an error is not 'no such coin'"


@pytest.mark.asyncio
async def test_concurrent_builds_share_one_refresh(monkeypatch, clock):
    fake = _install_cg(monkeypatch, _CG({"BTC": 1.0, "ETH": 2.0}, delay=0.05))
    outs = await asyncio.gather(*(
        _board(["BTCUSD", "ETHUSD"], user=f"u{n}") for n in range(8)
    ))
    assert all(set(o) == {"BTCUSD", "ETHUSD"} for o in outs)
    assert len(fake.markets_calls) == 1


@pytest.mark.asyncio
async def test_a_cancelled_caller_does_not_cancel_the_shared_refresh(monkeypatch, clock):
    fake = _install_cg(monkeypatch, _CG({"BTC": 1.0}, delay=0.05))
    first = asyncio.ensure_future(_board(["BTCUSD"], user="u1"))
    await asyncio.sleep(0.01)
    second = asyncio.ensure_future(_board(["BTCUSD"], user="u2"))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert set(await second) == {"BTCUSD"}, "the joiner still gets the refresh's rows"
    assert len(fake.markets_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -5, "soon", None, True])
async def test_an_unusable_max_age_falls_back_to_the_default_window(monkeypatch, clock, bad, caplog):
    """A NaN max_age would make every row stale AND every gate open — one call per build."""
    fake = _install_cg(monkeypatch, _CG({"BTC": 1.0}))
    with caplog.at_level(logging.WARNING, logger=ps.__name__):
        await _board(["BTCUSD"], max_age=bad)
        clock.advance(300)
        out = await _board(["BTCUSD"], max_age=bad)
    assert set(out) == {"BTCUSD"}
    assert len(fake.markets_calls) == 1
    assert "max_age" in caplog.text


@pytest.mark.asyncio
async def test_bad_limit_settings_fall_back_to_the_defaults(monkeypatch, clock, caplog):
    monkeypatch.setattr(settings, "WIDGET_CRYPTO_BOARD_MAX_COINS", 0)
    monkeypatch.setattr(settings, "WIDGET_CRYPTO_BOARD_PER_USER", "lots")
    coins = _coins(25)
    _install_cg(monkeypatch, _CG(_prices_for(coins)))
    with caplog.at_level(logging.WARNING, logger=ps.__name__):
        out = await _board(coins)
    assert len(out) == 20, "the default per-user cap, never 'no cap'"
    assert "WIDGET_CRYPTO_BOARD_PER_USER" in caplog.text


# ── 4. Write-through from the app's own path; the app path is unchanged ──────────────


@pytest.mark.asyncio
async def test_the_apps_fetch_writes_through_so_a_new_coin_is_priced_at_once(monkeypatch, clock):
    fake = _install_cg(monkeypatch, _CG({"BTC": 90_000.0, "SOL": 200.0}))
    await _board(["BTCUSD"], user="u1")                 # the window's refresh: BTC only
    clock.advance(60)
    app_rows = await PriceService()._crypto_quotes(["SOLUSD"])   # the app prices SOL
    assert set(app_rows) == {"SOLUSD"}
    assert fake.markets_calls[-1]["per_page"] is None, "the app's request is unchanged"
    calls = len(fake.markets_calls)
    out = await _board(["SOLUSD"], user="u2")
    assert out["SOLUSD"] is app_rows["SOLUSD"], "the app's row, served from the board"
    assert len(fake.markets_calls) == calls


@pytest.mark.asyncio
async def test_write_through_never_marks_a_coin_no_price(monkeypatch, clock):
    """A coin missing from the app's answer may be a page CoinGecko cut — never a marker."""
    _install_cg(monkeypatch, _CG({"BTC": 1.0}, no_price=["DEAD"]))
    await PriceService()._crypto_quotes(["BTCUSD", "DEADUSD"])
    assert "DEADUSD" not in ps._CRYPTO_BOARD.entries
    assert ps._CRYPTO_BOARD.entries["BTCUSD"][1]["price"] == 1.0


@pytest.mark.asyncio
async def test_a_cache_hit_on_the_app_path_does_not_restamp_the_board(monkeypatch, clock):
    _install_cg(monkeypatch, _CG({"BTC": 1.0}))
    await PriceService()._crypto_quotes(["BTCUSD"])
    stamp = ps._CRYPTO_BOARD.entries["BTCUSD"][0]
    clock.advance(30)
    await PriceService()._crypto_quotes(["BTCUSD"])     # served from the 60 s set cache
    assert ps._CRYPTO_BOARD.entries["BTCUSD"][0] == stamp


@pytest.mark.asyncio
async def test_an_older_refresh_never_overwrites_a_newer_write_through(monkeypatch, clock):
    board = ps._CRYPTO_BOARD
    board.write("BTCUSD", 200.0, {"symbol": "BTCUSD", "price": 2.0})
    board.write("BTCUSD", 100.0, {"symbol": "BTCUSD", "price": 1.0})
    assert board.entries["BTCUSD"][1]["price"] == 2.0


@pytest.mark.asyncio
async def test_get_quotes_without_max_age_never_touches_the_board(monkeypatch, clock):
    fake = _install_cg(monkeypatch, _CG({"BTC": 90_000.0}))
    _stub_equities(monkeypatch, {})
    out = await PriceService().get_quotes(["BTCUSD"])
    assert set(out) == {"BTCUSD"}
    assert ps.crypto_board_size() == 0
    assert fake.markets_calls[-1]["per_page"] is None


def _stub_equities(monkeypatch, universe: Dict[str, Dict[str, Any]]) -> None:
    async def _universe(self):
        return universe

    async def _closes(self, symbols):
        return {}

    monkeypatch.setattr(PriceService, "_get_universe", _universe)
    monkeypatch.setattr(PriceService, "get_close_snapshots", _closes)


def _screener_row(symbol: str, price: float) -> Dict[str, Any]:
    return {"symbol": symbol, "companyName": f"{symbol} Inc.", "price": price,
            "volume": 1_000_000, "avgVolume": 2_000_000, "marketCap": 5e9,
            "exchangeShortName": "NASDAQ", "isEtf": False, "isFund": False}


@pytest.mark.asyncio
async def test_get_quotes_with_max_age_routes_coins_to_the_board_in_request_order(monkeypatch, clock):
    monkeypatch.setattr(settings, "WIDGET_CRYPTO_BOARD_PER_USER", 2)
    fake = _install_cg(monkeypatch, _CG({"BTC": 1.0, "ETH": 2.0, "SOL": 3.0}))
    _stub_equities(monkeypatch, {"AAPL": _screener_row("AAPL", 100.0)})
    rows = await PriceService().get_quotes_list(
        ["SOLUSD", "AAPL", "BTCUSD", "ETHUSD"], crypto_max_age=600, crypto_board_user="u1",
    )
    assert [r["symbol"] for r in rows] == ["SOLUSD", "AAPL", "BTCUSD"], (
        "the first two COINS in the order asked; ETH is past the cap and absent"
    )
    assert fake.markets_calls[0]["per_page"] == 250
    assert sorted(fake.markets_calls[0]["symbols"]) == ["BTC", "SOL"]


@pytest.mark.asyncio
async def test_a_coingecko_outage_on_the_board_leaves_equities_priced(monkeypatch, clock):
    _install_cg(monkeypatch, _CG({"BTC": 1.0}, fail=True))
    _stub_equities(monkeypatch, {"AAPL": _screener_row("AAPL", 100.0)})
    out = await PriceService().get_quotes(["AAPL", "BTCUSD"], crypto_max_age=600)
    assert set(out) == {"AAPL"}


# ── 5. L3: the tiered single-quote TTL ────────────────────────────────────────────────


class _ProfileFMP:
    def __init__(self, exchange: Optional[str] = "NASDAQ", inactive: bool = False) -> None:
        self.calls: List[str] = []
        self.exchange = exchange
        self.inactive = inactive

    async def get_company_profile(self, ticker: str) -> Dict[str, Any]:
        self.calls.append(ticker)
        row = {"symbol": ticker.upper(), "companyName": "Tiny Co", "price": 4.2,
               "change": 0.1, "changePercentage": 2.4}
        if self.exchange is not None:
            row["exchange"] = self.exchange
        if self.inactive:
            row["isActivelyTrading"] = False
        return row


def _epoch(y, mo, d, h, mi, s=0) -> float:
    return datetime(y, mo, d, h, mi, s, tzinfo=ET).timestamp()


class _WallClock:
    def __init__(self, t: float) -> None:
        self.t = t

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return time.monotonic()


async def _quote_twice(monkeypatch, fmp, first: float, second: float) -> int:
    """Quote TINY at `first`, then at `second` (epoch s); return how many profile calls."""
    wall = _WallClock(first)
    monkeypatch.setattr(ps, "time", wall)
    monkeypatch.setattr(ps, "get_fmp_client", lambda: fmp)
    svc = PriceService()
    assert (await svc.get_quote("TINY"))["price"] == 4.2
    wall.t = second
    assert (await svc.get_quote("TINY"))["price"] == 4.2
    return len(fmp.calls)


SAT_NOON = _epoch(2026, 11, 28, 12, 0)          # a weekend: closed all day
MON_10AM = _epoch(2026, 11, 30, 10, 0)          # the regular session


@pytest.mark.asyncio
@pytest.mark.parametrize("age,calls", [(5, 1), (30, 1), (31, 2), (120, 2)])
async def test_l3_a_live_session_read_keeps_the_30s_rule(monkeypatch, age, calls):
    """No app-visible change while a session trades: past 30 s it refetches, exactly as
    `_cache_get(key, _QUOTE_TTL)` did."""
    assert await _quote_twice(monkeypatch, _ProfileFMP(), MON_10AM, MON_10AM + age) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("age,calls", [(31, 1), (600, 1), (900, 1), (901, 2)])
async def test_l3_inside_the_closed_window_a_us_quote_lives_15_minutes(monkeypatch, age, calls):
    assert await _quote_twice(monkeypatch, _ProfileFMP(), SAT_NOON, SAT_NOON + age) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("first,second", [
    # Stamped in after-hours, read 70 s later in the closed window: the stamp is not closed.
    (_epoch(2026, 11, 30, 19, 59, 50), _epoch(2026, 11, 30, 20, 1, 0)),
    # Stamped in the closed window, read in pre-market: the read is not closed.
    (_epoch(2026, 12, 1, 3, 59, 0), _epoch(2026, 12, 1, 4, 0, 40)),
])
async def test_l3_a_stamp_or_read_outside_the_closed_window_keeps_30s(monkeypatch, first, second):
    assert await _quote_twice(monkeypatch, _ProfileFMP(), first, second) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fmp", [
    _ProfileFMP(exchange="TSX"),          # trades while the US is closed
    _ProfileFMP(exchange="LSE"),
    _ProfileFMP(exchange=None),           # unknown → fail closed
    _ProfileFMP(exchange="OTC"),          # not on the closed-window list
    _ProfileFMP(inactive=True),           # its change is decided against a moving registry
], ids=["tsx", "lse", "no-exchange", "otc", "inactive"])
async def test_l3_only_an_active_us_listing_gets_the_long_ttl(monkeypatch, fmp):
    assert await _quote_twice(monkeypatch, fmp, SAT_NOON, SAT_NOON + 120) == 2


@pytest.mark.asyncio
async def test_l3_counts_each_profile_call_for_the_build_line(monkeypatch):
    fmp = _ProfileFMP()
    monkeypatch.setattr(ps, "get_fmp_client", lambda: fmp)
    with counting_upstream_calls() as counters:
        await PriceService().get_quote("TINY")
        await PriceService().get_quote("TINY")         # a cache hit costs nothing
    assert counters == {"fmp_profile": 1}


def test_counting_is_a_no_op_outside_a_build():
    ps.count_upstream("fmp_profile")                    # must not raise or leak anywhere
    with counting_upstream_calls() as counters:
        pass
    assert counters == {}


# ── 6. L2: the head-industry memo ────────────────────────────────────────────────────


class _IndustryFMP:
    def __init__(self, rows: Any = None, *, fail: bool = False, delay: float = 0.0) -> None:
        self.rows = rows if rows is not None else [{"symbol": "ACHR", "industry": "Aerospace & Defense"}]
        self.fail = fail
        self.delay = delay
        self.calls = 0

    async def get_company_profiles_batch(self, tickers):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("fmp 429")
        return self.rows


class _Mono:
    def __init__(self) -> None:
        self.t = 1_000.0

    def monotonic(self) -> float:
        return self.t

    def time(self) -> float:
        return time.time()


def _industry_service(monkeypatch, fmp: _IndustryFMP):
    mono = _Mono()
    monkeypatch.setattr(wm, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(wm, "time", mono)
    return WidgetMoversService(), mono


@pytest.mark.asyncio
async def test_l2_a_found_industry_is_memoised_for_a_day(monkeypatch):
    fmp = _IndustryFMP()
    svc, mono = _industry_service(monkeypatch, fmp)
    assert await svc._industry_for_one("achr") == "Aerospace & Defense"
    mono.t += 23 * 3600
    assert await svc._industry_for_one("ACHR") == "Aerospace & Defense"
    assert fmp.calls == 1
    mono.t += 3601
    await svc._industry_for_one("ACHR")
    assert fmp.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("rows,ttl", [
    ([{"symbol": "ACHR", "industry": ""}], wm._INDUSTRY_MISS_TTL_SECONDS),     # names none
    ([{"symbol": "ACHR"}], wm._INDUSTRY_MISS_TTL_SECONDS),
    ([], wm._INDUSTRY_UNSURE_TTL_SECONDS),                                     # maybe an outage
    ("junk", wm._INDUSTRY_UNSURE_TTL_SECONDS),
])
async def test_l2_a_miss_is_memoised_shorter(monkeypatch, rows, ttl):
    fmp = _IndustryFMP(rows)
    svc, mono = _industry_service(monkeypatch, fmp)
    assert await svc._industry_for_one("ACHR") is None
    mono.t += ttl - 1
    assert await svc._industry_for_one("ACHR") is None
    assert fmp.calls == 1
    mono.t += 2
    await svc._industry_for_one("ACHR")
    assert fmp.calls == 2


def test_l2_the_ttls_are_ordered_hit_miss_unsure():
    assert wm._INDUSTRY_HIT_TTL_SECONDS == 86_400
    assert wm._INDUSTRY_MISS_TTL_SECONDS == 3_600
    assert wm._INDUSTRY_UNSURE_TTL_SECONDS < wm._INDUSTRY_MISS_TTL_SECONDS


@pytest.mark.asyncio
async def test_l2_a_raised_error_is_never_memoised(monkeypatch):
    fmp = _IndustryFMP(fail=True)
    svc, _ = _industry_service(monkeypatch, fmp)
    assert await svc._industry_for_one("ACHR") is None
    assert await svc._industry_for_one("ACHR") is None
    assert fmp.calls == 2
    assert "ACHR" not in svc._industry_memo


@pytest.mark.asyncio
async def test_l2_junk_rows_are_skipped_not_fatal(monkeypatch):
    fmp = _IndustryFMP(["junk", None, 7, {"symbol": "ACHR", "industry": "  Semiconductors "}])
    svc, _ = _industry_service(monkeypatch, fmp)
    assert await svc._industry_for_one("ACHR") == "Semiconductors"


@pytest.mark.asyncio
async def test_l2_concurrent_builds_share_one_lookup(monkeypatch):
    fmp = _IndustryFMP(delay=0.02)
    svc, _ = _industry_service(monkeypatch, fmp)
    names = await asyncio.gather(*(svc._industry_for_one("ACHR") for _ in range(10)))
    assert set(names) == {"Aerospace & Defense"}
    assert fmp.calls == 1
    assert svc._industry_inflight == {}, "the in-flight entry is released"


@pytest.mark.asyncio
async def test_l2_the_memo_is_bounded(monkeypatch):
    monkeypatch.setattr(wm, "_INDUSTRY_MEMO_MAX_ENTRIES", 3)
    svc, _ = _industry_service(monkeypatch, _IndustryFMP())
    for t in ("A", "B", "C", "D", "E"):
        await svc._industry_for_one(t)
    assert list(svc._industry_memo) == ["C", "D", "E"]


@pytest.mark.asyncio
async def test_l2_a_blank_ticker_costs_nothing(monkeypatch):
    fmp = _IndustryFMP()
    svc, _ = _industry_service(monkeypatch, fmp)
    assert await svc._industry_for_one("  ") is None
    assert fmp.calls == 0


@pytest.mark.asyncio
async def test_l2_counts_only_the_build_that_called(monkeypatch):
    fmp = _IndustryFMP()
    svc, _ = _industry_service(monkeypatch, fmp)
    with counting_upstream_calls() as first:
        await svc._industry_for_one("ACHR")
    with counting_upstream_calls() as second:
        await svc._industry_for_one("ACHR")
    assert first == {"fmp_profile": 1} and second == {}


# ── 7. The extension build: plumbing, payload key, INFO line ─────────────────────────


def test_the_card_scopes_survive_both_suffixes():
    base = "portfolio:u1:AAPL,BTCUSD"
    for key in (
        base + wm._EXTENSION_KEY_SUFFIX,
        _payload_key(base + wm._EXTENSION_KEY_SUFFIX, True),
        _payload_key(base, True),
        base,
    ):
        assert _payload_card_scopes(key) == frozenset({"AAPL", "BTCUSD"}), key


def test_a_new_card_evicts_the_extension_entry_too():
    svc = WidgetMoversService()
    ext = _payload_key("portfolio:u1:AAPL,BTCUSD" + wm._EXTENSION_KEY_SUFFIX, True)
    svc._cache[ext] = (0.0, object())
    svc.forget_payloads_for("btcusd")
    assert ext not in svc._cache


def _stub_build_legs(monkeypatch, svc: WidgetMoversService) -> None:
    """The context, sector and grade legs of `_build_portfolio` (FMP / Supabase)."""

    async def _market_context(tickers, index_rows, session_date):
        return _MarketContext()

    async def _sectors(user_id, tickers):
        return {}

    async def _head_grades(ranked):
        return None

    monkeypatch.setattr(svc, "_market_context", _market_context)
    monkeypatch.setattr(svc, "_sectors", _sectors)
    monkeypatch.setattr(svc, "_head_grades", _head_grades)


@pytest.mark.asyncio
async def test_build_portfolio_forwards_the_board_only_for_the_extension(monkeypatch):
    svc = WidgetMoversService()
    _stub_build_legs(monkeypatch, svc)
    seen: List[Dict[str, Any]] = []

    async def _rank_and_read(tickers, **kwargs):
        seen.append(kwargs)
        return [], {}, True, {}

    monkeypatch.setattr(svc, "_rank_and_read", _rank_and_read)
    await svc.get_portfolio_mover("u1", ["AAPL", "BTCUSD"])
    await svc.get_portfolio_mover("u1", ["AAPL", "BTCUSD"], for_extension=True)
    assert "crypto_max_age" not in seen[0], "the app's call is exactly today's"
    assert seen[1]["crypto_max_age"] == 600 and seen[1]["crypto_board_user"] == "u1"


@pytest.mark.asyncio
async def test_rank_and_read_forwards_the_board_to_quotes_only_when_set(monkeypatch):
    svc = WidgetMoversService()
    calls: List[Dict[str, Any]] = []

    async def _quotes(symbols, **kwargs):
        calls.append(kwargs)
        return {}

    class _Sig:
        async def get_sigmas_bulk(self, symbols):
            return {}

    monkeypatch.setattr(svc, "_quotes", _quotes)
    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Sig())
    await svc._rank_and_read(["AAPL"])
    await svc._rank_and_read(["AAPL"], crypto_max_age=600, crypto_board_user="u1")
    assert calls[0] == {}
    assert calls[1] == {"crypto_max_age": 600, "crypto_board_user": "u1"}


@pytest.mark.asyncio
async def test_quotes_calls_the_source_exactly_as_before_for_the_app(monkeypatch):
    class _StrictSource:
        """The pre-change signature: any extra keyword is a TypeError."""
        def __init__(self):
            self.calls = []

        async def get_quotes_list(self, symbols):
            self.calls.append(list(symbols))
            return [{"symbol": "AAPL", "price": 1.0}]

    svc = WidgetMoversService()
    svc.price = _StrictSource()
    assert set(await svc._quotes(["AAPL"])) == {"AAPL"}


@pytest.mark.asyncio
@pytest.mark.parametrize("tickers,shared", [
    (["AAPL", "MSFT"], True),            # no coin: the two builds are identical → one key
    (["AAPL", "BTCUSD"], False),         # a coin: board vs per-set quote → two keys
])
async def test_the_extension_key_differs_only_when_a_coin_is_held(monkeypatch, tickers, shared):
    svc = WidgetMoversService()
    _stub_build_legs(monkeypatch, svc)
    builds = {"n": 0}

    async def _rank_and_read(_tickers, **kwargs):
        builds["n"] += 1
        return [], {}, True, {}

    monkeypatch.setattr(svc, "_rank_and_read", _rank_and_read)
    await svc.get_portfolio_mover("u1", tickers)
    await svc.get_portfolio_mover("u1", tickers, for_extension=True)
    await svc.get_portfolio_mover("u1", tickers, for_extension=True)
    assert builds["n"] == (1 if shared else 2)
    keys = list(svc._cache)
    assert any(k.startswith("portfolio:u1:") for k in keys)
    assert all(k.split("#")[0].startswith("portfolio:u1:") for k in keys), "per user"


def _end_to_end_service(monkeypatch, fmp) -> WidgetMoversService:
    """A real service whose quotes go through the REAL price_service (board included), with
    only the Supabase / FMP-context legs stubbed."""
    monkeypatch.setattr(wm, "session_phase", lambda now=None: "regular")
    monkeypatch.setattr(wm, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(ps, "get_fmp_client", lambda: fmp)

    class _Sig:
        async def get_sigmas_bulk(self, symbols):
            return {}

    class _News:
        async def get_cards(self, symbols):
            return {}

    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Sig())
    monkeypatch.setattr(wm, "get_news_insight_service", lambda: _News())
    svc = WidgetMoversService()

    async def _ctx(session_day):
        return _MarketContext()

    async def _industries(tickers):
        return {}

    async def _sectors(user_id, tickers):
        return {}

    async def _head_grades(ranked):
        return None

    monkeypatch.setattr(svc, "_fetch_market_context", _ctx)
    monkeypatch.setattr(svc, "_industries", _industries)
    monkeypatch.setattr(svc, "_sectors", _sectors)
    monkeypatch.setattr(svc, "_head_grades", _head_grades)
    return svc


class _BothFMP(_ProfileFMP, _IndustryFMP):
    def __init__(self) -> None:
        _ProfileFMP.__init__(self)
        _IndustryFMP.__init__(self, [{"symbol": "BTCUSD", "industry": ""}])


@pytest.mark.asyncio
async def test_one_info_line_per_extension_build_with_the_calls_it_caused(monkeypatch, clock, caplog):
    fake = _install_cg(monkeypatch, _CG({"BTC": 90_000.0, "ETH": 3_000.0}))
    _stub_equities(monkeypatch, {
        "SPY": _screener_row("SPY", 651.0), "ONEQ": _screener_row("ONEQ", 80.0),
        "DIA": _screener_row("DIA", 450.0), "IWM": _screener_row("IWM", 230.0),
        "GLD": _screener_row("GLD", 380.0), "AAPL": _screener_row("AAPL", 100.0),
    })
    fmp = _BothFMP()
    svc = _end_to_end_service(monkeypatch, fmp)
    tickers = ["AAPL", "BTCUSD", "ETHUSD", "TINY"]
    with caplog.at_level(logging.INFO, logger=wm.__name__):
        p = await svc.get_portfolio_mover("u1", tickers, for_extension=True)
        await svc.get_portfolio_mover("u1", tickers, for_extension=True)     # memo hit
    lines = [r.getMessage() for r in caplog.records if "widget holdings build" in r.getMessage()]
    assert len(lines) == 1, "one line per BUILD, none for a memo hit"
    line = lines[0]
    assert "user=u1" in line and "coingecko=1" in line and "board_size=2" in line
    assert "off_board=0" in line and "holdings=4" in line
    # TINY is off the universe → one profile; the head industry lookup → one more.
    assert "fmp_profile=2" in line, line
    assert p.holdings_count == 4
    assert {m.ticker for m in [p.headline_mover, *p.runners_up] if m} >= {"BTCUSD", "ETHUSD"}
    assert fake.markets_calls[0]["per_page"] == 250

    # A second device of another user inside the window: the board and the memos are warm.
    caplog.clear()
    svc2 = _end_to_end_service(monkeypatch, fmp)
    clock.advance(120)
    with caplog.at_level(logging.INFO, logger=wm.__name__):
        await svc2.get_portfolio_mover("u2", ["ETHUSD", "BTCUSD"], for_extension=True)
    line2 = next(r.getMessage() for r in caplog.records if "widget holdings build" in r.getMessage())
    assert "coingecko=0" in line2 and "user=u2" in line2
    assert len(fake.markets_calls) == 1


@pytest.mark.asyncio
async def test_the_app_build_logs_no_line_and_never_uses_the_board(monkeypatch, clock, caplog):
    fake = _install_cg(monkeypatch, _CG({"BTC": 90_000.0}))
    _stub_equities(monkeypatch, {"SPY": _screener_row("SPY", 651.0)})
    svc = _end_to_end_service(monkeypatch, _BothFMP())
    with caplog.at_level(logging.INFO, logger=wm.__name__):
        await svc.get_portfolio_mover("u1", ["BTCUSD"])
    assert not [r for r in caplog.records if "widget holdings build" in r.getMessage()]
    assert ps.crypto_board_size() == 0
    assert fake.markets_calls[0]["per_page"] is None
