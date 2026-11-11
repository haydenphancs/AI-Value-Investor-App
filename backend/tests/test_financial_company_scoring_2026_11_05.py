"""How banks, insurers and lenders are scored (owner decisions, 2026-11-05).

The method was built for operating companies. Deposits and policy reserves are a bank's and an
insurer's raw material, so free cash flow swings with them (JPMorgan's FCF / net income ran -2.6
to +2.8 over five years, BlackRock's held 0.64-0.85), EBITDA means nothing, and FMP's revenue
for a bank is gross interest income. `score_excluded_rows` stops those rows counting, and the
same rule keeps them out of the style nudge and the model's evidence. Decisions: debt comes off
insurers too ("Remove it"), asset managers keep their rows ("Keep their rows"), the moat stays
on the sector peers until a later release, and the headline keeps its number with a sentence
("Keep score, add a sentence"; moved under the cards and the moat, "good. but make it short
and simple").

Shapes are real FMP figures read on 2026-11-05. No network.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_growth_deepcheck_backend as gd
import test_non_lender_members_peer_free_2026_10_09 as nl
import test_snapshot_cards_2026_10_07_profitability_health as sph
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services import financials_metric_gate as gate
from app.services import growth_snapshot_service as gss
from app.services import profit_power_service as pp
from app.services import profitability_snapshot_service as ps
from app.services import valuation_snapshot_service as vss
from app.services.agents import narrative_prompts as np_
from app.services.agents import persona_scoring as scoring
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.persona_config import get_persona_config
from app.services.stock_overview_service import StockOverviewService
from test_ticker_report_schema_parity import _no_live_benchmark_lookup  # noqa: F401

BANK = "Banks - Diversified"
EXCLUDED = gate.SCORE_EXCLUDED_ROWS


# ── who the rule covers ──────────────────────────────────────────────────────

@pytest.mark.parametrize("industry", [
    "Banks", "Banks - Diversified", "Banks - Regional", "Banks—Regional", "banks - regional",
    "Insurance - Diversified", "Insurance - Life", "Insurance - Property & Casualty",
    "Insurance - Reinsurance", "Insurance - Specialty",
    "Investment - Banking & Investment Services",
    "Financial - Credit Services", "Financial - Mortgages", "REIT - Mortgage",
    "Financial - Conglomerates", "Financial - Diversified",
])
def test_the_rows_stop_counting_for_these_industries(industry):
    assert gate.score_excluded_rows(industry) == EXCLUDED


@pytest.mark.parametrize("industry", [
    "Asset Management", "Asset Management - Global", "Asset Management - Bonds",
    "Asset Management - Income", "Asset Management - Leveraged",
    "Asset Management - Cryptocurrency",
    "Insurance - Brokers", "Financial - Data & Stock Exchanges",
    "Software - Infrastructure", "Beverages - Non-Alcoholic", "REIT - Retail",
    "", None, 5,
])
def test_fee_businesses_operating_companies_and_unknowns_keep_every_row(industry):
    assert gate.score_excluded_rows(industry) == frozenset()


@pytest.mark.parametrize("ticker", ["GS", "MS", "SCHW", " gs ", "HOOD", "JEF", "NMR"])
def test_a_named_broker_dealer_is_covered(ticker):
    assert gate.score_excluded_rows("Financial - Capital Markets", ticker=ticker) == EXCLUDED


@pytest.mark.parametrize("ticker", ["MARA", "RIOT", "NAKA", "BMNR", "MKTX", "TW", "MIAX", "EVR", None, ""])
def test_the_rest_of_capital_markets_keeps_every_row(ticker):
    """Bitcoin miners, crypto treasuries, venues and advisers share FMP's industry with the
    brokers. A miner has no deposits, and "these rows do not fit" would be false for it."""
    assert gate.score_excluded_rows("Financial - Capital Markets", ticker=ticker) == frozenset()


def test_every_listed_broker_is_really_in_that_industry():
    universe = json.loads(Path("data/industry_universe.json").read_text(encoding="utf-8"))
    names = next(i["tickers"] for i in universe["industries"] if i["industry"] == "Financial - Capital Markets")
    assert gate.CAPITAL_MARKETS_BROKERS <= set(names)


def test_a_listed_non_lender_member_keeps_every_row():
    assert gate.score_excluded_rows("Financial - Credit Services", network=True) == frozenset()
    assert gate.score_excluded_rows("Financial - Credit Services", network=None) == EXCLUDED


def test_every_financial_industry_in_the_universe_is_decided():
    """A new FMP industry name must not silently fall on the wrong side."""
    universe = json.loads(Path("data/industry_universe.json").read_text(encoding="utf-8"))
    names = {i["industry"] for i in universe["industries"] if i.get("sector") == "Financial Services"}
    spared = {n for n in names if not gate.score_excluded_rows(n)}
    assert spared == {
        "Insurance - Brokers", "Financial - Data & Stock Exchanges", "Shell Companies",
        "Asset Management", "Asset Management - Cryptocurrency", "Asset Management - Global",
        "Financial - Capital Markets",      # the broker list is the only way in
    }


# ── the words ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "P/FCF 51.06 is 5.86x industry avg 8.71", "EV/EBITDA is rich", "P/S of 3.2",
    "Debt-to-Equity 0.17 shows exceptional financial strength", "low D/E", "debt to equity",
    "strong free cash flow", "FCF yield of 4%", "Gross margin of 23%", "gross profit margin",
    "price-to-sales of 3", "price to free cash flow",
    "Operating cash flow of -$147.8B", "OCF fell", "Total debt of $437B", "Net debt climbed 12%",
    "Leverage at 11x equity is a risk",
])
def test_these_name_a_left_out_measure(text):
    assert gate.names_excluded_metric(text)


@pytest.mark.parametrize("text", [
    "P/E 14.5 is 1.28x industry", "ROE 11.86% is 0.74x industry", "P/B 1.66",
    "Net margin of 22%", "Operating margin of 16%", "Cost Advantage moat 7.3 vs peer 5.9",
    "cash pile of $300 billion", "", None, 7,
])
def test_these_do_not(text):
    assert not gate.names_excluded_metric(text)


def test_a_thesis_side_is_never_emptied_by_the_filter():
    assert gate.without_excluded_metrics(["ROE is high", "FCF is strong"]) == ["ROE is high"]
    assert gate.without_excluded_metrics(["FCF is strong"]) == ["FCF is strong"]
    assert gate.without_excluded_metrics([]) == []


# ── the Price card ───────────────────────────────────────────────────────────

JPM_FR = {
    "priceToEarningsRatioTTM": 14.13, "priceToBookRatioTTM": 2.43, "priceToSalesRatioTTM": 2.97,
    "priceToFreeCashFlowRatioTTM": -10.65, "enterpriseValueMultipleTTM": 19.56,
}
MEDIANS = {"pe_ratio": 12.0, "pb_ratio": 1.2, "ps_ratio": 2.5, "pfcf_ratio": 8.0, "ev_ebitda": 9.0}


def _price(fr=JPM_FR, bench=MEDIANS, excluded=EXCLUDED, **kw):
    return vss.build_price_snapshot(
        fr=dict(fr), km={}, cf={}, inc={}, bs={}, profile={}, bench=dict(bench),
        excluded_rows=excluded, **kw,
    )


def _row(card, key):
    return next(m for m in card.metrics if m.metric_key == key)


def test_a_banks_price_card_counts_only_pe_and_pb():
    card = _price()
    for key in ("ps", "pfcf", "ev_ebitda"):
        row = _row(card, key)
        assert (row.value, row.score, row.peer_level) == ("N/M", None, None)
        assert "avg" not in row.name
    pe, pb = _row(card, "pe").score, _row(card, "pb").score
    assert card.weighted_score == round((pe * 0.25 + pb * 0.15) / 0.40, 3)


def test_a_left_out_row_is_no_vote_at_all():
    """Mutation: a neutral 3 at its old weight would pull this card towards 3."""
    cheap = {**JPM_FR, "priceToEarningsRatioTTM": 5.0, "priceToBookRatioTTM": 0.4}
    card = _price(fr=cheap)
    assert _row(card, "pe").score == 5 and _row(card, "pb").score == 5
    assert card.weighted_score == 5.0 and card.rating == 5


def test_the_operating_path_is_untouched():
    plain = vss.build_price_snapshot(
        fr=dict(JPM_FR), km={}, cf={}, inc={}, bs={}, profile={}, bench=dict(MEDIANS),
    )
    assert _row(plain, "ev_ebitda").value != "N/M"
    assert plain.weighted_score == round(
        sum(s * w for s, w in (
            (_row(plain, "pe").score, 0.25), (_row(plain, "pb").score, 0.15),
            (_row(plain, "ps").score, 0.15), (3, 0.20), (_row(plain, "ev_ebitda").score, 0.25),
        )), 3,
    )


@pytest.mark.parametrize("fr, bench", [
    ({**JPM_FR, "priceToEarningsRatioTTM": -8.0}, MEDIANS),        # a loss: no P/E vote
    ({**JPM_FR, "priceToBookRatioTTM": None}, MEDIANS),            # no P/B
    (JPM_FR, {**MEDIANS, "pb_ratio": None}),                       # no P/B median
    (JPM_FR, {}),                                                  # no peers at all
])
def test_fewer_than_two_judged_rows_is_no_rating(fr, bench):
    card = _price(fr=fr, bench=bench)
    assert card.rating == 0 and card.weighted_score is None


def test_a_real_valuation_build_passes_the_rule(monkeypatch):
    lookup = nl._Recorder(value=12.0)
    svc = nl._val_svc(monkeypatch, "AXP", lookup)
    snap, degraded = asyncio.run(svc._compute_with_status("AXP"))
    assert _row(snap, "pfcf").value == "N/M" and degraded == []


def test_a_listed_network_keeps_its_rows_in_a_real_build(monkeypatch):
    svc = nl._val_svc(monkeypatch, "V", nl._Recorder(value=12.0))
    snap, _ = asyncio.run(svc._compute_with_status("V"))
    assert _row(snap, "pfcf").value != "N/M"


# ── the Profitability card ───────────────────────────────────────────────────

def test_a_lenders_gross_margin_does_not_count(monkeypatch):
    svc = nl._prof_svc(monkeypatch, "AXP", nl._Recorder(value=0.20))
    snap, _ = asyncio.run(svc._compute_with_status("AXP"))
    gross = _row(snap, "gross_margin")
    assert (gross.value, gross.score, gross.peer_level) == ("N/M", None, None)
    assert gross.name == "Gross Margin"
    scored = [m.score for m in snap.metrics if m.score is not None]
    assert len(scored) == 4


def test_a_networks_gross_margin_still_counts(monkeypatch):
    svc = nl._prof_svc(monkeypatch, "V", nl._Recorder(value=0.20))
    snap, _ = asyncio.run(svc._compute_with_status("V"))
    assert _row(snap, "gross_margin").value != "N/M"
    assert _row(snap, "gross_margin").score is not None


# ── the Growth card ──────────────────────────────────────────────────────────

def _point(yoy, peer=None):
    return SimpleNamespace(yoy_change_percent=yoy, sector_average_yoy=peer)


def _growth_card(monkeypatch, industry, symbol="BAC", degraded=()):
    growth = SimpleNamespace(
        industry=industry,
        revenue_annual=[_point(-0.5)], eps_annual=[_point(18.6)],
        free_cash_flow_annual=[_point(243.2)], operating_profit_annual=[_point(28.9)],
    )

    class _Service:
        async def get_growth_with_status(self, ticker):
            return growth, list(degraded)

    import app.services.growth_service as gs
    monkeypatch.setattr(gs, "get_growth_service", lambda: _Service())
    svc = gss.GrowthSnapshotService.__new__(gss.GrowthSnapshotService)
    return asyncio.run(svc._compute_with_status(symbol))


def test_a_banks_fcf_growth_does_not_count(monkeypatch):
    """BAC's +243.2% read as the best row on its Growth card."""
    card, _ = _growth_card(monkeypatch, BANK)
    fcf = _row(card, "fcf_growth")
    assert (fcf.value, fcf.score) == ("N/M", None)
    scores = {k: _row(card, k).score for k in ("revenue_growth", "eps_growth", "operating_income_growth")}
    expected = (
        scores["revenue_growth"] * 0.3 + scores["eps_growth"] * 0.3
        + scores["operating_income_growth"] * 0.2
    ) / 0.8
    assert card.weighted_score == round(expected, 3)


