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
    # Personas were renamed from real people to style names (migration 103). The
    # pre-rename surname forms must still resolve, because reports frozen before the
    # rename carry `agent.name == "Warren Buffett"`.
    assert _persona_display({"name": "buffett"}) == "Quality Agent"
    assert _persona_display({"name": "Warren Buffett"}) == "Quality Agent"
    assert _persona_display({"key": "cathie_wood"}) == "Disruption Agent"
    assert _persona_display({"name": "Peter Lynch"}) == "GARP Agent"
    # Current style names.
    assert _persona_display({"name": "The Quality Compounder"}) == "Quality Agent"
    assert _persona_display({"name": "The Activist Concentrator"}) == "Activist Agent"


def test_build_context_full_sample():
    ctx = build_context(_sample(), fair_value_estimate=196.0)
    assert ctx["quality_score"] == 72
    assert ctx["persona_name"] == "Quality Agent"
    # The model value the report was built with — never its analyst target (205, 2026-09-26).
    assert ctx["fair_value"] == 196.0
    assert ctx["margin_of_safety_pct"] > 0  # 196 vs 172.4
    # Neutral gap wording, never a verdict (hard rule 4 — dcf-methodology-v1.md §5).
    assert ctx["valuation_word"] == "Price 12% below the model value"
    # Eight: the "Wall Street" row (analyst conviction) left with the analyst half.
    assert len(ctx["vitals"]) == 8
    assert "Wall Street" not in [v["label"] for v in ctx["vitals"]]
    assert ctx["bull_case"] and ctx["bear_case"]


def test_quality_label_matches_ios_band_and_persona_lens():
    # The PDF hero headline must use the SAME band cutoffs (80/65/48/33) and the
    # persona-aware "<adjective> <lens> Profile" wording as the in-app gauge — NOT the
    # old 80/60/40 "Quality Business" phrasing the iOS reframe dropped for compliance.
    # _sample(): quality_score=72 (Strong band), agent=buffett (Value lens).
    ctx = build_context(_sample(), fair_value_estimate=196.0)
    assert ctx["quality_label"] == "Strong Value Profile"

    # Burry → Contrarian lens; a low score → Weak/Poor band.
    burry = _sample()
    burry["agent"] = "burry"
    burry["quality_score"] = 42
    assert build_context(burry, 196.0)["quality_label"] == "Weak Contrarian Profile"

    # Band boundaries track iOS (80 Excellent / 65 Strong / 48 Fair / 33 Weak).
    wood = _sample()
    wood["agent"] = "wood"
    wood["quality_score"] = 80
    assert build_context(wood, 196.0)["quality_label"] == "Excellent Growth Profile"


def test_the_hero_never_shows_an_analyst_target():
    """Until 2026-09-26 the hero preferred the analyst consensus target ("Per Wall Street
    consensus") and printed "Analyst target $X" under the price. Analyst targets are unlicensed
    and removed from every surface; a target under "Fair Value" is the placement rule inverted."""
    ctx = build_context(_sample(), fair_value_estimate=196.0)     # the sample HAS a $205 target
    assert ctx["fair_value"] == 196.0
    assert "target_price" not in ctx
    html = render_html(ctx)
    for gone in ("Per Wall Street consensus", "Analyst target", "$205"):
        assert gone not in html, gone
    assert build_context(_sample(), fair_value_estimate=None)["fair_value"] is None


def test_a_fair_value_equal_to_the_price_is_no_estimate():
    """Rows persisted between the FMP rebuild and 2026-09-12 carry a FABRICATED
    `fair_value_estimate` equal to the frozen current price (the collector wrote
    `round(current_price, 2)` whenever the DCF was missing). Those rows are immutable, so
    the hero must render "—" with an empty basis rather than "Fairly Valued +0.0%"."""
    sample = _sample()
    sample["wall_street_consensus"]["target_price"] = None
    price = sample["price_action"]["current_price"]
    ctx = build_context(sample, fair_value_estimate=price)
    assert ctx["fair_value"] is None
    assert ctx["valuation_word"] == "—"
    assert ctx["fair_value_basis"] == ""
    assert ctx["margin_of_safety_pct"] is None
    # Half a cent either side is still "equal" (the persisted value was round(price, 2)).
    ctx_close = build_context(sample, fair_value_estimate=price + 0.004)
    assert ctx_close["fair_value"] is None
    # A genuinely different estimate is still used.
    ctx2 = build_context(sample, fair_value_estimate=price * 1.2)
    assert ctx2["fair_value"] == price * 1.2
    assert ctx2["valuation_word"] == "Price 17% below the model value"
    # No price at all → the estimate stands (nothing to compare against).
    sample2 = _sample()
    sample2["wall_street_consensus"]["target_price"] = None
    sample2["price_action"]["current_price"] = None
    assert build_context(sample2, fair_value_estimate=150.0)["fair_value"] == 150.0


