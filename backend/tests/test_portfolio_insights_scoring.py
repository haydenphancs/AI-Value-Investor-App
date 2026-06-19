"""
Comprehensive tests for Portfolio Insights diversification scoring.

Model: each dimension earns POINTS out of a budget; the budgets sum to 100, so
the bars add up to the overall score (the additive "old way", but driven by a
weight-responsive normalized-HHI quality so nothing saturates). Geography is
excluded (US-only); there are no nudges and no letter grade.

Three layers:
  1. Pure helpers (HHI, normalized score, effective-N, cap buckets, zone bands).
  2. `score_holdings` — additive points, the score == sum of points invariant,
     the weight-responsiveness regression, allocations, edge cases.
  3. The per-portfolio data path with INLINE fakes for Supabase + FMP.

No live network / Supabase — fakes are injected inline per the suite rules.
"""

from __future__ import annotations

import math

import pytest

from app.schemas.tracking import PortfolioHoldingResponse
from app.services import portfolio_insights_service as svc
from app.services.portfolio_insights_service import (
    MIN_HOLDINGS,
    _cap_bucket,
    _top5_score,
    _zone,
    effective_holdings,
    hhi,
    normalized_hhi_score,
    score_holdings,
)


def _h(
    ticker: str,
    value: float,
    sector: str | None = "Technology",
    country: str = "US",
    market_cap: float | None = 300_000_000_000.0,
) -> PortfolioHoldingResponse:
    return PortfolioHoldingResponse(
        id=ticker,
        ticker=ticker,
        company_name=ticker,
        market_value=value,
        shares=None,
        sector=sector,
        asset_type="Stock",
        country=country,
        market_cap=market_cap,
    )


# ════════════════════════════ 1. PURE HELPERS ════════════════════════════


def test_hhi_known_values():
    assert hhi([1.0]) == pytest.approx(1.0)
    assert hhi([0.5, 0.5]) == pytest.approx(0.5)
    assert hhi([0.25] * 4) == pytest.approx(0.25)


@pytest.mark.parametrize(
    "weights,expected",
    [
        ([0.5, 0.5], 2.0),
        ([0.25] * 4, 4.0),
        ([1.0], 1.0),
        ([0.9, 0.1], 1.0 / (0.81 + 0.01)),
    ],
)
def test_effective_holdings_is_inverse_hhi(weights, expected):
    assert effective_holdings(weights) == pytest.approx(expected)
    assert effective_holdings(weights) == pytest.approx(1.0 / hhi(weights))


def test_effective_holdings_zero_safe():
    assert effective_holdings([]) == 0.0
    assert effective_holdings([0.0, 0.0]) == 0.0


def test_normalized_hhi_score_bounds_and_anchors():
    assert normalized_hhi_score([1.0], 1) == 0.0
    assert normalized_hhi_score([], 0) == 0.0
    assert normalized_hhi_score([0.5, 0.5], 2) == pytest.approx(100.0)
    assert normalized_hhi_score([0.25] * 4, 4) == pytest.approx(100.0)
    assert normalized_hhi_score([1.0, 0.0], 2) == pytest.approx(0.0)


def test_normalized_hhi_score_monotonic_in_skew():
    s_equal = normalized_hhi_score([0.5, 0.5], 2)
    s_mild = normalized_hhi_score([0.7, 0.3], 2)
    s_hard = normalized_hhi_score([0.9, 0.1], 2)
    assert s_equal > s_mild > s_hard
    for s in (s_equal, s_mild, s_hard):
        assert 0.0 <= s <= 100.0


@pytest.mark.parametrize("n", [2, 3, 4, 5])
def test_top5_score_full_for_small_books(n):
    assert _top5_score([1.0 / n] * n, n) == pytest.approx(100.0)


def test_top5_score_penalizes_large_books():
    assert _top5_score([0.1] * 10, 10) == pytest.approx(100.0)
    weights = [0.18] * 5 + [0.02] * 5
    assert 0.0 <= _top5_score(weights, 10) < 60.0


