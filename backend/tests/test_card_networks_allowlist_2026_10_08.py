"""Payment networks are an ALLOW-LIST, not an interest-income share (2026-10-08).

The first cut of owner decision 3 called a "Financial - Credit Services" company a card
network when its interest income was under 25% of revenue. Real FMP data (read-only probe,
2026-10-08, latest annual interestIncome / revenue | netInterestIncome / revenue |
interestExpense / revenue) showed it freeing real LENDERS whose interest income FMP
zero-fills:

    ENVA (Enova, consumer lender)  0.00 | -0.05 | 0.05
    SEZL (Sezzle, BNPL)            0.00 | -0.03 | 0.03
    QFIN                           0.02 |  0.02 | 0.00
    FINV                           0.10 |  0.10 | 0.00

— the same shape as the networks V 0.00 | -0.01 | 0.01, MA 0.00 | -0.02 | 0.02, PYPL
0.02 | 0.00 | 0.01, WU 0.00 | -0.03 | 0.04, GPN 0.02 | -0.06 | 0.08 (GPN's interest
expense alone rules out an interest-expense line as well).

Decision (Claude, owner-level, 2026-10-08): `PAYMENT_NETWORKS = {V, MA, PYPL, WU, GPN}`. A
company is a network only when its normalised ticker is listed AND its own income does not
read as a lender's (`lender_verdict` True → gated + WARNING; unknown data → still a network).
Every other member stays gated whatever its data; a gate site with no ticker fails closed.
A network's current ratio, quick ratio, interest coverage AND debt-to-equity are judged on
ABSOLUTE bands only (LND-6: the Credit Services D/E median pools lenders), `peer_level`
None, and the report drill-down withholds those lines.

2026-10-09 (owner decisions NET-4 / NET-5): the list grows into `NON_LENDER_MEMBERS` (the
five networks plus the fee businesses TREE and PMTS), EVERY metric of a listed member is
judged without the industry median, and WU's current ratio, quick ratio and interest
coverage are withheld by a curated per-company fact — so WU's cards are covered by
`test_non_lender_members_peer_free_2026_10_09.py`, not by the "keeps its rows" tests here.

Each test's docstring names the mutation it catches. Hermetic: stubbed FMP answers, peer
lookups and Supabase tables — no network.
"""

from __future__ import annotations

import ast
import inspect
import logging
import textwrap
from typing import Any, Dict, List

import pytest

import test_card_networks_vs_lenders_2026_10_08 as nv
import test_health_check_deepcheck as hcd
import test_report_round4_2026_10_08 as r4
import test_snapshot_cards_2026_10_07_profitability_health as sph
from app.services.sector_benchmark_lookup import BenchmarkLookupFailed
from app.services import financials_metric_gate as gate
from app.services import health_check_service as hc
from app.services import stock_overview_service as sos
from app.services.agents import ticker_report_data_collector as C
from app.services.stock_overview_service import StockOverviewService

CREDIT = nv.CREDIT
FS = nv.FS
_GATED = nv._GATED
_ABSOLUTE = nv._ABSOLUTE
_LISTED = ("GPN", "MA", "PYPL", "V", "WU")
# The listed networks whose three rows are NOT curated-withheld (WU's are: its filings carry
# no current/non-current split, and FMP's interest expense is not WU's).
_KEEPS_ROWS = tuple(t for t in _LISTED if t not in gate.CURATED_WITHHELD_ROWS)

# The probe's network-SHAPED members that are lenders (latest annual, $ rounded; the
# SHARES are the probe's). All read "not a lender" to the sanity check.
_ZERO_FILLED_LENDERS = {
    "ENVA": (2.6e9, 0.0, -0.13e9),      # 0.00 | -0.05
    "SEZL": (0.27e9, 0.0, -0.0081e9),   # 0.00 | -0.03
    "QFIN": (17.0e9, 0.34e9, 0.34e9),   # 0.02 | 0.02
    "FINV": (5.0e9, 0.5e9, 0.5e9),      # 0.10 | 0.10
}
# Lenders whose data the sanity check does catch — gated by the list first anyway.
_PLAIN_LENDERS = {"AXP": nv._LENDERS["AXP"], "COF": nv._LENDERS["COF"]}
_ALL_GATED_MEMBERS = {**_ZERO_FILLED_LENDERS, **_PLAIN_LENDERS}


