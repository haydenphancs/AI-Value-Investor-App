"""In-app validators for Wiser weekly pieces (plan "Wiser weekly content engine", §1.7).

A weekly piece is model prose that ships INSIDE the authenticated app, narrated, to every account,
after one owner tap. These are the deterministic gates in front of that tap; the shadow judge and
the owner read what passes. Every rule below is a hard refusal of the block (the writer gets one
repair round that lists them), never a silent rewrite.

What is enforced, and why it differs from the public marketing scan
-------------------------------------------------------------------
* **Compliance** — `marketing/compliance.scan_text(strict_instruments=False)`, keeping only
  `ENFORCED_COMPLIANCE_CODES` (an EXPLICIT allow-list, pinned by a test): directives /
  recommendations, promises and forecasts, banned phrases, misattributions, famous quotes, links,
  markup, emoji, handles, hashtags, cashtags, code-owned copy, the brand, real people's names
  (weekly pieces name people by role only), and citing the fact sheet or "the source"
  (`source_reference`, 2026-10-31). NOT enforced: `class_b_valuation` (it flags "value
  trap", "per share", "market cap" — the vocabulary a business case study is made of), the
  evaluative and `return_figure` rows, the strict role/pronoun rows (off with
  `strict_instruments=False`) and `identity_leak`. In strict mode the scan fails 21 of the 23
  hand-made bundle articles; the in-app line is narrower on purpose and the judge carries the
  semantic half.
* **Identity** — only FIRST-PERSON self-reference is fatal ("As an AI, I…", "I was trained by
  Google", "my training data"). The owner allowed AI companies and AI technology as third-person
  subject matter in-app (2026-10-22: "Yes in-app, not in podcast"), so "Frontier models trained by
  OpenAI", "LLMs need GPUs" and "AI model training demand" must pass — which is exactly what the
  un-anchored trained-by row of `chat_guardrails` and compliance's `identity_leak` refuse.
* **Advice** — `chat_guardrails.scan_answer`'s `advice_directive` and `suitability_claim` tags,
  plus two Learn rows under `advice_directive` (2026-10-23 review, F2): a trade verb (buy, sell,
  short, trim, dump, avoid, load up on, get in, consider buying…) whose object within 3 words is
  one of the piece's subjects or a security ("Buy CoreWeave.", "trim a position") — not after a
  third-person subject ("Companies sell shares to raise money") unless a named company's STOCK is
  traded, never "buy back", never the issuer's own "sell its/new shares" — and the reader's own
  holdings ("your CoreWeave position", "your portfolio"). The chat rows know only second-person
  phrases and compliance's directive rows only lexicon companies, so a new listing passed both.
* **Valuation verdicts** (`valuation_verdict`) — "undervalued", "overvalued", "must-own" in a
  sentence naming a subject or a lexicon company; "cheap", "expensive", "bargain" there only when
  compliance's VALUATION row binds them to the company or the sentence is about the stock. The
  rest of `class_b_valuation` / `class_b_evaluative` stays ignored ("value trap", "per share",
  "market cap", "a long, expensive transition").
* **Quotations** (`quotation`) — a double-quoted span with a speaker: a reporting verb (said,
  told, called, described, wrote, according to…), a role noun or a subject within ~8 words before
  the opening mark or right after the closing one. A framing quote with no speaker (the bundle's
  'narrower than "the challenger won"') and a filing's defined term ('(the "Company")') pass.
* **Authorship** (`authorship_reveal`) — one sentence naming the article as made "by/with/using" a
  model vendor or model ("This article was drafted with the Gemini models"): the third-person
  leak `self_reference` cannot see once the fact sheet grounds the vendor's name.
* **People** — a role noun (CEO, founder, chairman, CFO, executives, management, auditors…) in the
  same sentence as a conduct verb (misled, concealed, inflated, embezzled…) is `conduct_claim`:
  misconduct stories are not written by the model (plan, Ground rules), and a role-only retelling
  of one stays identifiable (`content_pool.py` EXCLUDED notes). Beyond the plan's role rule, the
  same code also refuses a misconduct-by-definition term in any sentence ("committed fraud",
  "embezzled") and an unambiguous conduct verb beside a company ("the company concealed…", a
  lexicon company, or one of the piece's own subjects) — see `conduct_hits`; the
  securities-complaint formula "(materially) false and misleading statements" counts as such a
  verb. Real people's names are compliance's `person_named` (enforced above).
* **Rumour** — "reportedly", "in talks", "rumor", "sources say", "allegedly", "alleges", "a
  complaint says": a peg is a FILED fact, never a report or a lawsuit's claim about one.
* **Time words** — "this week", "today", "recently"… rot the archive, so they are allowed only in
  a `peg` section block that also states a full date ("October 21, 2026" / "2026-10-21").
* **Forbidden tokens** — `**`, `__`, a markdown heading, a URL, emoji, "Caydex", "Cay AI", a
  non-Latin letter (the homoglyph backstop: a Cyrillic "a", U+0430, splits every lexicon) and a
  non-ASCII digit. Several overlap compliance codes on purpose: these are the Learn rules as the
  plan states them, and they must not move when the marketing scan is retuned.
* **Misquotes** — any `learn_content_rules.BANNED_PHRASES` / `ATTRIBUTION_REQUIRED` phrase:
  weekly pieces carry NO quotes, so even a correctly credited one is refused.
* **Grounding** — `marketing/grounding.check_grounding` per block against the piece's fact sheet
  (numbers and proper nouns must come from it), with the vocabulary built exactly as the marketing
  writer builds it (`content_pool._vocab` over the Learn corpus). `chat_numeric_grounding` is NOT
  used: it is log-only, has no cap and exempts years. Its one Learn-side exemption: an
  `ungrounded_entity` that is a letter's plural ("Hs") or an `EVERYDAY_WORDS` form ("Lifespans",
  "Capex") written where a capital says no name — a heading, a sentence's first word, a Title-Case
  line (`_everyday_word`). A capital mid-sentence in prose still reads as a name.

Totality and cost: pure; every field is length-capped BEFORE any normalisation or regex (they run
on the single uvicorn worker over model output that can be a repetition loop at MAX_TOKENS); every
regex is linear; neither public entry point raises — an unexpected error is logged with its stack
and returned as a `validator_error` issue, so a bug rejects a draft instead of publishing it.

Import boundary: this module imports `marketing.compliance` / `grounding` / `content_pool` (pure,
FMP-free by construction) and NEVER `agents.chat_guardrails` — importing it runs
`agents/__init__`, which loads the FMP client (compliance.py records the same choice). The
guardrail patterns it needs are COPIED below and pinned equal by
`tests/test_learn_weekly_validators.py`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, replace
from datetime import date
from itertools import islice
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.services import learn_content_rules as rules
from app.services.learn_weekly import contracts as C
from app.services.marketing import compliance, content_pool, grounding
from app.services.marketing.numbers import has_non_ascii_digit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Issue:
    """One refusal: where (`field`, a JSON-ish path), what rule (`code`), and the offending text
    (`detail`, ≤ 160 chars — it is quoted back to the model in the repair prompt and shown in the
    owner's Telegram header)."""

    field: str
    code: str
    detail: str

    def as_dict(self) -> Dict[str, str]:
        return asdict(self)


# ── caps (applied to the RAW string, before clean/fold/any regex) ────────────────────────────

#: A 75-word paragraph is ~450-600 characters; twice that leaves room for long financial words
#: and still bounds the work. Text past a cap is reported `too_long` and only the prefix is read.
BLOCK_TEXT_CAP = 1200
#: One bullet item (≤ 30 words).
ITEM_TEXT_CAP = 600
#: Title, section title, keyHighlight title, tagLabel.
TITLE_CAP = 160
#: Subtitle, cardSubtitle, keyHighlight description, a statistic's label/value.
SHORT_TEXT_CAP = 400
SLUG_CAP = 160
#: `self_reference_hits` / `advice_hits` as standalone helpers: compliance's own scan ceiling.
HELPER_TEXT_CAP = 6000
#: Structural ceilings — the outline asks for 5-7 sections; more is a repetition loop, and the
#: rest is not read (the issue already refuses the draft).
MAX_SECTIONS = 12
MAX_BLOCKS_PER_SECTION = 40
MAX_STATISTICS = 8
#: How many served / in-flight titles the uniqueness check reads (the catalog is ~23 + ~52/yr).
MAX_TITLES_COMPARED = 5000
MAX_READ_TIME_MINUTES = 60
#: A fact sheet names a handful of subjects; more is a malformed sheet.
_MAX_SUBJECTS = 50
_DETAIL_MAX = 160

# ── compliance: the explicit enforced allow-list ─────────────────────────────────────────────

#: Every other code `compliance.scan_text` emits is deliberately IGNORED in-app (see the module
#: docstring and `IGNORED_COMPLIANCE_CODES`). A test fails when compliance grows a code that is in
#: neither set, so a new marketing rule is a decision here, never a silent change in-app.
ENFORCED_COMPLIANCE_CODES: FrozenSet[str] = frozenset({
    "class_b_recommendation",   # directive / recommendation ("worth owning", "buy low")
    "class_b_forward",          # forecast ("will soar", "poised to")
    "promissory",               # promise ("always recovers", "can't lose")
    "banned_phrase",
    "misattribution",
    "famous_quote",
    "link",
    "markup",
    "emoji",
    "handle",
    "hashtag",
    "cashtag",
    "code_owned",               # advice / disclaimer / "written without AI" copy is code's
    "brand_mention",            # Caydex, Cay AI, "download", "this app"
    "person_named",             # a real person by name: weekly pieces name people by ROLE only
    # "The fact sheet gives two consecutive annual reports" shipped in an ACCEPTED Claude draft
    # (2026-10-31 live check): an article states its facts, it never cites its brief.
    "source_reference",
})

#: Emitted by `compliance.scan_text` and NOT enforced in-app, each for a stated reason.
IGNORED_COMPLIANCE_CODES: FrozenSet[str] = frozenset({
    "class_b_valuation",        # "value trap", "per share", "market cap": case-study vocabulary
    "class_b_evaluative",       # tier-2 adjectives next to a company; the judge's job in-app
    "return_figure",            # a filed growth figure is the piece's subject matter
    "identity_leak",            # bare AI vocabulary is allowed third-person subject matter
    "first_person",             # generic "we"; self-REFERENCE is `self_reference` below
    "cta",                      # social CTAs ("save this", "follow"); brand terms stay enforced
    "endorsement",              # social-proof claims; the judge reads tone in-app
    "non_latin",                # enforced as `forbidden_token` (homoglyph backstop) below
    "non_ascii_digit",          # enforced as `forbidden_token` below
    "too_long",                 # our caps run first
    "empty",                    # our own `empty` runs first
})

# ── identity: first-person self-reference only ───────────────────────────────────────────────

#: Copied VERBATIM from `agents/chat_guardrails._IDENTITY_ENFORCE_PATTERNS` — every row EXCEPT the
#: un-anchored `\b(?:trained|fine-?tuned)… by (?:google|openai|gemini)` row, which needs no first
#: person and so refuses "Frontier models trained by OpenAI run on these clusters". Pinned (subset +
#: the exact excluded row) by `tests/test_learn_weekly_validators.py`; not imported (module doc).
_GUARDRAIL_SELF_PATTERNS: Tuple[str, ...] = (
    r"\bi(?:'m| am) an? ai\b",                                  # "I am an AI"
    r"\bas an ai(?=,? i\b)",                                    # "As an AI, I …"
    r"\bi(?:'m| am) a (?:large )?language model\b",             # "I'm a large language model"
    r"\bas a language model(?=,? i\b)",                         # "As a language model, I …"
    r"\bi(?:'m| am| was)?\s+(?:developed|built|created|made|powered)"
    r"(?:\s+\w+){0,3}\s+by (?:google|openai|gemini)\b",
    r"\bi(?:'m| am) (?:gemini|chatgpt|gpt-?[345])\b",           # "I am Gemini / ChatGPT / GPT-4"
    r"\bi(?:'m| am) a model (?:trained|made|built|created|developed|powered)\b",
    r"\bthe (?:language |ai )?model behind me\b",
    r"\bmy underlying (?:language |ai )?model\b",
)
#: Learn's own first-person rows, covering what the excluded row covered — anchored on "I"/"my".
_LEARN_SELF_PATTERNS: Tuple[str, ...] = (
    # "I was trained by Google", "I've been fine-tuned on…": the trained row, first person only.
    r"\bi(?:'m| am| was| have been|'ve been| had been)?\s+(?:trained|fine-?tuned)\b",
    # "my training data", "my knowledge cutoff".
    r"\bmy (?:own )?(?:training (?:data|set|corpus|cut-?off)|knowledge cut-?off)\b",
    # "As an AI language model, I…", "As a large language model, I…".
    r"\bas an? (?:ai|artificial intelligence|large language model|language model|llm)"
    r"(?: language model| model| assistant| system| chatbot)?(?=,? i\b)",
    # "I'm just an AI assistant", "I am a chatbot".
    r"\bi(?:'m| am) (?:just |only |merely )?an? (?:ai|artificial intelligence|llm|chatbot|"
    r"virtual assistant|ai assistant|ai model)\b",
    # Claude can write Learn pieces too (llm_provider, 2026-10-30), so the first-person vendor rows
    # above (Google / OpenAI / Gemini) get their Anthropic twins — first person only, so
    # "Anthropic sells its Claude models to clouds" stays third-person subject matter.
    r"\bi(?:'m| am) claude\b",                                  # "I am Claude"
    r"\bi(?:'m| am| was)?\s+(?:developed|built|created|made|powered|trained)"
    r"(?:\s+\w+){0,3}\s+by (?:anthropic|claude)\b",                # "I was made by Anthropic"
)
_SELF_RE = re.compile("|".join(_GUARDRAIL_SELF_PATTERNS + _LEARN_SELF_PATTERNS), re.IGNORECASE)

# ── advice: chat_guardrails.scan_answer's advice_directive + suitability_claim ───────────────

#: Copied VERBATIM from `agents/chat_guardrails._ADVICE_PATTERNS` (pinned equal by a test).
_ADVICE_PATTERNS: Tuple[str, ...] = (
    "you should buy", "you should sell", "you should hold",
    "you ought to buy", "you ought to sell",
    "i recommend buying", "i recommend selling", "i'd recommend buying", "i'd recommend selling",
    "my recommendation is to buy", "my recommendation is to sell",
    "you must buy", "you must sell", "you need to buy", "you need to sell",
    "buy this stock now", "sell this stock now", "definitely buy", "definitely sell",
    "i'd buy it", "i would buy it", "i'd sell it", "i would sell it",
)
#: Copied VERBATIM from `agents/chat_guardrails._SUITABILITY_PATTERNS` (pinned equal by a test).
_SUITABILITY_PATTERNS: Tuple[str, ...] = (
    "right for you", "right for your", "suitable for you", "suitable for your",
    "a good fit for you", "a great fit for you", "fits your profile", "fits your risk",
    "fits your goals", "matches your profile", "matches your risk", "matches your goals",
    "suits your", "appropriate for you", "appropriate for your",
    "well-suited to you", "well suited to you", "ideal for you", "perfect for you",
    "given your risk tolerance", "based on your risk", "aligns with your goals",
    "given your goals", "given your profile", "for someone like you",
)


def _boundary_regex(patterns: Iterable[str]) -> "re.Pattern[str]":
    """chat_guardrails' construction: whole-token matches, so "as an ai" never fires inside
    "as an aid"."""
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(p) for p in patterns) + r")(?!\w)")


