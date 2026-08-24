"""The contract for the trade-intent gate on the chat disclaimer.

`chat_intent.is_trade_intent` decides whether a turn gets the "educational, not
financial advice" line. These two tables ARE that contract — a phrase's presence here
is the specification, not an illustration of it.

Read the negative table as the important one. Finance prose uses buy / sell / hold /
short / long / position as ordinary words far more often than as trade instructions,
and this app ships features built on exactly that vocabulary: congressional trades,
13F holdings, insider transactions, short interest, and its own Buy/Sell meter. Every
row down there is a way the gate could have fired on a question nobody was asking for
advice about.

Tuned asymmetrically on purpose: a false negative is a compliance miss, a false
positive is a cosmetic oddity. See `test_known_accepted_false_positive`.
"""

from __future__ import annotations

import pytest

from app.services.chat_intent import is_trade_intent


# ── Must fire: the user is asking whether to act ─────────────────────────────

TRADE_INTENT = [
    # canonical, and the phrasings the app itself ships as suggestion chips
    "Should I buy Apple?",
    "Should I buy?",                       # TickerDetailModels.swift / CryptoDetailModels.swift
    "Should I buy #AAPL?",                 # ChatModels.swift — `#` must not break the boundary
    "My portfolio is down 15% this month. Should I hold or sell?",   # LearnModels.swift
    # case / punctuation / terseness
    "should i sell NVDA now?",
    "is tsla worth buying now",
    "do i sell my apple",
    "aapl good buy?",
    "Buy now?",
    # multi-verb and either/or forms
    "Should I hold or sell TSLA?",
    "AAPL buy or sell?",
    "buy or sell?",
    "buy, sell or hold NVDA?",
    # frames without an explicit pronoun
    "Is Apple a good buy right now?",
    "Is it a good time to buy MSFT?",
    "Time to sell my Tesla shares?",
    "Worth buying at these levels?",
    "What's a good entry point?",
    "Is now a good time to short the market?",
    # the verbs the user named, each as a real action
    "Should I short Tesla?",
    "should I trim my position in AAPL?",
    "Should I add to my position?",
    "should i dump my meta shares",
    "Should I get out of Nvidia?",
    "Should I invest in this ETF?",
    "Can I average down here?",
    "Should I take profits on my Nvidia position?",
    "Should I cut my losses on PLTR?",
    "should I go long on oil?",            # contrast: "long-term debt" is masked
    "should i buy the dip on AMZN",
    "should I keep holding AMD?",          # contrast: "top holdings" is masked
    "should i hold through earnings?",
    "should we exit our stake in intel?",
    "position sizing for a $10k account?",
    # sizing / declarative intent / second person
    "How much should I allocate to AAPL?",
    "how many shares should I buy?",
    "I'm thinking of buying Google, good idea?",
    "I want to buy Tesla, thoughts?",
    "thinking about selling my meta",
    "would you buy Apple here?",
    "When should I sell?",
    "help me decide whether to sell",
    # suitability — ADVICE_BOUNDARY's other half. A personalized-fit question IS advice.
    "Is this ETF right for me?",
    "Is AAPL suitable for me?",
    "Does this fit my risk profile?",
    "Given my goals, is this smart?",
    "Based on my risk tolerance, what should I do?",
]


# ── Must NOT fire: nobody is being told what to do ───────────────────────────

