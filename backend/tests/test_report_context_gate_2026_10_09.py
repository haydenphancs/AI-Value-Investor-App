"""The financials gate on the report's MODEL context (2026-10-09).

`financials_metric_gate` takes a bank's, insurer's, broker-dealer's, asset manager's and
lender's current ratio, quick ratio and interest coverage off every card (meaningless for
them), and `CURATED_WITHHELD_ROWS` takes WU's three off every card (FMP's split and interest
expense are made up). The collector still put FMP's raw TTM current ratio into
`build_financial_context` — the evidence Stage A, Stage B and the deep door's agentic phase
all read — so the model could write "a current ratio of 3.75" beside a card that shows none.

Now both halves follow `company_metric_applicable` (ticker + industry + pass 2's company
verdict): `_compute_metrics` drops the gated raw values from `computed`, and
`build_financial_context` re-decides the gate on every build (a collection cached before the
fix still carries the raw value) and STATES the rows as not meaningful / not available,
digit-free, in place of a number. A company that keeps the rows keeps its line.

Each test's docstring names the mutation it catches. Hermetic: no FMP, no Supabase.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

import pytest

import test_card_networks_vs_lenders_2026_10_08 as nv
import test_snapshot_cards_2026_10_07_profitability_health as sph
from app.services import financials_metric_gate as gate
from app.services.agents import ticker_report_data_collector as C

FS = "Financial Services"
CREDIT = "Financial - Credit Services"
SOFTWARE = "Software - Infrastructure"

# Distinctive raw FMP TTM values: none of them may reach a gated company's context.
_RAW_CR, _RAW_QR, _RAW_IC = 4.37, 4.21, 0.29
_RAW_TEXTS = ("4.37", "4.21", "0.29")

_GATED_ROW = re.compile(r"current ratio|quick ratio|interest coverage", re.IGNORECASE)


def _collected(ticker: str, industry: Optional[str], *, sector: str = FS,
               network: bool = False, snap_health=None) -> C.CollectedTickerData:
    """A minimal collection the real `_compute_metrics` + `build_financial_context` accept,
    with FMP's raw TTM ratios carrying the distinctive values above."""
    out = C.CollectedTickerData(ticker=ticker, persona_key="warren_buffett")
    out.profile = {"companyName": f"{ticker} Corp", "symbol": ticker, "sector": sector,
                   "industry": industry, "mktCap": 1.4e11, "exchangeShortName": "NYSE"}
    out.quote = {"price": 70.0}
    out.income = [{"fiscalYear": "2025", "revenue": 8.0e10, "netIncome": 1.2e10},
                  {"fiscalYear": "2024", "revenue": 7.8e10, "netIncome": 1.0e10}]
    out.balance = [{"totalAssets": 2.4e11, "totalDebt": 5.8e10,
                    "cashAndCashEquivalents": 3.0e10}]
    out.cash_flow = [{"freeCashFlow": 9e9, "operatingCashFlow": 1e10}]
    out.ratios = [{"debtEquityRatio": 1.2, "priceEarningsRatio": 11.0,
                   "currentRatio": 7.12, "interestCoverageRatio": 1.85}]
    out.ratios_ttm = [{"currentRatioTTM": _RAW_CR, "quickRatioTTM": _RAW_QR,
                       "interestCoverageRatioTTM": _RAW_IC, "debtToEquityRatioTTM": 1.2}]
    out.historical = {"historical": [{"date": f"2026-09-{d:02d}", "close": 70.0}
                                     for d in range(1, 21)]}
    # Pass 2's company verdict (`resolve_payment_network`); False = never judged / a lender.
    out.non_lender_member = network
    out.snap_health = snap_health
    return out


def _context(out: C.CollectedTickerData) -> str:
    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    coll._compute_metrics(out)
    return C.build_financial_context(out)