def test_an_operating_company_keeps_fcf_growth(monkeypatch):
    card, _ = _growth_card(monkeypatch, "Software - Infrastructure")
    assert _row(card, "fcf_growth").value == "+243.2%"


def test_a_listed_network_keeps_fcf_growth(monkeypatch):
    card, _ = _growth_card(monkeypatch, "Financial - Credit Services", symbol="V")
    assert _row(card, "fcf_growth").value == "+243.2%"


def test_a_failed_profile_leg_is_a_degraded_build_not_a_verdict(monkeypatch):
    card, degraded = _growth_card(monkeypatch, None, degraded=["profile"])
    assert _row(card, "fcf_growth").value == "+243.2%"
    assert "profile" in degraded, "a degraded build is never stored"


def test_the_growth_response_carries_the_industry(monkeypatch):
    fmp = gd._FakeFMP(profile={"symbol": "BAC", "sector": "Financial Services", "industry": BANK})
    response, _ = asyncio.run(gd._service(monkeypatch, fmp, gd._yoy_metrics_lookup())._build_growth("BAC"))
    assert response.industry == BANK


# ── the Overview fallbacks ───────────────────────────────────────────────────

def _overview():
    return StockOverviewService.__new__(StockOverviewService)


def test_the_growth_fallback_leaves_fcf_out_of_its_rating():
    base = dict(inc0={"revenue": 100.0, "operatingIncome": 10.0}, inc1={"revenue": 100.0, "operatingIncome": 10.0},
                cf0={"freeCashFlow": 500.0}, cf1={"freeCashFlow": 100.0}, km={}, key_metrics=[])
    card = _overview()._build_growth_snapshot(**base, excluded_rows=EXCLUDED)
    assert card.metrics[2].value == "N/M"
    assert card.rating == 2, "flat revenue and profit, with +400% FCF growth out of the average"
    assert _overview()._build_growth_snapshot(**base).rating == 5


