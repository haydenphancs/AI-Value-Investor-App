"""`chat_intent.is_web_search_intent` — the gate for report chat's paid `web_search` tool.

Owner decision (2026-10-02): web search only on an EXPLICIT ask — never on bare "research" (the
product is a "research report"), never on Google Search the product, never on searching inside the
report, never on a negated ask. Precision over recall: a false positive declares a paid tool and
collapses a multi-lens answer to one lens.
"""

from __future__ import annotations

import time

import pytest

from app.services.chat_intent import is_trade_intent, is_web_search_intent

WEB_INTENT = [
    # search the web / online
    "Can you search the web for the latest on Apple's DOJ case?",
    "search online for TSMC Arizona fab delays",
    "Search for news about the CEO",
    "Please do a quick web search on Nvidia export curbs",
    "Can you do a search on their CFO?",
    "could you search the internet for the merger terms",
    "Do an internet search on the recall",
    "browse the web for analyst reactions",
    # look it up
    "Can you look it up?",
    "look up whether the merger closed",
    "Look online for analyst reactions",
    "Find this online.",
    "check online if the dividend was raised",
    "check the news on Tesla",
    "Google it",
    "can you google the new CFO",
    # the latest / any news
    "What's the latest news on Broadcom?",
    "what's the latest on the Pfizer trial?",
    "What's the latest?",
    "Any news?",
    "any recent developments?",
    "recent updates on the FTC suit",
    "What's in the news about Meta today?",
    "today's news on Nvidia?",
    "today’s news on Nvidia?",        # iOS smart apostrophe
    # verify / double-check / confirm
    "Can you verify the revenue figure?",
    "double-check that",
    "fact check the margin claim",
    "cross-check the debt numbers",
    "Verify the revenue growth number.",
    "Please verify this.",
    "I'd like you to verify whether the buyback is still running",
    "Confirm whether the buyback is still active",
    "can you confirm",
    "Could this be verified?",
    "sanity-check the free cash flow",
    # still true?
    "Is this still true?",
    "is the bull case still valid?",
    "Is the CEO still in place?",
    "Are these numbers still accurate?",
    # research online / people saying
    "Do some research online about China exposure",
    "research it on the web",
    "What are people saying online about the recall?",
    # negation that does NOT reach the verb
    "Don't just use the report — search the web",
    "I don't trust this number, can you verify it?",
    "I don’t trust this number, can you verify it?",
]

NOT_WEB_INTENT = [
    # bare research is never a trigger
    "Summarize this research report",
    "What does the research say about the moat?",
    "Do more research on the moat",
    "Explain the Cay research findings",
    # search as a noun / Google Search the product
    "How much does Search contribute to Alphabet's revenue?",
    "Is Google Search losing share?",
    "Search revenue grew — in the report?",
    "search engine market share",
    "How's the CEO search going?",
    "the search for a new CEO",
    "How big is the paid search business?",
    # online as an adjective
    "How are online sales trending?",
    "Online advertising revenue?",
    # searching inside the report
    "Can you check online sales growth in the report?",
    "Look up the P/E in the report",
    "Search the report for margin trends",
    "Investors look up to Buffett",
    # negated asks
    "Don't search the web, just use the report",
    "Don’t search the web, just use the report",
    "No need to check the news",
    "Without checking online, what's the thesis?",
    "Do not google anything, answer from the report",
    # latest + another noun
    "What's the latest price?",
    "What are the latest quarter's margins?",
    "When is the latest report from?",
    "What is the news sentiment score?",
    # not on the still-list
    "Is the moat still intact?",
    "Is it still a buy?",
    # bare verify / confirm without a request frame
    "How does Visa confirm transactions?",
    "How does Visa verify transactions?",
    "Verisign's moat?",
    "Can they double the dividend?",
    "Did management confirm that guidance?",
    "What does 'verify' mean?",
    # unrelated
    "Why is the stock up?",
    "Check the debt-to-equity ratio",
    "What is the moat?",
    "Hi",
]


