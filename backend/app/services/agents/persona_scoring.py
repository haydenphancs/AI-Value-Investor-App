"""
Persona-specific scoring math.

The per-vital judgments are deterministic, module-sourced 0-10 scores
(see the `_build_*_vital` / `_derive_*_vital` builders in
ticker_report_data_collector.py). What changes per persona is how those
vital scores roll up into the headline `overall_score` (0-100). Buffett
caring about moat ≠ Wood caring about moat, and that should change the
number, not just the prose.

`PERSONA_WEIGHTS[persona_key][vital_name]` weights sum to 1.0 and cover
ALL ten vitals (valuation, moat, financial_health, profitability, revenue,
insider, macro, forecast, wall_street, capital_allocation). The first four
mirror the industry-relative Fundamentals cards. Missing vitals are renormalized out (not
counted as 0) so unmeasurable data redistributes weight instead of
capping the score.

Every vital now emits a numeric `score.value`; valuation/moat/
financial_health additionally keep their status/label strings for their
report cards, and the scorer falls back to those strings (via the
`_*_SCALE` maps) for reports cached before the numeric scores existed.
This is an INTERNAL substrate — the user-facing Key Vitals UI was
removed; `_scoring_inputs` is stripped from every client response.
"""

from typing import Any, Dict, Optional


# ── Per-Persona Weight Vectors ──────────────────────────────────────
#
# Each persona's investing philosophy mapped to a probability mass over
# the 8 vitals. Each row sums to 1.0.

PERSONA_WEIGHTS: Dict[str, Dict[str, float]] = {
    # Quality-at-a-fair-price: moat + balance-sheet durability dominate;
    # capital-allocation discipline (buybacks/dividends) matters to Buffett.
    "warren_buffett": {
        "moat":              0.22,
        "financial_health":  0.18,
        "valuation":         0.14,
        "profitability":     0.12,
        "capital_allocation": 0.12,
        "revenue":           0.05,
        "insider":           0.05,
        "forecast":          0.04,
        "wall_street":       0.04,
        "macro":             0.04,
    },
    # Innovation/growth: revenue + forward forecast lead; value, balance-sheet,
    # profitability and capital-return weight is light; macro/secular regime matters.
    "cathie_wood": {
        "revenue":           0.25,
        "forecast":          0.24,
        "moat":              0.12,
        "macro":             0.08,
        "wall_street":       0.07,
        "valuation":         0.06,
        "financial_health":  0.05,
        "capital_allocation": 0.05,
        "profitability":     0.04,
        "insider":           0.04,
    },
    # GARP: growth (forecast/revenue) balanced against price (valuation), a solid
    # balance sheet, and decent profitability — "buy what you know, at a sane price".
    "peter_lynch": {
        "forecast":          0.20,
        "revenue":           0.16,
        "valuation":         0.16,
        "financial_health":  0.11,
        "profitability":     0.09,
        "capital_allocation": 0.07,
        "moat":              0.06,
        "insider":           0.05,
        "wall_street":       0.05,
        "macro":             0.05,
    },
    # Concentrated activist value: balance-sheet quality (FCF proxy) + valuation +
    # capital-allocation discipline + moat + return quality carry the thesis.
    "bill_ackman": {
        "financial_health":  0.20,
        "valuation":         0.16,
        "capital_allocation": 0.14,
        "moat":              0.13,
        "profitability":     0.12,
        "forecast":          0.08,
        "revenue":           0.07,
        "wall_street":       0.04,
        "macro":             0.04,
        "insider":           0.02,
    },
}

# Equal weight across all 10 vitals — used for unknown persona keys so we never
# crash on a missing config and instead degrade to a neutral average. (The
# scorer renormalizes over present vitals, so the exact sum need not be 1.0.)
_EQUAL_WEIGHTS: Dict[str, float] = {
    v: 1.0 / 10.0
    for v in (
        "valuation", "moat", "financial_health", "profitability", "revenue",
        "insider", "macro", "forecast", "wall_street", "capital_allocation",
    )
}


