"""The daily σ precompute + cache read path for the Updates volatility trigger.

Covers the pure parse/σ helpers and the sweeper read path's degrade-to-None
guarantee (a DB failure must never raise into the 5-min sweep). No network.
"""

import pytest

from app.services import volatility_cache_service as mod
from app.services.price_volatility import _BASELINE_DAYS, _daily_returns, _std_dev_pop
from app.services.volatility_cache_service import (
    VolatilityCacheService,
    _chronological_closes,
    _sigma_from_closes,
)


# ── Parsing FMP historical → chronological closes ─────────────────────────

def test_chronological_closes_reverses_newest_first_and_skips_bad():
    hist = {"historical": [
        {"date": "2026-07-03", "close": 103.0},   # newest first (FMP order)
        {"date": "2026-07-02", "close": None},     # skipped
        {"date": "2026-07-01", "close": "oops"},   # skipped (unparseable)
        {"date": "2026-06-30", "close": 100.0},
    ]}
    assert _chronological_closes(hist) == [100.0, 103.0]   # oldest→newest


def test_chronological_closes_accepts_flat_list_and_junk():
    assert _chronological_closes([{"close": 5}, {"close": 4}]) == [4.0, 5.0]
    assert _chronological_closes(None) == []
    assert _chronological_closes({"nope": 1}) == []


# ── σ from closes (mirrors price_volatility) ──────────────────────────────

def test_sigma_none_when_history_too_short():
    sigma, sample = _sigma_from_closes([100.0] * 20)   # < 30 closes
    assert sigma is None
    assert sample == 19


def test_sigma_none_when_all_flat_zero_variance():
    sigma, _ = _sigma_from_closes([100.0] * 50)   # returns all 0 → σ=0 → None
    assert sigma is None


def test_sigma_matches_the_shared_helper_over_the_baseline():
    prices = [100.0]
    for i in range(1, 200):
        prices.append(prices[-1] * (1.012 if i % 2 else 0.991))
    sigma, sample = _sigma_from_closes(prices)
    baseline = prices[-(_BASELINE_DAYS + 1):]
    expected = _std_dev_pop(_daily_returns(baseline))
    assert sigma == expected and sigma > 0
    assert sample == len(_daily_returns(baseline))


# ── Sweeper read path degrades, never raises ──────────────────────────────

class _NoDBService(VolatilityCacheService):
    def __init__(self):
        self.supabase = None   # any DB access raises → _select_fresh returns {}
        self.fmp = None


@pytest.mark.asyncio
async def test_get_sigmas_bulk_degrades_to_none_on_db_failure():
    mod._mem.clear()
    svc = _NoDBService()
    out = await svc.get_sigmas_bulk(["AAPL", "MSFT", "aapl", "", None])
    # Deduped + uppercased; every symbol maps to None (→ gate uses the fixed band).
    assert out == {"AAPL": None, "MSFT": None}


@pytest.mark.asyncio
async def test_get_sigmas_bulk_empty_input():
    assert await _NoDBService().get_sigmas_bulk([]) == {}


@pytest.mark.asyncio
async def test_get_sigmas_bulk_serves_the_in_memory_tier():
    mod._mem.clear()
    import time
    mod._mem["NVDA"] = (time.monotonic(), 0.031)
    out = await _NoDBService().get_sigmas_bulk(["NVDA"])
    assert out == {"NVDA": 0.031}   # served from memory, no DB touched