_ADVICE_RE = _boundary_regex(_ADVICE_PATTERNS)
_SUITABILITY_RE = _boundary_regex(_SUITABILITY_PATTERNS)

# ── people: a role noun + a conduct verb in one sentence ─────────────────────────────────────

_ROLE_RE = re.compile(
    r"\b(?:ceos?|cfos?|coos?|co-?founders?|founders?|chairm[ae]n|chairwom[ae]n|chairpersons?|"
    r"chief (?:executive|financial|operating|accounting)(?: officers?)?|executives?|"
    r"management|managers?|auditors?|presidents?|directors?|insiders?|officers?)\b")
#: Three tiers of conduct wording, by how much context makes them a claim about someone's conduct.
#: Past and progressive forms only where the bare form is ordinary English ("the answer lies in",
#: "hide" as a noun).
#: 1. Misconduct by definition — refused in ANY sentence: misconduct stories are not model-written
#:    (plan, Ground rules), so a weekly piece has no honest use for them.
#:    A bare "fraud" is NOT one: "fraud detection" / "fraud prevention" is a payments and AI
#:    product (visa-vs-mastercard, the-future-of-digital-finance) — only "fraud" as something done
#:    or proven ("committed fraud", "convicted of fraud", "accounting fraud", "it was a fraud").
_MISCONDUCT_TERMS = (
    r"defraud(?:ed|ing|s)?|fraudulent(?:ly)?|embezzl(?:ed|ing|ement)|"
    r"falsif(?:ied|ying|ies)|cook(?:ed|ing)? the books|misappropriat(?:ed|ing|ion)|ponzi|"
    r"brib(?:ed|ing|ery)|insider trading|looted|swindl(?:ed|ing)|accounting scandals?|"
    r"commit(?:s|ted|ting)? (?:a |an )?(?:\w+ )?fraud|"
    r"(?:accounting|securities|wire|bank|corporate|financial|investor|massive|elaborate|outright)"
    r" frauds?|"
    r"(?:charged with|convicted of|guilty of|accused of|indicted for|indicted on|sentenced for)"
    r" (?:\w+ ){0,2}?fraud|"
    r"(?:was|is|were|are|been) (?:a |an )?(?:\w+ )?frauds?(?! (?:detection|prevention|protection|"
    r"monitoring|screening|controls?|tools?|checks?|rates?|losses|risk|teams?|alerts?))")
#: 2. A conduct verb once it has a SUBJECT — a role noun, a named company (the lexicon, the
#:    piece's own subjects) or "the company"/"the firm": "CoreWeave concealed…", "the company hid…".
_SUBJECT_CONDUCT = (
    r"misled|misleads?|lied|conceal(?:ed|ing|s)?|hid|deceiv(?:ed|ing|es)|deception|"
    r"deceptive(?:ly)?|faked|faking|cover(?:ed|ing|s)? up|covered-up|cover-up|"
    r"misrepresent(?:ed|ing|s)?|misstat(?:ed|ing)|stole|steal(?:ing|s)?|cheated|siphon(?:ed|ing)")
#: 3. Ordinary business English beside a company ("demand inflated the backlog", "TSMC fabricated
#:    the chips", "profits hiding in plain sight", "revenue growth can be misleading", "lying low")
#:    — a conduct claim only beside a role noun ("executives inflated revenue").
_ROLE_ONLY_CONDUCT = (r"inflat(?:ed|ing)|manipulat(?:ed|ing|es)|fabricat(?:ed|ing)|misleading|"
                      r"lying|hiding")
_MISCONDUCT_RE = re.compile(r"\b(?:" + _MISCONDUCT_TERMS + r")\b")
_SUBJECT_CONDUCT_RE = re.compile(r"\b(?:" + _MISCONDUCT_TERMS + "|" + _SUBJECT_CONDUCT + r")\b")
#: The securities-complaint formula ("made materially false and misleading statements"): an
#: allegation lifted from a lawsuit, read as conduct once the sentence names a company (tier 2's
#: subjects). "Revenue growth alone can be misleading" names no statement and stays honest.
_FALSE_STATEMENTS_RE = re.compile(
    r"\b(?:false|untrue|misleading)(?: and (?:false|untrue|misleading))? statements?\b")
_ROLE_CONDUCT_RE = re.compile(r"\b(?:" + _MISCONDUCT_TERMS + "|" + _SUBJECT_CONDUCT + "|"
                              + _ROLE_ONLY_CONDUCT + r")\b")
_GENERIC_COMPANY_RE = re.compile(
    r"\b(?:the|its|their|this|that) (?:company|companies|firm|firms|startup|business|board|"
    r"management team|issuer|lender|bank|exchange|fund)\b")
#: Tokens of a subject's display name that never identify it ("CoreWeave, Inc." → "coreweave").
_CORPORATE_SUFFIXES = frozenset({
    "inc", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "llc", "lp", "sa",
    "nv", "ag", "se", "holdings", "holding", "group", "the", "and", "of", "class", "a", "b", "c",
})
_SUBJECT_TOKEN_RE = re.compile(r"[a-z0-9&]+")

# ── rumour, time words, dates ────────────────────────────────────────────────────────────────

#: "alleges" / "a complaint says" join the allegation words: a lawsuit's claim is not a filed
#: fact, and the plan excludes any allegation not finally adjudicated.
_RUMOUR_RE = re.compile(
    r"\b(?:reportedly|in talks|rumou?rs?|rumou?red|sources say|sources said|sources told|"
    r"according to (?:people|sources|insiders)|people familiar with|"
    r"alleg(?:e|es|ed|edly|ing|ations?)|"
    r"complaints? (?:says?|said|alleges?|alleged|claims?|claimed|asserts?|asserted|charges?)|"
    r"unconfirmed reports?|is said to be|are said to be)\b")

#: Any MENTION of a share price or a stock move. `topic`'s peg screen refuses a filing span on a
#: mention alone (a filing that talks about its stock price is not a peg). An article block is
#: refused only when the mention STATES one — see `share_price_hits`: the hand-written catalog
#: says "a single number — market share, units, or stock price — misses the point", which is an
#: idea, not a price. A deal's "purchase price" and a dividend "per share" are filed facts and
#: pass. Widened 2026-10-23: a live preview printed CoreWeave's "last reported sale price of
#: $79.88 per share" — "sale price" and "price of $N per share" were not covered.
SHARE_PRICE_RE = re.compile(
    r"\b(?:closing\s+(?:sale\s+)?price|closed\s+at\s+\$|stock\s+price|share\s+price|"
    r"trading\s+price|offering\s+price|price\s+per\s+share|per[- ]share\s+price|"
    r"(?:last\s+)?(?:reported\s+)?sale\s+price|"
    r"market\s+price\s+(?:of|for)\s+(?:its\s+|the\s+|our\s+)?(?:class\s+[a-z]\s+)?"
    r"(?:common\s+)?(?:stock|shares?)|"
    r"trad(?:ed|es|ing)\s+(?:at|as\s+high\s+as|as\s+low\s+as)\s+\$|"
    r"from\s+the\s+(?:prior|previous)\s+(?:trading\s+)?(?:day|close))\b"
    r"|(?<!purchase\s)\bprice\s+of\s+(?:approximately\s+|about\s+)?\$\s?[0-9][0-9,]*(?:\.[0-9]+)?"
    r"\s+per\s+share\b",
    re.IGNORECASE)
