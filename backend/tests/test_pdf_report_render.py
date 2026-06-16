"""
Network-free smoke tests for the detailed-analysis PDF pipeline.

Covers the pure transforms (build_context, the SVG chart helpers, and Jinja
HTML rendering). The final WeasyPrint render is exercised only where the native
libs are available (skipped on dev machines lacking cairo/pango — e.g. an
arm64 Python against an Intel Homebrew — but runs in the Linux deploy image).
"""

import pytest

from app.services import pdf_charts
from app.services.pdf_report_service import (
    build_context,
    render_html,
    _persona_display,
)


def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False


def _sample() -> dict:
    return {
        "symbol": "ORCL",
        "company_name": "Oracle Corporation",
        "exchange": "NYSE",
        "agent": "buffett",
        "quality_score": 72,
        "_scoring_inputs": {
            "valuation": 47, "financial_health": 58, "revenue": 76, "forecast": 70,
            "moat": 84, "wall_street": 81, "insider": 52, "capital_allocation": 66,
            "macro": 63,
        },
        "core_thesis": {"bull_case": ["Cloud backlog"], "bear_case": ["Leverage"]},
        "executive_summary_text": "Fair-quality compounder.",
        "price_action": {"current_price": 172.4, "prices": [120.0, 150.0, 172.4],
                         "change_pct": 41.2, "window_label": "12M"},
        "wall_street_consensus": {
            "rating": "buy", "current_price": 172.4, "target_price": 205.0,
            "low_target": 150.0, "high_target": 260.0, "valuation_status": "Undervalued",
            "discount_percent": 15.9, "hedge_fund_flow_data": [],
            "momentum_upgrades": 6, "momentum_downgrades": 2, "momentum_maintains": 5,
            "analyst_strong_buy": 8, "analyst_buy": 22, "analyst_hold": 12,
            "analyst_sell": 2, "analyst_strong_sell": 1,
        },
        "moat_competition": {"dimensions": [
            {"name": "Switching", "score": 88, "peer_score": 70},
            {"name": "Brand", "score": 64, "peer_score": 72},
            {"name": "Scale", "score": 82, "peer_score": 78},
        ]},
        "critical_factors": [{"title": "Backlog", "description": "Watch RPO",
                              "severity": "high", "watch": "RPO growth"}],
        "macro_data": {
            "overall_threat_level": "Moderate", "headline": "Rates dominate.",
            "intelligence_brief": "Higher for longer.",
            "risk_factors": [
                {"category": "interest_rates", "title": "Financing costs", "impact": 0.6,
                 "description": "Costlier capex.", "trend": "stable", "severity": "elevated",
                 "sources": [
                     {"title": "Fed holds", "uri": "https://www.federalreserve.gov", "publisher": "Federal Reserve"},
                     {"title": "Rates note", "uri": "https://www.reuters.com/markets/rates", "publisher": "Reuters"},
                 ]},
                {"category": "supply_chain", "title": "GPU supply", "impact": 0.5,
                 "description": "Allocation risk.", "trend": "improving", "severity": "low",
                 "sources": [
                     {"title": "Rates note", "uri": "https://www.reuters.com/markets/rates", "publisher": "Reuters"},
                 ]},
            ],
        },
    }


def test_build_context_handles_empty_input():
    """Worst case: an empty dict must not crash and must degrade gracefully."""
    ctx = build_context({}, None)
    assert ctx["symbol"] == "—"
    assert ctx["vitals"] == []
    assert ctx["margin_of_safety_pct"] is None
    assert "<svg" in ctx["charts"]["gauge"]  # gauge renders even at score 0


def test_persona_mapping_to_agent_label():
    assert _persona_display({"name": "buffett"}) == "Buffett Agent"
    assert _persona_display({"name": "Warren Buffett"}) == "Buffett Agent"
    assert _persona_display({"key": "cathie_wood"}) == "Wood Agent"
    assert _persona_display({"name": "Peter Lynch"}) == "Lynch Agent"