# A gated row's name followed by a value: "Current Ratio: 4.37", "Interest Coverage (vs
# sector 1.50): 0.29x" (the CARD VALUES block joins a card's rows onto one line).
_GATED_VALUE = re.compile(
    r"(current ratio|quick ratio|interest coverage)[^:\n]*:\s*[-+$]?\d", re.IGNORECASE)
_WITHHELD_LINE = "NOT AVAILABLE for this company"


def _assert_no_gated_number(ctx: str) -> None:
    assert not _GATED_VALUE.findall(ctx), _GATED_VALUE.findall(ctx)
    for line in ctx.splitlines():
        if _GATED_ROW.search(line) and "CARD VALUES" not in line:
            if "NOT MEANINGFUL" in line or _WITHHELD_LINE in line:
                assert not re.search(r"\d", line), line
    for raw in _RAW_TEXTS:
        assert raw not in ctx, raw
    assert not any(line.startswith("Current Ratio:") for line in ctx.splitlines()), ctx


# ── Gated industries: no number, a stated "not meaningful" line ───────────────────────


@pytest.mark.parametrize("ticker,industry", [
    ("JPM", "Banks - Diversified"),
    ("USB", "Banks—Regional"),             # the older feed's em dash
    ("MET", "Insurance - Life"),
    ("GS", "Financial - Capital Markets"),
    ("BLK", "Asset Management"),
    ("AXP", CREDIT),                        # a lender in the mixed industry
    ("RKT", "Financial - Mortgages"),
])
def test_a_gated_financials_context_carries_no_liquidity_or_coverage_number(
        ticker, industry, caplog):
    """Bank, insurer, broker-dealer, asset manager, lender: the raw TTM current ratio is
    neither in `computed` nor in the context, interest coverage is dropped from `computed`,
    and one line states all three rows as not meaningful. Mutations: the
    `_drop_gated_context_values` call removed (computed keeps 4.37 / 0.29); the render-time
    `if CURRENT_RATIO not in gated` removed ("Current Ratio: N/A" beside the gate line)."""
    with caplog.at_level(logging.INFO):
        out = _collected(ticker, industry)
        ctx = _context(out)
    assert out.computed["current_ratio"] is None
    assert out.computed["interest_coverage"] is None
    _assert_no_gated_number(ctx)
    gate_lines = [line for line in ctx.splitlines() if _GATED_ROW.search(line)]
    assert gate_lines == [
        "Current ratio, quick ratio and interest coverage: NOT MEANINGFUL for a company in "
        "this industry — its funding and balance sheet make these ratios say nothing about "
        "its health; omitted from every card. Do not cite, estimate, compute or recall them."
    ], gate_lines
    assert _WITHHELD_LINE not in ctx
    messages = [r.getMessage() for r in caplog.records if "[report-context-gate]" in r.getMessage()]
    assert any("step=compute_metrics" in m and "current_ratio[not_meaningful]" in m
               and "interest_coverage[not_meaningful]" in m and f"ticker={ticker}" in m
               for m in messages), messages
    assert any("step=financial_context" in m for m in messages), messages


def test_an_insurance_broker_keeps_interest_coverage_only():
    """Brokers (MMC, AON, AJG) keep interest coverage — only the liquidity rows are gated.
    Mutation: the context gating every `GATED_METRICS` row by the liquidity answer."""
    out = _collected("MMC", "Insurance - Brokers")
    ctx = _context(out)
    assert out.computed["current_ratio"] is None
    assert out.computed["interest_coverage"] == _RAW_IC
    _assert_no_gated_number(ctx)
    assert ("Current ratio and quick ratio: NOT MEANINGFUL for a company in this industry — "
            "its funding and balance sheet make these ratios say nothing about its health; "
            "omitted from every card. Do not cite, estimate, compute or recall them.") in ctx
    assert "interest coverage" not in ctx.lower()


# ── WU: withheld in every industry, whatever pass 2 said ──────────────────────────────