@pytest.mark.parametrize("msg", WEB_INTENT)
def test_explicit_asks_fire(msg):
    assert is_web_search_intent(msg) is True, msg


@pytest.mark.parametrize("msg", NOT_WEB_INTENT)
def test_traps_do_not_fire(msg):
    assert is_web_search_intent(msg) is False, msg


def test_tables_are_populated():
    assert len(WEB_INTENT) >= 40 and len(NOT_WEB_INTENT) >= 35
    assert not set(WEB_INTENT) & set(NOT_WEB_INTENT)


@pytest.mark.parametrize("msg", [
    "research", "Research.", "do research", "more research please", "research report",
    "the research report says", "RESEARCH THE MOAT", "is the research outdated?",
])
def test_bare_research_never_fires(msg):
    assert is_web_search_intent(msg) is False, msg


@pytest.mark.parametrize("pair", [
    ("search the web for the recall", "Don't search the web for the recall"),
    ("check the news", "No need to check the news"),
    ("verify the margin", "never verify the margin, just explain it"),
    ("google it", "Don't google it"),
])
def test_negation_masks_the_ask(pair):
    ask, negated = pair
    assert is_web_search_intent(ask) is True
    assert is_web_search_intent(negated) is False


@pytest.mark.parametrize("msg", [
    "search the report for the moat score", "look it up in the report", "find the margin in the analysis",
    "check the data in this report", "search this section for risks",
])
def test_report_internal_search_is_not_web(msg):
    assert is_web_search_intent(msg) is False, msg


@pytest.mark.parametrize("junk", [None, "", "   ", b"search the web", 123, ["search the web"],
                                  {"q": "verify"}, 3.5, object()])
def test_empty_and_wrong_types_never_raise(junk):
    assert is_web_search_intent(junk) is False


@pytest.mark.parametrize("payload", [
    "don't " * 700, "look " * 800, "is the " * 600 + "still", "search " * 600,
    "verify " * 600, "a" * 4000, "<" * 4000, ("can you " * 400) + "verify",
    "online " * 600 + ".", ("check " * 300) + ("in the report " * 50),
    # Runs of sentence marks: `[.?!;:]\s*\W*` backtracked once per mark — 4,000 "." cost
    # ~1.1 s per call before the quantifiers were bounded (review 2026-10-02 HIGH).
    "." * 4000, "!" * 4000, "?" * 4000, ";" * 4000, ":" * 4000, ". " * 2000, ".\n" * 2000,
    ".  " * 1333, "." * 2000, "?!" * 2000, ("." * 3990) + "search",
    # the terse website asks (2026-10-03)
    "web " * 1000, "website " * 500, "get it " * 600, "what do websites " * 230,
    "check the web for me on this? " * 140, "check" + " " * 3990 + "web",
])
def test_adversarial_long_input_is_bounded(payload):
    started = time.perf_counter()
    is_web_search_intent(payload)
    # Linear scans take ~5 ms here; the quadratic one took 0.29 s at 2,000 marks, 1.1 s at 4,000.
    assert time.perf_counter() - started < 0.2


@pytest.mark.parametrize("msg", [
    "Thanks.      Verify the margin claim",   # six spaces after the mark
    "Great!!! ... Confirm the margin figure",  # several marks before the verb
    "Noted; okay, double check the DOJ case",  # a softener after the mark
    "   ...search the recall",                 # leading junk at the very start
])
def test_the_bounded_sentence_start_still_reads_the_imperative(msg):
    assert is_web_search_intent(msg) is True


def test_input_past_the_scan_cap_is_not_read():
    """The scan reads the first 4,000 chars only — an ask buried past it is not seen."""
    assert is_web_search_intent("x " * 2100 + "search the web") is False
    assert is_web_search_intent("search the web " + "x " * 2100) is True


