"""The crypto-only off-hours pass, and the loop schedule that runs it.

TestFlight (ETH, 2026-09-02): "Why crypto news are lacking? And no AI insights?" —
on today's code the sweeper sleeps outside 04:00–20:00 ET weekdays, so a coin's card
was frozen from Friday 20:00 ET to Monday 04:00 ET while its timeline stayed fresh.
`run_sweep(crypto_only=True)` is the SAME sweep restricted to the coins in the
universe; `_run_one_tick` runs it every 30 minutes while the market is closed and
never touches the equity cadence.
"""

from __future__ import annotations

import asyncio

import pytest

import app.services.updates_insight_sweeper as mod
from app.services.news_cache_service import MARKET_SCOPE
from app.services.updates_insight_sweeper import (
    CRYPTO_OFF_HOURS_INTERVAL_SECONDS,
    NEWS_EVERY_N_CYCLES,
    InsightSweeper,
    _LoopState,
    _run_one_tick,
)
from app.services.updates_materiality import ACTION_SKIP, Decision
from _price_fakes import PriceFromFMPFake

UNIVERSE = [MARKET_SCOPE, "AAPL", "BTCUSD", "GCUSD", "USD", "ETHUSDT", "MSFT"]
COINS = {"BTCUSD", "ETHUSDT"}


@pytest.fixture(autouse=True)
def _fresh_earnings_singleton():
    # The market-active sweep below reaches the REAL earnings service with a stub
    # client that lacks the method; it degrades (and stamps a negative TTL on the
    # process singleton). Reset so no later test inherits that clock.
    from app.services.earnings_window_service import get_earnings_window_service

    get_earnings_window_service().reset()
    yield
    get_earnings_window_service().reset()


class _Stub(InsightSweeper):
    def __init__(self, universe=UNIVERSE):
        self.supabase = None
        self.fmp = self          # the price fake looks up `get_batch_quotes_bulk` here
        self.price = PriceFromFMPFake(self.fmp)
        self.vol = self
        self.news = self
        self.insights = self
        self._catalyst_day = self._enrich_day = None
        self._catalyst_count = self._enrich_count = 0
        self._catalyst_scopes = set()
        self._universe_value = list(universe)
        self.quoted = None
        self.refreshed = None
        self.bulk_scopes = None
        self.state_scopes = None
        self.verified = None

    async def _universe(self):
        return list(self._universe_value)

    def _company_names(self, scopes):
        return {}

    def _load_state(self, scopes):
        self.state_scopes = list(scopes)
        return {}

    def _record_skips(self, skips, now):
        pass

    async def get_batch_quotes_bulk(self, symbols):
        self.quoted = list(symbols)
        return []

    async def get_sigmas_bulk(self, symbols):
        return {}

    async def _refresh_news(self, scopes):
        self.refreshed = list(scopes)

    def get_cached_bulk(self, scopes, limit):
        self.bulk_scopes = list(scopes)
        return {s: [] for s in scopes}

    async def mark_verified_current(self, scopes, market_active):
        self.verified = (list(scopes), market_active)

    async def _enrich_windows(self, corpora, scopes, now):
        return 0, 0


def _record_decide(monkeypatch):
    seen = {}

    def _decide(**kwargs):
        seen[kwargs["scope"]] = kwargs
        return Decision(action=ACTION_SKIP, reason="fingerprint_unchanged")

    monkeypatch.setattr(mod, "decide", _decide)
    return seen


# ── run_sweep(crypto_only=True) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crypto_only_restricts_the_universe_to_crypto_pairs(monkeypatch):
    seen = _record_decide(monkeypatch)
    monkeypatch.setattr(mod, "is_market_active", lambda: False)
    stub = _Stub()

    result = await stub.run_sweep(refresh_news=True, crypto_only=True)

    assert set(seen) == COINS, "MARKET, equities, the USD ETF and the gold pair are excluded"
    assert set(stub.quoted) == COINS, "no SPY leg: the index does not print at 02:00"
    assert set(stub.refreshed) == COINS
    assert set(stub.bulk_scopes) == COINS and set(stub.state_scopes) == COINS
    assert result["scopes"] == 2


@pytest.mark.asyncio
async def test_crypto_only_with_no_crypto_in_the_universe_is_a_no_op(monkeypatch):
    seen = _record_decide(monkeypatch)
    monkeypatch.setattr(mod, "is_market_active", lambda: False)
    stub = _Stub(universe=[MARKET_SCOPE, "AAPL", "GCUSD"])
    assert await stub.run_sweep(refresh_news=True, crypto_only=True) == {}
    assert seen == {}
    assert stub.quoted is None and stub.refreshed is None and stub.bulk_scopes is None


@pytest.mark.asyncio
async def test_a_failed_universe_read_makes_the_crypto_pass_a_no_op(monkeypatch):
    """`_universe()` returns `[MARKET_SCOPE]` on RPC failure; filtered, that is empty."""
    seen = _record_decide(monkeypatch)
    monkeypatch.setattr(mod, "is_market_active", lambda: False)
    stub = _Stub(universe=[MARKET_SCOPE])
    assert await stub.run_sweep(refresh_news=True, crypto_only=True) == {}
    assert seen == {}