def test_the_price_fallback_shows_nm():
    card = _overview()._build_valuation_snapshot(dict(JPM_FR), {}, {}, {}, {}, {}, excluded_rows=EXCLUDED)
    assert _row(card, "pfcf").value == "N/M" and card.rating == 0


# ── the style nudge ──────────────────────────────────────────────────────────

BRK_SIGNALS = {
    "fcf": 25042000000.0, "roe": 11.9, "roic": 4.2, "mkt_cap": 1223929379887.0, "mos_pct": None,
    "eps_cagr": 1.3, "pe_ratio": 14.35, "moat_score": 5.9, "net_income": 66968000000.0,
    "debt_equity": 0.17, "gross_margin": 23.5, "revenue_cagr": 2.2, "revenue_growth": 0.0,
    "gross_margin_prev": 23.3,
}


def _partial(**over):
    signals = dict(BRK_SIGNALS)
    signals.update(dict.fromkeys(C._STYLE_SIGNALS_EXCLUDED))
    signals["excluded_rows"] = sorted(EXCLUDED)
    signals.update(over)
    return signals


def test_debt_no_longer_moves_berkshires_buffett_nudge():
    assert scoring.style_fit_adjustment("warren_buffett", BRK_SIGNALS) > 2
    assert scoring.style_fit_adjustment("warren_buffett", _partial()) == pytest.approx(-1.8, abs=0.05)