def _gate_records(caplog, level: int = logging.INFO) -> List[logging.LogRecord]:
    return [r for r in caplog.records
            if r.levelno >= level and "[network-gate]" in r.getMessage()]


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. The list and the verdict
# ══════════════════════════════════════════════════════════════════════════════════════


def test_the_list_is_exactly_the_five_payment_networks():
    """Mutation: adding a member (FCFS, UPST, a BDC) frees a lender's rows; dropping one
    (GPN) gates a processor. The list is upper-case and immutable."""
    assert gate.PAYMENT_NETWORKS == frozenset({"V", "MA", "PYPL", "WU", "GPN"})
    assert isinstance(gate.PAYMENT_NETWORKS, frozenset)
    assert all(t == t.strip().upper() for t in gate.PAYMENT_NETWORKS)
    # 2026-10-09: the every-metric rule replaced the four-metric `NETWORK_ABSOLUTE_METRICS`.
    assert not hasattr(gate, "NETWORK_ABSOLUTE_METRICS")
    assert gate.PAYMENT_NETWORKS <= gate.NON_LENDER_MEMBERS


@pytest.mark.parametrize("ticker", [
    "V", "v", " V ", "\tma\n", "Ma", "pypl", "PyPl", "wu ", " gpn", "GPN",
])
@pytest.mark.parametrize("row", [nv._annual(*nv._NETWORKS["V"]), None, {}])
def test_a_listed_ticker_is_a_network_whatever_its_case_or_whitespace(ticker, row):
    """Normalised (stripped, upper-cased) before the lookup. Unknown data (None, {}) leaves
    the curated list standing. Mutation: an exact-match lookup gates "v" from a caller that
    did not upper-case; an unknown verdict vetoing the list gates V on a missing row."""
    verdict = gate.payment_network_verdict(ticker, CREDIT, row)
    assert verdict.is_network is True, verdict.reason
    assert verdict.warn is False
    assert gate.normalize_ticker(ticker) in gate.PAYMENT_NETWORKS


@pytest.mark.parametrize("ticker,warn", [
    ("VV", False), ("VISA", False), ("V.", False), ("MA-", False), ("PYPL1", False),
    ("P YPL", False), ("W U", False), ("BRK.B", False), ("FCFS", False), ("UPST", False),
    ("", True), ("   ", True), ("\n", True), (None, True), (5, True), (b"V", True),
    (["V"], True), ({"V"}, True), (True, True),
])
def test_a_lookalike_or_missing_ticker_is_not_a_network(ticker, warn):
    """Only an exact listed symbol counts; a blank or non-string ticker is "no ticker",
    gated and flagged (`warn`: the resolver logs it at WARNING). Mutation: a prefix /
    substring match ("VV", "VISA"), or treating a missing ticker as unknown-but-listed."""
    verdict = gate.payment_network_verdict(ticker, CREDIT, nv._annual(*nv._NETWORKS["V"]))
    assert verdict.is_network is False, verdict.reason
    assert verdict.warn is warn, verdict.reason