@pytest.mark.parametrize(
    "cap,bucket",
    [
        (300e9, "Mega Cap"),
        (200e9, "Mega Cap"),
        (199.9e9, "Large Cap"),
        (10e9, "Large Cap"),
        (9.9e9, "Mid Cap"),
        (2e9, "Mid Cap"),
        (1.99e9, "Small Cap"),
        (0, None),
        (-5, None),
        (None, None),
    ],
)
def test_cap_bucket_boundaries(cap, bucket):
    assert _cap_bucket(cap) == bucket


@pytest.mark.parametrize(
    "ratio,zone",
    [(100, "green"), (70, "green"), (69, "yellow"), (40, "yellow"),
     (39, "red"), (0, "red")],
)
def test_zone_bands(ratio, zone):
    assert _zone(ratio) == zone


# ════════════════════════════ 2. score_holdings ══════════════════════════


def test_returns_none_below_min_holdings():
    assert MIN_HOLDINGS == 2
    assert score_holdings([]) is None
    assert score_holdings([_h("ORCL", 1000)]) is None


def test_returns_none_when_total_value_nonpositive():
    assert score_holdings([_h("A", 0), _h("B", 0)]) is None


def test_score_equals_sum_of_points():
    """The defining property: each bar's points add up to the overall score."""
    res = score_holdings([
        _h("AAPL", 5000, "Technology", "US", 3e12),
        _h("JPM", 5000, "Financial Services", "US", 5e11),
        _h("PFE", 5000, "Healthcare", "US", 2e11),
    ])
    assert res is not None
    assert sum(s.points for s in res.sub_scores) == res.score
    assert 0 <= res.score <= 100


def test_max_points_sum_to_100_with_caps():
    res = score_holdings([_h("A", 5000, "Technology", "US", 4e11),
                          _h("B", 5000, "Healthcare", "US", 1e9)])
    assert res is not None
    assert {s.key for s in res.sub_scores} == {"position", "sector", "single_top5", "marketcap"}
    assert sum(s.max_points for s in res.sub_scores) == 100


def test_max_points_sum_to_100_without_caps():
    res = score_holdings([_h("A", 5000, "Technology", "US", None),
                          _h("B", 5000, "Healthcare", "US", None)])
    assert res is not None
    assert {s.key for s in res.sub_scores} == {"position", "sector", "single_top5"}
    assert sum(s.max_points for s in res.sub_scores) == 100
    assert "marketcap" not in {s.key for s in res.sub_scores}


def test_no_region_grade_or_nudge_fields():
    res = score_holdings([_h("A", 5000, "Technology"), _h("B", 5000, "Healthcare")])
    assert res is not None
    payload = res.model_dump()
    for removed in ("grade", "region_allocations", "nudges"):
        assert removed not in payload
    assert "region" not in {s.key for s in res.sub_scores}


def test_score_responds_to_weight_split():
    """The original bug: 20 vs 100 CRM shares produced the same number."""
    skewed = score_holdings([_h("ORCL", 12_000), _h("CRM", 3_000)])      # ~80/20
    balanced = score_holdings([_h("ORCL", 12_000), _h("CRM", 12_000)])   # 50/50
    assert skewed and balanced
    assert balanced.score > skewed.score
    pos_skew = next(s for s in skewed.sub_scores if s.key == "position")
    pos_bal = next(s for s in balanced.sub_scores if s.key == "position")
    assert pos_bal.points > pos_skew.points


def test_well_diversified_two_holdings_scores_high():
    res = score_holdings([
        _h("ORCL", 10_000, "Technology", "US", 4e11),    # Mega
        _h("NESN", 10_000, "Consumer Defensive", "US", 1e9),  # Small
    ])
    assert res is not None
    assert res.score >= 85
    assert res.zone == "green"


def test_concentrated_single_sector_scores_low():
    res = score_holdings([_h("ORCL", 12_000), _h("CRM", 3_000)])  # both Tech, US, mega
    assert res is not None
    assert res.score < 50


def test_real_sectors_not_collapsed_to_other():
    res = score_holdings([_h("ORCL", 10_000, "Technology"), _h("JNJ", 10_000, "Healthcare")])
    assert res is not None
    names = {a.name for a in res.sector_allocations}
    assert names == {"Technology", "Healthcare"}
    assert res.sector_count == 2


