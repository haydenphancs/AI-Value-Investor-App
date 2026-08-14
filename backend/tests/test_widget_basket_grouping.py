"""The correlated-move case: "5 of your holdings fell together, and here's why."

Most of this file asserts that we say NOTHING. That is the point. A group claim
invents a shared cause, and the reader acts on it — so the bar for making one has
to be higher than "several numbers are red today".

The subtle one is `test_a_single_sector_portfolio_gets_no_sector_claim`. If every
holding is Technology, then "all your movers are Technology" is a fact about the
portfolio, not about the market. Reporting it as the driver is circular, and it
would fire on the most concentrated portfolios — exactly the users most likely to
believe it.
"""

from __future__ import annotations

import pytest

from app.services.widget_movers_service import detect_basket, rank_movers


def _holdings(*specs):
    """(ticker, change_percent, sigma_daily) → ranked movers."""
    return rank_movers(
        [{"ticker": t, "change_percent": c, "sigma_daily": s} for t, c, s in specs]
    )


TECH = "Technology"
STAPLES = "Consumer Staples"
HEALTH = "Health Care"


# ── refusals ──────────────────────────────────────────────────────────


def test_a_two_holding_portfolio_is_never_a_group():
    """Two holdings agree by coincidence about half the time."""
    h = _holdings(("NVDA", -5.0, 0.03), ("AMD", -6.0, 0.03))
    assert detect_basket(h, {"NVDA": TECH, "AMD": TECH}) is None


def test_fewer_than_three_movers_is_not_a_group():
    h = _holdings(
        ("NVDA", -5.0, 0.03), ("AMD", -6.0, 0.03),
        ("KO", -0.1, 0.01), ("PG", 0.05, 0.01), ("JNJ", 0.02, 0.01),
    )
    assert detect_basket(h, {"NVDA": TECH, "AMD": TECH, "KO": STAPLES}) is None


def test_mixed_directions_are_not_a_shared_driver():
    """Some up, some down is an ordinary day, not a factor."""
    h = _holdings(
        ("NVDA", 5.0, 0.03), ("AMD", -6.0, 0.03),
        ("AVGO", -5.0, 0.03), ("MU", 4.0, 0.03),
    )
    assert detect_basket(h, {t: TECH for t in ("NVDA", "AMD", "AVGO", "MU")}) is None


def test_a_single_sector_portfolio_gets_no_sector_claim():
    """Breadth in a concentrated portfolio is an artifact, not a factor.

    The movers are 100% Technology — but so is everything the user owns, so
    "Technology drove it" explains nothing. The group is still reported (they did
    all fall); the *cause* is not attributed.
    """
    h = _holdings(("NVDA", -5.0, 0.03), ("AMD", -6.0, 0.03), ("AVGO", -4.5, 0.03))
    b = detect_basket(h, {"NVDA": TECH, "AMD": TECH, "AVGO": TECH})
    assert b is not None
    assert b.factor_kind is None
    assert b.factor_label is None
    assert "no single sector" in b.text


def test_unknown_sectors_never_become_a_factor():
    """Missing data must not be bucketed into an "Other" sector and reported."""
    h = _holdings(
        ("A", -5.0, 0.03), ("B", -6.0, 0.03), ("C", -4.5, 0.03), ("KO", -0.1, 0.01)
    )
    b = detect_basket(h, {"KO": STAPLES})   # A/B/C have no sector at all
    assert b is not None
    assert b.factor_kind is None


def test_an_empty_or_tiny_portfolio_returns_none():
    assert detect_basket([], {}) is None
    assert detect_basket(_holdings(("A", -9.0, 0.02)), {"A": TECH}) is None


def test_calm_holdings_do_not_form_a_group():
    """Everything drifting −0.2% is not "moving together"."""
    h = _holdings(
        ("A", -0.2, 0.02), ("B", -0.1, 0.02), ("C", -0.15, 0.02), ("D", -0.05, 0.02)
    )
    assert detect_basket(h, {"A": TECH, "B": STAPLES, "C": HEALTH, "D": TECH}) is None


