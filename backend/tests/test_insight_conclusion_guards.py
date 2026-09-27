"""The Insights card's conclusion checks (`app/services/insight_conclusion.py`).

TestFlight, ETHUSD, 2026-09-10: the ↳ conclusion was "A proposed $5,000 dividend
could boost ETH if Republicans control Congress" under three points that never
mentioned a dividend, $5,000, Republicans or Congress. Every guard below exists to
catch that shape — and each has must-NOT-flag twins, because a guard that rejects
honest conclusions freezes the card the same way the old cap did.
"""

import pytest

from app.services.insight_conclusion import (
    ConclusionCheck,
    check_conclusion,
    duplicates_a_point,
    extract_figures,
    novelty_flags,
    opens_with_people_framing,
    pct_figure,
    repair_note,
    stale_timing_claims,
    unsupported_figures,
)

ETH_POINTS = [
    "Ethereum ETFs saw inflows while Bitcoin ETFs experienced outflows, indicating selective institutional interest.",
    "Researchers significantly reduced estimates for quantum computing attacks on Ethereum, though upgrades are still planned.",
    "Analysts project ETH could reach $11,800 by 2030, driven by fee revenue and staking yields.",
]
ETH_HEADLINE = "ETH faces mixed signals amid ETF flows and quantum risks"
ETH_BAD = "A proposed $5,000 dividend could boost ETH if Republicans control Congress, potentially increasing liquidity."


def test_the_testflight_eth_conclusion_is_caught_three_ways():
    check = check_conclusion(ETH_BAD, ETH_POINTS, ETH_HEADLINE, subject_terms=["ETH", "Ethereum"])
    assert check.figures == ["$5,000"]
    assert check.hard
    assert any("dividend" in n for n in check.novelty)
    assert any("Republicans" in n for n in check.novelty)
    assert any("Congress" in n for n in check.novelty)


def test_an_honest_eth_conclusion_is_clean():
    good = ("Institutional money is leaning toward Ethereum for now, but the long-range "
            "bull case still rests on forecasts more than on this week's flows.")
    check = check_conclusion(good, ETH_POINTS, ETH_HEADLINE, subject_terms=["ETH", "Ethereum"])
    assert check.clean, check.reasons()


# ── figures ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("conclusion,source", [
    ("The $638B backlog is the swing factor.", "a $638 billion backlog"),
    ("The $0.64 trillion backlog is the swing factor.", "a $638 billion backlog"),
    ("A 12% slide tests patience.", "down 12.4% this year"),
    ("A 3.5% yield anchors the stock.", "yields 3.50%"),
    ("About $600 billion of backlog is the swing factor.", "a $638 billion backlog"),
    ("A 50 bps cut matters.", "rates fell 0.5% on the cut"),
    ("An 11% drop resets the bar.", "shares fell -11% after hours"),
    ("A 30x multiple leaves little room.", "trades at 30x earnings"),
    ("Revenue of $5-6 billion is the range to beat.", "guided to $5 billion to $6 billion"),
    ("A 5 to 6% range frames the guide.", "guided 5% to 6% growth"),
    ("40 cents of EPS is the bar.", "EPS of $0.40 expected"),
    ("2 million new users is the story.", "added 2 million users"),
    # review 2026-09-27: honest restatements the matcher used to call new figures
    ("A backlog above $450 billion is the swing factor.", "a $455 billion backlog"),
    ("A $5 billion buyback anchors the stock.", "a US$5 billion buyback"),
    ("A US$5 billion buyback anchors the stock.", "a $5 billion buyback"),
    ("At 30x earnings the stock leaves little room.", "trades at 30 times forward earnings"),
])
def test_equivalent_figures_are_supported(conclusion, source):
    assert unsupported_figures(conclusion, [source]) == []


@pytest.mark.parametrize("conclusion,source", [
    ("A 12% slide tests patience.", "down 11% this year"),
    ("The $600 billion backlog is the swing factor.", "a $638 billion backlog"),
    ("A €5bn deal changes the math.", "a $5bn deal"),
    ("A $5,000 dividend could boost ETH.", "ETH could reach $11,800 by 2030"),
])
def test_different_figures_are_flagged(conclusion, source):
    assert unsupported_figures(conclusion, [source]) != []


@pytest.mark.parametrize("text", [
    "Q1 results beat on revenue.",
    "FY2027 guidance was raised.",
    "The S&P 500 and Nasdaq-100 both slipped.",
    "The 10-year yield rose.",
    "3M reported on Tuesday.",
    "By 2030 the network could look different.",
    "Its 20-day average is rising.",
    "A 4Q26 launch is planned.",
])
def test_names_and_dates_with_numbers_are_not_figures(text):
    assert extract_figures(text) == []


def test_catalyst_and_quote_moves_are_allowed_figures():
    assert unsupported_figures("The 20% jump already prices in the beat.", [], [pct_figure(20.4)]) == []
    assert unsupported_figures("The 20% jump already prices in the beat.", ["up 20.4% on the day"]) == []


@pytest.mark.parametrize("bad", [None, "5", True, float("nan"), float("inf")])
def test_pct_figure_rejects_non_numbers(bad):
    assert pct_figure(bad) is None


def test_extract_handles_empty_and_none():
    assert extract_figures("") == []
    assert extract_figures(None) == []