def test_missing_sector_becomes_other_bucket():
    res = score_holdings([_h("A", 10_000, sector=None), _h("B", 10_000, sector=None)])
    assert res is not None
    assert [a.name for a in res.sector_allocations] == ["Other"]


def test_allocations_sum_to_100():
    res = score_holdings([
        _h("ORCL", 6_000, "Technology", "US", 4e11),
        _h("JNJ", 3_000, "Healthcare", "US", 4e11),
        _h("NESN", 1_000, "Consumer Defensive", "US", 2e9),
    ])
    assert res is not None
    for allocs in (res.sector_allocations, res.marketcap_allocations):
        assert sum(a.percentage for a in allocs) == pytest.approx(100.0, abs=0.2)


def test_subscore_points_within_budget_and_zone_consistent():
    res = score_holdings([
        _h("ORCL", 6_000, "Technology", "US", 4e11),
        _h("JNJ", 4_000, "Healthcare", "US", 4e11),
    ])
    assert res is not None
    for s in res.sub_scores:
        assert 0 <= s.points <= s.max_points
        ratio = round(s.points / s.max_points * 100) if s.max_points else 0
        assert s.zone == _zone(ratio)


def test_effective_holdings_reported_rounded():
    res = score_holdings([_h("A", 7_000, "Technology", "US", 4e11),
                          _h("B", 3_000, "Healthcare", "US", 4e11)])
    assert res is not None
    assert res.effective_holdings == pytest.approx(round(1.0 / hhi([0.7, 0.3]), 1))


# ════════════════════════ 3. DATA PATH (inline fakes) ════════════════════


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, store, name):
        self.store = store
        self.name = name

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def update(self, vals):
        if self.store.raise_on_update:
            raise RuntimeError("column does not exist (simulated pre-migration)")
        self.store.updates.append((self.name, dict(vals)))
        return self

    def execute(self):
        return _FakeResult(list(self.store.data.get(self.name, [])))


class _FakeSupabase:
    def __init__(self, data, raise_on_update=False):
        self.data = data
        self.updates = []
        self.raise_on_update = raise_on_update

    def table(self, name):
        return _FakeTable(self, name)


class _FakeFMP:
    def __init__(self, profiles=None, prices=None):
        self._profiles = profiles or []
        self._prices = prices or {}

    async def get_company_profiles_batch(self, tickers):
        want = {t.upper() for t in tickers}
        return [p for p in self._profiles if str(p.get("symbol", "")).upper() in want]

    async def get_stock_price_quote(self, ticker):
        price = self._prices.get(ticker.upper())
        return {"price": price} if price is not None else {}


def _install_fakes(monkeypatch, supabase, fmp):
    monkeypatch.setattr(svc, "get_supabase", lambda: supabase)
    monkeypatch.setattr(svc, "get_fmp_client", lambda: fmp)


@pytest.mark.asyncio
async def test_get_portfolio_holdings_join_and_enrichment(monkeypatch):
    supabase = _FakeSupabase({
        "portfolio_items": [
            {"ticker": "ORCL", "shares": 100, "market_value": None},
            {"ticker": "CRM", "shares": None, "market_value": 3000},
            {"ticker": "ZZZ", "shares": None, "market_value": None},  # not a holding
        ],
        "watchlist_items": [
            {"ticker": "ORCL", "user_id": "u1", "company_name": "Oracle",
             "sector": "Technology", "market_cap": 4e11, "country": "US",
             "asset_type": "Stock", "industry": "Software", "beta": 1.0},
            {"ticker": "CRM", "user_id": "u1", "company_name": "Salesforce",
             "sector": None, "market_cap": None, "country": "US",
             "asset_type": "Stock"},
        ],
    })
    fmp = _FakeFMP(
        profiles=[{"symbol": "CRM", "sector": "Technology", "marketCap": 2.5e11,
                   "country": "US", "industry": "Software", "beta": 1.2}],
        prices={"ORCL": 120.0},
    )
    _install_fakes(monkeypatch, supabase, fmp)

    holdings = await svc.PortfolioInsightsService().get_portfolio_holdings("u1", "p1")

    assert [h.ticker for h in holdings] == ["ORCL", "CRM"]  # ZZZ excluded; value desc
    orcl, crm = holdings
    assert orcl.market_value == pytest.approx(12_000)   # live: 100 * 120
    assert crm.market_value == pytest.approx(3_000)     # stored
    assert crm.sector == "Technology"                   # enriched
    assert crm.market_cap == pytest.approx(2.5e11)
    assert any(name == "watchlist_items" for name, _ in supabase.updates)


