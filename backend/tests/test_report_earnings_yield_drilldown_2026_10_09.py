"""The paid report's Earnings Yield drill-down follows the Price card (2026-10-09).

Since Price-card payload v8 (owner decision 2026-10-09) the card's yield is 1 / its displayed P/E,
compared with 1 / the P/E median. The report's tap-to-expand history used two other sources:
the company line preferred FMP's own `earningsYield` (net income ÷ market cap where present), and
the peer line read the STORED earnings_yield medians (also net income ÷ market cap) — a few
percent off median 1/P/E where preferred dividends or a moving share count split them (C: 8.09%
beside P/E 13.75, whose inverse is 7.27%).

Pins: the company point is 100 / the period's P/E AS THE P/E SERIES SHOWS IT (2 decimals), None
(never 0) for a missing / zero / negative / non-finite / absurd P/E, annual and quarterly; the peer
line is 100 / the P/E median of the same period and peer level (an unusable median gives no point;
no P/E line, no yield line — a withheld P/E line withholds the yield's); the stored earnings_yield
medians are never read nor fetched; every drawn yield point is the inverse of the P/E point of the
same period. Pure: no FMP, no Supabase.
"""

from __future__ import annotations

import math

import pytest

from app.services.agents import ticker_report_data_collector as C
from app.services.agents.narrative_prompts import _metric_trajectory_line
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    _build_fundamentals_history,
    _fundamentals_history_for_period,
    _history_earnings_yield,
)


def _series(hist, key):
    return {p["period"]: p["value"] for p in hist.get(key, [])}


def _ratios(pes, quarterly=False):
    rows = []
    for i, pe in enumerate(pes):
        year = 2024 - (i // 4 if quarterly else i)
        row = {"calendarYear": str(year), "date": f"{year}-09-30", "priceToEarningsRatio": pe}
        if quarterly:
            q = 4 - (i % 4)
            row.update(date=f"{year}-{3 * q:02d}-28", period=f"Q{q}")
        rows.append(row)
    return rows


# ── the company line ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pe,expected", [
    (13.75, 7.27), (29.726, 3.36), (25.0, 4.0), ("20", 5.0), (0.5, 200.0), (9999.99, 0.01),
])
def test_a_period_yield_is_100_over_its_displayed_pe(pe, expected):
    assert _history_earnings_yield(pe) == expected


@pytest.mark.parametrize("pe", [None, 0, 0.0, 0.001, -18.75, float("nan"), float("inf"),
                                float("-inf"), "abc", True, 1e12, 30000.0])
def test_no_usable_pe_means_no_point_never_a_zero(pe):
    assert _history_earnings_yield(pe) is None


def test_fmps_own_yield_field_is_never_read():
    rows = [{"calendarYear": "2024", "date": "2024-09-30", "priceToEarningsRatio": 13.75,
             "earningsYield": 0.0809},
            {"calendarYear": "2023", "date": "2023-09-30", "earningsYield": 0.05}]
    hist = _fundamentals_history_for_period([], [], [], [], rows, {}, quarterly=False)
    assert _series(hist, "earnings_yield") == {"2024": 7.27, "2023": None}


@pytest.mark.parametrize("quarterly", [False, True])
def test_every_company_yield_point_inverts_the_pe_point_beside_it(quarterly):
    pes = [13.75, 29.726, -4.0, None, 71.3, 154.34, 0.0, 22.22]
    hist = _fundamentals_history_for_period([], [], [], [], _ratios(pes, quarterly), {},
                                            quarterly=quarterly)
    pe_series, ey_series = _series(hist, "pe"), _series(hist, "earnings_yield")
    assert pe_series.keys() == ey_series.keys()
    for period, ey in ey_series.items():
        pe = pe_series[period]
        if pe is None:
            assert ey is None, period
        else:
            assert ey == round(100.0 / pe, 2), (period, pe, ey)


# ── the peer line ────────────────────────────────────────────────────────────────

def _out(annual_bench, quarterly_bench=None, levels=None, pes=(25.0, 20.0, 16.0)):
    out = CollectedTickerData(ticker="C", persona_key="warren_buffett")
    out.profile = {"industry": "Banks - Diversified"}
    out.income = [{"calendarYear": str(2024 - i), "date": f"{2024 - i}-09-30", "revenue": 1}
                  for i in range(len(pes))]
    out.ratios = _ratios(list(pes))
    out.sector_benchmark_history = {"annual": annual_bench, "quarterly": quarterly_bench or {},
                                    "levels": levels or {}}
    return out


