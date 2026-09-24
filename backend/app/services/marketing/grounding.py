"""
Grounding: every number and every proper noun in a draft must come from its fact sheet
(SYSTEM_DESIGN_GUIDELINES §12.5).

The writer is shown ONE Learn item's cleaned text (`content_pool.py`). Nothing in the public post
may add to it: a hallucinated figure is a false financial claim under the brand, and a
hallucinated company or ticker turns an educational post into commentary about an instrument the
source never discussed. The checks, all pure:

* **Numbers** — each digit number in the draft must equal a fact-sheet number by value AND unit
  class (percent / currency / foreign currency / multiple / fraction are strict; plain and year
  interchange), and the words AROUND it must share an ANCHOR with the source number's sentence
  (or the draft sentence with the words around the source number). Subjects do not count — the
  sheet's proper nouns and the companies a draft names — nor do hedges ("roughly"): "Apple"
  anchored everything in the Apple case study. The anchor stops misattribution: the source's
  "~60% of operating profit from AWS" cannot reappear as "the stock rose 60%". An amount,
  percentage or multiple stated AS a price, value or return claim ("was worth $6.9 billion",
  "Apple climbed 70%") must be one the sheet states as a claim; a loss cannot come back as a
  profit; and a bare scale word ("a trillion-dollar company", "worth billions") needs its scale
  in the sheet. Unit-less integers 0-5 not followed by a unit are exempt ("3 lessons"); "up
  5%" and "5-fold" are not. Round 2 (over-block review) lets an honest restatement with the
  writer's OWN verb through without reopening those refusals (`_anchored`): a YEAR binds on any
  word the two sentences share, names included, unless its own verb is another event
  ("founded" ≠ "shipped"); unit-scoped verb classes ("acquired" for "Paid for", "established"
  for "Founded", "members" for "subscribers"); a rate's period ("a year") is no anchor; a
  names-only source row meets the draft's names but never its subject; the sheet's spoken
  durations and prices ("a decade", "three-twenty") ground on the SOURCE side only; and a
  designation the sheet writes ("737 MAX") grounds itself.
* **Entities** — one rule for every token with an uppercase letter, wherever it sits (sentence
  starts are NOT exempt — "Jassy doubled down" must not pass because it opens a line): allowed
  if its lowercase form is ordinary vocabulary (it occurs lowercased somewhere in the corpus, or
  in the function/template word lists), otherwise it must occur in THIS item's fact sheet.
  ALL-CAPS tokens are possible tickers: acronym allowlist or the fact sheet, nothing else.
  A company (`compliance.sentence_company_mentions`) must be one the sheet names, even when its
  name is an English word ("Apple", "Target", "Coke") or written in lower case.
  Two narrowings of "ordinary": a CamelCase token ("PayPal") is never ordinary English, and the
  surname of anyone on the App Store "Do not use" list ("Graham", "Lynch", "Wood", "Marks") is
  a person when capitalised, even though web2 lists it as a word — unless this fact sheet itself
  uses the word in lower case ("wood prices").
* **First name + name** — a given name (`compliance.given_names`) in front of one of this sheet's
  proper nouns, a non-word or an initial ("Walt Disney", "Henry Ford", "Jeff B.") is a founder the
  writer added unless the sheet states that exact pair ("Louis Vuitton" in the LVMH case study).
* **Accents** — entities are read on `compliance.skeleton()` of both the draft and the sheet, so
  "Büffett" is "Buffett"; and a word with a non-ASCII letter must be one the sheet itself uses
  ("Moët", "décor") — a fail-closed backstop under the skeleton.

Totality: `check_grounding` caps its input (`GROUNDING_CAP`, reported as `too_long`) before any
regex runs, and never raises — an unexpected error is logged and returned as a violation, so a
bug here rejects a draft instead of publishing it unchecked.
"""

from __future__ import annotations

import gzip
import logging
import math
import re
from bisect import bisect_left
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, Iterable, List, Optional, Sequence, Tuple

from app.services.marketing.compliance import (
    APP_STORE_NAMES,
    COMPANY_MARK,
    MISSPELLINGS,
    NOUN_GIVEN_NAMES,
    TITLE_WORDS,
    Violation,
    _FUNCTION_WORDS as _COMPLIANCE_FUNCTION_WORDS,
    _title_cased,
    company_view,
    denied_given_names,
    fold,
    given_names,
    sentence_company_mentions,
    sentences,
    skeleton,
)
from app.services.marketing.numbers import (
    CURRENCY,
    FOREIGN_CURRENCY,
    MULTIPLE,
    NAME_NUMBER_RE,
    PERCENT,
    PLAIN,
    YEAR,
    NumberMention,
    extract_numbers,
    same_number,
    words_to_digits,
)

logger = logging.getLogger(__name__)

#: Acronyms that are finance/general vocabulary, never a ticker claim on their own. A REGULATOR
#: is deliberately absent ("SEC", "FINRA"): "Endorsed by the SEC" is a claim about an institution,
#: so the acronym must come from the item's own fact sheet like any other name.
ACRONYMS = frozenset({
    "AI", "CEO", "CFO", "COO", "CTO", "ETF", "ETFS", "IPO", "IPOS", "GDP", "EPS", "ROE", "ROI",
    "ROA", "ROIC", "FCF", "EBIT", "EBITDA", "US", "USA", "UK", "EU", "FAQ", "TV", "PC",
    "PCS", "CPU", "CPUS", "GPU", "GPUS", "API", "APIS", "OK", "DIY", "FOMO", "YOLO", "R&D",
    "M&A", "S&P", "NYSE", "DCF", "IRA", "IRAS", "CD", "CDS", "ATM", "B2B", "B2C", "Q1", "Q2",
    "Q3", "Q4", "H1", "H2", "AM", "PM", "TL", "DR", "TLDR", "VS", "NO", "YES",
})

#: Name-numbers that are generic finance vocabulary.
GLOBAL_NAME_NUMBERS = frozenset({"s&p 500", "s&p500", "401(k)", "401 (k)", "13f", "10-k", "10-q", "24/7"})

#: Words the templates and ordinary headline style use that a 22k-word corpus may not contain
#: in lower case. Title-case versions of these must never be read as proper nouns.
TEMPLATE_WORDS = frozenset("""
myth myths fact facts takeaway takeaways checklist lesson lessons question questions answer
answers step steps rule rules sign signs mistake mistakes idea ideas truth truths reality
quick guide basics explained why how what when where who which true false tip tips story
case study playbook breakdown recap summary key point points thing things way ways part
""".split())

#: Nationality/region adjectives: capitalised by grammar, never a claim about an entity.
DEMONYMS = frozenset("""
american americans european europeans chinese japanese british french german germans asian
korean taiwanese indian canadian western eastern northern southern global african latin
""".split())

MONTHS_DAYS = frozenset("""
january february march april may june july august september october november december
monday tuesday wednesday thursday friday saturday sunday
""".split())

FUNCTION_WORDS = frozenset("""
a an the and or but nor so yet for of in on at to from by with without within into onto over
under about above below after before between through during until while since because though
although if then than that this these those there here it its it's they them their theirs
we us our you your he him his she her hers who whom whose which what when where why how all
any both each every few many more most much none no not only other some such same own very
can could may might must shall should will would do does did done be is are was were been
being have has had having get gets got just also even still again ever never always often
once twice first second third last next new old big small great good bad best worst better
worse high low long short one two three four five six seven eight nine ten i yes okay
""".split())

#: Anchors ignore function words and UNIT words. "company", "market", "years" stay — they are
#: often exactly what ties a number to its meaning ("500 companies"). A unit word never does: it
#: says what KIND of number it is, which `same_number` already checks, not what it measures. As
#: an anchor it let "the stock rose 90 percent" ground against "renewal rates stayed near 90
#: percent" while the identical claim written "90%" was rejected.
#: Hedge words say how precise a number is, never what it measures: as the only shared word,
#: "roughly" grounded "Microsoft was worth roughly $7.5 billion" on "Paid for GitHub: ~$7.5B".
_HEDGES = frozenset("""
roughly nearly approximately around almost estimated circa north south least just under over
""".split())
_STOPWORDS = FUNCTION_WORDS | _HEDGES
_UNIT_ANCHORS = frozenset("""
percent percents percentage percentages cent cents point points dollar dollars usd euro euros
pound pounds times thousand thousands million millions billion billions trillion trillions
hundred hundreds dozen dozens
""".split())