@pytest.mark.parametrize("persona", ["michael_burry", "bill_ackman"])
def test_one_signal_is_not_a_personas_read(persona):
    """Burry's P/E alone would swing the score by up to 10 points."""
    assert scoring.style_fit_adjustment(persona, _partial()) == 0.0


def test_an_operating_company_keeps_its_single_signal():
    signals = {"pe_ratio": 8.0}
    assert scoring.style_fit_adjustment("michael_burry", signals) == 10.0


def test_lynch_needs_growth_to_read_at_all():
    assert scoring.style_fit_adjustment("peter_lynch", _partial(
        pe_ratio=10.0, eps_cagr=None, revenue_cagr=None, revenue_growth=None,
    )) == 0.0
    assert scoring.style_fit_adjustment("peter_lynch", _partial(pe_ratio=10.0, eps_cagr=20.0)) != 0.0


def test_woods_acceleration_needs_one_revenue_basis():
    fast = dict(revenue_growth=2.0, revenue_cagr=30.0)
    full = scoring.style_fit_adjustment("cathie_wood", {**BRK_SIGNALS, **fast})
    partial = scoring.style_fit_adjustment("cathie_wood", _partial(**fast))
    assert partial < full


def test_berkshire_lands_where_the_owner_saw_it():
    """The measured table: 45.8 today, 44.7 with every decision taken."""
    def vital(value):
        return {"score": {"value": value}}

    report = {"_scoring_inputs": {
        "moat": vital(5.9), "macro": vital(3.6), "insider": vital(4.9), "revenue": vital(0.9),
        "forecast": vital(5.3), "valuation": vital(2.5), "profitability": vital(5.9),
        "financial_health": vital(None), "wall_street": vital(None),
        "capital_allocation": vital(5.2), "_style_signals": _partial(),
    }}
    assert scoring.compute_quality_score("warren_buffett", report) == 44.7


# ── the collector ────────────────────────────────────────────────────────────

def _out(industry=BANK, **extra):
    return SimpleNamespace(
        ticker="JPM", persona_key="warren_buffett", non_lender_member=False,
        profile={"industry": industry, "sector": "Financial Services"}, **extra,
    )


def test_the_collector_reads_one_predicate():
    assert C._score_excluded(_out()) == EXCLUDED
    assert C._score_excluded(_out("Software - Infrastructure")) == frozenset()
    assert C._score_excluded(_out("Financial - Credit Services")) == EXCLUDED
    assert C._score_excluded(SimpleNamespace(
        profile={"industry": "Financial - Credit Services"}, non_lender_member=True,
    )) == frozenset()
    assert C._score_excluded(SimpleNamespace(profile=None)) == frozenset()


def _report(monkeypatch, industry):
    import test_ticker_report_schema_parity as parity

    out = parity._make_collected_data()
    out.profile["industry"] = industry
    out.profile["sector"] = "Financial Services"
    coll = C.TickerReportDataCollector()
    return out, coll.assemble_report(out, parity.stage_a_fallback())


def test_the_style_signals_leave_the_rows_out(monkeypatch):
    _, report = _report(monkeypatch, BANK)
    signals = report["_scoring_inputs"]["_style_signals"]
    assert signals["excluded_rows"] == sorted(EXCLUDED)
    for key in C._STYLE_SIGNALS_EXCLUDED:
        assert signals[key] is None


