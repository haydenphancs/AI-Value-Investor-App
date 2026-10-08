"""Card networks and payment processors are not lenders (owner decision 3, 2026-10-08).

"Financial - Credit Services" is a MIXED FMP industry: payment networks, processors and
money-transfer firms (V, MA, PYPL, WU, GPN) beside lenders (AXP, COF, SYF, ALLY, SOFI,
AFRM, OMF, CACC, SLM, NAVI, ENVA, SEZL …). Until today the whole industry lost current
ratio, quick ratio and interest coverage. Now a company there is a PAYMENT NETWORK only when
its ticker is on the curated `financials_metric_gate.PAYMENT_NETWORKS` list AND its own
income does not read as a lender's (`lender_verdict`, a sanity check: interest income at
least 25% of revenue). A network keeps the three rows, and they and its debt-to-equity are
judged on ABSOLUTE bands only (the industry's medians pool lenders), and the report
drill-down draws no line for them. Every other member — a lender, an unlisted company whose
interest data looks network-shaped (FMP zero-fills real lenders' interest income), a
caller with no ticker — stays gated (fail closed). Every other industry is unchanged.

This file pins the sanity check, the trailing-quarter sum, the applicability tables and
every surface's plumbing; `test_card_networks_allowlist_2026_10_08.py` pins the allow-list
decision itself (the list, normalisation, the probe's zero-filled lenders, the lender-data
veto, the missing ticker at every site, D/E on absolute bands). The probe numbers are the
read-only FMP probe of 2026-10-08 (latest annual interest income / revenue). Each test's
docstring names the bug it catches (its mutation).

2026-10-09 (owner decisions NET-4 / NET-5): the list is `NON_LENDER_MEMBERS` (the five
networks plus the fee businesses TREE and PMTS), EVERY metric of a listed member is judged
without the Credit Services median (P/E and ROE included; no lookup at all when nothing is
left to ask), and a curated per-company fact withholds WU's current ratio, quick ratio and
interest coverage (`CURATED_WITHHELD_ROWS`). The pins below follow;
`test_non_lender_members_peer_free_2026_10_09.py` covers the new rules end to end.

Hermetic: stubbed FMP answers, peer lookups and Supabase tables — no network.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

import test_health_check_deepcheck as hcd
import test_report_round4_2026_10_08 as r4
import test_snapshot_cards_2026_10_07_profitability_health as sph
from app.schemas.health_check import HealthCheckResponse
from app.schemas.stock_overview import SnapshotItemResponse
from app.services import financials_metric_gate as gate
from app.services import health_check_service as hc
from app.services import health_snapshot_service as hs
from app.services.agents import ticker_report_data_collector as C
from app.services.industry_benchmark_service import IndustryBenchmarkService
from app.services.stock_overview_service import StockOverviewService

CREDIT = "Financial - Credit Services"
FS = "Financial Services"
_GATED = {"current_ratio", "quick_ratio", "interest_coverage"}
_ABSOLUTE = _GATED | {"debt_to_equity"}
_DATES = ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")


# ── fixtures: FMP-shaped income rows ─────────────────────────────────────────────────


def _annual(revenue: Any, ii: Any = 0.0, nii: Any = 0.0, **extra) -> Dict[str, Any]:
    row = {"date": "2025-12-31", "period": "FY", "revenue": revenue,
           "interestIncome": ii, "netInterestIncome": nii}
    row.update(extra)
    return row


def _quarters(revenue: float, ii: Any, nii: Any,
              overrides: Optional[Dict[int, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Four dated quarters (newest first) carrying the deep-check operating fields plus the
    interest fields; ``revenue``/``ii``/``nii`` are ANNUAL totals split evenly."""
    overrides = overrides or {}
    rows = []
    for i, date in enumerate(_DATES):
        row = dict(hcd._QUARTER, date=date, revenue=revenue / 4,
                   interestIncome=(ii / 4 if isinstance(ii, (int, float))
                                   and not isinstance(ii, bool) else ii),
                   netInterestIncome=(nii / 4 if isinstance(nii, (int, float))
                                      and not isinstance(nii, bool) else nii))
        row.update(overrides.get(i, {}))
        rows.append(row)
    return rows


def _lender(row: Any) -> Optional[bool]:
    return gate.lender_verdict(row)[0]


# Latest-annual shapes from the probe ($ figures rounded; the SHARES are the probe's).
# The listed payment networks (`gate.PAYMENT_NETWORKS`): interest income share < 0.25.
_NETWORKS = {
    "V": (40.0e9, 0.0, -0.40e9),       # 0.00 | -0.01, negative net interest income
    "MA": (28.2e9, 0.0, -0.56e9),      # 0.00 | -0.02
    "PYPL": (31.8e9, 0.64e9, 0.0),     # 0.02 | 0.00
    "WU": (4.2e9, 0.0, -0.13e9),       # 0.00 | -0.03
    "GPN": (10.1e9, 0.20e9, -0.61e9),  # 0.02 | -0.06
}
# Lenders FMP did NOT zero-fill: share >= 0.25 (the sanity check alone calls them lenders).
_LENDERS = {
    "AXP": (65.9e9, 21.1e9, 15.5e9),   # 0.32
    "AFRM": (2.3e9, 1.2e9, 0.9e9),     # 0.52
    "SOFI": (2.7e9, 1.9e9, 1.3e9),     # 0.71
    "SLM": (2.9e9, 2.44e9, 1.5e9),     # 0.84
    "COF": (39.1e9, 33.2e9, 30.0e9),   # 0.85
    "OMF": (5.0e9, 4.35e9, 3.0e9),     # 0.87
    "CACC": (2.2e9, 2.07e9, 1.7e9),    # 0.94
    "NAVI": (4.0e9, 3.84e9, 0.6e9),    # 0.96
    "ALLY": (8.2e9, 9.1e9, 6.0e9),     # 1.11 (interest income above net revenue)
    "SYF": (15.0e9, 17.7e9, 15.0e9),   # 1.18
}


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. The pure gate: the lender sanity check, the trailing quarters, applicability
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("ticker", sorted(_NETWORKS))
def test_the_listed_networks_pass_the_sanity_check(ticker):
    """Mutation: a share computed on the wrong field (or `>` vs `<`) vetoes V/MA/PYPL."""
    revenue, ii, nii = _NETWORKS[ticker]
    lender, why = gate.lender_verdict(_annual(revenue, ii, nii))
    assert lender is False, f"{ticker}: {why}"
    assert gate.payment_network_verdict(ticker, CREDIT, _annual(revenue, ii, nii)).is_network