INFORMATIONAL = [
    # the user's own examples from the feedback
    "What is the P/E?",
    "Hi",
    "hello",
    "thanks!",
    "what apple fundamentals",
    "What are Apple's fundamentals?",
    # OTHER PEOPLE'S trades — congress / insider / 13F, all shipped features
    "What did the company buy last quarter?",
    "Who bought shares recently?",
    "Which insiders bought AAPL?",
    "Did Nancy Pelosi buy NVDA?",
    "What stocks did Congress buy in March?",
    "What is institutional buying?",
    "Show me insider selling",
    "Explain 13F filings",
    "Who owns the most Apple stock?",
    # `buyback` — the substring trap
    "What is Apple's buyback program?",
    "How big are the buybacks?",
    "What is Affirm's buy now pay later business?",
    # `short` as a metric
    "What is short interest in GME?",
    "What's the short-term debt?",
    "What is Apple's long-term debt?",
    "Explain shorts outstanding",
    "What is the short ratio?",
    "What is short selling?",
    "Explain what days to cover means",
    # `hold` / `sell` as nouns
    "Is this a holding company?",
    "What are the top holdings of SPY?",
    "What is Buffett's biggest holding?",
    "Who are the largest shareholders?",
    "What do sell-side analysts say?",
    "The stock sold off yesterday, why?",
    "Is AAPL oversold?",
    "Is the market overbought?",
    # fundamentals vocabulary that reuses trade verbs
    "How does management allocate capital?",
    "What's capital allocation policy?",
    "What is Nvidia's competitive position?",
    "What's their cash position?",
    "How is Apple positioned in AI?",
    "Tell me about the exit strategy of the CEO",
    "What companies did Microsoft acquire?",
    # the app's own deterministic meter + analyst consensus
    "What's the consensus buy rating?",
    "What does Strong Buy mean on the meter?",
    "Explain the buy/sell meter",
    # definitional — a vocabulary lesson, not a decision
    "What is a stop loss?",
    "What does 'take profit' mean?",
    "Explain what going long means",
    "Explain dollar cost averaging",
    "What is dollar cost averaging?",
    "What is a REIT?",
    # in-app actions and product support
    "How do I add a stock to my watchlist?",
    "Can I add a portfolio?",
    "Can I export my portfolio?",
    "Do I need an account to use this?",
    "What is my portfolio worth?",          # "my portfolio" alone is a lookup, not advice
    "How is my portfolio doing?",
    "Show me my portfolio",
    "What are my holdings?",
    # an advisory frame with NO trade verb behind it
    "Should I read the 10-K or the 10-Q first?",
    "When should I expect the next earnings report?",
    "Is it a good time to look at the balance sheet?",
    "What time do markets open?",
    # `sell` far off-domain
    "How long does it take to sell a house?",
    # ordinary informational lookups
    "What's Apple's revenue growth?",
    "What is the dividend yield?",
    "Show me the cash flow",
    "Why did the stock drop?",
    "Summarize the latest earnings",
    "Is Berkshire a good company?",
    "Why #TSLA moved?",
    "What does this chart mean?",
    "#Tech Stocks",
    "#Crypto",
]


@pytest.mark.parametrize("question", TRADE_INTENT)
def test_trade_intent_is_detected(question):
    assert is_trade_intent(question) is True, question


@pytest.mark.parametrize("question", INFORMATIONAL)
def test_informational_question_does_not_trip_the_gate(question):
    assert is_trade_intent(question) is False, question


def test_tables_are_populated():
    """Anti-vacuity: a parametrize over an empty list passes and proves nothing."""
    assert len(TRADE_INTENT) >= 40
    assert len(INFORMATIONAL) >= 60


@pytest.mark.parametrize("empty", [None, "", "   ", "\n\t "])
def test_empty_input_is_never_trade_intent(empty):
    assert is_trade_intent(empty) is False


def test_known_accepted_false_positive():
    """A documented gap, recorded rather than left to be rediscovered.

    "entry point" is a standalone trigger because "what's a good entry point?" is a
    real trade question with no frame and no verb. The cost is that the definitional
    form also fires. Closing it needs an overfit `in technical analysis` guard; the
    asymmetry (a missed disclaimer is a compliance miss, a spurious one is cosmetic)
    says leave it.
    """
    assert is_trade_intent("What is an entry point in technical analysis?") is True


def test_hash_and_ticker_symbols_do_not_break_word_boundaries():
    # The chips ship with `#AAPL`, and `$` prefixes are common user shorthand.
    assert is_trade_intent("Should I buy $NVDA?") is True
    assert is_trade_intent("should i sell $TSLA") is True


def test_masked_span_cannot_supply_the_verb():
    """The mask runs FIRST, so a trap phrase is gone before the verb stage sees it.

    Both of these carry an advisory frame; only the second has a real trade verb.
    """
    assert is_trade_intent("Should I be worried about short interest?") is False
    assert is_trade_intent("Should I be worried, or should I sell?") is True