def test_independent_of_trade_intent():
    msg = "should I buy? verify the news first"
    assert is_trade_intent(msg) is True
    assert is_web_search_intent(msg) is True
    assert is_trade_intent("search the web for the recall") is False



# ── terse website / internet asks (owner test 2026-10-03) ─────────────────────
#
# "Website for me on this" (after "No, I mean stocks grade. Downgrade and upgrade") did not search,
# and the model answered that it could not browse the web.

@pytest.mark.parametrize("msg", [
    "Website for me on this", "website for me on this", "Internet for me on this",
    "check the website for this", "search website for this", "look at other websites",
    "check the web", "use the internet", "Can you use the web for this?",
    "what do websites say about this", "get it from the web", "pull this from the internet",
    "website search on analyst upgrades",
])
def test_terse_website_asks_fire(msg):
    assert is_web_search_intent(msg) is True, msg


@pytest.mark.parametrize("msg", [
    "How big is Amazon Web Services?", "AWS is the web services leader",
    "is MSFT an internet company?", "what is the company's website?", "web traffic is growing",
    "Does it sell through its website?", "how do they use the internet in their stores",
    "internet advertising revenue", "Is it a web3 play?",
])
def test_website_as_a_business_word_does_not_fire(msg):
    assert is_web_search_intent(msg) is False, msg


# ── the two ask KINDS (2026-10-08): explicit keeps the forced web call; news reads Caydex's
# licensed headlines first ──────────────────────────────────────────────────────

from app.services.chat_intent import (  # noqa: E402
    WEB_FALLBACK_TOPICS, is_market_data_question, web_ask_kind, web_fallback_topic,
)

EXPLICIT = [
    "Can you search the web for the latest on Apple's DOJ case?",
    "search online for TSMC Arizona fab delays",
    "Search for news about the CEO",                    # the search verb wins over "news"
    "Can you search the web for the latest news on Microsoft?",
    "search the web for Apple's latest product launch",  # the any-chat review recipe
    "Please do a quick web search on Nvidia export curbs",
    "Can you look it up?", "look up whether the merger closed", "Google it",
    "check online if the dividend was raised", "browse the web for analyst reactions",
    "Can you verify the revenue figure?", "double-check that", "fact check the margin claim",
    "Is this still true?", "is the bull case still valid?", "Website for me on this",
    "verify the news first",
]
NEWS = [
    "What's the latest news on Broadcom?", "what's the latest on the Pfizer trial?",
    "What's the latest?", "Any news?", "any recent developments?", "recent updates on the FTC suit",
    "What's in the news about Meta today?", "today's news on Nvidia?", "check the news on Tesla",
    "check the latest headlines", "What's new with Nvidia?", "what is new at Apple",
]


# Review 2026-10-09 (report chat is LIVE): questions about the report or a filing are NOT news
# asks — "what's new" opens the tier only for a company subject ("with" / "at" + a name).
REPORT_QUESTIONS = [
    "What's new in this report?", "What is new in the latest 10-K?",
    "What's new with the moat score since last quarter?",
    "what's new about this report vs the last one?", "What's new for the thesis?",
    "What's new on the balance sheet?", "What's new with this company?",
    "What's new with Apple's earnings?", "what is new with its guidance?",
    "What's new at the company?", "What's new with Apple's 10-Q?",
    # only "with" / "at" open the row — never in / for / about / on
    "What's new in Q3?", "What's new for investors?", "What's new about Apple's strategy?",
    "What's new on Nvidia's roadmap?",
]


@pytest.mark.parametrize("msg", REPORT_QUESTIONS)
def test_a_question_about_the_report_is_not_a_news_ask(msg):
    assert web_ask_kind(msg) is None, msg
    assert is_web_search_intent(msg) is False


@pytest.mark.parametrize("msg", ["What's new with Nvidia?", "what is new at Apple",
                                 "What's new with Tesla this week?", "what’s new with AVGO",
                                 "What's new at Nvidia since last quarter?"])