#: Words that make a small integer a money/size QUANTITY rather than a count ("3 lessons",
#: "one day", "two sides"). Time and head-count nouns are deliberately absent: "one day" is an
#: idiom, and grounding "two sides" against the fact sheet only produced false rejections.
_UNIT_WORDS = frozenset("""
percent per x times dollar dollars billion million thousand trillion points shares fold bagger
baggers bps basis
""".split())

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'&.-]*[A-Za-z]|[A-Za-z]")
#: Runs of letters in ANY script (the non-ASCII-word backstop).
_LETTERS_RE = re.compile(r"[^\W\d_]+")
_WORD_RE = re.compile(r"[a-z][a-z'&-]*")
#: A word written in lower case in the ORIGINAL (unfolded) text.
_LOWER_WORD_RE = re.compile(r"(?<![A-Za-z])[a-z][a-z'-]*")
_ALNUM_RE = re.compile(r"[a-z0-9]+")
_APOSTROPHE_FOLD = str.maketrans({"\u2019": "'", "\u2018": "'"})

#: Same ceiling as `compliance._SCAN_CAP`: real fields are far shorter, and every regex below
#: runs only over the capped prefix.
GROUNDING_CAP = 6000
#: Violation details are shown back to the model in a repair prompt; a 6,000-digit "number"
#: must not be.
_DETAIL_MAX = 160


def _stem(word: str) -> str:
    w = word.lower().strip("'-.")
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w


_SUFFIXES = ("ingly", "edly", "ings", "ing", "ed", "es", "s", "ly", "er", "ers", "e")


def root(word: str) -> str:
    """Crude inflection-stripping so "Understanding" matches the corpus's "understand" and
    "Fluctuate" its "fluctuates". Applied identically to the vocabulary and to the token, so
    only the SAME root ever matches. Proper nouns are unaffected: "Costco" has no lower-case
    form in the corpus to meet."""
    w = _stem(word)
    for suf in _SUFFIXES:
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


#: Irregular forms of the measure words a number sits next to, on their ROOTS, so an honest
#: paraphrase still meets its source once anchors are local ("Losses ~$40B" / "lost $40
#: billion", "sold" / "sales", "spent" / "spending").
_LEMMA = {
    "lost": "loss", "losing": "loss", "lose": "loss", "sold": "sell", "sale": "sell",
    "spent": "spend", "bought": "buy", "buying": "buy", "buys": "buy", "paid": "pay",
    "paying": "pay", "pays": "pay", "grew": "grow", "growth": "grow", "fell": "fall",
    "rose": "rise", "risen": "rise", "rising": "rise", "made": "make", "making": "make",
    "built": "build", "held": "hold", "kept": "keep", "began": "begin", "begun": "begin",
    "beginn": "begin", "shipp": "ship", "dated": "date", "took": "take", "taken": "take",
}

# ── same-meaning classes, each scoped to ONE unit (round 2: W2-OB-1, W2CB-12) ──
#
# A draft restates a sheet number with its own verb ("Airbus was founded in 1970" for "Airbus
# was created in 1970", "NVIDIA acquired Mellanox for $6.9 billion" for "Paid for Mellanox:
# ~$6.9B"). The irregular `_LEMMA` forms never meet those synonyms, so every such restatement
# was `number_context`. The classes are CLOSED lists of event verbs, and each applies to one unit
# only — measure NOUNS are never merged (sales/profit/revenue stay apart, or "renewal rate"
# re-attaches to anything):
#
# * `_BEGIN_CLASS` — a YEAR: something came into existence (founded, formed, launched, shipped).
# * `_ACQUIRE_CLASS` — an AMOUNT: what was paid for something (bought, acquired, paid, spent).
#   Never next to a buyback word ("NVIDIA bought back $6.9 billion of its own stock" is not an
#   acquisition), and only when the draft names the same thing the sheet's amount sentence
#   names ("Mellanox") — "NVIDIA spent $6.9 billion on research" names nothing the row does.
# * `_PEOPLE_CLASS` — a COUNT of people: subscribers / members / users / customers.
_BEGIN_CLASS = frozenset({
    "found", "form", "creat", "start", "establish", "open", "launch", "releas", "introduc",
    "ship", "debut", "unveil", "begin", "arriv", "incorporat", "born", "emerg", "date",
})
_ACQUIRE_CLASS = frozenset({"buy", "acquir", "acquisition", "purchas", "pay", "spend",
                            # Round 3 (W3OB-10): "GitHub cost Microsoft roughly $7.5 billion",
                            # "The Mellanox deal cost NVIDIA about $6.9 billion", "Tiffany joined in
                            # 2021 for roughly $15.8 billion". Never "part" or "worth": the first is
                            # everywhere, the second is compliance's valuation row.
                            "cost", "deal", "join"})
#: The acquisition verbs that are PHRASES: "picked up", "snapped up", "took over", "became part
#: of". Read on the draft's window text (W3OB-10).
_ACQUIRE_PHRASE_RE = re.compile(
    r"\b(?:picked|pick|picks|picking|snapped|snap|snaps|snapping|scooped|scoop|scoops|took|take|"
    r"takes|taking) (?:up|over)\b|\b(?:became|become|becomes|becoming) (?:a )?part of\b|\b(?:was|were) "
    r"(?:folded|absorbed|brought) into\b")
#: An acquisition amount restated in the PRESENT, a hypothetical or a holder's cost is no longer
#: the deal price: "Mellanox would cost about $6.9 billion today", "GitHub would cost Microsoft
#: $7.5 billion now", "The Mellanox deal cost NVIDIA shareholders about $6.9 billion".
_ACQUIRE_REFUSE_RE = re.compile(
    r"\b(?:would|could|might|today|now|currently|nowadays|these days|worth|shareholders?|"
    r"stockholders?|investors?|owners?)\b")
#: A YEAR's EVENT, where the verb says unambiguously which one (round 2): a draft whose year
#: carries one of these kinds may not bind to a sheet year whose sentence states only ANOTHER
#: kind — "NVIDIA was founded in 2006" is not "In 2006 NVIDIA shipped CUDA", "Meta founded
#: Oculus in 2014" is not "Oculus Acquired: 2014". Ambiguous verbs (start, open, launch, arrive,
#: begin, date) belong to no kind and never conflict: "TSMC was launched in 1987" is its founding.
_YEAR_EVENT_KINDS = (
    ("founded", frozenset({"found", "form", "creat", "establish", "incorporat", "born"})),
    ("shipped", frozenset({"releas", "introduc", "ship", "debut", "unveil"})),
    ("acquired", frozenset({"buy", "acquir", "acquisition", "purchas"})),
    ("renamed", frozenset({"renam", "rebrand"})),
)


def _event_kinds(tokens: FrozenSet[str]) -> FrozenSet[str]:
    return frozenset(kind for kind, words in _YEAR_EVENT_KINDS if tokens & words)


#: A year's own event verb lives in its own bracket / clause: "(CUDA in 2006), created a strong
#: position" — "created" is the main clause's, not the year's.
_EVENT_CUT_RE = re.compile(r"[()\[\];:]|\s[-\u2013\u2014]\s")


def _event_window(text: str, start: int, end: int) -> str:
    """The year's ±`_WINDOW_WORDS` words, cut at the nearest bracket, colon, semicolon or
    spaced dash on each side. Bounded like `_window_text`."""
    left = text[max(0, start - 200):start]
    cut = None
    for mm in _EVENT_CUT_RE.finditer(left):
        cut = mm.end()
    if cut is not None:
        left = left[cut:]
    right = text[end:end + 200]
    mm = _EVENT_CUT_RE.search(right)
    if mm:
        right = right[:mm.start()]
    return " ".join(_WINDOW_WORD_RE.findall(left)[-_WINDOW_WORDS:]
                    + _WINDOW_WORD_RE.findall(right)[:_WINDOW_WORDS])


_PEOPLE_CLASS = frozenset({"subscrib", "memb", "user", "custom", "household", "cardhold"})
#: A draft window with one of these is a buyback, a dividend or a stock trade, not a purchase.
_BUYBACK_WORDS = frozenset({"back", "buyback", "repurchas", "stock", "shar", "dividend"})
#: The PERIOD of a rate ("3% a year") says how the rate is expressed, not what it measures: as
#: the only shared word it let a sheet's inflation rate ("At just three percent a year, prices
#: roughly double") ground "Stocks have averaged about 3% a year above inflation" (W2CB-8).
_PERIOD_WORDS = frozenset({"year", "annual", "month", "week", "days", "decad", "quart"})
_RATE_UNITS = frozenset({PERCENT, MULTIPLE})