#: A figure in the same sentence: a dollar amount or a percentage.
_PRICE_FIGURE_RE = re.compile(r"\$\s?[0-9]|[0-9]\s?%|\bpercent\b", re.IGNORECASE)
#: A move word within a few words AFTER the mention ("stock price rose", "closing price was a
#: record").
_PRICE_MOVE_RE = re.compile(
    r"^\W*(?:\w+\W+){0,6}?(?:rose|rises|rising|fell|falls|falling|jumped|jumps|surged|surges|"
    r"soared|soars|dropped|drops|plunged|plunges|climbed|climbs|doubled|tripled|halved|crashed|"
    r"tumbled|slumped|spiked|rallied|record|all-time\s+high|52-week)\b", re.IGNORECASE)


def share_price_hits(clean: str) -> List[str]:
    """Each sentence that STATES a share price or a stock move: a `SHARE_PRICE_RE` mention with a
    figure in the same sentence, or a move word just after it. Pure, linear."""
    hits: List[str] = []
    for sent in compliance.sentences(clean):
        for m in SHARE_PRICE_RE.finditer(sent):
            if _PRICE_FIGURE_RE.search(sent) or _PRICE_MOVE_RE.search(sent[m.end():m.end() + 80]):
                hits.append(m.group(0))
                break
    return hits

# ── advice the chat rows miss: trade verbs, the reader's holdings, valuation verdicts ─────────

#: A trade verb; its object (the next 3 words) decides whether it is a trade. "buy back" is a
#: buyback, never a reader's trade; a hyphenated "short-term" is an adjective.
_TRADE_VERB_RE = re.compile(
    r"\b(?:buy(?![- ]back\b)|sell|short(?![-])|trim|dump|avoid|load up on|get in(?:to)?|"
    r"consider (?:buying|selling|trimming|adding to))\b")
#: Words that make the verb's object a security ("sell the shares", "trim a position").
_TRADE_OBJECTS = frozenset({"stock", "stocks", "shares", "position", "positions", "stake",
                            "stakes"})
#: A third-person subject right before the verb makes it a FINITE verb that describes someone
#: else ("Companies sell shares to raise money", "customers buy CoreWeave capacity") — never an
#: instruction to the reader. It does not excuse a named company's STOCK being traded.
_TRADE_THIRD_PERSON = frozenset({
    "it", "they", "he", "she", "who", "that", "which", "companies", "company", "firms", "firm",
    "issuers", "issuer", "businesses", "business", "startups", "founders", "insiders", "funds",
    "investors", "shareholders", "banks", "lenders", "customers", "buyers", "sellers",
    "retailers", "rivals", "competitors", "others", "executives", "managers", "owners",
    "traders", "people", "clouds", "labs",
})
#: The issuer's own financing ("sell its shares", "sell new shares to raise cash").
_TRADE_OWN_WORDS = frozenset({"its", "their", "his", "her", "new", "additional", "own"})
#: "short" as an adjective ("a short position", "in the short run").
_SHORT_ADJ_PREV = frozenset({"a", "an", "the", "too", "so", "very", "in", "run"})
_WORD_RE = re.compile(r"[a-z0-9&]+")
#: The reader's own holdings: a weekly piece never addresses them ("trim your position").
#: Singular "share" is left out: "your share of the profits" is ordinary English.
_YOUR_HOLDING_RE = re.compile(
    r"\byour (?:[a-z0-9&'-]+ )?(?:shares|stocks?|positions?|portfolios?|stakes?|holdings)\b")
#: Valuation verdicts. `undervalued` / `overvalued` / `must-own` have no honest business sense;
#: `cheap` / `expensive` / `bargain` do ("Microsoft could afford a long, expensive transition",
#: "cheaper ad-supported tiers"), so they count only when compliance's VALUATION row binds them
#: to the company ("Costco shares are expensive") or the sentence is about the stock.
_VERDICT_STRICT_RE = re.compile(r"\b(?:under|over)-?valued\b|\bmust[- ]owns?\b")
_VERDICT_SOFT_RE = re.compile(r"\b(?:bargains?|cheap(?:er|est)?|expensive)\b")
_STOCK_WORD_RE = re.compile(r"\b(?:stocks?|shares)\b")

# ── quotations and authorship ────────────────────────────────────────────────────────────────

#: A quote mark in compliance's skeleton (curly double quotes are already straightened there).
_QUOTE_MARK_RE = re.compile("[\"\u00ab\u00bb\u201f]")
#: Words that attribute what follows (or precedes) to a speaker.
_REPORTING_RE = re.compile(
    r"\b(?:said|says|say|told|tells|called|calls|described|describes|stated|wrote|writes|"
    r"quipped|remarked|declared|put it|according to|in (?:his|her|its|their) (?:own )?words)\b")
#: Words before / after a quote mark read for attribution (the plan: ~8 before, the "," said
#: right after the closing mark).
_QUOTE_WORDS_BEFORE = 8
_QUOTE_WORDS_AFTER = 3
#: What may sit inside a filing's defined-term parenthesis besides the quoted terms:
#: '(the "Company")', '(collectively, the "Notes")', '(the "Agreement" or "MSA")'.
_DEFINED_TERM_WORDS = frozenset({"the", "a", "an", "each", "collectively", "together",
                                 "individually", "and", "or", "as", "hereinafter", "herein",
                                 "referred", "to", "such"})
_DEFINED_TERM_MAX = 160
_QUOTED_RE = re.compile(r'"[^"]*"')
#: The three parts of "this article was written by <a model vendor>" — ALL in one sentence.
_AUTHOR_SUBJECT_RE = re.compile(
    r"\b(?:this|the) (?:article|piece|analysis|series|narration|summary)\b|\byou(?: are|'re) reading\b")
_AUTHOR_VERB_RE = re.compile(
    r"\b(?:written|drafted|produced|generated|created|narrated|powered)(?: [a-z0-9'-]+){0,3} "
    r"(?:by|with|using)\b")
_AUTHOR_VENDOR_RE = re.compile(
    r"\b(?:gemini|google|openai|chatgpt|gpt(?:-?[0-9][a-z0-9.]*)?|claude|anthropic|llms?|"
    r"(?:large )?language models?|ai models?)\b")
_TIME_WORD_RE = re.compile(
    r"\b(?:this week|last week|next week|this month|last month|next month|today|today's|tonight|"
    r"this morning|yesterday|recently)\b")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")
_LONG_DATE_RE = re.compile(r"\b(" + "|".join(_MONTHS) + r") (\d{1,2}), (\d{4})\b")
_ISO_DATE_RE = re.compile(r"(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\d-])")

# ── forbidden tokens ─────────────────────────────────────────────────────────────────────────

_BOLD_RE = re.compile(r"\*\*|__")
_HEADING_RE = re.compile(r"(?m)^[ \t]*#{1,6}(?:[ \t]|$)")
_URL_RE = re.compile(r"(?i)\b(?:https?|ftp)://|\bwww\.")
_BRAND_TOKEN_RE = re.compile(r"\bcaydex\b|\bcay[ -]?ai\b")

# ── article shape ────────────────────────────────────────────────────────────────────────────

#: Matched with `fullmatch` — `$` alone also matches before a trailing "\n", which would let
#: "slug-2026-10-23\n" through.
SLUG_RE = re.compile(r"[a-z0-9-]+-(\d{4})-(\d{2})-(\d{2})")
#: A tagLabel that repeats the app's own header (owner 2026-11-01): iOS draws "FEATURED DEEP DIVE" above
#: the featured article's tag, so a tag reading "FEATURE DEEP DIVE" printed the label twice. Refused
#: case-insensitively — DEEP DIVE (any spacing or hyphen) and any word starting FEATUR.
GENERIC_TAG_RE = re.compile(r"(?i)\bdeep[\s-]*dive|\bfeatur")
_HEX6_RE = re.compile(r"^[0-9A-Fa-f]{6}$")
REQUIRED_ARTICLE_KEYS: Tuple[str, ...] = (
    "slug", "title", "subtitle", "cardSubtitle", "category", "author", "readTimeMinutes",
    "viewCount", "heroGradientColors", "keyHighlights", "sections",
)
#: Engagement counters a model or a bug could fabricate. Any non-empty value is refused: a
#: made-up "12K learners" is a false claim under the brand.
ENGAGEMENT_KEYS: Tuple[str, ...] = ("viewCount", "learnerCount", "commentCount", "likeCount")
_BLOCK_KEYS: Mapping[str, FrozenSet[str]] = {
    "paragraph": frozenset({"type", "text", "readAlong"}),
    "subheading": frozenset({"type", "text", "readAlong"}),
    "callout": frozenset({"type", "text", "style", "icon", "readAlong"}),
    "bulletList": frozenset({"type", "items", "itemsReadAlong"}),
}
_WORD_LIMITS: Mapping[str, int] = {
    "paragraph": C.MAX_PARAGRAPH_WORDS,
    "subheading": C.MAX_SUBHEADING_WORDS,
    "callout": C.MAX_CALLOUT_WORDS,
}
_WORD_BOUNDS: Mapping[str, Tuple[int, int]] = {
    C.KIND_DEEP_DIVE: C.DEEP_DIVE_WORDS,
    C.KIND_SLOT: C.SLOT_WORDS,
}

#: Codes `grounding.check_grounding` emits; kept verbatim so the writer's repair prompt can reuse
#: marketing's hints for them.
GROUNDING_CODES: FrozenSet[str] = frozenset({
    "ungrounded_number", "number_context", "ungrounded_name_number", "ungrounded_acronym",
    "ungrounded_entity", "person_named", "non_latin", "too_long", "grounding_error",
})


# ── small helpers ────────────────────────────────────────────────────────────────────────────


def _detail(text: str) -> str:
    return (text or "")[:_DETAIL_MAX]


def _dedupe(issues: Iterable[Issue]) -> List[Issue]:
    seen = set()
    out: List[Issue] = []
    for i in issues:
        key = (i.field, i.code, i.detail)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def _capped(field: str, raw: str, cap: int, out: List[Issue]) -> str:
    """The raw string cut to `cap` BEFORE anything reads it; past the cap is `too_long`."""
    if len(raw) > cap:
        out.append(Issue(field, "too_long", f"{len(raw)} chars > {cap}"))
        return raw[:cap]
    return raw


def _clean_view(raw: str) -> Tuple[str, str]:
    """(clean, folded): compliance's stored form (entities decoded, NFKC, zero-width and format
    characters stripped) and its matching view (lower case, accents and dotless/stroked letters
    folded). Both "Caydex" with a zero-width space inside and "Caydex" with an accent on the a read
    as "caydex"."""
    text = compliance.clean(raw)
    return text, compliance.fold(text)


def word_count(text: Any) -> int:
    """Whitespace words of the cleaned text (0 for a non-string). Capped like a block, so a
    pathological input cannot cost more than a block does."""
    if not isinstance(text, str):
        return 0
    return len(compliance.clean(text[:BLOCK_TEXT_CAP * 4]).split())


def _has_full_date(folded: str) -> bool:
    """A real calendar date written in full ("october 21, 2026") or ISO ("2026-10-21") — the peg's
    anchor that makes a time word like "this week" readable in the archive."""
    for m in _LONG_DATE_RE.finditer(folded):
        try:
            date(int(m.group(3)), _MONTHS.index(m.group(1)) + 1, int(m.group(2)))
            return True
        except ValueError:
            continue
    for m in _ISO_DATE_RE.finditer(folded):
        try:
            date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return True
        except ValueError:
            continue
    return False


# ── public single-rule helpers (the judge preview and the writer's repair prompt reuse them) ─


