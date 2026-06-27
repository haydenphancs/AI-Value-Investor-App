"""'Cards become the score' — the 4 industry-relative Fundamentals card scores
now drive the per-persona quality_score via the vital factors.

Pins: the 1-5 → 0-10 mapper, the new profitability factor, the valuation factor
sourcing the card score while KEEPING the DCF fair_value (compliance lock), the
health/revenue re-source, the profitability factor moving the score, and the
halved Buffett ROE style nudge (de-double-count).
"""

from app.schemas.stock_overview import SnapshotItemResponse
from app.services.agents.persona_scoring import compute_quality_score, style_fit_adjustment
from app.services.agents.ticker_report_data_collector import (
    _build_health_vital,
    _build_profitability_vital,
    _build_revenue_vital,
    _build_valuation_vital,
    _card_weighted_to_score10,
    _valuation_score_from_upside,
)


def _snap(weighted, rating=None, category="Price"):
    return SnapshotItemResponse(
        category=category,
        rating=rating if rating is not None else round(weighted),
        metrics=[],
        weighted_score=weighted,
    )


def test_mapper_anchors():
    assert _card_weighted_to_score10(1.0) == 0.0
    assert _card_weighted_to_score10(3.0) == 5.0
    assert _card_weighted_to_score10(5.0) == 10.0
    assert _card_weighted_to_score10(None) is None
    # defensive clamping
    assert _card_weighted_to_score10(0.0) == 0.0
    assert _card_weighted_to_score10(6.0) == 10.0


def test_profitability_vital():
    assert _build_profitability_vital(None) is None
    v5 = _build_profitability_vital(5.0)
    assert v5["score"]["value"] == 10.0 and v5["score"]["status"] == "good"
    v1 = _build_profitability_vital(1.0)
    assert v1["score"]["value"] == 0.0 and v1["score"]["status"] == "critical"
    v3 = _build_profitability_vital(3.0)
    assert v3["score"]["value"] == 5.0 and v3["score"]["status"] == "neutral"


def test_valuation_factor_uses_card_but_fair_value_stays_dcf():
    # DCF says +20% upside → status "underpriced", fair_value 120. A CHEAP-vs-sector
    # card (weighted 5 → factor 10) must drive the SCORE, but fair_value stays DCF.
    snap = _snap(5.0)
    v = _build_valuation_vital(current_price=100.0, fair_value=120.0, upside=20.0,
                               valuation_snapshot=snap)
    assert v["score"]["value"] == 10.0          # card-driven factor
    assert v["fair_value"] == 120.0             # DCF preserved — compliance lock
    assert v["status"] == "underpriced"          # DCF status preserved
    # No card → falls back to the DCF-derived score (existing behavior).
    v_nocard = _build_valuation_vital(100.0, 120.0, 20.0, None)
    assert v_nocard["score"]["value"] == _valuation_score_from_upside(20.0)
    assert v_nocard["fair_value"] == 120.0


def test_health_and_revenue_factor_use_card_keep_labels():
    h = _build_health_vital(altman_z=2.0, debt_equity=3.0, fcf_negative=True, card_weighted=5.0)
    assert h["score"]["value"] == 10.0           # card overrides the absolute Z/leverage path
    assert "level" in h and "altman_z_label" in h  # labels preserved
    # No card → absolute fallback (leverage + FCF penalties apply, score < 5).
    h_nocard = _build_health_vital(2.0, 3.0, True, card_weighted=None)
    assert h_nocard["score"]["value"] < 5.0

    r = _build_revenue_vital(1e9, 30.0, "Cloud", 25.0, card_weighted=1.0)
    assert r["score"]["value"] == 0.0            # card (weighted 1 → 0) overrides 5+30/5=11→10
    assert r["top_segment"] == "Cloud"           # label preserved


def test_profitability_factor_moves_persona_score_and_missing_renormalizes():
    def inputs(prof):
        si = {
            "valuation": {"score": {"value": 5.0}},
            "moat": {"score": {"value": 5.0}},
            "financial_health": {"score": {"value": 5.0}},
            "revenue": {"score": {"value": 5.0}},
            "insider": {"score": {"value": 5.0}},
            "macro": {"score": {"value": 5.0}},
            "forecast": {"score": {"value": 5.0}},
            "wall_street": {"score": {"value": 5.0}},
            "capital_allocation": {"score": {"value": 5.0}},
            "profitability": ({"score": {"value": prof}} if prof is not None else None),
        }
        return {"_scoring_inputs": si}

    high = compute_quality_score("warren_buffett", inputs(10.0))
    low = compute_quality_score("warren_buffett", inputs(0.0))
    assert high > low                            # profitability now drives the score
    missing = compute_quality_score("warren_buffett", inputs(None))
    assert 0.0 <= missing <= 100.0               # renormalized out, no crash/deflation


def test_buffett_roe_style_nudge_halved():
    # ROE 20% would have nudged +full (cap) before; now halved because the
    # profitability factor already owns ROE-vs-industry. cap=10 → 0.5*10 = 5.0.
    assert style_fit_adjustment("warren_buffett", {"roe": 20.0}) == 5.0