def _classed(tokens: FrozenSet[str], cls: FrozenSet[str], tag: str) -> FrozenSet[str]:
    return frozenset(tag if t in cls else t for t in tokens)


def content_tokens(text: str, exclude: FrozenSet[str] = frozenset()) -> FrozenSet[str]:
    """Lower-case content words (≥4 letters, not function words), as ROOTS — the anchor set, so
    "compounding" in a draft meets "compounds" in the source. `exclude` (roots) drops the
    subject words — the sheet's proper nouns, the companies a draft names — so the shared word
    has to be what the number MEASURES: "Apple" is in every sentence of the Apple case study."""
    out = set()
    for w in _WORD_RE.findall(fold(text)):
        if w in _UNIT_ANCHORS:
            continue
        s = _stem(w)
        if len(s) >= 4 and s not in _STOPWORDS and s not in _UNIT_ANCHORS:
            r = root(s)
            r = _LEMMA.get(r, r)
            if r not in exclude:
                out.add(r)
    return frozenset(out)


#: How many words on each side of a number are its LOCAL context (content-B review, idx 6):
#: one shared word anywhere in the sentence let "renewal" ground "Costco rallied 90% as renewal
#: rates held" on "renewal rates sit above 90 percent".
_WINDOW_WORDS = 6
_WINDOW_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'&-]*")


def _window_text(text: str, start: int, end: int) -> str:
    """The `_WINDOW_WORDS` words either side of the span [start, end), joined. Bounded: it reads
    at most 200 characters each way, whatever the sentence length."""
    left = _WINDOW_WORD_RE.findall(text[max(0, start - 200):start])[-_WINDOW_WORDS:]
    right = _WINDOW_WORD_RE.findall(text[end:end + 200])[:_WINDOW_WORDS]
    return " ".join(left + right)


def _local_tokens(window: str, exclude: FrozenSet[str], sentence_x: FrozenSet[str],
                  sentence_all: FrozenSet[str]) -> Tuple[FrozenSet[str], bool]:
    """(anchors, names_only) for one number: the window's content words minus `exclude`,
    falling back to the whole sentence minus `exclude` (`sentence_x`), then to the whole sentence
    as written (`sentence_all` — "ETFs 101." has no word but the one a proper noun excludes).
    `names_only` is True on that last tier: the number's only context is NAMES. The SAME chain
    runs on both sides, so a fact sentence always grounds itself.

    The two sentence-level tiers are computed ONCE per sentence by the caller (round 2,
    W2-OB-6): recomputing them per number made a number-dense 6,000-character field quadratic
    (~0.7 s per field on the single worker). Per number this is O(window)."""
    local = content_tokens(window, exclude)
    if local:
        return local, False
    if sentence_x:
        return sentence_x, False
    return sentence_all, True


# ── ordinary English ──────────────────────────────────────────────────────────

#: Roots of every all-lower-case entry in Webster's Second International (web2, 1934; copyright
#: lapsed — see /usr/share/dict/README on any BSD/macOS host), one per line, sorted, gzipped.
#: Built with `root()` below, so a lookup must use `root()` too. It is what lets a title-cased
#: "Consistency" or "Affordable" read as a word and not as a name. Loaded lazily (~13 MB as a
#: sorted tuple) and only by the marketing writer's validator.
ENGLISH_ROOTS_PATH = Path(__file__).resolve().parents[3] / "data" / "english_roots_web2.txt.gz"
#: A root this long that PREFIXES a dictionary word is ordinary too ("automat" → "automatic";
#: 1934 predates "automate"). Seven characters, because six let "micros" (Microsoft) through.
_PREFIX_MIN = 7

#: Everyday words web2 (1934) cannot know. Lemmas only — `_candidates` reduces an inflected
#: form to meet them. Every entry is a common noun that is nobody's name; a word that is also a
#: brand or a person is deliberately NOT here (a brand that is a word is grounded per item).
MODERN_WORDS = frozenset("""
smartphone online offline website email app startup ecommerce podcast laptop paycheck
spreadsheet workflow workforce lifestyle mindset marketplace superstore megastore rideshare
freelancer broadband cellphone database dataset automation robotics cybersecurity aerospace
healthcare globalization benchmark stagflation headwind tailwind paywall carpool smartwatch
touchscreen outsource outsourcing offshoring microchip chatbot fintech
""".split())

#: Agentive heads for two-part compounds web2 predates ("carmaker", "chipmaking",
#: "homebuyer"). Deliberately a closed list of heads, not "any two words": an open rule makes
#: "facebook", "starbucks" and "blackrock" ordinary English.
_COMPOUND_HEADS = (
    "maker", "making", "builder", "building", "owner", "holder", "buyer", "seller", "goer",
    "earner", "saver", "spender", "lender", "lending", "borrower", "payer", "keeper", "worker",
    "grower",
)
_COMPOUND_PREFIX_MIN = 3


@lru_cache(maxsize=1)
def english_roots() -> Tuple[str, ...]:
    try:
        with gzip.open(ENGLISH_ROOTS_PATH, "rt", encoding="utf-8") as fh:
            words = tuple(line.strip() for line in fh if line.strip())
    except OSError as e:
        # Loud: without the dictionary every capitalised ordinary word is an "entity", and the
        # writer's acceptance rate collapses. Nothing unsafe gets through — only false rejects.
        logger.error("marketing grounding: cannot read %s (%s: %s)", ENGLISH_ROOTS_PATH.name,
                     type(e).__name__, e)
        return ()
    return tuple(sorted(set(words)))


def _sorted_has(seq: Sequence[str], word: str) -> bool:
    i = bisect_left(seq, word)
    return i < len(seq) and seq[i] == word


def _sorted_has_prefix(seq: Sequence[str], prefix: str) -> bool:
    i = bisect_left(seq, prefix)
    return i < len(seq) and seq[i].startswith(prefix)


@lru_cache(maxsize=8)
def _sorted_roots(roots: FrozenSet[str]) -> Tuple[str, ...]:
    """`vocab_roots` sorted, so the prefix test is a bisect, not a scan of the whole corpus
    vocabulary per capitalised token."""
    return tuple(sorted(roots))


def _candidates(low: str) -> List[str]:
    """The word and its likely bases: "adding" → add, "taming" → tame, "carmakers" → carmaker.
    web2 lists lemmas, not inflections, so an inflected form must be reduced to meet it."""
    w = _stem(low)
    out = [w, root(w)]
    for suf in ("ings", "ing", "ed", "es", "ers", "er", "s", "ly", "ies"):
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            base = w[: -len(suf)]
            out += [base, base + "e"]
            if suf == "ies":
                out.append(base + "y")
            if len(base) >= 3 and base[-1] == base[-2]:
                out.append(base[:-1])
    return out


def _plain_word(w: str, vocab: FrozenSet[str], vocab_roots: FrozenSet[str],
                eng: Sequence[str]) -> bool:
    r = root(w)
    return w in vocab or w in MODERN_WORDS or r in vocab_roots or _sorted_has(eng, r)


def _ordinary_compound(c: str, vocab: FrozenSet[str], vocab_roots: FrozenSet[str],
                       eng: Sequence[str]) -> bool:
    """"carmaker" = an ordinary word + a closed-list agentive head."""
    for head in _COMPOUND_HEADS:
        if c.endswith(head) and len(c) - len(head) >= _COMPOUND_PREFIX_MIN:
            if _plain_word(c[: -len(head)], vocab, vocab_roots, eng):
                return True
    return False


def ordinary(low: str, vocab: FrozenSet[str], vocab_roots: FrozenSet[str]) -> bool:
    """Is `low` an ordinary English word (not a name)? Corpus vocabulary first, then web2,
    then the modern-vocabulary supplement and closed-head compounds web2 predates."""
    if low in vocab or low in DEMONYMS or low in MODERN_WORDS:
        return True
    eng = english_roots()
    candidates = _candidates(low)
    for c in candidates:
        if c in vocab or c in MODERN_WORDS:
            return True
        r = root(c)
        if r in vocab_roots or _sorted_has(eng, r):
            return True
    r = root(low)
    if len(r) >= _PREFIX_MIN and (_sorted_has_prefix(eng, r)
                                  or _sorted_has_prefix(_sorted_roots(vocab_roots), r)):
        return True
    return any(_ordinary_compound(c, vocab, vocab_roots, eng) for c in candidates)


