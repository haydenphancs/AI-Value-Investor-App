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

Pure, no I/O, never raises.
"""

from __future__ import annotations

import re
from typing import Optional

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