def self_reference_hits(text: Any) -> List[str]:
    """First-person AI self-reference in `text` ("As an AI, I…", "I was trained by Google", "my
    training data"). Third-person AI subject matter ("models trained by OpenAI", "LLMs", "AI model
    training") is NOT a hit. Capped at `HELPER_TEXT_CAP`; never raises."""
    if not isinstance(text, str) or not text:
        return []
    _clean, folded = _clean_view(text[:HELPER_TEXT_CAP])
    return [m.group(0) for m in _SELF_RE.finditer(folded)]


def advice_hits(text: Any) -> List[str]:
    """`chat_guardrails.scan_answer`'s `advice_directive` / `suitability_claim` tags for `text`
    (each at most once, in that order). Capped; never raises."""
    if not isinstance(text, str) or not text:
        return []
    _clean, folded = _clean_view(text[:HELPER_TEXT_CAP])
    tags: List[str] = []
    if _ADVICE_RE.search(folded):
        tags.append("advice_directive")
    if _SUITABILITY_RE.search(folded):
        tags.append("suitability_claim")
    return tags


def subject_tokens(subjects: Any) -> FrozenSet[str]:
    """The identifying lower-case tokens of a piece's subjects (names and tickers): corporate
    suffixes and ordinary English words (`grounding.ordinary`, the grounding check's own test)
    dropped, so "Advanced Micro Devices" adds nothing (the company lexicon finds it) and
    "CoreWeave, Inc." adds "coreweave" — an ordinary word as a subject would turn every
    "devices concealed…" sentence into a claim about the company."""
    out = set()
    if isinstance(subjects, str):
        subjects = (subjects,)
    try:
        # islice, not list()[:50]: a runaway iterable is never materialised.
        items = list(islice(subjects or (), _MAX_SUBJECTS))
    except TypeError:
        logger.error("learn weekly validators: subjects is %s, not an iterable of names",
                     type(subjects).__name__)
        return frozenset()
    base = grounding.full_vocab(corpus_vocab())
    roots = grounding.vocab_roots(base)
    for s in items:
        if not isinstance(s, str):
            continue
        for tok in _SUBJECT_TOKEN_RE.findall(compliance.fold(s[:TITLE_CAP])):
            if (len(tok) >= 2 and tok not in _CORPORATE_SUFFIXES
                    and not grounding.ordinary(tok, base, roots)):
                out.add(tok)
    return frozenset(out)


def _conduct(clean: str, subjects: FrozenSet[str]) -> List[str]:
    out: List[str] = []
    for sent in compliance.sentences(clean):
        fs = compliance.fold(sent)
        role = _ROLE_RE.search(fs)
        verb = _ROLE_CONDUCT_RE.search(fs) if role is not None else None
        if role is not None and verb is not None:
            out.append(f"{role.group(0)} … {verb.group(0)}")
            continue
        verb = _MISCONDUCT_RE.search(fs)
        if verb is not None:
            out.append(f"misconduct … {verb.group(0)}")
            continue
        verb = _SUBJECT_CONDUCT_RE.search(fs) or _FALSE_STATEMENTS_RE.search(fs)
        if verb is None:
            continue
        subject = _GENERIC_COMPANY_RE.search(fs)
        if subject is not None:
            out.append(f"{subject.group(0)} … {verb.group(0)}")
            continue
        named = subjects.intersection(_SUBJECT_TOKEN_RE.findall(fs)) if subjects else ()
        if named:
            out.append(f"{sorted(named)[0]} … {verb.group(0)}")
            continue
        mentions = compliance.sentence_company_mentions(compliance.skeleton(sent))
        if mentions:
            out.append(f"{mentions[0][0]} … {verb.group(0)}")
    return out


def _names_subject(fs: str, sent: str, subjects: FrozenSet[str]) -> bool:
    """The folded sentence names one of the piece's subjects or a lexicon company."""
    if subjects and subjects.intersection(_WORD_RE.findall(fs)):
        return True
    return bool(compliance.sentence_company_mentions(compliance.skeleton(sent)))


def _trade_hits(sents: List[Tuple[str, str]], subjects: FrozenSet[str]) -> List[str]:
    """A trade verb whose object (within the next 3 words) is a subject or a security — "Buy
    CoreWeave.", "Consider trimming your CoreWeave position." The chat rows only know
    second-person phrases and compliance's directive rows only lexicon companies, so a new listing
    the topic step picked ("Buy CoreWeave.") passed both. `sents` = (sentence, folded) pairs."""
    out: List[str] = []
    for _sent, fs in sents:
        for m in _TRADE_VERB_RE.finditer(fs):
            verb = m.group(0)
            before = _WORD_RE.findall(fs[max(0, m.start() - 40):m.start()])
            prev = before[-1] if before else ""
            window = _WORD_RE.findall(fs[m.end():m.end() + 80])[:3]
            named = next((w for w in window if w in subjects), None) if subjects else None
            security = next((w for w in window if w in _TRADE_OBJECTS), None)
            if named is None and security is None:
                continue
            if verb == "short" and prev in _SHORT_ADJ_PREV:
                continue                    # "a short position", "in the short run"
            if prev in _TRADE_THIRD_PERSON and not (named and security):
                continue                    # "Companies sell shares to raise money."
            if named is None and window and window[0] in _TRADE_OWN_WORDS:
                continue                    # "sell its shares", "sell new shares"
            out.append(f"{verb} … {named or security}")
    return out


def _valuation_hits(sents: List[Tuple[str, str]], subjects: FrozenSet[str],
                    valuation_details: List[str]) -> List[str]:
    """A valuation verdict about a named company (a subject or a lexicon company) — the part of
    compliance's IGNORED valuation/evaluative rows that is a verdict, not case-study vocabulary
    ("value trap", "per share", "market cap" stay ignored). `valuation_details` are the folded
    details of the block's class_b_valuation hits; a detail is attributed to the sentence that
    contains it."""
    out: List[str] = []
    for sent, fs in sents:
        strict = _VERDICT_STRICT_RE.search(fs)
        soft = _VERDICT_SOFT_RE.search(fs)
        if strict is None and soft is None:
            continue
        if not _names_subject(fs, sent, subjects):
            continue
        if strict is not None:
            out.append(strict.group(0))
            continue
        bound = next((d for d in valuation_details if d in fs and _VERDICT_SOFT_RE.search(d)), None)
        if bound is not None:
            out.append(bound)
        elif _STOCK_WORD_RE.search(fs):
            out.append(f"{soft.group(0)} … stock")
    return out


def _in_defined_term(sk: str, a: int, b: int) -> bool:
    """The quote marks at `a` and `b` close a filing's defined term — '(the "Company")' — which is
    a label, never anyone's words: inside one short parenthesis holding only quoted terms and
    connector words."""
    open_at = sk.rfind("(", max(0, a - _DEFINED_TERM_MAX), a)
    if open_at == -1 or ")" in sk[open_at:a]:
        return False
    close_at = sk.find(")", b, b + _DEFINED_TERM_MAX)
    if close_at == -1 or "(" in sk[b:close_at]:
        return False
    inside = sk[open_at + 1:close_at]
    unquoted = _QUOTED_RE.sub(" ", inside)
    if '"' in unquoted:
        return False
    return set(_WORD_RE.findall(unquoted.lower())) <= _DEFINED_TERM_WORDS


def _quotation_hits(clean: str, subjects: FrozenSet[str]) -> List[str]:
    """A double-quoted span attributed to a speaker: a reporting verb, a role noun or a subject
    within ~8 words before the opening mark or right after the closing one ('The chief executive
    told investors the company "has never been stronger."', '"We delivered," said …'). Weekly
    pieces carry no quotations; a framing quote with no speaker ('narrower than "the challenger
    won"') is the bundle's own honest style and passes, as does a filing's defined term."""
    sk = compliance.skeleton(clean)
    marks = [m.start() for m in _QUOTE_MARK_RE.finditer(sk)]
    out: List[str] = []
    for k in range(0, len(marks), 2):
        a = marks[k]
        b = marks[k + 1] if k + 1 < len(marks) else len(sk)
        if b < len(sk) and _in_defined_term(sk, a, b):
            continue
        before = _WORD_RE.findall(compliance.fold(sk[max(0, a - 160):a]))[-_QUOTE_WORDS_BEFORE:]
        after = _WORD_RE.findall(compliance.fold(sk[b + 1:b + 80]))[:_QUOTE_WORDS_AFTER]
        quoted = sk[a + 1:b].strip()
        for window in (" ".join(before), " ".join(after)):
            m = _REPORTING_RE.search(window) or _ROLE_RE.search(window)
            who = m.group(0) if m else next((w for w in window.split() if w in subjects), None)
            if who:
                out.append(f"{who} … \"{quoted[:60]}\"")
                break
    return out


def _authorship_hits(sents: List[Tuple[str, str]]) -> List[str]:
    """"This article was drafted with the Gemini models" — the model vendor named as the
    article's AUTHOR, in the third person, which first-person `self_reference` cannot see once
    the fact sheet grounds the vendor's name. All three parts must share one sentence, so AI
    vendors as subject matter ("Google Cloud sells access to the Gemini models") pass."""
    out: List[str] = []
    for _sent, fs in sents:
        subj = _AUTHOR_SUBJECT_RE.search(fs)
        verb = _AUTHOR_VERB_RE.search(fs) if subj else None
        vendor = _AUTHOR_VENDOR_RE.search(fs) if verb else None
        if vendor is not None:
            out.append(f"{subj.group(0)} … {verb.group(0)} … {vendor.group(0)}")
    return out


def conduct_hits(text: Any, subjects: Any = ()) -> List[str]:
    """A conduct claim per sentence (plan §1.7 People): a role noun (CEO, founder, chairman, CFO,
    executives, management, auditors…) beside any conduct verb ("<role> … <verb>"); a
    misconduct-by-definition term anywhere ("committed fraud", "embezzled" — never a bare "fraud
    prevention"); or a conduct verb ("concealed",
    "misled") beside a company — the lexicon's, "the company"/"the firm", or one of `subjects`
    (the piece's own subject names/tickers: a new listing such as CoreWeave is in no lexicon).
    The plan states the role rule; the company half is the same defamation shape one step
    removed, and misconduct stories are not model-written at all. Capped; never raises."""
    if not isinstance(text, str) or not text:
        return []
    return _conduct(compliance.clean(text[:HELPER_TEXT_CAP]), subject_tokens(subjects))


def misquote_hits(text: Any) -> List[str]:
    """Every `BANNED_PHRASES` / `ATTRIBUTION_REQUIRED` phrase in `text`, normalised the way the
    bundle test reads them ("short-run," = "short run"). Capped."""
    if not isinstance(text, str) or not text:
        return []
    normed = rules.norm(compliance.clean(text[:HELPER_TEXT_CAP]))
    return [p for p in tuple(rules.BANNED_PHRASES) + tuple(rules.ATTRIBUTION_REQUIRED)
            if rules.norm(p) in normed]


def forbidden_token_hits(text: Any) -> List[str]:
    """`**`/`__`, a markdown heading, a URL, emoji, "Caydex"/"Cay AI", a non-Latin letter, a
    non-ASCII digit — one detail per kind. Capped."""
    if not isinstance(text, str) or not text:
        return []
    raw = text[:HELPER_TEXT_CAP]
    clean, folded = _clean_view(raw)
    out: List[str] = []
    # Read the raw AND the cleaned string: a zero-width space between the two "*" hides the
    # marker from the raw read, and `clean` could in principle normalise one into existence.
    for view in (raw, clean):
        m = _BOLD_RE.search(view)
        if m:
            out.append(f"markdown emphasis {m.group(0)!r}")
            break
    if _HEADING_RE.search(clean):
        out.append("markdown heading")
    m = _URL_RE.search(clean)
    if m:
        out.append(f"url {m.group(0)!r}")
    emoji = sorted({ch for ch in clean if compliance._is_emoji(ch)})
    if emoji:
        out.append("emoji " + "".join(emoji[:8]))
    m = _BRAND_TOKEN_RE.search(folded)
    if m:
        out.append(f"brand {m.group(0)!r}")
    letters = sorted({ch for ch in clean if ch.isalpha() and not compliance._is_latin_letter(ch)})
    if letters:
        out.append("non-Latin letter " + "".join(letters[:8]))
    if has_non_ascii_digit(clean):
        out.append("non-ASCII digit")
    return out


