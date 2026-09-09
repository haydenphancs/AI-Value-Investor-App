"""A degradation gate that never fires is worse than no gate — it reads as protection.

Two of them, both caching a total upstream failure as though it were data:

  1. `etf_service._get_fundamentals` guarded with
     `if not any([etf_info, holders, profile])`. But `profile` is a PROJECTION over nine
     FIXED keys, so an empty upstream profile still produces
     `{"companyName": None, "beta": None, ...}` — a nine-key dict, which is TRUTHY.
     `any([...])` was therefore always True and the gate was dead code: an
     every-call-failed bundle went into the 12h memory cache AND was upserted to Supabase.

  2. `profitability_snapshot_service` scores a missing metric with the sentinel 3
     ("neutral if no data"). When every leg fails, all five sub-scores are 3, the weighted
     mean is exactly 3.0, and the card renders a confident "3/5 Moderate" beside five
     em-dashes — then persisted it for 24h, so one transient FMP 429 pinned a fabricated
     rating on a stock for a day. Its sibling `stock_overview_service` already had the
     right pattern: serve the response, do NOT persist it.
"""

from __future__ import annotations

import asyncio
import inspect
import re

import pytest

from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse

PROFILE_KEYS = ("companyName", "beta", "averageVolume", "lastDividend", "lastDiv",
                "website", "description", "ipoDate", "marketCap")


# ── 1. the ETF gate must test VALUES, not the container ─────────────────────

def test_an_all_none_projection_is_truthy_which_is_why_the_gate_was_dead():
    """Documents the trap itself, so a 'simplification' back to `bool(dict)` is caught."""
    empty_profile: dict = {}
    projection = {k: empty_profile.get(k) for k in PROFILE_KEYS}
    assert projection, "a fixed-key projection is ALWAYS truthy — that was the bug"
    assert not any(v is not None for v in projection.values())


def test_the_etf_gate_inspects_the_projected_values():
    from app.services import etf_service

    src = inspect.getsource(etf_service.ETFService._get_fundamentals)
    stripped = "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )
    assert "_profile_has_data" in stripped, (
        "the gate must test whether any projected VALUE is present"
    )
    assert 'any([bundle["etf_info"], bundle["holders"], bundle["profile"]])' not in stripped, (
        "testing bundle['profile'] directly is always True — the gate is dead again"
    )
    # And the value-test must be what the gate consumes.
    gate = stripped[stripped.find("if not any(["):]
    assert "_profile_has_data" in gate[:200]


@pytest.mark.parametrize("profile,expected_fires", [
    ({}, True),
    ({k: None for k in PROFILE_KEYS}, True),
    ({"companyName": "SPDR S&P 500 ETF"}, False),
    ({"marketCap": 0}, False),          # 0 is DATA, not absence
])
def test_the_gate_logic_fires_exactly_when_nothing_was_measured(profile, expected_fires):
    projection = {k: profile.get(k) for k in PROFILE_KEYS}
    has_data = any(v is not None for v in projection.values())
    fires = not any([{}, [], has_data])      # etf_info / holders empty too
    assert fires is expected_fires


# ── 2. a sentinel rating must not be persisted ──────────────────────────────

def _snapshot(metric_values, rating):
    return SnapshotItemResponse(
        category="Profitability", rating=rating, verdict="Moderate",
        metrics=[SnapshotMetricResponse(name=f"M{i}", value=v, metric_key=f"m{i}")
                 for i, v in enumerate(metric_values)],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("values,should_persist", [
    (["—", "—", "—", "—", "—"], False),      # every leg failed -> fabricated 3/5
    (["42.10%", "—", "—", "—", "—"], True),  # one real metric -> a real rating
    (["0.00%", "—", "—", "—", "—"], True),   # 0% is MEASURED, not absent
])
async def test_a_snapshot_with_no_measured_metric_is_not_persisted(
    monkeypatch, values, should_persist
):
    from app.services import profitability_snapshot_service as m

    svc = m.get_profitability_snapshot_service()
    persisted: list = []
    monkeypatch.setattr(svc, "_upsert_supabase_cache", lambda t, r: persisted.append(t))
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda t: None)

    async def _fake_compute(ticker):
        return _snapshot(values, 3)

    monkeypatch.setattr(svc, "_compute", _fake_compute)
    m._cache.clear()
    m._inflight.clear()

    result = await svc.get_profitability_snapshot("ZZZZ")

    assert result is not None, "the response must still be SERVED, only not persisted"
    cached = bool(m._cache)
    assert cached is should_persist, (
        f"values={values}: cached={cached}, expected {should_persist}"
    )
    m._cache.clear()


def test_the_sentinel_score_is_still_three_so_the_gate_is_needed():
    """Anti-vacuity: if the sentinel changes, this whole gate needs rethinking."""
    from app.services.profitability_snapshot_service import _profitability_score

    assert _profitability_score(None, 0.30) == 3, (
        "a missing metric no longer scores the neutral sentinel — re-read the gate"
    )


def test_the_gate_returns_before_the_persist_call():
    from app.services import profitability_snapshot_service as m

    src = inspect.getsource(m.ProfitabilitySnapshotService.get_profitability_snapshot)
    stripped = "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )
    gate = stripped.find("if not _measured")
    persist = stripped.find("_upsert_supabase_cache")
    assert gate != -1 and persist != -1
    assert gate < persist, "the gate must short-circuit BEFORE the Supabase upsert"