@pytest.mark.parametrize("ticker", sorted(_ZERO_FILLED_LENDERS))
def test_the_sanity_check_cannot_tell_zero_filled_lenders_from_networks(ticker):
    """WHY the list exists: on FMP's data the probe's lenders read exactly like V. The
    verdict therefore only VETOES; it never frees. Mutation: reverting to the first cut
    (a network = `lender_verdict` False) frees ENVA, SEZL, QFIN, FINV."""
    row = nv._annual(*_ZERO_FILLED_LENDERS[ticker])
    assert gate.lender_verdict(row)[0] is False
    assert gate.lender_verdict(nv._annual(*nv._NETWORKS["V"]))[0] is False
    verdict = gate.payment_network_verdict(ticker, CREDIT, row)
    assert verdict.is_network is False and verdict.warn is False
    assert "not a listed payment network" in verdict.reason


@pytest.mark.parametrize("ticker", sorted(_ALL_GATED_MEMBERS))
@pytest.mark.parametrize("row_kind", ["own", "v_shaped", "unknown"])
def test_every_unlisted_member_is_gated_whatever_its_data(ticker, row_kind):
    """Fail closed: an unlisted member is gated with its own data, with V's data, and with
    none. Mutation: any data path into the network verdict."""
    row = {"own": nv._annual(*_ALL_GATED_MEMBERS[ticker]),
           "v_shaped": nv._annual(*nv._NETWORKS["V"]), "unknown": None}[row_kind]
    assert gate.payment_network_verdict(ticker, CREDIT, row).is_network is False
    assert gate.company_metric_applicable(
        "current_ratio", CREDIT, network=False, ticker=ticker) is False


@pytest.mark.parametrize("ticker", _LISTED)
def test_a_listed_ticker_with_lender_data_is_vetoed_with_a_warning(ticker, caplog):
    """The data sanity check vetoes the list: the resolver gates and logs a WARNING that
    names the ticker and BOTH shares. Mutation: trusting the list alone, or logging the
    veto at INFO (it is a list-vs-data contradiction someone must look at)."""
    row = nv._annual(10.0e9, 0.0, 8.5e9)
    with caplog.at_level(logging.INFO, logger=gate.logger.name):
        network = gate.resolve_payment_network(ticker.lower(), CREDIT, row, source="unit")
    assert network is False
    records = _gate_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    msg = records[0].getMessage()
    assert f"ticker={ticker}" in msg and "step=unit" in msg
    assert "interestIncome/revenue 0.000" in msg and "netInterestIncome/revenue 0.850" in msg
    assert "gated" in msg


def test_the_resolver_logs_each_outcome_at_its_level(caplog):
    """INFO for a decided answer, WARNING for a degraded one (no ticker, a veto), and no
    line at all outside a mixed industry (nothing was decided there — a bank log line on
    every build would be noise). Mutation: a missing ticker logged at INFO is invisible."""
    v_row = nv._annual(*nv._NETWORKS["V"])
    cases = [
        ("V", CREDIT, v_row, True, logging.INFO),
        ("ENVA", CREDIT, v_row, False, logging.INFO),
        (None, CREDIT, v_row, False, logging.WARNING),
        ("V", CREDIT, nv._annual(1.0, 0.5, 0.5), False, logging.WARNING),
    ]
    for ticker, industry, row, expected, level in cases:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=gate.logger.name):
            assert gate.resolve_payment_network(ticker, industry, row, source="t") is expected
        records = _gate_records(caplog)
        assert [r.levelno for r in records] == [level], (ticker, records)
    for industry in ("Banks - Regional", "Software - Infrastructure", "", None):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=gate.logger.name):
            assert gate.resolve_payment_network("V", industry, v_row, source="t") is False
        assert _gate_records(caplog) == [], industry


