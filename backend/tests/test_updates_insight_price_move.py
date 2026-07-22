"""The sweeper's gated "why did it move" catalyst fetch (P4).

The grounded web-search catalyst is expensive, so it is fetched ONLY for a
per-ticker Unusual/Extreme move, is kill-switchable, and is bounded by a per-ET-day
cap. A None result (hard failure / no clear catalyst path) yields no price_move and
must never block the news card. No network — get_catalyst is stubbed.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import updates_insight_sweeper as mod
from app.services.updates_insight_sweeper import (
    InsightSweeper,
    MARKET_SCOPE,
    _CATALYST_DAILY_CAP,
)
from app.services.updates_materiality import (
    ACTION_GENERATE,
    TIER_EXTREME,
    TIER_NOTABLE,
    TIER_TYPICAL,
    TIER_UNUSUAL,
    Decision,
)

NOW = datetime(2026, 7, 21, 18, 0, tzinfo=timezone.utc)


def _sweeper() -> InsightSweeper:
    s = object.__new__(InsightSweeper)   # bypass __init__ (no DB/FMP clients)
    s._catalyst_day = None
    s._catalyst_count = 0
    return s


def _dec(tier: str) -> Decision:
    return Decision(action=ACTION_GENERATE, reason="x", price_band=tier)


class _StubCatalyst:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def get_catalyst(self, ticker, change_pct, window_label):
        self.calls += 1
        return self.result


@pytest.fixture
def stub(monkeypatch):
    st = _StubCatalyst(
        {"tag": "Analyst Downgrade", "reason": "Cut to Underweight.", "sources": [{"uri": "x"}]}
    )
    monkeypatch.setattr(mod, "get_price_catalyst_service", lambda: st)
    monkeypatch.setattr(mod.settings, "PRICE_CATALYST_AI_ENABLED", True)
    return st


@pytest.mark.asyncio
async def test_catalyst_only_for_big_per_ticker_moves(stub):
    s = _sweeper()
    q = {"changePercentage": -8.2}

    pm = await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, q)
    assert pm == {
        "tier": "Extreme", "change_pct": -8.2,
        "catalyst_tag": "Analyst Downgrade", "reason": "Cut to Underweight.",
    }
    assert await s._maybe_price_move("AAPL", _dec(TIER_UNUSUAL), NOW, q) is not None
    # Not big enough → no paid search.
    assert await s._maybe_price_move("AAPL", _dec(TIER_NOTABLE), NOW, q) is None
    assert await s._maybe_price_move("AAPL", _dec(TIER_TYPICAL), NOW, q) is None
    # Market scope keeps its news summary — no per-ticker catalyst.
    assert await s._maybe_price_move(MARKET_SCOPE, _dec(TIER_EXTREME), NOW, q) is None


@pytest.mark.asyncio
async def test_kill_switch_skips_the_search(stub, monkeypatch):
    monkeypatch.setattr(mod.settings, "PRICE_CATALYST_AI_ENABLED", False)
    s = _sweeper()
    assert await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is None
    assert stub.calls == 0


@pytest.mark.asyncio
async def test_none_result_yields_no_price_move(monkeypatch):
    st = _StubCatalyst(None)   # hard failure / no result
    monkeypatch.setattr(mod, "get_price_catalyst_service", lambda: st)
    monkeypatch.setattr(mod.settings, "PRICE_CATALYST_AI_ENABLED", True)
    s = _sweeper()
    assert await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is None


@pytest.mark.asyncio
async def test_day_cap_bounds_calls_and_resets_next_day(stub):
    s = _sweeper()
    for i in range(_CATALYST_DAILY_CAP):
        assert await s._maybe_price_move(f"T{i}", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is not None
    # One past the cap → skipped, and no further search fired.
    calls_at_cap = stub.calls
    assert await s._maybe_price_move("OVER", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is None
    assert stub.calls == calls_at_cap
    # A new ET trading day resets the budget.
    next_day = NOW + timedelta(days=1)
    assert await s._maybe_price_move("NEWDAY", _dec(TIER_EXTREME), next_day, {"changePercentage": -8.0}) is not None
