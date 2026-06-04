"""Unit tests for analyst rating-action classification (`_analyst_common`).

Guards downgrade detection. FMP frequently labels a real rating change
`action="maintain"` (or omits the action), so direction must be inferred from
the previous/new grade labels — including regional-broker labels (RBC "Sector
Perform/Outperform", JMP "Market Outperform", Oppenheimer "Perform") that were
previously unranked and would silently bucket a downgrade as "maintain".

Context: verified live that ORCL's 0 downgrades over the trailing 12 months is
REAL (every maintain is a same-label reiteration). These tests pin the behavior
and the regional-label hardening so other tickers don't miss a cut.
"""
from datetime import datetime, timedelta

from app.services._analyst_common import (
    normalize_fmp_action,
    _infer_rating_direction,
)
from app.services.analyst_service import _compute_actions_summary


def test_explicit_actions_pass_through():
    assert normalize_fmp_action("upgrade", "Hold", "Buy") == "upgrade"
    assert normalize_fmp_action("downgrade", "Buy", "Hold") == "downgrade"
    assert normalize_fmp_action("initiate", None, "Buy") == "initiate"
    assert normalize_fmp_action("initiated", None, "Buy") == "initiate"


def test_same_label_maintain_is_a_reiteration_not_a_change():
    # ORCL real-data shape: prev == new → genuine maintain, never a down/upgrade.
    assert normalize_fmp_action("maintain", "Buy", "Buy") == "maintain"
    assert normalize_fmp_action("maintain", "Outperform", "Outperform") == "maintain"
    assert normalize_fmp_action("maintain", "Sector Outperform", "Sector Outperform") == "maintain"


def test_maintain_labeled_rating_change_is_caught():
    # FMP labels a real cut "maintain"; rank inference must still catch it.
    assert normalize_fmp_action("maintain", "Buy", "Hold") == "downgrade"
    assert normalize_fmp_action("maintain", "Overweight", "Underweight") == "downgrade"
    assert normalize_fmp_action("maintain", "Hold", "Buy") == "upgrade"


def test_regional_broker_labels_are_ranked():
    # Previously unranked → these would have been missed (bucketed "maintain").
    assert _infer_rating_direction("Sector Outperform", "Sector Perform") == "downgrade"
    assert _infer_rating_direction("Sector Perform", "Sector Outperform") == "upgrade"
    assert _infer_rating_direction("Market Outperform", "Market Perform") == "downgrade"
    assert _infer_rating_direction("Perform", "Outperform") == "upgrade"
    # …and through the maintain-relabel path that hides them:
    assert normalize_fmp_action("maintain", "Sector Outperform", "Sector Perform") == "downgrade"


def test_compute_actions_summary_counts_and_12mo_window():
    recent = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    stale = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d")
    grades = [
        {"date": recent, "action": "upgrade", "previousGrade": "Hold", "newGrade": "Buy"},
        {"date": recent, "action": "downgrade", "previousGrade": "Buy", "newGrade": "Hold"},
        {"date": recent, "action": "maintain", "previousGrade": "Buy", "newGrade": "Buy"},
        # A real cut FMP mislabeled "maintain" across regional labels:
        {"date": recent, "action": "maintain", "previousGrade": "Sector Outperform", "newGrade": "Sector Perform"},
        # Outside the trailing 12 months → must be excluded:
        {"date": stale, "action": "downgrade", "previousGrade": "Buy", "newGrade": "Sell"},
    ]
    summ = _compute_actions_summary(grades)
    assert summ.upgrades == 1
    assert summ.downgrades == 2  # explicit + the maintain-labeled Sector cut
    assert summ.maintains == 1