def test_render_html_embeds_data_and_charts():
    html = render_html(build_context(_sample(), 196.0))
    assert "Oracle Corporation" in html
    assert "Quality Agent" in html
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


def test_unknown_guidance_renders_as_a_dash_not_a_stance():
    """E1: the PDF's Guidance cell used to print whatever string arrived, capitalised.
    "unknown" is not a stance — it must fall to the template's "—"."""
    from app.services.pdf_report_service import _guidance_for_pdf
    assert _guidance_for_pdf("unknown") == ""
    assert _guidance_for_pdf(None) == ""
    assert _guidance_for_pdf("") == ""
    assert _guidance_for_pdf("MAINTAINED") == ""     # not a wire value; never guess
    for read in ("raised", "maintained", "lowered"):
        assert _guidance_for_pdf(read) == read
    sample = _sample()
    sample["revenue_forecast"] = {"cagr": 10.0, "eps_growth": 12.0,
                                  "management_guidance": "unknown", "projections": []}
    ctx = build_context(sample, fair_value_estimate=196.0)
    assert ctx["forecast"]["management_guidance"] == ""


# ── Section 09 "Valuation & Institutions" (was "Wall Street Consensus", 2026-09-26) ──────────
#
# The analyst half (ratings distribution, price targets, momentum) is unlicensed FMP data and is
# gone. The section leads with the Caydex Fair Value Estimate RANGE, the point estimate as its
# middle mark, the neutral gap, and the "not a price target" line; then the 13F flow and the
# insight. Strings mirror the iOS card (title, "Estimate range", the nil and refused states).

_EST_NONE = "No Caydex Fair Value Estimate is available for this report."
_EST_NOTICE = "DCF model estimate · not a price target · not a recommendation"


def _section_09(html: str) -> str:
    """Brace-bound the assertions to section 09 (the hero above it still has its own card)."""
    start = html.index("Valuation &amp; Institutions")
    return html[start:html.index("Factors to Watch", start)]


def _estimate_report(*, price=130.0, dcf_source="caydex", insight="Institutions added on dips.",
                     **est) -> dict:
    block = {"symbol": "EXMP", "status": "ok", "fair_value": 150.0, "range_low": 120.0,
             "range_high": 180.0, "as_of": "2026-09-25", **est}
    return {
        "symbol": "EXMP", "company_name": "Example Corp", "quality_score": 60,
        "price_action": {"current_price": price},
        "wall_street_consensus": {
            "rating": "hold", "current_price": price, "target_price": None,
            "valuation_status": "fairly_valued", "discount_percent": 0.0,
            "momentum_upgrades": 0, "momentum_downgrades": 0, "momentum_maintains": 0,
            "dcf_source": dcf_source, "wall_street_insight": insight,
            "caydex_fair_value": block,
            "hedge_fund_flow_data": [{"month": "06/2025", "buy_volume": 5, "sell_volume": 3}],
        },
    }


@pytest.fixture
def dcf_on(monkeypatch):
    from app.services import pdf_report_service as pdf
    monkeypatch.setattr(pdf.settings, "DCF_ENABLED", True)


def test_section_09_is_valuation_and_institutions_with_no_analyst_block(dcf_on):
    # _sample() is an ANALYST-ERA report: rating, targets, distribution and momentum all set.
    html = render_html(build_context(_sample(), 196.0))
    assert "Wall Street Consensus" not in html
    sec = _section_09(html)
    assert sec.startswith("Valuation &amp; Institutions")
    for gone in ("Price Target", "Analyst Ratings", "Momentum", "Upgrades", "Maintains",
                 "Downgrades", "analysts", "Strong Buy", "No analyst coverage",
                 "$150", "$260"):          # low/high analyst targets of the sample
        assert gone not in sec, gone
    assert _EST_NONE in sec                # no published estimate on an analyst-era report
    # The context no longer carries the analyst section's inputs at all.
    ctx = build_context(_sample(), 196.0)
    for key in ("low_target", "high_target", "consensus_counts", "consensus_legend",
                "consensus_total"):
        assert key not in ctx, key
    assert "consensus" not in ctx["charts"]
    assert set(ctx["wall_street"]) == {"estimate", "insight"}