@pytest.mark.parametrize("ticker", sorted(_LENDERS))
def test_the_probes_lenders_read_as_lenders(ticker):
    """Mutation: reading only `interestIncome`, or a threshold above 0.32, would let the
    sanity check pass AXP's data (it is not listed anyway: the list is what gates it)."""
    revenue, ii, nii = _LENDERS[ticker]
    lender, why = gate.lender_verdict(_annual(revenue, ii, nii))
    assert lender is True, f"{ticker}: {why}"
    assert not gate.payment_network_verdict(ticker, CREDIT, _annual(revenue, ii, nii)).is_network


@pytest.mark.parametrize("ii,nii,expected", [
    (25.0, 0.5, True),          # exactly 25% → a lender (>= is the rule)
    (24.999, 0.5, False),       # just under → not
    (0.0, 25.0, True),          # the net figure reaches the line on its own
    (1.0, 24.999, False),
    (25.0, -100.0, True),       # a negative twin does not cancel a lender's gross figure
])
def test_the_25_percent_boundary(ii, nii, expected):
    """Mutation: `>` instead of `>=` lets data at exactly a quarter of revenue through."""
    assert gate.LENDER_INTEREST_SHARE == 0.25
    assert _lender(_annual(100.0, ii, nii)) is expected


def test_a_zero_filled_interest_income_beside_a_large_net_figure_reads_as_a_lender():
    """FMP zero-fills `interestIncome` for some lenders and reports the spread as
    `netInterestIncome`. Mutation: reading `interestIncome` alone misses them."""
    assert gate.lender_verdict(_annual(10.0e9, 0, 8.5e9))[0] is True
    # ... and ONE readable field at the line is enough even when its twin is junk.
    assert _lender(_annual(10.0e9, "n/a", 8.5e9)) is True
    assert _lender(_annual(10.0e9, 8.5e9, None)) is True
    assert _lender(_annual(10.0e9, -1.0e9, 8.5e9)) is True    # negative gross, net reads


@pytest.mark.parametrize("row,why", [
    (None, "no income row"),
    ([], "a list is not a row"),
    ("V", "a string is not a row"),
    ({}, "empty row"),
    (_annual(None, 0.0, -1e9), "revenue missing"),
    (_annual(0.0, 0.0, -1e9), "revenue zero"),
    (_annual(-5e9, 0.0, -1e9), "revenue negative"),
    (_annual(float("nan"), 0.0, -1e9), "revenue NaN"),
    (_annual(float("inf"), 0.0, -1e9), "revenue inf"),
    (_annual(True, 0.0, -1e9), "revenue bool"),
    (_annual("40000000000", 0.0, -1e9), "revenue string"),
    ({"revenue": 40e9, "interestIncome": 0.0}, "netInterestIncome missing"),
    ({"revenue": 40e9, "netInterestIncome": -1e9}, "interestIncome missing"),
    (_annual(40e9, float("nan"), -1e9), "interestIncome NaN"),
    (_annual(40e9, 0.0, float("-inf")), "netInterestIncome -inf"),
    (_annual(40e9, False, -1e9), "interestIncome bool"),
    (_annual(40e9, 0.0, "-590000000"), "netInterestIncome string"),
    (_annual(40e9, -21.1e9, -26.7e9), "interestIncome NEGATIVE — a sign flip (LND-3)"),
    (_annual(40e9, -1.0, 1e9), "interestIncome negative, net below the line"),
    (_annual(10 ** 400, 0.0, -1e9), "revenue an int too large for a float"),
    (_annual(40e9, 10 ** 400, -1e9), "interestIncome an int too large for a float"),
    (_annual(40e9, 0.0, 0.0), "both exactly zero — FMP's zero-fill of an unparsed statement"),
    (_annual(40e9, 0, 0), "both exactly zero (ints)"),
])
def test_an_unreadable_share_is_unknown(row, why):
    """Unknown is neither verdict: a LISTED network keeps its rows (the list is curated,
    the data only vetoes), every unlisted member stays gated. Mutation: treating a
    missing / junk / zero-filled / negative field as a reading turns an unparsed statement
    into a verdict (LND-3: a sign-flipped lender read "not a lender")."""
    lender, reason = gate.lender_verdict(row)
    assert lender is None, f"{why}: {reason}"
    assert isinstance(reason, str) and reason
    assert gate.payment_network_verdict("V", CREDIT, row).is_network is True, why
    assert gate.payment_network_verdict("ENVA", CREDIT, row).is_network is False, why


@pytest.mark.parametrize("revenue,ii,nii,expected", [
    (1e15, 1e14, -1e12, False),        # huge magnitudes, 10%
    (1e15, 2.5e14, 0.0, True),
    (1e-6, 1.0, 0.5, True),            # near-zero revenue: any interest dominates
    (1e-6, 0.0, -1.0, False),          # near-zero revenue, debt only
    (5e9, -2e9, -3e9, None),           # negative interest income: unreadable (LND-3)
    (5e9, -2e9, 2e9, True),            # ... but the net figure still reads
    (1e-300, 1e300, 0.0, True),        # the share overflows to inf: still a verdict
])
def test_extreme_magnitudes_never_raise(revenue, ii, nii, expected):
    """Mutation: an `abs()` on the share would make a large NEGATIVE net interest
    (interest PAID on debt) look like a lender's income."""
    assert _lender(_annual(revenue, ii, nii)) is expected


def test_the_verdict_reason_names_both_shares():
    """The WARNING on a vetoed network quotes these: both shares, three decimals."""
    lender, why = gate.lender_verdict(_annual(*_NETWORKS["V"]))
    assert lender is False
    assert "interestIncome/revenue 0.000" in why and "netInterestIncome/revenue -0.010" in why
    assert "< 0.25" in why
    lender, why = gate.lender_verdict(_annual(*_LENDERS["AXP"]))
    assert lender is True
    assert "interestIncome/revenue 0.320" in why and "netInterestIncome/revenue 0.235" in why


# ── the trailing four quarters ──────────────────────────────────────────────────────


def test_trailing_interest_row_sums_four_quarters():
    row = gate.trailing_interest_row(_quarters(40e9, 0.0, -0.6e9))
    assert row["revenue"] == pytest.approx(40e9)
    assert row["interestIncome"] == 0.0
    assert row["netInterestIncome"] == pytest.approx(-0.6e9)
    assert row["date"] == "2026-06-30" and row["period"] == "TTM"
    assert _lender(row) is False


def test_trailing_interest_row_sorts_dedupes_and_takes_the_newest_four():
    """Mutation: summing FIVE quarters, or a duplicated filing twice, skews the share
    only mildly — but taking the OLDEST four would read a stale year. Here the newest
    quarter is the lender-shaped one, so a wrong pick misses the veto."""
    rows = _quarters(4e9, 0.0, -0.1e9)
    newest = dict(rows[0], date="2026-09-30", revenue=1e9, interestIncome=4e9)
    shuffled = [rows[2], newest, rows[0], dict(rows[0]), rows[3], rows[1]]
    row = gate.trailing_interest_row(shuffled)
    assert row["date"] == "2026-09-30"
    # newest four = 2026-09-30, 06-30, 03-31, 2025-12-31 (2025-09-30 dropped)
    assert row["revenue"] == pytest.approx(1e9 + 3 * 1e9)
    assert row["interestIncome"] == pytest.approx(4e9)
    assert _lender(row) is True


