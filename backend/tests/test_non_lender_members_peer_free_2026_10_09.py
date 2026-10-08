"""NET-4 (owner decision 2026-10-09): a listed non-lender member of the mixed "Financial -
Credit Services" industry is never compared with that industry's median, on ANY metric.

Live TTM rows (2026-10-04) showed what the four-metric rule left behind: V's P/E 31.9 read
"2.72x industry avg 11.7" and scored 1; PYPL's P/FCF 7.1x scored 1/5; MA's +15.7% operating
-income growth scored 2 against the lenders' +24.6% ("Shrinking Operating Profit"). The
Credit Services median is a lenders' yardstick (32 of 37 members lend), so for V, MA, PYPL,
WU, GPN (and the fee businesses TREE, PMTS — NET-5):

  * `financials_metric_gate.peer_median_comparable` is False for EVERY metric, and
    `comparable_peer_metrics` filters each service's benchmark list to [];
  * every comparison surface — the Health Check, the profitability / valuation / growth /
    profit-power services, the report — makes NO lookup, is NOT degraded, scores on the
    existing absolute bands (or leaves a row unscored), draws no line, names no peer;
  * the Price card is rated only when >= 2 multiples were actually judged (network-scoped:
    every other company keeps today's rating);
  * the report's cards are all peer-free (`peer_compared=False`, `peer_group_level` None) and
    their {peer} phrases read "High P/E", "High Gross Margins", "High EV/EBITDA";
  * the producer computes the Credit Services median from lenders only.

A lender (AXP) is unchanged on every surface. Each test's docstring names the mutation it
catches. Hermetic: stubbed FMP answers, peer lookups and universe entries — no network.
"""

from __future__ import annotations

import ast
import inspect
import logging
import math
import textwrap
from typing import Any, Dict, List, Optional, Tuple

import pytest

import test_card_networks_vs_lenders_2026_10_08 as nv
import test_growth_deepcheck_backend as gd
import test_health_check_deepcheck as hcd
import test_report_round4_2026_10_08 as r4
import test_snapshot_cards_2026_10_07_profitability_health as sph
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services import financials_metric_gate as gate
from app.services import growth_service as gmod
from app.services import health_check_service as hc
from app.services import industry_benchmark_service as ibs
from app.services import profit_power_service as pp
from app.services import profitability_snapshot_service as ps
from app.services import valuation_snapshot_service as vss
from app.services.agents import card_verdict as cv
from app.services.agents import ticker_report_data_collector as C
from app.services.sector_benchmark_lookup import BenchmarkLookupFailed

CREDIT = "Financial - Credit Services"
FS = "Financial Services"
_CREDIT_SPELLINGS = (CREDIT, "Financial—Credit Services", "  financial - credit  services ",
                     "FINANCIAL – CREDIT SERVICES")
_GATED = {"current_ratio", "quick_ratio", "interest_coverage"}

# Every metric name the comparison surfaces ask the lookup for (the four services' lists,
# the Health Check's, the report's drill-down names) plus Altman Z.
_ALL_METRICS = (
    "gross_margin", "operating_margin", "net_margin", "fcf_margin", "roe", "roa",
    "pe_ratio", "pb_ratio", "ps_ratio", "pfcf_ratio", "ev_ebitda", "earnings_yield",
    "eps_yoy", "revenue_yoy", "net_income_yoy", "operating_income_yoy", "fcf_yoy",
    "debt_to_equity", "current_ratio", "quick_ratio", "interest_coverage", "altman_z_score",
)


class _Recorder:
    """A peer lookup that RECORDS every call and answers a mature industry cell for every
    metric asked (a lender-pooled Credit Services median), or raises when ``boom``."""

    def __init__(self, value: float = 0.5, *, boom: Optional[BaseException] = None):
        self.value, self.boom = value, boom
        self.current: List[List[str]] = []
        self.series: List[Tuple[str, List[str]]] = []

    def get_current_benchmarks(self, industry, sector, metrics):
        self.current.append(list(metrics))
        if self.boom is not None:
            raise self.boom
        return {m: {"value": self.value, "level": "industry", "peer_group_name": "CS",
                    "n": 60} for m in metrics}

    def get_benchmark_series(self, industry, sector, metrics, period_type):
        self.series.append((period_type, list(metrics)))
        if self.boom is not None:
            raise self.boom
        return {m: {} for m in metrics}

    def calls(self) -> int:
        return len(self.current) + len(self.series)


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. The gate
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("metric", _ALL_METRICS)
@pytest.mark.parametrize("industry", _CREDIT_SPELLINGS)
def test_a_listed_member_compares_nothing_with_the_mixed_median(metric, industry):
    """Mutation: the old four-metric rule (P/E, ROE, margins, growth still compared)."""
    assert gate.peer_median_comparable(metric, industry, network=True) is False


@pytest.mark.parametrize("metric", _ALL_METRICS)
@pytest.mark.parametrize("network", [False, 1, "yes", None, 0])
def test_a_lender_and_junk_verdicts_are_unchanged(metric, network):
    """A lender (and truthy junk: only `True` is a verdict) compares every non-gated metric
    and none of the gated three. Mutation: `bool(network)` instead of `network is True`."""
    assert gate.peer_median_comparable(metric, CREDIT, network=network) is (
        metric not in _GATED)
    # the keyword's default is the fail-closed lender
    assert gate.peer_median_comparable(metric, CREDIT) is (metric not in _GATED)


@pytest.mark.parametrize("metric", _ALL_METRICS)
@pytest.mark.parametrize("industry", ["Banks - Diversified", "Software - Infrastructure",
                                      "Insurance - Brokers", "", None, 7, ["x"]])
def test_the_rule_means_nothing_outside_the_mixed_industry(metric, industry):
    """V filed as a bank (or an unknown / junk industry): exactly the industry answer.
    Mutation: checking `network` before the industry."""
    expected = (gate.peer_metric_applicable(metric, industry) if metric in _GATED else True)
    assert gate.peer_median_comparable(metric, industry, network=True) is expected


def test_comparable_peer_metrics_preserves_order_and_empties_for_a_member():
    names = ["roe", "current_ratio", "gross_margin", "debt_to_equity", "pe_ratio"]
    assert gate.comparable_peer_metrics(names, CREDIT, network=False) == [
        "roe", "gross_margin", "debt_to_equity", "pe_ratio"]
    assert gate.comparable_peer_metrics(names, CREDIT, network=True) == []
    assert gate.comparable_peer_metrics(names, "Technology", network=True) == names
    assert gate.comparable_peer_metrics([], CREDIT, network=False) == []
    assert gate.comparable_peer_metrics(iter(names), "Banks - Regional", network=True) == [
        "roe", "gross_margin", "debt_to_equity", "pe_ratio"]
    with pytest.raises(TypeError):        # `network` is a required keyword
        gate.comparable_peer_metrics(names, CREDIT)       # type: ignore[call-arg]