def is_ordinary_word(low: str, ctx: "GroundingContext") -> bool:
    return ordinary(low, ctx.vocab, ctx.vocab_roots)


@dataclass(frozen=True)
class FactNumber:
    value: float
    unit: str
    raw: str
    anchors: FrozenSet[str]             # the whole source sentence's anchors
    #: The anchors of the words around THIS number, minus the sheet's proper nouns (idx 6).
    local: FrozenSet[str] = frozenset()
    #: The whole sentence's anchors minus the same subject words.
    sentence: FrozenSet[str] = frozenset()
    #: The source states this number AS a price, value or return claim (`_PRICE_CLAIM_RE`).
    claim: bool = False
    #: "profit" / "loss" / "" — a source's loss cannot come back as a profit (idx 6).
    polarity: str = ""
    #: Roots of the NAMES in the source sentence (its excluded subject words): what an
    #: acquisition amount is the price OF ("Paid for Mellanox") — round 2, W2CB-12.
    names: FrozenSet[str] = frozenset()
    #: `local` came from the last fallback tier — the number's only context is names ("Dior,
    #: Fendi, Celine, Loewe, Bulgari in 2011, Tiffany in 2021…", "ETFs 101."). A draft may meet
    #: it with the names in ITS window (W2-OB-1: the fallback used to be one-sided, so those
    #: numbers could be restated only in a sentence with no other content word).
    names_only: bool = False
    #: Roots of the companies the source sentence names, whatever their length ("amd" is too
    #: short to be a content token): a YEAR's subject.
    companies: FrozenSet[str] = frozenset()
    #: The NAMES in this number's own clause (after the last , ; : ( or spaced dash before it) —
    #: what an AMOUNT is the price of in a names-only list row: "Dior, Fendi, Celine, Loewe,
    #: Bulgari in 2011, Tiffany in 2021 for roughly $15.8 billion" prices Tiffany, never Bulgari
    #: (round 3, W3CB-11).
    clause_names: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class GroundingContext:
    """Everything the grounding check needs to know about ONE item's fact sheet."""

    numbers: Tuple[FactNumber, ...]
    tokens: FrozenSet[str]              # every lower-cased word in the fact sheet
    text_folded: str                    # the folded fact sheet, for multi-word lookups
    vocab: FrozenSet[str] = field(default_factory=frozenset)   # corpus-wide ordinary words
    vocab_roots: FrozenSet[str] = field(default_factory=frozenset)
    #: Words this fact sheet writes in LOWER case — proof it uses a surname-like word
    #: ("wood", "marks") as an ordinary word, not as a person.
    lower_words: FrozenSet[str] = field(default_factory=frozenset)
    #: `text_folded.split()`, precomputed (it was re-split per capitalised token).
    folded_words: FrozenSet[str] = field(default_factory=frozenset)
    #: Maximal [a-z0-9] runs of the folded fact sheet: a letters-and-digits name-number ("13d",
    #: "ps5") is grounded iff it is one of these — a whole token, never a substring.
    alnum_tokens: FrozenSet[str] = field(default_factory=frozenset)
    #: Words this sheet writes ONLY capitalised ("disney", "ford", "dior") — its proper nouns.
    #: A first name in front of one ("Walt Disney") is a person unless the pair is in the sheet.
    proper_words: FrozenSet[str] = field(default_factory=frozenset)
    #: Words with a non-ASCII letter this sheet itself uses ("moët", "décor"), lower-cased. Any
    #: other accented word in a draft is rejected: an accent is how a name slips a lexicon.
    nonascii_words: FrozenSet[str] = field(default_factory=frozenset)
    #: Companies this sheet NAMES (`compliance.sentence_company_mentions`, plus any word it writes
    #: capitalised mid-sentence), canonical lower case. A company in a draft must be one of them
    #: — "apples to apples" does not ground "Apple" (content-B review, idx 4/11/12).
    company_names: FrozenSet[str] = field(default_factory=frozenset)
    #: Roots of this sheet's proper nouns, kept out of number anchors (idx 6).
    anchor_stop: FrozenSet[str] = field(default_factory=frozenset)
    #: (scale word, sentence anchors) for every million/billion/trillion the sheet states, as a
    #: number or a bare word ("tens of billions"): a draft's "trillion-dollar" needs one (idx 8).
    magnitudes: Tuple[Tuple[str, FrozenSet[str]], ...] = ()
    #: Designations the sheet writes ("737 max", "model 3") — `_designation_keys`.
    designations: FrozenSet[str] = frozenset()
    #: Roots of the companies this sheet is ABOUT (named in `_DOMINANT_MIN_SENTENCES`+ of its
    #: sentences: "nvidia" in the NVIDIA case study, never "mellanox"). What an acquisition
    #: amount is the price of is never the dominant subject.
    dominant: FrozenSet[str] = frozenset()


#: A number stated as a price, a value or a return: the sentence talks valuation, "worth", or
#: an instrument or a named company (the `compliance` placeholder) moving.
_CLAIM_MOVES = (r"(?:rose|climbed|rallied|soared|surged|jumped|plunged|fell|tumbled|sank|crashed|"
                r"tanked|doubled|tripled|gained|skyrocketed|slumped|slid|dropped|rebounded|popped|"
                r"returned|hit|reached|was up|were up|is up|are up)")
_PRICE_CLAIM_RE = re.compile(
    r"\b(?:valuations?|valued|market (?:cap|caps|capitalization|capitalisation|value)|share "
    r"prices?|stock prices?|per share|price targets?|trad(?:e|es|ed|ing) (?:at|near|around|below|"
    r"above)|worth\b(?!\s+(?:it\b|the (?:wait|effort|trouble|time)\b|a (?:look|read)\b|"
    r"[a-z]+ing\b)))\b|\b(?:" + COMPANY_MARK.lower() + r"|stock|shares)(?:'s)?(?:\s+(?:stock|shares))?\s+(?:[a-z]+ly\s+)?"
    + _CLAIM_MOVES + r"\b|(?<!market )\b(?:stock|shares?)\s+(?:costs?|sells? for|sold for|goes "
    r"for|went for|change[sd]? hands (?:for|at)|fetch(?:es|ed)?|traded for|trades for)\b"
)
#: Units a claim can carry: an amount of money, a percentage, a multiple.
_CLAIM_UNITS = frozenset({PERCENT, CURRENCY, FOREIGN_CURRENCY, MULTIPLE})
_PROFIT_RE = re.compile(r"\b(?:profits?|profitable|surplus|made money|earned)\b")
_LOSS_RE = re.compile(r"\b(?:loss|losses|lost|losing|lose|deficits?|burn(?:ed|t|ing|s)?)\b")


def _claim_view(sent: str, mentions: Optional[List[Tuple[str, int, int]]] = None) -> str:
    """The sentence folded, with every company it names replaced by `COMPANY_MARK` — the same
    view `compliance`'s company rows read."""
    sk = skeleton(sent)
    if mentions is None:
        mentions = sentence_company_mentions(sk)
    return fold(company_view(sk, mentions) if mentions else sk)


def _polarity(folded: str) -> str:
    p, l_ = bool(_PROFIT_RE.search(folded)), bool(_LOSS_RE.search(folded))
    return "profit" if p and not l_ else "loss" if l_ and not p else ""


def _mention_roots(sent: str, mentions: Optional[List[Tuple[str, int, int]]] = None
                   ) -> FrozenSet[str]:
    """Roots of every word of every company the sentence names."""
    if mentions is None:
        mentions = sentence_company_mentions(skeleton(sent))
    return frozenset(root(_stem(w)) for name, _a, _b in mentions for w in name.split())


#: Durations a sheet states in WORDS ("a decade before the incumbents", "more than half a
#: century since"), read as years on the SOURCE side only, so a draft's "10 years" / "over 50
#: years" can ground on them (round 2, corpus). A draft's own "a decade" stays words — it never
#: becomes a number the draft then has to ground.
_DURATION_WORDS_RE = re.compile(
    r"\b(a|one|two|three|four|five|half a|a half|a quarter|a quarter of a)\s+(decades?|"
    r"century|centuries)\b", re.IGNORECASE)
_DURATION_COUNT = {"a": 1.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0,
                   "half a": 0.5, "a half": 0.5, "a quarter": 0.25, "a quarter of a": 0.25}