def test_an_operating_companys_style_signals_are_unchanged(monkeypatch):
    _, report = _report(monkeypatch, "Software - Infrastructure")
    assert "excluded_rows" not in report["_scoring_inputs"]["_style_signals"]


def test_the_evidence_never_prints_a_left_out_figure(monkeypatch):
    out, _ = _report(monkeypatch, BANK)
    out.computed.update(gross_margin=56.08, debt_equity=1.28, ev_ebitda=13.61, pfcf_ratio=32.57, pe_ratio=14.15)
    text = C.build_financial_context(out)
    for needle in ("Gross Margin:", "D/E:", "Free CF:", "Operating CF:", "Total Debt:",
                   "EV/EBITDA:", "P/FCF:", "32.57", "13.61", "56.08"):
        assert needle not in text, needle
    assert C._EXCLUDED_CONTEXT_LINE in text
    assert "P/E: 14.15" in text


def test_an_operating_companys_evidence_is_unchanged(monkeypatch):
    out, _ = _report(monkeypatch, "Software - Infrastructure")
    out.computed.update(gross_margin=56.08, debt_equity=1.28, ev_ebitda=13.61, pfcf_ratio=32.57)
    text = C.build_financial_context(out)
    for needle in ("Gross Margin:", "D/E: 1.28", "EV/EBITDA: 13.61", "P/FCF: 32.57"):
        assert needle in text, needle
    assert C._EXCLUDED_CONTEXT_LINE not in text


def test_the_card_values_block_hides_the_debt_row_but_the_card_keeps_it():
    snap = SnapshotItemResponse(category="Financial Health", rating=0, metrics=[
        SnapshotMetricResponse(name="Debt-to-Equity (vs sector 0.22)", value="0.17", metric_key="debt_to_equity", score=4),
    ])
    empty = dict.fromkeys(("snap_profitability", "snap_growth", "snap_valuation"))
    bank = _out(snap_health=snap, **empty)
    other = _out("Software - Infrastructure", snap_health=snap, **empty)
    assert "0.17" not in C._format_snapshot_card_values(bank)
    assert "0.17" in C._format_snapshot_card_values(other)
    assert snap.metrics, "the row stays on the card: it is the Health card's only row"


def test_a_left_out_row_carries_no_history(monkeypatch):
    """The drill-down would print the multiple the row refuses to show."""
    history = {"pfcf": {"unit": "x", "annual": [{"period": "2025", "value": 51.0}], "quarterly": []}}
    monkeypatch.setattr(C, "_resolve_history_key", lambda name: "pfcf")
    rows = [
        SnapshotMetricResponse(name="P/FCF", value="N/M", metric_key="pfcf"),
        SnapshotMetricResponse(name="ROE", value="N/M", metric_key="roe"),
    ]
    card = C._snapshot_to_card(
        "Valuation", SnapshotItemResponse(category="Price", rating=3, metrics=rows),
        history_lookup=history,
    )
    keyed = {m["label"]: m for m in card["metrics"]}
    assert "history_key" not in keyed["P/FCF"]
    assert keyed["ROE"].get("history_key") == "pfcf", "a negative-equity ROE keeps its history"


def test_stage_a_bullets_are_filtered():
    thesis = {"bull_case": ["ROE 18%", "Debt-to-Equity 0.17 shows strength"], "bear_case": ["P/FCF 51", "P/E 14"]}
    clean = C._sanitize_thesis(thesis, drop_excluded=True)
    assert clean == {"bull_case": ["ROE 18%"], "bear_case": ["P/E 14"]}
    assert C._sanitize_thesis(thesis)["bull_case"] == thesis["bull_case"]


# ── the prompts ──────────────────────────────────────────────────────────────

def _shell(excluded=True, health=None):
    signals = {"excluded_rows": sorted(EXCLUDED)} if excluded else {}
    return {"_scoring_inputs": {
        "_style_signals": signals, "financial_health": {"score": {"value": health}},
    }}


def test_the_lens_skips_what_the_score_left_out():
    buffett = get_persona_config("warren_buffett")
    lens = np_._lens_directive(buffett, want_bull=True, want_metrics=True, excluded=True)
    for word in ("debt-to-equity", "gross margin", "interest coverage", "free cash flow"):
        assert word not in lens.lower(), word
    assert "ROE/ROIC above 15%" in lens
    plain = np_._lens_directive(buffett, want_bull=True, want_metrics=True)
    assert "debt-to-equity below 0.5" in plain


