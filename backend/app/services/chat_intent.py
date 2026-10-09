"""Deterministic trade-action intent classifier for a chat turn.

Answers ONE question: *is the user asking whether to buy / sell / hold / trade
something, or whether something suits them personally?* That is the gate for the
per-answer "educational, not financial advice" note
(``chat_security.finalize_disclaimer``).

WHY THIS EXISTS: the note used to ride on EVERY answer, including "Hi". A disclaimer
on a greeting is how you train people to stop reading disclaimers. It earns its place
on the turn where someone might act on the answer.

NO LLM CALL. Two reasons, both load-bearing:

* Cost + latency — this runs on every turn, on both the streaming and the
  non-streaming path, before the answer can be finalized.
* ``agents/chat_router.route_question`` is the LLM classifier that already exists,
  and it is exactly what must NOT be reused here: it runs on the STREAM path only,
  and it fails OPEN to ``{"specialists": ["general"], "degraded": True}``. Wiring a
  legally-material gate to it would mean a Gemini blip silently drops the disclaimer
  from every trade question.

TUNED ASYMMETRICALLY. A false negative (no disclaimer on a real "should I buy?") is a
compliance miss; a false positive (a disclaimer on a definitional answer) is a mild
cosmetic oddity. When the two conflict, recall on trade intent wins.

ENGLISH ONLY, stated honestly. A Vietnamese "có nên mua không?" will not fire this
gate. Three things bound the damage: the prompt instruction is language-agnostic so
the model still writes its own note, the strip is also English-marker-based so it can
never REMOVE a non-English note, and ``InlineDisclaimerNotice`` is on screen for the
whole conversation. Non-English trade turns degrade to the old fallback, never to
nothing. Adding a language is an additive table, not a redesign.

A SECOND, independent gate lives here too: ``web_ask_kind`` / ``is_web_search_intent`` — did
the user explicitly ask to search the web, look something up or verify / double-check something
("explicit"), or ask for the latest news ("news")? It decides whether chat offers its paid
``web_search`` tool on an ASKED turn and how round 1 is forced (see the section at the bottom of
this file). ``is_market_data_question`` (never answered from the web) and ``web_fallback_topic``
(a closed category label for the automatic tier's shadow log) sit beside it.

Pure, no I/O, never raises.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── 1. NEGATIVE MASK — run FIRST, replaces each span with a space ────────────
#
# These are the traps. Finance prose uses buy/sell/hold/short/long/position as
# ORDINARY words far more often than as trade instructions. Masking (rather than a
# negative lookahead per verb) means the span is GONE before stages 2-4 ever see it,
# so "what is short interest in GME?" cannot reach the `\bshort\b` verb at all.
#
# Every entry here is a real false-positive class, most of them features this app
# actually ships (congressional trades, 13F holdings, the Buy/Sell meter). Do not
# prune without re-running the table in tests/test_chat_intent.py.
_MASK_PATTERNS = (
    # `buy` as a corporate / product noun
    r"buy\s?backs?", r"share\s+repurchases?",
    r"buy\s+now,?\s+pay\s+later", r"\bbnpl\b",           # Affirm / Klarna business model
    # `short` as a metric, not an action
    r"short\s+interest", r"short[\s-]term", r"short\s+ratio", r"short\s+float",
    r"short\s+squeeze", r"short\s+sell(?:er|ers|ing)", r"days\s+to\s+cover", r"\bshorts\b",
    # `sell` as a research / market noun
    r"sell[\s-]side", r"sell[\s-]through", r"sell[\s-]?off", r"oversold", r"overbought",
    r"selling\s+pressure",
    # OTHER PEOPLE'S trades (insider / 13F / congress) — past tense + agent nouns
    r"insider\s+(?:buy|sell)(?:s|ing)?", r"institutional\s+(?:buy|sell)(?:s|ing)?",
    r"\b13f\b", r"\bsold\b", r"\bbought\b", r"\bbuyers?\b", r"\bsellers?\b",
    # `hold` as a noun. NOTE `\bhold\b` does NOT match "holding", so mask only the NOUN
    # forms — never bare "holding", or "should I keep holding AMD?" dies with them.
    r"\bholdings\b", r"(?:top|biggest|largest|major|core|main|portfolio)\s+holdings?",
    r"holding\s+compan(?:y|ies)", r"(?:share|stake)holders?",
    # allocate / long / position as fundamentals vocabulary
    r"capital\s+allocation", r"allocat(?:e|es|ed|ing)\s+capital",
    r"long[\s-]term", r"long[\s-]dated", r"\blongs\b",
    r"(?:market|competitive|cash|net\s+cash|financial|strategic)\s+positions?",
    r"position(?:ing|ed)",
    # the app's OWN deterministic meter + analyst consensus (informational surfaces)
    r"strong\s+buy", r"buy\s+ratings?", r"sell\s+ratings?", r"buy\s*/\s*sell\s+meter",
    r"buy\s+signals?", r"consensus\s+(?:buy|sell|hold)", r"analysts?'?\s+(?:buy|sell|hold)",
    # corporate exit / trim
    r"exit\s+strateg(?:y|ies)", r"\bexited\b",
    r"trim(?:med|ming|s)?\s+(?:costs?|guidance|jobs|staff|workforce|its)",
    # in-app action, not a trade
    r"add(?:s|ed|ing)?\s+to\s+(?:the\s+|my\s+|your\s+)?watchlist",
    # ACCESS / VENUE questions — "where can I buy DOGE?", "how do I invest in SPY?",
    # "which exchanges list SOL?" ask HOW an asset is reached, not WHETHER to buy it.
    # The FRAME ("can I") + VERB ("buy") read as trade intent, so the TestFlight
    # "where can I buy DOGE?" turn got the advice refusal AND the trade disclaimer
    # (2026-09-16, E4). The mask removes the FRAME and, via the lookahead, KEEPS the
    # verb: "where can I buy DOGE, should I?" still trips on its own frame, and
    # "where can I buy DOGE, is it a good buy?" on the standalone. "How much should I
    # invest" is untouched — "much" sits between "how" and the auxiliary.
    # No `you`: `would you buy…` is the advisory frame `_FRAME_RE` exists to catch. And a
    # DECISION TAIL in the same clause keeps the frame — "how do I sell before it drops?",
    # "how do I buy the dip?", "where do I buy in, now or after earnings?" are timing
    # questions wearing access words, and they keep their disclaimer.
    r"\b(?:where|how)\s+(?:can|could|do|does|would)\s+(?:i|we|one|someone)\s+"
    r"(?=(?:buy|purchase|get|trade|invest|acquire|access|sell|own|hold)\b)"
    r"(?![^?.!\n]{0,80}\b(?:before|after|now|today|tonight|tomorrow|dip|dips|drops?|falls?|"
    r"crash(?:es)?|profits?|gains?|losses|tranches?|earnings|calls?|puts?|position|split|"
    r"vs\.?|versus|or\s+wait|timing|when|at\s+what\s+price|at\s+this\s+price)\b)",
    r"\b(?:where|how)\s+to\s+(?=(?:buy|purchase|invest|get|trade|access|sell)\b)"
    r"(?![^?.!\n]{0,80}\b(?:before|after|now|today|tonight|tomorrow|dip|dips|drops?|falls?|"
    r"crash(?:es)?|profits?|gains?|losses|tranches?|earnings|calls?|puts?|position|split|"
    r"vs\.?|versus|or\s+wait|timing|when|at\s+what\s+price|at\s+this\s+price)\b)",
    r"\bwhich\s+(?:exchanges?|platforms?|brokers?|brokerages?|apps?|venues?)\b",
    r"\b(?:on\s+)?what\s+(?:exchanges?|platforms?|brokers?|apps?)\b",
    r"\bwhere\s+(?:is|are|can|could)\s+\S+\s+(?:be\s+)?(?:listed|traded|available|bought|purchased)\b",
)
_MASK_RE = re.compile("|".join(_MASK_PATTERNS), re.IGNORECASE)

# ── 2. DEFINITIONAL SUPPRESSOR ───────────────────────────────────────────────
# "What does 'take profit' mean?" is a vocabulary lesson, not a decision. Only
# suppresses when there is NO first-person pronoun, so "explain whether I should buy
# AAPL" still classifies as trade intent.
_DEFINITIONAL_RE = re.compile(
    r"^\s*\W*(?:explain\b|define\b|meaning\s+of\b|difference\s+between\b"
    r"|what\s+does\b(?=.{0,60}\bmean)|what\s+do\b(?=.{0,60}\bmean))",
    re.IGNORECASE,
)
_FIRST_PERSON_RE = re.compile(
    r"(?:^|\W)(?:i|i'm|i've|my|mine|we|our|us|me)(?:\W|$)", re.IGNORECASE
)

# ── 3. ADVISORY FRAME — "is this about MY decision?" ─────────────────────────
_FRAME_RE = re.compile("|".join((
    r"\b(?:should|shall|must|ought|can|could|would|do|did|will)\s+(?:i|we)\b",
    r"\bwould\s+you\b", r"\bwhat\s+would\s+you\s+do\b",
    r"\bi(?:'m|\s+am)\s+(?:thinking|planning|considering|looking|tempted|about)\b",
    r"\bi\s+(?:want|plan|intend|need)\s+to\b",
    r"\bthinking\s+(?:of|about)\b",
    r"\bis\s+(?:it|this|that|now|\w+)\s+(?:a\s+)?good\s+(?:time|buy|entry|moment)\b",
    r"\bgood\s+time\s+to\b", r"\btime\s+to\b",
    r"\bworth\s+(?:buying|selling|holding|shorting|adding|owning|investing|a\s+buy)\b",
    r"\bwhen\s+(?:to|should\s+i|do\s+i)\b",
    r"\bhow\s+(?:much|many)\s+(?:should|shares\s+should|do\s+i)\b",
    r"\bmy\s+(?:position|stake|shares|lot|cost\s+basis|entry)\b",
    r"\bhelp\s+me\s+decide\b",
)), re.IGNORECASE)

# ── 4. TRADE VERB — "about buying/selling what?" ─────────────────────────────
_VERB_RE = re.compile("|".join((
    r"\bbuy(?:ing)?\b", r"\bsell(?:ing)?\b", r"\bhold\b", r"\bshort(?:ing)?\b",
    r"\btrim(?:ming)?\b", r"\bdump(?:ing)?\b", r"\bexit(?:ing)?\b", r"\binvest(?:ing)?\b",
    r"\ballocate\b", r"\bposition\b", r"\bstake\b", r"\bentry\b", r"\bown\b",
    r"\bpurchase\b", r"\bacquire\b",
    # "keep" alone is far too weak ("should I keep reading?") — bind it to an object.
    r"\bkeep\s+(?:holding|owning|it|them|my|the\s+\w+)\b",
    r"\bget\s+(?:in|out)\b", r"\baverage\s+down\b", r"\bdouble\s+down\b", r"\bload\s+up\b",
    r"\btake\s+profits?\b", r"\bcut\s+(?:my\s+)?losses\b", r"\bgo\s+long\b",
    r"\badd\s+(?:to|more)\b", r"\bstop\s+loss\b",
)), re.IGNORECASE)

# ── 5. STANDALONE — high-precision phrases that need no frame ────────────────
# "AAPL buy or sell?" and "Is this ETF right for me?" carry no grammatical frame, but
# neither is ambiguous. The suitability half is ADVICE_BOUNDARY's other half: a
# personalized-fit question IS advice, and the app itself used to ship one as a chip.
_STANDALONE_RE = re.compile("|".join((
    r"\bbuy\s*(?:,|/|\s+or\s+)\s*sell\b", r"\bsell\s*(?:,|/|\s+or\s+)\s*buy\b",
    r"\bbuy\s*,?\s*sell\s*,?\s*(?:or\s+)?hold\b", r"\bhold\s+or\s+sell\b",
    r"\bgood\s+buy\b", r"\bgood\s+sell\b", r"\bbuy\s+the\s+dip\b",
    r"\bbuy\s+now\b", r"\bsell\s+now\b",           # "buy now pay later" is masked above
    r"\btake\s+profits?\b", r"\baverage\s+down\b", r"\bdouble\s+down\b",
    r"\bgo(?:ing)?\s+long\b", r"\bload\s+up\b", r"\bcut\s+(?:my\s+)?losses\b",
    r"\bentry\s+point\b", r"\bposition\s+siz(?:e|ing)\b",
    r"\bworth\s+buying\b", r"\bworth\s+selling\b",
    # suitability == personalized advice
    r"\bright\s+for\s+(?:me|my|us)\b", r"\bsuitable\s+for\s+(?:me|my|us)\b",
    r"\bgood\s+fit\s+for\s+(?:me|my)\b",
    r"\bfits?\s+my\s+(?:portfolio|goals|risk|profile)\b",
    r"\bsuits?\s+my\s+(?:goals|risk|portfolio|profile|situation|needs)\b",
    r"\bfor\s+someone\s+like\s+me\b",
    # NOT bare "my portfolio" — "what is my portfolio worth?" is a lookup, not advice.
    r"\bmy\s+(?:risk\s+tolerance|time\s+horizon|financial\s+situation|investment\s+goals)\b",
    r"\b(?:given|based\s+on|considering)\s+my\b",
)), re.IGNORECASE)


def is_trade_intent(text: Optional[str]) -> bool:
    """True when the turn asks whether to buy / sell / hold / trade, or whether
    something suits the user personally. Never raises."""
    if not text:
        return False
    masked = _MASK_RE.sub(" ", text)
    if _DEFINITIONAL_RE.match(masked) and not _FIRST_PERSON_RE.search(masked):
        return False
    if _STANDALONE_RE.search(masked):
        return True
    return bool(_FRAME_RE.search(masked) and _VERB_RE.search(masked))


# ═════════════════════════════════════════════════════════════════════════════
# WEB-SEARCH INTENT — "did the user explicitly ask to search / look up / verify?"
# ═════════════════════════════════════════════════════════════════════════════
#
# The ASKED-turn gate for chat's `web_search` tool (`chat_web_search_service.decide_web_search`).
# OWNER DECISION (2026-10-02): an asked search needs an explicit request — no chip opens it. (The
# separate AUTOMATIC tier of 2026-10-08 is model-decided, switched off by default, and does not read
# this classifier to open.) So this is tuned for PRECISION, the opposite of `is_trade_intent`: a
# false positive declares a paid tool, collapses a multi-lens answer to one lens and adds the web
# rule to the prompt; a false negative answers from Caydex's data as every turn did before. The
# model still decides whether to CALL the tool, and only a call costs money.
#
# ⚠️ The product is called a "research REPORT", so bare "research" NEVER triggers ("summarize this
# research report", "what does the research say"). Nor does "Google Search" the product (Alphabet's
# segment), "search the report", or a negated ask ("don't search the web, just use the report").
#
# ENGLISH ONLY, like the trade gate. Known false negatives, by design: implicit asks ("what
# happened since the report?", "has anything changed?"), a bare "news?", and a follow-up turn of
# a web turn ("and the other one?").
#
# Every quantifier is bounded and the input is capped, so a hostile 4,000-char message scans in
# linear time (pinned by `tests/test_chat_web_search_intent.py`).

_WEB_SCAN_MAX = 4000
# iOS smart punctuation sends the typographic apostrophe; every pattern below is written with the
# ASCII one ("don't", "today's"), so it is folded first.
_WEB_APOSTROPHES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u2032": "'"})

# The request verbs whose NEGATION cancels the ask. Shared by the negation mask below.
_WEB_VERBS = (
    r"(?:search|look|google|check|browse|verify|confirm|double[\s-]?check|fact[\s-]?check|"
    r"cross[\s-]?check)"
)

# 1. Negated asks are masked FIRST, to the end of the clause: "Don't search the web, just use the
#    report" / "No need to check the news" / "Without checking online, what's the thesis?". The
#    verb must follow the negation within three plain words — "I don't trust this number, can you
#    verify it?" is NOT masked (the comma stops the word run), and stays an ask.
_WEB_NEG_RE = re.compile(
    r"\b(?:don't|dont|do\s+not|never|no\s+need\s+to|without|instead\s+of|rather\s+than)\s+"
    r"(?:\w+\s+){0,3}?" + _WEB_VERBS + r"\w*\b[^.?!;\n]{0,80}",
    re.IGNORECASE,
)

# 2. An explicit "do a (web) search" — checked BEFORE the noun masks, which would otherwise eat
#    "a search" ("Can you do a search on their CFO?").
_WEB_STRONG_RE = re.compile(
    r"\b(?:do|run|perform|try)\s+(?:a|an|some|one)\s+(?:quick\s+)?"
    r"(?:google\s+|web\s+|internet\s+|online\s+)?search(?:es)?\b",
    re.IGNORECASE,
)

# 3. Traps — finance prose uses "search" / "look up" / "research" as ordinary words.
_WEB_MASK_RE = re.compile("|".join((
    # the product's own name
    r"\bresearch\s+reports?\b", r"\b(?:this|the|your|my|our|caydex|cay)\s+research\b",
    # "search" as a noun: a CEO search, Alphabet's search business, paid search
    r"\b(?:the|a|an|its|their|his|her|our|ceo|cfo|executive|job|talent|leadership|ongoing|paid|"
    r"organic)\s+search\b",
    r"\bsearch\s+(?:engines?|business(?:es)?|segment|revenues?|ads?|advertising|"
    r"market(?:\s+share)?|share|traffic|volumes?|queries|results?\s+pages?|(?:&|and)\s+other|"
    r"giant|monopoly|deal|default|box)\b",
    r"\bgoogle\s+search\b",
    # searching INSIDE the report or the screen, not the web
    r"\b(?:search|look|check|find|scan)\w*\s+(?:\w+\s+){0,3}?(?:in|through|within|inside|across)\s+"
    r"(?:the|this|your|my)\s+(?:report|analysis|section|chart|data|document|page)\b",
    r"\bsearch\w*\s+(?:the|this|your|my)\s+(?:report|analysis|section|document|page)\b",
    r"\blook\w*\s+(?:\w+\s+){0,2}?up\b[^.?!\n]{0,60}?\b(?:in|from)\s+(?:the|this|your|my)\s+report\b",
    # "investors look up to Buffett"
    r"\blook(?:s|ed|ing)?\s+up\s+to\b",
    # "web" / "internet" / "website" as a business, not a request: Amazon Web Services, web
    # traffic, an internet company, "the company's website" (owner test 2026-10-03 widened the
    # terse website asks below, so their nouns must not fire inside finance prose).
    r"\bweb\s+(?:services?|traffic|hosting|browsers?|apps?|platforms?|3|3\.0|summit)\b",
    r"\bweb3\b",
    r"\binternet\s+(?:company|companies|business(?:es)?|stocks?|giants?|services?|traffic|"
    r"advertising|ads?|retail(?:ers?)?|sales)\b",
    r"\b(?:company's|companies'|its|their|corporate|investor|official)\s+websites?\b",
)), re.IGNORECASE)

# The request frames that turn a verify / confirm verb into an ask ("can you verify…"). Bare
# "verify" is NOT an ask on its own: "How does Visa verify transactions?" (review 2026-10-02 #6).
_WEB_REQUEST_FRAME = (
    r"(?:please|pls|plz|can\s+you|could\s+you|would\s+you|will\s+you|you\s+to|go|help\s+me|"
    r"let's|lets|i\s+(?:want|need|would\s+like|'d\s+like)\s+(?:you\s+)?to)"
)
_WEB_CHECK_VERBS = r"(?:verify|confirm|double[\s-]?check|fact[\s-]?check|cross[\s-]?check)"
# The start of a sentence, optionally with a softener — the imperative position.
# Every quantifier after the sentence mark is BOUNDED: `[.?!;:]\s*\W*` backtracked once per
# punctuation character, so a 4,000-char run of "." (a valid message) cost ~1.1 s of event-loop
# time per call (review 2026-10-02 HIGH). `^\W*` stays unbounded — `^` (no MULTILINE) is tried
# at offset 0 only, so it is linear.
_WEB_IMPERATIVE_START = (
    r"(?:^\W*|[.?!;:]\s{0,6}\W{0,4})(?:please\s+|pls\s+|now\s+|also\s+|ok(?:ay)?\s*,?\s+)?"
)

# 4. The asks themselves, in TWO kinds (2026-10-08, owner decision 5): an EXPLICIT ask — search /
#    look up / browse / google / verify / double-check / "is this still true?" — keeps the forced
#    web call; a NEWS ask — "the latest news", "any updates?", "what's new with X", "in the news",
#    "check the news" — is answered from Caydex's licensed headlines FIRST (the headlines tool is
#    forced in round 1) and the web may follow. A message matching both is EXPLICIT: the search
#    verb wins ("Search for news about the CEO", "search the web for the latest news").
_WEB_EXPLICIT_RE = re.compile("|".join((
    # search the web / online / the news
    r"\bsearch\w*\s+(?:(?:on|in|through|across)\s+)?(?:the\s+)?(?:web|internet|net|online|news)\b",
    r"\b(?:web|internet|online)\s+search\w*\b",
    _WEB_IMPERATIVE_START + r"search\b",
    r"\b(?:please|pls|plz|can\s+you|could\s+you|would\s+you|will\s+you|you\s+to|go)\s+"
    r"(?:please\s+)?search\b",
    r"\bsearch\s+(?:for|up)\b",
    r"\bbrows(?:e|ing)\s+(?:the\s+)?(?:web|internet|net|online|news)\b",
    r"\b(?:can|could|would)\s+you\s+browse\b",
    # look it up / look online
    r"\blook\s+(?:(?:it|this|that|them|these|those|him|her)\s+)?up\b",
    r"\blook(?:ing)?\s+(?:(?:it|this|that)\s+)?(?:online|on\s+the\s+(?:web|internet|net))\b",
    r"\bcheck\w*\s+(?:(?:it|this|that)\s+)?(?:online|the\s+(?:web|internet|net))\b",
    r"\b(?:search|look|check|find|research|verify|confirm|dig|read)\w*\b[^.?!\n]{0,60}?"
    r"\b(?:online|on\s+the\s+(?:web|internet|net))(?=\s*(?:[.?!\n]|$))",
    r"\bresearch\w*\s+(?:(?:it|this|that|them)\s+)?(?:online|on\s+the\s+(?:web|internet|net))\b",
    r"\b(?:saying|say|said|written|posted|out\s+there)\b[^.?!\n]{0,30}?"
    r"\b(?:online|on\s+the\s+(?:web|internet))\b",
    # terse website / internet asks (owner test 2026-10-03: "Website for me on this" did not
    # search): "website for me", "check the website(s) for this", "use the internet", "get it from
    # the web", "what do websites say", "website search".
    r"\b(?:web\s*sites?|web|internet)\s+(?:for\s+me|for\s+(?:this|that|it)|on\s+(?:this|that|it))\b",
    r"\b(?:check|scan|search|look\s+(?:at|on|through)|go\s+(?:to|through)|try|visit)\w*\s+"
    r"(?:the\s+|some\s+|other\s+|a\s+few\s+|any\s+)?(?:web\s*sites?|websites?|internet|web)\b",
    r"(?:\b" + _WEB_REQUEST_FRAME + r"\s+|" + _WEB_IMPERATIVE_START + r")"
    r"(?:use|try)\s+(?:the\s+)?(?:web|internet|net)\b",
    r"\b(?:get|pull|grab|fetch|find|bring)\w*\s+(?:(?:it|this|that|them|these|those|some|more)\s+)?"
    r"(?:info\w*\s+)?(?:from|off|on)\s+(?:the\s+)?(?:web|internet|net|web\s*sites?|websites?)\b",
    r"\bwhat\s+(?:do|does|are|did)\s+(?:the\s+|other\s+)?(?:web\s*sites?|websites?|sites|web|internet|"
    r"online\s+sources|sources\s+online)\s+(?:say|saying|report|show)\w*\b",
    r"\bweb\s*sites?\s+search\w*\b",
    # google it
    r"\bgoogle\s+(?:it|that|this|them)\b",
    r"\b(?:please|pls|can\s+you|could\s+you|would\s+you|you\s+should|just)\s+google\b",
    # verify / double-check / confirm — only in a request frame or the imperative position
    _WEB_IMPERATIVE_START + _WEB_CHECK_VERBS + r"\b",
    r"\b" + _WEB_REQUEST_FRAME + r"\s+(?:please\s+)?(?:also\s+)?" + _WEB_CHECK_VERBS + r"\b",
    r"\bverify\s+(?:this|that|these|those|it|whether|if)\b",
    r"\bconfirm\s+(?:whether|if)\b",
    r"\b(?:can|could)\s+(?:this|that|it|these|those)\s+be\s+(?:verified|confirmed|double[\s-]?checked)\b",
    r"\b(?:double|fact|cross)[\s-]?check\w*\b",
    r"\bcross[\s-]?referenc\w*\b", r"\bsanity[\s-]?check\w*\b",
    # is this still true?
    r"\b(?:is|are)\s+(?:this|that|it|these|those|they|the\s+\w+(?:\s+\w+)?)\s+still\s+"
    r"(?:true|accurate|current|valid|correct|right|the\s+case|up[\s-]to[\s-]date|in\s+place|happening)\b",
    r"\bstill\s+(?:true|accurate|valid|current|up[\s-]to[\s-]date)\s*\?",
)), re.IGNORECASE)

_WEB_NEWS_RE = re.compile("|".join((
    # check the news / the headlines / the latest news
    r"\bcheck\w*\s+(?:(?:it|this|that)\s+)?(?:the\s+(?:news|headlines)|"
    r"(?:the\s+)?(?:latest|recent)\s+(?:news|headlines))\b",
    # the latest / any news
    r"\b(?:latest|recent|newest|current|fresh|breaking|today's)\s+(?:news|headlines|updates?|"
    r"developments?|announcements?|coverage)\b",
    r"\bany\s+(?:new\s+|recent\s+|fresh\s+|other\s+|more\s+|good\s+|bad\s+)?"
    r"(?:news|updates?|developments?|headlines)\b",
    r"\bwhat(?:'s|\s+is|\s+are)\s+the\s+(?:latest|news)"
    r"(?:\s*\??\s*$|\s+(?:on|with|about|for|regarding|today)\b)",
    r"\bthe\s+latest\s+(?:on|with|about|for|regarding)\b",
    r"\bin\s+the\s+news\b",
    # "what's new with Nvidia?", "what is new at Apple" — a COMPANY subject only. Report chat is
    # live, and "What's new in this report?", "what is new in the latest 10-K?" or "what's new
    # with the moat score?" are questions about the REPORT, not news asks (review 2026-10-09): so
    # only "with" / "at", never a determiner or pronoun next ("with the …", "with this …"), and
    # never a report / filing / score noun in the next 40 characters ("with Apple's earnings").
    r"\bwhat(?:'s|\s+is)\s+new\s+(?:with|at)\s+"
    r"(?!(?:the|this|that|these|those|its|it|their|his|her|your|my|our|a|an)\b)"
    r"(?![^.?!\n]{0,40}?\b(?:reports?|filings?|10-?[kq]|8-k|20-f|thesis|scores?|analysis|"
    r"sections?|results|earnings|guidance|financials|statements?|balance\s+sheet|valuation|"
    r"ratings?|moat|numbers|data|chart)\b)",
)), re.IGNORECASE)

# The kinds `web_ask_kind` returns (the web-search service reads them by these names).
WEB_ASK_EXPLICIT = "explicit"
WEB_ASK_NEWS = "news"


def _fold_web_text(text: object) -> Optional[str]:
    """The capped, apostrophe-folded scan text, or None for a non-string / blank message."""
    if not isinstance(text, str) or not text.strip():
        return None
    return text[:_WEB_SCAN_MAX].translate(_WEB_APOSTROPHES)


def web_ask_kind(text: Optional[str]) -> Optional[str]:
    """``"explicit"`` (search / look up / google / verify / fact-check / "still true?"), ``"news"``
    (the latest news, any updates, what's new with X, in the news) or None. Explicit wins when both
    match. Negated asks are masked first, the finance-prose traps are masked before the tables.
    Pure, English-only, linear-time on a capped input, never raises."""
    try:
        t = _fold_web_text(text)
        if t is None:
            return None
        t = _WEB_NEG_RE.sub(" ", t)
        if _WEB_STRONG_RE.search(t):
            return WEB_ASK_EXPLICIT
        masked = _WEB_MASK_RE.sub(" ", t)
        if _WEB_EXPLICIT_RE.search(masked):
            return WEB_ASK_EXPLICIT
        if _WEB_NEWS_RE.search(masked):
            return WEB_ASK_NEWS
        return None
    except Exception as e:  # noqa: BLE001 — a gate must never break a turn
        logger.warning("web ask classifier failed (%s) — read as no ask", type(e).__name__)
        return None


def is_web_search_intent(text: Optional[str]) -> bool:
    """True when the user explicitly asked to search the web, look something up, get the latest
    news, or verify / double-check something (`web_ask_kind` is not None). Pure, English-only,
    never raises.

    NEVER fires on bare "research" (the product is a "research report"), on "Google Search" the
    product, on searching inside the report, or on a negated ask."""
    return web_ask_kind(text) is not None


# ═════════════════════════════════════════════════════════════════════════════
# MARKET-DATA QUESTIONS — never answered from the web (owner decision 2026-10-08)
# ═════════════════════════════════════════════════════════════════════════════
#
# Prices, quotes, % moves, market caps, index levels, FX rates, the VIX and the DXY are market
# data: they come from Caydex's licensed feed (or are "not in Caydex data"), never from a web page.
# `is_market_data_question` closes the AUTOMATIC web tier for such a turn, and the web-search
# service refuses a query that reads as one on EVERY tier. Tuned for RECALL — a false positive only
# means no automatic search on that turn (the Caydex tools still answer); a false negative is
# backstopped by the tool descriptions, the prompt rules and the snippet scrub. NOT company
# fundamentals: "OpenAI's revenue" is a fact the web may hold that Caydex does not. Bounded
# quantifiers on a capped input (linear time).
_MD_CCY = r"(?:usd|eur|jpy|gbp|cny|cnh|cad|aud|nzd|chf|inr|krw|hkd|sgd|mxn|brl|sek|nok|try|zar)"
_MD_CURRENCY_WORD = (
    r"(?:dollars?|euros?|yen|pounds?|sterling|yuan|renminbi|rupees?|won|francs?|pesos?|reais|real|"
    r"loonie|aussie)"
)
_MD_INDEX = r"(?:s&p(?:\s*500)?|sp\s*500|spx|dow(?:\s+jones)?|djia|nasdaq(?:\s+composite|\s*100)?|russell\s*2000|ftse(?:\s*100)?|nikkei(?:\s*225)?|dax|stoxx(?:\s*600)?|hang\s+seng)"
#: The coins named by NAME in a market-data question or a web query — a CURATED list of names that
#: are never ordinary words or brands (final review 2026-10-09: "ethereum price" and "Solana price"
#: passed both layers). Never the raw CoinGecko map: "near", "stellar", "flow", "render", "one",
#: "sky", "trump", "compound", "curve", "dash", "mask", "rose" are words. Shared with the web-search
#: service's query refusal and snippet scrub (`chat_web_search_service`).
MARKET_COIN_NAMES = (
    r"(?:bitcoin|btc|ether(?:eum)?|eth|solana|dogecoin|cardano|litecoin|polkadot|chainlink|"
    r"shiba\s+inu|monero)"
)
_MARKET_DATA_RE = re.compile("|".join((
    # a security's price or quote
    r"\b(?:stock|share|shares|coin|token|crypto|" + MARKET_COIN_NAMES + r"|etf|fund|index|futures?|"
    r"gold|silver|oil|crude|copper)\s+(?:price|prices|quote|quotes)\b",
    r"\bprice\s+(?:of|for)\s+(?:a\s+|one\s+|the\s+|its\s+)?(?:share|shares|stock|"
    + MARKET_COIN_NAMES + r"|gold|silver|oil|crude|coin|token)\b",
    # the shapes the final review found open (2026-10-09): "what did AAPL close at", "how did the
    # market do", "how is Tesla stock doing", "is bitcoin above 100k", "what's the yen doing"
    r"\bwhat\s+did\s+(?:the\s+)?\S{1,24}\s+(?:stock\s+|shares?\s+)?(?:close|closed|end|finish)\s+"
    r"(?:at|the\s+day\s+at)\b",
    r"\bhow\s+(?:did|does|do|is|are|was|were|has|have)\s+(?:the\s+)?(?:stock\s+)?(?:market|markets|"
    r"stocks|wall\s+street|\S{1,24}\s+(?:stock|shares))\s+(?:do|doing|done|perform|performing|"
    r"performed|trade|trading|traded|close|closed|finish|finished)\b",
    r"\bwhat\s+(?:the\s+)?\S{1,24}\s+(?:stock\s+|shares?\s+)?(?:closed|ended|finished|opened)\s+at\b",
    r"\bhow\s+(?:the\s+)?(?:stock\s+)?(?:market|markets|stocks|wall\s+street|\S{1,24}\s+(?:stock|shares))"
    r"\s+(?:did|does|is\s+doing|was\s+doing|performed|traded|closed|finished)\b",
    r"\bis\s+(?:the\s+)?\S{1,24}\s+(?:above|below|over|under)\s+\$?\d",
    r"\bwhat(?:'s|\s+is|\s+are)\s+(?:the\s+)?(?:stock\s+)?(?:market|markets|" + _MD_CURRENCY_WORD
    + r"|" + MARKET_COIN_NAMES + r"|" + _MD_INDEX + r"|gold|silver|oil|crude)\s+doing\b",
    r"\b(?:ytd|year[\s-]to[\s-]date|weekly|monthly)\s+(?:return|gain|loss|change|move|"
    r"performance)s?\b",
    r"\bwhat(?:'s|\s+is|\s+was)\s+(?:the\s+|its\s+)?(?:current\s+|latest\s+|live\s+)?(?:price|quote)\b",
    r"\b(?:current|live|latest|today's|real[\s-]?time|last|closing|opening)\s+"
    r"(?:price|quote|trading\s+price|share\s+price|close)\b",
    r"\bprice\s+(?:today|now|right\s+now)\b",
    r"\bprice\s*\?",
    r"\b(?:get|give|show|tell)\s+(?:me\s+)?(?:a\s+|the\s+)?quote\b",
    r"\bquotes?\s+(?:for|on)\b",
    r"\btrading\s+at\b",
    r"\bhow\s+much\s+(?:is|are|does|do)\s+(?:a\s+|one\s+)?(?:share|shares|stock|" + MARKET_COIN_NAMES
    + r"|gold|silver|oil)\b",
    r"\bhow\s+much\s+is\s+\S{1,24}\s+(?:trading|worth|stock|shares?|selling\s+for)\b",
    # the terse quote asks (review 2026-10-09): "What's NVDA at right now?", "Where's bitcoin
    # at?", "What's the euro at?", "Is the Nasdaq up?", "How much is Tesla?", "What's Apple
    # worth?" — the question must END there (or with now / today), so "What's Apple at risk of?",
    # "Where is Apple at with AI?" and "Is revenue up this quarter?" stay open.
    r"\b(?:what|where)(?:'s|\s+is|\s+are)\s+(?:the\s+)?\S{1,24}\s+(?:at|worth|trading\s+at)"
    r"(?:\s+(?:right\s+)?now|\s+today|\s+the\s+moment)?\s*(?:[?.!]|$)",
    r"\bwhere\s+(?:the\s+)?\S{1,24}\s+(?:is|are|was)\s+(?:trading|at)\b",
    r"\bis\s+(?:the\s+)?\S{1,24}\s+(?:up|down|green|red)"
    r"(?:\s+(?:today|(?:right\s+)?now|so\s+far|yet|this\s+(?:morning|afternoon|week)))?\s*(?:[?.!]|$)",
    r"\bhow\s+much\s+is\s+(?:a\s+share\s+of\s+)?(?!(?:it|this|that|they|he|she|the)\b)\S{1,24}"
    r"(?:\s+(?:right\s+)?now|\s+today)?\s*(?:\?|$)",
    # % moves and changes
    r"\bhow\s+much\s+(?:is|did|has|was|are|have)\s+\S{1,24}\s+(?:\S{1,24}\s+)?(?:up|down|move|moved|"
    r"rise|risen|rose|fall|fallen|fell|gain|gained|lose|lost|drop|dropped|jump|jumped|climb|climbed)\b",
    r"\b(?:percent(?:age)?|%)\s+(?:change|move|gain|loss|drop|decline|rise)\b",
    r"\b(?:price|daily|intraday|today's)\s+(?:change|move|movement|performance|action)\b",
    r"\bup\s+or\s+down\s+(?:today|now)\b",
    r"\b(?:is|are|was)\s+\S{1,24}\s+(?:up|down)\s+(?:today|this\s+(?:week|month|morning|afternoon)|"
    r"so\s+far|right\s+now|now)\b",
    # market capitalisation
    r"\bmarket\s+cap(?:s|italization|italisation|italizations)?\b",
    # a company's market value, bare too ("Verify Nvidia's market value") — never the accounting
    # term "fair market value"
    r"(?<!fair\s)\bmarket\s+value\b",
    # index levels
    r"\b(?:index|indices)\s+(?:level|levels|value|close|closing)\b",
    r"\b(?:level|close|closing\s+level)\s+of\s+(?:the\s+)?" + _MD_INDEX + r"\b",
    r"\b(?:where|what)(?:'s|\s+is|\s+was|\s+did|\s+are)\s+(?:the\s+)?" + _MD_INDEX + r"\s+(?:at|trading|today|now|"
    r"close|closed|closing|end|ended|ending|level|finish|finished)\b",
    # highs, lows, targets, volume
    r"\b(?:52|fifty[\s-]two)[\s-]week\s+(?:high|low|range)\b",
    r"\ball[\s-]time\s+(?:high|low)s?\b",
    r"\bprice\s+targets?\b", r"\btarget\s+price\b",
    r"\btrading\s+volume\b", r"\bvolume\s+today\b",
    # exchange rates
    r"\bexchange\s+rates?\b", r"\b(?:fx|forex|currency)\s+(?:rates?|quotes?|prices?)\b", r"\bforex\b",
    r"\bconversion\s+rate\b",
    r"\b" + _MD_CCY + r"\s{0,2}[/-]\s{0,2}" + _MD_CCY + r"\b",
    r"\b(?:eur|gbp|aud|nzd)usd\b", r"\busd(?:jpy|cad|chf|cny|cnh|inr|krw|hkd|sgd|mxn|brl)\b",
    r"\b" + _MD_CURRENCY_WORD + r"\s+(?:to|vs\.?|versus|against|in)\s+(?:the\s+)?" + _MD_CURRENCY_WORD + r"\b",
    # "the euro dollar rate", "dollar yen exchange rate", "the dollar rate" (never "the real rate
    # of return": `real` is a currency word only beside another one)
    r"\b" + _MD_CURRENCY_WORD + r"[\s/-]{1,3}" + _MD_CURRENCY_WORD + r"\s+(?:exchange\s+)?rates?\b",
    r"\b(?:dollar|euro|yen|pound|sterling|yuan|renminbi|rupee|franc|peso)\s+(?:exchange\s+)?rates?\b",
    r"\bhow\s+much\s+is\s+(?:a|one|1)\s+(?:dollar|euro|yen|pound|yuan|rupee|bitcoin|btc)\b",
    # the VIX and the DXY (neither is in Caydex data)
    r"\bvix\b", r"\bvolatility\s+index\b", r"\bfear\s+gauge\b",
    r"\bdxy\b", r"\b(?:us\s+|u\.s\.\s+)?dollar\s+index\b",
)), re.IGNORECASE)


def is_market_data_question(text: Optional[str]) -> bool:
    """True for a price / quote / % move / market cap / index level / FX rate / exchange rate /
    VIX / DXY / dollar-index ask. Pure, English-only, linear-time on a capped input, never raises."""
    try:
        t = _fold_web_text(text)
        return bool(t is not None and _MARKET_DATA_RE.search(t))
    except Exception as e:  # noqa: BLE001
        logger.warning("market-data classifier failed (%s)", type(e).__name__)
        return False


# ═════════════════════════════════════════════════════════════════════════════
# WEB FALLBACK TOPIC — a category label for the automatic tier's SHADOW logging only
# ═════════════════════════════════════════════════════════════════════════════
#
# `AUTO_WEB_SHADOW category=<label>` tells the owner which kinds of question would reach the
# automatic web search, before it is switched on. It decides NOTHING: the gate never reads it. The
# label is a closed vocabulary (never message text), first match wins in table order, and a
# message that matches no row is "other".
WEB_FALLBACK_TOPICS = (
    "lawsuit_regulatory", "product_launch", "guidance_commentary", "ipo_calendar",
    "macro_calendar", "filing_text", "private_company", "event", "other",
)
_TOPIC_TABLE = (
    ("lawsuit_regulatory", re.compile(
        r"\b(?:lawsuits?|sued|suing|sues|class[\s-]action|settlements?|probes?|investigations?|"
        r"antitrust|doj|department\s+of\s+justice|ftc|subpoena\w*|fined|penalt(?:y|ies)|recalls?|"
        r"recalled|regulators?|regulatory|indict\w*|court|ruling|verdict|litigation|injunctions?|"
        r"sec\s+(?:charges?|probe|investigation|filed|sued|fine))\b", re.IGNORECASE)),
    ("product_launch", re.compile(
        r"\b(?:launch\w*|unveil\w*|release\s+date|rollout|rolled\s+out|new\s+(?:product|model|"
        r"phone|chip|device|feature)s?|debut\w*|keynote)\b", re.IGNORECASE)),
    ("guidance_commentary", re.compile(
        r"\b(?:guidance|outlook|earnings\s+call|conference\s+call|on\s+the\s+call|investor\s+day|"
        r"management\s+said|(?:ceo|cfo)\s+(?:said|says|comments?)|commentary|transcripts?)\b",
        re.IGNORECASE)),
    ("ipo_calendar", re.compile(
        r"\b(?:ipos?|going\s+public|go\s+public|direct\s+listing|spacs?|listing\s+date|"
        r"upcoming\s+listings?)\b", re.IGNORECASE)),
    ("macro_calendar", re.compile(
        r"\b(?:fomc|fed\s+meeting|rate\s+decision|cpi\s+(?:release|report|print)|jobs\s+report|"
        r"non[\s-]?farm|payrolls|economic\s+calendar|gdp\s+(?:release|report)|"
        r"next\s+(?:fed|cpi|jobs))\b", re.IGNORECASE)),
    ("filing_text", re.compile(
        r"(?:\b(?:8|10|20|6)-[kqf]\b|\bs-1\b|\bproxy\b|\brisk\s+factors?\b|\bannual\s+report\b|"
        r"\bfootnotes?\b|\bthe\s+filing\b)", re.IGNORECASE)),
    ("private_company", re.compile(
        r"\b(?:private\s+compan(?:y|ies)|privately[\s-]held|start[\s-]?ups?|pre[\s-]ipo|"
        r"funding\s+rounds?|series\s+[a-h]\s+(?:round|funding)|openai|spacex|stripe|databricks|"
        r"bytedance|xai)\b", re.IGNORECASE)),
    ("event", re.compile(
        r"\b(?:merger|mergers|acqui(?:re|res|red|ring|sition|sitions)|buyout|takeover|spin[\s-]?offs?|"
        r"layoffs?|job\s+cuts|resign\w*|steps?\s+down|stepped\s+down|appoint\w*|hired|fired|"
        r"partnerships?|bankruptcy|chapter\s+11|strikes?|outages?|hack\w*|breach\w*|scandal\w*)\b",
        re.IGNORECASE)),
)


def web_fallback_topic(text: Optional[str]) -> Optional[str]:
    """The automatic tier's shadow-log category (`WEB_FALLBACK_TOPICS`), or None for a
    non-string / blank message. Used ONLY for counts-only logging. Pure, never raises."""
    try:
        t = _fold_web_text(text)
        if t is None:
            return None
        for label, rx in _TOPIC_TABLE:
            if rx.search(t):
                return label
        return "other"
    except Exception as e:  # noqa: BLE001
        logger.warning("web topic classifier failed (%s)", type(e).__name__)
        return None
