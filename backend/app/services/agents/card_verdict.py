"""Deterministic verdict labels for the 4 Fundamentals & Growth cards.

Replaces the old per-card Gemini "quality_label" job. The label + footer
sentiment are derived ENTIRELY from the per-metric 1-5 scores the snapshot
services already compute against the industry/sector benchmark — so:

  * it's reliable (always matches the data, the star rating, and the new
    directional-band / threshold-zone charts),
  * it's sector-aware "for free" (the score is already normalized to the peer
    group — a 5 means "great for THIS industry"),
  * it's free (no AI call / quota) and identical in logic across all 4 cards.

Pure functions, no I/O — covered by tests/test_card_verdict.py.

NOTE: this is ONLY the short comment under each card. The separate "✨ Insight"
block (overall_assessment) stays AI-generated and is untouched.
"""

from typing import List, Optional, Tuple

# Per-card metric weights — mirror the snapshot services' rating weights so the
# label anchors on the metric that actually moved the rating (tie-break only).
_WEIGHTS = {
    "Profitability": {"gross_margin": 0.15, "operating_margin": 0.20, "net_margin": 0.25, "roe": 0.25, "roa": 0.15},
    "Growth": {"revenue_growth": 0.30, "eps_growth": 0.30, "fcf_growth": 0.20, "operating_income_growth": 0.20},
    "Valuation": {"pe": 0.25, "pb": 0.15, "ps": 0.15, "pfcf": 0.20, "ev_ebitda": 0.25},
    "Health": {"altman_z": 0.40, "debt_to_equity": 0.15, "current_ratio": 0.15, "interest_coverage": 0.15, "quick_ratio": 0.15},
}

# metric_key -> (strong phrase, weak phrase). "{peer}" -> "Industry"/"Sector".
# Absolute-threshold metrics (altman_z) carry no "{peer}" (no benchmark).
_VOCAB = {
    "gross_margin": ("Fat Margins vs {peer}", "Thin Margins vs {peer}"),
    "operating_margin": ("Strong Operating Margins", "Weak Operating Margins"),
    "net_margin": ("Fat Bottom-Line Margins", "Thin Bottom-Line Margins"),
    "roe": ("Top Returns on Equity", "Weak Returns on Equity"),
    "roa": ("Efficient Asset Use", "Poor Asset Returns"),
    "revenue_growth": ("Rapid Sales Growth", "Slowing Sales"),
    "eps_growth": ("Surging Earnings", "Falling Earnings"),
    "fcf_growth": ("Cash Flow Surging", "Burning Cash"),
    "operating_income_growth": ("Rising Operating Profit", "Shrinking Operating Profit"),
    "pe": ("Cheaper Than {peer}", "Pricey vs {peer}"),
    "pb": ("Low Price-to-Book", "Rich Price-to-Book"),
    "ps": ("Cheap on Sales", "Pricey on Sales"),
    "pfcf": ("Cheap on Cash Flow", "Pricey on Cash Flow"),
    "ev_ebitda": ("Cheap vs {peer}", "Expensive vs {peer}"),
    "debt_to_equity": ("Light Debt Load", "Heavy Debt Load"),
    "current_ratio": ("Strong Liquidity", "Tight Liquidity"),
    "quick_ratio": ("Solid Quick Liquidity", "Weak Quick Liquidity"),
    "interest_coverage": ("Easily Covers Interest", "Strained Interest Cover"),
    "altman_z": ("Rock-Solid Balance Sheet", "Bankruptcy Risk"),
}


def _peer_word(peer_level: Optional[str]) -> str:
    return "Industry" if peer_level == "industry" else "Sector"


def _rating_fallback(star_rating: int, peer: str) -> Tuple[str, str]:
    """No usable per-metric scores → a generic, peer-aware label from the rating."""
    if star_rating >= 4:
        return f"Beats {peer} Average", "positive"
    if 1 <= star_rating <= 2:
        return f"Below {peer} Average", "negative"
    return f"In Line With {peer}", "neutral"


def generate_card_verdict(
    title: str,
    star_rating: int,
    peer_level: Optional[str],
    scored_metrics: List[Tuple[Optional[str], Optional[int]]],
) -> Tuple[str, str]:
    """Return (label, sentiment) for one card.

    `scored_metrics` is a list of (metric_key, score) — score in 1..5, or None
    for unscored/informational rows (skipped). `peer_level` is "industry" /
    "sector" / None (the card's uniform peer group).

    Composition (mirrors the old 1-2 phrase style): one dominant STRENGTH
    (score >= 4) and/or one dominant DRAG (score <= 2), else "In Line With Peer".
    Sentiment is grounded in the drivers, NOT keyword-matched: a strength with no
    drag is positive; a drag (even on a high-starred card) is negative; a mix is
    neutral (iOS then falls back to the star color).
    """
    peer = _peer_word(peer_level)
    weights = _WEIGHTS.get(title, {})

    valid = [
        (k, s) for (k, s) in scored_metrics
        if k and s is not None and k in _VOCAB
    ]
    if not valid:
        return _rating_fallback(star_rating, peer)

    def weight(k: str) -> float:
        return weights.get(k, 0.1)

    # Strongest strength: highest score, tie-break by card weight.
    strengths = sorted(
        [(k, s) for (k, s) in valid if s >= 4],
        key=lambda x: (x[1], weight(x[0])), reverse=True,
    )
    # Most severe drag: lowest score, tie-break by card weight (desc).
    drags = sorted(
        [(k, s) for (k, s) in valid if s <= 2],
        key=lambda x: (x[1], -weight(x[0])),
    )

    def phrase(key: str, strong: bool) -> str:
        return _VOCAB[key][0 if strong else 1].replace("{peer}", peer)

    if strengths and drags:
        return f"{phrase(strengths[0][0], True)}, {phrase(drags[0][0], False)}", "neutral"
    if strengths:
        return phrase(strengths[0][0], True), "positive"
    if drags:
        return phrase(drags[0][0], False), "negative"
    return f"In Line With {peer}", "neutral"