def test_the_trajectory_and_digest_skip_the_rows():
    shell = _shell()
    shell["fundamental_metrics"] = [{"title": "Health", "star_rating": 0, "metrics": [
        {"label": "Debt-to-Equity (vs sector 0.22)", "value": "0.17"},
        {"label": "Return on Equity (ROE)", "value": "11.86%"},
    ]}]
    digest = " ".join(np_._digest_fundamentals(shell))
    assert "0.17" not in digest and "11.86" in digest
    plain = {**shell, "_scoring_inputs": {"_style_signals": {}}}
    assert "0.17" in " ".join(np_._digest_fundamentals(plain))


def test_the_trajectory_skips_a_left_out_row():
    def row(label):
        return {"label": label, "value": "1", "annual_history": [
            {"period": str(y), "value": float(y - 2015)} for y in range(2016, 2026)
        ]}

    cards = [{"title": "Health", "metrics": [row("Debt-to-Equity"), row("Return on Equity (ROE)")]}]
    shell = {**_shell(), "fundamental_metrics": cards}
    text = np_._fundamentals_trajectory_block(shell)
    assert "Debt-to-Equity" not in text and "Return on Equity" in text
    plain = {"fundamental_metrics": cards}
    assert "Debt-to-Equity" in np_._fundamentals_trajectory_block(plain)


def test_the_note_names_the_rows_it_means():
    assert np_._financial_score_note(_shell()) == (
        "P/S, P/FCF, EV/EBITDA, gross margin and FCF growth do not fit financial companies, so "
        "these cards do not score them. Financial Health is not rated."
    )


def test_a_measured_health_card_is_not_named():
    assert "Financial Health" not in np_._financial_score_note(_shell(health=6.0))


def test_an_operating_company_gets_no_note():
    assert np_._financial_score_note(_shell(excluded=False)) == ""
    assert np_._financial_score_note({}) == ""


def _jobs(shell, **extra):
    persona = get_persona_config("warren_buffett")
    base = {
        "fundamental_metrics": [], "overall_assessment": {"text": ""},
        "moat_competition": {}, "symbol": "JPM", "company_name": "JPMorgan",
    }
    return {j.label: j for j in np_.build_narrative_jobs(persona, "E", {**base, **shell, **extra})}


def test_the_note_sits_under_the_cards_on_real_text_and_the_fallback():
    job = _jobs(_shell())["overall_assessment_text"]
    assert job.postprocess("A bank.").startswith("A bank. P/S, P/FCF")
    assert "do not score them" in job.fallback_value


def test_the_executive_summary_stays_a_company_overview():
    """The financial line stays under the cards. `tests/test_score_coverage_2026_11_06.py` owns
    the one line the summary can carry, so every vital is rated here."""
    vitals = {name: {"score": {"value": 6.0}} for name in scoring.PERSONA_WEIGHTS["warren_buffett"]}
    shell = _shell()
    shell["_scoring_inputs"] = {**vitals, **shell["_scoring_inputs"]}
    job = _jobs(shell)["executive_summary_text"]
    assert job.postprocess is None
    assert job.fallback_value == np_.FALLBACK["executive_summary_text"]


def test_the_operating_path_keeps_its_job():
    insight = _jobs(_shell(excluded=False))["overall_assessment_text"]
    assert insight.postprocess is None
    assert insight.fallback_value == np_.FALLBACK["overall_assessment_text"]


# ── the versions ─────────────────────────────────────────────────────────────

def test_the_cards_cannot_serve_old_rows():
    from app.services import growth_service as gs
    assert vss._SNAPSHOT_PAYLOAD_VERSION == 10
    assert ps._SNAPSHOT_PAYLOAD_VERSION == 6
    assert gss._SNAPSHOT_PAYLOAD_VERSION == 6
    assert gs._GROWTH_PAYLOAD_VERSION == 8
    assert pp._PP_PAYLOAD_VERSION == 10


# ── wiring: the rule reaches each surface ────────────────────────────────────

@pytest.mark.parametrize("persona", sorted(scoring.PERSONA_WEIGHTS))
def test_no_persona_lens_steers_towards_a_left_out_measure(persona):
    config = get_persona_config(persona)
    lens = np_._lens_directive(
        config, want_bull=True, want_bear=True, want_metrics=True, want_flags=True, excluded=True,
    )
    assert not np_._EXCLUDED_LENS_RE.search(lens), lens


class _FakeGemini:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    async def generate_json(self, *, prompt, **_kw):
        self.prompts.append(prompt)
        return {"text": json.dumps(self.answer), "finish_reason": "STOP"}


def _thesis_report(excluded=True):
    return {
        **_shell(excluded=excluded), "symbol": "JPM", "company_name": "JPMorgan",
        "core_thesis": {"bull_case": ["ROE is high", "Stage A bull"], "bear_case": ["P/E is rich", "Stage A bear"]},
        "fundamental_metrics": [{"title": "Valuation", "star_rating": 2, "metrics": [
            {"label": "P/E", "value": "14.13"},
        ]}],
    }