@pytest.mark.parametrize("industry,network", [
    (CREDIT, True),          # production: a listed payment network
    (CREDIT, False),         # pass 2 never judged it (fail-closed default)
    (SOFTWARE, False),       # the curated fact follows the company, not the industry
    (None, False),           # no industry at all
])
@pytest.mark.parametrize("ticker", ["WU", " wu "])
def test_wus_context_carries_no_made_up_ratio_in_any_industry(ticker, industry, network):
    """WU's three rows are stated NOT AVAILABLE (never "not meaningful", never a number),
    and none of `CURATED_WITHHELD_ROWS`' reason text — which quotes FMP's made-up 3.75 /
    1.85 / 7.12 — reaches the model. Mutations: the curated table ignored (WU in Software
    keeps its 4.37); the withheld check after the industry check (WU in Credit Services
    reads "not meaningful")."""
    out = _collected(ticker, industry, network=network)
    ctx = _context(out)
    assert out.computed["current_ratio"] is None
    assert out.computed["interest_coverage"] is None
    _assert_no_gated_number(ctx)
    assert ("Current ratio, quick ratio and interest coverage: NOT AVAILABLE for this "
            "company — the available figures do not match the company's own filings; "
            "omitted from every card. Do not cite, estimate, compute or recall them.") in ctx
    assert "NOT MEANINGFUL" not in ctx
    for made_up in ("3.75", "1.85", "7.12", "FMP", "SEC", "CIK"):
        assert made_up not in ctx, made_up


# ── Companies that keep the rows ──────────────────────────────────────────────────────


@pytest.mark.parametrize("ticker,industry,sector,network", [
    ("MSFT", SOFTWARE, "Technology", False),
    ("V", CREDIT, FS, True),                # a listed network keeps its rows
    ("PMTS", CREDIT, FS, True),             # a listed fee business too
    ("XYZ", None, "Technology", False),     # unknown industry: the gate keeps every row
    ("XYZ", "", "Technology", False),
])
def test_a_company_that_keeps_the_rows_keeps_its_current_ratio_line(
        ticker, industry, sector, network):
    """The raw TTM current ratio stays in the context and interest coverage in `computed`;
    no gate line. Mutations: the gate ignoring `network`, or `peer_metric_applicable` in
    place of `company_metric_applicable` (V and PMTS gated)."""
    out = _collected(ticker, industry, sector=sector, network=network)
    ctx = _context(out)
    assert out.computed["current_ratio"] == _RAW_CR
    assert out.computed["interest_coverage"] == _RAW_IC
    assert f"Current Ratio: {_RAW_CR:.2f}" in ctx.splitlines()
    assert "NOT MEANINGFUL" not in ctx and _WITHHELD_LINE not in ctx


def test_a_listed_network_pass_two_never_judged_is_gated():
    """V with no pass-2 verdict (`non_lender_member` False: no sector, or a collection pass
    2 never judged) fails closed like every unlisted Credit Services member."""
    ctx = _context(_collected("V", CREDIT, network=False))
    _assert_no_gated_number(ctx)
    assert "NOT MEANINGFUL" in ctx


# ── A collection cached before the fix ────────────────────────────────────────────────


@pytest.mark.parametrize("ticker,industry,network,verdict", [
    ("JPM", "Banks - Diversified", False, "NOT MEANINGFUL"),
    ("WU", CREDIT, True, _WITHHELD_LINE),
])
def test_a_stale_cached_collection_still_prints_no_number(ticker, industry, network, verdict):
    """`computed` built by the old code (FMP's raw values still in it) — the render re-decides
    the gate instead of trusting `computed`. Mutation: the render-time
    `if CURRENT_RATIO not in gated` removed ("Current Ratio: 4.37")."""
    out = _collected(ticker, industry, network=network)
    out.computed = {"current_ratio": _RAW_CR, "interest_coverage": _RAW_IC,
                    "debt_equity": 1.2}
    ctx = C.build_financial_context(out)
    _assert_no_gated_number(ctx)
    assert verdict in ctx