def test_the_resolver_log_describes_the_every_metric_rule(caplog):
    """The `[network-gate]` line is the debugging surface: it must say EVERY metric is
    judged without the median. Mutation: the old "they and debt-to-equity" text."""
    with caplog.at_level(logging.INFO, logger=gate.logger.name):
        assert gate.resolve_payment_network("V", CREDIT, None, source="unit") is True
        assert gate.resolve_payment_network("AXP", CREDIT, None, source="unit") is False
    member, lender = [r.getMessage() for r in caplog.records if "[network-gate]" in r.getMessage()]
    assert "EVERY metric judged without the industry median" in member
    assert "debt-to-equity judged on absolute bands only" not in member
    assert "compared with the industry median, as a lender's" in lender


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. Profitability snapshot
# ══════════════════════════════════════════════════════════════════════════════════════

_V_PROF_RATIOS = {"grossProfitMarginTTM": 0.80, "operatingProfitMarginTTM": 0.67,
                  "netProfitMarginTTM": 0.55, "debtToEquityRatioTTM": 0.55,
                  "revenuePerShareTTM": 20.0}
_V_PROF_KM = {"returnOnEquityTTM": 0.50, "returnOnAssetsTTM": 0.20}


def _prof_svc(monkeypatch, symbol: str, lookup: _Recorder, industry: str = CREDIT):
    svc = sph._prof(monkeypatch, ratios=[dict(_V_PROF_RATIOS)], km=[dict(_V_PROF_KM)],
                    bs=[{"totalStockholdersEquity": 38e9}],
                    profile={"symbol": symbol.strip().upper(), "sector": FS,
                             "industry": industry, "mktCap": 6e11})
    monkeypatch.setattr(ps, "get_sector_benchmark_lookup", lambda: lookup)
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["V", "v ", "MA", "PYPL", "WU", "GPN", "TREE", "pmts"])
async def test_a_members_profitability_card_is_peer_free(monkeypatch, symbol):
    """No lookup (even one that would fail), NOT degraded, bare labels, `peer_level` None,
    absolute bands: V's 80/67/55% margins, 50% ROE and 20% ROA rate 5. Mutation: asking the
    lookup marks a peer-free card degraded on a Supabase blip; dropping the filter prints
    "(x.xx sector avg …)" against lenders."""
    lookup = _Recorder(boom=RuntimeError("supabase down"))
    svc = _prof_svc(monkeypatch, symbol, lookup)
    snap, degraded = await svc._compute_with_status(symbol)
    assert lookup.calls() == 0
    assert degraded == []
    for m in snap.metrics:
        assert m.peer_level is None and "sector avg" not in m.name and "vs" not in m.name
        assert m.score == 5, (m.name, m.score)
    assert snap.rating == 5 and snap.weighted_score is not None


@pytest.mark.asyncio
async def test_a_lenders_profitability_card_still_asks_for_all_five(monkeypatch):
    """AXP in the same industry: every median asked and compared. Control."""
    lookup = _Recorder(value=0.20)
    svc = _prof_svc(monkeypatch, "AXP", lookup)
    snap, degraded = await svc._compute_with_status("AXP")
    assert lookup.current == [["gross_margin", "operating_margin", "net_margin", "roe", "roa"]]
    assert degraded == []
    assert all(m.peer_level == "industry" for m in snap.metrics)


@pytest.mark.asyncio
async def test_a_listed_member_the_resolver_calls_a_lender_is_compared(monkeypatch):
    """The data veto (or any False verdict) makes a listed ticker a lender here too.
    Mutation: consulting the list directly instead of the resolver's verdict."""
    monkeypatch.setattr(ps, "resolve_payment_network", lambda *a, **k: False)
    lookup = _Recorder(value=0.20)
    svc = _prof_svc(monkeypatch, "V", lookup)
    await svc._compute_with_status("V")
    assert lookup.current == [["gross_margin", "operating_margin", "net_margin", "roe", "roa"]]


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. Valuation (Price) snapshot and the network-scoped two-judged-multiples rule
# ══════════════════════════════════════════════════════════════════════════════════════

_V_FR = {"priceToEarningsRatioTTM": 31.9, "priceToBookRatioTTM": 20.4,
         "priceToSalesRatioTTM": 17.0, "priceToFreeCashFlowRatioTTM": 33.3,
         "enterpriseValueMultipleTTM": 25.0}


def _val_svc(monkeypatch, symbol: str, lookup: _Recorder, fr=None):
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", False)
    monkeypatch.setattr(vss, "get_sector_benchmark_lookup", lambda: lookup)
    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
    svc.supabase = None
    svc.fmp = sph._FakeFMP(
        get_company_profile={"symbol": symbol, "sector": FS, "industry": CREDIT,
                             "mktCap": 6e11},
        get_ratios_ttm=[dict(_V_FR if fr is None else fr)],
    )
    return svc


@pytest.mark.asyncio
async def test_vs_price_card_is_unrated_with_no_lookup(monkeypatch, caplog):
    """V: no lookup, rating 0, no weighted_score, P/E keeps its band score (31.9 → 2), the
    other multiples unscored, one `[valuation-unrated]` INFO line naming V. Mutation: a
    rating of 2.75 → 3 built from four neutral votes ("a made-up verdict")."""
    lookup = _Recorder(boom=RuntimeError("supabase down"))
    svc = _val_svc(monkeypatch, "V", lookup)
    with caplog.at_level(logging.INFO, logger=vss.logger.name):
        snap, degraded = await svc._compute_with_status("V")
    assert lookup.calls() == 0 and degraded == []
    assert snap.rating == 0 and snap.weighted_score is None
    by = {m.metric_key: m for m in snap.metrics}
    assert by["pe"].score == 2
    for key in ("pb", "ps", "pfcf", "ev_ebitda", "earnings_yield"):
        assert by[key].score is None, key
    for m in snap.metrics:
        assert m.peer_level is None and "sector avg" not in m.name, m.name
    lines = [r.getMessage() for r in caplog.records if "[valuation-unrated]" in r.getMessage()]
    assert len(lines) == 1 and "ticker=V" in lines[0] and "judged=1" in lines[0]


@pytest.mark.asyncio
async def test_a_lenders_price_card_still_compares_and_rates(monkeypatch, caplog):
    """AXP: all six asked, every label prints the median, the card is rated, and no
    `[valuation-unrated]` line. Control for the network scope."""
    lookup = _Recorder(value=12.0)
    svc = _val_svc(monkeypatch, "AXP", lookup)
    with caplog.at_level(logging.INFO, logger=vss.logger.name):
        snap, degraded = await svc._compute_with_status("AXP")
    assert lookup.current == [["pe_ratio", "ps_ratio", "pb_ratio", "pfcf_ratio", "ev_ebitda",
                               "earnings_yield"]]
    assert degraded == [] and snap.rating > 0 and snap.weighted_score is not None
    assert all(m.peer_level == "industry" for m in snap.metrics), [
        (m.metric_key, m.peer_level) for m in snap.metrics]
    assert not [r for r in caplog.records if "[valuation-unrated]" in r.getMessage()]