# ── the affirmative case ──────────────────────────────────────────────


def test_a_real_sector_move_is_named():
    h = _holdings(
        ("NVDA", -4.0, 0.03), ("AMD", -5.0, 0.03), ("AVGO", -3.5, 0.03),
        ("KO", -0.2, 0.01), ("PG", 0.1, 0.01),
    )
    b = detect_basket(
        h, {"NVDA": TECH, "AMD": TECH, "AVGO": TECH, "KO": STAPLES, "PG": STAPLES}
    )
    assert b is not None
    assert b.direction == "down"
    assert b.moved_count == 3
    assert b.total_count == 5
    assert b.factor_kind == "sector"
    assert b.factor_label == TECH
    assert b.tickers == ["AMD", "AVGO", "NVDA"]      # sorted → stable rendering
    assert b.average_change_percent == pytest.approx(-4.17, abs=0.01)
    assert TECH in b.text


def test_an_upward_group_reads_as_rose():
    h = _holdings(
        ("NVDA", 4.0, 0.03), ("AMD", 5.0, 0.03), ("AVGO", 3.5, 0.03),
        ("KO", 0.1, 0.01), ("PG", -0.1, 0.01),
    )
    b = detect_basket(
        h, {"NVDA": TECH, "AMD": TECH, "AVGO": TECH, "KO": STAPLES, "PG": STAPLES}
    )
    assert b is not None and b.direction == "up"
    assert "rose" in b.text
    assert (b.average_change_percent or 0) > 0


def test_a_sector_minority_does_not_get_named():
    """2 of 4 movers sharing a sector is not "mostly" — the share bar is 2/3."""
    h = _holdings(
        ("NVDA", -4.0, 0.03), ("AMD", -5.0, 0.03),
        ("JNJ", -4.0, 0.03), ("KO", -4.5, 0.03),
        ("XOM", 0.05, 0.02),
    )
    b = detect_basket(
        h, {"NVDA": TECH, "AMD": TECH, "JNJ": HEALTH, "KO": STAPLES, "XOM": "Energy"}
    )
    assert b is not None
    assert b.factor_kind is None


def test_sigma_less_holdings_use_the_percent_fallback():
    """No σ → a 2% move still counts as "moved", mirroring _NOTABLE_PCT."""
    h = _holdings(("A", -3.0, None), ("B", -4.0, None), ("C", -2.5, None), ("D", -0.1, None))
    b = detect_basket(h, {"A": TECH, "B": TECH, "C": TECH, "D": STAPLES})
    assert b is not None
    assert b.moved_count == 3


def test_counts_describe_the_readable_portfolio_not_the_raw_input():
    """An unreadable holding is dropped by ranking, so "of your N" must not count it."""
    h = _holdings(
        ("A", -5.0, 0.03), ("B", -6.0, 0.03), ("C", -4.0, 0.03),
        ("BROKEN", float("nan"), 0.03),
    )
    b = detect_basket(h, {"A": TECH, "B": TECH, "C": HEALTH, "BROKEN": TECH})
    assert b is not None
    assert b.total_count == 3
    assert "BROKEN" not in b.tickers


def test_the_text_and_the_numbers_can_never_disagree():
    """The sentence is rendered from the same fields it is shipped beside."""
    h = _holdings(
        ("NVDA", -4.0, 0.03), ("AMD", -5.0, 0.03), ("AVGO", -3.5, 0.03),
        ("KO", -0.2, 0.01), ("PG", 0.1, 0.01),
    )
    b = detect_basket(
        h, {"NVDA": TECH, "AMD": TECH, "AVGO": TECH, "KO": STAPLES, "PG": STAPLES}
    )
    assert f"{b.moved_count} of your {b.total_count}" in b.text
    assert b.factor_label in b.text