# ── The whole context, with the REAL Financial Health card block ──────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker,industry,kept", [
    ("WU", CREDIT, False),
    ("JPM", "Banks - Diversified", False),
    ("V", CREDIT, True),
])
async def test_the_full_context_with_the_real_health_card(monkeypatch, ticker, industry, kept):
    """The Financial Health card from the real snapshot fallback (the Health Check raised)
    rides in the CARD VALUES block: for WU (a made-up 3.75 split on the balance sheet) and a
    bank the whole context names no gated row with a number; V (control) shows its card's
    current ratio, quick ratio and interest coverage AND keeps the raw line."""
    bs = dict(nv._V_BS, totalCurrentAssets=7.5e9, totalCurrentLiabilities=2.0e9)  # 3.75
    _hs, svc = sph._health_service(
        monkeypatch, RuntimeError("health check exploded"),
        profile={"symbol": ticker, "sector": FS, "industry": industry, "mktCap": 6e9},
        bs=bs, income=nv._quarters(*nv._NETWORKS["V"]), bench=nv._FB_BENCH)
    snap, _degraded = await svc._compute_with_status(ticker)
    ctx = _context(_collected(ticker, industry, network=kept, snap_health=snap))
    assert "CARD VALUES" in ctx and "Debt-to-Equity" in ctx
    if kept:
        numbered = _GATED_VALUE.findall(ctx)
        assert len(numbered) == 4, numbered       # the raw line + the card's three rows
        assert "3.75" in ctx
    else:
        _assert_no_gated_number(ctx)
        assert "3.75" not in ctx


# ── The stated lines themselves ───────────────────────────────────────────────────────


@pytest.mark.parametrize("gated", [
    {},
    {"current_ratio": "not_meaningful"},
    {"interest_coverage": "withheld"},
    {"current_ratio": "not_meaningful", "quick_ratio": "not_meaningful"},
    {"current_ratio": "withheld", "quick_ratio": "not_meaningful",
     "interest_coverage": "withheld"},
    {m: "not_meaningful" for m in gate.GATED_METRICS},
    {m: "withheld" for m in gate.GATED_METRICS},
])
def test_the_stated_lines_are_digit_free_and_name_each_row_once(gated):
    """One line per kind present, every gated row named exactly once, singular grammar for
    one row, no digit anywhere. Mutation: a row missing from `_CONTEXT_GATED_NAMES` (named
    zero times)."""
    lines = C._gated_context_lines(gated)
    kinds = set(gated.values())
    assert len(lines) == len(kinds)
    text = "\n".join(lines)
    assert not re.search(r"\d", text), text
    for metric, name in C._CONTEXT_GATED_NAMES:
        assert text.lower().count(name) == (1 if metric in gated else 0), (metric, text)
    for line in lines:
        n = len(_GATED_ROW.findall(line))
        assert line.endswith("recall it.") if n == 1 else line.endswith("recall them."), line


def test_the_context_gate_covers_every_gated_metric():
    """A new `GATED_METRICS` row must be named in the context's statement too. Mutation:
    a metric dropped from `_CONTEXT_GATED_NAMES`."""
    assert {m for m, _ in C._CONTEXT_GATED_NAMES} == set(gate.GATED_METRICS)
    assert {m for _, m in C._CONTEXT_GATED_KEYS} <= set(gate.GATED_METRICS)


def test_a_profile_that_is_not_a_dict_keeps_the_rows():
    """A malformed profile is an unknown industry (the gate keeps rows), never a crash."""
    out = _collected("XYZ", SOFTWARE, sector="Technology")
    out.profile = None  # type: ignore[assignment]
    assert C._context_gated_rows(out) == {}
    out.profile = ["not", "a", "dict"]  # type: ignore[assignment]
    assert C._context_gated_rows(out) == {}