@pytest.mark.asyncio
@pytest.mark.parametrize("boom", [True, False])
async def test_a_non_members_price_card_keeps_its_rating_without_medians(
    monkeypatch, caplog, boom,
):
    """The two-judged rule is network-SCOPED at the call site: a lender whose lookup fails
    (or answers no usable medians) is scored as before — P/E on bands, the rest unscored —
    and keeps a rating, with no `[valuation-unrated]` line. Mutation: passing
    `peers_withheld=True` at the call site unrates AXP here (it survived every other test)."""
    lookup = _Recorder(boom=RuntimeError("supabase down")) if boom else _Recorder(value=None)
    svc = _val_svc(monkeypatch, "AXP", lookup)
    with caplog.at_level(logging.INFO, logger=vss.logger.name):
        snap, degraded = await svc._compute_with_status("AXP")
    assert lookup.calls() == 1
    assert ("benchmarks" in degraded) is boom
    assert snap.rating > 0 and snap.weighted_score is not None
    assert not [r for r in caplog.records if "[valuation-unrated]" in r.getMessage()]


def _price(fr, bench=None, *, withheld=True) -> SnapshotItemResponse:
    return vss.build_price_snapshot(fr=fr, km={}, cf={}, inc={}, bs={}, profile={},
                                    bench=bench or {}, ticker="T", peers_withheld=withheld)


@pytest.mark.parametrize("fr,bench,judged", [
    ({"priceToEarningsRatioTTM": 10.3}, {}, 1),                               # PYPL-shaped
    ({"priceToEarningsRatioTTM": -21.2}, {}, 0),                              # GPN-shaped
    ({"priceToEarningsRatioTTM": 0.0}, {}, 0),                                # no earnings
    ({"priceToEarningsRatioTTM": float("nan"), "priceToBookRatioTTM": 2.0},
     {"pb_ratio": 1.5}, 1),                                                   # NaN never judged
    ({"priceToBookRatioTTM": -3.0, "priceToSalesRatioTTM": 2.0},
     {"pb_ratio": 1.5, "ps_ratio": 1.5}, 1),                                  # negative P/B
    ({"priceToBookRatioTTM": 3.0}, {"pb_ratio": 0.001}, 0),                   # unusable median
    ({}, {}, 0),
])
def test_fewer_than_two_judged_multiples_unrate_a_withheld_card(fr, bench, judged, caplog):
    """Mutation: counting a NaN / negative / zero multiple, or one beside an unusable
    median, as judged."""
    with caplog.at_level(logging.INFO, logger=vss.logger.name):
        snap = _price(fr, bench)
    assert snap.rating == 0 and snap.weighted_score is None
    assert any(f"judged={judged}" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("fr,bench", [
    ({"priceToEarningsRatioTTM": 20.0, "priceToBookRatioTTM": 2.0}, {"pb_ratio": 2.5}),
    ({"priceToEarningsRatioTTM": -5.0, "priceToBookRatioTTM": 2.0,
      "priceToSalesRatioTTM": 3.0}, {"pb_ratio": 2.5, "ps_ratio": 2.0}),
    ({"priceToBookRatioTTM": 2.0, "enterpriseValueMultipleTTM": 9.0},
     {"pb_ratio": 2.5, "ev_ebitda": 12.0}),
])
def test_two_judged_multiples_keep_the_rating(fr, bench):
    """P/E plus one peer-judged multiple, or two peer-judged multiples beside a negative
    P/E, is a real verdict. NOT A PRODUCTION PATH TODAY: a withheld card gets no medians
    (`bench={}`, see `test_a_withheld_card_is_always_unrated_in_production`); this pins
    the rule for the day a member is given a real peer median. Mutation: an off-by-one
    (`<= 2`)."""
    snap = _price(fr, bench)
    assert snap.rating > 0 and snap.weighted_score is not None


@pytest.mark.parametrize("fr", [
    {"priceToEarningsRatioTTM": 22.0, "priceToBookRatioTTM": 12.0,
     "priceToSalesRatioTTM": 15.0, "priceToFreeCashFlowRatioTTM": 30.0,
     "enterpriseValueMultipleTTM": 24.0},                                       # V-shaped
    {"priceToEarningsRatioTTM": 10.3},
    {"priceToEarningsRatioTTM": -21.2, "priceToBookRatioTTM": 1.1},
])
def test_a_withheld_card_is_always_unrated_in_production(fr, caplog):
    """What production passes for a member — `peers_withheld=True` with NO medians (no
    lookup ran) — is unrated whatever the multiples: only P/E can be judged. Documents
    the rule's real effect today (its docstring says so). Mutation: a bare multiple
    counted as judged without a median (V's five positive multiples would then rate)."""
    with caplog.at_level(logging.INFO, logger=vss.logger.name):
        snap = _price(fr, {})
    assert snap.rating == 0 and snap.weighted_score is None
    lines = [r.getMessage() for r in caplog.records if "[valuation-unrated]" in r.getMessage()]
    assert len(lines) == 1 and "only P/E" in lines[0]
    assert "judged=1" in lines[0] or "judged=0" in lines[0]


@pytest.mark.parametrize("fr", [
    {"priceToEarningsRatioTTM": 10.3},                                         # one judged
    {"priceToEarningsRatioTTM": -4.0, "priceToSalesRatioTTM": 0.0,             # biotech shape
     "priceToBookRatioTTM": 1.2, "priceToFreeCashFlowRatioTTM": -3.0,
     "enterpriseValueMultipleTTM": -2.0},
    {},
])
def test_the_rule_is_network_scoped(fr):
    """A non-member (peers not withheld) with fewer than two judged multiples keeps today's
    rating — the global rule is a separate owner decision. Mutation: applying it to all."""
    withheld, kept = _price(fr, withheld=True), _price(fr, withheld=False)
    assert withheld.rating == 0
    assert kept.rating > 0 and kept.weighted_score is not None


def test_the_default_build_is_unchanged():
    """`peers_withheld` defaults to False: every existing caller (the Overview fallback)
    rates exactly as before."""
    snap = vss.build_price_snapshot(fr={"priceToEarningsRatioTTM": 10.3}, km={}, cf={},
                                    inc={}, bs={}, profile={}, bench={})
    assert snap.rating > 0 and snap.weighted_score is not None


def test_a_rows_own_score_survives_the_unrating():
    """Each row keeps its own score on an unrated card (iOS still shows the band)."""
    snap = _price({"priceToEarningsRatioTTM": 9.0})
    assert snap.rating == 0
    assert next(m for m in snap.metrics if m.metric_key == "pe").score == 5


# ══════════════════════════════════════════════════════════════════════════════════════
# 4. Growth and Profit Power
# ══════════════════════════════════════════════════════════════════════════════════════


def _interest_quarters(rows, ii_share: float):
    """The fixture's quarters carrying interest fields at ``ii_share`` of revenue."""
    return [dict(r, interestIncome=r["revenue"] * ii_share, netInterestIncome=0.0) for r in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["V", "ma", "TREE"])
async def test_a_members_growth_reads_no_benchmark_series(monkeypatch, caplog, symbol):
    """Neither series read runs; every point's peer value is None; `peer_group_levels`
    empty; NOT degraded. Mutation: reading the series (MA's +15.7% drawn under lenders'
    +24.6%), or marking the peer-free build degraded."""
    lookup = gd._yoy_metrics_lookup()
    fmp = gd._FakeFMP(profile={"symbol": symbol, "sector": FS, "industry": CREDIT})
    svc = gd._service(monkeypatch, fmp, lookup)
    with caplog.at_level(logging.INFO, logger=gmod.logger.name):
        resp, degraded = await svc._build_growth(symbol)
    assert lookup.calls == [] and degraded == [] and resp.peer_group_levels == {}
    for series in ("eps_annual", "revenue_annual", "operating_profit_annual",
                   "free_cash_flow_annual", "revenue_quarterly", "free_cash_flow_quarterly"):
        assert all(p.sector_average_yoy is None for p in getattr(resp, series)), series
    assert any("no benchmark read" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol,quarters", [
    ("AXP", gd._CAL_QUARTERS),                                  # a lender
    ("V", _interest_quarters(gd._CAL_QUARTERS, 0.85)),          # V vetoed by its quarters
])
async def test_a_lenders_growth_still_reads_both_series(monkeypatch, symbol, quarters):
    """Control, and the ONE input: the trailing quarters veto a listed ticker here too."""
    lookup = gd._yoy_metrics_lookup()
    fmp = gd._FakeFMP(quarterly=quarters,
                      profile={"symbol": symbol, "sector": FS, "industry": CREDIT})
    svc = gd._service(monkeypatch, fmp, lookup)
    resp, degraded = await svc._build_growth(symbol)
    assert [c[0] for c in lookup.calls] == ["annual", gmod.CALENDAR_QUARTER_PERIOD_TYPE]
    assert all(len(c[1]) == 5 for c in lookup.calls)
    assert resp.peer_group_levels and degraded == []


