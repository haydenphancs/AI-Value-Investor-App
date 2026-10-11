"""The News Tone chart's deterministic filters (app/services/news_tone_filters.py).

Headlines here are REAL shapes from the live feed (2026-10-10). Every rule has must-not-match
twins: dropping a real story from the chart is worse than counting one advert, so a row that
widens a rule must keep these green.
"""

from __future__ import annotations

import time

import pytest

from app.services.news_tone_filters import (
    is_law_firm_solicitation,
    is_routine_holdings_filing,
    title_key,
)

# ── Routine holdings filings ─────────────────────────────────────────────────────

HOLDINGS = [
    "GAMMA Investing LLC Acquires 7,458 Shares of Meta Platforms, Inc. $META",
    "Meta Platforms, Inc. $META Holdings Reduced by Diversified Management Inc.",
    "Galvin Gaustad & Stein LLC Decreases Position in Meta Platforms, Inc. $META",
    "Grove Bank & Trust Cuts Stock Holdings in Meta Platforms, Inc. $META",
    "13,317 Broadcom Inc. $AVGO Shares Purchased by GAMMA Investing LLC",
    "HWG Holdings LP Cuts Stake in Broadcom Inc. $AVGO",
    "Echo45 Advisors LLC Lowers Holdings in Apple Inc. $AAPL",
    "Ayalon Insurance Comp Ltd. Grows Holdings in Meta Platforms, Inc. $META",
    "BlackRock Inc. Buys 31,939,194 Shares of Plug Power, Inc. $PLUG",
    "NVIDIA Corporation $NVDA Shares Sold by Maxi Investments CY Ltd",
    "Teradyne, Inc. $TER Position Boosted by Baird Financial Group Inc.",
    "AlphaGrep UK Ltd Takes $825,000 Position in Teradyne, Inc. $TER",
    "Apple Inc. $AAPL Shares Newly Purchased by Gainline Financial Partners LLC",
    "Broadcom Inc. $AVGO Stock Position Lessened by Sapient Capital LLC",
    "Microsoft Corporation $MSFT Stock Holdings Increased by Occidental Asset Management LLC",
    "Apple Inc. $AAPL Stock Position Cut by Amundi",
    "Texas Capital Bank Wealth Management Services Inc Grows Stake in Microsoft Corporation $MSFT",
    "Meta Platforms, Inc. $META Stock Holdings Trimmed by Overbrook Management Corp",
    "147,497 Meta Platforms, Inc. $META Shares Acquired by Artemis Investment Management LLP",
    "MassMutual Private Wealth & Trust FSB Reduces Stock Holdings in The Walt Disney Company $DIS",
]

NOT_HOLDINGS = [
    "Amazon Raises Stake in Anthropic",                       # real news: no cashtag
    "Microsoft Takes Stake in OpenAI",
    "Nvidia $NVDA Raises Stake in xAI",                       # the company's own deal, cashtag first
    "$NVDA shares jump after earnings beat",
    "Broadcom shares slip in premarket after $50B OpenAI financing report",
    # Insider (Form 4) lines are the model's call, not this template's.
    "Plug Power (NASDAQ:PLUG) Insider Benjamin Haycraft Sells 200,000 Shares",
    "CoreWeave’s Venturo sells 65,616 shares after RSU vesting (NASDAQ: CRWV)",
    "A Teradyne Director Sells 400 Shares After the Stock Soared Over 200% in the Past Year",
    "Tesla $TSLA CEO Elon Musk Buys $1B of Shares",
    "", None, 7, ["GAMMA Investing LLC Acquires 7,458 Shares of Meta Platforms, Inc. $META"],
]


@pytest.mark.parametrize("title", HOLDINGS)
def test_the_syndicated_holdings_template_is_caught(title):
    assert is_routine_holdings_filing(title)


@pytest.mark.parametrize("title", NOT_HOLDINGS)
def test_real_news_and_insider_lines_are_not_holdings_filler(title):
    assert not is_routine_holdings_filing(title)


# ── Law-firm solicitations ───────────────────────────────────────────────────────

