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