# ── String → 0-10 Score Derivation (FALLBACK only) ──────────────────
#
# New reports give valuation/moat/financial_health a numeric `score.value`
# (preferred by `_vital_score`). These maps are the back-compat bridge for
# reports cached before those numeric scores existed — they translate the
# status/label string each card still carries into a 0-10 number.

_VALUATION_SCALE: Dict[str, float] = {
    "deep_undervalued": 9.5,
    "underpriced":      7.5,
    "fair_value":       5.5,
    "overpriced":       3.0,
}

_MOAT_SCALE: Dict[str, float] = {
    "wide":   8.5,
    "narrow": 6.0,
    "none":   3.0,
}

# Covers BOTH the current builder levels (critical/weak/moderate/strong, see
# _build_health_vital) AND the legacy synonyms older rows carry. The previous
# version mapped only the legacy synonyms, so every grey/distress-zone company
# fell through to None and had its health dimension silently dropped — fixed.
_HEALTH_SCALE: Dict[str, float] = {
    "strong":     8.5,
    "moderate":   5.5,
    "weak":       3.5,
    "critical":   1.5,
    # legacy synonyms (pre-2026 builder output)
    "healthy":    7.0,
    "stressed":   4.0,
    "distressed": 2.0,
}


def _vital_score(
    vitals: Dict[str, Any], vital_name: str
) -> Optional[float]:
    """Return a 0-10 score for `vital_name`, or None if missing/unparseable.

    Prefers an explicit numeric `score.value` — all vitals now emit
    one. Falls back to the status/label string scales for
    valuation/moat/financial_health on reports cached before those vitals
    gained a numeric score, so user-history rows still resolve.
    """
    vital = vitals.get(vital_name)
    if not isinstance(vital, dict):
        return None

    # Preferred path: continuous numeric score (new reports, every vital).
    score_obj = vital.get("score")
    if isinstance(score_obj, dict):
        value = score_obj.get("value")
        if isinstance(value, (int, float)):
            return float(value)
        # An explicit `score` dict whose value is None is a DELIBERATE
        # "unmeasured" signal from the builder (no analyst coverage, no
        # forward estimates, no insider trades, no DCF/snapshot). Return None
        # so compute_quality_score renormalizes this dimension out — do NOT
        # fall through to the legacy string scale (that bridge is only for old
        # reports that carry no `score` dict at all).
        return None

    # Back-compat: derive 0-10 from the status/label string fields.
    if vital_name == "valuation":
        return _VALUATION_SCALE.get((vital.get("status") or "").lower())
    if vital_name == "moat":
        return _MOAT_SCALE.get((vital.get("overall_rating") or "").lower())
    if vital_name == "financial_health":
        return _HEALTH_SCALE.get((vital.get("level") or "").lower())
    return None


# ── Persona Style-Fit Adjustment ────────────────────────────────────
#
# The weighted vital rollup re-weights the SAME 10 sub-scores per persona.
# The style-fit term goes one step further: it nudges the score using a few
# RAW signals through each persona's own lens, so the same stock diverges more
# (Wood rewards 40% growth even if unprofitable; Buffett penalizes a no-moat,
# richly-valued name). It is a bounded NUDGE on top of the weighted base, not a
# second engine.
#
# UNITS (enforced by the collector that builds `_style_signals`):
#   percentages are PERCENT numbers — roe/roic/gross_margin/gross_margin_prev/
#   revenue_growth/revenue_cagr/eps_cagr/mos_pct (e.g. 18.0 == 18%);
#   debt_equity is a ratio (0.4); fcf/net_income/mkt_cap are dollars;
#   moat_score is 0-10. Missing signals contribute nothing (never a penalty for
#   unmeasured data) — mirrors the renormalize-over-present rule below.
#
# DE-DOUBLE-COUNT POLICY (since the `profitability` factor scores ROE/ROA/margins
# vs industry): any style sub that reads a return/margin LEVEL is HALVED so it
# doesn't re-spend signal the factor already owns — Buffett's ROE nudge and
# Ackman's ROIC/ROE quality nudge. Wood's gross-margin sub reads a DELTA/TREND
# (gm − gm_prev), not a level, so it is intentionally left full-strength.