# ── framing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Investors should care because the backlog is growing.",
    "Everyday investors should watch the guidance.",
    "Investors should watch how Oracle navigates the delays.",
    "For investors, the cut lowers yields.",
    "Long-term holders face a longer wait.",
    "Shareholders get a bigger dividend.",
    "You may see higher mortgage rates.",
    "If you own the stock, the call matters.",
    "Anyone holding the stock faces volatility.",
    "This matters because the backlog is huge.",
    "Why it matters: the backlog is huge.",
    "Investor should expect a volatile week.",
    '"Investors should care" is the wrong frame.',
])
def test_people_framing_is_flagged(text):
    assert opens_with_people_framing(text)


@pytest.mark.parametrize("text", [
    "Investor demand for ether ETFs is growing.",
    "Oracle's backlog gives it room, but the stock's slide shows doubt.",
    "The Fed's pause keeps borrowing costs high.",
    "Youth unemployment is a separate story.",
    "Holdings of Treasuries rose.",
])
def test_ordinary_subjects_are_not_framing(text):
    assert not opens_with_people_framing(text)


# ── novelty ──────────────────────────────────────────────────────────────────

def test_event_words_already_in_the_points_are_fine():
    flags = novelty_flags(
        "The buyback cushions the downside.", "Oracle announced a $10 billion buyback program",
    )
    assert flags == []


def test_event_word_matched_by_stem():
    flags = novelty_flags("Tariffs could squeeze margins.", "new tariff threats weigh on chips")
    assert flags == []


def test_calendar_words_and_subject_terms_are_not_names():
    flags = novelty_flags(
        "Results on Thursday put Oracle's cloud growth in focus through September.",
        "cloud growth accelerated", subject_terms=["Oracle", "ORCL"],
    )
    assert flags == []


def test_relative_day_words_are_flagged():
    flags = novelty_flags("Tonight's report decides the next leg.", "report due")
    assert 'relative day "Tonight"' in flags


def test_acronyms_are_not_flagged_as_names():
    assert novelty_flags("The AI and ETF story continues.", "ai demand") == []


# ── duplicate ────────────────────────────────────────────────────────────────

def test_an_exact_restatement_is_flagged_and_a_synthesis_is_not():
    point = "Oracle's stock has fallen 17% year-to-date despite its large backlog."
    assert duplicates_a_point("Oracle's stock has fallen 17% year-to-date despite its large backlog.", [point])
    assert not duplicates_a_point(
        "The backlog has not yet translated into confidence in the stock.", [point],
    )


# ── stale timing ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Oracle is set to report Q1 earnings with options predicting a big move.",
    "Oracle's upcoming earnings report will be key.",
    "Shares slipped ahead of its quarterly results.",
    "The stock is heading into earnings with momentum.",
    "Oracle is scheduled to release results after the close.",
    "The options market is pricing in a significant stock move, with potential for an 11% swing by week's end.",
    "Options markets are signaling anticipation of a large stock price move following the earnings report.",
    "Traders see an implied move of 11% on the print.",
    # review 2026-09-27: a "next" elsewhere in the sentence no longer hides the claim
    "Oracle is set to report Q1 results as investors weigh next year's AI capex.",
    "Oracle heads into its earnings with the next leg of AI spending in focus.",
    "Oracle is expected to post its quarterly results after the close.",
])
def test_stale_timing_is_found(text):
    assert stale_timing_claims(text)


@pytest.mark.parametrize("text", [
    "Oracle reported Q1 results that beat on revenue.",
    "The stock rallied ahead of its next report in December.",
    "Oracle is set to expand capacity in Texas.",
    "Oracle granted stock options to executives after the move to Texas.",
    "Options volume doubled as the stock rallied.",
    # review 2026-09-27: honest post-report text the old verb pattern flagged
    "Shares slipped due to post-earnings profit-taking.",
    "Oracle is poised to post its best day since 1992.",
    "Oracle is expected to post 40% cloud growth in fiscal 2027, per its guidance.",
    "Oracle is set to announce new AI data center deals.",
    "Oracle is set to report next quarter's numbers in December.",
    "Apple is set to announce a new buyback program on Friday.",
    "Tesla is expected to post record deliveries for the quarter.",
    "Oracle is scheduled to release its cloud roadmap at its October summit.",
    "",
])
def test_honest_timing_is_not_found(text):
    assert stale_timing_claims(text) == []


def test_timing_is_only_checked_once_the_report_happened():
    points = ["Oracle is set to report Q1 earnings after the close."]
    pre = check_conclusion("Results decide whether the backlog converts.", points, "Oracle eyes growth")
    assert pre.timing == []
    post = check_conclusion(
        "Results decide whether the backlog converts.", points, "Oracle eyes growth",
        report_happened=True,
    )
    assert post.timing == ["set to report"]
    assert not post.clean


# ── the verdict + repair note ─────────────────────────────────────────────────

def test_repair_note_names_tokens_but_never_article_text():
    check = check_conclusion(ETH_BAD, ETH_POINTS, ETH_HEADLINE)
    note = repair_note(check)
    assert "$5,000" in note
    assert "staking yields" not in note and "Bitcoin ETFs" not in note
    assert repair_note(ConclusionCheck()) == ""


def test_hard_soft_clean_properties():
    assert ConclusionCheck().clean
    assert ConclusionCheck(figures=["$1"]).hard
    assert ConclusionCheck(framing=True).soft and not ConclusionCheck(framing=True).hard
    assert not ConclusionCheck(timing=["set to report"]).clean


def test_guards_are_linear_on_long_hostile_input():
    import time
    hostile = ("$1,000,000," * 400) + ("9" * 2000) + (" billion" * 200)
    start = time.perf_counter()
    check_conclusion(hostile, [hostile], hostile, report_happened=True)
    assert time.perf_counter() - start < 2.0
