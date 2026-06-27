"""Deterministic card-verdict label/sentiment table.

The per-card comment under each Fundamentals & Growth card is generated purely
from the sector-relative metric scores (no AI). These cases pin the composition,
sentiment, peer-group wording, and degraded paths.
"""

from app.services.agents.card_verdict import generate_card_verdict


def test_all_strong_leads_with_top_weighted_strength():
    # ROE is the clear top → positive, single strength phrase.
    label, sent = generate_card_verdict(
        "Profitability", 5, "industry",
        [("roe", 5), ("net_margin", 4), ("gross_margin", 4),
         ("operating_margin", 4), ("roa", 4)],
    )
    assert label == "Top Returns on Equity"
    assert sent == "positive"


def test_strength_plus_drag_is_mixed_neutral():
    # Growth: earnings surging but cash burning → both phrases, neutral footer.
    label, sent = generate_card_verdict(
        "Growth", 3, "industry",
        [("eps_growth", 5), ("fcf_growth", 1),
         ("revenue_growth", 3), ("operating_income_growth", 3)],
    )
    assert label == "Surging Earnings, Burning Cash"
    assert sent == "neutral"


def test_drag_only_is_negative_even_on_high_rating():
    # 4-star Health card but the dominant driver is heavy debt → red.
    label, sent = generate_card_verdict(
        "Health", 4, "industry",
        [("debt_to_equity", 1), ("current_ratio", 3),
         ("quick_ratio", 3), ("interest_coverage", 3), ("altman_z", 3)],
    )
    assert label == "Heavy Debt Load"
    assert sent == "negative"


def test_valuation_pricey_picks_top_weighted_drag_and_skips_unscored():
    # P/E is the heaviest-weighted drag; Earnings Yield (score None) is ignored.
    label, sent = generate_card_verdict(
        "Valuation", 2, "industry",
        [("pe", 1), ("ev_ebitda", 2), ("pb", 3), ("ps", 3),
         ("pfcf", 3), ("earnings_yield", None)],
    )
    assert label == "Pricey vs Industry"
    assert sent == "negative"


def test_all_in_line():
    label, sent = generate_card_verdict(
        "Profitability", 3, "industry",
        [("gross_margin", 3), ("net_margin", 3), ("roe", 3)],
    )
    assert label == "In Line With Industry"
    assert sent == "neutral"


def test_sector_peer_wording():
    label, _ = generate_card_verdict(
        "Profitability", 5, "sector",
        [("gross_margin", 5), ("roe", 3)],
    )
    assert label == "Fat Margins vs Sector"


def test_altman_z_distress_is_absolute_no_peer():
    label, sent = generate_card_verdict(
        "Health", 1, "industry",
        [("altman_z", 1), ("current_ratio", 3), ("debt_to_equity", 3)],
    )
    assert label == "Bankruptcy Risk"
    assert "Industry" not in label and "Sector" not in label
    assert sent == "negative"


def test_altman_z_safe_is_positive():
    label, sent = generate_card_verdict(
        "Health", 5, "industry",
        [("altman_z", 5), ("current_ratio", 3), ("quick_ratio", 3)],
    )
    assert label == "Rock-Solid Balance Sheet"
    assert sent == "positive"


def test_missing_scores_falls_back_to_rating():
    assert generate_card_verdict("Growth", 5, "industry", [("revenue_growth", None)]) \
        == ("Beats Industry Average", "positive")
    assert generate_card_verdict("Growth", 2, "sector", []) \
        == ("Below Sector Average", "negative")
    assert generate_card_verdict("Growth", 3, "industry", [("x", None)]) \
        == ("In Line With Industry", "neutral")


def test_tie_break_by_card_weight():
    # net_margin (weight .25) beats gross_margin (.15) at equal score.
    label, _ = generate_card_verdict(
        "Profitability", 5, "industry",
        [("gross_margin", 5), ("net_margin", 5), ("roe", 3), ("roa", 3)],
    )
    assert label == "Fat Bottom-Line Margins"


def test_unknown_peer_level_defaults_to_sector_word():
    label, _ = generate_card_verdict(
        "Valuation", 1, None, [("pe", 1)],
    )
    assert label == "Pricey vs Sector"