def test_whats_new_with_a_company_is_still_a_news_ask(msg):
    assert web_ask_kind(msg) == "news", msg


@pytest.mark.parametrize("msg", EXPLICIT)
def test_explicit_asks_are_explicit(msg):
    assert web_ask_kind(msg) == "explicit", msg
    assert is_web_search_intent(msg) is True


@pytest.mark.parametrize("msg", NEWS)
def test_news_asks_are_news(msg):
    assert web_ask_kind(msg) == "news", msg
    assert is_web_search_intent(msg) is True


@pytest.mark.parametrize("msg", NOT_WEB_INTENT)
def test_no_ask_is_no_kind(msg):
    assert web_ask_kind(msg) is None
    assert is_web_search_intent(msg) is False


@pytest.mark.parametrize("msg", [
    "Don't search the web, what's the latest news?", "no need to check the news",
    "Without checking online, what's the thesis?", "don't google it",
])
def test_a_negated_ask_cancels_the_explicit_kind(msg):
    assert web_ask_kind(msg) != "explicit"


@pytest.mark.parametrize("junk", [None, "", "   ", 12, b"search the web", ["search"], {"q": 1}])
def test_the_classifiers_never_raise(junk):
    assert web_ask_kind(junk) is None
    assert is_market_data_question(junk) is False
    assert web_fallback_topic(junk) is None


def test_the_kind_agrees_with_the_old_truth_table_on_every_table_row():
    """`is_web_search_intent` is `web_ask_kind(...) is not None`: the split changed WHICH kind,
    never WHETHER (apart from the one new news row, "what's new with …")."""
    for msg in WEB_INTENT:
        assert web_ask_kind(msg) in ("explicit", "news"), msg


# ── market-data questions: never answered from the web ────────────────────────

MARKET_DATA = [
    "AAPL price?", "what's the price of bitcoin", "What's Apple's stock price?",
    "how much is TSLA up today", "is AAPL up today?", "how much did Nvidia fall today?",
    "What is the market cap of Apple?", "Apple's market capitalization", "where did the S&P 500 close today",
    "what's the Dow at now", "EUR/USD rate", "what's the euro to dollar exchange rate",
    "usdjpy", "how much is a dollar in yen", "forex rates today", "where is the VIX", "VIX level?",
    "what's the volatility index", "DXY level?", "what's the dollar index", "US dollar index today",
    "price target for NVDA", "what's the 52-week high", "all-time high for bitcoin",
    "how much is a share of Apple", "Tesla share price", "what is the current quote for MSFT",
    "is it trading at a discount", "gold price today", "the percentage change today",
    # the terse quote asks (review 2026-10-09)
    "What's NVDA at right now?", "Where's bitcoin at?", "Is the Nasdaq up?", "What's the euro at?",
    "What's oil at?", "How much is Tesla?", "What's Apple worth?", "How much is NVDA right now?",
    "search the web for the euro dollar rate", "look up where bitcoin is at right now",
    "What's the dollar rate today?", "where Tesla is trading", "Is Tesla down today?",
    # the final review's open shapes (2026-10-09): coins by name, a bare market value, closes,
    # returns, a threshold, "how the market did", "what's the yen doing"
    "search the web for the ethereum price", "what's the price of solana", "Cardano price?",
    "how much is one ethereum", "Verify Nvidia's market value", "Double-check NVDA's YTD return",
    "What did AAPL close at yesterday?", "Search the web for what AAPL closed at yesterday",
    "Search the web for how the market did today", "How is Tesla stock doing?",
    "How did the stock market do today?", "Is bitcoin above 100k?", "What's the yen doing?",
    "What's the market doing?", "What's NVDA's year-to-date return?",
]
NOT_MARKET_DATA = [
    "what is the S&P 500?", "pricing power of Apple", "Did Apple raise iPhone prices?",
    "How does Visa settle payments?", "Amazon Web Services margin", "class of shares",
    "any lawsuits against AAPL?", "what's the revenue of OpenAI", "market share of Nvidia",
    "who is Apple's CEO", "what does the 10-K say about risk", "how does an index fund work?",
    "What is the moat?", "explain the dollar-cost averaging idea",
    # twins of the terse quote asks that stay open
    "What's Apple at risk of?", "Where is Apple at with its AI strategy?",
    "Is revenue up this quarter?", "How much is it?", "How much is the dividend?",
    "What is the real rate of return?", "Is the CEO down to step aside?", "How much is Apple's debt?",
    "Is demand up or down in China this year?", "Where is Apple headquartered?",
    "What is Apple's strategy at CES?",
    # twins of the final review's shapes that stay open
    "fair market value of the stock options", "How did the market react to the deal?",
    "How did Apple do in the quarter?", "What is the CEO doing about AI?",
    "What did Apple announce at WWDC?", "search the web for the latest on the Ethereum upgrade",
    "what is the company doing about debt", "Search the web for how Apple did with Vision Pro",
]