@pytest.mark.asyncio
async def test_crypto_only_uses_closed_market_semantics(monkeypatch):
    seen = _record_decide(monkeypatch)
    monkeypatch.setattr(mod, "is_market_active", lambda: False)
    monkeypatch.setattr(mod, "session_phase", lambda now: "closed")
    stub = _Stub()
    await stub.run_sweep(refresh_news=False, crypto_only=True)
    for scope in COINS:
        kw = seen[scope]
        assert kw["market_active"] is False
        assert kw["session_phase"] == "closed"
        assert kw["market_change_percent"] is None, "no index leg → the MWCB guard is inert"
        assert kw["is_market_scope"] is False
        assert kw["earnings_window"] is False
    verified_scopes, verified_active = stub.verified
    assert set(verified_scopes) == COINS
    assert verified_active is False, "the closed soft TTL, so a coin card never trips is_stale"


@pytest.mark.asyncio
async def test_the_default_sweep_is_unchanged_by_the_flag(monkeypatch):
    """Anti-vacuity: without the flag every scope is swept and SPY is quoted."""
    seen = _record_decide(monkeypatch)
    monkeypatch.setattr(mod, "is_market_active", lambda: True)
    stub = _Stub()
    await stub.run_sweep(refresh_news=True)
    assert set(seen) == set(UNIVERSE)
    assert mod.MARKET_INDEX_SYMBOL in stub.quoted
    assert set(stub.refreshed) == set(UNIVERSE)


# ── _run_one_tick: the schedule ──────────────────────────────────────────────


class _Recorder:
    def __init__(self, raise_on=None):
        self.calls = []
        self.raise_on = raise_on

    async def run_sweep(self, refresh_news, *, crypto_only=False):
        self.calls.append((refresh_news, crypto_only))
        if self.raise_on is not None and len(self.calls) == self.raise_on:
            raise RuntimeError("sweep blew up")
        return {}


@pytest.mark.asyncio
async def test_the_first_off_hours_tick_after_boot_runs_the_crypto_pass():
    rec, state = _Recorder(), _LoopState()
    assert await _run_one_tick(rec, state, market_active=False, monotonic=1000.0) == "crypto"
    assert rec.calls == [(True, True)]
    assert state.last_crypto_pass == 1000.0
    assert state.cycle == 0, "the equity cycle counter must not advance on a crypto pass"


@pytest.mark.asyncio
async def test_off_hours_ticks_run_crypto_every_interval_not_every_tick():
    rec, state = _Recorder(), _LoopState()
    t0 = 5000.0
    assert await _run_one_tick(rec, state, market_active=False, monotonic=t0) == "crypto"
    assert await _run_one_tick(rec, state, market_active=False, monotonic=t0 + 300) is None
    assert await _run_one_tick(rec, state, market_active=False, monotonic=t0 + 1500) is None
    assert await _run_one_tick(
        rec, state, market_active=False, monotonic=t0 + CRYPTO_OFF_HOURS_INTERVAL_SECONDS
    ) == "crypto"
    assert rec.calls == [(True, True), (True, True)]


@pytest.mark.asyncio
async def test_the_stamp_is_taken_before_a_raising_sweep():
    rec, state = _Recorder(raise_on=1), _LoopState()
    with pytest.raises(RuntimeError):
        await _run_one_tick(rec, state, market_active=False, monotonic=100.0)
    assert state.last_crypto_pass == 100.0
    # The very next tick must NOT retry immediately.
    assert await _run_one_tick(rec, state, market_active=False, monotonic=400.0) is None
    assert len(rec.calls) == 1


@pytest.mark.asyncio
async def test_active_ticks_keep_the_news_cadence_and_never_pass_crypto_only():
    rec, state = _Recorder(), _LoopState()
    for i in range(2 * NEWS_EVERY_N_CYCLES):
        assert await _run_one_tick(rec, state, market_active=True, monotonic=float(i)) == "market"
    assert [c[0] for c in rec.calls] == [True, False, False, True, False, False]
    assert all(c[1] is False for c in rec.calls)
    assert state.cycle == 2 * NEWS_EVERY_N_CYCLES
    assert state.last_crypto_pass == float("-inf"), "an active tick never stamps the crypto pass"


@pytest.mark.asyncio
async def test_a_crypto_pass_between_sessions_does_not_disturb_the_equity_cadence():
    rec, state = _Recorder(), _LoopState()
    await _run_one_tick(rec, state, market_active=True, monotonic=0.0)     # news
    await _run_one_tick(rec, state, market_active=True, monotonic=300.0)   # price
    await _run_one_tick(rec, state, market_active=False, monotonic=600.0)  # crypto
    await _run_one_tick(rec, state, market_active=True, monotonic=900.0)   # price (cycle 2)
    await _run_one_tick(rec, state, market_active=True, monotonic=1200.0)  # news (cycle 3)
    assert rec.calls == [(True, False), (False, False), (True, True), (False, False), (True, False)]


@pytest.mark.asyncio
async def test_cancellation_propagates_from_the_tick():
    class _Cancelling:
        async def run_sweep(self, refresh_news, *, crypto_only=False):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _run_one_tick(_Cancelling(), _LoopState(), market_active=False, monotonic=0.0)
    with pytest.raises(asyncio.CancelledError):
        await _run_one_tick(_Cancelling(), _LoopState(), market_active=True, monotonic=0.0)


def test_the_interval_is_thirty_minutes_and_longer_than_a_tick():
    assert CRYPTO_OFF_HOURS_INTERVAL_SECONDS == 1800
    assert CRYPTO_OFF_HOURS_INTERVAL_SECONDS > mod.PRICE_INTERVAL_SECONDS
