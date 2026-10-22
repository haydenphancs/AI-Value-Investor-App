"""Unit tests for industry_moat_benchmark_service helpers.

These cover the pure math (winsorize + percentile + sample-size
threshold). No Supabase, no FMP — the heavy compute path is exercised
end-to-end via the bootstrap script and manual verification.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `import app...` work without a conftest.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.industry_moat_benchmark_service import (
    MIN_SAMPLE_SIZE,
    _percentile,
    _winsorize_p10_p90,
)


# ── _winsorize_p10_p90 ──────────────────────────────────────────────


def test_winsorize_below_10_samples_returns_input_unchanged():
    # Percentile-based winsorization on tiny samples is more noise than
    # signal; the helper should pass through unchanged.
    vals = [1.0, 2.0, 9.5, 10.0]
    assert _winsorize_p10_p90(vals) == vals


def test_winsorize_caps_extreme_values_at_p10_p90():
    # 20 values: 0..19. p10 ≈ 2.0, p90 ≈ 18.0.
    vals = [float(i) for i in range(20)]
    out = _winsorize_p10_p90(vals)
    # Lowest values clamped up to p10 (~2.0), highest clamped down to p90 (~18.0).
    assert min(out) == 2.0
    assert max(out) == 18.0
    # Order preserved (caller may correlate with per-ticker metadata).
    assert len(out) == len(vals)
    # Middle values untouched.
    assert out[10] == 10.0


def test_winsorize_preserves_count_and_dtype():
    vals = [1.5, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 9.5]
    out = _winsorize_p10_p90(vals)
    assert len(out) == 10
    for v in out:
        assert isinstance(v, float)


# ── _percentile ─────────────────────────────────────────────────────


def test_percentile_empty_returns_none():
    assert _percentile([], 0.5) is None


def test_percentile_single_value():
    assert _percentile([4.2], 0.5) == 4.2
    assert _percentile([4.2], 0.25) == 4.2


def test_percentile_linear_interpolation():
    # Sorted [0,1,2,3,4,5,6,7,8,9]. p50 should be 4.5 (midpoint).
    assert _percentile([float(i) for i in range(10)], 0.50) == pytest.approx(4.5)
    # p25 ≈ 2.25, p75 ≈ 6.75.
    assert _percentile([float(i) for i in range(10)], 0.25) == pytest.approx(2.25)
    assert _percentile([float(i) for i in range(10)], 0.75) == pytest.approx(6.75)


def test_percentile_edges():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(vals, 0.0) == 1.0
    assert _percentile(vals, 1.0) == 5.0


# ── MIN_SAMPLE_SIZE ─────────────────────────────────────────────────


def test_min_sample_size_matches_sector_benchmark_constant():
    # The threshold mirrors sector_benchmark_service.MIN_SAMPLE_SIZE so
    # the two related tables behave consistently for sparse industries.
    from app.services.sector_benchmark_service import (
        MIN_SAMPLE_SIZE as SECTOR_MIN,
    )
    assert MIN_SAMPLE_SIZE == SECTOR_MIN == 5


# ── recompute_all clears the Peer Avg lookup cache (2026-10-22) ─────


import uuid  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

from app.services import industry_moat_benchmark_service as imb  # noqa: E402
from app.services.moat_scoring_service import (  # noqa: E402
    PILLAR_ORDER,
    MoatScores,
    PillarResult,
)


class _MoatTable:
    """One in-memory `industry_moat_benchmarks`, unique on (industry, pillar_name): the
    recompute's upserts land in it and the lookup reads from it."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = {(r["industry"], r["pillar_name"]): dict(r) for r in rows}
        self.reads = 0

    def table(self, _name):
        store = self

        class _Q:
            upserted = None
            industry = None

            def upsert(self, row, **_k):
                self.upserted = row
                return self

            def select(self, *_a, **_k):
                return self

            def eq(self, col, val):
                if col == "industry":
                    self.industry = val
                return self

            def execute(self):
                if self.upserted is not None:
                    r = dict(self.upserted)
                    store.rows[(r["industry"], r["pillar_name"])] = r
                    return SimpleNamespace(data=[r])
                store.reads += 1
                return SimpleNamespace(data=[
                    dict(r) for (ind, _p), r in store.rows.items() if ind == self.industry
                ])

        return _Q()