@pytest.mark.parametrize("quarters", [
    None, {}, "rows", [],
    _quarters(4e9, 0.0, -0.1e9)[:3],                                   # three quarters
    [dict(r, date=_DATES[0]) for r in _quarters(4e9, 0.0, -0.1e9)],    # one date x4
    [dict(r, date=None) for r in _quarters(4e9, 0.0, -0.1e9)],          # undated
    [*_quarters(4e9, 0.0, -0.1e9)[:3], "junk"],
])
def test_fewer_than_four_dated_quarters_is_no_row(quarters):
    """Mutation: a part-year sum (the bug `_sum_ttm_income` was fixed for)."""
    assert gate.trailing_interest_row(quarters) is None
    assert _lender(gate.trailing_interest_row(quarters)) is None


@pytest.mark.parametrize("bad", [None, float("nan"), True, "0"])
def test_a_field_missing_in_one_quarter_is_left_out_of_the_sum(bad):
    """Mutation: a partial sum of `netInterestIncome` (three quarters) silently reads as
    the whole year; leaving it out makes the verdict unknown."""
    rows = _quarters(40e9, 0.0, -0.6e9, overrides={2: {"netInterestIncome": bad}})
    row = gate.trailing_interest_row(rows)
    assert "netInterestIncome" not in row and row["interestIncome"] == 0.0
    assert _lender(row) is None


# ── applicability and comparability ────────────────────────────────────────────────


@pytest.mark.parametrize("industry,network,kept", [
    (CREDIT, True, _GATED),
    ("Financial—Credit Services", True, _GATED),        # the older feed's em dash
    ("  financial - credit  services ", True, _GATED),
    (CREDIT, False, set()),
    (CREDIT, 1, set()),                                 # truthy junk is not `True`
    (CREDIT, "yes", set()),
    (CREDIT, None, set()),
    ("Banks - Regional", True, set()),                  # NOT mixed: network ignored
    ("Insurance - Life", True, set()),
    ("Financial - Mortgages", True, set()),
    ("Insurance - Brokers", True, {"interest_coverage"}),
    ("Insurance - Brokers", False, {"interest_coverage"}),
    ("Software - Infrastructure", False, _GATED),       # NOT mixed: network ignored
    ("Financial - Data & Stock Exchanges", False, _GATED),
    ("", False, _GATED), (None, True, _GATED),
])
def test_company_metric_applicable(industry, network, kept):
    """Mutation: honouring `network=True` outside the mixed set would hand a bank its
    meaningless current ratio back; `bool(network)` instead of `network is True` would free
    a member on a truthy non-verdict."""
    for metric in _GATED:
        assert gate.company_metric_applicable(
            metric, industry, network=network, ticker="V") is (metric in kept), (
            industry, network, metric)
    assert gate.company_metric_applicable(
        "debt_to_equity", industry, network=False, ticker="V") is True
    assert hc.omitted_financial_rows(industry, network=network, ticker="V") == frozenset(
        _GATED - kept)


@pytest.mark.parametrize("industry,network,comparable", [
    (CREDIT, False, {"debt_to_equity"}),                # a lender: D/E still compared
    (CREDIT, True, set()),                              # a network: none of the four (nor any)
    ("Financial—Credit Services", True, set()),
    (CREDIT, 1, {"debt_to_equity"}),                    # truthy junk is not `True`
    ("Banks - Diversified", True, {"debt_to_equity"}),  # not mixed: network ignored
    ("Asset Management", False, {"debt_to_equity"}),
    ("Insurance - Brokers", False, {"interest_coverage", "debt_to_equity"}),
    ("Financial - Data & Stock Exchanges", True, _ABSOLUTE),
    ("Software - Infrastructure", False, _ABSOLUTE),
    ("", True, _ABSOLUTE), (None, False, _ABSOLUTE), (3, True, _ABSOLUTE),
])
def test_peer_median_comparable(industry, network, comparable):
    """Mutation: answering True for the mixed industry compares V with a lender median;
    dropping the D/E branch compares V's 0.55 leverage with lenders' (LND-6)."""
    for metric in _ABSOLUTE:
        assert gate.peer_median_comparable(metric, industry, network=network) is (
            metric in comparable), (industry, network, metric)
    # 2026-10-09 (NET-4): every other metric of a listed member of the mixed industry is
    # peer-free too; a lender, junk verdicts and every other industry compare it.
    peer_free = network is True and gate.is_mixed_lender_industry(industry)
    for metric in ("pe_ratio", "roe", "altman_z_score"):
        assert gate.peer_median_comparable(metric, industry, network=network) is (
            not peer_free), (industry, network, metric)
    # the default is the fail-closed "not a network"
    assert gate.peer_median_comparable("debt_to_equity", CREDIT) is True


def test_the_mixed_industries_stay_gated_at_the_industry_level():
    """The industry-level answer for a mixed industry must stay "gated": the producer
    keeps its values out of the Financial Services sector pool and every industry-only
    caller (the report vital, the benchmark pool) keeps its rule. Mutation: removing
    Credit Services from the gated sets instead of adding the company check."""
    assert gate._MIXED_LENDER_INDUSTRIES
    assert gate._MIXED_LENDER_INDUSTRIES <= gate._NO_LIQUIDITY_INDUSTRIES
    assert gate._MIXED_LENDER_INDUSTRIES <= gate._NO_COVERAGE_INDUSTRIES
    for metric in _GATED:
        assert gate.peer_metric_applicable(metric, CREDIT) is False
    assert hc.omitted_financial_rows(CREDIT, ticker=None) == frozenset(_GATED)   # no verdict
    # and every mixed name is a real FMP industry (a typo would free nobody)
    import test_health_check_2026_10_07 as h07
    known = {gate.industry_key(i) for i in h07._universe_industries()}
    assert gate._MIXED_LENDER_INDUSTRIES <= known


def test_the_benchmark_producer_still_leaves_credit_services_out_of_the_sector_pool():
    """`industry_benchmark_service` reads the INDUSTRY-level answer: a network's values sit
    in the same industry rows as the lenders', so the pool rule does not change."""
    acc: Dict[Any, List[float]] = defaultdict(list)
    left_out: Dict[str, set] = defaultdict(set)
    IndustryBenchmarkService._pool_into_sector(
        acc, {"current_ratio": [1.1, 0.4], "debt_to_equity": [0.6]}, CREDIT, left_out)
    assert "current_ratio" not in acc and acc["debt_to_equity"] == [0.6]
    assert left_out["current_ratio"] == {CREDIT}


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. The Health Check (Financials tab) — the REAL build, stubbed FMP + lookup
# ══════════════════════════════════════════════════════════════════════════════════════