# ── grounding ────────────────────────────────────────────────────────────────────────────────

_VOCAB: Optional[FrozenSet[str]] = None


def corpus_vocab() -> FrozenSet[str]:
    """The Learn corpus's lower-case vocabulary, built exactly as the marketing writer builds it
    (`content_pool.load_corpus`: `_vocab` over every split sentence of both bundled JSON files).
    `grounding.build_context` adds the function/template/calendar words itself. Cached for the
    process once NON-empty: an unreadable corpus is logged by `content_pool` and here, and the
    empty vocabulary it yields makes grounding STRICTER (every capitalised word must be on the
    sheet), never looser — so it is retried on the next call rather than cached."""
    global _VOCAB
    if _VOCAB is not None:
        return _VOCAB
    raw = content_pool._raw_items()
    vocab = content_pool._vocab([s for *_rest, sents in raw for s in sents])
    if not vocab:
        logger.error("learn weekly validators: the Learn corpus produced NO vocabulary "
                     "(items=%d); grounding runs fail-closed on the fact sheet alone", len(raw))
        return vocab
    _VOCAB = vocab
    return vocab


#: One fact sentence is one rendered line (plan §1.6); the sheet is capped at 9,000 chars
#: (`contracts.DEEP_DIVE_SHEET_CAP`). These bound the context build for a malformed sheet.
_FACT_TEXT_CAP = 2000
_MAX_FACTS = 400


def build_grounding_context(sheet: Any) -> grounding.GroundingContext:
    """The grounding context for one piece: its fact sheet's sentences (cleaned, as marketing
    cleans its source sentences) against the corpus vocabulary. A sheet that is not a
    `contracts.FactSheet` (or carries no usable sentence) builds an EMPTY context: every number
    and every non-ordinary name is then ungrounded — fail closed, logged loudly."""
    sentences: List[str] = []
    if isinstance(sheet, C.FactSheet):
        try:
            texts = [getattr(f, "text", None) for f in tuple(sheet.facts)[:_MAX_FACTS]]
        except TypeError as e:      # `facts` is not iterable: a hand-built, malformed sheet
            logger.error("learn weekly validators: fact sheet %r has unreadable facts (%s: %s)",
                         sheet.topic_title, type(e).__name__, e)
            texts = []
        for text in texts:
            if isinstance(text, str) and text.strip():
                # A dated peg is two grounding facts: the date sentence's day and year must not
                # anchor to the span's words ("raised its dividend for 20 consecutive years").
                for part in C.split_peg_date(text[:_FACT_TEXT_CAP]):
                    if part.strip():
                        sentences.append(compliance.clean(part))
        if not sentences:
            logger.error("learn weekly validators: fact sheet %r has no usable sentence; "
                         "grounding will refuse every figure and name", sheet.topic_title)
    else:
        logger.error("learn weekly validators: build_grounding_context got %s, not a FactSheet; "
                     "grounding will refuse every figure and name", type(sheet).__name__)
    ctx = grounding.build_context(sentences, corpus_vocab())
    # A subject's own digit-bearing name ("3M CO reported revenue …") must not ground the number it
    # looks like: the sheet read "3M" as 3 million, so "3M held 3M in cash" passed with no
    # violation (review, 2026-10-23). Numbers come from the sheet with those names masked; every
    # other part of the context (the name as a token included) is the sheet as written.
    masked = [_mask_subject_names(s, sheet) for s in sentences]
    if masked != sentences:
        # Both number-derived parts: "3M" also gave the sheet a "million" MAGNITUDE, so "3M spends
        # millions on research" passed on a sheet stating only billions (review round 3). The
        # designations stay unmasked: they are what grounds the name "3M CO" itself.
        m = grounding.build_context(masked, corpus_vocab())
        ctx = replace(ctx, numbers=m.numbers, magnitudes=m.magnitudes)
    return ctx


def _mask_subject_names(sentence: str, sheet: Any) -> str:
    """`sentence` with every subject name that holds a digit replaced by a word: the name however
    the sheet spells it ("1 800 FLOWERS COM INC", "1-800-Flowers.com", "10X GENOMICS, INC." —
    its "800" or "10x" is not a figure), and every bare letters-and-digits name token ("3M",
    never "$3M"). A figure that only equals a name digit ("800 stores") stays a figure."""
    names = _sheet_subject_names(sheet)
    for rx in _loose_name_patterns(names):
        sentence = rx.sub("Subjectname", sentence)
    tokens = _name_number_tokens(names)
    if tokens:
        sentence = re.sub(r"(?<![\w$€£¥])(?:" + "|".join(re.escape(t) for t in sorted(tokens))
                          + r")(?!\w)", "Subjectname", sentence, flags=re.IGNORECASE)
    return sentence


_NAME_SEP = r"[\s\-.,&']*"


def _loose_name_patterns(names: Sequence[str]) -> List["re.Pattern[str]"]:
    """One pattern per subject name holding a digit: its words (corporate suffixes dropped) in
    order, any punctuation or spacing between them, required up to its first all-letter word and
    optional after it ("1 800 FLOWERS COM" → "1-800-Flowers", "1-800-Flowers.com")."""
    out = []
    for name in names:
        core = [t for t in _SUBJECT_TOKEN_RE.findall(compliance.fold(name[:TITLE_CAP]))
                if t not in _CORPORATE_SUFFIXES]
        if not core or not any(any(c.isdigit() for c in t) for t in core):
            continue
        cut = next((i + 1 for i, t in enumerate(core) if t.isalpha()), len(core))
        body = _NAME_SEP.join(re.escape(t) for t in core[:cut])
        body += "".join(f"(?:{_NAME_SEP}{re.escape(t)})?" for t in core[cut:])
        out.append(re.compile(rf"(?<![\w$€£¥]){body}(?!\w)", re.IGNORECASE))
    return out


def _sheet_subject_names(sheet: Any) -> List[str]:
    out: List[str] = []
    for s in getattr(sheet, "subjects", None) or () if isinstance(sheet, C.FactSheet) else ():
        out += [x for x in (getattr(s, "name", None), getattr(s, "ticker", None))
                if isinstance(x, str) and x]
    return out


#: Everyday words grounding's dictionary (Webster's 1934, the Learn corpus, `grounding.MODERN_WORDS`)
#: cannot know — mostly modern closed compounds. An AI-chips outline was refused after its repair
#: on "Server Lifespans and Depreciation" (preview, 2026-10-24), and 21 of 31 plausible AI-chips
#: headings failed the same way ("Hyperscalers", "Capex", "Buybacks", "Tradeoffs"). Lemmas: a form
#: is the lemma or the lemma + "s". Every entry is a common word in a modern dictionary whose exact
#: spelling is not a listed company's or a well-known brand's, franchise's or event's name — so NOT
#: "bandwidth" (Bandwidth Inc.), "hyperscale" (Hyperscale Data), "fallout", "meltdown", "liftoff",
#: "logjam", "gigafactory" (review, 2026-10-24). Like web2's own words ("Elastic", "Unity"), an entry
#: can still be a private body's name ("Lifespan", a hospital system): the exemption is positional,
#: and the judge's support check reads every sentence. Not added to the grounding vocabulary: its
#: roots and 7-character prefix rule would make "Supercom" ordinary through "supercomputer". No
#: compound rule either ("Greenspan", "Brookfield", "Honeywell" are word + word). Pinned by
#: `tests/test_learn_weekly_validators.py`: no entry is already ordinary, a lexicon company or a name.
EVERYDAY_WORDS: FrozenSet[str] = frozenset("""
backorder bailout baseload breakeven buildout buyback buyout capex carryover carveout
cashflow chokepoint colocation datacenter deglobalization downcycle downtick downtime earnout
endpoint exaflop fab firmware freemium frontrunner gigawatt giveback greenwashing hashrate
headcount hyperscaler lifecycle lifespan lineup livestream lockdown malware megacap
megadeal megaproject megatrend microgrid multicloud nearshoring nowcast onboard onboarding opex
payback paydown payout petaflop photonic photonics pushback ransomware reshoring roadmap rollout
rollover rundown selloff skillset spinoff spinout stablecoin standalone supercomputer
supercomputing takeover teraflop terawatt timeframe tokenization tradeoff upcycle upsell uptick
uptime workaround writedown writeoff
""".split())

#: The plural of a letter ("the three Hs"): as nameless as the single letter grounding skips.
_LETTER_PLURAL_RE = re.compile(r"[A-Z]s")
#: A Capitalised word with no internal capital ("CapEx", "LifeSpan" are a brand's spelling).
_CAPITALISED_RE = re.compile(r"[A-Z][a-z]+")


def _bare_token(tok: str) -> str:
    """A `grounding._TOKEN_RE` token as `grounding._check_entities` reports it."""
    bare = tok.strip("'-.&")
    return bare[:-2] if bare.lower().endswith("'s") else bare


#: Closing quotes and brackets after a sentence's end punctuation ('… schedule."').
_SENTENCE_TAIL = " \t\r\n\"')]”’"


def _opens_sentence(sk: str, start: int, sent: str, pos: int) -> bool:
    """Is the token at `pos` of `sent` (found at `start` in the skeleton `sk`) its sentence's first
    word: nothing but punctuation before it in the sentence (a bullet dash, a quote — never "2025:"),
    and the sentence follows the text's start or end punctuation, never a bare line break inside a
    sentence ("Investors watch\\nBuybacks closely" — `compliance.sentences` splits on every newline)."""
    if any(ch.isalnum() for ch in sent[:pos]):
        return False
    before = sk[:start].rstrip(_SENTENCE_TAIL)
    return not before or before[-1] in ".!?"


def _capital_says_no_name(token: str, text: str, heading: bool) -> bool:
    """Every occurrence of `token` in `text` sits where its capital belongs to the text, not the
    word: anywhere in a heading, a sentence's first word (`_opens_sentence`), or a Title-Case line
    (a list item such as "Payback Periods and Lifespans", `compliance._title_cased`). A capital
    mid-sentence in prose is the writer naming something ("Investors watch Buybacks closely"), so it
    stays a name. Read on the skeleton, tokenised and split as `grounding._check_entities` /
    `_check_name_pairs` read it; a sentence that cannot be placed reads as prose (fail closed)."""
    if heading:
        return True
    sk = compliance.skeleton(text)
    at = 0
    for sent in compliance.sentences(sk):
        start = sk.find(sent, at)
        if start >= 0:
            at = start + len(sent)
        title: Optional[bool] = None
        for m in grounding._TOKEN_RE.finditer(sent):
            if _bare_token(m.group(0)) != token:
                continue
            if start >= 0 and _opens_sentence(sk, start, sent, m.start()):
                continue
            if title is None:
                title = compliance._title_cased(sent)
            if not title:
                return False
    return True