@pytest.mark.asyncio
async def test_get_portfolio_holdings_empty_when_no_holdings(monkeypatch):
    supabase = _FakeSupabase({
        "portfolio_items": [{"ticker": "ORCL", "shares": None, "market_value": None}],
        "watchlist_items": [],
    })
    _install_fakes(monkeypatch, supabase, _FakeFMP())
    holdings = await svc.PortfolioInsightsService().get_portfolio_holdings("u1", "p1")
    assert holdings == []


@pytest.mark.asyncio
async def test_enrichment_writeback_failure_degrades(monkeypatch):
    """Pre-migration safety: if writing the new columns raises, scoring still
    proceeds from whatever metadata is present (best-effort enrichment)."""
    supabase = _FakeSupabase(
        {
            "portfolio_items": [
                {"ticker": "ORCL", "shares": None, "market_value": 6000},
                {"ticker": "JNJ", "shares": None, "market_value": 4000},
            ],
            "watchlist_items": [
                {"ticker": "ORCL", "user_id": "u1", "sector": None,
                 "market_cap": None, "country": "US", "asset_type": "Stock"},
                {"ticker": "JNJ", "user_id": "u1", "sector": None,
                 "market_cap": None, "country": "US", "asset_type": "Stock"},
            ],
        },
        raise_on_update=True,
    )
    fmp = _FakeFMP(profiles=[
        {"symbol": "ORCL", "sector": "Technology", "marketCap": 4e11},
        {"symbol": "JNJ", "sector": "Healthcare", "marketCap": 4e11},
    ])
    _install_fakes(monkeypatch, supabase, fmp)

    holdings = await svc.PortfolioInsightsService().get_portfolio_holdings("u1", "p1")
    assert len(holdings) == 2
    assert {h.sector for h in holdings} == {"Technology", "Healthcare"}


@pytest.mark.asyncio
async def test_compute_insights_for_portfolio_end_to_end(monkeypatch):
    supabase = _FakeSupabase({
        "portfolio_items": [
            {"ticker": "ORCL", "shares": None, "market_value": 5000},
            {"ticker": "JNJ", "shares": None, "market_value": 5000},
        ],
        "watchlist_items": [
            {"ticker": "ORCL", "user_id": "u1", "sector": "Technology",
             "market_cap": 4e11, "country": "US", "asset_type": "Stock"},
            {"ticker": "JNJ", "user_id": "u1", "sector": "Healthcare",
             "market_cap": 4e11, "country": "US", "asset_type": "Stock"},
        ],
    })
    _install_fakes(monkeypatch, supabase, _FakeFMP())
    res = await svc.PortfolioInsightsService().compute_insights_for_portfolio("u1", "p1")
    assert res is not None
    assert res.holdings_count == 2
    assert sum(s.points for s in res.sub_scores) == res.score
    assert {a.name for a in res.sector_allocations} == {"Technology", "Healthcare"}
    assert not math.isnan(res.effective_holdings)


@pytest.mark.asyncio
async def test_compute_insights_for_portfolio_none_below_min(monkeypatch):
    supabase = _FakeSupabase({
        "portfolio_items": [{"ticker": "ORCL", "shares": None, "market_value": 5000}],
        "watchlist_items": [
            {"ticker": "ORCL", "user_id": "u1", "sector": "Technology",
             "market_cap": 4e11, "country": "US", "asset_type": "Stock"},
        ],
    })
    _install_fakes(monkeypatch, supabase, _FakeFMP())
    res = await svc.PortfolioInsightsService().compute_insights_for_portfolio("u1", "p1")
    assert res is None