class _RecordingLookup:
    """`get_current_benchmarks` with an INDUSTRY cell for every metric (a lender-pooled
    Credit Services median); records what the build asked for."""

    def __init__(self, table: Dict[str, float], level: str = "industry") -> None:
        self.table, self.level = table, level
        self.asked: List[List[str]] = []

    def get_current_benchmarks(self, industry, sector, metrics):
        self.asked.append(list(metrics))
        return {m: ({"value": self.table[m], "level": self.level,
                     "peer_group_name": "Credit Services", "n": 60}
                    if m in self.table else None) for m in metrics}


# A lender-pooled Credit Services median: tiny current / quick ratios, ~1.5x coverage,
# lenders' structurally high leverage.
_POOLED = {"debt_to_equity": 1.8, "pe_ratio": 14.0, "roe": 0.16,
           "current_ratio": 0.5, "quick_ratio": 0.4, "interest_coverage": 1.5}
# V-shaped TTM ratios: CR 1.08 and QR 1.0 read NEUTRAL on the absolute bands, but
# "+116% / +150% above the industry" against the pooled median; D/E 0.55 reads NEUTRAL on
# the absolute bands but "69% below the industry" (a pass) against lenders' 1.8.
_V_RATIOS = dict(hcd._RATIOS, currentRatioTTM=1.08, quickRatioTTM=1.0,
                 interestCoverageRatioTTM=40.0, debtToEquityRatioTTM=0.55,
                 priceToEarningsRatioTTM=31.0)


async def _hc(monkeypatch, *, industry=CREDIT, income=None, ratios=None,
              lookup: Optional[_RecordingLookup] = None, sector=FS, symbol="V"):
    lookup = lookup or _RecordingLookup(_POOLED)
    monkeypatch.setattr(hc, "get_sector_benchmark_lookup", lambda: lookup)
    hc._cache.clear()
    hc._inflight.clear()
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = None
    svc.fmp = hcd._FakeFMP(hcd._answers(
        profile=dict(hcd._PROFILE, symbol=symbol, sector=sector, industry=industry),
        ratios=[dict(_V_RATIOS if ratios is None else ratios)],
        income=_quarters(*_NETWORKS["V"]) if income is None else income,
    ))
    response, _next = await svc._build_health_check(symbol)
    return response, lookup


def _by(resp) -> Dict[str, Any]:
    return {m.type: m for m in resp.metrics}


def _counts_consistent(resp: HealthCheckResponse) -> None:
    passed = sum(1 for m in resp.metrics if m.status == "positive")
    neutrals = sum(1 for m in resp.metrics if m.status == "neutral")
    assert resp.passed_count == passed and resp.total_count == len(resp.metrics)
    assert resp.overall_rating == hc._overall_rating(passed + 0.5 * neutrals, len(resp.metrics))


def _gate_logs(caplog) -> str:
    return " ".join(r.getMessage() for r in caplog.records if "[network-gate]" in r.getMessage())


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker", sorted(set(_NETWORKS) - {"WU"}))
async def test_a_network_keeps_three_rows_on_absolute_bands(monkeypatch, caplog, ticker):
    """Mutation (a): dropping the company check → the rows vanish (the old behaviour).
    Mutation (b): asking the lookup for them → CR 1.08 reads "positive, +116% above the
    industry" against a median that pools lenders' 0.5; D/E 0.55 "beats" lenders' 1.8.
    2026-10-09 (NET-4): P/E and ROE are absolute too, and NO lookup is made. WU's three
    rows are curated-withheld (its own test)."""
    with caplog.at_level(logging.INFO):
        resp, lookup = await _hc(monkeypatch, income=_quarters(*_NETWORKS[ticker]),
                                 symbol=ticker)
    by = _by(resp)
    assert _GATED <= set(by), sorted(by)
    for metric in _ABSOLUTE:
        row = by[metric]
        assert row.comparison_value is None and row.percent_difference is None, metric
        assert row.peer_level is None, metric
        assert row.status == hc._absolute_status(metric, row.value), metric
        text = hcd._rendered(row)
        assert "industry" not in text and "sector" not in text, text
    assert by["current_ratio"].status == "neutral" and by["quick_ratio"].status == "neutral"
    assert by["interest_coverage"].status == "positive"
    assert by["debt_to_equity"].status == "neutral"         # 0.55: the absolute band
    # P/E and ROE are judged on the absolute bands too (2026-10-09, NET-4).
    for metric in ("pe_ratio", "roe"):
        assert by[metric].comparison_value is None and by[metric].peer_level is None, metric
        assert by[metric].status == hc._absolute_status(metric, by[metric].value), metric
    assert "altman_z_score" not in by, "the Financial Services Altman gate is unchanged"
    # Nothing is left to compare: the lookup is never called.
    assert lookup.asked == [], lookup.asked
    assert resp.degraded == []
    _counts_consistent(resp)
    assert resp.total_count == 6
    logs = _gate_logs(caplog)
    assert f"ticker={ticker}" in logs and "a listed payment network" in logs
    assert "step=health_check" in logs


@pytest.mark.asyncio
async def test_a_network_is_never_compared_even_with_a_sector_cell(monkeypatch):
    """Even a SECTOR-level answer for the four (the bank-pooled FS aggregate) is not
    compared: the median is never asked for in the first place."""
    resp, _ = await _hc(monkeypatch, lookup=_RecordingLookup(_POOLED, level="sector"))
    for metric in _ABSOLUTE:
        assert _by(resp)[metric].comparison_value is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker", ["AXP", "COF", "SYF", "ALLY", "SOFI"])
async def test_a_lender_stays_gated(monkeypatch, ticker):
    """Mutation: freeing a company on its data instead of the list gives AXP a "current
    ratio" of a balance sheet with no current/non-current split. Its D/E is still compared
    with the industry (a lender among lenders)."""
    resp, lookup = await _hc(monkeypatch, income=_quarters(*_LENDERS[ticker]), symbol=ticker)
    by = _by(resp)
    assert not (set(by) & _GATED)
    assert all(not (set(a) & _GATED) for a in lookup.asked)
    assert by["debt_to_equity"].comparison_value == 1.8
    assert by["debt_to_equity"].peer_level == "industry"
    assert resp.total_count == 3 and resp.degraded == []