#: A price said aloud ("might cost three-twenty next year" is $3.20), read on the SOURCE side
#: only and only in a sentence that already states a currency amount (round 2, corpus).
_SPOKEN_PRICE_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)-(ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety)\b", re.IGNORECASE)
_SPOKEN = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_SPOKEN.update({"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
                "seventy": 70, "eighty": 80, "ninety": 90})


def _source_only_numbers(converted: str, numbers: Sequence[NumberMention]) -> List[NumberMention]:
    """Numbers a fact sentence states in words the digit parser does not read: durations and a
    spoken price. Source side only (see the two patterns above)."""
    out: List[NumberMention] = []
    for m in _DURATION_WORDS_RE.finditer(converted):
        unit_years = 100.0 if m.group(2).lower().startswith("centur") else 10.0
        out.append(NumberMention(value=_DURATION_COUNT[m.group(1).lower()] * unit_years,
                                 unit=PLAIN, raw=m.group(0), start=m.start(), end=m.end()))
    if any(n.unit == CURRENCY for n in numbers):
        for m in _SPOKEN_PRICE_RE.finditer(converted):
            value = _SPOKEN[m.group(1).lower()] + _SPOKEN[m.group(2).lower()] / 100.0
            out.append(NumberMention(value=value, unit=CURRENCY, raw=m.group(0),
                                     start=m.start(), end=m.end()))
    return out


#: A number that is part of a DESIGNATION ("737 MAX", "Model 3", "Zen 2"): the number plus the
#: capitalised word glued to it. A designation the sheet writes grounds the same designation in
#: a draft ("The 737 MAX Example" was `number_context` — "MAX" is too short to be an anchor).
_DESIGNATION_AFTER_RE = re.compile(r"[ ]([A-Z][A-Za-z0-9]*)\b")
_DESIGNATION_BEFORE_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*)[ ]$")


def _designation_keys(sent: str, m: NumberMention) -> List[str]:
    """"737 MAX" → ["737 max"]; "Model 3" → ["model 3"]. A function word or a hedge is no part
    of a name ("The 737", "Over 500")."""
    if m.unit != PLAIN or not math.isfinite(m.value):
        return []
    num = m.raw.strip()
    keys = []
    for rx, text, fmt in ((_DESIGNATION_AFTER_RE, sent[m.end:m.end + 40], "{n} {w}"),
                          (_DESIGNATION_BEFORE_RE, sent[max(0, m.start - 40):m.start], "{w} {n}")):
        hit = rx.match(text) if fmt.startswith("{n}") else rx.search(text)
        if hit:
            w = hit.group(1).lower()
            if len(w) >= 2 and w not in _STOPWORDS:
                keys.append(fmt.format(n=num, w=w))
    return keys


def fact_numbers(sentences_: Iterable[str],
                 anchor_stop: FrozenSet[str] = frozenset()) -> Tuple[FactNumber, ...]:
    out: List[FactNumber] = []
    for sent in sentences_:
        converted = words_to_digits(sent)
        anchors = content_tokens(converted)
        mentions = sentence_company_mentions(skeleton(converted))
        exclude = anchor_stop | _mention_roots(converted, mentions)
        bare_x = content_tokens(converted, exclude)
        sentence_x = bare_x or anchors
        names = anchors - bare_x
        view = _claim_view(converted, mentions)
        claim = bool(_PRICE_CLAIM_RE.search(view))
        polarity = _polarity(view)
        numbers = extract_numbers(converted)
        for m in numbers + _source_only_numbers(converted, numbers):
            local, names_only = _local_tokens(_window_text(converted, m.start, m.end), exclude,
                                              bare_x, anchors)
            clause_names = (content_tokens(_own_clause(converted, m.start)) & names
                            if names_only else frozenset())
            out.append(FactNumber(value=m.value, unit=m.unit, raw=m.raw, anchors=anchors,
                                  local=local, sentence=sentence_x,
                                  claim=claim and m.unit in _CLAIM_UNITS, polarity=polarity,
                                  names=names, names_only=names_only,
                                  companies=_mention_roots(converted, mentions),
                                  clause_names=clause_names))
    return tuple(out)


_CLAUSE_CUT_RE = re.compile(r"[,;:(\[]|\s[-\u2013\u2014]\s")


def _own_clause(text: str, start: int) -> str:
    """The text of the number's own clause before it: after the last , ; : ( or spaced dash.
    Bounded to 200 characters, like `_window_text`."""
    left = text[max(0, start - 200):start]
    cut = 0
    for mm in _CLAUSE_CUT_RE.finditer(left):
        cut = mm.end()
    return left[cut:]




_DOMINANT_MIN_SENTENCES = 3


def _dominant_companies(fact_sentences: Sequence[str]) -> FrozenSet[str]:
    counts: dict = {}
    for fact in fact_sentences:
        for r in _mention_roots(fact):
            counts[r] = counts.get(r, 0) + 1
    return frozenset(r for r, n in counts.items() if n >= _DOMINANT_MIN_SENTENCES)


def fact_designations(sentences_: Iterable[str]) -> FrozenSet[str]:
    out = set()
    for sent in sentences_:
        converted = words_to_digits(sent)
        for m in extract_numbers(converted):
            out.update(_designation_keys(converted, m))
    return frozenset(out)


#: A bare or compounded scale word: "trillion-dollar", "multi-billion", "billions".
_MAGNITUDE_RE = re.compile(r"\b(?:multi-?)?(million|billion|trillion)s?(?=-dollar\b|\b)",
                           re.IGNORECASE)


def _scale_of(value: float) -> str:
    if not math.isfinite(value):
        return ""
    v = abs(value)
    return ("trillion" if v >= 1e12 else "billion" if v >= 1e9 else "million" if v >= 1e6
            else "")


def _bare_magnitudes(converted: str, numbers: Optional[Sequence[NumberMention]] = None
                     ) -> List[Tuple[str, str]]:
    """(scale, phrase) for every scale word NOT already part of a digit number in `converted`."""
    if "illion" not in converted.lower():
        return []
    if numbers is None:
        numbers = extract_numbers(converted)
    spans = [(m.start, m.end) for m in numbers]
    out = []
    for m in _MAGNITUDE_RE.finditer(converted):
        if any(a <= m.start() < b for a, b in spans):
            continue
        out.append((m.group(1).lower(), m.group(0)))
    return out


def _fact_magnitudes(sentences_: Iterable[str],
                     anchor_stop: FrozenSet[str]) -> Tuple[Tuple[str, FrozenSet[str]], ...]:
    out: List[Tuple[str, FrozenSet[str]]] = []
    for sent in sentences_:
        converted = words_to_digits(sent)
        anchors = content_tokens(converted, anchor_stop | _mention_roots(sent))
        scales = {scale for scale, _p in _bare_magnitudes(converted)}
        scales |= {_scale_of(m.value) for m in extract_numbers(converted)} - {""}
        out += [(scale, anchors) for scale in sorted(scales)]
    return tuple(out)