class _PPFake(gd._FakeFMP):
    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        return []


def _pp_service(monkeypatch, symbol: str, lookup, quarters=None, annual=None):
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)
    svc.supabase = None
    svc.fmp = _PPFake(quarterly=quarters, annual=annual,
                      profile={"symbol": symbol, "sector": FS, "industry": CREDIT})
    return svc


@pytest.mark.asyncio
async def test_a_members_profit_power_draws_no_peer_line(monkeypatch):
    """V: no read, no peer point, no level (so Cay AI's peer net-margin sentence, which
    keys off `peer_group_level`, is gone), not degraded. Mutation: the read left in."""
    lookup = _Recorder(boom=RuntimeError("supabase down"))
    resp, _next, degraded = await _pp_service(monkeypatch, "V", lookup)._build_profit_power("V")
    assert lookup.calls() == 0 and degraded == []
    assert resp.peer_group_levels == {} and resp.peer_group_level is None
    assert resp.annual or resp.quarterly, "the fixture must draw company points"
    for p in [*resp.annual, *resp.quarterly]:
        for field in ("sector_average_net_margin", "sector_average_gross_margin",
                      "sector_average_operating_margin", "sector_average_fcf_margin"):
            assert getattr(p, field) is None, field


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol,quarters", [
    ("AXP", gd._CAL_QUARTERS),                                  # a lender
    ("V", _interest_quarters(gd._CAL_QUARTERS, 0.85)),          # V vetoed by its quarters
])
async def test_a_lenders_profit_power_still_reads_both_series(monkeypatch, symbol, quarters):
    """Control, and the ONE input (spec D1): the verdict reads
    `trailing_interest_row(quarterly income)`, so V whose trailing quarters read as a
    lender's is gated here exactly as on Growth and the Health Check — both series read,
    all four margins. Mutation: the verdict input replaced with None or the annual row
    (V then stays peer-free on Profit Power while Growth compares it — two cards of one
    company disagree)."""
    lookup = _Recorder()
    resp, _next, degraded = await _pp_service(
        monkeypatch, symbol, lookup, quarters=quarters)._build_profit_power(symbol)
    assert [c[0] for c in lookup.series] == ["annual", pp.CALENDAR_QUARTER_PERIOD_TYPE]
    assert all(c[1] == ["net_margin", "gross_margin", "operating_margin", "fcf_margin"]
               for c in lookup.series)
    assert degraded == []


@pytest.mark.asyncio
async def test_profit_powers_verdict_ignores_a_lender_shaped_annual_row(monkeypatch):
    """The verdict never reads the annual income: V with network-shaped quarters but a
    lender-shaped latest ANNUAL row stays a non-lender member — no read. Mutation: the
    verdict fed `annual_income[0]` (gated on Profit Power only, while Growth and the
    Health Check, reading the quarters, keep V peer-free)."""
    lookup = _Recorder(boom=RuntimeError("supabase down"))
    annual = _interest_quarters(gd._ANNUAL, 0.85)
    resp, _next, degraded = await _pp_service(
        monkeypatch, "V", lookup, quarters=_interest_quarters(gd._CAL_QUARTERS, 0.0),
        annual=annual)._build_profit_power("V")
    assert lookup.calls() == 0 and degraded == []
    assert resp.peer_group_level is None


# ══════════════════════════════════════════════════════════════════════════════════════
# 5. The Health Check
# ══════════════════════════════════════════════════════════════════════════════════════


async def _hc(monkeypatch, symbol: str, lookup: _Recorder, *, ratios=None, income=None,
              industry=CREDIT, bs=None):
    monkeypatch.setattr(hc, "get_sector_benchmark_lookup", lambda: lookup)
    hc._cache.clear()
    hc._inflight.clear()
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = None
    svc.fmp = hcd._FakeFMP(hcd._answers(
        profile=dict(hcd._PROFILE, symbol=symbol, sector=FS, industry=industry),
        ratios=[dict(nv._V_RATIOS if ratios is None else ratios)],
        income=nv._quarters(*nv._NETWORKS["V"]) if income is None else income,
        bs=bs,
    ))
    resp, _next = await svc._build_health_check(symbol)
    return resp