def _everyday_word(token: str, text: str, heading: bool, letter_plurals: bool = True) -> bool:
    """Is grounding's `ungrounded_entity` `token` an everyday word rather than a name: a letter's
    plural anywhere ("Hs"), or an `EVERYDAY_WORDS` form ("Lifespans") where its capital says no
    name. A spelling the company lexicon knows is that company, whatever the list says.
    `letter_plurals` is False for an ALL-CAPS label read in title case: there "Gs" was "GS", a
    ticker (Goldman Sachs), never a letter's plural."""
    low = token.lower()
    forms = (low, low[:-1]) if low.endswith("s") else (low,)
    letter_plural = letter_plurals and bool(_LETTER_PLURAL_RE.fullmatch(token))
    if not letter_plural and not (_CAPITALISED_RE.fullmatch(token)
                                  and any(w in EVERYDAY_WORDS for w in forms)):
        return False
    if any(compliance._is_company_word(w, frozenset()) for w in forms):
        return False
    return letter_plural or _capital_says_no_name(token, text, heading)


# ── the per-text rule chain ──────────────────────────────────────────────────────────────────


def _text_issues(field: str, raw: Any, *, cap: int, time_words_ok: bool,
                 grounding_ctx: Optional[grounding.GroundingContext],
                 subjects: FrozenSet[str] = frozenset(), caps_label: bool = False,
                 heading: bool = False) -> List[Issue]:
    """Every text rule for ONE displayed/narrated string. `time_words_ok` is true only for a
    peg-section block that states a full date; `subjects` are `subject_tokens` of the piece.
    `caps_label`: an ALL-CAPS display label ("DEEP DIVE") is grounded in title case — grounding
    reads every all-caps token as a possible ticker, so the label's ordinary words would fail,
    while a ticker in it ("NVDA" → "Nvda") still must come from the sheet. `heading`: a title or
    label, where any capital may be the heading's (`_everyday_word`)."""
    out: List[Issue] = []
    if not isinstance(raw, str):
        out.append(Issue(field, "block_invalid", f"expected a string, got {type(raw).__name__}"))
        return out
    raw = _capped(field, raw, cap, out)
    clean, folded = _clean_view(raw)
    if not clean:
        out.append(Issue(field, "empty", "no visible text"))
        return out

    for hit in forbidden_token_hits(raw):
        out.append(Issue(field, "forbidden_token", _detail(hit)))
    for hit in _SELF_RE.finditer(folded):
        out.append(Issue(field, "self_reference", _detail(hit.group(0))))
    m = _ADVICE_RE.search(folded)
    if m:
        out.append(Issue(field, "advice_directive", _detail(m.group(0))))
    m = _SUITABILITY_RE.search(folded)
    if m:
        out.append(Issue(field, "suitability_claim", _detail(m.group(0))))
    for hit in _conduct(clean, subjects):
        out.append(Issue(field, "conduct_claim", _detail(hit)))
    m = _RUMOUR_RE.search(folded)
    if m:
        out.append(Issue(field, "rumour", _detail(m.group(0))))
    for hit in share_price_hits(clean):
        out.append(Issue(field, "share_price", _detail(hit)))
    if not time_words_ok:
        m = _TIME_WORD_RE.search(folded)
        if m:
            out.append(Issue(field, "time_word", _detail(m.group(0))))
    for phrase in misquote_hits(clean):
        out.append(Issue(field, "misquote", _detail(phrase)))

    sents = [(s, compliance.fold(s)) for s in compliance.sentences(clean)]
    for hit in _trade_hits(sents, subjects):
        out.append(Issue(field, "advice_directive", _detail(hit)))
    m = _YOUR_HOLDING_RE.search(folded)
    if m:
        out.append(Issue(field, "advice_directive", _detail(m.group(0))))
    for hit in _quotation_hits(clean, subjects):
        out.append(Issue(field, "quotation", _detail(hit)))
    for hit in _authorship_hits(sents):
        out.append(Issue(field, "authorship_reveal", _detail(hit)))

    scanned = compliance.scan_text(field, clean, strict_instruments=False)
    for v in scanned:
        if v.code in ENFORCED_COMPLIANCE_CODES:
            out.append(Issue(field, v.code, _detail(v.detail)))
    # Only the VALUATION row binds a soft word to the company ("costco are expensive"); the
    # evaluative row fires on any adjective near a company ("Microsoft could afford a long,
    # expensive transition") and is not a verdict.
    valuation = [compliance.fold(v.detail) for v in scanned if v.code == "class_b_valuation"]
    for hit in _valuation_hits(sents, subjects, valuation):
        out.append(Issue(field, "valuation_verdict", _detail(hit)))
    if grounding_ctx is not None:
        titled = caps_label and clean.isupper()
        gtext = clean.title() if titled else clean
        names = _name_number_tokens(subjects)
        named = set()
        for v in grounding.check_grounding(field, gtext, grounding_ctx):
            if v.code == "ungrounded_entity" and _everyday_word(v.detail, gtext, heading,
                                                                letter_plurals=not titled):
                continue
            token = _name_number_detail(v, names)
            if token is None:
                out.append(Issue(field, v.code, _detail(v.detail)))
            elif token not in named:
                named.add(token)
                if _reads_as_an_amount(token, gtext):
                    out.append(Issue(field, "subject_name_number", _detail(v.detail)))
    return out


#: Grounding codes that read a digit as a quantity.
_NUMBER_CODES = frozenset({"number_context", "ungrounded_number", "ungrounded_name_number"})
#: A multiple ("10x") reads as a quantity wherever it stands.
_MULTIPLE_RE = re.compile(r"\d+(?:\.\d+)?x")


def _name_number_tokens(subjects: Any) -> FrozenSet[str]:
    """The subject-name tokens a name exemption can apply to: letters AND digits in one token
    ("3m", "8x8"). A bare numeral ("800" of 1-800-Flowers, "1847") or a multiple ("10x") is a
    quantity wherever it stands, so it is never exempt (such a piece is refused, fail closed)."""
    toks = subjects if isinstance(subjects, frozenset) else subject_tokens(subjects)
    return frozenset(t for t in toks if any(c.isdigit() for c in t) and any(c.isalpha() for c in t)
                     and not _MULTIPLE_RE.fullmatch(t))


def _name_number_detail(v: Any, names: FrozenSet[str]) -> Optional[str]:
    """The subject-name token a grounding number violation is about, or None."""
    if not names or getattr(v, "code", None) not in _NUMBER_CODES:
        return None
    detail = compliance.fold(str(getattr(v, "detail", "") or "")).strip(" .,;:!?'\"$€£¥")
    return detail if detail in names else None


# Where an occurrence of a digit-bearing subject name ("3M") stands decides what it is. It is the
# company everywhere EXCEPT in a shape only an amount takes. A first rule that exempted only
# name positions refused "Origins of 3M", "the 3M model" and about a third of honest 3M prose,
# and failed the live outline (review round 2, 2026-10-23); a count-noun-only rule let "3M in
# fines" and "USD 3M" through (round 1). Known residual: a bare count ("3M served 3M customers")
# reads as the name — the prompt forbids K/M/B abbreviations and the owner reviews every piece.
# A currency symbol or code, never the word "dollar": "every dollar 3M invests" is honest, and
# "3M dollars" is one number grounding refuses on its own (review round 4).
_CURRENCY_BEFORE_RE = re.compile(r"(?:[$€£¥]\s*|\b(?:usd|us\$|eur|gbp|cad|aud|jpy)\s+)\Z",
                                 re.IGNORECASE)
_POSSESSIVE_AFTER_RE = re.compile(r"['’]s\b", re.IGNORECASE)
_POSSESSIVE_WORTH_RE = re.compile(r"['’]s\s+worth\s+of\b", re.IGNORECASE)
# A hedge may stand between an amount word and the figure ("worth about 3M").
_HEDGE = (r"(?:(?:about|around|over|under|up\s+to|some|just\s+(?:over|under)|roughly|nearly|"
          r"almost|approximately)\s+)?")
_CHANGE_VERBS = (r"ris(?:e[sn]?|ing)|rose|gr(?:ow[ns]?|ew|owing)|increas(?:e[sd]?|ing)|"
                 r"decreas(?:e[sd]?|ing)|declin(?:e[sd]?|ing)|jump(?:s|ed|ing)?|climb(?:s|ed|ing)?|"
                 r"drop(?:s|ped|ping)?|surg(?:e[sd]?|ing)|soar(?:s|ed|ing)?|slip(?:s|ped|ping)?|"
                 r"shr(?:ank|unk|inks?|inking)")
# Only words that do not also take a COMPANY as their object: "a book about 3M", "brands under
# 3M", "the task fell to 3M", "Post-it earned 3M a reputation" are honest (review round 3).
_AMOUNT_BEFORE_RE = re.compile(
    r"\b(?:roughly|nearly|almost|approximately|circa|exactly|an\s+estimated|"
    r"(?:more|less|fewer|greater)\s+than|at\s+(?:least|most)|total(?:s|ed|led|ing|ling)?(?:\s+of)?|"
    r"(?:valued|priced|estimated)\s+at|worth|spen(?:t|ds?|ding)|borrow(?:ed|s|ing)?|"
    r"repa(?:id|ys|ying)|averag(?:e[sd]?|ing)|"
    r"(?:" + _CHANGE_VERBS + r")\s+(?:by|to|from)|(?:fell|falls?|falling)\s+(?:by|from))"
    r"\s+" + _HEDGE + r"\Z", re.IGNORECASE)
# The company, not an amount, after those words: a passive agent ("was dropped by 3M") and a
# source with a noun after it ("grew from 3M research").
_PASSIVE_BY_RE = re.compile(r"\b(?:was|were|is|are|been|be|being)\s+(?:\w+\s+)?\w+\s+by\s+\Z",
                            re.IGNORECASE)
_SOURCE_FROM_RE = re.compile(r"\bfrom\s+\Z", re.IGNORECASE)
_LOWER_WORD_AFTER_RE = re.compile(r"\s+[a-z]")
# Money nouns that never name a business line ("goods", "products", "orders" do: "competes with
# 3M in consumer products"), and the money adjectives that may stand between ("3M in annual
# revenue", "3M in share repurchases") — never a free word ("partnered with 3M in data center
# sales").
_MONEY_NOUNS = (r"revenues?|sales|cash|fines?|fees?|penalt(?:y|ies)|profits?|earnings|income|"
                r"tax(?:es)?|royalt(?:y|ies)|debt|loans?|savings|costs?|expenses?|spending|funding|"
                r"investments?|dividends|buybacks|repurchases|proceeds|damages|settlements?|capital|"
                r"losses|value|payments?|payouts?|charges?|liabilities|wages|bonuses|write-?downs")
_MONEY_ADJECTIVES = (r"annual|new|total|net|gross|quarterly|yearly|additional|extra|unpaid|back|"
                     r"operating|free|cash|legal|share|stock|dividend|impairment|interest|research|"
                     r"capital|tax")
_UNIT_NOUNS = (r"units?|rolls?|tons?|tonnes?|barrels?|masks?|respirators?|items?|pieces?|"
               r"sheets?|pounds?|gallons?|liters?|litres?|miles?|meters?|metres?")