def test_the_peer_line_is_100_over_the_pe_median_at_the_pe_lines_level():
    out = _out({"pe_ratio": {"2024": 14.43, "2023": 12.5, "2022": 10.0},
                "earnings_yield": {"2024": 0.0735, "2023": 0.08, "2022": 0.1}},
               levels={"annual": {"pe_ratio": "industry", "earnings_yield": "sector"}})
    ey = _build_fundamentals_history(out)["earnings_yield"]
    assert [p["value"] for p in ey["sector_annual"]] == [10.0, 8.0, 6.93]
    assert ey["sector_annual_level"] == "industry", "the P/E line's peer group, not the stored one"


def test_the_stored_yield_medians_alone_draw_nothing():
    out = _out({"earnings_yield": {"2024": 0.0735, "2023": 0.08, "2022": 0.1}},
               levels={"annual": {"earnings_yield": "industry"}})
    ey = _build_fundamentals_history(out)["earnings_yield"]
    assert "sector_annual" not in ey and "sector_annual_level" not in ey


@pytest.mark.parametrize("median", [0.0, -12.0, 0.001, float("nan"), float("inf"), None, "x"])
def test_an_unusable_pe_median_gives_no_yield_point(median):
    out = _out({"pe_ratio": {"2024": median, "2023": 20.0}})
    ey = _build_fundamentals_history(out)["earnings_yield"]
    assert _series({"s": ey["sector_annual"]}, "s") == {"2024": None, "2023": 5.0, "2022": None}


def test_the_quarterly_peer_line_is_derived_the_same_way():
    out = _out({}, quarterly_bench={"pe_ratio": {"Q3 '24": 40.0}},
               levels={"quarterly": {"pe_ratio": "sector"}})
    out.ratios_q = _ratios([30.0, 25.0], quarterly=True)
    out.income_q = [dict(r, revenue=1) for r in out.ratios_q]
    ey = _build_fundamentals_history(out)["earnings_yield"]
    values = [p["value"] for p in ey.get("sector_quarterly", [])]
    assert 2.5 in values and ey.get("sector_quarterly_level") == "sector"


def test_every_drawn_peer_yield_inverts_the_pe_peer_point_of_its_period():
    out = _out({"pe_ratio": {"2024": 14.43, "2023": 33.3, "2022": 7.77}},
               levels={"annual": {"pe_ratio": "industry"}})
    hist = _build_fundamentals_history(out)
    pe_peer = {p["period"]: p["value"] for p in hist["pe"]["sector_annual"]}
    for p in hist["earnings_yield"]["sector_annual"]:
        assert math.isclose(p["value"], 100.0 / pe_peer[p["period"]], abs_tol=0.006), p


def test_a_withheld_pe_line_withholds_the_yield_line():
    """A non-lender member's lines are dropped before the build (`_withhold_payment_network_lines`):
    with no P/E line there is nothing to invert."""
    out = _out({"gross_margin": {"2024": 0.4}}, levels={"annual": {"gross_margin": "industry"}})
    hist = _build_fundamentals_history(out)
    assert "sector_annual" not in hist["earnings_yield"]


def test_the_stored_yield_medians_are_no_longer_fetched():
    assert "earnings_yield" not in C._SECTOR_HISTORY_METRIC_NAMES
    assert "pe_ratio" in C._SECTOR_HISTORY_METRIC_NAMES
    assert C._RECIPROCAL_SECTOR_LINE_BY_HISTORY_KEY == {"earnings_yield": "pe_ratio"}
    assert "earnings_yield" not in C._SECTOR_METRIC_BY_HISTORY_KEY


# ── what the report's model reads ───────────────────────────────────────────────

def test_the_trajectory_line_states_the_derived_peer_yield():
    out = _out({"pe_ratio": {"2024": 14.43, "2023": 12.5, "2022": 10.0}},
               levels={"annual": {"pe_ratio": "industry"}})
    ey = _build_fundamentals_history(out)["earnings_yield"]
    metric = {"label": "Earnings Yield", "history_unit": "percent",
              "annual_history": ey["annual"], "sector_annual_history": ey["sector_annual"],
              "sector_annual_level": ey["sector_annual_level"]}
    line = _metric_trajectory_line(metric, "industry") or ""
    assert "6.9" in line and "10.0" not in line.split("industry")[0][-20:], line