@pytest.mark.parametrize("industry", [
    "Banks - Regional", "Banks - Diversified", "Insurance - Life", "Insurance - Brokers",
    "Financial - Mortgages", "Asset Management", "Financial - Data & Stock Exchanges",
    "Software - Infrastructure", "", None,
])
def test_the_list_means_nothing_outside_a_mixed_industry(industry):
    """V filed under a bank or insurer industry is a bank: not a network, and every gate
    answer is the industry's own. Mutation: consulting the list before the industry."""
    assert gate.payment_network_verdict("V", industry, None).is_network is False
    for metric in _GATED:
        assert gate.company_metric_applicable(metric, industry, network=True, ticker="V") is (
            gate.peer_metric_applicable(metric, industry))
    assert gate.peer_median_comparable("debt_to_equity", industry, network=True) is True


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. The Health Check build
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker", sorted(_ALL_GATED_MEMBERS))
async def test_the_probes_members_are_gated_in_the_build(monkeypatch, caplog, ticker):
    """ENVA / SEZL / QFIN / FINV carry the networks' data shape and AXP / COF a lender's;
    all six are gated, their D/E compared with the industry as a lender's is. Mutation:
    the first cut's share rule gave ENVA a "current ratio" and "interest coverage"."""
    with caplog.at_level(logging.INFO, logger=gate.logger.name):
        resp, lookup = await nv._hc(
            monkeypatch, income=nv._quarters(*_ALL_GATED_MEMBERS[ticker]), symbol=ticker)
    by = nv._by(resp)
    assert not (set(by) & _GATED), sorted(by)
    assert by["debt_to_equity"].comparison_value == 1.8
    assert by["debt_to_equity"].peer_level == "industry"
    assert all(not (set(a) & _GATED) for a in lookup.asked)
    assert any("debt_to_equity" in a for a in lookup.asked)
    msgs = [r.getMessage() for r in _gate_records(caplog)]
    assert len(msgs) == 1 and f"ticker={ticker}" in msgs[0], msgs
    assert "not a listed payment network" in msgs[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker", ["v", " MA ", "pypl", "Wu", "gpn\n"])
async def test_the_build_normalises_the_ticker(monkeypatch, ticker):
    """The ticker reaches the gate as the caller passed it; the gate normalises — the
    network list AND the curated withheld rows (a lower-case "Wu" still loses its three).
    Mutation: an un-normalised lookup gates a lower-case caller's V, or shows "Wu"'s rows."""
    symbol = ticker.strip().upper()
    shape = nv._NETWORKS[symbol]
    resp, _ = await nv._hc(monkeypatch, income=nv._quarters(*shape), symbol=ticker)
    if symbol in gate.CURATED_WITHHELD_ROWS:
        assert not (_GATED & set(nv._by(resp)))
    else:
        assert _GATED <= set(nv._by(resp))


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker", ["", "   "])
async def test_a_build_with_no_ticker_is_gated(monkeypatch, caplog, ticker):
    """Fail closed with a WARNING when the build cannot say which company it holds.
    Mutation: a blank ticker treated as unknown-but-listed."""
    with caplog.at_level(logging.INFO, logger=gate.logger.name):
        resp, _ = await nv._hc(monkeypatch, symbol=ticker)
    by = nv._by(resp)
    assert not (set(by) & _GATED)
    assert by["debt_to_equity"].peer_level == "industry"
    assert [r.levelno for r in _gate_records(caplog)] == [logging.WARNING]


@pytest.mark.asyncio
@pytest.mark.parametrize("de,network_status", [
    (1.2, "neutral"),       # the absolute 0.5-2.0 band; 33% BELOW lenders' 1.8 would pass
    (0.3, "positive"),
    (2.6, "negative"),
])
async def test_a_networks_de_is_judged_on_absolute_bands(monkeypatch, de, network_status):
    """LND-6: V's D/E is never compared with the Credit Services median (lenders'
    leverage). At 1.2 the absolute band says neutral, while the pooled median calls it a
    pass. An unlisted member with the same D/E is still compared. Mutation: dropping
    debt-to-equity from the network's absolute-only metrics."""
    ratios = dict(nv._V_RATIOS, debtToEquityRatioTTM=de)
    resp, lookup = await nv._hc(monkeypatch, ratios=ratios)
    row = nv._by(resp)["debt_to_equity"]
    assert row.comparison_value is None and row.percent_difference is None
    assert row.peer_level is None and row.status == network_status
    assert row.status == hc._absolute_status("debt_to_equity", de)
    text = hcd._rendered(row)
    assert "industry" not in text and "sector" not in text, text
    assert all("debt_to_equity" not in a for a in lookup.asked), lookup.asked

    resp, _ = await nv._hc(monkeypatch, ratios=ratios, symbol="ENVA",
                           income=nv._quarters(*_ZERO_FILLED_LENDERS["ENVA"]))
    row = nv._by(resp)["debt_to_equity"]
    assert row.comparison_value == 1.8 and row.peer_level == "industry"
    pct = round((de - 1.8) / 1.8 * 100, 1)
    assert row.percent_difference == pct
    assert row.status == hc._determine_status("debt_to_equity", pct, True)
    if de == 1.2:   # the case that tells the two rules apart
        assert row.status == "positive" != network_status


@pytest.mark.asyncio
async def test_a_networks_negative_equity_de_keeps_its_forced_verdict(monkeypatch):
    """A negative D/E (negative equity) is forced "negative" before any comparison, on the
    network path as everywhere. Mutation: the absolute-only path re-judging it."""
    resp, _ = await nv._hc(monkeypatch, ratios=dict(nv._V_RATIOS, debtToEquityRatioTTM=-0.8))
    row = nv._by(resp)["debt_to_equity"]
    assert row.status == "negative" and row.highlighted_value == "Negative"
    assert row.comparison_value is None and row.peer_level is None


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. The Financial Health card is RATED for every listed network
# ══════════════════════════════════════════════════════════════════════════════════════


def _scored(snap) -> List[Any]:
    return [m for m in snap.metrics
            if m.score is not None and m.value not in (None, "", "—", "N/M")]


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker", _KEEPS_ROWS)
async def test_a_networks_card_is_rated_on_the_main_path(monkeypatch, ticker):
    """Four scored rows (D/E + the three), all on absolute bands, no "vs" in any name.
    Mutation: the network's D/E carrying the pooled median ("vs sector 1.80") or the
    card staying unrated (the D/E-only card was)."""
    shape = nv._NETWORKS[ticker]
    health, _ = await nv._hc(monkeypatch, income=nv._quarters(*shape), symbol=ticker)
    _hs, svc = sph._health_service(
        monkeypatch, health, profile=dict(nv._V_PROFILE, symbol=ticker),
        income=nv._quarters(*shape))
    snap, degraded = await svc._compute_with_status(ticker)
    assert degraded == []
    assert len(_scored(snap)) >= 2 and len(_scored(snap)) == 4
    assert snap.rating > 0 and snap.weighted_score is not None
    for m in snap.metrics:
        assert m.peer_level is None and "vs " not in m.name, m.name


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker", _KEEPS_ROWS)
async def test_a_networks_card_is_rated_on_the_fallback_path(monkeypatch, ticker):
    """The Health Check raised: the local fallback reaches the same verdict from its own
    quarters, scores all four rows on absolute bands, and drops every pooled median."""
    shape = nv._NETWORKS[ticker]
    _hs, svc = sph._health_service(
        monkeypatch, RuntimeError("health check exploded"),
        profile=dict(nv._V_PROFILE, symbol=ticker), bs=dict(nv._V_BS),
        income=nv._quarters(*shape), bench=nv._FB_BENCH)
    snap, degraded = await svc._compute_with_status(ticker)
    assert degraded == ["health_check"]
    assert {m.metric_key for m in snap.metrics} == _ABSOLUTE
    assert len(_scored(snap)) == 4
    assert snap.rating > 0 and snap.weighted_score is not None
    for m in snap.metrics:
        assert m.peer_level is None and "vs " not in m.name, m.name


@pytest.mark.asyncio
async def test_the_fallback_rating_blends_all_four_absolute_scores(monkeypatch):
    """The fallback's pass rating is the mean of the SAME four scores its rows show (D/E
    included, on its absolute band). Values chosen so leaving D/E out moves the rating:
    D/E 0.30 → 4, CR 0.50 → 2, QR 0.40 → 2, IC 3.0x → 3: mean 2.75 → 3 (2.33 → 2 without
    D/E). Altman Z is omitted for Financial Services, so its blend anchor is the neutral
    3. Mutation: the blend still scoring D/E against the (dropped) peer median."""
    bs = dict(nv._V_BS, totalCurrentAssets=17.5e9, totalCurrentLiabilities=35e9,
              cashAndCashEquivalents=7e9, netReceivables=7e9, totalDebt=11.4e9,
              totalStockholdersEquity=38e9)
    income = nv._quarters(*nv._NETWORKS["V"],
                          overrides={i: {"operatingIncome": 3e9, "interestExpense": 1e9}
                                     for i in range(4)})
    _hs, svc = sph._health_service(
        monkeypatch, RuntimeError("health check exploded"), profile=nv._V_PROFILE,
        bs=bs, income=income, bench=nv._FB_BENCH)
    snap, _ = await svc._compute_with_status("V")
    scores = {m.metric_key: m.score for m in snap.metrics}
    assert scores == {"debt_to_equity": 4, "current_ratio": 2, "quick_ratio": 2,
                      "interest_coverage": 3}, scores
    assert snap.weighted_score == pytest.approx(0.4 * 3 + 0.6 * 3)
    assert snap.rating == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker", ["", "ENVA"])
async def test_the_fallback_gates_a_missing_or_unlisted_ticker(monkeypatch, caplog, ticker):
    """Same rule on the fallback: no ticker → gated (WARNING), unlisted → gated; the D/E
    keeps its industry comparison. Mutation: the fallback deciding on the data alone."""
    with caplog.at_level(logging.INFO, logger=gate.logger.name):
        _hs, svc = sph._health_service(
            monkeypatch, RuntimeError("health check exploded"),
            profile=dict(nv._V_PROFILE, symbol=ticker), bs=dict(nv._V_BS),
            income=nv._quarters(*nv._NETWORKS["V"]), bench=nv._FB_BENCH)
        snap, _ = await svc._compute_with_status(ticker)
    assert [m.metric_key for m in snap.metrics] == ["debt_to_equity"]
    assert snap.metrics[0].name == "Debt-to-Equity (vs sector 1.80)"
    assert snap.rating == 0
    expected = logging.WARNING if not ticker else logging.INFO
    assert [r.levelno for r in _gate_records(caplog)] == [expected]


# ══════════════════════════════════════════════════════════════════════════════════════
# 4. The Overview's degraded card: the ticker is threaded, and its absence fails closed
# ══════════════════════════════════════════════════════════════════════════════════════


def _names(card) -> List[str]:
    return [m.name for m in card.metrics]


def test_the_overview_builder_without_a_ticker_fails_closed(caplog):
    """A caller that never passes the ticker (the pre-2026-10-08 signature) gates V's
    Interest Coverage and says so at WARNING. Mutation: defaulting to a network."""
    svc = StockOverviewService.__new__(StockOverviewService)
    with caplog.at_level(logging.INFO, logger=gate.logger.name):
        card = svc._build_health_snapshot(
            dict(nv._V_BS), nv._annual(*nv._NETWORKS["V"]), {}, {"interestCoverage": 40.0},
            {}, 6.3e11, sector=FS, industry=CREDIT)
    assert "Interest Coverage" not in _names(card)
    records = _gate_records(caplog)
    assert [r.levelno for r in records] == [logging.WARNING]
    assert "step=overview_fallback_health" in records[0].getMessage()


@pytest.mark.parametrize("ticker,kept", [("V", True), ("gpn", True), ("ENVA", False),
                                         (None, False)])
def test_build_snapshots_threads_the_ticker_to_the_health_fallback(ticker, kept):
    """`_build_snapshots` passes its ``ticker`` to the degraded Health builder. Mutation:
    dropping the keyword on either hop gates every network's Interest Coverage."""
    svc = StockOverviewService.__new__(StockOverviewService)
    snaps = svc._build_snapshots(
        [{}], [{"interestCoverage": 40.0}], [nv._annual(*nv._NETWORKS["V"])],
        [dict(nv._V_BS)], [{}], 100.0, 6.3e11, FS, industry=CREDIT, ticker=ticker,
    )
    health = next(s for s in snaps if s.category == "Financial Health")
    assert ("Interest Coverage" in _names(health)) is kept


def _call_keywords(func, callee: str) -> List[Dict[str, str]]:
    """Every call to ``<x>.<callee>(...)`` inside ``func``: its keyword → source text."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == callee):
            found.append({k.arg: ast.unparse(k.value) for k in node.keywords if k.arg})
    return found


def test_the_overview_response_passes_its_ticker_to_the_snapshots():
    """`_build_full_response` (which holds the request ticker) hands it to
    `_build_snapshots`. AST, not text: a comment cannot satisfy it. Mutation: the
    keyword dropped — every network's degraded card fails closed in production."""
    calls = _call_keywords(sos.StockOverviewService._build_full_response, "_build_snapshots")
    assert calls and all(c.get("ticker") == "ticker" for c in calls), calls
    calls = _call_keywords(sos.StockOverviewService._build_snapshots, "_build_health_snapshot")
    assert calls and all(c.get("ticker") == "ticker" for c in calls), calls


# ══════════════════════════════════════════════════════════════════════════════════════
# 5. The report collector: the ticker and the quarters reach the drill-down gate
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [True, False])
async def test_pass_two_withholds_a_networks_de_line_from_any_read(monkeypatch, failed):
    """`_fetch_dependent` (pass 2, after pass 1 settled `income_q`) resolves the company
    verdict BEFORE the history read (spec D1, 2026-10-09): a listed non-lender member
    compares no metric with the industry median, so it makes NO read — every line is
    empty and a Supabase blip cannot mark its build degraded (which would keep a clean
    report out of ticker_data_cache and the shared report caches). A gated member's read
    is made as before, a FAILED read's partial lines kept with its degraded mark. The
    fetch keeps its two-argument shape (other suites stub it). Mutation: the read made
    for V (degraded_sections then names the failed read), or the verdict resolved after
    the read."""
    nv._stub_pass_two_legs(monkeypatch)
    line = {"2024": 1.3, "2025": 1.4}
    reads: list = []

    def _fresh():
        return {"annual": {"debt_to_equity": dict(line), "roe": dict(line)},
                "quarterly": {"debt_to_equity": {"Q2'26": 1.2}},
                "levels": {"annual": {"debt_to_equity": "industry", "roe": "industry"},
                           "quarterly": {"debt_to_equity": "industry"}}}

    async def _history(industry, sector):
        reads.append((industry, sector))
        return BenchmarkLookupFailed(_fresh()) if failed else _fresh()

    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    coll.fmp = None
    monkeypatch.setattr(coll, "_fetch_sector_benchmark_history", _history)
    out = nv._pass_two_out("V", nv._quarters(*nv._NETWORKS["V"]), CREDIT, FS)
    await coll._fetch_dependent(out)
    hist = out.sector_benchmark_history
    assert reads == []
    assert hist["annual"] == {} and hist["quarterly"] == {}
    assert hist["levels"] == {"annual": {}, "quarterly": {}}
    assert out.non_lender_member is True and out.listed_non_lender is True
    assert out.degraded_sections == []

    # a gated member is read and keeps its D/E and ROE lines (and a failed read's mark)
    out = nv._pass_two_out("ENVA", nv._quarters(*_ZERO_FILLED_LENDERS["ENVA"]), CREDIT, FS)
    await coll._fetch_dependent(out)
    assert reads == [(CREDIT, FS)]
    assert out.sector_benchmark_history["annual"]["debt_to_equity"] == line
    assert out.sector_benchmark_history["annual"]["roe"] == line
    assert out.non_lender_member is False and out.listed_non_lender is False
    assert out.degraded_sections == (["sector_history:benchmarks"] if failed else [])

    # a listed member whose quarters read as a lender's is vetoed: gated, so read
    reads.clear()
    out = nv._pass_two_out("V", nv._quarters(10.0e9, 0.0, 8.5e9), CREDIT, FS)
    await coll._fetch_dependent(out)
    assert reads == [(CREDIT, FS)] and out.non_lender_member is False
    # still LISTED: its report verdicts read each card's own rows (`_snapshot_to_card`)
    assert out.listed_non_lender is True
    assert out.sector_benchmark_history["annual"]["roe"] == line