def test_the_synthesis_drops_a_left_out_bullet_for_a_bank():
    gemini = _FakeGemini({
        "bull_case": ["ROE 18% leads peers", "Debt-to-Equity 0.17: exceptionally low leverage"],
        "bear_case": ["P/E 14 is rich", "P/FCF 51.06 is 5.86x industry avg 8.71"],
    })
    report = _thesis_report()
    asyncio.run(np_.synthesize_core_thesis(report, get_persona_config("warren_buffett"), gemini, "E"))
    assert report["core_thesis"] == {"bull_case": ["ROE 18% leads peers"], "bear_case": ["P/E 14 is rich"]}
    assert "debt-to-equity below 0.5" not in gemini.prompts[0]


def test_an_operating_companys_synthesis_keeps_every_bullet():
    gemini = _FakeGemini({
        "bull_case": ["ROE 18%", "Debt-to-Equity 0.17"], "bear_case": ["P/E 14", "P/FCF 51"],
    })
    report = _thesis_report(excluded=False)
    asyncio.run(np_.synthesize_core_thesis(report, get_persona_config("warren_buffett"), gemini, "E"))
    assert len(report["core_thesis"]["bull_case"]) == 2
    assert "debt-to-equity below 0.5" in gemini.prompts[0]


def test_a_stage_a_bullet_is_filtered_on_the_real_report_path(monkeypatch):
    import test_ticker_report_schema_parity as parity

    out = parity._make_collected_data()
    out.profile.update(industry=BANK, sector="Financial Services")
    shell = parity.stage_a_fallback()
    shell["core_thesis"] = {"bull_case": ["ROE 18%", "FCF is strong"], "bear_case": ["P/E 14"]}
    report = C.TickerReportDataCollector().assemble_report(out, shell)
    assert report["core_thesis"]["bull_case"] == ["ROE 18%"]


def test_the_whole_style_signals_dict_is_identical_for_an_operating_company(monkeypatch):
    import test_ticker_report_schema_parity as parity

    out = parity._make_collected_data()
    signals = C.TickerReportDataCollector().assemble_report(out, parity.stage_a_fallback())[
        "_scoring_inputs"]["_style_signals"]
    assert "excluded_rows" not in signals
    assert signals["fcf"] == out.computed.get("fcf")
    assert signals["debt_equity"] == out.computed.get("debt_equity")
    assert signals["gross_margin"] == out.computed.get("gross_margin")


def test_a_dcf_never_stands_in_for_a_financial_companys_unrated_card():
    """A DCF rests on free cash flow. Mutation: a lender with a DCF votes its upside."""
    vital = C._build_valuation_vital(100.0, None, None, None)
    assert vital["score"]["value"] is None
    assert "mos_pct" in C._STYLE_SIGNALS_EXCLUDED


def test_a_row_outside_the_rule_keeps_its_history(monkeypatch):
    history = {"pfcf": {"unit": "x", "annual": [{"period": "2025", "value": 51.0}], "quarterly": []}}
    monkeypatch.setattr(C, "_resolve_history_key", lambda name: "pfcf")
    rows = [SnapshotMetricResponse(name="P/FCF", value="51.06", metric_key="pfcf")]
    card = C._snapshot_to_card(
        "Valuation", SnapshotItemResponse(category="Price", rating=3, metrics=rows),
        history_lookup=history,
    )
    assert card["metrics"][0]["history_key"] == "pfcf"


def test_the_real_statement_figures_stay_out_of_a_banks_evidence(monkeypatch):
    """JPM's capex reads 0, so its operating cash flow IS the free one: -$147.8B."""
    out, _ = _report(monkeypatch, BANK)
    out.cash_flow = [{"operatingCashFlow": -147_782_000_000, "freeCashFlow": -147_782_000_000, "reportedCurrency": "USD"}]
    out.balance = [{"totalAssets": 4e12, "totalDebt": 942_400_000_000, "cashAndCashEquivalents": 3.4e11, "reportedCurrency": "USD"}]
    text = C.build_financial_context(out)
    assert "147.8" not in text and "942.4" not in text


def _growth_payload(monkeypatch, profile):
    fmp = gd._FakeFMP(profile=profile)
    response, _ = asyncio.run(gd._service(monkeypatch, fmp, gd._yoy_metrics_lookup())._build_growth(profile["symbol"]))
    return response


def test_a_banks_growth_chart_has_no_fcf_series(monkeypatch):
    bank = _growth_payload(monkeypatch, {"symbol": "BAC", "sector": "Financial Services", "industry": BANK})
    assert bank.free_cash_flow_annual == [] and bank.free_cash_flow_quarterly == []
    other = _growth_payload(monkeypatch, {"symbol": "KO", "sector": "Consumer Defensive", "industry": "Beverages - Non-Alcoholic"})
    assert other.free_cash_flow_annual