_NAME_TOKEN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9&]|[-'.](?=[A-Za-z0-9]))*")


def _sheet_company_names(fact_sentences: Sequence[str]) -> FrozenSet[str]:
    """Every company the sheet names (the detector the draft is judged by, which is what finds a
    multi-word name), plus every WHOLE token it writes capitalised, in any position: the sheet
    is our own text, so "Apple Moves Its Chips: 2014." or "— Marvel, Star Wars, Pixar" grounds
    the company wherever a draft then puts it. Lower case never does ("apples to apples"), and a
    hyphenated token stays whole ("Target-Date" is not "Target"). No eligible Journey sheet
    writes a lexicon company capitalised anywhere (`test_the_journey_sheets_name_no_company`)."""
    out = set()
    for fact in fact_sentences:
        for sent in sentences(skeleton(fact)):
            out.update(name for name, _a, _b in sentence_company_mentions(sent))
            for t in _NAME_TOKEN_RE.finditer(sent):
                w = t.group(0)
                if w.endswith("'s"):
                    w = w[:-2]
                if w[:1].isupper():
                    out.add(w.lower())
    return frozenset(out)


def full_vocab(corpus_vocab: FrozenSet[str]) -> FrozenSet[str]:
    return corpus_vocab | FUNCTION_WORDS | TEMPLATE_WORDS | MONTHS_DAYS


def build_context(fact_sentences: Sequence[str], vocab: FrozenSet[str]) -> GroundingContext:
    text = "\n".join(fact_sentences)
    folded = fold(text)
    # The sheet is read through the SAME skeleton as a draft (`_check_entities`), so an accented
    # proper noun ("Moët") grounds its own spelling and an accent can never split a name.
    sk = skeleton(text)
    tokens = set()
    for w in _WORD_RE.findall(folded):
        tokens.add(w.strip("'-."))
        tokens.add(_stem(w))
        for part in w.split("-"):
            if part:
                tokens.add(part.strip("'."))
    lower = set()
    for w in _LOWER_WORD_RE.findall(sk.translate(_APOSTROPHE_FOLD)):
        w = w.strip("'-")
        if w.endswith("'s"):
            w = w[:-2]
        if w:
            lower.add(w)
    capitalised = set()
    for tok in _TOKEN_RE.findall(sk):
        if tok[:1].isupper():
            low = tok.strip("'-.&").lower()
            if low.endswith("'s"):
                low = low[:-2]
            capitalised.update(p for p in re.split(r"[-.&']", low) if p)
    nonascii = frozenset(w.lower() for w in _LETTERS_RE.findall(text) if not w.isascii())
    base = full_vocab(vocab)
    proper = frozenset(capitalised - lower)
    company_names = _sheet_company_names(fact_sentences)
    # Anchors ignore the SUBJECT: the companies the sheet names (the detector's mentions, NOT
    # every mid-sentence capital — "Member Renewal Rate: ~90%" measures a renewal rate) and its
    # other true names (not ordinary English). A capitalised ordinary word ("Cost", "October",
    # "Reality") still measures something and stays an anchor.
    roots_ = vocab_roots(base)
    anchor_stop = frozenset(
        set().union(*(_mention_roots(f) for f in fact_sentences))
        | {root(_stem(w)) for w in proper if not ordinary(w, base, roots_)}
    )
    return GroundingContext(
        numbers=fact_numbers(fact_sentences, anchor_stop),
        tokens=frozenset(t for t in tokens if t),
        text_folded=folded,
        vocab=base,
        vocab_roots=vocab_roots(base),
        lower_words=frozenset(lower),
        folded_words=frozenset(folded.split()),
        alnum_tokens=frozenset(_ALNUM_RE.findall(folded)),
        proper_words=proper,
        nonascii_words=nonascii,
        company_names=company_names,
        anchor_stop=anchor_stop,
        magnitudes=_fact_magnitudes(fact_sentences, anchor_stop),
        designations=fact_designations(fact_sentences),
        dominant=_dominant_companies(fact_sentences),
    )


@lru_cache(maxsize=8)
def vocab_roots(vocab: FrozenSet[str]) -> FrozenSet[str]:
    return frozenset(root(w) for w in vocab)


#: A slash between digits makes a FRACTION or ratio ("3/4", and NFKC's "3⁄4" for "¾"): a
#: quantity, so neither side is ever an exempt small count. SOLIDUS, FRACTION SLASH, DIVISION
#: SLASH.
_SLASHES = "/\u2044\u2215"
_FRACTION_BEFORE_RE = re.compile(r"[0-9]\s?[" + _SLASHES + r"]\s?$")
_FRACTION_AFTER_RE = re.compile(r"\s?[" + _SLASHES + r"]\s?[0-9]")


def _in_fraction(m: NumberMention, sent: str) -> bool:
    return bool(_FRACTION_BEFORE_RE.search(sent[max(0, m.start - 4):m.start])
                or _FRACTION_AFTER_RE.match(sent, m.end))


def _small_structural(m: NumberMention, converted: str) -> bool:
    """A unit-less integer 0-5 used as a count ("3 lessons"), not a quantity ("up 5%", "3/4")."""
    if m.unit != PLAIN or not math.isfinite(m.value) or m.value != int(m.value) \
            or not 0 <= m.value <= 5:
        return False
    if _in_fraction(m, converted):
        return False
    tail = converted[m.end:m.end + 24].lower()
    # A hyphen-joined unit is still the unit ("5-fold", "4-bagger" — content-B review, idx 9).
    nxt = re.match(r"[\s-]*([a-z]+)", tail)
    return not (nxt and nxt.group(1) in _UNIT_WORDS)


def _check_numbers(field_name: str, text: str, ctx: GroundingContext) -> List[Violation]:
    """Every draft number must be a fact-sheet number (value AND unit class) whose context meets
    the source number's context (`_anchored`). Two refusals on top, whatever the anchors say:
    * an amount, percentage or multiple the draft states AS a price, value or return claim
      ("NVIDIA was worth $6.9 billion", "Apple climbed 70%") must be one the sheet states as
      such a claim — a Money Moves sheet states none, so the number is refused;
    * a sheet's loss cannot come back as a profit, or the reverse.
    A bare or compounded scale word ("a trillion-dollar company", "worth billions") is a
    magnitude and must share its scale and an anchor with a sheet sentence (idx 8).

    Linear: every sentence-level token set is computed once per sentence, and each number costs
    a bounded window plus set operations over the (bounded) matching sheet numbers."""
    out: List[Violation] = []
    # Spelled-out numbers are converted, then held to exactly the same standard as digits.
    converted = words_to_digits(text)
    #: (value, unit) → the sheet numbers it equals: a repetition loop of one number scans the
    #: sheet's numbers once, not once per repetition.
    matches_of: dict = {}
    for sent in sentences(converted):
        mentions = sentence_company_mentions(skeleton(sent))
        companies = _mention_roots(sent, mentions)
        exclude = ctx.anchor_stop | companies
        anchors = content_tokens(sent, exclude)
        full: Optional[FrozenSet[str]] = None
        subject: Optional[FrozenSet[str]] = None
        view = _claim_view(sent, mentions)
        claim = bool(_PRICE_CLAIM_RE.search(view))
        polarity = _polarity(view)
        numbers = extract_numbers(sent)
        for m in numbers:
            if _small_structural(m, sent):
                continue
            key = (m.value, m.unit)
            matches = matches_of.get(key)
            if matches is None:
                matches = matches_of[key] = [f for f in ctx.numbers if same_number(m, f)]
            if not matches:
                out.append(Violation(field_name, "ungrounded_number", m.raw[:_DETAIL_MAX]))
                continue
            if full is None:        # sentence-level sets: once per sentence (W2-OB-6)
                full = content_tokens(sent)
                subject = _sentence_subject(sent, mentions)
            window = _window_text(sent, m.start, m.end)
            local, _names_only = _local_tokens(window, exclude, anchors, full)
            ctx_m = _MentionContext(sent, m, window, local, full, full - anchors, ctx,
                                    subject or frozenset(), companies)
            fits = [f for f in matches if _anchored(ctx_m, f)]
            if claim and m.unit in _CLAIM_UNITS:
                fits = [f for f in fits if f.claim]
            if polarity:
                fits = [f for f in fits if f.polarity != _OPPOSITE[polarity]]
            if not fits:
                out.append(Violation(field_name, "number_context", m.raw[:_DETAIL_MAX]))
        for scale, phrase in _bare_magnitudes(sent, numbers):
            if not any(s == scale and a & anchors for s, a in ctx.magnitudes):
                out.append(Violation(field_name, "ungrounded_number", phrase[:_DETAIL_MAX]))
    return out


def _sentence_subject(sent: str, mentions: Sequence[Tuple[str, int, int]]) -> FrozenSet[str]:
    """Roots of the company the sentence OPENS with ("Tesla hired 500 staff", "The Tesla
    team…") — its subject in the plain order a draft uses. A company after a lead-in or a verb
    ("In 2021, Tiffany joined", "The group added Tiffany") is not taken for the subject."""
    if not mentions:
        return frozenset()
    name, start, _end = mentions[0]
    prefix = sent[:start].strip().lower()
    if prefix and prefix not in ("the", "a", "an"):
        return frozenset()
    return _mention_roots(sent, mentions[:1])


@dataclass
class _MentionContext:
    """One draft number and the token sets `_anchored` compares, computed once per number."""

    sent: str
    m: NumberMention
    window: str
    local: FrozenSet[str]            # window minus subjects (the chain's first live tier)
    full: FrozenSet[str]             # the whole sentence, subjects included
    names: FrozenSet[str]            # the sentence's subject/name roots
    ctx: GroundingContext
    #: The roots of the company the sentence OPENS with (`_sentence_subject`).
    subject: FrozenSet[str] = frozenset()
    #: Roots of every company the sentence names, whatever their length.
    companies: FrozenSet[str] = frozenset()
    _window_all: Optional[FrozenSet[str]] = None
    _year: Optional[Tuple[FrozenSet[str], FrozenSet[str]]] = None

    @property
    def window_all(self) -> FrozenSet[str]:
        """The window's content words WITH its names ("Tiffany", "ETFs")."""
        if self._window_all is None:
            self._window_all = content_tokens(self.window)
        return self._window_all

    @property
    def year_sets(self) -> Tuple[FrozenSet[str], FrozenSet[str]]:
        """(event kinds of the year's own clause, the sentence's words + companies with the
        begin class folded) — per number, not per matching sheet number."""
        if self._year is None:
            self._year = (_event_kinds(content_tokens(_event_window(self.sent, self.m.start,
                                                                   self.m.end))),
                          _classed(self.full | self.companies, _BEGIN_CLASS, "~begin"))
        return self._year


def _anchored(c: _MentionContext, f: FactNumber) -> bool:
    """Does the draft number's context meet sheet number `f`'s? (round 2: W2-OB-1, W2CB-12,
    W2CB-8). In order:

    1. The draft number's own words meet the source number's words or its sentence, subjects
       and hedges excluded (idx 6). For a RATE the period ("a year") does not count, unless it
       is all the source has.
    2. A YEAR binds by a shared word ANYWHERE in the two sentences, names included — a year
       carries no price or value claim, and the subject is what identifies a dated event
       ("LVMH bought Bulgari in 2011" against "…, Bulgari in 2011, …"). Founding / launch verbs
       are one class ("established in 1987" against "TSMC Founded: 1987"). A year whose own
       verb is unambiguously ANOTHER event never binds, whatever else is shared
       (`_YEAR_EVENT_KINDS`, checked first).
    3. An AMOUNT restated with another acquisition verb ("acquired", "cost", "picked up",
       "became part of" for "Paid for"), when the draft names what the sheet's amount sentence
       names — not the company the sheet is about ("NVIDIA spent $6.9 billion on research") —
       and is not a buyback, a holder's cost or a present-day price ("Mellanox would cost about
       $6.9 billion today"). Rule 1 cannot skip that names check: an amount whose only shared
       word is an acquisition verb needs it too (round 3, W3CB-11).
    4. A COUNT of people restated with another people noun ("members" for "subscribers").
    5. A source number whose only context is names meets the names in the draft's window —
       never the draft's subject company ("Tesla hired 500 staff" against "Tesla: 500." is the
       subject anchoring everything again, idx 6) — and, for an amount, only the names of its
       own clause (round 3, W3CB-11).
    6. A designation the sheet writes ("737 MAX")."""
    m = c.m
    if m.unit == YEAR:
        mine, full_begin = c.year_sets
        theirs_kinds = _event_kinds(f.anchors)
        if mine and theirs_kinds and not mine & theirs_kinds:
            return False            # "Meta founded Oculus in 2014" is not "Oculus Acquired: 2014"
    theirs = f.local | f.sentence
    shared = c.local & theirs
    amount = m.unit in (CURRENCY, FOREIGN_CURRENCY)
    # Round 3 (W3CB-11): an AMOUNT whose only shared words are acquisition verbs ("paid") is
    # bound to what it is the price OF — "LVMH paid about $15.8 billion for Celine" shares
    # "pay" with "Paid for Tiffany: ~$15.8B" and used to skip rule 3's names check.
    acquire_only = (amount and shared and shared <= _ACQUIRE_CLASS
                    and bool(f.names - c.ctx.dominant)
                    and not (c.names - c.ctx.dominant) & f.names)
    if m.unit in _RATE_UNITS:
        if shared - _PERIOD_WORDS or (shared and theirs <= _PERIOD_WORDS):
            return True
    elif shared and not acquire_only:
        return True
    if m.unit == YEAR:
        if c.year_sets[1] & _classed(f.anchors | f.companies, _BEGIN_CLASS, "~begin"):
            return True
    if amount and f.unit == m.unit:
        # The phrase is read on the number's own clause: "Tiffany became part of LVMH in 2021
        # for roughly $15.8 billion" puts "became" outside the six-word window.
        mine_acquire = ("~acquire" in _classed(c.local, _ACQUIRE_CLASS, "~acquire")
                        or bool(_ACQUIRE_PHRASE_RE.search(_own_clause(c.sent, m.start).lower())))
        if (mine_acquire and "~acquire" in _classed(theirs, _ACQUIRE_CLASS, "~acquire")
                and (c.names - c.ctx.dominant) & f.names
                and not c.window_all & _BUYBACK_WORDS
                and not _ACQUIRE_REFUSE_RE.search(c.window.lower())):
            return True
    if m.unit == PLAIN and ("~people" in _classed(c.local, _PEOPLE_CLASS, "~people")
                            & _classed(theirs, _PEOPLE_CLASS, "~people")):
        return True
    # Rule 5. An AMOUNT, a percentage or a multiple meets only the names of its OWN clause in
    # the source row (W3CB-11); a YEAR or a plain count may meet any name in the row
    # ("LVMH bought Bulgari in 2011" against "…, Bulgari in 2011, Tiffany in 2021 for…").
    if f.names_only and (c.window_all - c.subject - c.ctx.dominant) & (
            f.local if m.unit in (YEAR, PLAIN) else f.clause_names):
        return True
    if m.unit == PLAIN and c.ctx.designations and any(
            k in c.ctx.designations for k in _designation_keys(c.sent, m)):
        return True
    return False


_OPPOSITE = {"profit": "loss", "loss": "profit"}


def _contains_token(haystack: str, needle: str) -> bool:
    """`needle` occurs in `haystack` as a whole token — "3d" is NOT in "schedule 13d"."""
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack) is not None