@pytest.mark.parametrize("history", [None, {}, [], "x", {"annual": None, "levels": "x"},
                                     {"annual": {"debt_to_equity": None}}])
def test_the_network_line_filter_never_raises_on_junk(history):
    """Mutation: an unguarded `.get` / `.pop` on a non-dict read crashes pass 2 — and the
    whole paid report — for V."""
    C._withhold_payment_network_lines(history, network=True, industry=CREDIT, sector=FS,
                                      ticker="V")


def test_the_network_line_filter_is_a_no_op_for_a_non_network():
    history = {"annual": {"debt_to_equity": {"2025": 1.4}},
               "levels": {"annual": {"debt_to_equity": "industry"}}}
    for network in (False, None, 1, "yes"):
        C._withhold_payment_network_lines(history, network=network, industry=CREDIT,
                                          sector=FS, ticker="V")
        assert history["annual"] == {"debt_to_equity": {"2025": 1.4}}, network


@pytest.mark.asyncio
async def test_the_drill_down_without_a_ticker_keeps_the_de_line(monkeypatch, caplog):
    """No ticker → not a network (fail closed, WARNING): the D/E line stays, exactly as
    the card that also gated the company compares D/E. Mutation: a missing ticker treated
    as a network would withhold the line a gated card still compares."""
    with caplog.at_level(logging.INFO):
        hist = await nv._history_for(monkeypatch, ticker="",
                                     income_q=nv._quarters(*nv._NETWORKS["V"]))
    assert "debt_to_equity" in hist["annual"]
    for metric in _GATED:
        assert metric not in hist["annual"]
    assert [r.levelno for r in _gate_records(caplog)] == [logging.WARNING]


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker", _LISTED)
async def test_every_listed_networks_drill_down_withholds_the_four_lines(monkeypatch, ticker):
    hist = await nv._history_for(monkeypatch, ticker=ticker.lower(),
                                 income_q=nv._quarters(*nv._NETWORKS[ticker]))
    for metric in _ABSOLUTE:
        assert metric not in hist["annual"] and metric not in hist["quarterly"], metric
    # 2026-10-09 (NET-4): every other line goes too (the round-4 fixture carries only these
    # four, so a non-gated line is checked through the reason table).
    assert C._withheld_peer_line_reason(
        "roe", "industry", CREDIT, FS, network=True) == "payment_network"


@pytest.mark.asyncio
async def test_the_drill_down_of_a_bank_ignores_the_list(monkeypatch):
    """V filed as a bank: its D/E line is drawn, its liquidity lines are withheld as
    "not_applicable". Mutation: the list consulted outside the mixed industry."""
    hist = await nv._history_for(monkeypatch, ticker="V", industry="Banks - Diversified",
                                 income_q=nv._quarters(*nv._NETWORKS["V"]))
    assert hist["annual"]["debt_to_equity"] == {"2024": 1.3, "2025": 1.4, r4._CUR: 1.5}
    for metric in _GATED:
        assert metric not in hist["annual"]