def test_build_context_full_sample():
    ctx = build_context(_sample(), fair_value_estimate=196.0)
    assert ctx["quality_score"] == 72
    assert ctx["persona_name"] == "Buffett Agent"
    assert ctx["fair_value"] == 205.0  # Wall Street consensus target, not the passed estimate
    assert ctx["margin_of_safety_pct"] > 0  # 205 vs 172.4 → undervalued
    assert ctx["valuation_word"] == "Undervalued"
    assert len(ctx["vitals"]) == 9
    assert ctx["bull_case"] and ctx["bear_case"]


def test_fair_value_prefers_wall_street_target():
    """Fair value = Wall Street consensus target (the hero card is labeled
    'Per Wall Street consensus'); the passed estimate is only a fallback."""
    ctx = build_context(_sample(), fair_value_estimate=196.0)
    assert ctx["fair_value"] == 205.0  # consensus target wins over the estimate
    sample = _sample()
    sample["wall_street_consensus"]["target_price"] = None
    ctx2 = build_context(sample, fair_value_estimate=196.0)
    assert ctx2["fair_value"] == 196.0  # no consensus target → fall back to the estimate


def test_render_html_embeds_data_and_charts():
    html = render_html(build_context(_sample(), 196.0))
    assert "Oracle Corporation" in html
    assert "Buffett Agent" in html
    assert "Quantitative Scorecard" in html
    assert "Factors to Watch" in html
    assert "Sources &amp; References" in html
    assert "federalreserve.gov" in html  # full source URL in references
    assert "<svg" in html  # charts embedded inline


def test_sources_aggregated_deduped_and_numbered():
    ctx = build_context(_sample(), 196.0)
    sources = ctx["sources"]
    uris = [s["uri"] for s in sources]
    assert len(uris) == len(set(uris))           # deduped
    assert [s["n"] for s in sources] == list(range(1, len(sources) + 1))  # 1..N
    # each macro risk factor exposes the reference numbers of its citations
    for rf in ctx["macro"]["risk_factors"]:
        assert all(isinstance(n, int) for n in rf["source_refs"])


def test_charts_empty_inputs_return_empty_string():
    assert pdf_charts.price_sparkline([]) == ""
    assert pdf_charts.price_sparkline([1.0]) == ""  # needs >= 2 points
    assert pdf_charts.moat_radar([]) == ""
    assert pdf_charts.diverging_bars([]) == ""
    assert pdf_charts.bars_actuals_forecast([]) == ""
    assert pdf_charts.analyst_consensus_stacked_bar({}) == ""
    assert pdf_charts.mini_line([]) == ""


def test_charts_valid_inputs_contain_svg():
    assert "<svg" in pdf_charts.score_gauge(72)
    assert "<svg" in pdf_charts.price_sparkline([1.0, 2.0, 3.0])
    assert "<svg" in pdf_charts.analyst_consensus_stacked_bar({"buy": 5, "hold": 2})
    assert "<svg" in pdf_charts.diverging_bars([{"label": "Jan", "up": 5, "down": 3}])
    assert "<svg" in pdf_charts.bars_actuals_forecast(
        [{"label": "FY24", "value": 50, "is_forecast": False},
         {"label": "FY25", "value": 60, "is_forecast": True}])
    assert "<svg" in pdf_charts.moat_radar([
        {"name": "A", "score": 80, "peer_score": 60},
        {"name": "B", "score": 70, "peer_score": 65},
        {"name": "C", "score": 60, "peer_score": 55}])


def test_band_color_thresholds():
    assert pdf_charts.band_color(85) == pdf_charts._GOOD   # blue
    assert pdf_charts.band_color(55) == pdf_charts._AMBER
    assert pdf_charts.band_color(30) == pdf_charts._RED


@pytest.mark.skipif(not _weasyprint_available(),
                    reason="WeasyPrint native libs unavailable on this host")
def test_render_pdf_bytes_produces_pdf():
    from app.services.pdf_report_service import render_pdf_bytes
    pdf = render_pdf_bytes(render_html(build_context(_sample(), 196.0)))
    assert pdf[:4] == b"%PDF"