def _check_name_numbers(field_name: str, text: str, ctx: GroundingContext) -> List[Violation]:
    out: List[Violation] = []
    seen = set()
    for m in NAME_NUMBER_RE.finditer(text):
        raw = m.group(0).lower()
        if raw in seen:
            continue
        seen.add(raw)
        # "Q3", "H1", "B2B" are allowlisted vocabulary (ACRONYMS), not a product or a filing.
        if raw in GLOBAL_NAME_NUMBERS or m.group(0).upper() in ACRONYMS:
            continue
        if raw in ctx.alnum_tokens:
            continue
        # "s&p 500", "form 4", "10-k" carry a space or punctuation: whole-token search (rare,
        # and only for a name-number the fast set did not ground). A context built without
        # `alnum_tokens` falls back to the same search for every name-number.
        if (not raw.isalnum() or not ctx.alnum_tokens) and _contains_token(ctx.text_folded, raw):
            continue
        out.append(Violation(field_name, "ungrounded_name_number", m.group(0)[:_DETAIL_MAX]))
    return out


#: Surnames of the App Store "Do not use" names AND of their common misspellings. Web2 lists
#: most of them as words ("graham", "lynch", "wood", "marks", "buffet"), which made "Graham
#: taught patience." ordinary English; compliance matches only the unambiguous ones alone.
#: Capitalised, they are a person ("A Buffet-style habit" is the misspelled investor).
DENIED_SURNAMES = frozenset(n.split()[-1] for n in APP_STORE_NAMES + MISSPELLINGS if n.split())

_CAMEL_RE = re.compile(r"[a-z][A-Z]")


def _check_entities(field_name: str, text: str, ctx: GroundingContext) -> List[Violation]:
    """`text` is the draft's SKELETON: an accent or a U+2010 hyphen can no longer split "Buffétt"
    into an ordinary "Buff" and a lower-case "tt" (the sheet is read the same way)."""
    out: List[Violation] = []
    seen = set()
    for tok in _TOKEN_RE.findall(text):
        if not any(c.isupper() for c in tok):
            continue
        bare = tok.strip("'-.&")
        if bare.lower().endswith("'s"):
            bare = bare[:-2]
        if not bare or bare in seen:
            continue
        seen.add(bare)
        low = bare.lower()
        letters = re.sub(r"[^A-Za-z]", "", bare)
        if len(letters) >= 3 and letters.endswith("s") and letters[:-1].isupper():
            bare, letters, low = bare[:-1], letters[:-1], low[:-1]  # plural acronym: "CPUs"
        if len(letters) >= 2 and letters.isupper():
            if bare.upper() in ACRONYMS or low in ctx.tokens or low in ctx.folded_words:
                continue
            out.append(Violation(field_name, "ungrounded_acronym", bare[:_DETAIL_MAX]))
            continue
        surname = next((w for w in (low, _stem(low), *re.split(r"[-.&']", low))
                        if w in DENIED_SURNAMES), None)        # "Graham", "Grahams", "Lynch-style"
        if surname and surname not in ctx.lower_words:
            out.append(Violation(field_name, "person_named", bare[:_DETAIL_MAX]))
            continue
        if low in ctx.tokens or _stem(low) in ctx.tokens:
            continue
        if _CAMEL_RE.search(bare):
            # "PayPal", "BlackRock", "YouTube": internal capitals are a brand's, never a word's.
            out.append(Violation(field_name, "ungrounded_entity", bare[:_DETAIL_MAX]))
            continue
        if is_ordinary_word(low, ctx):
            continue
        parts = [p for p in re.split(r"[-.&']", low) if p]
        if len(parts) > 1 and all(p in ctx.tokens or is_ordinary_word(p, ctx) for p in parts):
            continue
        if len(bare) == 1:
            continue
        out.append(Violation(field_name, "ungrounded_entity", bare[:_DETAIL_MAX]))
    return out