_HYPHEN = "[-\u2010\u2011\u2012\u2013]"
# "a/per" only: "Hospitals used 3M respirators every day" is honest (review round 4).
_AMOUNT_AFTER_RE = re.compile(
    r"(?:\s*%|" + _HYPHEN + r"(?:dollars?|units?|tons?|tonnes?|barrels?|people|strong|plus|square)\b|"
    r"\s+(?:worth(?!\s+(?:it|a\s+(?:closer\s+|second\s+|quick\s+)?(?:look|read)|the\s+\w+|"
    r"[a-z]+ing)\b)|percent|per|annually|yearly|times|x)\b|"
    r"\s+(?:in|of)\s+(?:(?:" + _MONEY_ADJECTIVES + r")\s+){0,2}(?:" + _MONEY_NOUNS + r")\b|"
    r"\s+(?:" + _UNIT_NOUNS + r")\s+(?:a|per)\s+(?:day|week|month|year|quarter)\b)",
    re.IGNORECASE)


def _reads_as_an_amount(token: str, text: str) -> bool:
    """Some occurrence of the subject-name `token` in `text` reads as a quantity: lower case
    ("3m" is a length), a currency before it ("$3M", "USD 3M"), a quantity or change word before
    it ("nearly 3M", "grew by 3M", "spends 3M", "worth about 3M", "a total of 3M"; never a
    passive agent "dropped by 3M" or a source "grew from 3M research"), or a measure after it
    ("3M%", "3M in fines", "3M in share repurchases", "3M worth of tape", "3M-dollar", "3M masks a
    day"). A possessive ("3M's history") is the name unless a currency precedes it or "worth of"
    follows it. Fail
    closed: no occurrence found = an amount (the violation named a token the text does not
    show)."""
    hits = list(re.finditer(rf"(?<!\w){re.escape(token)}(?!\w)", text or "", re.IGNORECASE))
    if not hits:
        return True
    for m in hits:
        if not any(c.isupper() for c in m.group(0)):
            return True
        before, after = text[max(0, m.start() - 40):m.start()], text[m.end():m.end() + 60]
        if _CURRENCY_BEFORE_RE.search(before) or _POSSESSIVE_WORTH_RE.match(after):
            return True
        if _POSSESSIVE_AFTER_RE.match(after):
            continue
        amount_word = _AMOUNT_BEFORE_RE.search(before) is not None
        if amount_word and (_PASSIVE_BY_RE.search(before) or (
                _SOURCE_FROM_RE.search(before) and _LOWER_WORD_AFTER_RE.match(after))):
            amount_word = False
        if amount_word or _AMOUNT_AFTER_RE.match(after):
            return True
    return False


# ── blocks ───────────────────────────────────────────────────────────────────────────────────


def _block_texts(block: Mapping[str, Any]) -> List[Tuple[str, Any, int]]:
    """(sub-field, value, cap) for every string a block displays or narrates."""
    out: List[Tuple[str, Any, int]] = []
    if "text" in block:
        out.append(("", block.get("text"), BLOCK_TEXT_CAP))
    items = block.get("items")
    if isinstance(items, list):
        for k, item in enumerate(items[:C.MAX_BULLET_ITEMS + 1]):
            out.append((f".items[{k}]", item, ITEM_TEXT_CAP))
    return out


def _validate_block(field: str, block: Any, section_kind: Any,
                    grounding_ctx: Optional[grounding.GroundingContext],
                    subjects: FrozenSet[str]) -> List[Issue]:
    out: List[Issue] = []
    if not isinstance(block, Mapping):
        out.append(Issue(field, "block_invalid", f"block is {type(block).__name__}, not an object"))
        return out
    if section_kind not in C.SECTION_KINDS:
        # Read as a non-peg section (the strictest: no time words) and say so.
        out.append(Issue(field, "section_kind", _detail(f"unknown section kind {section_kind!r}")))
    btype = block.get("type")

    if btype not in C.BLOCK_TYPES:
        why = ("quote blocks are not allowed in weekly pieces" if btype == "quote"
               else f"unknown block type {btype!r}")
        out.append(Issue(field, "block_type", _detail(why)))
    else:
        extra = sorted(str(k) for k in block if k not in _BLOCK_KEYS[btype])
        if extra:
            out.append(Issue(field, "block_invalid", _detail(f"unexpected keys {extra}")))
        if btype == "bulletList":
            items = block.get("items")
            if not isinstance(items, list) or not items:
                out.append(Issue(field, "block_invalid", "bulletList needs a non-empty items list"))
            elif len(items) > C.MAX_BULLET_ITEMS:
                out.append(Issue(field, "word_limit",
                                 f"{len(items)} items > {C.MAX_BULLET_ITEMS}"))
            for k, item in enumerate(items[:C.MAX_BULLET_ITEMS + 1]
                                     if isinstance(items, list) else []):
                n = word_count(item)
                if n > C.MAX_BULLET_WORDS:
                    out.append(Issue(f"{field}.items[{k}]", "word_limit",
                                     f"{n} words > {C.MAX_BULLET_WORDS}"))
        else:
            if not isinstance(block.get("text"), str):
                out.append(Issue(field, "block_invalid", f"{btype} needs a string text"))
            else:
                n = word_count(block["text"])
                if n > _WORD_LIMITS[btype]:
                    out.append(Issue(field, "word_limit", f"{n} words > {_WORD_LIMITS[btype]}"))
        if btype == "callout":
            if block.get("style") not in C.CALLOUT_STYLES:
                out.append(Issue(field, "callout_style", _detail(
                    f"style {block.get('style')!r} not in {C.CALLOUT_STYLES}")))
            icon = block.get("icon")
            if not isinstance(icon, str) or icon not in rules.ICON_ALLOWLIST:
                out.append(Issue(field, "icon", _detail(f"icon {icon!r} not allowed")))

    texts = _block_texts(block)
    # A time word is readable in the archive only beside the date it means — in the SAME block.
    joined = " ".join(compliance.fold(compliance.clean(v[:cap])) for _s, v, cap in texts
                      if isinstance(v, str))
    time_words_ok = section_kind == "peg" and _has_full_date(joined)
    for sub, value, cap in texts:
        out += _text_issues(field + sub, value, cap=cap, time_words_ok=time_words_ok,
                            grounding_ctx=grounding_ctx, subjects=subjects)
    return out


def validate_block(field: str, block: Any, *, section_kind: str,
                   grounding_ctx: Optional[grounding.GroundingContext] = None,
                   subjects: Iterable[str] = ()) -> List[Issue]:
    """Every issue in one content block (empty = clean). Block types are `contracts.BLOCK_TYPES`
    only (a `quote` is refused), with the contracts' word limits, callout styles and the icon
    allow-list, then the text rules (module doc), then grounding when `grounding_ctx` is given
    (`build_grounding_context`). `section_kind` is the draft section's kind; only `peg` allows a
    dated time word. `subjects` (the fact sheet's subject names and tickers) lets the conduct rule
    see a company no lexicon knows. Pure; never raises."""
    try:
        return _dedupe(_validate_block(str(field), block, section_kind, grounding_ctx,
                                       subject_tokens(subjects)))
    except Exception as e:  # noqa: BLE001 — documented total; fail closed, loudly
        logger.exception("learn weekly validators: validate_block failed on field=%s (%s: %s)",
                         field, type(e).__name__, e)
        return [Issue(str(field), "validator_error", type(e).__name__)]


# ── articles ─────────────────────────────────────────────────────────────────────────────────


#: The longest paragraph read as a list's lead-in (one introducing sentence).
LEAD_IN_MAX_WORDS = 30


def block_key(text: Any) -> str:
    """The repetition key of a block's text: `title_key` without its closing punctuation, so the
    same sentence ending in "." and in ":" (an intro paragraph and its list's lead-in) is one."""
    return title_key(text).rstrip(" .:;!?") if isinstance(text, str) else ""


def is_list_lead_in(blocks: Any, index: int) -> bool:
    """`blocks[index]` is a list's lead-in: a short paragraph right before a bulletList (the shape
    `article_schema.section_blocks` makes from a list's text)."""
    try:
        block, nxt = blocks[index], blocks[index + 1]
    except (IndexError, TypeError, KeyError):
        return False
    return (isinstance(block, Mapping) and isinstance(nxt, Mapping)
            and block.get("type") == "paragraph" and nxt.get("type") == "bulletList"
            and 0 < word_count(block.get("text")) <= LEAD_IN_MAX_WORDS)


def title_key(title: Any) -> str:
    """The uniqueness key of a title: cleaned and folded (case, accents, quotes, whitespace), at
    least as strict as the `lower(title)` unique index and iOS's title-keyed cards."""
    if not isinstance(title, str):
        return ""
    return compliance.fold(compliance.clean(title[:TITLE_CAP * 2])).strip(" .!?")


def _strings(values: Any, label: str) -> FrozenSet[str]:
    """The strings in a caller-supplied collection, read defensively: None is empty, a bare
    string is ONE value (it would otherwise iterate as characters), non-strings are skipped, a
    non-iterable is logged and read as empty, and at most `MAX_TITLES_COMPARED` are read."""
    if values is None:
        return frozenset()
    if isinstance(values, str):
        values = (values,)
    out = set()
    try:
        for i, v in enumerate(values):
            if i >= MAX_TITLES_COMPARED:
                logger.warning("learn weekly validators: %s has more than %d entries; the rest "
                               "were not compared", label, MAX_TITLES_COMPARED)
                break
            if isinstance(v, str):
                out.add(v)
    except TypeError:
        logger.error("learn weekly validators: %s is %s, not an iterable of strings",
                     label, type(values).__name__)
    return frozenset(out)