@pytest.mark.asyncio
async def test_a_listed_network_with_lender_data_is_gated_with_a_warning(monkeypatch, caplog):
    """The sanity check vetoes the list: V whose FMP income reads as a lender's (a
    zero-filled gross figure beside an 85% net one) is gated, D/E compared as a lender's,
    and a WARNING names the ticker and both shares. Mutation: trusting the list alone."""
    with caplog.at_level(logging.WARNING):
        resp, _ = await _hc(monkeypatch, income=_quarters(10.0e9, 0.0, 8.5e9))
    by = _by(resp)
    assert not (set(by) & _GATED)
    assert by["debt_to_equity"].peer_level == "industry"
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING and "[network-gate]" in r.getMessage()]
    assert len(warnings) == 1, warnings
    assert "ticker=V" in warnings[0] and "reads as a lender's" in warnings[0]
    assert "interestIncome/revenue 0.000" in warnings[0]
    assert "netInterestIncome/revenue 0.850" in warnings[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("label,income", [
    ("fields absent", hcd._income()),                              # no interest fields
    ("three quarters", _quarters(*_NETWORKS["V"])[:3]),
    ("zero-filled pair", _quarters(40e9, 0.0, 0.0)),
    ("NaN net interest", _quarters(40e9, 0.0, -0.6e9,
                                   overrides={1: {"netInterestIncome": float("nan")}})),
    ("non-positive revenue", _quarters(-4e9, 0.0, -0.6e9)),
    ("not a list", {"error": "Limit Reach"}),
])
async def test_unreadable_income_leaves_the_list_standing(monkeypatch, caplog, label, income):
    """A listed network with unreadable interest data keeps its rows (the list is
    curated; the data can only veto), and the log says why; an UNLISTED member with the
    same data stays gated. Mutation: an unknown verdict vetoing the list drops V's rows
    every time FMP leaves its interest lines out."""
    with caplog.at_level(logging.INFO):
        resp, _ = await _hc(monkeypatch, income=income)
    assert _GATED <= set(_by(resp)), label
    assert "interest data unreadable" in _gate_logs(caplog), label
    resp, _ = await _hc(monkeypatch, income=income, symbol="ENVA")
    assert not (set(_by(resp)) & _GATED), label


@pytest.mark.asyncio
async def test_a_failed_income_leg_keeps_a_network_and_is_never_persisted(monkeypatch):
    """The 429'd leg leaves the list standing (V keeps its rows); the build is degraded
    (served from memory, never written for 24 h). Mutation: an unknown verdict gating."""
    from app.integrations.fmp import FMPRateLimitException

    resp, _ = await _hc(monkeypatch, income=FMPRateLimitException("429"))
    assert _GATED <= set(_by(resp))
    assert "income" in resp.degraded


@pytest.mark.asyncio
@pytest.mark.parametrize("revenue,ii,gated", [(100e9, 25e9, True), (100e9, 24.9e9, False)])
async def test_the_veto_boundary_through_the_build(monkeypatch, revenue, ii, gated):
    resp, _ = await _hc(monkeypatch, income=_quarters(revenue, ii, 1e9))
    assert (not (set(_by(resp)) & _GATED)) is gated


@pytest.mark.asyncio
@pytest.mark.parametrize("industry,expected", [
    ("Banks - Regional", set()),
    ("Banks - Diversified", set()),
    ("Insurance - Property & Casualty", set()),
    ("Financial - Mortgages", set()),
    ("Asset Management", set()),
    ("Insurance - Brokers", {"interest_coverage"}),
])
async def test_banks_and_insurers_are_unchanged_by_a_listed_ticker(monkeypatch,
                                                                  industry, expected):
    """A company filed under a bank / insurer industry is a bank even if its ticker is on
    the list and its interest lines look like a network's. Mutation: applying the list
    outside the mixed industry hands C / BAC their meaningless current ratio back, or
    takes their D/E comparison away."""
    resp, lookup = await _hc(monkeypatch, industry=industry)
    assert set(_by(resp)) & _GATED == expected
    assert _by(resp)["debt_to_equity"].comparison_value == 1.8
    if expected:   # the broker's coverage is still compared with its own industry
        ic = _by(resp)["interest_coverage"]
        assert ic.comparison_value == 1.5 and ic.peer_level == "industry"


@pytest.mark.asyncio
async def test_an_operating_company_still_compares_its_liquidity_rows(monkeypatch):
    resp, lookup = await _hc(monkeypatch, industry="Software - Infrastructure",
                             sector="Technology")
    for metric in _ABSOLUTE:
        assert _by(resp)[metric].comparison_value is not None
        assert _by(resp)[metric].peer_level == "industry"
    assert any(set(a) >= _ABSOLUTE for a in lookup.asked)


@pytest.mark.asyncio
async def test_the_network_build_round_trips_through_the_v9_cache_shape(monkeypatch):
    """What the writer stores is what the reader admits (the absolute-only rows included);
    a v8 row (P/E and ROE compared with lenders), a v7 row (the first cut) and a v6 row of
    the same company are rebuilt."""
    resp, _ = await _hc(monkeypatch)
    stored = {**resp.model_dump(), "payload_version": hc._HC_PAYLOAD_VERSION}
    json.dumps(stored, allow_nan=False)
    assert hc._HC_PAYLOAD_VERSION == 9

    class _Rows:
        def __init__(self, rows):
            self.rows = rows

        def table(self, name):
            return self

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            from types import SimpleNamespace
            return SimpleNamespace(data=self.rows)

    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    row = {"cached_at": datetime.now(timezone.utc).isoformat(), "next_earnings_date": None}
    svc.supabase = _Rows([dict(row, response_json=stored)])
    assert svc._check_supabase_cache("V") == resp
    for old in (6, 7, 8):
        svc.supabase = _Rows([dict(row, response_json={**stored, "payload_version": old})])
        assert svc._check_supabase_cache("V") is None, f"a v{old} row was served"


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. The Financial Health card — both paths
# ══════════════════════════════════════════════════════════════════════════════════════


_V_PROFILE = {"symbol": "V", "sector": FS, "industry": CREDIT, "mktCap": 6.3e11}


@pytest.mark.asyncio
async def test_the_network_card_is_rated_again_on_the_main_path(monkeypatch):
    """With four scored rows (D/E + the three, all on absolute bands) the card is RATED
    (>= 2 scored rows) — the D/E-only card was unrated. Mutation: the snapshot
    re-attaching a peer median, or the card staying unrated."""
    health, _ = await _hc(monkeypatch)
    _hs, svc = sph._health_service(monkeypatch, health, profile=_V_PROFILE,
                                   income=_quarters(*_NETWORKS["V"]))
    snap, degraded = await svc._compute_with_status("V")
    assert degraded == []
    by = {m.metric_key: m for m in snap.metrics}
    assert set(by) == {"debt_to_equity", "current_ratio", "interest_coverage", "quick_ratio"}
    for key in _ABSOLUTE:
        assert by[key].score is not None and by[key].peer_level is None, key
        assert "sector" not in by[key].name, by[key].name
    assert by["debt_to_equity"].name == "Debt-to-Equity"
    assert snap.rating > 0 and snap.weighted_score is not None
    json.dumps(snap.model_dump(), allow_nan=False)


@pytest.mark.asyncio
async def test_the_lender_card_stays_unrated_on_the_main_path(monkeypatch):
    health, _ = await _hc(monkeypatch, income=_quarters(*_LENDERS["AXP"]), symbol="AXP")
    _hs, svc = sph._health_service(monkeypatch, health, profile=dict(_V_PROFILE, symbol="AXP"))
    snap, _ = await svc._compute_with_status("AXP")
    assert [m.metric_key for m in snap.metrics] == ["debt_to_equity"]
    assert snap.metrics[0].peer_level == "industry"           # a lender among lenders
    assert snap.rating == 0 and snap.weighted_score is None


_FB_BENCH = {"debt_to_equity": (1.8, "industry"), "current_ratio": (0.5, "industry"),
             "quick_ratio": (0.4, "industry"), "interest_coverage": (1.5, "industry")}
# V-shaped balance sheet for the local fallback: CR 1.08, QR 0.9, D/E 0.55.
_V_BS = {"totalAssets": 95e9, "totalLiabilities": 57e9, "totalCurrentAssets": 37.8e9,
         "totalCurrentLiabilities": 35e9, "retainedEarnings": 20e9,
         "totalStockholdersEquity": 38e9, "totalDebt": 20.9e9,
         "cashAndCashEquivalents": 17.5e9, "netReceivables": 14e9}


@pytest.mark.asyncio
async def test_the_fallback_keeps_the_networks_rows_on_absolute_bands(monkeypatch, caplog):
    """The Health Check raised; the local fallback must show the same rows as the main
    path, scored on the Health Check's absolute bands, never against the pooled medians.
    Mutation: `_fallback_sector_score` against the lender medians scores CR 1.08 a "beat"
    (4) with a "(vs sector 0.50)" label, and D/E 0.55 a "beat" against lenders' 1.80."""
    with caplog.at_level(logging.INFO):     # hs (the fallback) + the gate's resolver
        _hs, svc = sph._health_service(
            monkeypatch, RuntimeError("health check exploded"), profile=_V_PROFILE,
            bs=dict(_V_BS), income=_quarters(*_NETWORKS["V"]), bench=_FB_BENCH)
        snap, degraded = await svc._compute_with_status("V")
    assert degraded == ["health_check"]
    by = {m.metric_key: m for m in snap.metrics}
    assert set(by) == {"debt_to_equity", "current_ratio", "interest_coverage", "quick_ratio"}
    for key in _ABSOLUTE:
        row = by[key]
        assert row.peer_level is None and "vs sector" not in row.name, row.name
        value = float(row.value)
        assert row.score == hs._status_score(hc._absolute_status(key, value)), key
    assert by["current_ratio"].value == "1.08" and by["current_ratio"].score == 3   # neutral
    assert by["debt_to_equity"].name == "Debt-to-Equity"
    assert by["debt_to_equity"].value == "0.55" and by["debt_to_equity"].score == 3
    assert snap.rating > 0 and snap.weighted_score is not None
    logs = " ".join(r.getMessage() for r in caplog.records)
    # 2026-10-09: nothing is comparable, so the fallback makes no lookup at all.
    assert "no benchmark lookup" in logs and "step=health_snapshot_fallback" in logs


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker,label,income", [
    ("V", "lender-shaped data vetoes the list", _quarters(*_LENDERS["COF"])),
    ("COF", "a lender", _quarters(*_LENDERS["COF"])),
    ("ENVA", "unlisted, network-shaped data", _quarters(2.6e9, 0.0, -0.13e9)),
    ("ENVA", "unlisted, unknown data", hcd._income()),
])
async def test_the_fallback_gates_every_member_but_a_network(monkeypatch, ticker, label,
                                                             income):
    """Mutation: the fallback deciding on the data alone (or ignoring the veto) shows
    a lender the three rows. The gated card keeps D/E compared with its industry."""
    _hs, svc = sph._health_service(
        monkeypatch, RuntimeError("health check exploded"),
        profile=dict(_V_PROFILE, symbol=ticker), bs=dict(_V_BS), income=income,
        bench=_FB_BENCH)
    snap, _ = await svc._compute_with_status(ticker)
    assert [m.metric_key for m in snap.metrics] == ["debt_to_equity"], label
    assert snap.metrics[0].peer_level == "industry", label
    assert snap.rating == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("label,income", [
    ("unknown data", hcd._income()),
    ("short history", _quarters(*_NETWORKS["V"])[:2]),
])
async def test_the_fallback_keeps_a_network_with_unreadable_income(monkeypatch, label, income):
    """The list stands when the data cannot be read (the same rule as the main path)."""
    _hs, svc = sph._health_service(
        monkeypatch, RuntimeError("health check exploded"), profile=_V_PROFILE,
        bs=dict(_V_BS), income=income, bench=_FB_BENCH)
    snap, _ = await svc._compute_with_status("V")
    assert {m.metric_key for m in snap.metrics} == _ABSOLUTE, label
    assert snap.rating > 0