@pytest.mark.parametrize("failed", ["cash_flow", "income", "balance_sheet"])
def test_a_leg_only_the_left_out_rows_use_does_not_cost_a_bank_its_card(monkeypatch, failed):
    method = {"cash_flow": "get_cash_flow_statement", "income": "get_income_statement",
              "balance_sheet": "get_balance_sheet"}[failed]
    svc = nl._val_svc(monkeypatch, "AXP", nl._Recorder(value=12.0))
    setattr(svc.fmp, method, _raises)
    _, degraded = asyncio.run(svc._compute_with_status("AXP"))
    assert failed not in degraded


def test_an_operating_companys_failed_leg_still_counts(monkeypatch):
    svc = nl._val_svc(monkeypatch, "V", nl._Recorder(value=12.0))
    svc.fmp.get_cash_flow_statement = _raises
    _, degraded = asyncio.run(svc._compute_with_status("V"))
    assert "cash_flow" in degraded


async def _raises(*_a, **_k):
    raise RuntimeError("429")


def test_a_lost_cash_flow_leg_does_not_degrade_a_banks_growth_card(monkeypatch):
    card, degraded = _growth_card(monkeypatch, BANK, degraded=["annual_cashflow"])
    assert "annual_cashflow" not in degraded
    _, kept = _growth_card(monkeypatch, "Software - Infrastructure", degraded=["annual_cashflow"])
    assert "annual_cashflow" in kept


_ANNUAL_ROWS = [
    {"date": f"{y}-12-31", "fiscalYear": str(y), "period": "FY", "revenue": 100.0,
     "grossProfit": 60.0, "operatingIncome": 30.0, "netIncome": 20.0, "freeCashFlow": 15.0,
     "operatingCashFlow": 18.0}
    for y in range(2025, 2020, -1)
]


def _margins(monkeypatch, symbol):
    svc = nl._pp_service(monkeypatch, symbol, nl._Recorder(), annual=_ANNUAL_ROWS)
    response, _next, _degraded = asyncio.run(svc._build_profit_power(symbol))
    assert response.annual
    return response


def test_a_banks_margin_chart_has_no_gross_or_fcf_line(monkeypatch):
    """Report chat quoted these from here: "Gross 59.9%, FCF -52.8%" on a report calling them N/M."""
    response = _margins(monkeypatch, "AXP")
    for point in response.annual + response.quarterly:
        assert point.gross_margin is None and point.fcf_margin is None
        assert point.sector_average_gross_margin is None and point.sector_average_fcf_margin is None
    assert any(p.net_margin is not None for p in response.annual), "real margins stay"


class _PeerSeries(nl._Recorder):
    def get_benchmark_series(self, industry, sector, metrics, period_type):
        return {m: {str(y): {"value": 0.2, "level": "industry", "peer_group_name": "CS", "n": 60}
                    for y in range(2021, 2026)} for m in metrics}


def test_a_banks_chart_draws_no_peer_line_for_a_left_out_margin(monkeypatch):
    def peers():
        svc = nl._pp_service(monkeypatch, "AXP", _PeerSeries(), annual=_ANNUAL_ROWS)
        return asyncio.run(svc._build_profit_power("AXP"))[0].annual

    assert all(p.sector_average_gross_margin is None for p in peers())
    assert all(p.sector_average_fcf_margin is None for p in peers())
    monkeypatch.setattr(pp, "score_excluded_rows", lambda *a, **k: frozenset())
    assert any(p.sector_average_gross_margin is not None for p in peers()), "the fixture has peers"
    assert any(p.sector_average_fcf_margin is not None for p in peers()), "the fixture has peers"


def test_a_networks_margin_chart_keeps_its_lines(monkeypatch):
    assert any(p.gross_margin is not None for p in _margins(monkeypatch, "V").annual)


def test_a_chat_summary_for_a_bank_has_no_left_out_margin(monkeypatch):
    from app.services.chat_service import ChatService

    text = ChatService._format_profit_summary("AXP", _margins(monkeypatch, "AXP"))
    assert "Gross" not in text and "FCF" not in text


def test_a_dcf_upside_does_not_score_a_banks_valuation_on_the_real_path():
    import test_ticker_report_schema_parity as parity

    out = parity._make_collected_data()
    out.profile.update(industry=BANK, sector="Financial Services")
    out.snap_valuation = None
    out.computed.update(fair_value=200.0, upside_pct=50.0)
    C.TickerReportDataCollector()._build_sections(out)
    assert out.valuation_vital["score"]["value"] is None


def test_both_overview_fallbacks_are_wired():
    tree = ast.parse(Path("app/services/stock_overview_service.py").read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) in ("_build_growth_snapshot", "_build_valuation_snapshot")
    ]
    assert len(calls) == 2
    assert all("excluded_rows" in {kw.arg for kw in call.keywords} for call in calls)
