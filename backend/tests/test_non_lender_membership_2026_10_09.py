"""NET-5 (owner decision 2026-10-09): who is a non-lender member of "Financial - Credit
Services", and the curated rows withheld for WU.

  * The curated list stays the ONLY way in; data checks only take rows away.
    `NON_LENDER_MEMBERS = PAYMENT_NETWORKS | CREDIT_SERVICES_FEE_BUSINESSES`, where the fee
    businesses are TREE (LendingTree, a loan marketplace earning referral fees) and PMTS (CPI
    Card Group, a card manufacturer) — both verified filed under Credit Services by a
    read-only FMP profile call on 2026-10-08. The verdict names the CATEGORY.
  * WU: its own SEC filings carry no current/non-current split (no AssetsCurrent /
    LiabilitiesCurrent for CIK 1365135), so FMP's current and quick ratios are made up
    (3.75 / 3.75, ~1.0 or 7.12 in other quarters), and FMP's Q3-25 / Q4-25 interest expense
    ($175.6M / $101.7M vs SEC's $37.0M / $36.7M) makes its interest coverage known-wrong.
    `CURATED_WITHHELD_ROWS` withholds the three — fail-closed, no expiry — on the Health
    Check, the health-snapshot fallback, the Overview fallback and the report's lines. WU's
    Health card then rests on D/E alone, on absolute bands → unrated.
  * `REVIEWED_CREDIT_SERVICES_LENDERS`: the 32 lender members of the universe, each with
    why; together with `NON_LENDER_MEMBERS` they classify every Credit Services member.

Each test's docstring names the mutation it catches. Hermetic: stubbed FMP answers, peer
lookups and Supabase — no network (the universe test reads the local data file).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import pytest

import test_card_networks_vs_lenders_2026_10_08 as nv
import test_health_check_deepcheck as hcd
import test_snapshot_cards_2026_10_07_profitability_health as sph
from app.services import financials_metric_gate as gate
from app.services import health_check_service as hc
from app.services.agents import ticker_report_data_collector as C
from app.services.sector_benchmark_lookup import BenchmarkLookupFailed
from app.services.stock_overview_service import StockOverviewService

CREDIT = "Financial - Credit Services"
FS = "Financial Services"
_GATED = {"current_ratio", "quick_ratio", "interest_coverage"}
_DATA = Path(__file__).resolve().parent.parent / "data"
_DATES = ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")

# Real FMP quarters (read-only probe 2026-10-08), newest first: revenue, netInterestIncome
# (interestIncome is 0 in every quarter for both).
_TREE_Q = ((313.422e6, -8.483e6), (327.267e6, -8.566e6), (319.688e6, -9.394e6),
           (307.792e6, -17.907e6))
_PMTS_Q = ((149.181e6, -7.405e6), (147.108e6, -7.656e6), (153.054e6, -7.966e6),
           (137.966e6, -8.746e6))


def _quarters(shape, ii_share: float = 0.0) -> List[Dict[str, Any]]:
    return [dict(hcd._QUARTER, date=d, revenue=rev, interestIncome=rev * ii_share,
                 netInterestIncome=nii)
            for d, (rev, nii) in zip(_DATES, shape)]


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. Membership
# ══════════════════════════════════════════════════════════════════════════════════════


def test_non_lender_members_are_exactly_networks_plus_fee_businesses():
    """Mutation: adding a pawn lender (FCFS), a lease-to-own firm (PRG), a BNPL lender
    (SEZL), a bank (KLAR) or NRDS; dropping TREE or PMTS."""
    assert gate.PAYMENT_NETWORKS == frozenset({"V", "MA", "PYPL", "WU", "GPN"})
    assert gate.CREDIT_SERVICES_FEE_BUSINESSES == frozenset({"TREE", "PMTS"})
    assert gate.NON_LENDER_MEMBERS == gate.PAYMENT_NETWORKS | gate.CREDIT_SERVICES_FEE_BUSINESSES
    for members in (gate.PAYMENT_NETWORKS, gate.CREDIT_SERVICES_FEE_BUSINESSES,
                    gate.NON_LENDER_MEMBERS):
        assert isinstance(members, frozenset)
        assert all(t == t.strip().upper() and t for t in members)
    for outsider in ("FCFS", "EZPW", "PRG", "NRDS", "SEZL", "KLAR", "UPST", "AXP"):
        assert outsider not in gate.NON_LENDER_MEMBERS, outsider
    # each fee business carries its one-line evidence
    assert set(gate.NON_LENDER_EVIDENCE) == set(gate.CREDIT_SERVICES_FEE_BUSINESSES)
    assert all(isinstance(v, str) and len(v) > 20 for v in gate.NON_LENDER_EVIDENCE.values())


@pytest.mark.parametrize("ticker,category", [
    ("V", gate.CATEGORY_PAYMENT_NETWORK), ("gpn ", gate.CATEGORY_PAYMENT_NETWORK),
    ("TREE", gate.CATEGORY_FEE_BUSINESS), ("pmts", gate.CATEGORY_FEE_BUSINESS),
    ("AXP", None), ("", None), (None, None), (5, None),
])
def test_the_category_is_exposed(ticker, category):
    """A future payments peer group must read the category, never `is_network` (TREE and
    PMTS are not payment networks). Mutation: one category for both sets."""
    assert gate.non_lender_category(ticker) == category
    verdict = gate.payment_network_verdict(ticker, CREDIT, None)
    assert verdict.category == category


@pytest.mark.parametrize("ticker,shape", [("TREE", _TREE_Q), ("PMTS", _PMTS_Q)])
def test_tree_and_pmts_are_members_on_their_real_quarters(ticker, shape):
    """Their real trailing quarters (interestIncome 0, a small negative net interest — the
    cost of corporate debt) pass the lender check; the reason names the category."""
    row = gate.trailing_interest_row(_quarters(shape))
    assert gate.lender_verdict(row)[0] is False
    verdict = gate.payment_network_verdict(ticker, CREDIT, row)
    assert verdict.is_network is True and verdict.warn is False
    assert "non-lender fee business" in verdict.reason
    assert "payment network" not in verdict.reason


def test_a_fee_business_with_lender_data_is_vetoed_with_a_warning(caplog):
    """The data can still veto a listed fee business. Mutation: trusting the list alone."""
    row = gate.trailing_interest_row(_quarters(_TREE_Q, ii_share=0.25))
    verdict = gate.payment_network_verdict("TREE", CREDIT, row)
    assert verdict.is_network is False and verdict.warn is True
    assert verdict.category == gate.CATEGORY_FEE_BUSINESS
    with caplog.at_level(logging.INFO, logger=gate.logger.name):
        assert gate.resolve_payment_network("TREE", CREDIT, row, source="unit") is False
    warn = [r for r in caplog.records if r.levelno == logging.WARNING
            and "[network-gate]" in r.getMessage()]
    assert len(warn) == 1 and "ticker=TREE" in warn[0].getMessage()


def test_every_credit_services_member_is_classified():
    """Every Credit Services member of the benchmark universe is a listed non-lender member
    OR a reviewed lender with a reason — never both, never neither — and every reviewed
    lender found in the universe sits under Credit Services (an entry FMP moved elsewhere
    gets pruned). The universe builder is to WARN on the same gap (pending: not wired yet). Mutation: a new member left
    unclassified, or a ticker in both sets."""
    path = _DATA / "benchmark_universe.json"
    if not path.exists():
        pytest.skip("backend/data/benchmark_universe.json is gitignored and absent here")
    rows = json.loads(path.read_text()).get("industries") or []
    members = {
        t.strip().upper()
        for r in rows if isinstance(r, dict) and gate.is_mixed_lender_industry(r.get("industry"))
        for t in (r.get("tickers") or [])
    }
    assert len(members) >= 30, "an empty or truncated read cannot pass this check"
    reviewed = gate.REVIEWED_CREDIT_SERVICES_LENDERS
    assert not (set(reviewed) & gate.NON_LENDER_MEMBERS)
    unclassified = members - gate.NON_LENDER_MEMBERS - set(reviewed)
    assert not unclassified, f"classify these Credit Services members: {sorted(unclassified)}"
    elsewhere = {
        t.strip().upper(): r.get("industry")
        for r in rows if isinstance(r, dict) and not gate.is_mixed_lender_industry(r.get("industry"))
        for t in (r.get("tickers") or []) if t.strip().upper() in reviewed
    }
    assert not elsewhere, f"reviewed lenders filed elsewhere now (prune them): {elsewhere}"
    for ticker, why in reviewed.items():
        assert ticker == ticker.strip().upper() and isinstance(why, str) and len(why) > 10


def test_the_reviewed_lenders_are_the_32_named_in_the_decision():
    expected = set("AXP COF SYF ALLY SOFI SLM BFH GDOT KLAR AFRM UPST SEZL ENVA OPFI OMF CACC "
                   "WRLD ATLC ECPG PRAA JCAP PRG IX NNI NAVI AGM LU QFIN FINV SWRD FCFS "
                   "EZPW".split())
    assert set(gate.REVIEWED_CREDIT_SERVICES_LENDERS) == expected and len(expected) == 32
    for lender in ("ENVA", "FCFS", "PRG", "SEZL"):     # reviewed ≠ admitted
        assert gate.payment_network_verdict(lender, CREDIT, None).is_network is False


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. The curated withheld rows (WU)
# ══════════════════════════════════════════════════════════════════════════════════════


def test_the_curated_table_is_wu_and_its_three_rows():
    """No expiry, no data condition: a fact about WU's filings and FMP's numbers. Mutation:
    deleting WU (its made-up 3.75 / 3.75 "positive" rows ship) or a row from it."""
    assert set(gate.CURATED_WITHHELD_ROWS) == {"WU"}
    assert set(gate.CURATED_WITHHELD_ROWS["WU"]) == _GATED
    reasons = gate.CURATED_WITHHELD_ROWS["WU"]
    assert "1365135" in reasons["current_ratio"] and "AssetsCurrent" in reasons["quick_ratio"]
    assert "InterestExpenseDebt" in reasons["interest_coverage"]
    with pytest.raises(TypeError):            # immutable
        gate.CURATED_WITHHELD_ROWS["WU"]["debt_to_equity"] = "x"  # type: ignore[index]


@pytest.mark.parametrize("ticker,expected", [
    ("WU", _GATED), ("wu", _GATED), (" Wu\n", _GATED),
    ("WUX", set()), ("V", set()), ("", set()), (None, set()), (7, set()), (["WU"], set()),
])
def test_withheld_company_rows_normalises_the_ticker(ticker, expected):
    assert set(gate.withheld_company_rows(ticker)) == expected


@pytest.mark.parametrize("industry", [CREDIT, "Software - Infrastructure", "Banks - Regional",
                                      "", None])
@pytest.mark.parametrize("network", [True, False])
def test_wus_rows_are_withheld_in_every_industry(industry, network):
    """A fact about the company, not its filing category. D/E is never withheld.
    Mutation: applying the table only in the mixed industry, or only to a network."""
    for metric in _GATED:
        assert gate.company_metric_applicable(metric, industry, network=network,
                                              ticker="wu") is False
    assert gate.company_metric_applicable("debt_to_equity", industry, network=network,
                                          ticker="WU") is True
    assert hc.omitted_financial_rows(industry, network=network, ticker="WU") == frozenset(_GATED)


def test_the_ticker_is_a_required_keyword():
    """A caller that forgets the ticker would silently show WU's rows: it must fail loudly.
    Mutation: a default of None."""
    with pytest.raises(TypeError):
        gate.company_metric_applicable("current_ratio", CREDIT, network=True)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        hc.omitted_financial_rows(CREDIT, network=True)  # type: ignore[call-arg]


def test_the_resolver_logs_once_with_every_reason(caplog):
    with caplog.at_level(logging.INFO, logger=gate.logger.name):
        assert gate.resolve_withheld_company_rows("wu", source="unit") == frozenset(_GATED)
        assert gate.resolve_withheld_company_rows("V", source="unit") == frozenset()
    lines = [r.getMessage() for r in caplog.records if "[curated-withheld]" in r.getMessage()]
    assert len(lines) == 1
    assert "ticker=WU" in lines[0] and "step=unit" in lines[0]
    assert "1365135" in lines[0] and "InterestExpenseDebt" in lines[0]


# ── WU end to end ───────────────────────────────────────────────────────────────────

# WU's FMP ratios-TTM shapes: 2026-06-30 (3.75 / 3.75, IC 1.85) and 2026-03-31 (a
# plausible-looking 1.02 current ratio — the shape a balance-sheet heuristic misses).
_WU_RATIOS_JUNE = dict(nv._V_RATIOS, currentRatioTTM=3.7528, quickRatioTTM=3.7528,
                       interestCoverageRatioTTM=1.85, debtToEquityRatioTTM=3.1,
                       priceToEarningsRatioTTM=5.05)
_WU_RATIOS_MARCH = dict(_WU_RATIOS_JUNE, currentRatioTTM=1.02, quickRatioTTM=1.02)


class _Lookup:
    def __init__(self, boom=None):
        self.asked: List[List[str]] = []
        self.boom = boom

    def get_current_benchmarks(self, industry, sector, metrics):
        self.asked.append(list(metrics))
        if self.boom:
            raise self.boom
        return {m: {"value": 1.0, "level": "industry", "n": 60} for m in metrics}


async def _wu_health_check(monkeypatch, ratios, *, bs=None, symbol="WU"):
    lookup = _Lookup()
    monkeypatch.setattr(hc, "get_sector_benchmark_lookup", lambda: lookup)
    hc._cache.clear()
    hc._inflight.clear()
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = None
    svc.fmp = hcd._FakeFMP(hcd._answers(
        profile=dict(hcd._PROFILE, symbol=symbol, sector=FS, industry=CREDIT),
        ratios=[dict(ratios)], income=nv._quarters(*nv._NETWORKS["WU"]), bs=bs,
    ))
    resp, _ = await svc._build_health_check(symbol)
    return resp, lookup


@pytest.mark.asyncio
@pytest.mark.parametrize("ratios", [_WU_RATIOS_JUNE, _WU_RATIOS_MARCH])
@pytest.mark.parametrize("bs", [None, RuntimeError("balance sheet 429")])
async def test_wus_health_check_has_no_made_up_rows(monkeypatch, caplog, ratios, bs):
    """Whatever FMP's split looks like this quarter, and even when the balance-sheet leg
    fails: no current ratio, quick ratio or interest coverage; D/E, P/E and ROE on
    absolute bands; no lookup; ONE `[curated-withheld]` line. Mutation: deleting WU from
    the table (3.75 "positive" rows), or a data heuristic in its place (the March shape)."""
    with caplog.at_level(logging.INFO):
        resp, lookup = await _wu_health_check(monkeypatch, ratios, bs=bs)
    by = nv._by(resp)
    assert not (_GATED & set(by)), sorted(by)
    assert by["debt_to_equity"].status == hc._absolute_status("debt_to_equity", 3.1)
    assert all(m.peer_level is None and m.comparison_value is None for m in resp.metrics)
    assert lookup.asked == []
    lines = [r.getMessage() for r in caplog.records if "[curated-withheld]" in r.getMessage()]
    assert len(lines) == 1 and "step=health_check" in lines[0]
    # the industry-omission line does not claim the curated rows as "not meaningful"
    assert not [r for r in caplog.records
                if "omitted — not meaningful for industry" in r.getMessage()]


@pytest.mark.asyncio
async def test_wus_health_card_is_unrated_on_the_main_path(monkeypatch):
    """The snapshot hides P/E and ROE; D/E alone is one scored row → unrated (rating 0, no
    weighted_score), exactly like production before the network gate."""
    health, _ = await _wu_health_check(monkeypatch, _WU_RATIOS_JUNE)
    _hs, svc = sph._health_service(monkeypatch, health,
                                   profile={"symbol": "WU", "sector": FS, "industry": CREDIT,
                                            "mktCap": 6e9})
    snap, degraded = await svc._compute_with_status("WU")
    assert degraded == []
    assert [m.metric_key for m in snap.metrics] == ["debt_to_equity"]
    assert snap.metrics[0].peer_level is None and "vs" not in snap.metrics[0].name
    assert snap.rating == 0 and snap.weighted_score is None


@pytest.mark.asyncio
async def test_wus_fallback_card_has_no_made_up_rows(monkeypatch, caplog):
    """The Health Check raised: the local fallback withholds the same three rows, asks the
    lookup nothing (WU is a listed member: nothing comparable), and is unrated. V (control)
    keeps all three. Mutation: the fallback reading `liquidity_ok` without the ticker."""
    for symbol, expected in (("WU", ["debt_to_equity"]),
                             ("V", ["debt_to_equity", "current_ratio", "interest_coverage",
                                    "quick_ratio"])):
        caplog.clear()
        with caplog.at_level(logging.INFO):
            _hs, svc = sph._health_service(
                monkeypatch, RuntimeError("health check exploded"),
                profile={"symbol": symbol, "sector": FS, "industry": CREDIT, "mktCap": 6e9},
                bs=dict(nv._V_BS), income=nv._quarters(*nv._NETWORKS[symbol]),
                bench=nv._FB_BENCH)
            snap, degraded = await svc._compute_with_status(symbol)
        assert [m.metric_key for m in snap.metrics] == expected, symbol
        assert degraded == ["health_check"]
        curated = [r for r in caplog.records if "[curated-withheld]" in r.getMessage()]
        assert len(curated) == (1 if symbol == "WU" else 0)
        if symbol == "WU":
            assert snap.rating == 0 and snap.weighted_score is None


@pytest.mark.parametrize("symbol,kept", [("WU", False), ("wu", False), ("V", True),
                                         ("PMTS", True)])
def test_the_overview_fallback_withholds_wus_interest_coverage(symbol, kept, caplog):
    """WU's Overview fallback omits Interest Coverage, and says why ONCE (the curated table
    is logged once per build on every surface). Mutation: the
    `resolve_withheld_company_rows` call dropped (the row vanishes with no log line)."""
    with caplog.at_level(logging.INFO, logger=gate.logger.name):
        card = StockOverviewService.__new__(StockOverviewService)._build_health_snapshot(
            dict(nv._V_BS), {}, {"freeCashFlow": 5e9}, {"interestCoverage": 1.85}, {}, 6e9,
            sector=FS, industry=CREDIT, ticker=symbol, income_quarterly=None,
        )
    assert ("Interest Coverage" in [m.name for m in card.metrics]) is kept
    curated = [r.getMessage() for r in caplog.records
               if "[curated-withheld]" in r.getMessage()]
    if kept:
        assert curated == []
    else:
        assert len(curated) == 1
        assert "ticker=WU" in curated[0] and "step=overview_fallback_health" in curated[0]
        assert "interest_coverage" in curated[0]


def test_the_reports_peer_lines_for_wus_rows_are_withheld_in_any_industry():
    """Even if FMP refiled WU into an industry whose lines are drawn, its curated rows draw
    no line (the card omits them). Mutation: the helper keyed on the industry."""
    line = {"2024": 1.0, "2025": 1.1}
    hist = {"annual": {"current_ratio": dict(line), "roe": dict(line)},
            "quarterly": {"interest_coverage": {"Q1'26": 4.0}},
            "levels": {"annual": {"current_ratio": "industry", "roe": "industry"},
                       "quarterly": {"interest_coverage": "industry"}}}
    C._withhold_curated_company_lines(hist, withheld=gate.resolve_withheld_company_rows(
        "WU", source="unit"), ticker="WU")
    assert hist["annual"] == {"roe": line} and hist["quarterly"] == {}
    assert hist["levels"] == {"annual": {"roe": "industry"}, "quarterly": {}}
    failed = BenchmarkLookupFailed({"annual": {"quick_ratio": dict(line)}})
    C._withhold_curated_company_lines(failed, withheld=frozenset({"quick_ratio"}), ticker="WU")
    assert isinstance(failed, BenchmarkLookupFailed) and failed["annual"] == {}
    for junk in (None, [], "x", {"annual": None, "levels": 3}):
        C._withhold_curated_company_lines(junk, withheld=frozenset(_GATED), ticker="WU")
    untouched = {"annual": {"current_ratio": dict(line)}}
    C._withhold_curated_company_lines(untouched, withheld=frozenset(), ticker="V")
    assert untouched == {"annual": {"current_ratio": line}}


def test_the_report_freezes_none_of_wus_withheld_series(monkeypatch):
    """`_build_sections` drops WU's current-ratio / quick-ratio / interest-coverage
    drill-down series (FMP's made-up split, 7.12 in FY2024), and keeps V's."""
    import test_report_round4_2026_10_08 as r4

    class _NoLookup:
        def get_current_benchmarks(self, industry, sector, metrics):
            return {}

    monkeypatch.setattr("app.services.sector_benchmark_lookup.get_sector_benchmark_lookup",
                        lambda: _NoLookup())
    keys = {}
    real = C._build_fundamental_metrics_from_snapshots
    for symbol in ("WU", "V"):
        out = r4._collected({"sector": FS, "industry": CREDIT}, de=3.1, fcf=1e9,
                            snap_health=None)
        out.ticker = symbol
        out.ratios = [{"date": "2025-12-31", "calendarYear": "2025", "period": "FY",
                       "currentRatio": 7.12, "quickRatio": 7.12, "interestCoverageRatio": 1.85,
                       "debtToEquityRatio": 3.1, "priceToEarningsRatio": 5.0}]
        captured = {}

        def _spy(*a, **k):
            captured.update(k.get("history_lookup") or {})
            return real(*a, **k)

        monkeypatch.setattr(C, "_build_fundamental_metrics_from_snapshots", _spy)
        coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
        coll._compute_metrics(out)
        coll._build_sections(out)
        keys[symbol] = set(captured)
    assert "debt_to_equity" in keys["WU"], keys["WU"]
    assert not (_GATED & keys["WU"]), keys["WU"]
    assert _GATED <= keys["V"], keys["V"]


@pytest.mark.asyncio
async def test_pass_two_withholds_wus_curated_lines_through_the_real_wiring(monkeypatch, caplog):
    """Through the REAL pass 2 (`_fetch_dependent`): WU filed in an industry whose
    liquidity / coverage lines are drawn still gets none of its three (the curated fact
    follows the company), with ONE `[curated-withheld]` line; V there keeps them.
    Mutation: the pass-2 call to `_withhold_curated_company_lines` removed."""
    for symbol, kept in (("WU", False), ("V", True)):
        caplog.clear()
        with caplog.at_level(logging.INFO):
            hist = await nv._history_for(
                monkeypatch, ticker=symbol, income_q=nv._quarters(*nv._NETWORKS[symbol]),
                industry="Software - Infrastructure", sector="Technology")
        for metric in _GATED:
            assert (metric in hist["annual"]) is kept, (symbol, metric)
        curated = [r for r in caplog.records if "[curated-withheld]" in r.getMessage()]
        assert len(curated) == (0 if kept else 1)
        if not kept:
            assert "step=report_peer_lines" in curated[0].getMessage()


@pytest.mark.asyncio
async def test_each_curated_row_is_withheld_on_its_own(monkeypatch):
    """A curated entry may withhold ONE row: the fallback must gate quick ratio on its own
    answer, not on the current ratio's. Mutation: `quick_ok` folded into `liquidity_ok`."""
    from types import MappingProxyType

    monkeypatch.setattr(gate, "CURATED_WITHHELD_ROWS", MappingProxyType({
        "V": MappingProxyType({"quick_ratio": "hypothetical: only this row"})}))
    _hs, svc = sph._health_service(
        monkeypatch, RuntimeError("health check exploded"),
        profile={"symbol": "V", "sector": FS, "industry": CREDIT, "mktCap": 6e11},
        # a tiny quick ratio (0.03, "negative") that WOULD drag the blend if it voted
        # (current ratio 2.0 and coverage 30x read positive, D/E 0.55 neutral: 3/4/4 → 4;
        # a voting quick-ratio 2 would make it 3.25 → 3)
        bs=dict(nv._V_BS, cashAndCashEquivalents=1e9, netReceivables=0.0,
                totalCurrentAssets=70e9),
        income=nv._quarters(*nv._NETWORKS["V"]), bench=nv._FB_BENCH)
    snap, _ = await svc._compute_with_status("V")
    assert [m.metric_key for m in snap.metrics] == [
        "debt_to_equity", "current_ratio", "interest_coverage"]
    # ...and the withheld row never votes in the pass rating either (Altman Z is omitted
    # for Financial Services: the blend's anchor is the neutral 3).
    scores = [m.score for m in snap.metrics]
    assert scores == [3, 4, 4], scores
    assert snap.weighted_score == pytest.approx(0.4 * 3 + 0.6 * 4)
