"""The sweep threads ONE earnings-window verdict into BOTH cap halves, and stamps
capped scopes as verified-current.

Two call-site tests, for the same reason `test_updates_insight_subject_wiring` exists:
the pure pieces (`daily_cap_for(..., earnings_window=)`, `_claim(..., earnings_window=)`,
`earnings_window_service`) can each be green while the sweep passes the flag to one
half and not the other — which is precisely the 6-vs-16 market incident: the gate
admits, the RPC refuses, and nothing is logged or written.

Plus the hermeticity trap: the earnings lookup MUST go through the sweeper's own
client. The suite's sweep stubs carry `fmp = None`; a lookup that reached for the
singleton would make every one of them attempt a real HTTP call, and `conftest.py`
fails the whole session on the first one.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import conftest
import app.services.updates_insight_sweeper as mod
from app.services.news_cache_service import MARKET_SCOPE
from app.services.updates_insight_sweeper import InsightSweeper, _VERIFIED_CURRENT_REASONS
from app.services.updates_materiality import ACTION_GENERATE, ACTION_SKIP, Decision
from _price_fakes import PriceFromFMPFake


class _Stub(InsightSweeper):
    """No network clients (see .claude/rules/testing.md). Every corpus is one row,
    so the gate has something to decide on; `_claim` is replaced per test."""

    def __init__(self, universe=(MARKET_SCOPE, "AAPL", "MSFT")):
        self.supabase = None
        self.fmp = None
        self.price = PriceFromFMPFake(self.fmp)
        self.vol = self
        self.news = self
        self.insights = self
        self._catalyst_day = self._enrich_day = None
        self._catalyst_count = self._enrich_count = 0
        self._catalyst_scopes = set()
        self._universe_value = list(universe)
        self.claims = []
        self.verified = None
        self.skips_recorded = None

    async def _universe(self):
        return list(self._universe_value)

    def _company_names(self, scopes):
        return {}

    def _load_state(self, scopes):
        return {}

    def _record_skips(self, skips, now):
        self.skips_recorded = skips

    async def get_batch_quotes_bulk(self, symbols):
        return []

    async def get_sigmas_bulk(self, symbols):
        return {}

    def get_cached_bulk(self, scopes, limit):
        # Dated off the REAL clock: `run_sweep` reads it, and a future-dated row is
        # dropped by the corpus window, which would make every corpus empty.
        fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        return {s: [{"external_id": f"{s}-1", "headline": f"{s} news",
                     "published_at": fresh}] for s in scopes}

    async def mark_verified_current(self, scopes, market_active):
        self.verified = (list(scopes), market_active)

    def _claim(self, scope, now, is_market_scope, earnings_window=False):
        self.claims.append((scope, is_market_scope, earnings_window))
        return False    # never generate — nothing paid in these tests

    def _consume_global_budget(self, now):
        return True


def _generate_all(**kwargs):
    return Decision(action=ACTION_GENERATE, reason="new_articles (1)")


@pytest.mark.asyncio
async def test_the_sweep_feeds_the_same_boost_into_the_gate_and_the_claim(monkeypatch):
    seen = {}

    def _recording_decide(**kwargs):
        seen[kwargs["scope"]] = kwargs["earnings_window"]
        return _generate_all()

    async def _window(now, *, fmp):
        return frozenset({"AAPL"})

    monkeypatch.setattr(mod, "decide", _recording_decide)
    monkeypatch.setattr(mod, "symbols_in_earnings_window", _window)
    monkeypatch.setattr(mod, "is_market_active", lambda: True)
    stub = _Stub()

    result = await stub.run_sweep(refresh_news=False)

    assert seen == {MARKET_SCOPE: False, "AAPL": True, "MSFT": False}
    assert sorted(stub.claims) == sorted([
        (MARKET_SCOPE, True, False), ("AAPL", False, True), ("MSFT", False, False),
    ]), "the claim must carry the SAME verdict the gate used, scope by scope"
    assert result["earnings_boosted"] == 1


@pytest.mark.asyncio
async def test_the_earnings_lookup_uses_the_sweepers_client_not_the_singleton(monkeypatch):
    stub = _Stub()
    sentinel = object()
    stub.fmp = sentinel
    stub.price = PriceFromFMPFake(None)
    seen = {}

    async def _window(now, *, fmp):
        seen["fmp"] = fmp
        return frozenset()

    monkeypatch.setattr(mod, "symbols_in_earnings_window", _window)
    monkeypatch.setattr(mod, "decide", lambda **kw: Decision(action=ACTION_SKIP, reason="no_corpus"))
    monkeypatch.setattr(mod, "is_market_active", lambda: True)
    await stub.run_sweep(refresh_news=False)
    assert seen["fmp"] is sentinel


@pytest.mark.asyncio
async def test_the_sweep_survives_an_earnings_lookup_that_raises(monkeypatch, caplog):
    async def _window(now, *, fmp):
        raise RuntimeError("calendar exploded")

    monkeypatch.setattr(mod, "symbols_in_earnings_window", _window)
    monkeypatch.setattr(mod, "decide", lambda **kw: Decision(action=ACTION_SKIP, reason="no_corpus"))
    monkeypatch.setattr(mod, "is_market_active", lambda: True)
    with caplog.at_level("WARNING"):
        result = await _Stub().run_sweep(refresh_news=False)
    assert result["earnings_boosted"] == 0
    assert "no cap boost" in caplog.text


@pytest.mark.asyncio
async def test_the_default_stubs_reach_the_degraded_path_without_network(monkeypatch):
    """The REAL service, a stub with `fmp = None`: the sweep completes, nobody is
    boosted, and no outbound call was attempted."""
    from app.services.earnings_window_service import get_earnings_window_service

    get_earnings_window_service().reset()
    before = list(conftest.BLOCKED_NETWORK_CALLS)
    seen = {}

    def _recording_decide(**kwargs):
        seen[kwargs["scope"]] = kwargs["earnings_window"]
        return Decision(action=ACTION_SKIP, reason="no_corpus")

    monkeypatch.setattr(mod, "decide", _recording_decide)
    monkeypatch.setattr(mod, "is_market_active", lambda: True)
    try:
        result = await _Stub().run_sweep(refresh_news=False)
    finally:
        get_earnings_window_service().reset()
    assert result["earnings_boosted"] == 0
    assert set(seen.values()) == {False}
    assert conftest.BLOCKED_NETWORK_CALLS == before


@pytest.mark.asyncio
async def test_the_crypto_pass_never_reads_the_earnings_calendar(monkeypatch):
    calls = []

    async def _window(now, *, fmp):
        calls.append(now)
        return frozenset({"BTCUSD"})

    monkeypatch.setattr(mod, "symbols_in_earnings_window", _window)
    monkeypatch.setattr(mod, "decide", lambda **kw: Decision(action=ACTION_SKIP, reason="no_corpus"))
    monkeypatch.setattr(mod, "is_market_active", lambda: False)
    result = await _Stub(universe=(MARKET_SCOPE, "AAPL", "BTCUSD")).run_sweep(
        refresh_news=False, crypto_only=True
    )
    assert calls == []
    assert result["earnings_boosted"] == 0


@pytest.mark.asyncio
async def test_the_stub_corpus_reaches_the_real_gate_non_empty(monkeypatch):
    """Anti-vacuity for `_Stub`: with the real `decide`, a one-row corpus dated off the
    real clock is admitted (cold_start → GENERATE → the claim recorder sees it)."""
    async def _window(now, *, fmp):
        return frozenset()

    monkeypatch.setattr(mod, "symbols_in_earnings_window", _window)
    monkeypatch.setattr(mod, "is_market_active", lambda: True)
    stub = _Stub(universe=("AAPL",))
    await stub.run_sweep(refresh_news=False)
    assert stub.claims == [("AAPL", False, False)], "the real gate saw an empty corpus"


# ── capped scopes are stamped verified-current ───────────────────────────────


def test_the_verified_current_reason_set_is_pinned():
    assert _VERIFIED_CURRENT_REASONS == {
        "fingerprint_unchanged", "daily_cap", "attempt_cap", "premarket_reserved",
    }


@pytest.mark.asyncio
async def test_capped_scopes_are_stamped_current_and_pending_ones_are_not(monkeypatch):
    reasons = {
        "FP": "fingerprint_unchanged", "DC": "daily_cap", "AC": "attempt_cap",
        "CD": "cooldown", "PR": "premarket_reserved", "NC": "no_corpus",
        "MW": "mwcb_market_only", "GE": "gate_error",
    }

    def _decide(**kwargs):
        return Decision(action=ACTION_SKIP, reason=reasons.get(kwargs["scope"], "no_corpus"))

    async def _window(now, *, fmp):
        return frozenset()

    monkeypatch.setattr(mod, "decide", _decide)
    monkeypatch.setattr(mod, "symbols_in_earnings_window", _window)
    monkeypatch.setattr(mod, "is_market_active", lambda: True)
    stub = _Stub(universe=[MARKET_SCOPE, *reasons])

    await stub.run_sweep(refresh_news=False)

    scopes, market_active = stub.verified
    assert sorted(scopes) == ["AC", "DC", "FP", "PR"]
    assert market_active is True
    # Every skip is still recorded on the state row, stamped or not.
    assert sorted(s for s, _ in stub.skips_recorded) == sorted([MARKET_SCOPE, *reasons])


@pytest.mark.asyncio
async def test_the_stamp_carries_the_sweeps_market_active_flag(monkeypatch):
    """Off-hours the crypto pass stamps the CLOSED soft TTL (4h) — `_row_to_card`
    gates `is_stale` on market activity, so a coin card can never flicker."""
    monkeypatch.setattr(mod, "decide", lambda **kw: Decision(action=ACTION_SKIP, reason="daily_cap"))
    monkeypatch.setattr(mod, "is_market_active", lambda: False)
    stub = _Stub(universe=(MARKET_SCOPE, "BTCUSD", "AAPL"))
    await stub.run_sweep(refresh_news=False, crypto_only=True)
    assert stub.verified == (["BTCUSD"], False)