@pytest.mark.asyncio
async def test_the_fallback_still_compares_an_operating_company(monkeypatch):
    """Control: the absolute-band scoring is ONLY for rows that are never compared."""
    _hs, svc = sph._health_service(
        monkeypatch, RuntimeError("health check exploded"),
        bs=dict(_V_BS), bench=_FB_BENCH)
    snap, _ = await svc._compute_with_status("TEST")
    by = {m.metric_key: m for m in snap.metrics}
    assert by["current_ratio"].name == "Current Ratio (vs sector 0.50)"
    assert by["current_ratio"].score == 4 and by["current_ratio"].peer_level == "industry"
    assert by["debt_to_equity"].name == "Debt-to-Equity (vs sector 1.80)"
    assert by["debt_to_equity"].score == 4 and by["debt_to_equity"].peer_level == "industry"


def test_the_snapshot_payload_version_is_8_and_older_rows_rebuild():
    """A v5 row of V is the unrated D/E-only card; a v6 row may be the first cut's (an
    unlisted lender with the three rows, or V's D/E compared with lenders); a v7 row of WU
    may carry its made-up rows (2026-10-09). All rebuild. Mutation: leaving the version
    at 7 serves WU's."""
    assert hs._SNAPSHOT_PAYLOAD_VERSION == 8
    body = SnapshotItemResponse(category="Financial Health", rating=0, metrics=[],
                                computed_at="2026-10-08T09:15:00Z").model_dump()
    svc = hs.HealthSnapshotService.__new__(hs.HealthSnapshotService)
    for old in (5, 6, 7):
        svc.supabase = sph._Supabase([sph._row({**body, hs._VERSION_KEY: old})[0]])
        assert svc._check_supabase_cache("V") is None, f"a v{old} row was served"
    svc.supabase = sph._Supabase([sph._row({**body, hs._VERSION_KEY: 8})[0]])
    assert svc._check_supabase_cache("V") is not None


# ── the Overview's degraded fallback card ───────────────────────────────────────────