STYLE_FIT_CAP: float = 10.0   # max |adjustment| in score points


def _lin(value: Optional[float], bad: float, good: float) -> Optional[float]:
    """Map `value` onto [-1, +1]: at `bad` → -1, at `good` → +1, linear between
    and clamped outside. `good` may be below `bad` for "lower is better" metrics
    (e.g. debt-to-equity). None in → None out (signal absent)."""
    if value is None or good == bad:
        return None if value is None else 0.0
    frac = (value - bad) / (good - bad)
    return max(-1.0, min(1.0, frac * 2.0 - 1.0))


def _clamp(x: Optional[float], lo: float, hi: float) -> Optional[float]:
    return None if x is None else max(lo, min(hi, x))


def _avg_present(subs) -> float:
    """Average the sub-scores that are present; 0.0 if none."""
    present = [s for s in subs if s is not None]
    return sum(present) / len(present) if present else 0.0


def style_fit_adjustment(persona_key: str, signals: Optional[Dict[str, Any]]) -> float:
    """Bounded persona nudge in [-STYLE_FIT_CAP, +STYLE_FIT_CAP].

    Each persona maps its signature raw signals to sub-scores in [-1, +1],
    averages the ones present, and scales by the cap. Deterministic; returns
    0.0 when `signals` is empty or the persona is unknown."""
    if not signals:
        return 0.0

    def num(key: str) -> Optional[float]:
        v = signals.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    roe = num("roe"); roic = num("roic"); de = num("debt_equity")
    gm = num("gross_margin"); gm_prev = num("gross_margin_prev")
    pe = num("pe_ratio"); fcf = num("fcf"); ni = num("net_income"); mcap = num("mkt_cap")
    rev_g = num("revenue_growth"); rev_cagr = num("revenue_cagr"); eps_cagr = num("eps_cagr")
    moat = num("moat_score"); mos = num("mos_pct")

    # Derived ratios (kept here so the collector only ships raw metrics).
    fcf_yield = (fcf / mcap) if (fcf is not None and mcap not in (None, 0)) else None
    fcf_conv = (fcf / ni) if (fcf is not None and ni is not None and ni > 0) else None
    growth = next((c for c in (eps_cagr, rev_cagr, rev_g) if c is not None and c > 0), None)
    peg = (pe / growth) if (pe is not None and pe > 0 and growth) else None

    subs = []
    if persona_key == "warren_buffett":
        # ROE nudge HALVED — the new `profitability` persona FACTOR already scores
        # ROE/margins vs industry, so don't double-count ROE here at full weight.
        _roe_sub = _lin(roe, 10.0, 20.0)            # ROE 10% → -1, 20% → +1
        subs.append(_roe_sub * 0.5 if _roe_sub is not None else None)
        subs.append(_lin(de, 1.5, 0.5))             # D/E 1.5 → -1, 0.5 → +1
        m = _lin(moat, 4.0, 8.0)                     # moat 0-10
        if m is not None and m > 0 and roe is not None and roe < 15:
            m *= 0.3                                 # a moat with weak returns isn't a Buffett moat
        subs.append(m)
        subs.append(_lin(mos, 0.0, 25.0))           # margin of safety 0% → -1, 25% → +1
    elif persona_key == "cathie_wood":
        subs.append(_lin(rev_g, 20.0, 40.0))        # trailing growth
        subs.append(_lin(rev_cagr, 15.0, 30.0))     # forward CAGR
        if rev_cagr is not None and rev_g is not None:
            subs.append(_clamp(_lin(rev_cagr - rev_g, -15.0, 15.0), -0.5, 0.5))  # acceleration
        if gm is not None and gm_prev is not None:
            subs.append(_clamp(_lin(gm - gm_prev, -3.0, 3.0), -0.4, 0.4))        # margin trend
    elif persona_key == "peter_lynch":
        subs.append(_lin(peg, 2.0, 0.5))            # PEG 2 → -1, 0.5 → +1
        subs.append(_clamp(_lin(growth, 8.0, 22.0), -0.5, 0.5))   # earnings/revenue growth band
        subs.append(_clamp(_lin(de, 1.5, 0.3), -0.3, 0.3))        # net-cash lean
    elif persona_key == "bill_ackman":
        subs.append(_lin(fcf_conv, 0.6, 0.9))       # FCF conversion 60% → -1, 90% → +1
        subs.append(_lin(fcf_yield, 0.02, 0.06))    # FCF yield 2% → -1, 6% → +1
        quality = roic if roic is not None else roe
        # ROIC/ROE LEVEL nudge HALVED — same de-double-count as Buffett's ROE above:
        # the `profitability` persona factor already scores ROE/ROA vs industry, so a
        # full-weight LEVEL nudge here would double-count it. (Wood's margin nudge is a
        # DELTA/trend, not a level, so it is deliberately left full-strength.)
        _quality_sub = _clamp(_lin(quality, 8.0, 15.0), -0.5, 0.5)
        subs.append(_quality_sub * 0.5 if _quality_sub is not None else None)
        subs.append(_clamp(_lin(de, 2.5, 0.8), -0.5, 0.5))        # leverage
    else:
        return 0.0

    return round(_avg_present(subs) * STYLE_FIT_CAP, 2)