def _title_keys(titles: Any, label: str) -> FrozenSet[str]:
    return frozenset(k for k in (title_key(t) for t in _strings(titles, label)) if k)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_article(content: Any, kind: Any, sheet: Any, served_titles: Any,
                      in_flight_titles: Any, served_slugs: Any) -> List[Issue]:
    out: List[Issue] = []
    if not isinstance(content, Mapping):
        return [Issue("article", "article_invalid",
                      f"article is {type(content).__name__}, not an object")]
    if kind not in C.KINDS:
        out.append(Issue("article", "kind", _detail(f"unknown piece kind {kind!r}")))

    ctx: Optional[grounding.GroundingContext] = None
    if sheet is not None:
        if not isinstance(sheet, C.FactSheet):
            out.append(Issue("article", "sheet_invalid",
                             f"sheet is {type(sheet).__name__}, not a FactSheet"))
        ctx = build_grounding_context(sheet)       # empty (fail-closed) for a bad sheet
    # The piece's own subjects (names + tickers): the conduct rule's company list for a new listing
    # no lexicon knows ("CoreWeave concealed…").
    subj_names: Tuple[str, ...] = ()
    if isinstance(sheet, C.FactSheet):
        subj_names = tuple(x for s in sheet.subjects[:_MAX_SUBJECTS] for x in (s.name, s.ticker)
                           if isinstance(x, str))
    subj = subject_tokens(subj_names)

    for key in REQUIRED_ARTICLE_KEYS:
        if key not in content:
            out.append(Issue(key, "missing_key", f"{key} is required by the iOS article shape"))

    # slug
    slug = content.get("slug")
    if "slug" in content:
        if not isinstance(slug, str) or not slug:
            out.append(Issue("slug", "slug_pattern", "slug must be a non-empty string"))
        else:
            s = _capped("slug", slug, SLUG_CAP, out)
            m = SLUG_RE.fullmatch(s)
            if m is None:
                out.append(Issue("slug", "slug_pattern",
                                 _detail(f"{s!r} is not <kebab-title>-<yyyy-mm-dd>")))
            else:
                try:
                    date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    out.append(Issue("slug", "slug_pattern",
                                     _detail(f"{s!r} ends in no real date")))
            if slug in _strings(served_slugs, "served_slugs"):
                out.append(Issue("slug", "slug_collision", _detail(slug)))

    # header strings (never a peg: no time words)
    for key, cap in (("title", TITLE_CAP), ("subtitle", SHORT_TEXT_CAP),
                     ("cardSubtitle", SHORT_TEXT_CAP)):
        if key in content:
            out += _text_issues(key, content.get(key), cap=cap, time_words_ok=False,
                                grounding_ctx=ctx, subjects=subj, heading=key == "title")
    if content.get("tagLabel") is not None:
        out += _text_issues("tagLabel", content.get("tagLabel"), cap=TITLE_CAP,
                            time_words_ok=False, grounding_ctx=ctx, subjects=subj,
                            caps_label=True, heading=True)
        tag = content.get("tagLabel")
        hit = GENERIC_TAG_RE.search(tag[:TITLE_CAP]) if isinstance(tag, str) else None
        if hit:
            out.append(Issue("tagLabel", "tag_label_generic", _detail(
                f"{hit.group(0)!r} repeats the app's own \"Featured Deep Dive\" header")))

    if "category" in content and content.get("category") not in C.CATEGORIES:
        out.append(Issue("category", "bad_value",
                         _detail(f"{content.get('category')!r} not in {C.CATEGORIES}")))

    # author — code-owned, so its own words ("Caydex Research") are never text-scanned
    author = content.get("author")
    if "author" in content:
        if not isinstance(author, Mapping):
            out.append(Issue("author", "bad_value", "author must be an object"))
        else:
            for sub in ("name", "title"):
                if not isinstance(author.get(sub), str) or not author.get(sub, "").strip():
                    out.append(Issue(f"author.{sub}", "bad_value", f"author.{sub} is required"))
            if author.get("isVerified") is not False:
                out.append(Issue("author.isVerified", "verified",
                                 _detail(f"must be false, got {author.get('isVerified')!r}")))
            # Its own check (not an elif): a stale byline is named even when isVerified is wrong too.
            if {k: v for k, v in author.items() if k != "isVerified"} != \
                    {k: v for k, v in C.AUTHOR.items() if k != "isVerified"}:
                out.append(Issue("author", "author_mismatch",
                                 _detail(f"{dict(author)!r} is not the code-owned author")))

    rt = content.get("readTimeMinutes")
    if "readTimeMinutes" in content and not (_is_int(rt) and 1 <= rt <= MAX_READ_TIME_MINUTES):
        out.append(Issue("readTimeMinutes", "bad_value",
                         _detail(f"{rt!r} is not a whole number of minutes in "
                                 f"1..{MAX_READ_TIME_MINUTES}")))

    for key in ENGAGEMENT_KEYS:
        if key in content and content.get(key) != "":
            out.append(Issue(key, "engagement_count", _detail(
                f"{key} must be \"\" (never a count), got {content.get(key)!r}")))

    colors = content.get("heroGradientColors")
    if "heroGradientColors" in content and not (
            isinstance(colors, list) and len(colors) == 3
            and all(isinstance(c, str) and _HEX6_RE.match(c) for c in colors)):
        out.append(Issue("heroGradientColors", "bad_value",
                         _detail(f"need 3 hex colours without '#', got {colors!r}")))

    # keyHighlights
    highlights = content.get("keyHighlights")
    if "keyHighlights" in content:
        if not isinstance(highlights, list) or len(highlights) != 3:
            n = len(highlights) if isinstance(highlights, list) else type(highlights).__name__
            out.append(Issue("keyHighlights", "bad_value", f"need exactly 3, got {n}"))
        seen_h = set()
        for k, h in enumerate(highlights[:4] if isinstance(highlights, list) else []):
            f = f"keyHighlights[{k}]"
            if not isinstance(h, Mapping):
                out.append(Issue(f, "bad_value", "a key highlight must be an object"))
                continue
            icon = h.get("icon")
            if not isinstance(icon, str) or icon not in rules.ICON_ALLOWLIST:
                out.append(Issue(f"{f}.icon", "icon", _detail(f"icon {icon!r} not allowed")))
            out += _text_issues(f"{f}.title", h.get("title"), cap=TITLE_CAP,
                                time_words_ok=False, grounding_ctx=ctx, subjects=subj,
                                heading=True)
            out += _text_issues(f"{f}.description", h.get("description"), cap=SHORT_TEXT_CAP,
                                time_words_ok=False, grounding_ctx=ctx, subjects=subj)
            hk = title_key(h.get("title"))
            if hk and hk in seen_h:
                out.append(Issue(f"{f}.title", "duplicate", _detail(str(h.get("title")))))
            seen_h.add(hk)

    # statistics (optional; code renders their values from fact ids, so no grounding here)
    stats = content.get("statistics")
    if stats is not None:
        if not isinstance(stats, list) or len(stats) > MAX_STATISTICS:
            out.append(Issue("statistics", "bad_value",
                             f"need a list of at most {MAX_STATISTICS} statistics"))
        for k, st in enumerate(stats[:MAX_STATISTICS] if isinstance(stats, list) else []):
            if not isinstance(st, Mapping):
                out.append(Issue(f"statistics[{k}]", "bad_value", "a statistic must be an object"))
                continue
            for sub in ("label", "value"):
                out += _text_issues(f"statistics[{k}].{sub}", st.get(sub), cap=SHORT_TEXT_CAP,
                                    time_words_ok=False, grounding_ctx=None)

    # sections + blocks + word counts
    sections = content.get("sections")
    total_words = 0
    peg_words = 0
    if "sections" in content:
        if not isinstance(sections, list) or not sections:
            out.append(Issue("sections", "bad_value", "sections must be a non-empty list"))
            sections = []
        elif len(sections) > MAX_SECTIONS:
            out.append(Issue("sections", "bad_value", f"{len(sections)} sections > {MAX_SECTIONS}"))
        seen_titles = set()
        seen_blocks = set()
        for i, sec in enumerate(sections[:MAX_SECTIONS]):
            f = f"sections[{i}]"
            if not isinstance(sec, Mapping):
                out.append(Issue(f, "bad_value", "a section must be an object"))
                continue
            sk = sec.get("kind")
            if sk not in C.SECTION_KINDS:
                out.append(Issue(f"{f}.kind", "section_kind",
                                 _detail(f"draft section kind {sk!r} not in {C.SECTION_KINDS}")))
            out += _text_issues(f"{f}.title", sec.get("title"), cap=TITLE_CAP,
                                time_words_ok=False, grounding_ctx=ctx, subjects=subj,
                                heading=True)
            tk = title_key(sec.get("title"))
            if tk and tk in seen_titles:
                out.append(Issue(f"{f}.title", "duplicate", _detail(str(sec.get("title")))))
            seen_titles.add(tk)
            icon = sec.get("icon")
            if icon is not None and (not isinstance(icon, str) or icon not in rules.ICON_ALLOWLIST):
                out.append(Issue(f"{f}.icon", "icon", _detail(f"icon {icon!r} not allowed")))
            blocks = sec.get("content")
            if not isinstance(blocks, list) or not blocks:
                out.append(Issue(f"{f}.content", "bad_value", "content must be a non-empty list"))
                continue
            if len(blocks) > MAX_BLOCKS_PER_SECTION:
                out.append(Issue(f"{f}.content", "bad_value",
                                 f"{len(blocks)} blocks > {MAX_BLOCKS_PER_SECTION}"))
            section_seen = set()
            for j, block in enumerate(blocks[:MAX_BLOCKS_PER_SECTION]):
                bf = f"{f}.content[{j}]"
                out += validate_block(bf, block, section_kind=sk if sk in C.SECTION_KINDS
                                      else "history", grounding_ctx=ctx, subjects=subj_names)
                if not isinstance(block, Mapping):
                    continue
                words = 0
                lead_in = is_list_lead_in(blocks, j)
                for _sub, value, _cap in _block_texts(block):
                    words += word_count(value)
                    key = block_key(value)
                    # A repeated paragraph is the shape of a model repetition loop, inside a
                    # section or across them. A list's lead-in ("Key factors include:") recurring
                    # in ANOTHER section is not (each section is written blind to the others), so
                    # a lead-in is compared within its own section only.
                    if key and len(key.split()) >= 4:
                        if key in section_seen or (not lead_in and key in seen_blocks):
                            out.append(Issue(bf, "duplicate", _detail(str(value))))
                        section_seen.add(key)
                        if not lead_in:
                            seen_blocks.add(key)
                total_words += words
                if sk == "peg":
                    peg_words += words

    bounds = _WORD_BOUNDS.get(kind) if isinstance(kind, str) else None
    if bounds is not None and "sections" in content:
        lo, hi = bounds
        if not lo <= total_words <= hi:
            out.append(Issue("sections", "word_count",
                             f"{total_words} body words outside {lo}-{hi} for {kind}"))
    if total_words and peg_words / total_words > C.MAX_PEG_SHARE:
        out.append(Issue("sections", "peg_share",
                         f"peg is {peg_words}/{total_words} words "
                         f"({peg_words / total_words:.0%}) > {C.MAX_PEG_SHARE:.0%}"))

    # uniqueness against what is served AND every other in-flight piece
    tkey = title_key(content.get("title"))
    if tkey:
        if tkey in _title_keys(served_titles, "served_titles"):
            out.append(Issue("title", "title_collision",
                             _detail(f"{content.get('title')!r} is already served")))
        if tkey in _title_keys(in_flight_titles, "in_flight_titles"):
            out.append(Issue("title", "title_collision", _detail(
                f"{content.get('title')!r} is another in-flight piece's title")))
    return out


def validate_article(content: Any, *, kind: str, sheet: Optional[C.FactSheet] = None,
                     served_titles: Optional[Iterable[str]],
                     in_flight_titles: Optional[Iterable[str]],
                     served_slugs: Optional[Iterable[str]] = ()) -> List[Issue]:
    """Every issue in one DRAFT article (the iOS content shape plus each section's draft `kind`,
    which the renderer drops). Checks the iOS-required keys, the slug pattern, the category enum,
    the code-owned author (`isVerified` false, equal to `contracts.AUTHOR`), no fabricated engagement
    count, the gradient and the three key highlights, every section and block
    (`validate_block`, grounded against `sheet` when given), the body word count for `kind`
    (`contracts.DEEP_DIVE_WORDS` / `SLOT_WORDS`; body = paragraph, subheading, callout and bullet
    words — the closing disclosure callout is appended AFTER validation), the peg share
    (≤ `contracts.MAX_PEG_SHARE`), and title uniqueness (case/accent-folded) against the served
    titles AND every other in-flight piece (pass the OTHER pieces' titles — the piece's own row
    would collide with itself). `served_slugs` adds a slug-uniqueness check. Pure; never raises."""
    try:
        return _dedupe(_validate_article(content, kind, sheet, served_titles, in_flight_titles,
                                         served_slugs))
    except Exception as e:  # noqa: BLE001 — documented total; fail closed, loudly
        slug = content.get("slug") if isinstance(content, Mapping) else None
        logger.exception("learn weekly validators: validate_article failed on slug=%r kind=%r "
                         "(%s: %s)", slug, kind, type(e).__name__, e)
        return [Issue("article", "validator_error", type(e).__name__)]


__all__ = [
    "ENFORCED_COMPLIANCE_CODES", "ENGAGEMENT_KEYS", "GENERIC_TAG_RE", "GROUNDING_CODES",
    "IGNORED_COMPLIANCE_CODES", "Issue", "REQUIRED_ARTICLE_KEYS", "SLUG_RE", "advice_hits",
    "build_grounding_context", "conduct_hits", "corpus_vocab", "forbidden_token_hits",
    "misquote_hits", "self_reference_hits", "title_key", "validate_article", "validate_block",
    "word_count",
]