def test_section_09_leads_with_the_range_then_the_estimate_then_the_gap(dcf_on):
    sec = _section_09(render_html(build_context(_estimate_report())))
    assert "Caydex Fair Value Estimate" in sec and "Estimate range" in sec
    order = [sec.index("$120.00 – $180.00"), sec.index("Estimate $150.00"),
             sec.index("Price 13% below the estimate"), sec.index(_EST_NOTICE)]
    assert order == sorted(order), order
    assert "as of 2026-09-25" in sec
    assert _EST_NONE not in sec and "Not modelled" not in sec


def test_section_09_gap_words_track_the_reports_price(dcf_on):
    def gap(price):
        return build_context(_estimate_report(price=price))["wall_street"]["estimate"]["gap_word"]

    assert gap(165.0) == "Price 10% above the estimate"
    assert gap(150.0) == "Price in line with the estimate"
    assert gap(150.6) == "Price in line with the estimate"   # inside the ±0.5 % band
    assert gap(151.0) == "Price 1% above the estimate"       # just outside it
    assert gap(None) == ""                                    # no price → no gap line, value stays
    sec = _section_09(render_html(build_context(_estimate_report(price=None))))
    assert "$120.00 – $180.00" in sec and "Price " not in sec


def test_section_09_refused_state_says_not_modelled_with_the_reason(dcf_on):
    reason = "Banks and insurers are not modelled by a cash-flow DCF."
    sec = _section_09(render_html(build_context(_estimate_report(
        status="refused", fair_value=150.0, refusal_reason=reason))))
    assert "Not modelled" in sec and reason in sec
    assert "$150.00" not in sec and "Estimate range" not in sec and _EST_NONE not in sec


def test_section_09_nil_state_for_an_old_report_and_with_the_switch_off(monkeypatch):
    from app.services import pdf_report_service as pdf
    old = _estimate_report()
    old["wall_street_consensus"].pop("caydex_fair_value")
    monkeypatch.setattr(pdf.settings, "DCF_ENABLED", True)
    assert _EST_NONE in _section_09(render_html(build_context(old)))
    # Kill switch: a stored estimate is not rendered while DCF_ENABLED is off.
    monkeypatch.setattr(pdf.settings, "DCF_ENABLED", False)
    # Production passes the research_reports column, which on a Caydex-built report IS the
    # estimate: it must not come back on the hero re-labelled "DCF model value", rangeless.
    ctx = build_context(_estimate_report(), fair_value_estimate=150.0)
    assert ctx["fair_value"] is None
    html = render_html(ctx)
    sec = _section_09(html)
    assert _EST_NONE in sec
    assert "$120.00" not in sec and "$150.00" not in sec and "Estimate range" not in sec
    assert "$150" not in html and "model value" not in html


@pytest.mark.parametrize("est", [
    {"range_low": None},                                   # a value never without its range
    {"range_high": float("nan")},
    {"fair_value": float("inf")},
    {"fair_value": 0.0},
    {"range_low": 200.0},                                  # value outside its own range
    {"range_low": -5.0, "fair_value": 150.0},
    {"status": "pending"},                                 # unknown status
])
def test_a_malformed_estimate_degrades_to_the_nil_state_in_both_places(dcf_on, est):
    # fair_value_estimate=150.0 is what production passes (the column holds the Caydex value).
    ctx = build_context(_estimate_report(**est), fair_value_estimate=150.0)
    assert ctx["wall_street"]["estimate"] == {"state": "none"}
    assert ctx["fair_value"] is None                       # the hero does not use it either
    sec = _section_09(render_html(ctx))
    assert _EST_NONE in sec and "$nan" not in sec.lower() and "$inf" not in sec.lower()


def test_section_09_keeps_the_institutional_flow_chart(dcf_on):
    sec = _section_09(render_html(build_context(_estimate_report())))
    assert "Institutional (13F) Net Flow" in sec and "<svg" in sec