@pytest.mark.asyncio
async def test_vs_health_check_judges_pe_and_roe_on_absolute_bands(monkeypatch, caplog):
    """V's P/E 31.9 (neutral band, was "negative" against lenders' 14) and ROE (positive)
    carry no comparison, and the lookup is never called — even one that would raise, so
    the build is NOT degraded. Mutation: `bench_metrics == []` still calling the lookup
    (a Supabase blip marks V degraded → never persisted → FMP on every view)."""
    lookup = _Recorder(boom=RuntimeError("supabase down"))
    with caplog.at_level(logging.INFO, logger=hc.logger.name):
        resp = await _hc(monkeypatch, "V", lookup, ratios=dict(nv._V_RATIOS,
                                                               priceToEarningsRatioTTM=31.9))
    assert lookup.calls() == 0 and resp.degraded == []
    # The INFO line names every row judged on absolute bands only (logs alone explain a
    # bare row). Mutation: the `if absolute_only:` log skipped.
    absolute = [r.getMessage() for r in caplog.records
                if "judged on absolute bands only" in r.getMessage()]
    assert len(absolute) == 1, absolute
    for metric in ("pe_ratio", "roe", "debt_to_equity", "current_ratio"):
        assert metric in absolute[0], (metric, absolute[0])
    by = nv._by(resp)
    assert by["pe_ratio"].status == "neutral" and by["roe"].status == "positive"
    for row in resp.metrics:
        assert row.peer_level is None and row.comparison_value is None, row.type
        text = hcd._rendered(row)
        assert "industry" not in text and "sector" not in text, text
    assert hc._HC_PAYLOAD_VERSION == 9


@pytest.mark.asyncio
async def test_a_lenders_health_check_is_unchanged(monkeypatch):
    """AXP: D/E, P/E and ROE asked and compared; a lookup failure degrades the build."""
    lookup = _Recorder(value=1.5)
    resp = await _hc(monkeypatch, "AXP", lookup, income=nv._quarters(*nv._LENDERS["AXP"]))
    assert lookup.current == [["debt_to_equity", "pe_ratio", "roe"]]
    assert all(m.peer_level == "industry" for m in resp.metrics)
    failing = _Recorder(boom=RuntimeError("supabase down"))
    resp = await _hc(monkeypatch, "AXP", failing, income=nv._quarters(*nv._LENDERS["AXP"]))
    assert "benchmarks" in resp.degraded


# ══════════════════════════════════════════════════════════════════════════════════════
# 6. The report: lines, the ticker-wide level, the card verdicts, the valuation vital
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("metric", ["roe", "pe_ratio", "gross_margin", "revenue_yoy",
                                    "earnings_yield", "debt_to_equity"])
def test_every_non_gated_line_of_a_member_is_withheld(metric):
    assert C._withheld_peer_line_reason(metric, "industry", CREDIT, FS, network=True) == (
        "payment_network")
    assert C._withheld_peer_line_reason(metric, "industry", CREDIT, FS, network=False) is None


def _full_history() -> Dict[str, Any]:
    names = ("roe", "pe_ratio", "gross_margin", "ev_ebitda", "debt_to_equity", "net_margin")
    return {
        "annual": {m: {"2024": 1.0, "2025": 1.1, r4._CUR: 1.2} for m in names},
        "quarterly": {m: {"Q1'26": 1.0} for m in names},
        "levels": {"annual": {m: "industry" for m in names},
                   "quarterly": {m: "industry" for m in names}},
    }


def test_a_members_lines_and_ttm_points_all_go_and_a_lenders_stay():
    """Mutation: the pass-2 filter still keyed on D/E alone."""
    hist = _full_history()
    C._withhold_payment_network_lines(hist, network=True, industry=CREDIT, sector=FS,
                                      ticker="V")
    assert hist["annual"] == {} and hist["quarterly"] == {}
    assert hist["levels"] == {"annual": {}, "quarterly": {}}
    hist = _full_history()
    C._withhold_payment_network_lines(hist, network=False, industry=CREDIT, sector=FS,
                                      ticker="AXP")
    assert hist == _full_history()


def test_a_failed_read_keeps_its_type_when_emptied():
    hist = BenchmarkLookupFailed(_full_history())
    C._withhold_payment_network_lines(hist, network=True, industry=CREDIT, sector=FS,
                                      ticker="V")
    assert isinstance(hist, BenchmarkLookupFailed) and hist["annual"] == {}


def _snap(category: str, rows: Dict[str, int], *, rating: int = 3, level=None):
    return SnapshotItemResponse(
        category=category, rating=rating, weighted_score=float(rating),
        metrics=[SnapshotMetricResponse(name=k, value="1", metric_key=k, score=s,
                                        peer_level=level) for k, s in rows.items()],
    )


@pytest.mark.parametrize("title,rows,expected", [
    ("Profitability", {"gross_margin": 5, "net_margin": 3}, "High Gross Margins"),
    ("Valuation", {"pe": 1, "pb": 3}, "High P/E"),
    ("Valuation", {"ev_ebitda": 1, "pb": 3}, "High EV/EBITDA"),
    ("Growth", {"revenue_growth": 3, "eps_growth": 3}, "Neither Strong Nor Weak"),
    ("Health", {"debt_to_equity": 3}, "Neither Strong Nor Weak"),
])
def test_a_members_report_cards_name_no_peer(title, rows, expected):
    """`non_lender_member=True` makes EVERY card peer-free, even with `peer_group_level`
    "industry". Mutation: the Health-only rule ("Pricey vs Industry" on V's Price card)."""
    card = C._snapshot_to_card(title, _snap(title, rows), peer_group_level="industry",
                               non_lender_member=True)
    assert card["quality_label"] == expected
    assert "Industry" not in card["quality_label"] and "Sector" not in card["quality_label"]


@pytest.mark.parametrize("title,rows,expected", [
    ("Valuation", {"pe": 1, "pb": 3}, "Pricey vs Industry"),
    ("Profitability", {"gross_margin": 5, "net_margin": 3}, "Fat Margins vs Industry"),
    ("Profitability", {"gross_margin": 3, "net_margin": 3}, "In Line With Industry"),
])
def test_a_non_members_cards_keep_todays_wording(title, rows, expected):
    """Out of scope: a non-member's non-Health card stays peer-worded even with no
    `peer_level` row (that broader change is not this decision)."""
    for flag in (False, None, 1):
        card = C._snapshot_to_card(title, _snap(title, rows), peer_group_level="industry",
                                   non_lender_member=flag)
        assert card["quality_label"] == expected, flag


class _NoLookup:
    def __init__(self) -> None:
        self.calls = 0

    def get_current_benchmarks(self, industry, sector, metrics):
        self.calls += 1
        return {m: {"value": 1.0, "level": "industry", "n": 60} for m in metrics}


def _sections(monkeypatch, *, ticker: str, member: bool, ratios_rows=None):
    lookup = _NoLookup()
    monkeypatch.setattr("app.services.sector_benchmark_lookup.get_sector_benchmark_lookup",
                        lambda: lookup)
    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    out = r4._collected({"sector": FS, "industry": CREDIT}, de=0.55, fcf=2.0e10,
                        snap_health=_snap("Financial Health", {"debt_to_equity": 3}))
    out.ticker = ticker
    out.non_lender_member = member
    out.snap_profitability = _snap("Profitability", {"gross_margin": 5, "net_margin": 3})
    out.snap_valuation = _snap("Price", {"pe": 1, "pb": 3})
    out.snap_growth = _snap("Growth", {"revenue_growth": 3})
    if ratios_rows is not None:
        out.ratios = ratios_rows
    coll._compute_metrics(out)
    coll._build_sections(out)
    return out, lookup