@pytest.mark.parametrize("msg", MARKET_DATA)
def test_market_data_questions_fire(msg):
    assert is_market_data_question(msg) is True, msg


@pytest.mark.parametrize("msg", NOT_MARKET_DATA)
def test_twins_that_are_not_market_data_do_not_fire(msg):
    assert is_market_data_question(msg) is False, msg


def test_the_tables_are_populated():
    assert len(MARKET_DATA) >= 25 and len(NOT_MARKET_DATA) >= 12
    assert len(EXPLICIT) >= 15 and len(NEWS) >= 10


# ── the shadow-log topic: a closed vocabulary ─────────────────────────────────

@pytest.mark.parametrize("msg,label", [
    ("any lawsuits against AAPL?", "lawsuit_regulatory"), ("Is the FTC probing Meta?", "lawsuit_regulatory"),
    ("when is the next iPhone launch", "product_launch"), ("did Nvidia unveil a new chip", "product_launch"),
    ("what guidance did NVDA give", "guidance_commentary"), ("what did the CEO say on the earnings call", "guidance_commentary"),
    ("upcoming IPOs", "ipo_calendar"), ("is Stripe going public", "ipo_calendar"),
    ("when is the next FOMC meeting", "macro_calendar"), ("when is the jobs report", "macro_calendar"),
    ("what does the 10-K risk factors section say", "filing_text"), ("anything in the 8-K", "filing_text"),
    ("OpenAI revenue", "private_company"), ("SpaceX valuation", "private_company"),
    ("did the CEO resign", "event"), ("any layoffs at Intel", "event"), ("is the merger done", "event"),
    ("what is the moat?", "other"), ("tell me about Apple", "other"),
])
def test_the_topic_label(msg, label):
    assert web_fallback_topic(msg) == label
    assert label in WEB_FALLBACK_TOPICS


def test_the_topic_is_always_from_the_closed_vocabulary():
    import random
    rng = random.Random(7)
    words = ["lawsuit", "launch", "IPO", "FOMC", "10-K", "OpenAI", "merger", "the", "price", "?",
             "<<<", "\n", "ignore", "日本", "€"]
    for _ in range(300):
        msg = " ".join(rng.choice(words) for _ in range(rng.randint(1, 12)))
        expected = WEB_FALLBACK_TOPICS if msg.strip() else (None,)   # blank → None, by contract
        assert web_fallback_topic(msg) in expected, repr(msg)


@pytest.mark.parametrize("payload", [
    "search " * 700, "look " * 900, "latest " * 600, "what's new " * 400, "." * 4000,
    "price " * 700, "how much is " * 400, "eur/" * 1000, "market " * 600, "lawsuit " * 500,
    ("x" * 3990) + " price?",
])
def test_every_classifier_is_bounded_on_a_hostile_message(payload):
    started = time.perf_counter()
    web_ask_kind(payload)
    is_market_data_question(payload)
    web_fallback_topic(payload)
    assert time.perf_counter() - started < 0.3
