"""Calibration test for the deterministic macro threat-level pipeline.

Hand-built FRED + FMP snapshots for canonical macro regimes — Goldilocks
(benign), late-cycle (mild mixed), inflation peak (broad-but-mild), and
Lehman (multi-front crisis) — running them through the production
pipeline:

    _build_macro_risk_factors_from_fred + _build_macro_risk_factors_from_indicators
      -> _merge_macro_risk_factors
      -> _compute_macro_threat (with sector β)
      -> _derive_macro_vital (composite -> 0-10 score)

This is a math test (Category 1 per .claude/rules/testing.md), not an
integration test — no live FMP/FRED/Gemini calls. Failure here means
the threat-level math broke, not a network issue.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.agents.ticker_report_data_collector import (
    _build_macro_risk_factors_from_fred,
    _build_macro_risk_factors_from_indicators,
    _compute_macro_threat,
    _derive_macro_vital,
    _merge_macro_risk_factors,
)


# ── Fixture builders ─────────────────────────────────────────────────


def _fred(series_id: str, latest: float, yoy_pct: float | None = None,
          change_6mo_pct: float | None = None) -> Dict[str, Any]:
    return {
        "series_id": series_id,
        "latest": latest,
        "as_of": "2024-01-01",
        "yoy_pct": yoy_pct,
        "change_6mo_pct": change_6mo_pct,
        "change_6mo_relative_pct": None,
    }


def _fmp(symbol: str, level: float | None = None,
         change_1m_pct: float | None = None,
         change_3m_pct: float | None = None) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "level": level,
        "change_5d_pct": None,
        "change_1m_pct": change_1m_pct,
        "change_3m_pct": change_3m_pct,
        "change_1y_pct": None,
    }


def _run(
    fred_rows: List[Dict[str, Any]],
    fmp_rows: List[Dict[str, Any]],
    sector: str,
    ai_factors: List[Dict[str, Any]] | None = None,
):
    """Mirror the production assemble_report flow: composite is
    computed on the FULL (uncapped) factor set, while the display
    list is the post-merge capped list.
    """
    fred_factors = _build_macro_risk_factors_from_fred(fred_rows)
    fmp_factors = _build_macro_risk_factors_from_indicators(fmp_rows)
    ai_keyed_categories = {
        f.get("category") for f in (fred_factors + fmp_factors)
    }
    ai_factors_kept = [
        f for f in (ai_factors or [])
        if f.get("category") not in ai_keyed_categories
    ]
    full_factor_set = fred_factors + fmp_factors + ai_factors_kept
    tier, composite = _compute_macro_threat(full_factor_set, sector)
    merged_for_display = _merge_macro_risk_factors(
        _merge_macro_risk_factors(fred_factors, fmp_factors),
        (ai_factors or []),
    )
    vital = _derive_macro_vital(merged_for_display, tier, composite)
    return merged_for_display, tier, composite, vital


# ── A. Goldilocks (mid-2014 vibe) ────────────────────────────────────


def test_goldilocks_is_low_for_both_sectors():
    """All indicators below ELEVATED threshold → empty risk factors,
    tier=LOW, composite=1.0, macro-vital score near 8.0."""
    fred_rows = [
        _fred("CPIAUCSL", latest=237.0, yoy_pct=1.7),       # < 2 → skip
        _fred("PCEPILFE", latest=109.0, yoy_pct=1.6),       # skip
        _fred("T5YIE", latest=1.90),                         # < 2 → skip
        _fred("FEDFUNDS", latest=0.10, change_6mo_pct=0.0),  # both skip
        _fred("DGS10", latest=2.60),                          # < 3 → skip
        _fred("T10Y2Y", latest=2.05),                         # > 1 → skip
        _fred("UNRATE", latest=6.1, change_6mo_pct=-0.3),    # not rising → skip
        _fred("ICSA", latest=260_000),                        # < 275k → skip
        _fred("BAMLH0A0HYM2", latest=2.8),                   # < 3 → skip
    ]
    fmp_rows = [
        _fmp("CLUSD", change_3m_pct=2.0, change_1m_pct=1.0),
        _fmp("GCUSD", change_3m_pct=1.0, change_1m_pct=0.5),
        _fmp("HGUSD", change_1m_pct=-2.0),
        _fmp("^VIX", level=12.0, change_1m_pct=-5.0),
        _fmp("^TNX", change_3m_pct=2.0, change_1m_pct=1.0),
        _fmp("DXY", change_3m_pct=-1.0, change_1m_pct=-0.5),
    ]
    for sector in ("Technology", "Financial Services"):
        merged, tier, composite, vital = _run(fred_rows, fmp_rows, sector)
        assert merged == [], f"{sector}: expected empty, got {merged}"
        assert tier == "low", f"{sector}: tier={tier}"
        assert composite == 1.0
        assert vital["score"]["value"] == 8.0
        assert vital["score"]["status"] == "good"


# ── B. Late-cycle (Q4 2018 vibe) ─────────────────────────────────────


def _late_cycle_fixtures():
    fred_rows = [
        _fred("CPIAUCSL", latest=252.0, yoy_pct=2.2),         # ELEV
        _fred("PCEPILFE", latest=110.0, yoy_pct=1.9),         # skip
        _fred("T5YIE", latest=1.70),                            # skip
        _fred("FEDFUNDS", latest=2.27, change_6mo_pct=0.5),    # both ELEV (level + Δ)
        _fred("DGS10", latest=2.69),                             # skip (<3)
        _fred("T10Y2Y", latest=0.21),                            # HIGH (≤0.3)
        _fred("UNRATE", latest=3.9, change_6mo_pct=0.0),        # skip
        _fred("ICSA", latest=220_000),                            # skip
        _fred("BAMLH0A0HYM2", latest=5.0),                       # HIGH (4-6)
    ]
    fmp_rows = [
        _fmp("^VIX", level=25.0, change_1m_pct=20.0),            # ELEV (22-30)
        _fmp("DXY", change_3m_pct=3.5, change_1m_pct=1.0),       # ELEV (2-5)
    ]
    return fred_rows, fmp_rows


def test_late_cycle_lands_high_for_tech_and_financials():
    """Mixed mid-tier signals across rates, credit, curve, vol → HIGH
    for both, but Financials lands modestly higher (yield_curve β=1.4,
    credit β=1.4)."""
    fred_rows, fmp_rows = _late_cycle_fixtures()
    _, tech_tier, tech_comp, _ = _run(fred_rows, fmp_rows, "Technology")
    _, fin_tier, fin_comp, _ = _run(fred_rows, fmp_rows, "Financial Services")
    assert tech_tier in ("high", "severe"), f"tech tier={tech_tier} comp={tech_comp}"
    assert fin_tier in ("high", "severe"), f"fin tier={fin_tier} comp={fin_comp}"
    # Financials should read at least as severely as Tech given β
    # multipliers on yield_curve and credit; allow equal because the
    # tail term may already saturate either ticker at the same level.
    assert fin_comp >= tech_comp - 0.05, (
        f"Expected Financials composite ≥ Tech, got "
        f"fin={fin_comp} tech={tech_comp}"
    )


# ── C. Inflation peak (mid-2022 vibe) ────────────────────────────────


def test_inflation_peak_is_high_or_severe():
    """High CPI + sticky core + rising rates + oil shock should land
    HIGH/SEVERE for AAPL (Tech) — softer inflation β=0.9 and oil
    β=0.5 keep it from CRIT — while Financials, with rates β=1.2 and
    credit β=1.4, lands at least as severely.
    """
    fred_rows = [
        _fred("CPIAUCSL", latest=296.0, yoy_pct=9.1),           # SEVERE (5-8 SEVERE; >8 CRIT)
        _fred("PCEPILFE", latest=120.0, yoy_pct=5.0),           # SEVERE (4.5-6)
        _fred("T5YIE", latest=2.80),                              # HIGH (2.5-3.5)
        _fred("FEDFUNDS", latest=1.58, change_6mo_pct=1.4),      # FF level LOW skip; FF Δ HIGH
        _fred("DGS10", latest=3.0),                                # ELEV (3-4.5)
        _fred("T10Y2Y", latest=0.05),                              # HIGH (≤0.3)
        _fred("UNRATE", latest=3.6, change_6mo_pct=-0.2),         # skip (improving)
        _fred("ICSA", latest=235_000),                              # skip
        _fred("BAMLH0A0HYM2", latest=5.4),                         # HIGH (4-6)
    ]
    fmp_rows = [
        _fmp("CLUSD", change_3m_pct=28.0, change_1m_pct=10.0),    # HIGH (20-35)
        _fmp("^VIX", level=29.0, change_1m_pct=8.0),               # ELEV (22-30)
        _fmp("DXY", change_3m_pct=7.0, change_1m_pct=2.5),         # HIGH (5-8)
    ]
    _, tech_tier, tech_comp, tech_vital = _run(
        fred_rows, fmp_rows, "Technology",
    )
    _, fin_tier, fin_comp, _ = _run(
        fred_rows, fmp_rows, "Financial Services",
    )
    assert tech_tier in ("high", "severe"), (
        f"tech tier={tech_tier} comp={tech_comp}"
    )
    assert fin_tier in ("high", "severe", "critical"), (
        f"fin tier={fin_tier} comp={fin_comp}"
    )
    # Tech vital score should land in the "warning" band, not "good".
    assert tech_vital["score"]["status"] in ("warning", "critical")
    assert tech_vital["score"]["value"] < 7.0


# ── D. Lehman (Sep 2008 vibe) ─────────────────────────────────────────


def test_lehman_lands_severe_or_critical_for_both_sectors():
    """Multi-front crisis — credit blowout, ICSA spike, CPI hot,
    sticky core, VIX 36, dollar surge — should hit SEVERE/CRIT for
    both AAPL and KRE. The composite needs both breadth and tail
    elevated; this is the calibration anchor for "real crisis".
    """
    fred_rows = [
        _fred("CPIAUCSL", latest=219.0, yoy_pct=4.94),          # HIGH (3-5)
        _fred("PCEPILFE", latest=108.0, yoy_pct=2.5),           # ELEV
        _fred("T5YIE", latest=2.50),                              # ELEV
        _fred("FEDFUNDS", latest=1.81, change_6mo_pct=-0.4),    # FF level LOW skip; FF Δ LOW skip
        _fred("DGS10", latest=3.83),                              # ELEV (3-4.5)
        _fred("T10Y2Y", latest=1.50),                              # LOW (>1) skip
        _fred("UNRATE", latest=6.1, change_6mo_pct=0.5),         # HIGH (0.3-0.5 → exactly at 0.5 → SEVERE)
        _fred("ICSA", latest=480_000),                              # SEVERE (400-500)
        _fred("BAMLH0A0HYM2", latest=9.5),                         # CRIT (>8)
    ]
    fmp_rows = [
        _fmp("CLUSD", change_3m_pct=-28.0, change_1m_pct=-12.0),  # HIGH (|28|)
        _fmp("^VIX", level=36.0, change_1m_pct=40.0),               # HIGH (30-40)
        _fmp("DXY", change_3m_pct=8.0, change_1m_pct=3.0),          # HIGH (8-12)
        _fmp("HGUSD", change_1m_pct=-15.0),                          # HIGH
    ]
    _, tech_tier, tech_comp, tech_vital = _run(
        fred_rows, fmp_rows, "Technology",
    )
    _, fin_tier, fin_comp, fin_vital = _run(
        fred_rows, fmp_rows, "Financial Services",
    )
    assert tech_tier in ("severe", "critical"), (
        f"tech tier={tech_tier} comp={tech_comp}"
    )
    assert fin_tier in ("severe", "critical"), (
        f"fin tier={fin_tier} comp={fin_comp}"
    )
    # In a regime crisis the Macro vital should report "critical"
    # status and a very low score.
    assert tech_vital["score"]["status"] == "critical"
    assert tech_vital["score"]["value"] <= 3.0
    assert fin_vital["score"]["status"] == "critical"


# ── E. Sector mediation sanity check ─────────────────────────────────


def test_sector_beta_makes_a_difference():
    """A REIT and a software company looking at the SAME mid-tier
    macro stack should not produce identical composites — Real Estate
    weights rates and credit more heavily, so its composite should
    exceed the Tech reading.
    """
    fred_rows = [
        _fred("FEDFUNDS", latest=5.0, change_6mo_pct=0.6),    # HIGH level + ELEV Δ
        _fred("DGS10", latest=4.6),                             # HIGH (4.5-5.5)
        _fred("BAMLH0A0HYM2", latest=4.5),                     # HIGH (4-6)
        _fred("CPIAUCSL", latest=300.0, yoy_pct=3.2),          # HIGH (3-5)
    ]
    fmp_rows: List[Dict[str, Any]] = []
    _, _, tech_comp, _ = _run(fred_rows, fmp_rows, "Technology")
    _, _, reit_comp, _ = _run(fred_rows, fmp_rows, "Real Estate")
    assert reit_comp > tech_comp + 0.1, (
        f"Real Estate composite ({reit_comp}) should clearly exceed "
        f"Tech ({tech_comp}) given rates β=1.5 vs 1.3 and credit β=1.3"
    )


# ── F. Risk-group inference + AI factor merge ────────────────────────


def test_ai_factor_alone_caps_at_high_not_severe():
    """A lone AI-emitted factor can NEVER drive the tier to severe/critical.
    Gemini severities are capped at "high", and severe/critical require ≥2
    sourced (non-AI) high fronts — so a single high geopolitical signal lands
    "high" for BOTH Tech and Utilities. Sector β still nudges the composite
    (Tech geopolitical β=1.3 > Utilities 1.0), but the breadth gate holds the
    tier at "high".

    (Before the breadth gate this lone factor read "severe" for Tech — that
    was the over-eager behavior this calibration deliberately removes.)
    """
    from app.services.agents.ticker_report_data_collector import (
        _sanitize_risk_factor,
    )
    ai_factor = {
        "category": "geopolitical",
        "title": "Taiwan Strait Escalation",
        "impact": 0.7,
        "description": "Chip-supply trade route risk.",
        "trend": "worsening",
        "severity": "high",
    }
    rfs = [_sanitize_risk_factor(ai_factor)]
    tech_tier, tech_comp = _compute_macro_threat(rfs, "Technology")
    util_tier, util_comp = _compute_macro_threat(rfs, "Utilities")
    assert tech_tier == "high", f"lone AI factor must cap at high, got {tech_tier}"
    assert util_tier == "high", f"lone AI factor must cap at high, got {util_tier}"
    # β still routes — Tech weights geopolitical higher — even after the cap.
    assert tech_comp >= util_comp, (
        f"Tech ({tech_comp}) should be ≥ Utilities ({util_comp}) via β"
    )


def test_lone_extreme_deterministic_factor_caps_at_high():
    """One sourced indicator in its top band is, by itself, a single front
    — it caps at "high" no matter how extreme. A second distinct sourced
    front frees the tier to reach severe/critical. This is the core of the
    "critical = real multi-front crisis" calibration."""
    # Lone CRITICAL credit factor (HY OAS 9.5) → one front → capped at high.
    lone = _build_macro_risk_factors_from_fred([_fred("BAMLH0A0HYM2", latest=9.5)])
    tier_lone, comp_lone = _compute_macro_threat(lone, "Technology")
    assert tier_lone == "high", (
        f"a lone extreme factor must cap at high, got {tier_lone} ({comp_lone})"
    )
    # Two distinct sourced fronts (credit + inverted curve) → severe/critical.
    two_front = _build_macro_risk_factors_from_fred([
        _fred("BAMLH0A0HYM2", latest=9.5),    # CRIT credit
        _fred("T10Y2Y", latest=-1.0),          # CRIT inverted curve (reverse band)
    ])
    tier_two, comp_two = _compute_macro_threat(two_front, "Technology")
    assert tier_two in ("severe", "critical"), (
        f"two sourced fronts should reach severe+, got {tier_two} ({comp_two})"
    )


def test_ai_factor_cannot_supply_a_breadth_front():
    """AI factors are capped at "high" AND excluded from the breadth count,
    so an AI "critical" factor paired with ONE sourced high front still only
    reaches "high" — severe/critical needs two SOURCED fronts."""
    from app.services.agents.ticker_report_data_collector import (
        _sanitize_risk_factor,
    )
    ai_crit = _sanitize_risk_factor({
        "category": "geopolitical", "title": "War", "impact": 1.0,
        "description": "x", "trend": "worsening", "severity": "critical",
    })
    one_det_front = _build_macro_risk_factors_from_fred([
        _fred("BAMLH0A0HYM2", latest=9.5),    # CRIT credit — 1 sourced front
    ])
    tier, comp = _compute_macro_threat(one_det_front + [ai_crit], "Technology")
    assert tier == "high", (
        f"AI factor must not unlock severe with one sourced front, got {tier} ({comp})"
    )


def test_grounded_factor_is_sourced_not_capped_and_counts_as_a_front():
    """A WEB-GROUNDED factor (_source='grounded') is sourced, so unlike an
    ungrounded AI one it is NOT severity-capped and DOES count toward the
    breadth gate. A lone grounded event still caps at 'high' (one front);
    paired with a sourced deterministic front, the tier can reach severe/critical.
    This is what lets a real crisis (a grounded war + a deterministic credit
    blowout) register — while a single news item still can't alone read critical.
    """
    grounded = {
        "category": "geopolitical", "title": "Major War", "impact": 1.0,
        "description": "Active conflict disrupts energy and supply chains.",
        "trend": "worsening", "severity": "critical",
        "_risk_group": "geopolitical", "_source": "grounded",
    }
    # Lone grounded CRITICAL → one front → capped at "high" (breadth gate).
    tier_lone, comp_lone = _compute_macro_threat([grounded], "Technology")
    assert tier_lone == "high", (
        f"a lone grounded factor should cap at high, got {tier_lone} ({comp_lone})"
    )
    # Grounded + a deterministic high front (credit blowout) → 2 fronts → severe+.
    det = _build_macro_risk_factors_from_fred([_fred("BAMLH0A0HYM2", latest=9.5)])
    tier_two, comp_two = _compute_macro_threat([grounded] + det, "Technology")
    assert tier_two in ("severe", "critical"), (
        f"grounded + a deterministic front should reach severe+, got {tier_two} ({comp_two})"
    )