def test_build_sections_makes_every_card_of_a_member_peer_free(monkeypatch):
    """V: no lookup for the ticker-wide level, `peer_group_level` None on all four cards,
    no peer wording. AXP (control): the level is "industry" and the Price drag reads
    "Pricey vs Industry". Mutation: the pass-2 verdict not reaching `_build_sections`."""
    out, lookup = _sections(monkeypatch, ticker="V", member=True)
    assert lookup.calls == 0
    cards = {c["title"]: c for c in out.fundamental_metrics_partial}
    assert all(c["peer_group_level"] is None for c in cards.values())
    assert cards["Valuation"]["quality_label"] == "High P/E"
    assert cards["Profitability"]["quality_label"] == "High Gross Margins"

    out, lookup = _sections(monkeypatch, ticker="AXP", member=False)
    assert lookup.calls == 1
    cards = {c["title"]: c for c in out.fundamental_metrics_partial}
    assert all(c["peer_group_level"] == "industry" for c in cards.values())
    assert cards["Valuation"]["quality_label"] == "Pricey vs Industry"


@pytest.mark.parametrize("has_dcf", [True, False])
def test_an_unrated_price_card_leaves_the_valuation_vital_to_the_dcf(has_dcf):
    """Rating 0 and no weighted_score: the vital scores from the DCF upside when one
    exists, else it is UNMEASURED — never a neutral vote."""
    card = SnapshotItemResponse(category="Price", rating=0, weighted_score=None, metrics=[])
    vital = C._build_valuation_vital(100.0, 130.0 if has_dcf else None,
                                     30.0 if has_dcf else None, card)
    if has_dcf:
        assert vital["score"]["value"] == C._valuation_score_from_upside(30.0)
    else:
        assert vital["score"] == {"value": None, "status": "unmeasured"}


# ══════════════════════════════════════════════════════════════════════════════════════
# 7. card_verdict's peer-free phrases
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("title,key,score,expected", [
    ("Valuation", "pe", 1, "High P/E"), ("Valuation", "pe", 5, "Low P/E"),
    ("Valuation", "ev_ebitda", 1, "High EV/EBITDA"), ("Valuation", "ev_ebitda", 5, "Low EV/EBITDA"),
    ("Profitability", "gross_margin", 5, "High Gross Margins"),
    ("Profitability", "gross_margin", 1, "Low Gross Margins"),
])
def test_uncompared_peer_phrases(title, key, score, expected):
    label, _ = cv.generate_card_verdict(title, 3, "industry", [(key, score)],
                                        peer_compared=False)
    assert label == expected


@pytest.mark.parametrize("title,key,score,expected", [
    ("Valuation", "pe", 1, "Pricey vs Industry"),
    ("Profitability", "gross_margin", 5, "Fat Margins vs Industry"),
    ("Valuation", "ev_ebitda", 5, "Cheap vs Industry"),
])
def test_compared_and_legacy_phrases_are_unchanged(title, key, score, expected):
    assert cv.generate_card_verdict(title, 3, "industry", [(key, score)],
                                    peer_compared=True)[0] == expected
    assert cv.generate_card_verdict(title, 3, "industry", [(key, score)])[0] == expected


def test_every_peer_phrase_has_a_peer_free_twin():
    """Structural: a new "{peer}" phrase without a twin would leak "vs Sector" onto a
    member's card. Mutation: deleting one twin."""
    with_peer = {k for k, pair in cv._VOCAB.items() if any("{peer}" in p for p in pair)}
    assert with_peer == set(cv._PEER_FREE_VOCAB)
    for pair in cv._PEER_FREE_VOCAB.values():
        for phrase in pair:
            assert "{peer}" not in phrase and "Industry" not in phrase and "Sector" not in phrase


def test_an_unrated_card_is_not_rated_whatever_its_rows():
    assert cv.generate_card_verdict("Valuation", 0, None, [("pe", 1)],
                                    peer_compared=False) == ("Not Rated", "neutral")


# ══════════════════════════════════════════════════════════════════════════════════════
# 8. The producer: the Credit Services median is the lenders'
# ══════════════════════════════════════════════════════════════════════════════════════


def _universe(cs_caps: Dict[str, Any], other=None) -> List[Dict[str, Any]]:
    entries = [{"industry": CREDIT, "sector": FS, "tickers": list(cs_caps),
                "market_caps": cs_caps}]
    if other:
        entries.append(other)
    return entries


def test_the_producer_drops_the_members_before_the_top_n_cut(monkeypatch, caplog):
    """The five networks hold the largest caps. With a cut of 4, the lenders must fill all
    four slots — excluding AFTER the cut would leave fewer. One INFO line names them.
    Mutation: a filter after the slice, or none."""
    caps = {"V": 6e11, "MA": 5e11, "PYPL": 7e10, "wu ": 6e9, "Gpn": 2e10, "TREE": 3.5e8,
            "AXP": 2e11, "COF": 1.3e11, "SYF": 2.5e10, "ALLY": 1.2e10, "SOFI": 2e10}
    other = {"industry": "Software - Infrastructure", "sector": "Technology",
             "tickers": ["V", "MSFT"], "market_caps": {"V": 1e9, "MSFT": 3e12}}
    monkeypatch.setattr(ibs, "_benchmark_universe_entries", lambda f=None: _universe(caps, other))
    monkeypatch.setattr(ibs, "TOP_TICKERS_PER_INDUSTRY", 4)
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)
    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        universe = svc._load_universe()
    by_industry = {ind: [t for t, _c in rows] for _s, inds in universe for ind, rows in inds}
    assert by_industry[CREDIT] == ["AXP", "COF", "SYF", "SOFI"]
    # V filed elsewhere is an ordinary member of that industry.
    assert "V" in by_industry["Software - Infrastructure"]
    lines = [r.getMessage() for r in caplog.records if "left out of the mixed industry" in r.getMessage()]
    assert len(lines) == 1
    for t in ("V", "MA", "PYPL", "WU", "GPN", "TREE"):
        assert t in lines[0], t


def test_the_producer_logs_nothing_when_no_member_is_present(monkeypatch, caplog):
    monkeypatch.setattr(ibs, "_benchmark_universe_entries",
                        lambda f=None: _universe({"AXP": 2e11, "COF": 1e11}))
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)
    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        universe = svc._load_universe()
    assert [t for _s, inds in universe for _i, rows in inds for t, _c in rows] == ["AXP", "COF"]
    assert not [r for r in caplog.records if "left out of the mixed industry" in r.getMessage()]