#: A capitalised first name followed by a capitalised word or an initial ("Walt Disney",
#: "Henry Ford", "Jeff B."). Possessive allowed on the second word. Zero-width (a lookahead) so
#: "Uncle Walt Disney" still offers "Walt Disney"; each attempt is bounded.
_NAME_PAIR_RE = re.compile(
    r"(?<![A-Za-z0-9'\-])(?=([A-Z][a-z]{2,})\s+([A-Z][A-Za-z\-]*(?:'[A-Za-z]+)?|[A-Z]\.?)(?![A-Za-z]))"
)


def _check_name_pairs(field_name: str, text: str, ctx: GroundingContext) -> List[Violation]:
    """A first name (`compliance.given_names`) in front of a NAME — a proper noun of this sheet
    ("Disney", "Ford", "Dior"), a word that is not English, or an initial — is a person unless
    this sheet states that exact pair ("Louis Vuitton" in the LVMH case study is the house, so it
    passes). The sheet names the brand, never its founder, so "Walt Disney" and "Henry Ford"
    are founders the writer added. The first name of someone the person lexicon lists
    ("Warren", "Peter", "Charlie") needs no name-like second word: a Title-Case "Why Warren
    Waits" is that person too, unless the sheet states the pair. `text` is the skeleton."""
    out: List[Violation] = []
    names = given_names()
    denied = denied_given_names() - NOUN_GIVEN_NAMES
    seen = set()
    for sent in sentences(text):
        out += _sentence_name_pairs(field_name, sent, ctx, names, denied, seen)
    return out


_PREV_TOKEN_RE = re.compile(r"([A-Za-z][A-Za-z'.-]*)\W*$")


def _sentence_name_pairs(field_name: str, sent: str, ctx: GroundingContext,
                         names: FrozenSet[str], denied: FrozenSet[str], seen: set) -> List[Violation]:
    """Round 2 (W2CB-7): the pair is also a person when a title word stands in front of it
    ("Economist Frank Knight drew this line") or when it sits MID-sentence in prose that is not a
    Title-Case headline ("As Frank Knight showed, …") — whatever the surname is. A company the
    lexicon names ("Morgan Stanley") is never a person pair."""
    out: List[Violation] = []
    first_letter = next((i for i, ch in enumerate(sent) if ch.isalpha()), 0)
    title: List[bool] = []
    spans: Optional[List[Tuple[str, int, int]]] = None
    for m in _NAME_PAIR_RE.finditer(sent):
        first = m.group(1).lower()
        if first not in names and first not in denied:
            continue
        second = m.group(2)
        if second.lower().endswith("'s"):
            second = second[:-2]
        sur = second.strip(".-'").lower()
        if not sur:
            continue
        if sur in FUNCTION_WORDS or sur in TEMPLATE_WORDS or sur in MONTHS_DAYS:
            continue
        name_like = (first in denied or len(sur) == 1 or sur in ctx.proper_words
                     or not (sur in ctx.tokens or is_ordinary_word(sur, ctx)))
        if not name_like:
            pm = _PREV_TOKEN_RE.search(sent[max(0, m.start() - 30):m.start()])
            prev = pm.group(1).lower().rstrip(".") if pm else ""
            if prev in TITLE_WORDS:
                name_like = True                     # "Economist Frank Knight"
            elif m.start() > first_letter and not (
                    pm and pm.group(1)[:1].isupper() and prev not in FUNCTION_WORDS
                    and prev not in _COMPLIANCE_FUNCTION_WORDS
                    and max(0, m.start() - 30) + pm.start(1) > first_letter):
                if not title:
                    title.append(_title_cased(sent))
                name_like = not title[0]             # "As Frank Knight showed"
        if not name_like:
            continue
        if spans is None:
            spans = sentence_company_mentions(sent)
        if any(a <= m.start() < b for _n, a, b in spans):
            continue                                 # "Morgan Stanley" is a company
        pair = f"{first} {sur}"
        # Substring test first (C speed); the token-bounded regex only when it could be there.
        if pair in seen or (pair in ctx.text_folded and _contains_token(ctx.text_folded, pair)):
            continue
        seen.add(pair)
        out.append(Violation(field_name, "person_named", f"{m.group(1)} {second}"[:_DETAIL_MAX]))
    return out


def _check_companies(field_name: str, text: str, ctx: GroundingContext,
                     already: FrozenSet[str] = frozenset()) -> List[Violation]:
    """A company the draft NAMES (`compliance.sentence_company_mentions`) must be one this sheet
    names too (content-B review, idx 4/11/12). This is the rule `_check_entities` cannot apply to
    a company whose name is an English word ("Apple", "Target", "Visa", "Coke", "Home Depot"),
    or to a name written in lower case ("buy nvidia"), or to a hyphenated name whose parts are
    words ("Coca-Cola"): the dictionary, or the lower case, waves it through. A name
    `_check_entities` already reported (as an entity, an acronym or a person) is not reported
    twice. `text` is the skeleton; `already` holds the details `_check_entities` reported."""
    out: List[Violation] = []
    seen = set()
    for sent in sentences(text):
        for name, a, b in sentence_company_mentions(sent):
            surface = sent[a:b]
            if name in ctx.company_names or name in seen or surface in already:
                continue
            seen.add(name)
            out.append(Violation(field_name, "ungrounded_entity", surface[:_DETAIL_MAX]))
    return out


def _check_accented_words(field_name: str, text: str, ctx: GroundingContext) -> List[Violation]:
    """Fail-closed backstop under the skeleton: a word with a non-ASCII letter must be one this
    sheet itself uses ("Moët", "décor"). A LETTER-level allowance would not close anything —
    "Buffétt" would pass on any sheet containing "décor"."""
    out: List[Violation] = []
    seen = set()
    for w in _LETTERS_RE.findall(text):
        if w.isascii():
            continue
        low = w.lower()
        if low in seen or low in ctx.nonascii_words:
            continue
        seen.add(low)
        out.append(Violation(field_name, "non_latin", w[:_DETAIL_MAX]))
    return out


def check_grounding(field_name: str, text: str, ctx: GroundingContext) -> List[Violation]:
    """All grounding violations for one field. Pure; never raises.

    Text past `GROUNDING_CAP` is reported as `too_long` and only the prefix is scanned — the
    same contract as `compliance.scan_text`, so a repetition loop costs a bounded amount of work
    on the single worker. An unexpected error is logged with its stack and returned as a
    `grounding_error` violation: failing closed rejects the draft, it never waves it through."""
    out: List[Violation] = []
    try:
        if not text:
            return []
        if len(text) > GROUNDING_CAP:
            out.append(Violation(field_name, "too_long", f"{len(text)} chars > {GROUNDING_CAP}"))
            text = text[:GROUNDING_CAP]
        out += _check_numbers(field_name, text, ctx)
        out += _check_name_numbers(field_name, text, ctx)
        sk = skeleton(text)
        entities = _check_entities(field_name, sk, ctx)
        out += entities
        out += _check_companies(field_name, sk, ctx, frozenset(v.detail for v in entities))
        out += _check_name_pairs(field_name, sk, ctx)
        out += _check_accented_words(field_name, text, ctx)
    except Exception as e:  # noqa: BLE001 — documented total; fail closed, loudly
        logger.exception("marketing grounding: check failed on field=%s (%s: %s)",
                         field_name, type(e).__name__, e)
        out.append(Violation(field_name, "grounding_error", type(e).__name__))
    return out
