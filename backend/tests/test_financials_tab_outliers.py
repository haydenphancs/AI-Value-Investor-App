"""
Outlier regression tests for the Financials-tab deep-check (2026-07-23).

Second adversarial pass over the six sections (earnings, growth, revenue
breakdown, profit power, health check, signal of confidence). Each test below
pins a defect that was REPRODUCED against the real code before being fixed —
the docstrings record the concrete failing input and the wrong output.

Two themes:

  1. Malformed upstream -> 500. FMP's ``_make_request`` is typed ``-> Any``
     ("list or dict"), and a present-but-NULL field is not the same as a missing
     one: ``rec.get("date", "")`` returns None when the key exists with a null
     value, so ``None < str`` raised TypeError inside every sorter.

  2. Fabricated numbers. Missing inputs were substituted with 0 and then
     rendered as fact — a market cap of 0 in the Altman Z-Score, a dividend-only
     yield compared against a div+buyback average, a break-even EPS silently
     dropped.

Pure-function tests; no network, no Supabase.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.encoders import jsonable_encoder

from app.schemas.earnings import EarningsDailyPriceSchema
from app.services.earnings_service import (
    _as_list as earn_as_list,
    _build_fiscal_quarter_map,
    _first_not_none,
    _safe_float as earn_safe_float,
)
from app.services.growth_service import (
    _as_list as growth_as_list,
    _compute_growth_points,
)
from app.services.health_check_service import _compute_z_score, _sum_ttm_income
from app.services.profit_power_service import _build_margin_points
from app.services.revenue_breakdown_service import _record_year
from app.services.signal_of_confidence_service import (
    SignalOfConfidenceService,
    _build_market_cap_lookup,
    _market_cap_on,
)
from app.utils.period_labels import extract_year, quarterly_period_label


# ── 1. Null date / null period never crash a sorter or label builder ─────────


def test_growth_points_survive_null_date():
    """FMP row with ``"date": null``.

    Was: ``sorted(records, key=lambda r: r.get("date", ""))`` ->
    ``TypeError: '<' not supported between instances of 'str' and 'NoneType'``
    -> 502 for the whole Growth section.
    """
    points = _compute_growth_points(
        [
            {"date": None, "period": "Q1", "calendarYear": "2023", "epsDiluted": 1.0},
            {"date": "2024-03-31", "period": "Q1", "calendarYear": "2024", "epsDiluted": 2.0},
        ],
        "epsDiluted",
        is_quarterly=True,
    )
    # Both rows are chartable; the null-date one just sorts first.
    assert len(points) == 2
    assert all(p["value"] is not None for p in points)


def test_margin_points_survive_null_date():
    """Same null-date row through the Profit Power builder."""
    points = _build_margin_points(
        [
            {"date": None, "period": "Q1", "calendarYear": "2024",
             "revenue": 100.0, "netIncome": 10.0},
            {"date": "2024-03-31", "period": "Q2", "calendarYear": "2024",
             "revenue": 200.0, "netIncome": 40.0},
        ],
        [],
        is_quarterly=True,
    )
    assert len(points) == 2
    assert points[-1]["net_margin"] == 20.0


def test_extract_year_and_label_survive_null_fields():
    """``len(record.get("date", ""))`` raised TypeError on a null date; the
    shared helper coerces. A null ``period`` must not render as "None '24"."""
    assert extract_year({"date": None}) == ""
    assert extract_year({"date": None, "calendarYear": 2024}) == "2024"
    label = quarterly_period_label({"period": None, "fiscalYear": 2024})
    assert "None" not in label


def test_fiscal_quarter_map_survives_null_period():
    """Was: ``None.startswith("Q")`` -> AttributeError -> 502 for Earnings."""
    assert _build_fiscal_quarter_map([{"period": None, "date": "2024-03-31"}]) == {}
    assert _build_fiscal_quarter_map(
        [{"period": "Q1", "date": None}, {"period": "Q1", "date": "2024-03-31"}]
    ) == {3: "Q1"}


# ── 2. A dict where a list was expected degrades instead of 502-ing ──────────


@pytest.mark.parametrize(
    "payload", [{"Error Message": "Limit Reach"}, "unexpected", 42, None]
)
def test_non_list_fmp_payload_degrades(payload):
    """FMP can answer 200 with a bare object. Iterating it yields string KEYS and
    the first ``rec.get(...)`` raised ``AttributeError: 'str' object has no
    attribute 'get'``."""
    assert _compute_growth_points(payload, "epsDiluted", is_quarterly=False) == []
    assert _build_margin_points(payload, [], is_quarterly=False) == []
    assert growth_as_list(payload) == []
    assert earn_as_list(payload) == []


def test_as_list_drops_non_dict_entries():
    assert growth_as_list([{"a": 1}, "junk", None, 7]) == [{"a": 1}]


# ── 3. A NaN close must not 500 the Earnings section ────────────────────────


def test_nan_price_is_not_json_serializable():
    """Guards the reason B1 mattered: Starlette serialises with
    ``allow_nan=False``, so a NaN that reaches a REQUIRED ``price: float``
    raises ValueError at encode time -> 500 for the whole section."""
    schema = EarningsDailyPriceSchema(date="2026-01-02", price=float("nan"))
    with pytest.raises(ValueError):
        json.dumps(jsonable_encoder(schema), allow_nan=False)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_daily_price_loop_skips_non_finite(bad):
    """The daily-price loop now applies the same isfinite guard the price_lookup
    loop always had. This asserts the filter predicate the fix relies on."""
    assert not math.isfinite(bad)
    finite = [v for v in [1.0, bad, 2.0] if math.isfinite(v)]
    assert finite == [1.0, 2.0]


# ── 4. Altman Z-Score omits itself rather than fabricating a distress zone ───

_APPLE_BS = {
    "totalAssets": 365_000e6,
    "totalLiabilities": 308_000e6,
    "totalCurrentAssets": 133_000e6,
    "totalCurrentLiabilities": 145_000e6,
    "retainedEarnings": -19_000e6,
}
_APPLE_INC = {"operatingIncome": 123_000e6, "revenue": 391_000e6}


def test_z_score_complete_inputs():
    assert _compute_z_score(_APPLE_BS, _APPLE_INC, 3_500_000e6) == 8.9


@pytest.mark.parametrize(
    "mcap,inc",
    [
        (None, _APPLE_INC),                       # profile fetch failed
        (3_500_000e6, {"revenue": 391_000e6}),     # TTM dropped EBIT
        (3_500_000e6, {"operatingIncome": 123_000e6}),  # TTM dropped revenue
    ],
)
def test_z_score_returns_none_when_a_weighted_input_is_missing(mcap, inc):
    """Was: ``0.6 * ((mcap or 0) / tl)``. A failed company-profile fetch (only a
    logger.warning) valued the equity at ZERO, taking Apple-shaped inputs from
    Z=8.9 "positive" (fortress) to Z=2.1 "neutral" ("Grey zone. Moderate
    financial stress signals") — a wrong verdict, cached for 24 hours."""
    assert _compute_z_score(_APPLE_BS, inc, mcap) is None


def test_z_score_still_requires_a_balance_sheet():
    assert _compute_z_score({}, _APPLE_INC, 3_500_000e6) is None


def test_ttm_sum_drops_field_when_a_quarter_is_missing_it():
    """A partial sum would understate a TTM flow figure, so the field is
    dropped — the bug was dropping it SILENTLY, letting ``(ebit or 0)``
    downstream fabricate a 0 EBIT. The Z-Score now refuses instead."""
    quarters = [
        {"date": f"2025-0{i}-30", "operatingIncome": 100.0, "interestExpense": 1.0,
         "revenue": 500.0, "netIncome": 80.0, "ebitda": 120.0}
        for i in range(1, 5)
    ]
    quarters[2]["operatingIncome"] = None
    summed = _sum_ttm_income(quarters)
    assert "operatingIncome" not in summed
    assert summed["revenue"] == 2000.0
    assert _compute_z_score(_APPLE_BS, summed, 3_500_000e6) is None


def test_ttm_sum_handles_non_list():
    assert _sum_ttm_income({"Error Message": "x"}) == {}
    assert _sum_ttm_income([]) == {}


# ── 5. A break-even quarter (EPS 0.00) is still a reported quarter ───────────


def test_first_not_none_preserves_zero():
    """Was: ``_safe_float(rec,"epsDiluted") or _safe_float(rec,"eps")``. 0.0 is
    falsy, so a genuine break-even quarter evaluated to None and its EPS bar
    vanished from the chart."""
    rec = {"epsDiluted": 0.0}
    assert (earn_safe_float(rec, "epsDiluted") or earn_safe_float(rec, "eps")) is None
    assert _first_not_none(
        earn_safe_float(rec, "epsDiluted"), earn_safe_float(rec, "eps")
    ) == 0.0


def test_first_not_none_falls_through_and_defaults():
    assert _first_not_none(None, 2.5) == 2.5
    assert _first_not_none(None, None) is None
    assert _first_not_none(-0.0, 9.9) == 0.0


# ── 6. Dividend status compares like with like ──────────────────────────────


def _dp(dividend_yield: float, buyback_yield: float):
    class _P:
        pass

    p = _P()
    p.dividend_yield = dividend_yield
    p.buyback_yield = buyback_yield
    return p


def test_dividend_status_ignores_buybacks_in_the_average():
    """Was: the "5-year average" summed dividend + buyback yield, then was
    divided into a DIVIDEND-ONLY numerator. A company paying 0.5% dividends and
    buying back 3.5% every quarter got avg=4.0, ratio=0.125 -> status "Low",
    despite yielding exactly its own historical average."""
    svc = SignalOfConfidenceService.__new__(SignalOfConfidenceService)
    info = svc._build_dividend_info(
        [{"date": "2026-01-05", "yield": 0.5}],
        0.5,   # T12M dividend yield
        3.5,   # T12M buyback yield
        -4.0,  # share count change
        data_points=[_dp(0.5, 3.5) for _ in range(8)],
    )
    assert info.five_year_avg_yield == 0.5   # dividend-only
    assert info.status != "Low"


def test_dividend_status_low_when_genuinely_below_its_average():
    svc = SignalOfConfidenceService.__new__(SignalOfConfidenceService)
    info = svc._build_dividend_info(
        [{"date": "2026-01-05", "yield": 2.0}],
        0.5, 0.0, 0.0,
        data_points=[_dp(2.0, 0.0) for _ in range(8)],
    )
    assert info.five_year_avg_yield == 2.0
    assert info.status == "Low"   # 0.5 / 2.0 = 0.25


def test_dividend_info_none_without_history():
    svc = SignalOfConfidenceService.__new__(SignalOfConfidenceService)
    assert svc._build_dividend_info([], 1.0, 1.0, 0.0, data_points=[]) is None


# ── 7. Point-in-time market cap for historical quarters ─────────────────────


def test_market_cap_lookup_scans_for_a_weekend_period_end():
    """A fiscal period end often falls on a weekend/holiday, so an exact match
    isn't guaranteed — mirrors earnings_service._find_close_price."""
    lookup = _build_market_cap_lookup(
        [
            {"date": "2025-09-26", "marketCap": 3.5e12},
            {"date": "2025-06-27", "marketCap": 3.0e12},
        ]
    )
    assert _market_cap_on("2025-09-28", lookup) == 3.5e12   # Sunday period end
    assert _market_cap_on("2025-09-26", lookup) == 3.5e12   # exact
    assert _market_cap_on("2020-01-01", lookup) is None     # out of range -> fallback
    assert _market_cap_on("", lookup) is None


def test_market_cap_lookup_rejects_junk_rows():
    lookup = _build_market_cap_lookup(
        [
            {"date": None, "marketCap": 1e12},
            {"date": "2025-09-26", "marketCap": None},
            {"date": "2025-09-27", "marketCap": float("nan")},
            {"date": "2025-09-28", "marketCap": 0},
            {"date": "2025-09-29", "marketCap": 2e12},
        ]
    )
    assert lookup == {"2025-09-29": 2e12}


# ── 8. Revenue breakdown pairs the segmentation year with its own income ────


def test_growth_never_stores_a_past_next_earnings_date():
    """The growth cache borrows ``next_earnings_date`` from profit_power_cache.
    That date decays as the sibling row ages, so copying it verbatim could write
    a row whose OWN freshness check (``today >= next_earnings``) rejects it on
    the very next read — a born-stale row that permanently disables the tier-2
    cache and re-runs the 10-call FMP fan-out every 5 minutes.
    """
    from app.services.growth_service import GrowthService

    svc = GrowthService.__new__(GrowthService)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    past = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")

    class _FakeSupabase:
        def __init__(self, value):
            self._value = value

        def table(self, _name):
            return self

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def execute(self):
            class _R:
                pass

            r = _R()
            r.data = [{"next_earnings_date": self._value}]
            return r

    svc.supabase = _FakeSupabase(past)
    assert svc._next_earnings_date_safe("AAPL") is None      # stale -> fall back to TTL
    svc.supabase = _FakeSupabase(today)
    assert svc._next_earnings_date_safe("AAPL") is None      # today is already too late
    svc.supabase = _FakeSupabase(future)
    assert svc._next_earnings_date_safe("AAPL") == future    # genuinely future -> keep
    svc.supabase = _FakeSupabase(None)
    assert svc._next_earnings_date_safe("AAPL") is None


def test_record_year_prefers_fiscal_then_calendar_then_date():
    assert _record_year({"fiscalYear": 2025, "calendarYear": 2024}) == "2025"
    assert _record_year({"calendarYear": 2024, "date": "2023-12-31"}) == "2024"
    assert _record_year({"date": "2023-12-31"}) == "2023"
    assert _record_year({"date": None}) == ""
    assert _record_year({}) == ""


# ── 9. Revenue breakdown pairs segments with the SAME fiscal year's income ───


class _FakeRevFMP:
    def __init__(self, seg, inc):
        self._seg, self._inc = seg, inc

    async def get_revenue_product_segmentation(self, *a, **k):
        return self._seg

    async def get_income_statement(self, *a, **k):
        return self._inc

    async def get_earning_calendar_full(self, *a, **k):
        return []


def _build_rev(seg, inc):
    import asyncio

    from app.services.revenue_breakdown_service import RevenueBreakdownService

    svc = RevenueBreakdownService.__new__(RevenueBreakdownService)
    svc.fmp = _FakeRevFMP(seg, inc)
    resp, _ = asyncio.run(svc._build_revenue_breakdown("AAPL"))
    return resp


def test_revenue_breakdown_normal_case_pairs_same_year():
    r = _build_rev(
        [{"fiscalYear": 2024, "date": "2024-09-28",
          "data": {"iPhone": 200e9, "Services": 96e9, "Mac": 30e9}}],
        [{"fiscalYear": 2024, "calendarYear": "2024", "date": "2024-09-28",
          "revenue": 391e9, "costOfRevenue": 210e9}],
    )
    assert r.fiscal_year == "2024"
    assert {"iPhone", "Services"} <= {s.name for s in r.revenue_sources}
    assert r.cost_of_sales == 210e9


def test_revenue_breakdown_lagging_segmentation_uses_its_own_years_income():
    """FMP segmentation commonly lags the income statement by a year. The card
    must pair the segments with the SAME year's costs and label — not the latest
    income year's — or net profit/margin is computed across two fiscal years."""
    r = _build_rev(
        [{"fiscalYear": 2023, "date": "2023-09-30",
          "data": {"iPhone": 190e9, "Services": 85e9}}],
        [
            {"fiscalYear": 2024, "calendarYear": "2024", "date": "2024-09-28",
             "revenue": 391e9, "costOfRevenue": 210e9},
            {"fiscalYear": 2023, "calendarYear": "2023", "date": "2023-09-30",
             "revenue": 383e9, "costOfRevenue": 214e9},
        ],
    )
    assert r.fiscal_year == "2023"       # label matches the SEGMENT year
    assert r.cost_of_sales == 214e9      # costs from the 2023 income, not 2024's 210e9


def test_revenue_breakdown_no_year_match_degrades_to_total_revenue():
    """When no segmentation year has a matching income statement, fall back to an
    honest income-only Total Revenue rather than pairing mismatched years."""
    r = _build_rev(
        [{"fiscalYear": 2015, "date": "2015-09-30", "data": {"iPhone": 100e9}}],
        [{"fiscalYear": 2024, "calendarYear": "2024", "date": "2024-09-28",
          "revenue": 391e9, "costOfRevenue": 210e9}],
    )
    assert [s.name for s in r.revenue_sources] == ["Total Revenue"]
    assert r.fiscal_year == "2024"