def _signals_from_inputs(vitals: Dict[str, Any]) -> Dict[str, Any]:
    """Raw style signals nested under `_scoring_inputs._style_signals` (the
    collector writes them there; the block is stripped before reaching iOS).
    Empty dict if absent — old cached reports get a 0.0 style-fit."""
    if not isinstance(vitals, dict):
        return {}
    sig = vitals.get("_style_signals")
    return sig if isinstance(sig, dict) else {}


def compute_quality_score(
    persona_key: str,
    ticker_report_data: Dict[str, Any],
    signals: Optional[Dict[str, Any]] = None,
) -> float:
    """Persona-weighted overall score in [0, 100].

    Rolls the 10 vital scores into a single number using the persona's weight
    vector, then RENORMALIZES over the vitals actually present so missing data
    redistributes weight instead of capping the score. A dimension we couldn't
    measure should drop out and let the measured ones speak — not be scored as
    if the company FAILED it. When nothing is measurable, returns 0.0.

    SHARED 0-10 ANCHOR: every builder emits its vital on the same scale —
    5.0 = neutral/par, >=6.5 = good, <3.5 = weak. Keep that anchor when adding
    or tuning a vital, otherwise the persona weights stop being comparable.

    Unknown persona keys fall back to equal weights across all 10 vitals,
    so calling this with a bad key never crashes and never silently
    returns a Buffett-default.
    """
    weights = PERSONA_WEIGHTS.get(persona_key, _EQUAL_WEIGHTS)
    # `_scoring_inputs` is the internal per-dimension score layer; the legacy
    # "key_vitals" fallback covers reports cached before the key was renamed.
    vitals = (
        ticker_report_data.get("_scoring_inputs")
        or ticker_report_data.get("key_vitals")
        or {}
    )

    weighted_sum = 0.0
    present_weight = 0.0
    for vital_name, weight in weights.items():
        score = _vital_score(vitals, vital_name)
        if score is None:
            continue
        # Clip into [0, 10] in case a builder emits something out of band.
        clipped = max(0.0, min(10.0, score))
        weighted_sum += weight * clipped
        present_weight += weight

    # No measurable vital → honest 0.0 (better than a confident neutral).
    if present_weight <= 0.0:
        return 0.0

    # Renormalize over the measured weight mass (≤ 1.0), scale 0-10 → 0-100.
    overall = (weighted_sum / present_weight) * 10.0

    # Persona style-fit nudge (bounded ±STYLE_FIT_CAP). Default the signals from
    # `_scoring_inputs._style_signals` so BOTH scoring sites (the collector and
    # the research_service re-score) compute identically without extra plumbing.
    if signals is None:
        signals = _signals_from_inputs(vitals)
    overall += style_fit_adjustment(persona_key, signals)

    # Clip, round to one decimal so iOS sees stable, non-jittering numbers.
    return round(max(0.0, min(100.0, overall)), 1)