class _FakeFMP:
    async def get_company_profile(self, ticker):
        return {"symbol": ticker, "sector": "Industrials"}

    async def get_income_statement(self, *_a, **_k):
        return [{"date": "2025-12-31", "revenue": 100.0}]

    async def get_balance_sheet(self, *_a, **_k):
        return []

    async def get_financial_ratios(self, *_a, **_k):
        return []

    async def get_earning_call_transcript(self, *_a, **_k):
        return None


@pytest.mark.asyncio
async def test_recompute_clears_the_peer_avg_lookup_cache(monkeypatch):
    industry = f"Test Industry {uuid.uuid4().hex[:8]}"
    pillar = PILLAR_ORDER[0]
    table = _MoatTable([{
        "industry": industry, "pillar_name": pillar, "peer_average_score": 4.0,
        "model_version": imb.MODEL_VERSION,
    }])
    lookup = imb.IndustryMoatBenchmarkLookup.__new__(imb.IndustryMoatBenchmarkLookup)
    lookup.supabase = table
    try:
        # A report built before the recompute caches the old Peer Avg for the hour.
        assert lookup.get_pillar_benchmarks(industry) == {pillar: 4.0}
        assert lookup.get_pillar_benchmarks(industry) == {pillar: 4.0}
        assert table.reads == 1, "the second read is served from the 1 h cache"

        svc = imb.IndustryMoatBenchmarkService.__new__(imb.IndustryMoatBenchmarkService)
        svc.fmp = _FakeFMP()
        svc.supabase = table

        async def _no_tam(_industry, _samples, **_k):
            return None

        monkeypatch.setattr(svc, "_fetch_industry_tam", _no_tam)
        monkeypatch.setattr(
            imb, "score_moat_dimensions",
            lambda **_k: MoatScores({pillar: PillarResult(name=pillar, score=8.0)}),
        )
        peers = {f"T{i}": 1.0e10 - i for i in range(MIN_SAMPLE_SIZE + 1)}
        monkeypatch.setattr(
            imb, "load_universe",
            lambda _f: [{"industry": industry, "market_caps": dict(peers)}],
        )
        summary = await svc.recompute_all(skip_if_fresh_hours=None)
        assert summary["pillars_written"] == 1
        assert table.rows[(industry, pillar)]["peer_average_score"] == 8.0

        # The next report reads the recomputed row, not the cached pre-recompute average.
        assert lookup.get_pillar_benchmarks(industry) == {pillar: 8.0}
        assert table.reads == 2
    finally:
        imb._lookup_cache.pop(industry, None)


@pytest.mark.asyncio
async def test_a_recompute_that_raises_keeps_the_peer_avg_cache(monkeypatch):
    """Nothing was written (empty universe → raise before any write): the cached averages
    are still the stored ones, so the cache is left alone."""
    industry = f"Test Industry {uuid.uuid4().hex[:8]}"
    imb._lookup_cache[industry] = (imb.time.time(), {"Brand Power": 4.0})
    try:
        svc = imb.IndustryMoatBenchmarkService.__new__(imb.IndustryMoatBenchmarkService)
        svc.fmp = _FakeFMP()
        svc.supabase = _MoatTable([])
        monkeypatch.setattr(imb, "load_universe", lambda _f: [])
        with pytest.raises(imb.IndustryMoatBenchmarkRecomputeSkipped):
            await svc.recompute_all(skip_if_fresh_hours=None)
        assert industry in imb._lookup_cache
    finally:
        imb._lookup_cache.pop(industry, None)


def test_model_version_is_the_newest_known_vintage():
    """Every vintage the lookup ranks, oldest first, the current one LAST — a row of any
    earlier vintage is served as recompute-pending, never as current."""
    order = imb._MODEL_VERSION_ORDER
    assert order[-1] == imb.MODEL_VERSION
    assert len(set(order)) == len(order)
    assert "moat_v1.2026-10-11" in order
    assert imb._vintage_rank("moat_v1.2026-10-11") < imb._vintage_rank(imb.MODEL_VERSION)