def _ov_names(industry: str, inc: Dict[str, Any], sector: str = FS,
              ticker: Optional[str] = "V", quarters: Any = None) -> List[str]:
    card = StockOverviewService.__new__(StockOverviewService)._build_health_snapshot(
        dict(_V_BS), inc, {"freeCashFlow": 5e9}, {"interestCoverage": 40.0,
                                                  "assetTurnover": 0.4},
        {}, 6.3e11, sector=sector, industry=industry, ticker=ticker,
        income_quarterly=quarters,
    )
    return [m.name for m in card.metrics]


@pytest.mark.parametrize("ticker,inc,quarters,kept", [
    ("V", _annual(*_NETWORKS["V"]), _quarters(*_NETWORKS["V"]), True),
    ("MA", _annual(*_NETWORKS["MA"]), _quarters(*_NETWORKS["MA"]), True),
    ("gpn", _annual(*_NETWORKS["GPN"]), None, True),           # normalised; no quarters
    ("V", {}, None, True),                                     # unknown data: list stands
    ("V", _annual(*_NETWORKS["V"]), _quarters(10e9, 0.0, 8.5e9), False),  # quarters veto
    # 2026-10-09: the ANNUAL row is no longer the verdict's input (one input everywhere:
    # the trailing quarters) — lender-shaped annual data alone leaves the list standing.
    ("V", _annual(10e9, 0.0, 8.5e9), None, True),
    ("AXP", _annual(*_LENDERS["AXP"]), None, False),
    ("COF", _annual(10e9, 0.0, 8.5e9), None, False),           # zero-filled lender
    ("ENVA", _annual(2.6e9, 0.0, -0.13e9), None, False),       # network-shaped, unlisted
    (None, _annual(*_NETWORKS["V"]), None, False),             # no ticker: fail closed
    ("", _annual(*_NETWORKS["V"]), None, False),
    ("TREE", {}, None, True),                                  # a listed fee business
    ("WU", _annual(*_NETWORKS["WU"]), _quarters(*_NETWORKS["WU"]), False),  # curated
])
def test_the_overview_fallback_card_follows_the_list(ticker, inc, quarters, kept):
    """Mutation: the industry-only `interest_coverage_applicable` (V loses the row), the
    data-only verdict (ENVA gets it), a missing ticker treated as a network, the annual
    row as the verdict's input again, or WU's curated row shown."""
    assert ("Interest Coverage" in _ov_names(CREDIT, inc, ticker=ticker,
                                              quarters=quarters)) is kept


def test_the_overview_fallback_card_ignores_the_list_elsewhere():
    v_shaped = _annual(*_NETWORKS["V"])
    assert "Interest Coverage" not in _ov_names("Banks - Diversified", v_shaped)
    assert "Interest Coverage" in _ov_names("Insurance - Brokers", _annual(*_LENDERS["AXP"]),
                                            ticker="AXP")
    assert "Interest Coverage" in _ov_names("Software - Application", {}, "Technology",
                                            ticker=None)


# ══════════════════════════════════════════════════════════════════════════════════════
# 4. The paid report — drill-down lines and the health vital
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("metric,level,industry,network,expected", [
    ("current_ratio", "industry", CREDIT, True, "mixed_industry"),
    ("current_ratio", "sector", CREDIT, False, "mixed_industry"),
    ("quick_ratio", None, CREDIT, True, "mixed_industry"),
    ("interest_coverage", "industry", "Financial—Credit Services", False, "mixed_industry"),
    ("debt_to_equity", "industry", CREDIT, True, "payment_network"),
    ("debt_to_equity", "sector", "Financial—Credit Services", True, "payment_network"),
    ("debt_to_equity", "industry", CREDIT, False, None),      # a lender's D/E line stays
    ("debt_to_equity", "industry", CREDIT, 1, None),          # truthy junk is not `True`
    # 2026-10-09 (NET-4): every other metric of a listed member is withheld too.
    ("pe_ratio", "sector", CREDIT, True, "payment_network"),
    ("roe", "industry", CREDIT, True, "payment_network"),
    ("pe_ratio", "sector", CREDIT, False, None),              # a lender keeps them
    ("roe", "industry", CREDIT, 1, None),
    # every other industry: unchanged, whatever `network` says
    ("debt_to_equity", "industry", "Banks - Regional", True, None),
    ("current_ratio", "industry", "Banks - Regional", True, "not_applicable"),
    ("interest_coverage", "industry", "Insurance - Brokers", False, None),
    ("current_ratio", "industry", "Financial - Data & Stock Exchanges", True, None),
    ("current_ratio", "sector", "Financial - Data & Stock Exchanges", False,
     "financial_services_sector"),
])
def test_withheld_reason_for_the_mixed_industry(metric, level, industry, network, expected):
    """Mutation: an industry-only check reports V's line as "not_applicable" (wrong
    reason); dropping the D/E branch draws a lender-pooled D/E line beside V's absolute
    row; honouring `network` outside the mixed industry withholds a bank's D/E line."""
    assert C._withheld_peer_line_reason(metric, level, industry, FS,
                                        network=network) == expected


def test_withheld_reason_default_is_not_a_network():
    """The four-argument call (no verdict) is the fail-closed one: no D/E withholding."""
    assert C._withheld_peer_line_reason("debt_to_equity", "industry", CREDIT, FS) is None
    assert C._withheld_peer_line_reason("current_ratio", "industry", CREDIT, FS) == (
        "mixed_industry")


def _stub_pass_two_legs(monkeypatch) -> None:
    """Every pass-2 leg but the sector history (hermetic: no universe, dossier, FRED,
    USPTO, Supabase)."""
    import app.services.industry_dossier_service as ids
    import app.services.ip_intel_service as ips

    class _NoIp:
        async def get_ip_intel(self, ticker, profile):
            return None

    class _NoDossier:
        async def get_or_compute_dossier_with_status(self, *, industry, sector):
            return None, False

    async def _no_aggregates(_sector):
        return None

    monkeypatch.setattr(ips, "get_ip_intel_service", lambda: _NoIp())
    monkeypatch.setattr(ids, "get_industry_dossier_service", lambda: _NoDossier())
    monkeypatch.setattr(C, "get_sector_aggregates", _no_aggregates)
    monkeypatch.setattr(C, "_industry_universe_peers", lambda industry, exclude: [])


def _pass_two_out(ticker: str, income_q: Any, industry: Any, sector: Any):
    out = C.CollectedTickerData(ticker=ticker, persona_key="warren_buffett")
    out.profile = {"sector": sector, "industry": industry}
    out.income_q = income_q
    return out