@pytest.mark.parametrize("ticker,industry,expected", [
    ("V", CREDIT, True), (" pypl ", "Financial—Credit Services", True), ("TREE", CREDIT, True),
    ("AXP", CREDIT, False), ("V", "Banks - Diversified", False), (None, CREDIT, False),
    ("", CREDIT, False), (5, CREDIT, False), ("NRDS", CREDIT, False),
])
def test_excluded_from_industry_median(ticker, industry, expected):
    assert gate.excluded_from_industry_median(ticker, industry) is expected


# ══════════════════════════════════════════════════════════════════════════════════════
# 9. Source-scan guard: every comparison surface asks the gate BEFORE its benchmark read
# ══════════════════════════════════════════════════════════════════════════════════════

_READS = {"get_current_benchmarks", "get_benchmark_series"}
# (class, method, the verdict's income input): the trailing quarters where the surface
# fetches them (`trailing_interest_row(<that variable>)`), else the literal None — ONE
# input everywhere (spec D1), never an annual row on one card only.
_SURFACES = [
    (ps.ProfitabilitySnapshotService, "_compute_with_status", None),
    (vss.ValuationSnapshotService, "_compute_with_status", None),
    (gmod.GrowthService, "_build_growth", "quarterly_income"),
    (pp.ProfitPowerService, "_build_profit_power", "quarterly_income"),
    (hc.HealthCheckService, "_build_health_check_full", "inc_raw"),
]
_MUTATING_METHODS = {"append", "extend", "insert", "clear", "pop", "remove", "__iadd__"}