SOLICITATIONS = [
    "NOTICE: Investors in CoreWeave, Inc. (NASDAQ: CRWV) shares are encouraged to contact the "
    "Shareholders Foundation in connection with Lawsuit",
    "Bronstein, Gewirtz & Grossman LLC Urges Alphabet Inc. Investors to Act: Class Action Filed "
    "Alleging Investor Harm",
    "ROSEN, A GLOBAL AND LEADING LAW FIRM, Encourages Alphabet Inc. Investors to Secure Counsel "
    "Before Important Deadline in Securities Class Action - GOOG",
    "Alphabet Inc. (GOOGL) Shareholders Who Lost Money Have Opportunity to Lead Securities Fraud "
    "Lawsuit",
    "GOOGL, GOOG Investors Have Opportunity to Lead Alphabet Inc. Securities Fraud Lawsuit",
    "GOOG, GOOGL Securities Alert: Lose Money on Your Alphabet Investment? BFA Law Notifies "
    "Investors of the Filed Securities Class Action over AI Issues",
    "GOOGL UPCOMING DEADLINE: Faruqi & Faruqi, LLP Reminds Alphabet Investors of Securities Class "
    "Action Lawsuit Deadline on November 30, 2026",
    "Deadline Alert: Alphabet Inc. (GOOGL) Shareholders Who Lost Money Urged To Contact Glancy "
    "Prongay Wolke & Rotter LLP About Securities Fraud Lawsuit",
    "Alphabet Shareholder Alert: Investors With Losses May Seek to Lead the Class Action in "
    "Alphabet Inc. Securities Lawsuit - Contact SueWallSt",
    "SHAREHOLDER ALERT: Pomerantz Law Firm Investigates Claims On Behalf of Investors of XYZ",
    "Levi & Korsinsky Reminds Shareholders of a Lead Plaintiff Deadline of November 3, 2026",
]

NOT_SOLICITATIONS = [
    "Joby Hit With Over $100 Million in Damages in IP Lawsuit Verdict",
    "The advice immigration attorneys have for Microsoft workers worried about their green cards",
    "Securitize Launches Tokenized US Stocks With Shareholder Rights on Solana",
    "XYZ wins dismissal of securities fraud lawsuit",
    "XYZ agrees to pay $50 million to settle class action",
    "SEC announces investigation into XYZ",
    "XYZ hires law firm to review strategic options",
    "XYZ Board Adopts Shareholder Rights Plan",
    "Elliott urges shareholders to reject Phillips 66 board nominees",      # a proxy fight
    "Investors urged to diversify as tariffs bite",
    "Investors who bought Nvidia a decade ago are now millionaires",
    "Texas attorney general sues XYZ over data practices",
    "Judge certifies class action against XYZ over fees",
    "", None, 3.5,
]


@pytest.mark.parametrize("title", SOLICITATIONS)
def test_law_firm_solicitations_are_caught(title):
    assert is_law_firm_solicitation(title)


@pytest.mark.parametrize("title", NOT_SOLICITATIONS)
def test_real_legal_news_is_not_a_solicitation(title):
    assert not is_law_firm_solicitation(title)


# ── Duplicate titles ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("a,b", [
    ("UPDATE 1-Fed holds rates steady", "Fed Holds Rates Steady!"),
    ("Updated: Nvidia beats estimates", "nvidia   beats estimates"),
    ("CORRECTED - Oracle signs deal", "Oracle signs deal."),
])
def test_wire_copies_share_a_key(a, b):
    assert title_key(a) == title_key(b) is not None


@pytest.mark.parametrize("raw", ["", "   ", "!!!", None, 5, ["Fed holds"]])
def test_no_key_without_words(raw):
    assert title_key(raw) is None


def test_different_stories_keep_different_keys():
    assert title_key("Fed holds rates") != title_key("Fed cuts rates")


# ── Cost: linear on hostile input ───────────────────────────────────────────────


@pytest.mark.parametrize("fn", [is_routine_holdings_filing, is_law_firm_solicitation, title_key])
def test_hostile_titles_are_cheap(fn):
    hostile = [
        "$A " * 5000,
        "shares " * 5000 + "$META",
        "investors " * 5000 + "lawsuit",
        "encourages " * 3000 + "investors " * 3000,
        "a" * 100_000,
    ]
    started = time.perf_counter()
    for t in hostile:
        fn(t)
    assert time.perf_counter() - started < 0.5, "titles are length-capped before any regex"


# ── fix review 2026-10-10: bare alternatives over-blocked real news ──────────────

MORE_SOLICITATIONS = [
    "ROSEN, A GLOBAL AND LEADING LAW FIRM, Announces Filing of Securities Class Action Lawsuit Against XYZ",
    "ROSEN, A LEADING LAW FIRM, Announces Investigation of Securities Claims Against XYZ",
]
MORE_NOT_SOLICITATIONS = [
    "Microsoft sees opportunity to lead in quantum computing",
    "GM and Honda seek to lead hydrogen fuel cell race",
    "SEC alerts investors to crypto fraud investigation",
    "Pfizer reminds shareholders of tender offer deadline",
    "Activist urges Disney shareholders to back its board slate before the deadline",
    "Investor alert: Apple to report earnings Thursday",
    "Former DOJ official Jeffrey Rosen, now at XYZ, weighs in on antitrust case",
    "XYZ reminds shareholders of merger election deadline",
]


@pytest.mark.parametrize("title", MORE_SOLICITATIONS)
def test_releases_that_open_with_the_firm_name_are_caught(title):
    assert is_law_firm_solicitation(title)


@pytest.mark.parametrize("title", MORE_NOT_SOLICITATIONS)
def test_generic_phrasings_need_a_legal_context(title):
    assert not is_law_firm_solicitation(title)