async def _history_for(monkeypatch, *, ticker, income_q, industry=CREDIT, sector=FS):
    """The REAL pass 2 (`_fetch_dependent`): the real `_fetch_sector_benchmark_history`
    over the round-4 lookup (industry lines + mature industry TTM cells for the four
    metrics), then the company verdict over `income_q`; every other leg stubbed."""
    series = r4._sector_lines(level="industry")
    ttm = {m: r4._cell(1.5, "industry", n=60) for m in _ABSOLUTE}
    lookup = r4._Lookup(series, ttm)
    monkeypatch.setattr("app.services.sector_benchmark_lookup.get_sector_benchmark_lookup",
                        lambda: lookup)
    _stub_pass_two_legs(monkeypatch)
    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    coll.fmp = None
    out = _pass_two_out(ticker, income_q, industry, sector)
    await coll._fetch_dependent(out)
    assert out.degraded_sections == []
    return out.sector_benchmark_history


class _CountingLookup(r4._Lookup):
    """The round-4 lookup, recording every read (period type) it answers."""

    def __init__(self, series, ttm):
        super().__init__(series, ttm)
        self.reads: List[str] = []

    def get_benchmark_series(self, industry, sector, metrics, period_type):
        self.reads.append(period_type)
        return super().get_benchmark_series(industry, sector, metrics, period_type)

    def get_benchmarks(self, industry, sector, metrics, period_type):
        self.reads.append(period_type)
        return super().get_benchmarks(industry, sector, metrics, period_type)


@pytest.mark.asyncio
async def test_the_drill_down_withholds_a_networks_four_lines(monkeypatch, caplog):
    """V's current-ratio, quick-ratio, interest-coverage AND D/E lines (and every other
    line, and their TTM points) are withheld — its cards compare no metric with the
    industry median. Since 2026-10-09 (spec D1) pass 2 asks the gate BEFORE the read, so
    for V the benchmark lookup is never even called (no failed read can degrade the
    build). Through the real pass 2. Mutation: the read made anyway (the lookup records
    it), or the verdict resolved after the read."""
    lookup = _CountingLookup(r4._sector_lines(level="industry"),
                             {m: r4._cell(1.5, "industry", n=60) for m in _ABSOLUTE})
    monkeypatch.setattr("app.services.sector_benchmark_lookup.get_sector_benchmark_lookup",
                        lambda: lookup)
    _stub_pass_two_legs(monkeypatch)
    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    coll.fmp = None
    out = _pass_two_out("V", _quarters(*_NETWORKS["V"]), CREDIT, FS)
    with caplog.at_level(logging.INFO):
        await coll._fetch_dependent(out)
    hist = out.sector_benchmark_history
    assert lookup.reads == []
    assert out.degraded_sections == []
    assert out.non_lender_member is True
    assert hist == {"annual": {}, "quarterly": {}, "levels": {"annual": {}, "quarterly": {}}}
    no_read = [r.getMessage() for r in caplog.records if "no peer read" in r.getMessage()]
    assert len(no_read) == 1 and "ticker=V" in no_read[0]
    assert not [r for r in caplog.records if "step=peer_line_gate" in r.getMessage()]
    assert "step=report_peer_lines" in _gate_logs(caplog)

    # Control: a lender of the same industry IS read (all three reads), the fetch's
    # industry gate withholds only the three liquidity / coverage lines, and its D/E
    # line stays (no company-level withholding).
    caplog.clear()
    lookup.reads.clear()
    out = _pass_two_out("COF", _quarters(*_LENDERS["COF"]), CREDIT, FS)
    with caplog.at_level(logging.INFO):
        await coll._fetch_dependent(out)
    assert sorted(lookup.reads) == sorted(["annual", C.CALENDAR_QUARTER_PERIOD_TYPE, "ttm"])
    assert out.non_lender_member is False and out.degraded_sections == []
    industry_gate = [r.getMessage() for r in caplog.records
                     if "step=peer_line_gate:" in r.getMessage()]
    assert len(industry_gate) == 1
    assert "annual:current_ratio[mixed_industry]" in industry_gate[0]
    assert "ttm:interest_coverage[mixed_industry]" in industry_gate[0]
    assert "debt_to_equity" not in industry_gate[0]      # the fetch knows no company
    assert not [r for r in caplog.records if "step=peer_line_gate_network" in r.getMessage()]
    assert not [r for r in caplog.records if "no peer read" in r.getMessage()]
    assert "debt_to_equity" in out.sector_benchmark_history["annual"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker,income_q", [
    ("ENVA", _quarters(2.6e9, 0.0, -0.13e9)),       # unlisted, network-shaped data
    ("COF", _quarters(*_LENDERS["COF"])),           # a lender
    ("V", _quarters(10.0e9, 0.0, 8.5e9)),           # listed, lender data vetoes
])
async def test_the_drill_down_keeps_a_gated_members_de_line(monkeypatch, ticker, income_q):
    """A gated member's card compares D/E with its industry, so the drill-down draws that
    line (the three liquidity lines stay withheld). Mutation: withholding every Credit
    Services D/E line, or reading the data instead of the list."""
    hist = await _history_for(monkeypatch, ticker=ticker, income_q=income_q)
    for metric in _GATED:
        assert metric not in hist["annual"], metric
    assert hist["annual"]["debt_to_equity"] == {"2024": 1.3, "2025": 1.4, r4._CUR: 1.5}
    assert hist["levels"]["annual"]["debt_to_equity"] == "industry"


@pytest.mark.asyncio
async def test_the_report_health_vital_is_measured_from_the_networks_card(monkeypatch):
    """The report's Financial Health vital reads the card's `weighted_score` (Altman Z is
    refused for Financial Services): the rated network card is MEASURED, not the
    "unmeasured" outcome of a gated, unrated card. Through the real `_compute_metrics` →
    `_build_sections` call site."""
    health, _ = await _hc(monkeypatch)
    _hs, svc = sph._health_service(monkeypatch, health, profile=_V_PROFILE)
    card, _ = await svc._compute_with_status("V")
    assert card.weighted_score is not None

    monkeypatch.setattr("app.services.sector_benchmark_lookup.get_sector_benchmark_lookup",
                        lambda: r4._Lookup({}, {}))
    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    out = r4._collected({"sector": FS, "industry": CREDIT}, de=0.55, fcf=2.0e10,
                        snap_health=card)
    coll._compute_metrics(out)
    assert out.computed["altman_z"] is None
    coll._build_sections(out)
    vital = out.financial_health_vital
    assert vital["score"]["value"] == round(C._card_weighted_to_score10(card.weighted_score), 1)
    assert vital["score"]["status"] != "unmeasured"

    # Control: a gated member (a lender, or V vetoed by its data) — unrated → unmeasured.
    unrated = SnapshotItemResponse(category="Financial Health", rating=0, weighted_score=None,
                                   metrics=[r4._metric("Debt-to-Equity (vs sector 1.80)",
                                                       "0.55", "industry", "debt_to_equity", 4)])
    vital = C._build_health_vital(None, 0.55, False, card_weighted=unrated.weighted_score,
                                  industry=CREDIT)
    assert vital["score"] == {"value": None, "status": "unmeasured"}