def _function_tree(cls, name: str) -> ast.FunctionDef:
    """The method's OWN AST (brace-bound; comments and docstrings cannot satisfy a scan of
    calls)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(cls, name))))
    fn = tree.body[0]
    assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name
    return fn


def _call_lines(fn, callee: str) -> List[int]:
    return sorted(
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Name) and n.func.id == callee)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == callee))
    )


def _assigned_from(fn, callee: str) -> set:
    """Names bound to the result of ``callee(...)`` in ``fn``."""
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            f = n.value.func
            if (isinstance(f, ast.Name) and f.id == callee) or (
                    isinstance(f, ast.Attribute) and f.attr == callee):
                out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return out


def _stores(fn, name: str) -> List[ast.AST]:
    """Every node in ``fn`` that (re)binds or mutates ``name``: a Store of the name (an
    assignment, augmented assignment, loop or walrus target) or an in-place list method
    called on it. A filtered metric list must be bound ONCE and never refilled."""
    out: List[ast.AST] = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store)
    ]
    out += [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in _MUTATING_METHODS
        and isinstance(n.func.value, ast.Name) and n.func.value.id == name
    ]
    return out


def _calls(fn, callee: str) -> List[ast.Call]:
    return [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Name) and n.func.id == callee)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == callee))
    ]


def _assert_verdict_wiring(fn, name: str, income_arg) -> str:
    """The method resolves the verdict ONCE, from the expected income input, into one
    name bound once; returns that name."""
    resolves = _calls(fn, "resolve_payment_network")
    assert len(resolves) == 1, (name, len(resolves))
    income = resolves[0].args[2] if len(resolves[0].args) > 2 else None
    if income_arg is None:
        assert isinstance(income, ast.Constant) and income.value is None, (
            name, ast.unparse(resolves[0]))
    else:
        assert (isinstance(income, ast.Call) and isinstance(income.func, ast.Name)
                and income.func.id == "trailing_interest_row"
                and len(income.args) == 1 and isinstance(income.args[0], ast.Name)
                and income.args[0].id == income_arg), (name, ast.unparse(resolves[0]))
    verdicts = _assigned_from(fn, "resolve_payment_network")
    assert len(verdicts) == 1, (name, verdicts)
    (verdict,) = verdicts
    assert len(_stores(fn, verdict)) == 1, (name, verdict, "re-bound after the verdict")
    return verdict


def _network_kw_is(call: ast.Call, verdict: str) -> bool:
    kws = [k for k in call.keywords if k.arg == "network"]
    return (len(kws) == 1 and isinstance(kws[0].value, ast.Name)
            and kws[0].value.id == verdict)


@pytest.mark.parametrize("cls,name,income_arg", _SURFACES,
                         ids=[n for _c, n, _i in _SURFACES])
def test_each_surface_asks_the_gate_before_its_benchmark_read(cls, name, income_arg):
    """Brace-bound to each method (AST: comments and docstrings cannot satisfy it):
      * it resolves the company verdict ONCE, from the one income input (the trailing
        quarters it fetched, or None), into a name never re-bound;
      * it filters its metric list through the gate with ``network=`` THAT verdict —
        `comparable_peer_metrics`, or, on the Health Check, `peer_median_comparable` into
        ``absolute_only`` and a ``bench_metrics`` comprehension that excludes it;
      * the filtered list is bound once and never refilled or mutated;
      * every benchmark read comes after the gate and is handed THAT filtered list.
    Mutation-tested by hand (2026-10-09): the raw list passed to the read, the Health
    Check's ``absolute_only`` filter dropped, ``bench_metrics = bench_metrics or [...]``,
    ``network=False``, and the verdict input replaced with None / the annual row each fail
    this."""
    fn = _function_tree(cls, name)
    verdict = _assert_verdict_wiring(fn, name, income_arg)
    if name == "_build_health_check_full":
        gate_calls = _calls(fn, "peer_median_comparable")
        assert gate_calls and all(_network_kw_is(c, verdict) for c in gate_calls), name
        assert len(_stores(fn, "absolute_only")) == 1
        (abs_assign,) = [n for n in ast.walk(fn) if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "absolute_only" for t in n.targets)]
        assert any(c in gate_calls for c in ast.walk(abs_assign.value)), \
            "absolute_only must come from peer_median_comparable"
        assert len(_stores(fn, "bench_metrics")) == 1
        (bench_assign,) = [n for n in ast.walk(fn) if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "bench_metrics" for t in n.targets)]
        comp = bench_assign.value
        assert isinstance(comp, ast.ListComp), ast.unparse(comp)
        excludes = [
            c for g in comp.generators for cond in g.ifs for c in ast.walk(cond)
            if isinstance(c, ast.Compare) and any(isinstance(o, ast.NotIn) for o in c.ops)
            and any(isinstance(x, ast.Name) and x.id == "absolute_only"
                    for x in c.comparators)
        ]
        assert excludes, f"bench_metrics must exclude absolute_only: {ast.unparse(comp)}"
        filtered = {"bench_metrics"}
        gate_lines = [c.lineno for c in gate_calls]
    else:
        gate_calls = _calls(fn, "comparable_peer_metrics")
        assert len(gate_calls) == 1 and _network_kw_is(gate_calls[0], verdict), (
            name, [ast.unparse(c) for c in gate_calls])
        filtered = _assigned_from(fn, "comparable_peer_metrics")
        assert len(filtered) == 1, (name, filtered)
        gate_lines = [gate_calls[0].lineno]
    for fname in filtered:
        assert len(_stores(fn, fname)) == 1, (name, fname, "re-bound or mutated")
    resolve = _call_lines(fn, "resolve_payment_network")
    reads = [n for n in ast.walk(fn)
             if isinstance(n, ast.Attribute) and n.attr in _READS]
    assert reads, name
    for read in reads:
        assert read.lineno > max(min(resolve), max(gate_lines)), (name, read.lineno)
    # Every read's metric argument is the filtered list (by name).
    read_calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and any(
        isinstance(a, ast.Attribute) and a.attr in _READS for a in n.args)]
    assert read_calls, name
    for call in read_calls:
        names = {a.id for a in call.args if isinstance(a, ast.Name)}
        assert names & filtered, (name, ast.unparse(call))


def test_the_fallback_health_card_asks_the_gate_before_its_read():
    from app.services import health_snapshot_service as hs

    fn = _function_tree(hs.HealthSnapshotService, "_compute_with_status")
    verdict = _assert_verdict_wiring(fn, "health_snapshot_fallback", "inc_raw")
    gate_calls = _calls(fn, "comparable_peer_metrics")
    assert len(gate_calls) == 1 and _network_kw_is(gate_calls[0], verdict)
    assert _assigned_from(fn, "comparable_peer_metrics") == {"fb_metrics"}
    assert len(_stores(fn, "fb_metrics")) == 1
    gate_lines = _call_lines(fn, "comparable_peer_metrics")
    reads = [n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.Attribute) and n.attr == "get_current_benchmarks"]
    assert gate_lines and reads and min(reads) > min(gate_lines)
    read_calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and any(
        isinstance(a, ast.Attribute) and a.attr == "get_current_benchmarks" for a in n.args)]
    assert read_calls and all(
        "fb_metrics" in {a.id for a in c.args if isinstance(a, ast.Name)} for c in read_calls)


# ══════════════════════════════════════════════════════════════════════════════════════
# 10. One verdict input everywhere: the Overview fallback reads the trailing QUARTERS
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("quarters,kept", [
    (None, True),                                              # no quarters: the list stands
    (nv._quarters(*nv._NETWORKS["V"]), True),
    (nv._quarters(10e9, 0.0, 8.5e9), False),                   # lender-shaped: vetoed
])
def test_build_snapshots_threads_the_quarters_to_the_health_fallback(quarters, kept):
    """`_build_snapshots` hands its ``income_quarterly`` to the degraded Health builder, so
    the Overview card reaches the SAME verdict as every other surface. Mutation: the
    keyword dropped on that hop (the veto never reaches the Overview)."""
    from app.services.stock_overview_service import StockOverviewService

    svc = StockOverviewService.__new__(StockOverviewService)
    snaps = svc._build_snapshots(
        [{}], [{"interestCoverage": 40.0}], [nv._annual(*nv._NETWORKS["V"])],
        [dict(nv._V_BS)], [{}], 100.0, 6.3e11, FS, industry=CREDIT, ticker="V",
        income_quarterly=quarters,
    )
    health = next(s for s in snaps if s.category == "Financial Health")
    assert ("Interest Coverage" in [m.name for m in health.metrics]) is kept


def test_the_overview_response_passes_its_quarters_to_the_snapshots():
    """AST, not text: `_build_full_response` hands ``income_quarterly`` to
    `_build_snapshots`. Mutation: the keyword dropped there."""
    from app.services import stock_overview_service as sos

    fn = _function_tree(sos.StockOverviewService, "_build_full_response")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "_build_snapshots"]
    assert calls
    for call in calls:
        kw = {k.arg: ast.unparse(k.value) for k in call.keywords if k.arg}
        assert kw.get("income_quarterly") == "income_quarterly", kw


# ══════════════════════════════════════════════════════════════════════════════════════
# 11. Payload versions: a pre-NET-4 cached row is a MISS (rebuilt), the current one served
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_growth_payload_version_is_6_and_older_rows_rebuild(monkeypatch):
    """Growth v6 (2026-10-09, NET-4): a v5 row of MA drew the lenders' +24.6% operating-
    income line under its +15.7%. Mutation: the constant left at 5 serves that row."""
    assert gmod._GROWTH_PAYLOAD_VERSION == 6
    lookup = gd._yoy_metrics_lookup()
    fmp = gd._FakeFMP(profile={"symbol": "MA", "sector": FS, "industry": CREDIT})
    resp, _degraded = await gd._service(monkeypatch, fmp, lookup)._build_growth("MA")
    body = resp.model_dump()
    svc = gmod.GrowthService.__new__(gmod.GrowthService)
    for old in (None, 4, 5):
        stale = dict(body) if old is None else {**body, gmod._VERSION_KEY: old}
        svc.supabase = sph._Supabase([sph._row(stale)[0]])
        assert svc._check_supabase_cache("MA") is None, f"a v{old} Growth row was served"
    svc.supabase = sph._Supabase([sph._row({**body, gmod._VERSION_KEY: 6})[0]])
    assert svc._check_supabase_cache("MA") is not None


def test_growth_snapshot_payload_version_is_4_and_older_rows_rebuild():
    """Growth snapshot v4 (2026-10-09, NET-4): a v3 row of MA scored its growth 2/5 against
    the lenders' median. Mutation: the constant left at 3 serves that score."""
    from app.services import growth_snapshot_service as gss

    assert gss._SNAPSHOT_PAYLOAD_VERSION == 4
    body = SnapshotItemResponse(category="Growth", rating=2, metrics=[],
                                computed_at="2026-10-08T09:15:00Z").model_dump()
    svc = gss.GrowthSnapshotService.__new__(gss.GrowthSnapshotService)
    for old in (None, 2, 3):
        stale = dict(body) if old is None else {**body, gss._VERSION_KEY: old}
        svc.supabase = sph._Supabase([sph._row(stale)[0]])
        assert svc._check_supabase_cache("MA") is None, f"a v{old} growth snapshot was served"
    svc.supabase = sph._Supabase([sph._row({**body, gss._VERSION_KEY: 4})[0]])
    assert svc._check_supabase_cache("MA") is not None


@pytest.mark.asyncio
async def test_profit_power_payload_version_is_8_and_older_rows_rebuild(monkeypatch):
    """Profit Power v8 (2026-10-09, NET-4): a v7 row of V drew the lenders' margin lines
    beside V's. Mutation: the constant left at 7 serves that row."""
    assert pp._PP_PAYLOAD_VERSION == 8
    resp, _next, _degraded = await _pp_service(
        monkeypatch, "V", _Recorder())._build_profit_power("V")
    body = resp.model_dump()
    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)
    for old in (None, 6, 7):
        stale = dict(body) if old is None else {**body, pp._VERSION_KEY: old}
        svc.supabase = sph._Supabase([sph._row(stale)[0]])
        assert svc._check_supabase_cache("V") is None, f"a v{old} Profit Power row was served"
    svc.supabase = sph._Supabase([sph._row({**body, pp._VERSION_KEY: 8})[0]])
    assert svc._check_supabase_cache("V") is not None
