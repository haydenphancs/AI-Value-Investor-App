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
    _zone,
    effective_holdings,
    hhi,
    normalized_hhi_score,
    score_holdings,
)
from _price_fakes import PriceFromFMPFake


def _h(
    ticker: str,
    value: float,
    sector: str | None = "Technology",
    country: str = "US",
    market_cap: float | None = 300_000_000_000.0,
    asset_type: str = "Stock",
) -> PortfolioHoldingResponse:
    return PortfolioHoldingResponse(
        id=ticker,
        ticker=ticker,
        company_name=ticker,
        market_value=value,
        shares=None,
        sector=sector,
        asset_type=asset_type,
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
    assert {s.key for s in res.sub_scores} == {"position", "sector", "marketcap"}
    assert sum(s.max_points for s in res.sub_scores) == 100


def test_max_points_sum_to_100_without_caps():
    res = score_holdings([_h("A", 5000, "Technology", "US", None),
                          _h("B", 5000, "Healthcare", "US", None)])
    assert res is not None
    assert {s.key for s in res.sub_scores} == {"position", "sector"}
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

    async def get_batch_quotes_bulk(self, symbols):
        """Mirrors the real client: ONE call, one row per resolvable symbol.

        `_fetch_prices` moved off a per-ticker fan-out (an unbounded `asyncio.gather`
        with no semaphore) onto `/stable/batch-quote`. A symbol with no price is simply
        ABSENT from the response rather than returning an empty dict — the caller then
        falls back to the stored `market_value`, which is what keeps ZZZ excluded here.
        """
        out = []
        for sym in symbols:
            price = self._prices.get(str(sym).upper())
            if price is not None:
                out.append({"symbol": str(sym).upper(), "price": price})
        return out


def _install_fakes(monkeypatch, supabase, fmp):
    monkeypatch.setattr(svc, "get_supabase", lambda: supabase)
    monkeypatch.setattr(svc, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(svc, "price_source",
                        lambda owner=None: PriceFromFMPFake(fmp))


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


# ═══════════ 4. Zero-value holdings + market-cap gate (regressions) ═══════


def test_zero_value_holding_excluded_from_scoring():
    """A phantom market_value==0 holding (failed price refresh / shares==0) must
    not inflate n or deflate the normalized-HHI denominators."""
    with_phantom = score_holdings([
        _h("A", 0, "Technology", "US", None),
        _h("B", 5000, "Healthcare", "US", None),
        _h("C", 5000, "Energy", "US", None),
    ])
    without = score_holdings([
        _h("B", 5000, "Healthcare", "US", None),
        _h("C", 5000, "Energy", "US", None),
    ])
    assert with_phantom is not None and without is not None
    assert with_phantom.score == without.score
    assert with_phantom.holdings_count == 2            # phantom not counted
    assert with_phantom.effective_holdings == without.effective_holdings


def test_single_funded_holding_returns_none():
    # One real position + one zero-value phantom → below MIN once filtered.
    assert score_holdings([_h("A", 5000), _h("B", 0)]) is None


def test_single_cap_bucket_folds_to_position_sector():
    """An all-mega, multi-sector, equal-weight book is a single cap bucket, which
    is NOT a measurable size-mix signal — its budget folds into position/sector
    rather than scoring 0 and capping the total at 80."""
    res = score_holdings([
        _h("AAPL", 5000, "Technology", "US", 3e12),          # Mega
        _h("JPM", 5000, "Financial Services", "US", 5e11),   # Mega
        _h("PFE", 5000, "Healthcare", "US", 2e11),           # Mega
    ])
    assert res is not None
    assert {s.key for s in res.sub_scores} == {"position", "sector"}
    assert sum(s.max_points for s in res.sub_scores) == 100
    assert res.score == 100                                # not capped at 80


def test_market_cap_gate_is_monotonic():
    """Learning one holding's cap must never LOWER the score (adding
    information can't make a portfolio look less diversified)."""
    no_caps = score_holdings([
        _h("A", 5000, "Technology", "US", None),
        _h("B", 5000, "Healthcare", "US", None),
        _h("C", 5000, "Energy", "US", None),
    ])
    one_cap = score_holdings([
        _h("A", 5000, "Technology", "US", 3e12),
        _h("B", 5000, "Healthcare", "US", None),
        _h("C", 5000, "Energy", "US", None),
    ])
    assert no_caps is not None and one_cap is not None
    assert one_cap.score >= no_caps.score


def test_market_cap_scored_when_two_buckets_and_half_priced():
    """The size dimension still appears when it IS a real signal: ≥2 buckets and
    ≥half the book priced."""
    res = score_holdings([
        _h("MEGA", 5000, "Technology", "US", 3e12),   # Mega
        _h("SMALL", 5000, "Healthcare", "US", 1e9),   # Small
    ])
    assert res is not None
    assert {s.key for s in res.sub_scores} == {"position", "sector", "marketcap"}
    assert sum(s.max_points for s in res.sub_scores) == 100


# ═══════════ 5. PLACEHOLDER SECTORS + THE CRYPTO BUCKET (TestFlight 1.0 (8)) ═══════════
#
# The card showed "Technology 100% / N/A 0% / Industrials 0% / Other 0%" to a user holding
# crypto: the string "N/A" (persisted by the feed backfill from a formatted profile) passed
# the old `if h.sector` test and became a bucket of its own, and a coin — sector NULL,
# market cap NULL after migration 160 — fell into "Other" and "Unknown". Both fixed in
# `_sector_bucket` / `_size_bucket`; the iOS offline mirror (`DiversificationCalculator`)
# applies the same rules and is pinned by `test_ios_diversification_guards.py`.

from app.services._classification_common import PLACEHOLDER_TEXT, is_placeholder_text  # noqa: E402
from app.services.portfolio_insights_service import (  # noqa: E402
    CRYPTO_BUCKET, OTHER_SECTOR, UNKNOWN_CAP, _is_crypto, _sector_bucket, _size_bucket,
)


def _names(allocs):
    return [a.name for a in allocs]


@pytest.mark.parametrize("placeholder", ["N/A", "n/a", " N/A ", "-", "None", "", "null", "nan", "NA", "\u2014"])
def test_a_placeholder_sector_folds_into_other_not_its_own_bucket(placeholder):
    """The over-count the skeptic found: a placeholder row AND a NULL row used to be TWO
    buckets ("N/A" + "Other"), so `sector_count` and the sector HHI's n were one too many."""
    res = score_holdings([_h("A", 5000, sector=placeholder), _h("B", 5000, sector=None)])
    assert _names(res.sector_allocations) == [OTHER_SECTOR]
    assert res.sector_allocations[0].percentage == pytest.approx(100.0)
    assert res.sector_count == 1
    assert not any(is_placeholder_text(a.name) for a in res.sector_allocations)


def test_a_placeholder_sector_still_counts_as_a_holding():
    """Folding is a relabel, never a drop: the money is still in the book."""
    res = score_holdings([_h("A", 6000, sector="N/A"), _h("B", 4000, sector="Technology")])
    assert res.holdings_count == 2
    assert dict((a.name, a.percentage) for a in res.sector_allocations) == {OTHER_SECTOR: 60.0, "Technology": 40.0}


def test_crypto_holding_gets_the_crypto_bucket_in_both_donuts():
    res = score_holdings([
        _h("NVDA", 6000, "Technology", market_cap=4e12),
        _h("DOGEUSD", 4000, sector=None, market_cap=None, asset_type="crypto"),
    ])
    assert set(_names(res.sector_allocations)) == {"Technology", CRYPTO_BUCKET}
    assert set(_names(res.marketcap_allocations)) == {"Mega Cap", CRYPTO_BUCKET}
    assert OTHER_SECTOR not in _names(res.sector_allocations)
    assert UNKNOWN_CAP not in _names(res.marketcap_allocations)
    sector = next(s for s in res.sub_scores if s.key == "sector")
    assert sector.points > 0, "a coin beside one sector is a second bucket — Sector Spread must move"
    # One equity cap bucket → the market-cap gate is closed → 50/50 budgets.
    assert [s.max_points for s in res.sub_scores] == [50, 50]


def test_a_pair_form_ticker_without_a_stored_class_is_crypto():
    """A pre-eaccf8e6 row: ('ETHUSD', 'Stock'). The USD-suffix rule still says coin."""
    res = score_holdings([_h("ETHUSD", 5000, sector=None, market_cap=None, asset_type="Stock"),
                          _h("AAPL", 5000)])
    assert CRYPTO_BUCKET in _names(res.sector_allocations)


def test_an_fx_pair_is_not_a_coin():
    """`resolve_asset_class` calls any long *USD symbol crypto; the same FX exclusion
    `uses_coingecko_price` carries keeps EURUSD out of the Crypto slice."""
    res = score_holdings([_h("EURUSD", 5000, sector=None, market_cap=None, asset_type="Stock"),
                          _h("AAPL", 5000)])
    assert CRYPTO_BUCKET not in _names(res.sector_allocations)
    assert _names(res.sector_allocations) == ["Technology", OTHER_SECTOR] or set(_names(res.sector_allocations)) == {"Technology", OTHER_SECTOR}
    assert UNKNOWN_CAP in _names(res.marketcap_allocations)


def test_a_bare_coin_ticker_stays_an_equity():
    """BTC with a stored 'Stock' is the Grayscale Bitcoin Mini Trust ETF (migration 160:
    bare = the listed security) — never the coin's bucket."""
    res = score_holdings([_h("BTC", 5000, "Financial Services", market_cap=5e9, asset_type="Stock"),
                          _h("AAPL", 5000)])
    assert CRYPTO_BUCKET not in _names(res.sector_allocations)
    assert "Financial Services" in _names(res.sector_allocations)
    assert "Mid Cap" in _names(res.marketcap_allocations)


def test_crypto_never_enters_market_cap_scoring():
    """Even with a cap stored, a coin is not part of the equity size mix: the Market-Cap
    Mix points are identical whether the coin's cap is set or None."""
    equities = [_h("NVDA", 4000, market_cap=4e12), _h("CAT", 3000, "Industrials", market_cap=1.5e11)]
    with_cap = score_holdings(equities + [_h("BTCUSD", 3000, sector=None, market_cap=1.5e12, asset_type="crypto")])
    no_cap = score_holdings(equities + [_h("BTCUSD", 3000, sector=None, market_cap=None, asset_type="crypto")])
    cap_points = lambda r: next(s.points for s in r.sub_scores if s.key == "marketcap")
    assert cap_points(with_cap) == cap_points(no_cap)
    assert "Mega Cap" in _names(with_cap.marketcap_allocations)
    assert CRYPTO_BUCKET in _names(with_cap.marketcap_allocations)


def test_a_crypto_majority_book_folds_the_cap_budget():
    """Coins carry no equity cap, so with 60 % crypto the priced equity weight is under
    half the book → the gate closes and the 20 pts fold into position/sector."""
    res = score_holdings([
        _h("BTCUSD", 6000, sector=None, market_cap=None, asset_type="crypto"),
        _h("NVDA", 2500, market_cap=4e12),
        _h("CAT", 1500, "Industrials", market_cap=1.5e11),
    ])
    assert [s.max_points for s in res.sub_scores] == [50, 50]


@pytest.mark.parametrize("ticker, asset_type, expected", [
    ("DOGEUSD", "crypto", True),
    ("DOGEUSD", "Stock", True),     # suffix rule
    ("DOGE", "crypto", True),       # trusted stored class
    ("DOGE", "Stock", False),       # bare + declared security
    ("BTC", None, False),           # bare = the listed security
    ("EURUSD", None, False),        # FX
    ("AAPL", None, False),
])
def test_is_crypto_table(ticker, asset_type, expected):
    assert _is_crypto(_h(ticker, 1.0, asset_type=asset_type or "Stock")) is expected


def test_the_unknown_cap_label_is_not_a_placeholder():
    """`UNKNOWN_CAP` is a label the service EMITS; `PLACEHOLDER_TEXT` is what it reads.
    If 'unknown' ever joins the sentinel set, the parity test forbidding placeholder
    labels would go red on every capless equity."""
    assert UNKNOWN_CAP.lower() not in PLACEHOLDER_TEXT
    assert _size_bucket(_h("AAPL", 1.0, market_cap=None)) == UNKNOWN_CAP
    assert _sector_bucket(_h("AAPL", 1.0, sector="  Technology ")) == "Technology"


@pytest.mark.asyncio
async def test_get_portfolio_holdings_prices_a_pair_form_coin_and_never_asks_fmp_for_its_profile(monkeypatch):
    supabase = _FakeSupabase({
        "portfolio_items": [
            {"ticker": "DOGEUSD", "shares": 100, "market_value": None},
            {"ticker": "NVDA", "shares": None, "market_value": 8000},
        ],
        "watchlist_items": [
            {"ticker": "DOGEUSD", "user_id": "u1", "company_name": "Dogecoin",
             "sector": None, "market_cap": None, "country": "US", "asset_type": "crypto"},
            {"ticker": "NVDA", "user_id": "u1", "company_name": "NVIDIA",
             "sector": "Technology", "market_cap": 4e12, "country": "US", "asset_type": "stock"},
        ],
    })
    asked = []

    class _FMP(_FakeFMP):
        async def get_company_profiles_batch(self, tickers):
            asked.extend(tickers)
            return await super().get_company_profiles_batch(tickers)

    fmp = _FMP(profiles=[], prices={"DOGEUSD": 0.2})
    _install_fakes(monkeypatch, supabase, fmp)

    holdings = await svc.PortfolioInsightsService().get_portfolio_holdings("u1", "p1")
    doge = next(h for h in holdings if h.ticker == "DOGEUSD")
    assert doge.market_value == pytest.approx(20.0)      # 100 × 0.2 — priced, not stored 0
    assert doge.asset_type == "crypto"
    assert "DOGEUSD" not in asked, "a coin has no FMP profile — the call is outside the Order Form"

    res = score_holdings(holdings)
    assert CRYPTO_BUCKET in _names(res.sector_allocations)
    assert CRYPTO_BUCKET in _names(res.marketcap_allocations)
    assert res.holdings_count == 2


@pytest.mark.asyncio
async def test_enrich_missing_never_writes_placeholder_strings(monkeypatch):
    """FMP has served "N/A" as a string; the write-back used to store it raw, and because
    both healers test falsiness the row could never be re-healed."""
    supabase = _FakeSupabase({
        "portfolio_items": [{"ticker": "SPY", "shares": None, "market_value": 5000},
                            {"ticker": "NVDA", "shares": None, "market_value": 5000}],
        "watchlist_items": [
            {"ticker": "SPY", "user_id": "u1", "company_name": "SPDR", "sector": None,
             "industry": None, "market_cap": None, "country": None, "asset_type": "etf"},
            {"ticker": "NVDA", "user_id": "u1", "company_name": "NVIDIA", "sector": "Technology",
             "market_cap": 4e12, "country": "US", "asset_type": "stock"},
        ],
    })
    fmp = _FakeFMP(profiles=[{"symbol": "SPY", "sector": "N/A", "industry": "N/A",
                              "country": "N/A", "marketCap": 5e11}])
    _install_fakes(monkeypatch, supabase, fmp)

    holdings = await svc.PortfolioInsightsService().get_portfolio_holdings("u1", "p1")
    spy = next(h for h in holdings if h.ticker == "SPY")
    assert spy.sector is None, "the placeholder must not be adopted in memory either"
    assert spy.market_cap == pytest.approx(5e11)
    writes = [vals for name, vals in supabase.updates if name == "watchlist_items"]
    assert writes == [{"market_cap": 5e11}], writes

    res = score_holdings(holdings)
    assert not any(is_placeholder_text(a.name) for a in res.sector_allocations)
    assert OTHER_SECTOR in _names(res.sector_allocations)


@pytest.mark.asyncio
async def test_enrich_missing_keeps_a_namibian_country_code(monkeypatch):
    supabase = _FakeSupabase({
        "portfolio_items": [{"ticker": "PDL", "shares": None, "market_value": 5000},
                            {"ticker": "NVDA", "shares": None, "market_value": 5000}],
        "watchlist_items": [
            {"ticker": "PDL", "user_id": "u1", "company_name": "Paladin", "sector": None,
             "industry": None, "market_cap": None, "country": None, "asset_type": "stock"},
            {"ticker": "NVDA", "user_id": "u1", "company_name": "NVIDIA", "sector": "Technology",
             "market_cap": 4e12, "country": "US", "asset_type": "stock"},
        ],
    })
    fmp = _FakeFMP(profiles=[{"symbol": "PDL", "sector": "Energy", "industry": "Uranium",
                              "country": "NA", "marketCap": 2e9}])
    _install_fakes(monkeypatch, supabase, fmp)
    holdings = await svc.PortfolioInsightsService().get_portfolio_holdings("u1", "p1")
    pdl = next(h for h in holdings if h.ticker == "PDL")
    assert pdl.sector == "Energy" and pdl.country == "NA"
    writes = [vals for name, vals in supabase.updates if name == "watchlist_items"]
    assert writes == [{"sector": "Energy", "industry": "Uranium", "country": "NA", "market_cap": 2e9}], writes

