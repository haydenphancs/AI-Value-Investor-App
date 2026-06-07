"""
Tests for the fundamentals-card footer sentiment classifier.

The footer label is AI-written and can contradict the card's star rating
(FMP's snapshot rating, mirrored from the Financials tab). iOS colors the
footer by this sentiment so a negative takeaway reads red even on a
high-starred card — the "Debt 4.21, Far Too High" on a 4-star Health card case.
"""

from __future__ import annotations

import pytest

from app.services.agents.narrative_prompts import _classify_label_sentiment


@pytest.mark.parametrize("label,expected", [
    # The four cards from the Oracle screenshot.
    ("Exceptional 57% Shareholder Return", "positive"),
    ("Growing Sales, Burning Cash", "neutral"),    # mixed → neutral (star fallback)
    ("Burning Cash, Too Pricey", "negative"),
    ("Debt 4.21, Far Too High", "negative"),       # the bug: 4-star card, red footer
    # Other prompt-vocabulary verdicts.
    ("Heavy Debt Load", "negative"),
    ("Too Pricey vs. Sector", "negative"),
    ("Fat Margins, High Debt", "neutral"),         # both signs → neutral
    ("Steady Profits", "positive"),
    ("A Cash Machine", "positive"),
    ("Accelerating Growth", "positive"),
])
def test_classify_label_sentiment(label, expected):
    assert _classify_label_sentiment(label) == expected


def test_classify_label_sentiment_empty_and_fallback():
    # Empty / None / the "—" fallback token must never crash and read neutral
    # (so the iOS footer keeps its star-based color).
    assert _classify_label_sentiment("") == "neutral"
    assert _classify_label_sentiment(None) == "neutral"
    assert _classify_label_sentiment("—") == "neutral"
