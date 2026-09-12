"""A degraded shared input must not be amplified, nor pinned past the outage.

Two confirmed findings (F36, F25), same shape as the FRED one: the system answers a
transient by doing MORE work, or by remembering the nothing it got.

F36 — `PriceService.get_quotes`: when `get_universe` returns `{}` (a screener 429 or a
Supabase blip), EVERY requested symbol is "missing", so the per-symbol fallback issues one
`/stable/profile` call per symbol per caller. A cold Home is ~30 symbols and the Tracking
feed polls every 30 s, so one screener 429 becomes hundreds of profile calls a minute
against the same rate-limited upstream.

F25 — `get_scanners`: `_build_scanner_groups` never raises, so an all-empty build (no
gainers, no losers, no volume rows — i.e. the universe was unavailable) was an ordinary
success and went into the 20-minute cache, over a `market_movers_service` degraded memo
that lasts 15 SECONDS precisely so the next request can recover.
"""

from __future__ import annotations

import time
from typing import Any, Dict

import pytest

import app.services.home_dashboard_service as hd
import app.services.price_service as ps_module
from app.schemas.home_dashboard import ScannerGroupResponse, ScannerGroupsResponse
from app.services.price_service import PriceService, _cache


@pytest.fixture(autouse=True)
def _clear():
    _cache.clear()
    yield
    _cache.clear()


# ── F36 ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_dead_universe_does_not_fan_out_one_profile_call_per_symbol(monkeypatch, caplog):
    profile_calls = []

    svc = PriceService()

    async def _universe():
        return {}                      # the degraded shape get_universe serves

    async def _quote(sym):
        profile_calls.append(sym)
        return {"symbol": sym, "price": 1.0}

    monkeypatch.setattr(svc, "_get_universe", _universe, raising=False)
    monkeypatch.setattr(svc, "get_quote", _quote, raising=False)

    async def _closes(symbols):
        return {}

    monkeypatch.setattr(svc, "get_close_snapshots", _closes, raising=False)
    with caplog.at_level("WARNING", logger="app.services.price_service"):
        out = await svc.get_quotes([f"SYM{i}" for i in range(30)])
    assert profile_calls == [], (
        f"the outage was amplified into {len(profile_calls)} profile calls"
    )
    assert out == {}, "an unavailable universe must degrade, not fabricate"
    assert any("skipping the per-symbol profile fallback" in r.getMessage()
               for r in caplog.records), "the degradation must be visible in the log"


@pytest.mark.asyncio
async def test_a_healthy_universe_still_falls_back_for_a_genuinely_missing_symbol(monkeypatch):
    """Anti-vacuity control: the fallback exists for foreign listings and microcaps."""
    profile_calls = []
    svc = PriceService()

    async def _universe():
        return {"AAPL": {"symbol": "AAPL", "price": 190.0}}

    async def _quote(sym):
        profile_calls.append(sym)
        return {"symbol": sym, "price": 3.0}

    async def _closes(symbols):
        return {}

    monkeypatch.setattr(svc, "_get_universe", _universe, raising=False)
    monkeypatch.setattr(svc, "get_quote", _quote, raising=False)
    monkeypatch.setattr(svc, "get_close_snapshots", _closes, raising=False)
    out = await svc.get_quotes(["AAPL", "TINYCO"])
    assert profile_calls == ["TINYCO"], "the single-symbol fallback was lost"
    assert set(out) == {"AAPL", "TINYCO"}


# ── F25 ─────────────────────────────────────────────────────────────────────────────


def _svc() -> "hd.HomeDashboardService":
    svc = hd.HomeDashboardService.__new__(hd.HomeDashboardService)
    svc._scanner_cache, svc._scanner_inflight = {}, {}
    return svc


@pytest.mark.asyncio
async def test_an_all_empty_scanner_build_is_held_seconds_not_twenty_minutes(monkeypatch, caplog):
    svc = _svc()

    async def _empty():
        return ScannerGroupsResponse()

    monkeypatch.setattr(svc, "_build_scanner_groups", _empty, raising=False)
    with caplog.at_level("WARNING", logger="app.services.home_dashboard_service"):
        await svc.get_scanners()
    stamp, _result = svc._scanner_cache[hd._SCANNER_CACHE_KEY]
    age_budget = hd._SCANNER_CACHE_TTL_SECONDS - (time.time() - stamp)
    assert age_budget <= hd._SCANNER_DEGRADED_TTL_SECONDS + 2, (
        f"a degraded build is cached for {age_budget:.0f}s — it outlives the 15s degraded "
        "memo the movers service keeps so the next request can recover"
    )
    assert any("Scanners built empty" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_real_build_keeps_the_full_twenty_minute_ttl(monkeypatch):
    svc = _svc()

    async def _good():
        return ScannerGroupsResponse(
            movers=ScannerGroupResponse(kind="movers", gainers=[], losers=[]),
        )

    monkeypatch.setattr(svc, "_build_scanner_groups", _good, raising=False)
    await svc.get_scanners()
    stamp, _ = svc._scanner_cache[hd._SCANNER_CACHE_KEY]
    assert time.time() - stamp < 2, "a good build must not be aged"
