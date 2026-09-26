"""
Compliance scan for PUBLIC marketing copy (SYSTEM_DESIGN_GUIDELINES §12.5, rules marketing.md §7).

Every string the writer produces is scanned here before it can be stored as `accepted`, and the
Learn corpus is scanned with the same rules sentence by sentence before the writer ever sees it
(`content_pool.py`), so prompt, fact sheet and validator agree on what is allowed. What is
checked, and why each is a hard reject rather than a warning:

* **Real people** — the App Store "Do not use" list and the migration-103 impersonation boundary
  apply to marketing copy (rules §1). A name in a public post is a likeness/defamation surface
  the model can make worse, so no named individual is ever allowed, even one the source names.
  A listed surname that is also an English word ("Graham", "Lynch") is matched by its capital
  and position (`_ambiguous_surname_hits`), so "graham crackers" and "Merrill Lynch" survive.
  A person pointed at WITHOUT a lexicon name is the same person: a listed first name used as a
  name ("Uncle Warren", "Ben invented Mr. Market"), a founder's first name + brand doing what
  only a person does ("Henry Ford said"), an epithet ("a legendary value investor", "the father
  of value investing"), a named company's role holder ("Apple's CEO"), and — in a Money Moves
  case study only — any singular role ("its own CEO", "one man") or he/she (`_given_name_hits`,
  `_described_person_hits`).
* **Quotations** — a vendored quote's 6-gram, a famous saying's signature phrase, an
  attribution frame ("as the saying goes"), or ≥80% of one quote clause's words in any order.
* **Promises and the disclaimer's subject** — the disclaimer says "not investment advice … risk,
  including loss of principal … AI-assisted" and code appends it, so model text that promises an
  outcome ("always goes up", "never lose money", "a safe way to grow"), talks about advice,
  recommendations or disclaimers, or denies AI authorship contradicts it and is rejected.
* **Class-B language** — EU MAR Art. 2(4)/3(1)(35) treats a public opinion on a named
  instrument's present or future value or price as an investment recommendation, and "not
  investment advice" does not help (ESMA, Jan 2026). Tier 1 phrases are rejected anywhere;
  tier 2 evaluative words only in a sentence that also talks about stock/shares/valuation or
  names a company, so "the warehouse sells cheap goods" survives and "its shares look cheap"
  does not. A verdict pinned to one instrument or issuer ("the shares are a bargain", "Apple
  stock looks cheap") fires in every mode. A COMPANY is found by the vendored lexicon
  (`company_mentions`, English-word names like "Apple" or "Target" only in a name position);
  a sentence naming one gets every Money Moves row even in a Journey post, and the company is
  the instrument of the directive / verdict / forecast / price-move / worth rows ("Buy NVIDIA",
  "Costco is a great investment", "AMD will keep climbing", "Apple climbed 70%"). Forecasts
  and buy/sell directives about the market, an index or a fund apply in every mode.
* **Return figures** — a percentage (read after `words_to_digits`, so "seventy percent" counts),
  "triple-digit gains" or an N-bagger next to a return or an investor.
* **Identity / brand / voice** — the model must never name its vendor (IDENTITY_RULE), and the
  brand, CTA and disclaimer are CODE-owned (`post_copy.py`): model text that mentions Caydex, the
  app, a link or a disclaimer, asks for a tap/follow/save/share, claims an endorsement or social
  proof, or speaks as I/we (outside a reader's self-question) is rejected rather than trusted.
* **Accents** — every lexicon matches a `skeleton()` of the text (accents and dotless/stroked
  letters folded), so "Buffétt", "Gémini" and "Cáydex" are the names they read as.
* **Links, handles, markup, cashtags, hashtags** — each is either a cost (X bills $0.20 per
  post with a URL), a tag on a real person, or an injection surface on the passkey domain.

This module is PURE and FMP-free by construction: it imports only `chat_security` (verified not
to load `app.integrations.fmp`) and the pure `numbers` module, and reads four bundled data files
(whale registry, investor quotes, given names, known companies). It deliberately does NOT
import `agents.chat_guardrails` — that import runs `agents/__init__`, which loads the FMP client.

Performance: every pattern is linear, and every input is capped (`_SCAN_CAP`) BEFORE any regex
runs — this executes on the single uvicorn worker over model output that can be a repetition
loop at MAX_TOKENS.
"""

from __future__ import annotations

import html
import json
import logging
import re
import unicodedata
from bisect import bisect_right
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Sequence, Tuple

from app.services.chat_security import normalize_text
from app.services.marketing.numbers import has_non_ascii_digit, words_to_digits
from app.services.marketing.tlds import is_non_tld_tail

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

#: Hard ceiling on how much of any one field is scanned. Real fields are far shorter (the
#: longest caption budget is ~1,500 chars); anything longer is rejected as `too_long` first.
_SCAN_CAP = 6000
#: How much of the NEXT field `scan_text(next_text=…)` reads: only its first sentence matters.
_NEXT_TEXT_CAP = 600


@dataclass(frozen=True)
class Violation:
    field: str
    code: str
    detail: str

    def as_dict(self) -> Dict[str, str]:
        return asdict(self)


# ── normalisation ─────────────────────────────────────────────────────────────

_QUOTE_FOLD = str.maketrans({
    # Code points, never literal look-alikes, so the table is reviewable.
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u00a0": " ",
})
_WS_RE = re.compile(r"\s+")
_BLANK_RUN_RE = re.compile(r"\n{3,}")

#: Default-ignorable code points that are NOT category Cf but still render as nothing, so they
#: can split a name exactly like a zero-width space: COMBINING GRAPHEME JOINER, the Khmer
#: inherent vowels, the Mongolian free variation selectors and the Hangul fillers.
_IGNORABLE_NON_CF = frozenset({
    0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F, 0x3164, 0xFFA0,
})
_VS16 = 0xFE0F


def _is_invisible(ch: str) -> bool:
    o = ord(ch)
    if o < 0xAD:
        return False
    if o in _IGNORABLE_NON_CF:
        return True
    # Variation selectors 1-15 and the supplement never carry meaning in Latin prose.
    if 0xFE00 <= o <= 0xFE0E or 0xE0100 <= o <= 0xE01EF:
        return True
    return unicodedata.category(ch) == "Cf"


def _strip_invisibles(s: str) -> str:
    """Drop every format (Cf) and default-ignorable character. `normalize_text` strips a fixed
    list; SOFT HYPHEN, the invisible math operators, ARABIC LETTER MARK, MONGOLIAN VOWEL
    SEPARATOR and COMBINING GRAPHEME JOINER survived it and split "Buffett" past the scan.
    VARIATION SELECTOR-16 is kept only after a non-letter (an emoji's presentation selector);
    after a letter it is as invisible as the rest. One linear pass; ASCII short-circuits."""
    if s.isascii():
        return s
    out: List[str] = []
    prev_alpha = False
    changed = False
    for ch in s:
        if _is_invisible(ch) or (ord(ch) == _VS16 and prev_alpha):
            changed = True
            continue
        out.append(ch)
        prev_alpha = ch.isalpha()
    if not changed:
        return s
    # Removing a joiner can leave a composable pair or a new blank-line run behind.
    return _BLANK_RUN_RE.sub("\n\n", unicodedata.normalize("NFKC", "".join(out)))


#: An HTML character reference WITH its semicolon ("&amp;", "&#46;", "&#x40;"). The semicolon is
#: required on purpose: `html.unescape` alone also decodes legacy names without one, and
#: "S&P 500", "R&D" and "Scale & Logistics" must stay exactly as written.
_ENTITY_RE = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,31}|#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6});")


def _decode_entities(s: str) -> str:
    """Decode HTML character references once, BEFORE the invisible strip: "&#8203;" must come
    back as a zero-width space while the strip can still remove it, and "example&period;com"
    must reach the link scan as "example.com". Anything still entity-shaped afterwards (a
    double-encoded "&amp;lt;") is left for the markup scan to reject. Linear: one regex pass."""
    if "&" not in s:
        return s
    return _ENTITY_RE.sub(lambda m: html.unescape(m.group(0)), s)


def clean(text: str) -> str:
    """The canonical form of a string: HTML entities decoded, NFKC, invisibles/controls/format
    characters stripped. This IS the text that gets stored and published — scanning a folded
    copy and publishing the original is the gap that lets a zero-width space hide a name, and a
    literal "&amp;" published to a plain-text platform is the same gap in reverse."""
    return _strip_invisibles(normalize_text(_decode_entities(text or ""))).strip()


#: Latin letters with no Unicode decomposition, mapped to what a reader (and a TTS voice) sees.
#: NFKD + dropping combining marks handles "é", "ü", "ý"; these survive it and would still split
#: a name past every lexicon (a dotless-i "Gemini", a stroked-t "Buffett").
_LETTER_FOLD = str.maketrans({
    "\u0131": "i", "\u0130": "I", "\u0142": "l", "\u0141": "L", "\u00f8": "o", "\u00d8": "O",
    "\u0111": "d", "\u0110": "D", "\u00f0": "d", "\u00d0": "D", "\u0127": "h", "\u0126": "H",
    "\u0167": "t", "\u0166": "T", "\u00df": "ss", "\u1e9e": "SS", "\u00e6": "ae", "\u00c6": "AE",
    "\u0153": "oe", "\u0152": "OE", "\u00fe": "th", "\u00de": "Th", "\u0138": "k", "\u0180": "b",
    "\u0183": "b", "\u0188": "c", "\u018c": "d", "\u0192": "f", "\u01a5": "p", "\u01ad": "t",
    "\u01b4": "y", "\u01b6": "z", "\u0237": "j", "\u0269": "i", "\u0199": "k", "\u019a": "l",
    "\u0271": "m", "\u0272": "n", "\u0273": "n", "\u0280": "r", "\u0282": "s", "\u0288": "t",
    "\u028b": "v", "\u0290": "z", "\u0291": "z",
})
#: Interpuncts between two letters ("Buf" U+00B7 "fett") read as nothing inside a word.
_INTERPUNCTS = "\u00b7\u0387\u2027\u2219\u22c5\u30fb"
_INNER_INTERPUNCT_RE = re.compile(r"(?<=[A-Za-z])[" + _INTERPUNCTS + r"](?=[A-Za-z])")


def skeleton(text: str) -> str:
    """The matching skeleton of a string: quotes/dashes straightened, CASE KEPT, every accent
    removed (NFKD, combining marks dropped, then `_LETTER_FOLD`), inner interpuncts dropped.
    "Warren Buffétt", "Gémini", "Cáydex" and "Buff" U+2010 "ett" all become the ASCII their
    reader sees, so every lexicon and the grounding tokenizer see them too. Used ONLY for
    matching — the stored and published text is `clean()`'s, unchanged ("Moët", "décor").
    Linear; ASCII short-circuits."""
    s = (text or "").translate(_QUOTE_FOLD)
    if s.isascii():
        return s
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn").translate(_LETTER_FOLD)
    return _INNER_INTERPUNCT_RE.sub("", s)


def fold(text: str) -> str:
    """Lower-cased, quote/dash-straightened, ACCENT-FREE (`skeleton`), whitespace-collapsed view
    used for phrase matching (so a name split across a line break, or spelled with a stray
    accent, still matches)."""
    return _WS_RE.sub(" ", skeleton(text).lower()).strip()


#: Words whose trailing period is not a sentence end ("Mr. Market", "Visa vs. Mastercard",
#: "Costco Inc. was…"). Lower case, without the period. `no` only counts before a digit
#: ("No. 1"); a one-letter capital is an initial ("Burton G. Malkiel") and a dotted run is an
#: abbreviation ("U.S.", "e.g.", "i.e.") — both handled in `_is_abbreviation`.
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "mt", "ft", "gen", "gov", "sen", "rep",
    "capt", "lt", "col", "sgt", "rev", "hon", "inc", "co", "corp", "ltd", "llc", "plc", "bros",
    "vs", "v", "no", "nos", "fig", "vol", "approx", "dept", "est", "jan", "feb", "mar", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
})
_ABBREV_LOOKBACK = 12
_DOTTED_ABBREV_RE = re.compile(r"(?:[A-Za-z]\.)+[A-Za-z]")
_BOUNDARY_RE = re.compile(r"[.!?]\s+|\n+")


def _is_abbreviation(text: str, dot: int, after: int) -> bool:
    """Is the period at `text[dot]` the end of an abbreviation rather than of a sentence?
    Looks back at most `_ABBREV_LOOKBACK` characters, so the whole split stays linear."""
    j = dot
    floor = max(0, dot - _ABBREV_LOOKBACK)
    while j > floor and text[j - 1].isascii() and (text[j - 1].isalpha() or text[j - 1] == "."):
        j -= 1
    if j == floor and j > 0 and (text[j - 1].isalpha() or text[j - 1] == "."):
        return False  # a run longer than any abbreviation
    if j > 0 and text[j - 1].isdigit():
        return False  # "1st." is an ordinal ending a sentence, not "St."
    token = text[j:dot]
    if not token or token.startswith("."):
        return False
    low = token.lower()
    if low == "no":
        return text[after:after + 1].isdigit()
    if low in _ABBREVIATIONS:
        return True
    if len(token) == 1:
        return token.isupper()          # an initial
    return bool(_DOTTED_ABBREV_RE.fullmatch(token))


def sentences(text: str) -> List[str]:
    """Split on sentence punctuation and line breaks, but not after an abbreviation ("Mr.",
    "vs.", "Inc.", "U.S.", "e.g.", an initial) — splitting there fragments fact sheets and
    shrinks the scope of the per-sentence checks ("Investors thought Costco Inc. was a bargain."
    must be ONE sentence for the tier-2 check to see "Costco" and "bargain" together). A line
    break always splits. Linear: one regex pass plus an O(1) look-back per boundary."""
    text = text or ""
    out: List[str] = []
    start = 0
    for m in _BOUNDARY_RE.finditer(text):
        end = m.start()
        if text[end] != "\n":
            if text[end] == "." and "\n" not in m.group(0) and _is_abbreviation(text, end, m.end()):
                continue
            end += 1                     # the punctuation stays with its sentence
        part = text[start:end].strip()
        if part:
            out.append(part)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def _phrase_re(phrases: Iterable[str]) -> re.Pattern:
    """Word-bounded alternation over folded phrases. Longest first so "warren buffett" wins
    over "buffett" in the detail string."""
    alts = sorted({p for p in phrases if p}, key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(p) for p in alts) + r")(?![a-z0-9])")


# ── real people ───────────────────────────────────────────────────────────────

#: The App Store listing's "⛔ Do not use" names (documents/legal/app-store-listing.md).
APP_STORE_NAMES = (
    "warren buffett", "peter lynch", "cathie wood", "bill ackman", "benjamin graham",
    "ben graham", "charlie munger", "ray dalio", "michael burry", "joel greenblatt",
    "howard marks", "morgan housel", "robert kiyosaki",
)

#: People the Learn corpus names (CEOs, founders, the fraud cases) plus the executives a model
#: is most likely to volunteer about those same companies. Full names only where the surname is
#: an ordinary word ("cook", "jobs", "su", "wood", "holmes", "marks").
CORPUS_PEOPLE = (
    "jeff bezos", "andy jassy", "satya nadella", "steve ballmer", "bill gates", "lisa su",
    "pat gelsinger", "tim cook", "steve jobs", "bernard arnault", "morris chang",
    "elizabeth holmes", "sam bankman-fried", "sam bankman fried", "adam neumann",
    "kenneth lay", "ken lay", "jeffrey skilling", "jeff skilling", "andrew fastow",
    "jensen huang", "elon musk", "mark zuckerberg", "sundar pichai", "larry page",
    "sergey brin", "reed hastings", "bob iger", "jim sinegal", "craig jelinek",
    "jamie dimon", "john bogle", "jack bogle", "john templeton", "philip fisher",
    "walter schloss", "burton malkiel", "charles ellis", "john maynard keynes",
    "t. rowe price", "terry smith", "seth klarman", "george soros", "carl icahn",
    "nancy pelosi", "shou chew", "zhang yiming", "lloyd blankfein",
)

#: Surnames distinctive enough to match alone (never ordinary English words).
UNAMBIGUOUS_SURNAMES = (
    "buffett", "munger", "bezos", "nadella", "arnault", "bankman-fried", "dalio", "burry",
    "ackman", "greenblatt", "housel", "kiyosaki", "zuckerberg", "musk", "jassy", "pichai",
    "gelsinger", "sinegal", "jelinek", "fastow", "bogle", "templeton", "klarman", "keynes",
    "schloss", "druckenmiller", "soros", "icahn", "pelosi", "tepper", "einhorn", "peltz",
    "pabrai", "grantham", "cooperman", "gottheimer", "tuberville", "dimon", "ballmer",
    "malkiel", "blankfein",
    # "Bankman" alone catches every separator a model might use ("Bankman Fried", "Bankman–Fried").
    "bankman", "huang", "iger", "ackmann",
)

#: The spellings a model (or a reader) actually types for a denied name. "Warren Buffet" is the
#: most common misspelling on the web, and both of its words are ordinary English.
MISSPELLINGS = (
    "warren buffet", "warren buffets", "cathy wood", "cathie woods", "kathy wood", "kathie wood",
    "ben grahm", "benjamin grahm", "peter lynche", "charlie mungar",
)

#: Surnames of listed people that are ALSO ordinary English words ("graham crackers", "a lynch
#: mob", "wood", "marks", "a buffet", "jobs", "cook"). They cannot join the lower-case lexicon,
#: so `_ambiguous_surname_hits` matches them CASE-SENSITIVELY, and only where the capital is a
#: name's capital: mid-sentence, possessive, after an honorific/initial, or opening a sentence
#: as the subject of a verb ("Graham taught patience.").
AMBIGUOUS_SURNAMES = (
    "Graham", "Lynch", "Wood", "Marks", "Buffet", "Jobs", "Cook", "Gates", "Holmes", "Su", "Chang",
    # Round 2 (W2CB-7): famous surnames that are English words, named alone in an investing
    # post ("Even Newton got burned chasing a bubble", "Knight called it uncertainty", "Smith's
    # invisible hand", "Fisher bought growth").
    "Newton", "Knight", "Smith", "Fisher",
)

#: Ways to point at a real person without naming them.
PERSON_DESCRIPTORS = (
    "oracle of omaha", "sage of omaha", "legendary investor", "billionaire investor",
    "famous investor", "star investor", "superinvestor", "super investor", "investing legend",
    "investing icon", "investing guru", "investment guru", "stock guru", "famed investor",
    "renowned investor", "celebrity investor", "hedge fund legend",
)

#: Brand names that contain a lexicon name as a substring or word. Blanked before the person
#: scan so the brand never reads as the person.
_BRAND_SHIELD = ("merrill lynch", "t. rowe price group")


@lru_cache(maxsize=1)
def _registry_names() -> Tuple[str, ...]:
    """Individuals from the whale registry (investors + politicians; institutions skipped).
    Never raises: a missing file only narrows the lexicon, and is logged."""
    try:
        rows = json.loads((DATA_DIR / "whale_registry.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("marketing compliance: whale_registry.json unreadable (%s: %s)",
                       type(e).__name__, e)
        return ()
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("category") in ("investors", "politicians"):
            name = fold(str(row.get("name") or ""))
            if name and " " in name:
                out.append(name)
    return tuple(out)


@lru_cache(maxsize=1)
def investor_quotes() -> Tuple[Dict[str, str], ...]:
    """The vendored weekly investor quotes (`data/weekly_investor_quotes.json`, byte-parity
    with the iOS bundle). Their AUTHORS join the person lexicon and their TEXT feeds the
    famous-quote n-gram check."""
    try:
        data = json.loads((DATA_DIR / "weekly_investor_quotes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("marketing compliance: weekly_investor_quotes.json unreadable (%s: %s)",
                       type(e).__name__, e)
        return ()
    quotes = data.get("quotes") if isinstance(data, dict) else None
    return tuple(q for q in (quotes or []) if isinstance(q, dict))


#: Generational suffixes dropped when deriving the name as people write it ("Thomas Rowe Price
#: Jr." → "thomas rowe price").
_NAME_SUFFIXES = frozenset({"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"})


def _author_names() -> Tuple[str, ...]:
    out = []
    for q in investor_quotes():
        a = fold(str(q.get("author") or ""))
        if not a:
            continue
        out.append(a)
        words = [p.strip(",") for p in a.split()]
        # "burton g. malkiel" → also "burton malkiel"; "thomas rowe price jr." → also
        # "thomas rowe price" (each alone, and both together).
        no_suffix = [p for p in words if p not in _NAME_SUFFIXES]
        no_initials = [p for p in words if not (len(p) == 2 and p.endswith("."))]
        plain = [p for p in no_suffix if not (len(p) == 2 and p.endswith("."))]
        for parts in (no_suffix, no_initials, plain):
            if len(parts) >= 2:
                out.append(" ".join(parts))
    return tuple(dict.fromkeys(out))


@lru_cache(maxsize=1)
def person_lexicon() -> Tuple[str, ...]:
    return tuple(sorted(set(
        APP_STORE_NAMES + CORPUS_PEOPLE + UNAMBIGUOUS_SURNAMES + PERSON_DESCRIPTORS
        + MISSPELLINGS + _registry_names() + _author_names()
    )))


@lru_cache(maxsize=1)
def _person_re() -> re.Pattern:
    return _phrase_re(person_lexicon())


@lru_cache(maxsize=1)
def _compact_names() -> Tuple[str, ...]:
    """Multi-word names with the spaces removed, for hashtags/handles ("#warrenbuffett")."""
    out = set()
    for name in (APP_STORE_NAMES + CORPUS_PEOPLE + MISSPELLINGS + _registry_names()
                 + _author_names()):
        c = re.sub(r"[^a-z]", "", name)
        if len(c) >= 8:
            out.add(c)
    for s in UNAMBIGUOUS_SURNAMES:
        c = re.sub(r"[^a-z]", "", s)
        if len(c) >= 5:
            out.add(c)
    return tuple(sorted(out))


_TAGLIKE_RE = re.compile(r"[#@][A-Za-z0-9_]{2,60}")


def _person_hits(folded: str, original: str) -> List[str]:
    text = folded
    for brand in _BRAND_SHIELD:
        text = text.replace(brand, " " * len(brand))
    hits = [m.group(0) for m in _person_re().finditer(text)]
    for tag in _TAGLIKE_RE.findall(original or ""):
        compact = re.sub(r"[^a-z]", "", tag.lower())
        for name in _compact_names():
            if name in compact:
                hits.append(tag)
                break
    return hits


#: A hyphenated suffix that turns a name into an adjective ABOUT the person ("Graham-style",
#: "Buffet-like"): still the person. Any other hyphen ("Wood-fired", "Cook-off") is a compound.
_NAME_SUFFIX = r"(?:style|like|esque|inspired|approved|type|ian|ite)"
_AMBIGUOUS_RE = re.compile(
    r"(?<![A-Za-z0-9\-])(" + "|".join(AMBIGUOUS_SURNAMES) + r")('s|'|-" + _NAME_SUFFIX
    + r"(?![A-Za-z0-9]))?(?![A-Za-z0-9\-])"
)
_FIRST_LETTER_RE = re.compile(r"[A-Za-z]")
_PREV_WORD_RE = re.compile(r"(\S+)\s+$")
_NEXT_WORD_RE = re.compile(r"\s+([A-Za-z&][A-Za-z'&]*)")
_HONORIFICS = frozenset({"mr", "mrs", "ms", "dr", "prof", "professor", "sir", "mister"})
#: A capitalised word before the surname that does NOT make it part of another proper noun
#: ("The Graham method", "As Lynch said").
_FUNCTION_WORDS = frozenset({
    "the", "a", "an", "this", "that", "these", "those", "as", "like", "for", "per", "and", "or",
    "but", "so", "when", "while", "if", "because", "then", "even", "with", "by", "from", "to",
    "of", "in", "on", "at", "ask", "let", "after", "before", "unlike", "since", "until", "what",
    "how", "why", "where", "whether", "though", "although", "once", "only", "just", "both",
})
#: The surname opening a phrase that is not the person ("Graham crackers", "Marks & Spencer").
_SURNAME_PHRASES = (
    "graham cracker", "marks & spencer", "marks and spencer", "wood mackenzie", "cook islands",
    "cook the book", "jobs report", "jobs data", "jobs number", "jobs act", "jobs growth",
    "smith & wesson", "smith and wesson", "smith barney", "knight capital", "knight frank",
    "fisher investments",
)
#: A sentence-initial capital is only a name's capital when a verb follows ("Graham taught").
_NAME_VERBS = frozenset({
    "said", "says", "wrote", "writes", "taught", "teaches", "thought", "thinks", "believed",
    "believes", "argued", "argues", "warned", "warns", "noted", "notes", "explained", "explains",
    "called", "calls", "bought", "buys", "sold", "sells", "built", "builds", "ran", "runs",
    "founded", "started", "liked", "likes", "loved", "loves", "preferred", "prefers", "advised",
    "advises", "told", "tells", "put", "puts", "made", "makes", "held", "holds", "kept", "keeps",
    "saw", "sees", "found", "finds", "famously", "once", "also", "later", "never", "always",
    "often", "insisted", "urged", "recommended", "described", "coined", "pioneered",
    "championed", "learned", "studied", "managed", "manages", "invested", "invests", "compared", "emphasized",
    "emphasised", "stressed", "favored", "favoured", "avoided", "avoids", "himself", "herself",
    "who", "knew", "knows", "wanted", "wants", "hated", "hates", "used", "uses", "led", "leads",
})
#: Auxiliaries count only for surnames that are not also everyday nouns: "Graham was patient"
#: is the person, "Wood is renewable" / "Jobs are scarce" are not.
_AUXILIARIES = frozenset({"was", "is", "has", "had", "would", "could", "will", "did", "does"})
_NOUN_SURNAMES = frozenset({"Wood", "Marks", "Jobs", "Cook", "Gates", "Newton", "Knight", "Smith",
                            "Fisher"})


def _ambiguous_surname_hits(text: str) -> List[str]:
    """Bare surnames of listed people that are also English words, told apart from the word by
    their capital and position. `text` is the cleaned, capped original. Linear: one regex pass
    per sentence plus bounded look-arounds per match."""
    hits: List[str] = []
    for sent in sentences(skeleton(text)):
        first = _FIRST_LETTER_RE.search(sent)
        lead = first.start() if first else 0
        for m in _AMBIGUOUS_RE.finditer(sent):
            name = m.group(1)
            if m.group(2):
                hits.append(m.group(0))              # "Graham's rule", "Marks' memos", "Graham-style"
                continue
            pm = _PREV_WORD_RE.search(sent[max(0, m.start() - 30):m.start()])
            prev = pm.group(1).strip("\"'([{") if pm else ""
            if prev:
                if prev.rstrip(".").lower() in _HONORIFICS or re.fullmatch(r"[A-Z]\.", prev):
                    hits.append(name)                # "Mr. Graham", "B. Graham"
                    continue
                if prev[0].isupper() and prev[-1].isalpha() and prev.lower() not in _FUNCTION_WORDS:
                    # Part of another proper noun ("Merrill Lynch", "Sherlock Holmes"), or of a
                    # lexicon name already reported whole ("Peter Lynch").
                    continue
            if any(fold(sent[m.start():m.start() + 40]).startswith(p) for p in _SURNAME_PHRASES):
                continue
            if m.start() > lead:
                hits.append(name)                    # a capital mid-sentence is a name's capital
                continue
            nm = _NEXT_WORD_RE.match(sent, m.end(), m.end() + 30)
            nxt = nm.group(1).lower() if nm else ""
            if (nxt in _NAME_VERBS or (len(nxt) >= 5 and nxt.endswith("ed"))
                    or (nxt in _AUXILIARIES and name not in _NOUN_SURNAMES)):
                hits.append(name)                    # "Graham taught patience."
    return hits


# ── people pointed at without a lexicon name ──────────────────────────────────
#
# The lexicons above work by NAME. A model (or a fact sheet whose naming sentence was dropped)
# can point at the same real person by first name ("Uncle Warren", "Mark renamed Facebook"), by
# first name + a brand surname the sheet never states ("Walt Disney", "Henry Ford"), by epithet
# ("a legendary value investor", "the father of value investing") or by role ("Tesla's own CEO",
# "one man has run LVMH"). Each rule below closes one of those shapes; all report `person_named`.

GIVEN_NAMES_PATH = DATA_DIR / "given_names_en.txt"

#: Never a given name in a name slot here, whatever a names list says: modal verbs, months and
#: headline words ("Will Costco grow?", "May sales", "Max Markup Cap").
_NOT_GIVEN = frozenset({
    "will", "may", "june", "april", "august", "max", "price", "grant", "chase", "hope", "faith",
    "the", "and", "for", "but", "not",
})


@lru_cache(maxsize=1)
def given_names() -> FrozenSet[str]:
    """The vendored given-name list (`data/given_names_en.txt`). Never raises: a missing file
    only narrows the check to the lexicon-derived names, and is logged."""
    try:
        lines = GIVEN_NAMES_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        logger.warning("marketing compliance: %s unreadable (%s: %s)", GIVEN_NAMES_PATH.name,
                       type(e).__name__, e)
        lines = []
    names = {w.strip().lower() for w in lines if w.strip() and not w.lstrip().startswith("#")}
    return frozenset(n for n in names if n.isalpha() and len(n) >= 3) - _NOT_GIVEN


@lru_cache(maxsize=1)
def denied_given_names() -> FrozenSet[str]:
    """First names of everyone the person lexicon lists by full name — the App Store list, the
    corpus people, the misspellings, the whale registry and the quote authors ("warren",
    "peter", "ben", "benjamin", "charlie", "mark", "jeff"). A capitalised one used AS a name
    ("Uncle Warren", "Ben invented Mr. Market") is that person. Initials and two-letter names
    ("t.", "Ro", "Li") are skipped: they would match anything."""
    out = set()
    for name in (APP_STORE_NAMES + CORPUS_PEOPLE + MISSPELLINGS + _registry_names()
                 + _author_names()):
        parts = name.split()
        if len(parts) >= 2 and parts[0].isalpha() and len(parts[0]) >= 3:
            out.add(parts[0])
    return frozenset(out) - _NOT_GIVEN


#: Denied first names that are also everyday nouns: after one of these, an auxiliary is not a
#: person ("Bill is due", "Mark is visible"), and in a Title-Cased line the capital proves nothing.
NOUN_GIVEN_NAMES = frozenset({"bill", "mark", "pat", "ray", "jack", "bob", "chuck", "reed", "ken",
                              "josh", "terry", "morris", "sam", "dan", "tim"})
_NOUN_GIVEN = NOUN_GIVEN_NAMES
#: A word before a first name that makes it a person: kinship, honorifics, age.
_NAME_PREFIXES = frozenset({
    "uncle", "aunt", "auntie", "grandpa", "grandma", "granny", "grandfather", "grandmother",
    "papa", "mama", "brother", "sister", "saint", "mr", "mrs", "ms", "dr", "prof", "professor",
    "sir", "dame", "mister", "young", "old", "little", "dear", "cousin", "father", "mother",
})
#: A title or trade written in front of a name ("Economist Frank Knight", "investor Warren"):
#: the name after it is a person, whatever the surname is (round-2 W2CB-7).
TITLE_WORDS = frozenset({
    "economist", "professor", "investor", "historian", "physicist", "scientist", "author",
    "writer", "banker", "trader", "analyst", "billionaire", "mathematician", "philosopher",
    "statesman", "senator", "financier", "entrepreneur", "founder", "legendary", "famed",
    "famous", "novelist", "journalist", "engineer", "inventor", "chairman", "ceo", "manager",
    "king", "queen", "prince", "princess", "emperor", "empress", "lord", "lady", "duke",
    "duchess", "pope", "general", "president", "governor",
})
_NAME_PREFIXES = _NAME_PREFIXES | TITLE_WORDS
#: Brands built on a first name, blanked before the first-name scan ("Sam's Club").
_GIVEN_BRAND_SHIELD_RE = re.compile(
    r"(?i)\b(?:sam's club|ben (?:&|and) jerry's|carl's jr|tim hortons|trader joe's|wendy's|"
    r"dave (?:&|and) buster's|mark's work wearhouse|uncle ben's|aunt jemima|uncle sam|"
    r"papa john's|victoria's secret|casey's|denny's|saint laurent|little caesars)"
)
_GIVEN_CAND_RE = re.compile(
    r"(?<![A-Za-z0-9'\-])([A-Z][a-z]{2,})('s|'|-" + _NAME_SUFFIX
    + r"(?![A-Za-z0-9]))?(?![A-Za-z0-9\-])"
)
#: Verbs only a PERSON does — "Henry Ford said", "Walt Disney founded", but not "Louis Vuitton
#: merged with Moët Hennessy" (a company merges) or "Louis Vuitton began making trunks".
_HUMAN_VERBS = frozenset({
    "said", "says", "wrote", "writes", "taught", "teaches", "thought", "thinks", "believed",
    "believes", "argued", "argues", "told", "tells", "invented", "dreamed", "dreamt", "died",
    "retired", "married", "learned", "studied", "hated", "loved", "liked", "wanted", "insisted",
    "joked", "quipped", "admitted", "recalled", "founded", "cofounded", "co-founded", "himself",
    "herself", "famously", "personally", "remembered", "worried", "feared", "hoped", "vowed",
    # Round 2 (W2CB-7): "Frank Knight called it true uncertainty".
    "called", "calls", "coined", "dubbed", "labeled", "labelled", "warned", "observed",
    "explained", "lectured", "preached", "mused", "wondered", "distinguished", "defined",
})
#: Zero-width (a lookahead) so every capitalised word is tried as a first name: a consuming
#: match of "Young Louis" would hide "Louis Vuitton" behind it. Each attempt is bounded.
_FULL_NAME_RE = re.compile(
    r"(?<![A-Za-z0-9'\-])(?=([A-Z][a-z]{2,})\s+([A-Z][a-z]+|[A-Z]\.)(?:'s)?(?:\s+([a-z][a-z-]*))?)"
)
_WORD3_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _title_cased(sent: str) -> bool:
    """A headline ("Pay the Bill First"): most content words capitalised, so a capital proves
    nothing about a name."""
    words = [w for w in _WORD3_RE.findall(sent) if len(w) >= 3 and w.lower() not in _FUNCTION_WORDS]
    if len(words) < 3:
        return False
    return sum(1 for w in words if w[0].isupper()) * 10 >= len(words) * 7


#: After a denied first name, punctuation is skipped on the way to the next word, so "Warren,
#: famously, loved moats" reaches "famously" (round-2 W2CB-7).
_NEXT_WORD_PUNCT_RE = re.compile(r"[\s,;:]+([A-Za-z&][A-Za-z'&]*)")


def _is_company_word(low: str, company_terms: FrozenSet[str]) -> bool:
    lex = company_lexicon()
    return (low in company_terms or low in lex.distinct or low.capitalize() in lex.words
            or low.upper() in lex.caps)


def _given_name_hits(text: str, strict: bool = False,
                     company_terms: FrozenSet[str] = frozenset()) -> List[str]:
    """First names used as a name. `text` is the SKELETON (case kept). Two rules:

    1. A DENIED first name (`denied_given_names`) by position, like `_ambiguous_surname_hits`:
       after a kinship word/honorific ("Uncle Warren"), possessive or "-style" ("Warren's
       habit"), mid-sentence ("As Warren likes to say"), or opening a sentence as the subject of
       a verb ("Ben invented Mr. Market", "Warren would call this patience"). Followed by a
       capitalised word it is a full name, which rule 2, the lexicon and grounding's pair rule
       judge instead.
    2. ANY first name (`given_names`) + a capitalised surname, then a verb only a person does
       ("Henry Ford said", "Walt Disney founded") or after an age/kinship word ("Young Louis
       Vuitton") — a founder's name used as the founder even where the sheet names the brand.

    Round 2 (W2CB-6/7): ANY first name after a kinship word or honorific ("Uncle Walt"); a
    denied first name opening a sentence before "and", "&" or a comma ("Warren and his partner
    loved moats"); and, in a Money Moves case study (`strict`), any first name as a possessive
    ("Henry's assembly line") or opening a sentence as the subject of a verb ("Walt built an
    empire") unless it is a company (the lexicon, or one of the item's own `company_terms`).

    Linear: one pass per sentence, set lookups, bounded look-arounds."""
    denied, given = denied_given_names(), given_names()
    hits: List[str] = []
    text = _GIVEN_BRAND_SHIELD_RE.sub(lambda m: " " * len(m.group(0)), text)
    for sent in sentences(text):
        first = _FIRST_LETTER_RE.search(sent)
        lead = first.start() if first else 0
        title: List[bool] = []           # computed at most once per sentence (keeps it linear)
        for m in _GIVEN_CAND_RE.finditer(sent):
            low = m.group(1).lower()
            if low not in denied:
                if low not in given or _is_company_word(low, company_terms):
                    continue
                pm = _PREV_WORD_RE.search(sent[max(0, m.start() - 30):m.start()])
                raw_prev = pm.group(1).strip("\"'([{").rstrip(".") if pm else ""
                prev = raw_prev.lower()
                nm = _NEXT_WORD_RE.match(sent, m.end(), m.end() + 30)
                nxt = nm.group(1) if nm else ""
                if prev in _NAME_PREFIXES and not nxt[:1].isupper():
                    hits.append(m.group(0))          # "Uncle Walt", "Young Henry"
                elif strict and not nxt[:1].isupper() and not (
                        raw_prev[:1].isupper() and prev not in _FUNCTION_WORDS):
                    if m.group(2) == "'s":
                        hits.append(m.group(0))      # "Henry's assembly line" (Money Moves)
                    elif m.start() <= lead and not m.group(2) and nxt.lower() in _NAME_VERBS:
                        hits.append(m.group(1))      # "Walt built an empire" (Money Moves)
                continue
            pm = _PREV_WORD_RE.search(sent[max(0, m.start() - 30):m.start()])
            prev = pm.group(1).strip("\"'([{") if pm else ""
            if prev.rstrip(".").lower() in _NAME_PREFIXES:
                hits.append(m.group(0))              # "Uncle Warren", "Mr. Warren"
                continue
            if m.group(2):
                hits.append(m.group(0))              # "Warren's", "Warren-style"
                continue
            nm = _NEXT_WORD_PUNCT_RE.match(sent, m.end(), m.end() + 30)
            nxt = nm.group(1) if nm else ""
            if nxt[:1].isupper() and sent[m.end():nm.start(1)].strip() == "":
                continue                             # a full name: rule 2 / lexicon / grounding
            if (m.start() <= lead and low not in _NOUN_GIVEN
                    and (sent[m.end():m.end() + 1] == "," or nxt.lower() in ("and", "&"))):
                hits.append(m.group(1))              # "Warren and his partner", "Warren, famously"
                continue
            if prev and prev[0].isupper() and prev.rstrip(".").lower() not in _FUNCTION_WORDS:
                continue                             # part of another proper noun ("Union Jack")
            if m.start() > lead:
                if low in _NOUN_GIVEN:
                    if not title:
                        title.append(_title_cased(sent))
                    if title[0]:
                        continue                     # "Pay the Bill First"
                hits.append(m.group(1))              # "As Warren likes to say"
                continue
            nlow = nxt.lower()
            if (nlow in _NAME_VERBS or (len(nlow) >= 5 and nlow.endswith("ed"))
                    or (nlow in _AUXILIARIES and low not in _NOUN_GIVEN)):
                hits.append(m.group(1))              # "Ben invented", "Warren would"
        mentions: List[List[Tuple[str, int, int]]] = []   # at most once per sentence
        for m in _FULL_NAME_RE.finditer(sent):
            if m.group(1).lower() not in given:
                continue
            pm = _PREV_WORD_RE.search(sent[max(0, m.start() - 30):m.start()])
            prev = pm.group(1).strip("\"'([{").rstrip(".").lower() if pm else ""
            verb = (m.group(3) or "").lower()
            if prev in _NAME_PREFIXES or verb in _HUMAN_VERBS or _title_case_person(
                    sent, m, title, mentions, company_terms):
                hits.append(f"{m.group(1)} {m.group(2)}")
    return hits


#: Round 4 (residual f): a Title-Case headline capitalises the verb after a name too, so rule 2's
#: lower-case verb slot never saw it ("Why Frank Knight Mattered", "What Frank Knight Taught
#: Investors"). The capitalised verbs rule 2 accepts there: a verb only a person does, plus the
#: headline verbs any subject takes (`_TITLE_ANY_SUBJECT_VERBS`) — never a participle a headline
#: uses as a noun-phrase SUFFIX ("Grace Period Explained", "Pearl Harbor Remembered", "Lessons
#: Learned").
#: Round 5: the present tense ("Why Frank Knight Matters", "How Frank Knight Shapes Risk",
#: "Frank Knight Warns Investors") — the round-4 list had "mattered" only.
_TITLE_ANY_SUBJECT_VERBS = frozenset({"matters", "mattered", "shapes", "shaped"})
_TITLE_PERSON_VERBS = (_HUMAN_VERBS - frozenset({
    "explained", "defined", "remembered", "recalled", "observed", "labeled", "labelled",
    "distinguished", "studied", "learned",
})) | frozenset({"warns", "explains", "invents"}) | _TITLE_ANY_SUBJECT_VERBS
#: Given names that are also everyday words — a noun, adjective or verb that opens a headline
#: noun phrase ("Why Angel Funding Mattered", "Why Frank Talk Matters", "Why Grace Periods
#: Mattered", "What Angel Investors Taught Founders"). Round 4 read each as a person. After one
#: of these, the capitalised word must be a KNOWN surname (`known_surnames`) — "Frank Knight" is.
_WORD_GIVEN = frozenset({
    "amber", "angel", "art", "august", "autumn", "basil", "bishop", "bud", "buddy", "candy",
    "carol", "cash", "chance", "clay", "coral", "crystal", "dale", "dawn", "dean", "don", "duke",
    "dusty", "earl", "frank", "gene", "ginger", "glen", "grace", "guy", "hank", "harry", "hazel",
    "heather", "herb", "holly", "homer", "honey", "hunter", "iris", "ivy", "jade", "jay", "jean",
    "jimmy", "joy", "king", "kit", "lance", "lee", "lily", "lucky", "major", "martin", "mason",
    "mike", "misty", "olive", "pearl", "penny", "pony", "prince", "randy", "rich", "rick", "rob",
    "robin", "rocky", "rose", "rosemary", "ruby", "rusty", "sage", "sally", "sandy", "sherry",
    "sky", "sol", "sonny", "star", "sterling", "storm", "sue", "summer", "toby", "tom", "tony",
    "troy", "victor", "viola", "violet", "wade", "warren",
})


@lru_cache(maxsize=1)
def known_surnames() -> FrozenSet[str]:
    """Lower-case surnames of every listed person (the App Store list, the corpus people, the
    misspellings, the whale registry, the quote authors) plus the everyday-word surnames the
    ambiguous-surname rules know ("knight", "wood", "cook"). Never raises."""
    out = {s.lower() for s in AMBIGUOUS_SURNAMES} | {s.lower() for s in _NOUN_SURNAMES}
    for name in (APP_STORE_NAMES + CORPUS_PEOPLE + MISSPELLINGS + _registry_names()
                 + _author_names()):
        parts = name.split()
        if len(parts) >= 2 and parts[-1].isalpha() and len(parts[-1]) >= 3:
            out.add(parts[-1].lower())
    return frozenset(out)


def _title_case_person(sent: str, m: "re.Match[str]", title: List[bool],
                       mentions: List[List[Tuple[str, int, int]]],
                       company_terms: FrozenSet[str]) -> bool:
    """In a Title-Case line, first name + capitalised surname + a CAPITALISED person verb from
    `_TITLE_PERSON_VERBS` is a full name ("Why Frank Knight Mattered"). Narrow on purpose — the
    semantic judge is the main gate for a person described in a headline: never a noun first
    name ("Mark Your Calendar", "Pay the Bill First"), an initial or a possessive for a surname,
    and never a pair the company scan reads as a company ("Why Louis Vuitton Mattered", "Why
    Charles Schwab Mattered") or whose words are the item's own company terms. Round 5: a verb
    ANY subject takes ("Matters", "Shapes": "Why Henry Hub Matters", "Why Kelly Criterion
    Mattered") or a first name that is an everyday word (`_WORD_GIVEN`: "Why Angel Funding
    Mattered") needs a KNOWN surname ("Why Frank Knight Matters"). `title` and `mentions`
    memoise the sentence's Title-Case test and company scan (each at most once)."""
    first, sur = m.group(1), m.group(2)
    if sur.endswith(".") or first.lower() in _NOUN_GIVEN:
        return False
    nm = _NEXT_WORD_RE.match(sent, m.end(2), m.end(2) + 30)
    if nm is None or not nm.group(1)[:1].isupper() \
            or nm.group(1).lower() not in _TITLE_PERSON_VERBS:
        return False
    if ((nm.group(1).lower() in _TITLE_ANY_SUBJECT_VERBS or first.lower() in _WORD_GIVEN)
            and sur.lower() not in known_surnames()):
        return False
    if not title:
        title.append(_title_cased(sent))
    if not title[0]:
        return False
    if (_is_company_word(first.lower(), company_terms)
            or _is_company_word(sur.lower(), company_terms)):
        return False
    if not mentions:
        mentions.append(sentence_company_mentions(sent))
    a, b = m.start(1), m.end(2)
    return not any(s < b and a < e for _n, s, e in mentions[0])


#: A fame word + a person noun, singular (a plural "great investors" is generic teaching).
_FAME = (r"(?:famous|famed|legendary|well[- ]known|best[- ]known|greatest|renowned|celebrated|"
         r"billionaire|star|superstar|iconic|storied|revered|world[- ]famous|richest|wealthiest|"
         r"most (?:famous|successful|celebrated|admired|respected|influential))")
#: No "icon": "a legendary Kirkland icon" is a product. "investing icon" is the row below.
_PERSON_NOUN = (r"(?:investor|money manager|fund manager|stock ?picker|trader|speculator|"
                r"financier|billionaire|capitalist|tycoon|mogul|legend|guru|businessman|"
                r"businesswoman|entrepreneur|economist|ceo|founder|executive|banker|analyst|"
                r"professor|philanthropist|chairman|dealmaker|short[- ]seller|oracle|sage|"
                # Round 2 (W2CB-6): "the richest man in France", "its billionaire owner".
                r"man|woman|person|owner)")
_DESCRIPTOR_RES = (
    re.compile(r"\b" + _FAME + r"(?: [a-z-]+){0,2} " + _PERSON_NOUN + r"(?![a-z-])"),
    re.compile(r"\b(?:investing|investment|stock[- ]market|wall street|finance|value|money|"
               r"business) (?:legend|icon|guru|genius|wizard|sage|oracle|hero|superstar|god)"
               r"(?![a-z-])"),
    re.compile(r"\b(?:god)?(?:father|mother|grandfather|dean|pope|prophet) of (?:[a-z-]+ ){0,2}"
               r"(?:investing|investment|investors|wall street|indexing|index funds?|finance|"
               r"the stock market|security analysis|portfolio theory)\b"),
    re.compile(r"\bthe (?:oracle|sage|wizard|seer|prophet) of [a-z]+\b"),
    re.compile(r"\bthe (?:oracle|sage)\b(?! (?:database|databases|cloud|software|corporation|"
               r"corp|stock|shares|advice|words|counsel))"),
    # "the man who taught value investing", "invented by a professor": a person described by
    # what they did to the idea the post teaches.
    re.compile(r"\bthe (?:man|woman|guy|person) who (?:taught|invented|created|coined|founded|"
               r"wrote|pioneered|popularized|popularised|dreamed up|came up with)\b"),
    re.compile(r"\b(?:invented|created|coined|popularized|popularised|pioneered|dreamed up|"
               r"devised|described) by (?:a|an|the|one) (?:[a-z-]+ ){0,3}(?:man|woman|investor|"
               r"professor|teacher|economist|writer|author|mentor|legend|trader|banker)\b"),
    re.compile(r"\bone of (?:the|history's|wall street's|the world's|america's) (?:most (?:famous|"
               r"successful|celebrated|admired|respected|influential)|greatest|great|best|"
               r"best[- ]known|richest|wealthiest|top|legendary|world's (?:best|greatest|richest|"
               r"wealthiest)) "
               r"(?:[a-z-]+ ){0,2}(?:investors|money managers|fund managers|stock ?pickers|traders|"
               r"billionaires|ceos|founders|businessmen|entrepreneurs)\b"),
    # ── round 2 (W2CB-7): the same person, in the shapes rows 0-7 never listed ──
    # A field's founder by possessive: "Value investing's founding father", "Mr. Market's
    # creator", "Tesla's creator". "TikTok's creator fund" is a product.
    re.compile(r"\b(?:investing|investment|value investing|index investing|indexing|index funds?|"
               r"wall street|finance|economics|modern finance|behavioral finance|growth investing|"
               r"security analysis|the stock market|the index fund)'s (?:founding |original |great |"
               r"first |true )?(?:father|mother|godfather|grandfather|creator|inventor|pioneer|dean|"
               r"pope|architect|guru|prophet|author|originator)\b"
               r"|'s (?:creator|inventor|originator|mastermind|coiner)\b(?!s|\s+(?:fund|funds|"
               r"economy|program|programs|tools?|marketplace|studio|app|account|accounts|platform|"
               r"community|payouts?|content|tier|badge|rewards?)\b)"),
    # Subject-first: "A Columbia professor invented Mr. Market", "An economist named this idea
    # long ago". Singular only — "economists call this opportunity cost" is teaching — and never
    # "an investor named Sam" (a hypothetical, named on the spot).
    re.compile(r"\b(?:a|an|the|one) (?:[a-z-]+ ){0,2}(?:professor|economist|investor|physicist|"
               r"teacher|scientist|mathematician|banker|trader|analyst|writer|author|philosopher|"
               r"statistician|money manager|fund manager) (?:once |first |famously |later )?"
               r"(?:invented|created|coined|devised|dreamed up|came up with|introduced|popularized|"
               r"popularised|pioneered|first described|named (?:this|it|the (?:idea|concept|effect|"
               r"phenomenon|rule)))\b"),
    re.compile(r"\bthe (?:creator|inventor|originator|father|author|mastermind) of (?:mr\.? market|"
               r"value investing|the index fund|index funds|indexing|the margin of safety|margin of "
               r"safety|the moat idea|economic moats)\b"),
    # Round 3 (W3VAC-01): the plural wealth epithet with "one of" — "LVMH's owner is one of the
    # richest people in the world", "made its owner one of the wealthiest men alive". Always one
    # real person; "The richest people let time do the work" (no "one of") stays generic.
    re.compile(r"\bone of (?:the |the world's |the planet's |europe's |france's |america's |asia's |"
               r"history's |its |their )?(?:[a-z-]+ )?(?:richest|wealthiest) (?:people|men|women|"
               r"individuals|persons)\b"),
)
#: "The author of The Intelligent Investor created Mr. Market": a person pointed at by a TITLE
#: (case-sensitive, on the skeleton — the capital is what makes it a title).
_AUTHOR_OF_TITLE_RE = re.compile(r"\b[Tt]he (?:author|writer|creator|inventor|originator) of "
                                 r"(?:[Tt]he |[Aa]n? )?[A-Z][a-z]")
#: A singular role that, in a case study about named companies, is one real person: "Tesla's
#: own CEO", "the founder", "a new chief executive". Plurals ("executives", "founders") and
#: compounds ("founder-led") are generic and stay allowed. Money Moves (strict) only: a Journey
#: lesson names no company, and "read the CEO's letter" is exactly what it teaches.
#: No copula right before (the W3OB-2 guard for a head a thing can hold).
_NOT_AFTER_COPULA = (r"(?<!\bis )(?<!\bwas )(?<!\bare )(?<!\bwere )(?<!\bbecame )(?<!\bbecomes )"
                     r"(?<!\bbecome )(?<!\bremains )(?<!\bremained )(?<!\bbe )(?<!\bbeing )"
                     r"(?<!'s )(?<!\bproved )(?<!\bseems like )(?<!\bacts as )(?<!\bas )")
_ROLE_RE = re.compile(
    r"\b(?:ceo|chief executive(?: officer)?|chief financial officer|chief operating officer|"
    r"cfo|founder|co-?founder|chair(?:man|woman|person)|boss|president|heir(?:ess)?|"
    r"patriarch|matriarch|mogul|tycoon)(?![a-z-])"
    r"|\bchair(?= of\b)|\b(?:board|company's|firm's) chair\b(?![a-z-])"
    r"|\b(?<!no )(?<!any )(?<!every )(?:one|a single|just one|a lone) (?:man|woman|person|"
    r"individual|guy)\b(?!'s)"
    r"|\bthe (?:man|woman|guy) (?:who|behind|that)\b"
    # Round 2 (W2CB-6): "the person/mind behind Tesla", "the man at the top", "the person in
    # charge", "its top executive", "the executive who".
    # Round 3 (W3OB-2): a head noun a THING can hold too ("force", "brain", "one") is a person
    # only in SUBJECT position — after a copula it describes the copula's subject ("Scale is the
    # force behind the flywheel", "Your bank is the one in charge of the loan"), and a person
    # there is caught by its own row (a pronoun, a role noun, a name). "The force behind Tesla
    # slept on the factory floor" stays a person.
    r"|\bthe (?:person|mind|genius|visionary) behind\b"
    r"|" + _NOT_AFTER_COPULA + r"\bthe (?:(?:driving|real|true|main|guiding|creative)\s+)?"
    r"(?:force|brain|brains) behind\b"
    r"|\bthe (?:mind|genius|visionary) (?:who|that)\b"
    r"|" + _NOT_AFTER_COPULA + r"\bthe (?:brain|brains) (?:who|that)\b"
    # Round 3 (W3CB-10): "The man running Amazon" — any object after "running".
    r"|\bthe (?:man|woman|person|guy) (?:at the top|in charge|at the helm|running|calling the "
    r"shots)\b"
    r"|" + _NOT_AFTER_COPULA + r"\bthe one (?:at the top|in charge|at the helm|running (?:the|it)|"
    r"calling the shots)\b"
    # Round 3 (W3CB-10): a single person by trade, at the helm ("The engineer behind NVIDIA",
    # "The engineer who runs NVIDIA", "the executive who turned Microsoft around"). Human-only
    # trades: "newcomer", "leader", "outsider", "insider" and "architect" are also said of a
    # COMPANY ("a newcomer who took over the market").
    r"|\b(?:the|a|an) (?:[a-z-]+\s+){0,3}?(?:engineer|"
    r"executive|frontman|frontwoman|entrepreneur|salesman|saleswoman|programmer|designer|inventor|"
    r"scientist|physicist|banker|lawyer|accountant|dropout|immigrant|billionaire) (?:behind|"
    r"running|who (?:runs|ran|leads|led|took over|took charge of|turned around|turned|built|"
    r"rebuilt|saved|transformed|steered|reinvented|bet)|(?:is|was|became) the (?:(?:driving|"
    r"real|true|main|guiding|creative)\s+)?(?:force|brain|brains|mind|genius|visionary) behind)\b"
    # "The architect of Microsoft's turnaround" — a turnaround-type noun only: "the architect of
    # Costco's model was the membership fee".
    r"|\bthe (?:chief\s+)?architect of (?:its|their|[a-z0-9&.-]{1,40}'s) (?:[a-z-]+ )?(?:turnaround|"
    r"comeback|revival|pivot|reinvention|transformation|rebirth|renaissance|resurgence|rescue)\b"
    # "A new leader took over Microsoft and bet on Azure".
    r"|\b(?:a|the) new (?:leader|boss|chief|head|chief executive|captain|helmsman) (?:took over|"
    r"took charge|stepped in|arrived|came in|was appointed|was named|turned|bet|reframed|pushed|"
    r"steered)\b"
    # The owner token is bounded (round 2, W2-OB-7 sweep): an unbounded one restarted from every
    # word boundary of an unspaced run ("u.s.u.s.…") and went quadratic.
    r"|\b(?:its|their|the company's|the firm's|[a-z0-9&.-]{1,40}'s) top (?:executive|boss|dog|brass)\b"
    r"|\bthe executive (?:who|behind)\b"
    # A singular founder described by a trade: "A former hedge fund analyst started Amazon",
    # "The engineer who cofounded NVIDIA". Plurals ("engineers built") stay generic.
    r"|\b(?:a|an|the|one) (?:[a-z-]+ ){0,3}?(?:analyst|engineer|entrepreneur|designer|programmer|"
    r"student|dropout|salesman|saleswoman|immigrant|inventor|scientist|physicist|chemist|"
    r"accountant|lawyer|banker|trader|hacker|tinkerer|teenager|professor|executive|visionary) "
    r"(?:who )?(?:co-?founded|founded|started|launched|created|dreamed up)\b"
)
#: A company's singular role holder in ROLE position — the possessive/its + role noun is followed
#: by a verb, an adverb or the end of the clause: "Tesla's chief once slept on the factory floor",
#: "Its longtime leader bet the company on CUDA", "The company's head bet everything". Not
#: "Costco's chief advantage", "its head start", "the industry's leader" (a company).
_ROLE_POSITION_RE = re.compile(
    r"(?<!\bon )(?<!\bover )(?<!\babove )(?<!\bupon )\b([a-z0-9&.-]{1,40}'s|its|their)\s+(?:[a-z-]+\s+){0,2}?(chief|leader|head|captain|"
    r"helmsman|visionary|mastermind|figurehead|boss|frontman|frontwoman|"
    # Round 3 (W3VAC-01, W3CB-10): the COMPOUND shareholder role ("Meta's controlling
    # shareholder", "its largest shareholder"). Never a bare "owner" or "shareholder": "A luxury
    # brand can be destroyed by its owner" is the LVMH sheet's own thesis, and "Instagram's
    # owner" is Meta.
    r"(?:controlling|largest|biggest|main|majority|principal|dominant) (?:shareholder|"
    r"stockholder|owner))(?=\s*[,.;:!?]|\s*$|\s+(?:once|also|"
    r"then|later|famously|still|long|had|has|was|is|would|could|will|did|does|bet|built|ran|led|"
    r"made|took|kept|saw|said|told|sold|bought|grew|gave|won|lost|put|set|spent|sent|thought|knew|"
    r"left|went|came|became|began|wrote|drove|chose|held|met|paid|quit|rose|shut|slept|spoke|"
    r"split|stood|struck|swore|taught|threw|woke|says|wants|thinks|believes|runs|leads|makes|"
    r"takes|keeps|sees|calls|sleeps|bets|builds|owns|controls|sets|holds|likes|loves|hates|tells|"
    r"argues|insists|admits|[a-z]+ed)\b)"
)
_GENERIC_POSSESSOR = frozenset({
    "industry's", "market's", "sector's", "world's", "country's", "nation's", "category's",
    "field's", "segment's", "pack's", "group's", "team's", "game's", "era's", "decade's",
    "year's", "week's", "day's", "today's", "investor's", "reader's", "customer's",
})
#: Third-person singular pronouns: in a Money Moves case study they can only be a real person
#: (the orphans of a dropped naming sentence: "What he actually bought.").
_PRONOUN_RE = re.compile(r"\b(?:he|him|his|himself|she|her|hers|herself)\b")
#: A NAMED company's role holder, in every mode ("Apple's CEO" names nobody but is one person;
#: in Journey a word-brand like "Apple" passes grounding as vocabulary). Case-sensitive, on the
#: skeleton: the capital is what makes it a name.
#: The owner token is BOUNDED (round 2, W2-OB-7): unbounded, a word boundary before every letter
#: of an unspaced run ("U.S.U.S.…", "A-A-A-…") restarted a greedy scan to the end of the run —
#: quadratic, 63 ms per 6,000-character field. No company name is longer than 40 characters.
_POSSESSIVE_ROLE_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&.\-]{0,40})'s? (?:[a-z-]+ ){0,1}(?:CEO|[Cc]hief [Ee]xecutive|[Ff]ounder|"
    r"[Cc]o-?[Ff]ounder|[Cc]hair(?:man|woman|person)?|[Bb]oss|[Pp]resident|[Hh]eir(?:ess)?)"
    r"(?![A-Za-z-])"
)
_NOT_AN_OWNER = frozenset({"it", "its", "one", "someone", "anyone", "everyone", "nobody",
                           "company", "business", "firm", "today", "yesterday", "year", "market"})
#: The Fed chair is one real person in any post.
_FED_CHAIR_RE = re.compile(r"\bfed(?:eral reserve)?(?: board)? (?:chair|chairman|chairwoman|"
                           r"chairperson|chief|governor|president)(?![a-z-])")


#: A maker noun whose holder may be a COMPANY ("TikTok's creator, ByteDance"): skipped when a
#: company's name follows it (`_names_a_company_next`).
_MAKER_NOUNS = ("creator", "inventor", "originator", "mastermind", "coiner")


#: Possessors that are a company whatever their word: a role held under them at the end of a
#: clause is its one real holder ("Everyone trusted the company's head.").
_COMPANY_POSSESSORS = frozenset({"its", "their", "company's", "firm's", "business's",
                                 "corporation's", "conglomerate's", "retailer's", "chipmaker's",
                                 "automaker's", "carmaker's", "startup's", "brand's"})
_CLAUSE_END_RE = re.compile(r"\s*(?:[,.;:!?]|$)")


def _possessor_is_a_company(owner: str) -> bool:
    """`owner` ("tesla's", "its", "streaming's") names a company: a pronoun or company noun, or a
    LEXICON company — never a market ("streaming's", "luxury's", "ai's"), whose "leader" at the
    end of a clause is a company (W3OB-2). Not the item's `company_terms`: they carry common
    words a sheet capitalised ("ai", "tv", "store", "ceo")."""
    if owner in _COMPANY_POSSESSORS:
        return True
    tok = owner[:-2] if owner.endswith("'s") else owner
    return _is_company_word(tok, frozenset()) or tok in lex_first_tokens()


def _described_person_hits(folded: str, sk: str, strict: bool) -> List[str]:
    hits = [m.group(0) for rx in _DESCRIPTOR_RES for m in rx.finditer(folded)
            if not (m.group(0).endswith(_MAKER_NOUNS) and _names_a_company_next(folded, m.end()))]
    hits += [m.group(0) for m in _FED_CHAIR_RE.finditer(folded)]
    hits += [m.group(0) for m in _AUTHOR_OF_TITLE_RE.finditer(sk)]
    for m in _POSSESSIVE_ROLE_RE.finditer(sk):
        if m.group(1).lower() not in _NOT_AN_OWNER:
            hits.append(m.group(0))
    if strict:
        hits += [m.group(0) for m in _ROLE_RE.finditer(folded)]
        hits += [m.group(0) for m in _PRONOUN_RE.finditer(folded)]
        for m in _ROLE_POSITION_RE.finditer(folded):
            if m.group(1) in _GENERIC_POSSESSOR or _names_a_company_next(folded, m.end()):
                continue
            # At the END of a clause the role has no verb to make it a person: "Netflix became
            # streaming's leader" is a company. A company's own role holder there still is one
            # ("Everyone trusted its leader.", "Tesla's leader."). Followed by a verb it is a
            # person whoever the possessor ("AI's leader bet everything on CUDA").
            if (_CLAUSE_END_RE.match(folded, m.end())
                    and not _possessor_is_a_company(m.group(1))):
                continue
            hits.append(m.group(0))
    return hits


def _names_a_company_next(folded: str, pos: int) -> bool:
    """The role noun at `folded[:pos]` is followed by the NAME of a company ("TikTok's creator,
    ByteDance, …", "Instagram's owner Meta"): the role holder is that company, not a person."""
    m = _NEXT_NAME_RE.match(folded, pos, min(len(folded), pos + 40))
    if not m:
        return False
    tok = m.group(1)
    lex = company_lexicon()
    return (tok in lex.distinct or tok.capitalize() in lex.words or tok.upper() in lex.caps
            or tok in lex_first_tokens())


_NEXT_NAME_RE = re.compile(r"\s*,?\s*([a-z0-9&]+)")


@lru_cache(maxsize=1)
def lex_first_tokens() -> FrozenSet[str]:
    return frozenset(k[0].lower() for k in company_lexicon().multi)


@lru_cache(maxsize=1)
def _quote_ngrams() -> FrozenSet[Tuple[str, ...]]:
    grams = set()
    for q in investor_quotes():
        words = re.findall(r"[a-z0-9']+", fold(str(q.get("text") or "")).replace("'", ""))
        for i in range(len(words) - 5):
            grams.add(tuple(words[i:i + 6]))
    return frozenset(grams)


def _ngram_quote_hit(folded: str) -> str:
    words = re.findall(r"[a-z0-9']+", folded.replace("'", ""))
    grams = _quote_ngrams()
    for i in range(len(words) - 5):
        g = tuple(words[i:i + 6])
        if g in grams:
            return " ".join(g)
    return ""


#: The best-known investing sayings that are NOT in the vendored weekly quotes, as signature
#: phrases (folded, word-bounded). Each is specific enough that ordinary teaching does not say it
#: by accident. "when others are fearful" alone is deliberately absent: journey:fomo_cycle
#: teaches "bought when others are fearful, not when everyone is cheering". Concept NAMES
#: ("Mr. Market", "margin of safety", "circle of competence") are lessons, not quotations.
FAMOUS_SAYINGS = (
    "greedy when others", "fearful when others are greedy", "swimming naked",
    "when the tide goes out", "voting machine", "weighing machine", "price is what you pay",
    "value is what you get", "holding period is forever", "risk comes from not knowing",
    "buy the haystack", "needle in the haystack", "rule number one is never",
    "wonderful company at a fair price", "know what you own", "buy what you know",
    "invest in what you know", "time in the market beats timing",
    "time in the market is more important", "the trend is your friend",
    "let your winners run", "pigs get slaughtered", "bulls make money",
    "the market votes", "the market weighs", "market is a voting", "market is a weighing",
    "any idiot can run", "temperament, not intellect", "big money is not in the buying",
    "the big money is in the waiting", "turns over the most rocks", "illustrate with a crayon",
)
#: The full text of the classics above, for the order-insensitive clause check below.
_FAMOUS_SAYING_TEXTS = (
    "Price is what you pay; value is what you get.",
    "In the short run, the market is a voting machine but in the long run it is a weighing machine.",
    "Our favorite holding period is forever.",
    "Risk comes from not knowing what you are doing.",
    "Only when the tide goes out do you discover who has been swimming naked.",
    "Be fearful when others are greedy and greedy when others are fearful.",
    "Wide diversification is only required when investors do not understand what they are doing.",
    "Go for a business that any idiot can run, because sooner or later any idiot probably is going to run it.",
    "The most important quality for an investor is temperament, not intellect.",
    "The big money is not in the buying or the selling, but in the waiting.",
    "Know what you own, and know why you own it.",
    "Time in the market beats timing the market.",
)
_SAYINGS_RE = _phrase_re(FAMOUS_SAYINGS)
_COMPARISON_SYMBOLS = (">", "\u2192", "\u27f6", "\u21d2", "\u226b", "->", "=>")
_COMPARISON_RE = re.compile(r"\s*(?:>>|=>|->|>|\u2192|\u27f6|\u21d2|\u226b)\s*")
#: The four or five chiastic classics as SHAPES, not wording (round-2 W2CB-10): a one-word
#: paraphrase ("fearful when everyone else is greedy", "What you pay is the price; what you get
#: is the value") defeated the signature phrases, and their clauses are too short for the
#: overlap check. Each needs the rhetorical structure, so "bought when others are fearful"
#: (journey:fomo_cycle) and "Patient investors let others be greedy or fearful" stay teaching.
_SAYING_SHAPE_RES = (
    re.compile(r"\b(?:fearful|greedy)\s+when\s+(?:others|everyone(?: else)?|everybody(?: else)?|"
               r"the crowd|the herd|most people|other people|other investors|the rest|the market)\s+"
               r"(?:is|are|gets?|turns?|grows?|becomes?|feels?|goes)\s+(?:greedy|fearful)\b"),
    re.compile(r"\b(?:what )?you pay\b[^.!?]{0,20}\bprice\b[^.!?]{0,30}\b(?:what )?you get\b"
               r"[^.!?]{0,20}\bvalue\b|\bprice\b[^.!?]{0,15}\b(?:what )?you pay\b[^.!?]{0,30}\b"
               r"value\b[^.!?]{0,15}\b(?:what )?you get\b"),
    re.compile(r"\b(?:business|company)\b[^.!?]{0,20}\b(?:any|an) (?:fool|idiot|dummy)\b"
               r"[^.!?]{0,12}\b(?:can|could|would be able to)\s+run\b"),
    re.compile(r"\bshort (?:run|term)\b[^.!?]{0,60}\b(?:voting|popularity|beauty contest)\b"
               r"[^.!?]{0,80}\blong (?:run|term)\b[^.!?]{0,60}\b(?:weighing|scale)\b"),
    re.compile(r"\b(?:wonderful|great|excellent|outstanding|terrific) (?:business|company) at a "
               r"(?:fair|reasonable|decent|sensible) price\b|\bfair (?:business|company) at a "
               r"(?:wonderful|great|excellent|terrific) price\b"),
)
#: Attribution scaffolding: a saying framed as someone's words, whoever they are.
_SAYING_FRAME_RES = (
    re.compile(r"\bas the (?:old |famous |wise )?saying goes\b"),
    re.compile(r"\bas (?:they|people|investors|traders|the pros) (?:like to |often )?say\b"),
    re.compile(r"\b(?:proverb|proverbs|adage|adages|aphorism|aphorisms|maxim|maxims|old saw)\b"),
    re.compile(r"\b(?:once|famously) (?:said|quipped|wrote|remarked|joked|observed|declared|put it)\b"),
    re.compile(r"\bin the (?:immortal |famous )?words of\b|\bto quote\b"),
    # "A patient investor says no to the daily noise" is behaviour, not a quotation frame.
    re.compile(r"\b(?:one|a|an|the) (?:[a-z-]+ ){0,3}(?:legend|icon|guru|sage|investor|billionaire|"
               r"trader|pro|expert|veteran|master|wise man|wise woman|old-timer|economist|founder|"
               r"ceo) (?:once |famously )?(?:said|says|put it|wrote|writes|quipped|advised|"
               r"warned|noted|observed|joked|liked to say|used to say)\b(?!\s+(?:no|yes)\b)"),
)

_QUOTE_STOP = frozenset("""
that this these those there their them they then than with without from into onto over under
about above below after before between through during until while since because though
although what when where which whose whom your yours have having been being were does done
also even still just only very more most much many some such same other each every both
will would could should shall might must upon like into unto
""".split())
_QUOTE_SPLIT_RE = re.compile(r"[.,;:!?()\"-]+|\b(?:and|but|or|then|than)\b")
_QWORD_RE = re.compile(r"[a-z]+")
#: A clause shorter than this many content roots ("Do nothing.", "Time is your friend.") is
#: ordinary teaching copy, never matched on overlap alone.
_CLAUSE_MIN_ROOTS = 4
_CLAUSE_OVERLAP = 0.8


def _qroot(w: str) -> str:
    for suf in ("ings", "ing", "ers", "er", "ed", "es", "s", "ly"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _qroots(folded: str) -> FrozenSet[str]:
    return frozenset(_qroot(w) for w in _QWORD_RE.findall(folded.replace("'", ""))
                     if len(w) >= 4 and w not in _QUOTE_STOP)


@lru_cache(maxsize=1)
def _quote_clause_index() -> Tuple[Tuple[FrozenSet[str], ...], Dict[str, Tuple[int, ...]]]:
    """Every clause (≥ `_CLAUSE_MIN_ROOTS` content roots) of every vendored quote and famous
    saying, plus an inverted index root → clause ids, so the overlap check is linear in the
    draft, not in the number of quotes."""
    texts = [str(q.get("text") or "") for q in investor_quotes()] + list(_FAMOUS_SAYING_TEXTS)
    clauses: List[FrozenSet[str]] = []
    for t in texts:
        for part in _QUOTE_SPLIT_RE.split(fold(t)):
            roots = _qroots(part)
            if len(roots) >= _CLAUSE_MIN_ROOTS:
                clauses.append(roots)
    index: Dict[str, List[int]] = {}
    for i, roots in enumerate(clauses):
        for r in roots:
            index.setdefault(r, []).append(i)
    return tuple(clauses), {r: tuple(ids) for r, ids in index.items()}


def _clause_overlap_hit(folded: str) -> str:
    """A draft sentence holding ≥80% of one quote clause's content roots, in ANY order: the
    swapped halves of a line and a light paraphrase ("When the tide goes out, you see who was
    swimming naked") share no contiguous 6-gram with the source but keep its words."""
    clauses, index = _quote_clause_index()
    for sent in sentences(folded):
        counts: Counter = Counter()
        for r in _qroots(sent):
            for cid in index.get(r, ()):
                counts[cid] += 1
        for cid, n in counts.items():
            if n >= _CLAUSE_OVERLAP * len(clauses[cid]):
                return " ".join(sorted(clauses[cid]))
    return ""


def _famous_quote_hit(folded: str) -> str:
    """A famous quotation or its scaffolding: a contiguous 6-gram of a vendored quote, a
    signature phrase of a famous saying, an attribution frame ("as the saying goes", "a
    legendary investor once said"), or an order-insensitive clause overlap."""
    hit = _ngram_quote_hit(folded)
    if hit:
        return hit
    m = _SAYINGS_RE.search(folded)
    if m:
        return m.group(0)
    # A saying written with a comparison SYMBOL ("Time in the market > timing the market",
    # "… → timing the market") is the same saying (round 2, w2ww-3). Linear: one translate.
    if any(sym in folded for sym in _COMPARISON_SYMBOLS):
        m = _SAYINGS_RE.search(_COMPARISON_RE.sub(" beats ", folded))
        if m:
            return m.group(0)
    for rx in _SAYING_SHAPE_RES + _SAYING_FRAME_RES:
        m = rx.search(folded)
        if m:
            return m.group(0)
    return _clause_overlap_hit(folded)


# ── companies: who a sentence is about (content-B review) ─────────────────────
#
# A company whose name is an English word ("Apple", "Target", "Visa", "Oracle", "Coke") passed the
# grounding dictionary as ordinary vocabulary, and Journey mode turned every valuation row off on
# the premise that entity grounding keeps a named instrument out. Both halves were false, so a
# Journey post could say "Apple stock looks cheap" and a Money Moves post "Buy NVIDIA". The
# vendored lexicon (`data/known_companies_en.txt`) makes a company a company wherever it is
# written in a NAME position; `grounding._check_companies` then requires the item's own fact sheet
# to name it, and `scan_text` treats it as the instrument of every opinion row in both modes.

KNOWN_COMPANIES_PATH = DATA_DIR / "known_companies_en.txt"

_CO_TOKEN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9&]|[-'.](?=[A-Za-z0-9]))*")


@dataclass(frozen=True)
class CompanyLexicon:
    words: FrozenSet[str]                  # "~Apple": English words, exact capitalised surface
    distinct: FrozenSet[str]               # not English words ("nvidia", "costco"), any case
    caps: FrozenSet[str]                   # all capitals, exact ("IBM", "AT&T")
    multi: Dict[Tuple[str, ...], str]      # exact token tuple -> canonical lower-case name
    max_len: int = 1
    multi_first: FrozenSet[str] = frozenset()   # first tokens of `multi` (a cheap pre-check)


@lru_cache(maxsize=1)
def company_lexicon() -> CompanyLexicon:
    """The vendored company lexicon. Never raises: a missing file only narrows the company rules
    to the item's own company terms, and is logged."""
    try:
        lines = KNOWN_COMPANIES_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        logger.warning("marketing compliance: %s unreadable (%s: %s)", KNOWN_COMPANIES_PATH.name,
                       type(e).__name__, e)
        lines = []
    words, distinct, caps = set(), set(), set()
    multi: Dict[Tuple[str, ...], str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        word = line.startswith("~")
        name = skeleton(line.lstrip("~").strip())
        toks = tuple(m.group(0) for m in _CO_TOKEN_RE.finditer(name))
        if not toks:
            continue
        if len(toks) > 1:
            multi[toks] = " ".join(t.lower() for t in toks)
        elif word:
            words.add(toks[0])
        elif len(toks[0]) >= 2 and toks[0].upper() == toks[0] and any(c.isalpha() for c in toks[0]):
            caps.add(toks[0])
        else:
            distinct.add(toks[0].lower())
    return CompanyLexicon(words=frozenset(words), distinct=frozenset(distinct),
                          caps=frozenset(caps), multi=multi,
                          max_len=max((len(k) for k in multi), default=1),
                          multi_first=frozenset(k[0] for k in multi))


#: After a word-brand, these make it the COMPANY even at a sentence start ("Target shares",
#: "Apple Inc", "Visa and Mastercard" is handled separately).
_CO_CONTEXT = frozenset({
    "stock", "stocks", "shares", "share", "shareholders", "shareholder", "investors", "inc",
    "corp", "corporation", "co", "company", "group", "holdings", "plc", "ltd", "llc",
})
#: A sentence-initial word-brand is the company only as the SUBJECT of a verb ("Apple has",
#: "Target is", "Kodak once looked"). An imperative takes an object instead ("Target a savings
#: rate", "Chase returns", "Shell out", "Discover how"), so none of these follows it.
_CO_VERBS = frozenset("""
is was are were has had have will would could can may might should must did does do remains
remained became becomes become looks looked seems seemed appears appeared stays stayed keeps kept
still now also never once already just then later soon quickly recently famously even often
rarely always usually pays paid sells sold makes made built builds grew grows trades traded owns
owned earns earned spends spent bought buys runs ran charges charged launched launches opened
opens reported reports controls controlled dominates dominated faced faces rose rises fell falls
gained gains lost loses won wins beat beats overtook overtakes joined joins needs needed uses
used offers offered turned turns started starts began begins shipped ships invests invested
acquired acquires merged merges went goes hit hits reached reaches climbed climbs soared soars
jumped jumps surged surges plunged plunges doubled doubles tripled sank crashed rallied
delivered delivers posted posts raised raises cut cuts hired created creates changed changes
shows showed proved proves took takes gave gives got gets holds held deserves deserved copied
copies broke breaks bet bets spun sits sat stands stood leads led helped helps moved moves
struggled struggles tried tries focused focuses kept wanted wants saw sees
belongs belong belonged ranks ranked
""".split())
_CLAUSE_OPENERS = frozenset(':;"(')
_MR = frozenset({"mr", "mrs", "ms", "dr"})
# ── round 3 (W3CB-3/4, W3OB-3, W3OB-9): what may stand between a word-brand and its verb, and
#    when a clause-initial word-brand is a VERB or a MODIFIER instead ──
#: One adverb between a word-brand and its verb ("Apple really looks cheap", "Visa truly is one
#: of the best", "NVIDIA arguably belongs"): an "-ly" word of five letters or more that is not a
#: noun or an adjective ("Chase only quality", "Apple supply chains", "Target early retirement"
#: keep their next word), or one of these. The skipped word only moves the verb test one word on:
#: a word after it that is no verb leaves the brand a word ("Chase really cheap stocks").
_BRAND_ADVERBS = frozenset("""
sure today currently actually really truly simply certainly clearly probably surely quietly
honestly arguably finally suddenly definitely genuinely obviously
""".split())
#: The adverbs `_CO_VERBS` lists (a noun-brand before one is the company, as before): for a
#: VERB-brand they are skipped like any adverb, so "Discover just how cheap index funds are"
#: stays an imperative and "Target still looks cheap" stays the company.
_CO_ADVERBS = frozenset("""
still now also never once already just then later soon quickly recently famously even often
rarely always usually
""".split())
_NOT_AN_ADVERB = frozenset("""
only early supply family rally apply reply ally italy jelly belly bully daily weekly monthly
yearly quarterly likely costly friendly lovely lonely holy ugly silly
""".split())
#: Two- and three-word adverbials in the same slot ("Apple right now looks cheap", "Apple at this
#: price looks cheap"): (first word, the words that may follow it).
_BRAND_ADVERBIALS = {
    "right": (("now",),),
    "at": (("this", "price"), ("these", "prices"), ("this", "level"), ("that", "price"),
           ("today's", "price"), ("today's", "prices"), ("current", "prices")),
    "these": (("days",),),
    "this": (("year",), ("week",), ("month",)),
}
#: Word-brands that are also English VERBS: at a clause start they may open an imperative
#: ("Discover ways to sell", "Chase headlines", "Zoom out", "Block emotional selling"). A noun-only
#: brand ("Apple", "Coke", "Visa", "Meta", "Lemonade") never can. Closed and hand-kept: a verb-brand
#: missing here is read as a noun-brand — more company readings, never fewer (fail-closed).
_VERB_BRANDS = frozenset({"Arm", "Block", "Chase", "Discover", "Ford", "Marvel", "Shell", "Snap",
                          "Square", "Target", "Zoom"})
#: A particle after a clause-initial verb-brand makes it a phrasal imperative ("Zoom In On The
#: Business", "Shell out", "Block out the noise").
_BRAND_PARTICLES = frozenset({"in", "out", "up", "down", "off", "back", "away", "through", "into"})
#: Words before a verb-brand that make it the verb ("Don't Chase Hot Stocks", "Stop Chasing",
#: "You Chase…"): a negation, "to", a subject pronoun, a frequency adverb.
_BRAND_VERB_BEFORE = frozenset("""
don't dont never to can't cannot won't shouldn't doesn't didn't stop let's you we they i just
always rarely often
""".split())
#: A modal or "do" before a verb-brand makes it the verb ("Why You Should Zoom Out Before You
#: Sell") — except in a QUESTION, where the auxiliary inverts with its subject: "Did Target Look
#: Cheap?", "Can Target Recover?" name the company.
_BRAND_AUX_BEFORE = frozenset("should must can could would will do does did may might".split())
#: What follows a PLURAL NOUN, never a third-person verb's object: after "Discover ways", "Discover
#: tools", "Discover funds" comes "to", "like", "that" — after "Apple enjoys", "Target sports",
#: "Apple generates" comes the object ("a", "huge"), so those stay the company's verb.
_NOUN_FOLLOWERS = frozenset("""
to for that which who whose like from of and or but with about on in at into when while before
after can could will would may might should must often rarely usually always never sometimes
typically frequently seldom
""".split())
#: English compounds whose first word is a word-brand used as a plain MODIFIER: "lemonade stand"
#: (journey:income_statement's own metaphor), "snap decisions/judgments". Keyed on the exact
#: brand and the next token, never possessive: "Lemonade's market cap", "Lemonade stock" and
#: "Lemonade looks cheap" stay the company.
_WORD_BRAND_COMPOUNDS = {
    "Lemonade": frozenset({"stand", "stands"}),
    "Snap": frozenset({"decision", "decisions", "judgment", "judgments", "judgement",
                       "judgements", "reaction", "reactions"}),
}


def _brand_adverb(low: str) -> bool:
    return (low in _BRAND_ADVERBS or low in _CO_ADVERBS
            or (len(low) > 4 and low.endswith("ly") and low not in _NOT_AN_ADVERB))


def _after_adverbial(toks: Sequence["re.Match[str]"], j: int) -> int:
    """The index of the first token at or after j that is not a brand adverb or adverbial
    (at most one adverb, or one listed two/three-word adverbial). Bounded: O(1)."""
    n = len(toks)
    if j >= n:
        return j
    low = toks[j].group(0).lower()
    for tail in _BRAND_ADVERBIALS.get(low, ()):
        if j + len(tail) < n and all(toks[j + 1 + k].group(0).lower() == w
                                     for k, w in enumerate(tail)):
            return j + 1 + len(tail)
    if _brand_adverb(low):
        return j + 1
    return j


def _brand_verb_next(toks: Sequence["re.Match[str]"], i: int) -> str:
    """The lower-case word that would be the verb of the word-brand at token i: the next word,
    or the word after one adverb or adverbial ("Apple really looks", "Apple right now looks")."""
    j = _after_adverbial(toks, i + 1)
    if j == i + 1 or j >= len(toks):
        return toks[i + 1].group(0).lower() if i + 1 < len(toks) else ""
    return toks[j].group(0).lower()
#: Valuation / verdict / trading vocabulary in a sentence makes a capitalised word-brand the
#: COMPANY in any position (round-2 W2CB-3): a Title-Case "Apple At A Bargain Price", a label
#: "Target: low P/E, solid dividend", an unlisted verb "Apple enjoys a wide moat, and it looks
#: cheap today", "Mr. Market Is Selling Apple Cheap". Real price vocabulary only — "Shell Out
#: Less On Fees" and "Don't Chase Hot Returns" carry none — and never a word-brand after an
#: article ("Mind The Gap"), inside a hyphenated compound ("Target-Date Funds") or opening an
#: imperative ("Target a savings rate", "Chase the trend").
_BRAND_VERDICT_RE = re.compile(
    r"\b(?:cheap|cheaper|cheapest|expensive|pricey|bargains?|a steal|on sale|discount(?:ed)?|"
    r"under-?valued|over-?valued|over-?priced|under-?priced|p/e|pe ratio|price-to-earnings|"
    r"valuations?|worth|fair price|great price|good price|great deal|good deal|a buy|no-brainer|"
    r"stock|stocks|shares|share price|stock price|market cap|dividend yield|buy|sell|selling|"
    r"buying)\b"
)
_IMPERATIVE_OBJECT = frozenset({
    "a", "an", "the", "your", "my", "our", "his", "her", "their", "this", "that", "these", "those",
    "it", "them", "out", "down", "up", "off", "less", "more", "every", "each", "some", "any",
    # A headline imperative: "Discover Why Index Funds…", "Square Your Budget…".
    "how", "why", "what", "when", "which", "where", "who", "whether", "if", "you", "yourself",
})
_ARTICLE_BEFORE = frozenset({"the", "a", "an", "your", "my", "our", "their", "this", "that",
                             "every", "each", "no", "any"})


def _clause_initial(sent: str, toks: Sequence["re.Match[str]"], i: int) -> bool:
    """Token i opens the sentence or a clause (after ':', ';', a quote, a bracket or a dash)."""
    if i == 0:
        return True
    # A hyphen inside a name ("Coca-Cola") is inside one token, so a "-" in the GAP is a dash.
    return any(c in _CLAUSE_OPENERS or c == "-" for c in sent[toks[i - 1].end():toks[i].start()])


def _base(surface: str) -> Tuple[str, bool]:
    if surface.endswith("'s") or surface.endswith("'S"):
        return surface[:-2], True
    return surface, False


def sentence_company_mentions(sent: str, own: FrozenSet[str] = frozenset()
                              ) -> List[Tuple[str, int, int]]:
    """Every company named in ONE sentence (the SKELETON: case kept, accents folded), as
    `(canonical lower-case name, start, end)` of the name itself (a possessive "'s" is left
    outside the span). Linear: one tokenisation, set lookups, a bounded multi-word window.

    `own` (round 3, W3CB-3): the lower-case word-brands a Money Moves item is ABOUT ("visa" in
    visa-vs-mastercard). Opening a clause, that word is the issuer whatever verb follows — "Visa
    quietly compounds…" — unless it opens an imperative ("Target a…", "Zoom in…")."""
    lex = company_lexicon()
    toks = list(_CO_TOKEN_RE.finditer(sent))
    if not toks:
        return []
    title: List[bool] = []
    verdict: List[bool] = []         # computed at most once per sentence (keeps it linear)
    out: List[Tuple[str, int, int]] = []
    n = len(toks)
    i = 0
    while i < n:
        hit = None
        first = toks[i].group(0)
        for k in (range(min(lex.max_len, n - i), 1, -1) if first in lex.multi_first else ()):
            parts = tuple(_base(t.group(0))[0] if j == i + k - 1 else t.group(0)
                          for j, t in enumerate(toks[i:i + k], start=i))
            name = lex.multi.get(parts)
            if name:
                end = toks[i + k - 1].start() + len(parts[-1])
                hit = (name, toks[i].start(), end, k)
                break
        if hit:
            out.append(hit[:3])
            i += hit[3]
            continue
        surface = toks[i].group(0)
        base, possessive = _base(surface)
        start, end = toks[i].start(), toks[i].start() + len(base)
        if base in lex.caps or (base.lower() in lex.distinct and any(c.isalpha() for c in base)):
            out.append((base.lower(), start, end))
        elif base in lex.words:
            nxt = toks[i + 1].group(0) if i + 1 < n else ""
            nlow = nxt.lower()
            verb = _brand_verb_next(toks, i)
            # A noun-brand before a listed adverb is the company, as it always was ("Apple
            # still…"); a verb-brand needs the verb AFTER the adverb ("Discover just how…").
            subject_verb = verb in _CO_VERBS or (base not in _VERB_BRANDS and nlow in _CO_VERBS)
            if possessive or nlow in _CO_CONTEXT or sent[end:end + 1] == ",":
                out.append((base.lower(), start, end))   # "Apple's", "Target shares", "Shell, BP"
            elif nlow in _WORD_BRAND_COMPOUNDS.get(base, ()):
                pass                                     # "Lemonade stands", "Snap decisions"
            elif (base.lower() in own and _clause_initial(sent, toks, i)
                  and not _brand_opens_imperative(base, nlow)):
                out.append((base.lower(), start, end))   # the case study's own brand (W3CB-3)
            elif _verdict_brand(sent, toks, i, end, nlow, verdict):
                out.append((base.lower(), start, end))   # "Apple At A Bargain Price", "Target: low P/E"
            else:
                if not title:
                    title.append(_title_cased(sent))
                if title[0]:
                    # A headline capitalises every word, so only a verb after the name tells:
                    # "Why Apple Looks Cheap" is the company, "Why Target Dates Matter" is not.
                    if subject_verb:
                        out.append((base.lower(), start, end))
                else:
                    if not _clause_initial(sent, toks, i):
                        prev = toks[i - 1].group(0).lower()
                        if prev not in _MR:
                            out.append((base.lower(), start, end))
                    elif nxt.islower() and subject_verb:
                        # "Visa belongs…", "Visa truly is…", "Apple right now looks…" (W3CB-3/4)
                        out.append((base.lower(), start, end))
                    elif nlow in ("and", "&") and i + 2 < n and toks[i + 2].group(0)[:1].isupper():
                        out.append((base.lower(), start, end))  # "Visa and Mastercard"
        i += 1
    return out


def _brand_opens_imperative(base: str, nlow: str) -> bool:
    """A clause-initial word-brand followed by `nlow` opens an imperative: an object determiner
    ("Target a…", "Chase the…") for any brand, a particle only for a verb-brand ("Zoom in")."""
    return nlow in _IMPERATIVE_OBJECT or (base in _VERB_BRANDS and nlow in _BRAND_PARTICLES)


def _verdict_brand(sent: str, toks: Sequence["re.Match[str]"], i: int, end: int, nlow: str,
                   verdict: List[bool]) -> bool:
    """A word-brand at token i is the company because its sentence talks price, value or
    trading (`_BRAND_VERDICT_RE`) — unless it is plainly a word: after an article or a numeral
    ("One Apple Or A Basket Of Stocks?"), opening an imperative whose object follows ("Target a
    low P/E", "Chase the cheap stocks", "Discover ways to sell", "Zoom In On The Business"), or,
    for a verb-brand, used as the verb after a negation, a modal or a plural subject ("Don't
    Chase Hot Stocks", "Why Investors Chase Rising Stocks", "Why You Should Zoom Out").

    Round 3 (W3CB-4, W3OB-3): one adverb or adverbial may sit between the brand and its verb
    ("Apple really looks cheap", "Apple right now looks cheap"); and a plural NOUN after a
    verb-brand ("Discover ways to…", "Discover tools like…") is its object, not its verb — a
    third-person verb takes an object ("Target sports a low P/E", "Apple generates huge cash")."""
    if not verdict:
        verdict.append(bool(_BRAND_VERDICT_RE.search(sent.lower())))
    if not verdict[0]:
        return False
    base = _base(toks[i].group(0))[0]
    verb_brand = base in _VERB_BRANDS
    if i > 0:
        prev = toks[i - 1].group(0).lower()
        if prev in _ARTICLE_BEFORE or prev in ("one", "single"):
            return False
        if verb_brand and (prev in _BRAND_VERB_BEFORE or _ACTOR_WORD_RE.fullmatch(prev)
                           or (prev in _BRAND_AUX_BEFORE and "?" not in sent)):
            return False                     # "Don't Chase…", "Investors Chase…", "Should Zoom…"
    if not (_clause_initial(sent, toks, i) and sent[end:end + 1] not in (":", ";")
            and i + 1 < len(toks)):
        return True
    if _brand_opens_imperative(base, nlow):
        return False                         # "Target a…", "Discover How…", "Zoom In On…"
    if not verb_brand and nlow in _CO_VERBS:
        return True                          # "Apple has…", "Coke still…"
    # The brand's verb slot: the next word, or the word after one adverb or adverbial ("Apple
    # really looks", "Apple right now looks", "Target still looks").
    j = _after_adverbial(toks, i + 1)
    skipped = i + 1 < j < len(toks)
    k = j if skipped else i + 1
    vtok = toks[k].group(0)
    vlow = vtok.lower()
    if vlow in _CO_VERBS:
        return True
    if skipped or vtok.islower():
        if len(vlow) > 3 and vlow.endswith("s"):
            if not verb_brand:
                return True                  # "Apple enjoys…", "Coke quietly commands…"
            # A verb-brand before a plural noun is an imperative + object when a noun's
            # follower, a punctuation mark or the end comes next ("Discover ways to…",
            # "Discover funds that…", "Chase headlines, and…"); a verb takes its object
            # ("Target generates huge cash", "Target quietly generates…").
            if k + 1 >= len(toks) or any(c in ",;:.!?)" for c in
                                         sent[toks[k].end():toks[k + 1].start()]):
                return False
            return toks[k + 1].group(0).lower() not in _NOUN_FOLLOWERS
        # An imperative's object ("Chase really cheap stocks", "Discover exactly how…") or a
        # compound noun ("Apple pie…").
        return False
    # A capitalised next word (a headline). A noun-brand opening one is the company ("Apple At
    # A Bargain Price"); so is a verb-brand before a preposition or a gerund ("Target At A
    # Bargain Price", "Target Trading Below Its Worth"). Before anything else a verb-brand is
    # the imperative ahead of its object ("Block Emotional Selling With Rules", "Chase Hot
    # Stocks").
    return not verb_brand or vlow.endswith("ing") or vlow in _NOUN_FOLLOWERS


def company_mentions(text: str) -> List[str]:
    """Canonical names of every company `text` (a skeleton) names, sentence by sentence."""
    return [name for sent in sentences(text) for name, _s, _e in sentence_company_mentions(sent)]


#: The placeholder a company becomes in the view the company rows read ("Buy Zzco"). Exported:
#: `grounding` reads the same view (`company_view`) to tell a price claim from a business fact.
COMPANY_MARK = "Zzco"
_CO_MARK = COMPANY_MARK


def company_view(sent: str, mentions: Sequence[Tuple[str, int, int]]) -> str:
    """`sent` with every mention span replaced by `COMPANY_MARK` (case kept; fold it after)."""
    out, last = [], 0
    for _name, s, e in mentions:
        out.append(sent[last:s])
        out.append(_CO_MARK)
        last = e
    out.append(sent[last:])
    return "".join(out)


_company_view = company_view


#: A capitalised word mid-sentence that is a proper noun of no company: kept out of the
#: relaxed-mode escalation below so "Mr. Market", "Wall Street" and "the Fed" stay Journey copy.
_NOT_A_NAME_SIGNAL = frozenset("""
i mr mrs ms dr market wall street fed federal reserve treasury treasuries great depression dow
jones nasdaq s&p main god earth internet english rule roth ira fdic social security medicare
congress january february march april may june july august september october november december
monday tuesday wednesday thursday friday saturday sunday american americans european
europeans chinese japanese british french german germans asian korean taiwanese indian
canadian western eastern northern southern global african latin america europe asia china
japan
""".split())


#: Heading words a lesson's lead-in or a card title capitalises ("Checklist Item 1:", "The
#: Myth:", "Key Takeaway") — never a company's name.
_HEADING_WORDS = frozenset("""
myth myths fact facts takeaway takeaways checklist lesson lessons question questions answer
answers step steps rule rules sign signs mistake mistakes idea ideas truth truths reality quick
guide basics explained item items part key point points tip tips recap summary why how what
""".split())
_SHORT_TITLE_WORDS = 6


def _sheet_word(low: str, sheet_words: FrozenSet[str]) -> bool:
    return (low in sheet_words or low in _HEADING_WORDS
            or (low.endswith("s") and low[:-1] in sheet_words) or low + "s" in sheet_words)


def _short_title(sent: str) -> bool:
    """A card or slide title ("Market Cap", "P/E Ratio", "When a Stock Climbs"): a short,
    period-free fragment whose every content word is capitalised. `_title_cased` needs three
    content words, so two-word titles used to read as a sentence with a mid-sentence name."""
    words = _WORD3_RE.findall(sent)
    if not words or len(words) > _SHORT_TITLE_WORDS or sent.rstrip().endswith("."):
        return False
    content = [w for w in words if len(w) >= 3 and w.lower() not in _FUNCTION_WORDS]
    return bool(content) and all(w[0].isupper() for w in content)


def _proper_noun_signal(sent: str, sheet_words: FrozenSet[str] = frozenset()) -> bool:
    """A capitalised word written mid-sentence (outside a Title-Case headline) that is not an
    acronym and not a known non-company proper noun: in a Journey post, which names no company
    by design, it is the name of one the lexicon does not list ("Companies like Lemonade …").

    Round 2 (W2-OB-5): a word the item's OWN fact sheet uses (`sheet_words`, lower case — "Your
    Quick Dashboard:", "The FOMO Cycle:", "The Price-to-Earnings ratio") or a heading word is
    no name, and a short Title-Case fragment is a title, not a sentence ("Market Cap", "P/E
    Ratio"). A LEXICON company escalates independently of this (`names_co` in `_sentence_hits`),
    so "apples to apples" in a sheet never shields "Apple's market cap"."""
    toks = list(_CO_TOKEN_RE.finditer(sent))
    cand = []
    for i, t in enumerate(toks):
        w = _base(t.group(0))[0]
        # A single letter ("P/E", an initial) or an acronym ("ETFs" aside, "S&P") is no name.
        if not w[:1].isupper() or len(w) < 2 or w.upper() == w or (w[:-1].upper() == w[:-1]
                                                                   and w.endswith("s")) \
                or _clause_initial(sent, toks, i):
            continue
        if (w.lower() in _NOT_A_NAME_SIGNAL or w.lower() in _FUNCTION_WORDS
                or toks[i - 1].group(0).lower() in _MR
                or any(c in ".!?" for c in sent[toks[i - 1].end():t.start()])):
            continue                  # "P/E. That's a real start" is two sentences to a reader
        parts = [p for p in w.lower().split("-") if p]
        if parts and all(p in _FUNCTION_WORDS or _sheet_word(p, sheet_words) for p in parts):
            continue
        cand.append(w)
    return bool(cand) and not (_title_cased(sent) or _short_title(sent))


# ── class B (EU MAR / UK FCA): value or price opinions ────────────────────────

_INSTRUMENT_WORDS = ("stock", "stocks", "share", "shares", "equity", "equities")
_INSTRUMENT = r"(?:" + "|".join(_INSTRUMENT_WORDS) + r")"
#: A specific instrument moving: "its shares fell", "the stock soared". Not "stocks" (the market
#: in general) and not "stock market" — a crash of the market is history, not a verdict.
_ONE_INSTRUMENT = r"(?:stock(?! market)|shares)"
#: The same instrument, told apart from its two homographs (round 3, W3OB-11): INVENTORY stock
#: ("kept deeper stock of the boring items", "less stock per aisle", "the stock is deep") and
#: the VERB "shares" with its object ("Visa shares the road with Mastercard", "Each house shares
#: the back office"). A time phrase after "shares" is no object ("NVIDIA shares a year later had
#: tripled"), and a determiner alone never exempts "stock": "the stock of NVIDIA took off".
_INVENTORY_BEFORE = (r"(?<!\bin )(?<!\bout of )(?<!\bdeep )(?<!\bdeeper )(?<!\bdeepest )"
                     r"(?<!\bmore )(?<!\bless )(?<!\benough )(?<!\bextra )(?<!\bexcess )"
                     r"(?<!\bspare )(?<!\bsurplus )(?<!\bplenty of )(?<!\blots of )(?<!\bkeep )"
                     r"(?<!\bkeeps )(?<!\bkept )(?<!\bkeeping )(?<!\bcarry )(?<!\bcarries )"
                     r"(?<!\bcarried )(?<!\bcarrying )")
_SHARES_OBJECT = (r"(?:the|its|a|an|their|his|her|this|that|these|those|some|much|every|each|one|"
                  r"both|space|costs?|data|information|revenue|profits?|risks?|ownership|control|"
                  r"credit|access|power|features?|technology|platforms?|networks?|warehouses?)")
_TIME_AFTER = (r"(?:year|years|decade|decades|month|months|week|weeks|day|days|quarter|quarters|"
               r"session|morning|afternoon|following|next|few|couple|while|moment)")
_MOVED_INSTRUMENT = (r"(?:" + _INVENTORY_BEFORE + r"\bstock(?! market)(?!\s+per\b)(?!\s+(?:is|was|"
                     r"stays?|stayed|runs?|ran)\s+(?:deep|deeper|thin|thinner|plentiful|full|limited|"
                     r"fresh)\b)|\bshares(?!\s+" + _SHARES_OBJECT + r"\b(?!\s+" + _TIME_AFTER
                     + r"\b)))")
#: Price moves, in every tense a draft uses ("will keep climbing", "hit an all-time high").
_MOVE = (r"(?:rose|rise|rises|rising|fell|fall|falls|falling|soared|soar|soars|soaring|plunged|"
         r"plunge|plunges|plunging|jumped|jump|jumps|jumping|surged|surge|surges|surging|crashed|"
         r"crash|crashes|crashing|tanked|tanking|doubled|double|doubles|doubling|tripled|triple|"
         r"tripling|climbed|climb|climbs|climbing|rallied|rally|rallying|dropped|drop|dropping|"
         r"sank|sink|sinking|skyrocketed|skyrocket|skyrocketing|collapsed|collapsing|recovered|"
         r"recovering|rebounded|rebounding|popped|slid|sliding|slumped|slumping|tumbled|tumbling|"
         r"hit|hits|reached|reaches|touched|notched|grew|gained|multiplied|ballooned|"
         # Round 2 (W2CB-5): the idioms case-study prose uses for a stock ("NVIDIA shares took
         # off", "the shares shot up", "Shares of NVIDIA exploded").
         r"took off|shot up|shot higher|exploded|went through the roof|went parabolic|"
         r"went vertical|marched higher|marched up|ran up|rocketed|zoomed|leapt|leaped|spiked|"
         r"cratered|nosedived|went up|went down)")

#: An evaluative verdict pinned to ONE specific instrument or issuer by a determiner: "its shares
#: look cheap", "the stock is a bargain", "the company looked cheap on paper". This is an opinion
#: on a named instrument's value in any post, so these rows apply in BOTH modes; generic concept
#: teaching ("a stock that looks cheap can stay cheap", "ETFs are cheap to own") has no such
#: anchor and stays allowed in Journey.
_DETERMINER = r"(?:its|the|their|this|that|these|those)"
_SEEM = (r"(?:looks?|looked|looking|is|are|was|were|seems?|seemed|appears?|appeared|became|"
         r"becomes?|remains?|remained|stays?|stayed)")
_DEGREE = (r"(?:(?:very|so|too|quite|really|still|incredibly|extremely|relatively|historically|"
           r"deeply|fairly|surprisingly|remarkably|clearly|obviously|absurdly|ridiculously|"
           r"genuinely|truly) )?")
_PRICE_VERDICT = (r"(?:cheap|cheaper|cheapest|inexpensive|expensive|pricey|over-?priced|"
                  r"under-?priced|over-?valued|under-?valued|mis-?priced|a bargain|a steal)")
#: "attractive"/"compelling" are a verdict on shares, but on a business they are usually about
#: customers or talent ("the business is attractive to rivals"): only the instrument row has them.
_INSTRUMENT_VERDICT = r"(?:" + _PRICE_VERDICT + r"|attractive|compelling|on sale)"
#: One adverb between an instrument and its verb ("its stock STILL looks cheap").
_ADV = (r"(?:(?:still|now|once|often|already|really|suddenly|briefly|clearly|arguably|finally|"
        r"again|even|always|usually|rarely|never|also|just|[a-z]+ly) )?")
#: The investment verdict nouns ("a great investment", "the safest bet", "a core holding").
_INVEST_VERDICT = (r"(?:good|great|wonderful|smart|safe|safest|solid|wise|excellent|best|worst|"
                   r"perfect|ideal|terrible|bad|poor|risky|core|no-brainer|top|winning) "
                   r"(?:long-term )?(?:investments?|bets?|picks?|holdings?)")
#: "worth" + an amount: a company's value ("worth $6.9 billion", "worth trillions"). "worth
#: studying", "worth more than being right" and "worth it" have no amount and stay legal.
_WORTH_AMOUNT = (r"\bworth\s+(?:(?:roughly|about|nearly|over|more than|around|close to|almost|"
                 r"some|at least|an estimated|well over|north of|just under|under|less than)\s+)?"
                 r"(?:\$\s?[0-9]|[0-9]|a fortune|(?:tens of |hundreds of )?(?:trillions|billions|"
                 r"millions)\b|(?:(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
                 r"twelve|fifteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
                 r"several|many|half a)[\s-]+){1,3}(?:trillion|billion|million)s?\b)")

#: Three tier-1 rows carry a clause exemption (`_TIER1_EXEMPTIONS`), so their patterns are named.
_INVEST_VERDICT_ROW = r"\b" + _INVEST_VERDICT + r"\b"
_CONSIDER_ROW = r"\bconsider (?:buying|selling|owning|holding|shorting|investing|adding)\b"
_BUY_WHEN_ROW = r"\b(?:buy|sell) (?:when|low|high|now|today|it)\b"
#: "buy low" / "sell high" is advice; "buy high and sell low" is the MISTAKE a lesson names ("An
#: emotional loop that can lead investors to buy high and sell low" — journey:fomo_cycle, a real
#: draft). Only the PAIR is exempt: "Buy high, sell higher" is still a directive (round 2).
_MISTAKE_PAIR_RE = re.compile(r"\bbuy(?:ing)? high,?\s+(?:and\s+|then\s+)?sell(?:ing)? low\b")

#: (code, pattern, strict_only). `strict_only` rows are the neutral VOCABULARY of valuation
#: ("market cap", "P/E", "share price", "the stock fell"). In a Money Moves post — always about
#: named companies — that vocabulary is how a value/price opinion on an instrument gets said, so
#: it is rejected outright. In a Journey post "what is a P/E ratio" is exactly the education
#: class A exists for, so they are off — EXCEPT in a sentence that names a company or another
#: proper noun (`_sentence_hits`): grounding alone did not keep a word-brand ("Apple", "Target")
#: out of a Journey post (content-B review, idx 4/11/12). The OPINION rows (undervalued, price
#: target, buy the stock, will soar, a buy, a trillion-dollar company) apply everywhere.
CLASS_B_TIER1: Tuple[Tuple[str, str, bool], ...] = (
    ("valuation", r"\bunder-?valued\b", False),
    ("valuation", r"\bover-?valued\b", False),
    ("valuation", r"\bfair value\b", False),
    ("valuation", r"\bprice targets?\b", False),
    ("valuation", r"\bmost valuable (?:company|companies|firm|business|stock)\b", False),
    ("valuation", r"\b(?:share|stock) prices?\b", True),
    ("valuation", r"\bper share\b", True),
    ("valuation", r"\bmarket (?:cap|caps|capitalization|capitalisation|value)\b", True),
    ("valuation", r"\bvaluations?\b", True),
    ("valuation", r"\bvalued at\b", True),
    # "traded near two dollars", not "traded near-term profit for position".
    ("valuation", r"\btrad(?:e|es|ed|ing) (?:at|near|around|under|below|above)\b(?!-)", True),
    ("valuation", r"\bpriced in\b", True),
    ("valuation", r"\b(?:p/e|pe ratio|price-to-earnings|price to earnings|earnings multiple)\b", True),
    # Synonyms the first human read caught a draft using to talk valuation anyway
    # ("appeared inexpensive on paper, with assets exceeding its market price").
    ("valuation", r"\bmarket price\b", True),
    ("valuation", r"\b(?:over|under)-?priced\b", True),
    ("valuation", r"\bcheapness\b", True),
    ("valuation", r"\b(?:asset|liquidation|intrinsic|enterprise) value\b", True),
    ("valuation", r"\bwhat (?:the )?market (?:was |is |were )?(?:paying|pays|paid)\b", True),
    ("valuation", r"\bmis-?priced\b", True),
    ("valuation", r"\bvalue traps?\b", True),
    ("valuation", r"\bmargin of safety\b", True),
    ("valuation", r"\bbook value\b", True),
    ("valuation", r"\bsum[- ]of[- ](?:the|its|these|their)[- ](?:parts|assets)\b", True),
    ("valuation", r"\b(?:value|investment|investing) opportunit(?:y|ies)\b", True),
    ("valuation", r"\b(?:looked|looks|look|looking|appeared|appears|seemed|seems) "
                  r"(?:cheap|inexpensive|expensive|pricey|undervalued|overvalued|steep|stretched|"
                  r"frothy|rich)\b", True),
    ("valuation", r"\b(?:cheap|inexpensive|expensive) on paper\b", True),
    ("valuation", _MOVED_INSTRUMENT + r"\b[^.!?\n]{0,40}\b" + _MOVE + r"\b", True),
    # The market re-pricing a named company, in the words a case study uses for it (content-B
    # review, idx 5): "quietly re-rating the whole company", "what investors were willing to pay
    # for it", "Wall Street punished the thin margins", "reward patient owners for decades".
    # Strict only: "the market rewards patience" is Journey's generic lesson.
    ("valuation", r"\bre-?rat(?:e|es|ed|ing)\b", True),
    ("valuation", r"\b(?:higher|lower|richer|premium|bigger|fatter) multiples?\b|\bmultiple "
                  r"(?:expansion|compression)\b", True),
    # Round 2 (W2CB-12): "buyers" only when what they pay for is the instrument — "Buyers pay
    # more for a name with history" (LVMH) and "Few buyers are willing to pay that bill"
    # (NVIDIA's switching cost) are a case study's CUSTOMERS.
    ("valuation", r"\b(?:investors|markets?|wall street|shareholders|traders)\b[^.!?\n]{0,30}"
                  r"\b(?:willing to pay|paying more|paid more|pay more|paid up|pay up|paying up)\b",
     True),
    ("valuation", r"\bbuyers\b[^.!?\n]{0,30}\b(?:willing to pay|paying more|paid more|pay more|"
                  r"paid up|pay up|paying up)\s+(?:(?:a (?:premium|higher price|lot|fortune)|more|"
                  r"up)\s+)?for\s+(?:(?:its|the|their|this|that|each|every|a)\s+)?(?:[a-z0-9&.'-]+"
                  r"\s+)?(?:stock|shares|share|equity)\b", True),
    ("valuation", r"\b(?:wall street|the market|markets|investors|shareholders|traders)\b"
                  r"[^.!?\n]{0,20}\b(?:punish(?:ed|es|ing)?|reward(?:ed|s|ing)?|cheer(?:ed|s|ing)?|"
                  r"shun(?:ned|s|ning)?|bid up|sold off)\b", True),
    ("forward", r"\breward(?:ed|s|ing)?\b[^.!?\n]{0,25}\b(?:owners|shareholders|investors|"
                r"holders)\b", True),
    # A verdict on an investment (idx 3): "a great investment", "the safest bet", "in a bubble",
    # "overhyped", "priced for perfection", "room to run", "a core holding". Strict only: "what
    # makes a good investment" and "the dot-com bubble" are Journey's concepts; a Journey
    # sentence that names a company runs these too (`_class_b_sentence_hits`).
    ("recommendation", _INVEST_VERDICT_ROW, True),
    ("valuation", r"\b(?:in|into) (?:an? |the )?bubble\b|\bbubble (?:valuations?|territory|"
                  r"prices?|stocks?)\b|\b(?:is|was|are|were) an? bubble\b", True),
    ("valuation", r"\bover-?(?:hyped|rated|bought|sold)\b|\bunderrated\b", True),
    ("valuation", r"\bpriced for (?:perfection|growth)\b|\b(?:fairly|reasonably|richly|fully|"
                  r"attractively|generously) (?:priced|valued)\b|\brichly rewarded\b", True),
    ("recommendation", r"\b(?:core|long-term|forever) holdings?\b|\broom to run\b|\bstocks? to "
                       r"(?:own|buy|hold)\b|\b(?:the|a) (?:one|stock|company|name) to (?:own|buy|"
                       r"hold)\b|\bmust-own\b", True),
    ("valuation", r"\b(?:shareholders|owners|investors|holders|backers)\b[^.!?\n]{0,20}"
                  r"\b(?:rewarded|rich|wealthy|a fortune|millionaires)\b", True),
    ("valuation", r"\bdeserves? (?:its|a|the|every bit of its|a higher|a lower) (?:premium|"
                  r"valuation|multiple|price tag|price)\b", True),
    ("valuation", _WORTH_AMOUNT, True),
    # A share's price in other words (idx 48 skeptic): "a Costco share costs $1.50", "Costco
    # stock sells for 50", "shares change hands for". Strict only: "if one share costs $50 and
    # earns $5, the P/E is 10" is the Journey lesson. "market share costs" is not a share.
    ("valuation", r"(?<!market )(?<!market-)\b(?:stock|shares?)\s+(?:[a-z]+ly\s+)?(?:costs?|sells? "
                  r"for|sold for|goes for|went for|change[sd]? hands (?:for|at)|fetch(?:es|ed)?|"
                  r"traded for|trades for|(?:is|was) priced at)\b", True),
    # "It is expensive." after a sentence about the company: in a Money Moves post the pronoun
    # IS the company (idx 3). "…is expensive to run" is a cost.
    ("valuation", r"\b(?:it|they)\s+" + _ADV + r"(?:is|was|are|were|looks?|looked|seems?|seemed|"
                  r"remains?|remained|became|appears?|appeared|stays?|stayed)\s+" + _DEGREE
                  + r"(?:like\s+)?" + _PRICE_VERDICT + r"\b(?! to\b)|\b(?:it's|they're)\s+"
                  + _DEGREE + _PRICE_VERDICT + r"\b(?! to\b)", True),
    ("valuation", r"\b" + _DETERMINER + r"(?: company's| firm's)? (?:shares|stock|stocks|equity) "
                  + _ADV + _SEEM + r" " + _DEGREE + r"(?:like )?" + _INSTRUMENT_VERDICT + r"\b",
     False),
    # The same verdict with no determiner — the company's name, or nothing, in front ("Apple
    # stock looks cheap", "Target shares are a bargain"). A generic article ("a stock that looks
    # cheap", "when a stock looks cheap") is education and stays legal.
    ("valuation", r"(?<!\ba )(?<!\ban )(?<!\bany )(?<!\bevery )(?<!\bone )(?<!\bno )(?<!\beach )"
                  r"(?<!\bsuch )(?<!\bsome )(?<!\byour )\b(?:stock|shares) " + _ADV + _SEEM + r" "
                  + _DEGREE + r"(?:like )?" + _INSTRUMENT_VERDICT + r"\b", False),
    # "…is expensive to run" is a cost, not a valuation.
    ("valuation", r"\b" + _DETERMINER + r" (?:company|business|firm) " + _SEEM + r" " + _DEGREE
                  + _PRICE_VERDICT + r"\b(?! to\b)", False),
    # Digit-free market caps and price levels (idx 8). Strict (valuation FACTS, not opinions:
    # `theme_insights_service` reuses the non-strict rows for in-app summaries, where "shares
    # hit a 52-week high" is exactly what may be said); a Journey sentence that names a company
    # runs them too (`_sentence_hits`), and "an all-time high" is checked per sentence there.
    ("valuation", r"\b(?:multi-?)?(?:million|billion|trillion)-dollar (?:company|companies|club|"
                  r"giants?|firms?|business(?:es)?|stocks?|valuations?|market caps?|behemoths?|"
                  r"titans?|brands?)\b", True),
    ("valuation", r"\b(?:world's|worlds|global) (?:largest|biggest|most valuable|richest) "
                  r"(?:compan(?:y|ies)|firms?|stocks?|business(?:es)?|corporations?)\b|"
                  r"\b(?:largest|biggest|richest) (?:compan(?:y|ies)|firms?|stocks?|"
                  r"corporations?) (?:in|on) (?:the world|earth|the planet|the market|wall street|"
                  r"the s&p 500|the index)\b", True),
    # "an all-time high" / "record highs" are handled per sentence (`_RECORD_RE`): a record in
    # inflation or interest rates is economics, not a price level.
    ("valuation", r"\b52-week (?:highs?|lows?)\b", True),
    # ── round 2 (W2CB-4/5): valuation FACTS in words the rows above never listed. Strict (a
    # Journey sentence that names a company runs them too); `theme_insights_service` reads only
    # the non-strict rows. ──
    ("valuation", r"\bdividend yields?\b", True),
    # Something moving the stock: "Renewals above 90% have lifted the shares for decades".
    ("valuation", r"\b(?:lifted|lifts|lift|carried|carries|sent|sends|pushed|pushes|drove|drives|"
                  r"propelled|propels|boosted|boosts|sank|sinks|hurt|hurts|hammered|hammers|"
                  r"crushed|crushes|dragged|drags|weighed on|weighs on|tanked|tanks)\s+(?:the|its|"
                  r"their)\s+(?:stock|shares|share price|stock price)\b", True),
    ("valuation", r"\bmost valuable (?:[a-z-]+\s+){0,2}?(?:chipmakers?|retailers?|automakers?|"
                  r"carmakers?|makers?|brands?|giants?|corporations?|enterprises?|banks?|groups?|"
                  r"conglomerates?|startups?|manufacturers?|producers?|players?|names?|public "
                  r"compan(?:y|ies)|listed compan(?:y|ies)|tech (?:firms?|compan(?:y|ies)))\b", True),
    ("valuation", r"\b(?:largest|biggest|richest) (?:compan(?:y|ies)|firms?|corporations?|"
                  r"business(?:es)?) (?:ever|in history|of all time|the world has seen)\b", True),
    # Returns with no digit: "one of the best performers of the decade", "a small bet on
    # Amazon turned into a fortune".
    ("valuation", r"\b(?:best|top|worst|biggest)[- ](?:performing (?:stocks?|shares|investments?|"
                  r"assets?|funds?|compan(?:y|ies)|names?)|performers?)\b", True),
    ("valuation", r"\b(?:bet|stake|investment|position|holding|shares?|stock|dollars?|"
                  r"\$\s?[0-9][0-9,.]*[kmb]?)\b[^.!?\n]{0,50}?\b(?:turned|grew|became|would have "
                  r"become|would have grown|would be worth|is now worth|made|earned|returned|"
                  r"produced|delivered)\s+(?:into\s+)?(?:a\s+(?:small\s+|tidy\s+|life-changing\s+)?"
                  r"fortune|millions|a million|life-changing (?:money|wealth))\b", True),
    # ── round 3 (W3CB-6): the same return or move, in the case-study idioms the rows above
    #    never listed. Strict; each is bound to an INVESTOR or an INSTRUMENT, never to the
    #    company alone ("When Netflix spends a fortune on a blockbuster", "Water your winners",
    #    "The winner of the streaming wars" are business prose). ──
    # A holder's windfall: "Anyone who bought NVIDIA early made a fortune", "Everyone who held
    # Costco has done extremely well". The subject is the person who BOUGHT or HELD.
    ("valuation", r"\b(?:anyone|anybody|everyone|everybody|whoever|those|someone|investors|"
                  r"shareholders|holders|owners|backers|people)\s+(?:who|that)\s+(?:(?:had\s+)?"
                  r"(?:bought|held|owned|kept|backed|picked up)|invested\s+in|got\s+in|bet\s+on|"
                  r"stuck\s+with|held\s+onto|hung\s+on\s+to)\b[^.!?\n]{0,50}?\b(?:(?:made|earned|"
                  r"pocketed|banked|became|got|grew|ended up|turned)\s+(?:(?:very|really|incredibly|"
                  r"seriously)\s+)?(?:a\s+(?:small\s+|tidy\s+|life-changing\s+)?fortune|millions|"
                  r"a million|rich|wealthy|millionaires?)|(?:has|have|had)\s+done\s+(?:(?:very|"
                  r"extremely|incredibly|remarkably|exceptionally|spectacularly|really|so|quite|"
                  r"pretty|phenomenally)\s+)?well)\b", True),
    # Holders did well: "Long-term Costco shareholders have done extremely well".
    ("valuation", r"\b(?:shareholders|stockholders|investors|owners|holders|backers)\b[^.!?\n]{0,30}?"
                  r"\b(?:have|has|had)\s+(?:(?:all|also|historically|generally|consistently)\s+)?"
                  r"done\s+(?:(?:very|extremely|incredibly|remarkably|exceptionally|spectacularly|"
                  r"really|so|quite|pretty|phenomenally)\s+)?well\b", True),
    # A market's winners: "one of the decade's biggest winners", "the biggest winners of the AI
    # boom", "NVIDIA stock was among the best winners". Only with a MARKET frame (decade, year,
    # boom, rally, index) or an instrument word — "biggest winners of the streaming wars" is the
    # business.
    ("valuation", r"\b(?:biggest|best|top|greatest|largest)[- ](?:stock[- ]market\s+|market\s+)?"
                  r"winners?\b[^.!?\n]{0,30}?\bof\s+(?:the\s+|this\s+|that\s+)?(?:[a-z0-9&'-]+\s+)"
                  r"{0,2}?(?:decade|year|market|stock market|boom|rally|bull market|bull run|century|"
                  r"index|s&p 500|nasdaq|dow)\b"
                  r"|\b(?:decade|year|market|boom|rally|century|index|s&p 500|nasdaq)'s\s+(?:biggest|"
                  r"best|top|greatest|largest)[- ]winners?\b"
                  r"|\b(?:stock|stocks|shares)\b[^.!?\n]{0,40}\b(?:biggest|best|top|greatest|"
                  r"largest)[- ]winners?\b", True),
    # The move said by ELLIPSIS: "Data-center sales hit record highs, and so did the stock",
    # "Profits hit records, and the stock followed", "…, as did the shares". "…, and so did the
    # stock of spare parts" is inventory.
    ("valuation", r"\b(?:and|as)\s+(?:so\s+)?did\s+(?:the|its|their|[a-z0-9&.-]{1,40}'s)\s+"
                  r"(?:company's\s+)?(?:stock|shares|share price|stock price)\b(?!\s+of\b)"
                  r"|\b(?:the|its|their)\s+(?:stock|shares|share price|stock price)\s+(?:followed"
                  r"(?:\s+suit)?(?=\s*(?:[.,;:!?)]|$))|did (?:too|the same|likewise|as well)|went "
                  r"(?:along )?with (?:it|them)|kept pace|tagged along|was not far behind|wasn't far "
                  r"behind)", True),
    # The run is not over, said with a pronoun (in a Money Moves post "its" IS the company).
    ("forward", r"\b(?:its|their)\s+(?:[a-z-]+\s+){0,2}?(?:run|rally|story|growth|boom|dominance|"
                r"lead|streak|momentum|golden age|best (?:days|years)|runway|reign)\s+(?:(?:is|are|"
                r"may|might|could|should|will|looks?|seems?|lie|lies|be|still|only|just|yet)\s+){0,4}"
                r"(?:far from over|nowhere near (?:over|done)|not (?:yet )?over|(?:getting )?"
                r"(?:started|beginning)|ahead(?! of\b)|ahead of (?:it|them)|yet to come|to come)\b"
                r"|\b(?:years|decades) of (?:growth|gains|upside|dominance|runway) (?:ahead|left|"
                r"to come)\b", True),
    # A verdict NOUN, whatever the subject ("NVIDIA stock is a buy", "Is NVIDIA still a buy?").
    ("recommendation", r"\b(?:is|was|are|were|remains?|remained|looks? like|looked like|seems? "
                       r"like|seemed like|still|as|be|became|becomes?|stays?)\s+(?:still\s+|now\s+|"
                       r"clearly\s+|really\s+)?an?\s+(?:strong\s+|clear\s+|screaming\s+|easy\s+|"
                       r"obvious\s+)?(?:buy|sell|hold)\b(?!-|\s+(?:and|or|&)\s)", False),
    ("recommendation", r"\b(?:good|great|smart|better|best) buy\b", False),
    ("recommendation", r"\bworth (?:buying|owning|holding)\b", False),
    ("recommendation", r"\bbuy(?:ing)? the dip\b", False),
    # A directive about ONE instrument ("sell the stock", "buy its shares"). Generic
    # description ("owning shares means owning part of a business") is education, not this.
    ("recommendation", r"\b(?:buy|sell|hold|short|dump|load up on) (?:the|its|this|that) "
                       + _INSTRUMENT + r"\b", False),
    ("recommendation", r"\b(?:you|investors|readers) should (?:buy|sell|hold|own|short|dump)\b", False),
    ("recommendation", r"\bshould (?:you )?(?:buy|sell|hold|own|invest)\b", False),
    ("recommendation", r"\btime to (?:buy|sell|invest)\b", False),
    ("recommendation", _CONSIDER_ROW, False),
    ("recommendation", r"\b(?:might|may) (?:want to )?(?:buy|sell|consider buying|consider selling)\b", False),
    ("recommendation", _BUY_WHEN_ROW, False),
    ("recommendation", r"\b(?:strong )?(?:buy|sell|hold) (?:rating|signal|call)\b", False),
    ("recommendation", r"\bbetter (?:bet|investment|stock|pick)\b", False),
    ("recommendation", r"\bsmarter (?:bet|buy|investment)\b", False),
    ("recommendation", r"\bwinners? for investors\b", False),
    ("forward", r"\bwill (?:soar|skyrocket|double|triple|10x|moon|crash|collapse|outperform|"
                r"underperform|rally|surge|explode)\b", False),
    ("forward", r"\bpoised to\b", False),
    ("forward", r"\bset to (?:soar|double|surge|rise|jump|explode|rally)\b", False),
    ("forward", r"\bcould (?:soar|double|triple|10x|skyrocket|explode)\b", False),
    ("forward", r"\b(?:[0-9]+|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|"
                r"fifty|hundred)[- ]?baggers?\b", False),
    ("forward", r"\bmulti-?baggers?\b", False),
    ("forward", r"\b[0-9]+x (?:returns?|gains?|upside)\b", False),
    ("forward", r"\bto the moon\b", False),
)
# ── tier-1 clause exemptions (round 2: W2CB-12, real drafts) ──
#
# Three rows are words a case study or a behaviour lesson uses for something that is NOT an
# opinion on an instrument. Each exemption reads the match's own sentence in the company view
# (a named company is `COMPANY_MARK`), so the instrument readings stay rejected.

#: Words that make the thing bet on (or the subject of the verdict) an INSTRUMENT.
_BET_INSTRUMENT = (r"(?:" + _CO_MARK.lower() + r"|stocks?|shares|equit\w*|it|them|index|indexes|"
                   r"etfs?|funds?|market|markets|bitcoin|crypto|500|s&p|dow|nasdaq|bonds?|gold)")
_NOT_INSTR_WORD = (r"(?!(?:" + _CO_MARK.lower() + r"|stocks?|shares|equit\w*|buy|buying|sell|selling|"
                   r"own|owning|hold|holding|invest\w*|it|them|index|etfs?|funds?|company|business|"
                   r"firm)\b)")
#: The bet is a DECISION: "The metaverse pivot was a risky bet", "Meta made a risky bet", "Was
#: the metaverse a bad bet?". Anchored at the end of the text before the verdict adjective.
_BET_DECISIONS = (r"(?:pivot|decision|move|acquisition|deal|strategy|plan|launch|project|expansion|"
                  r"shift|push|venture|purchase|entry|foray|experiment|gamble|choice|approach|"
                  r"spending|investment|programme|program|division|unit|lab|labs|metaverse)")
_BET_DECISION_BEFORE_RE = re.compile(
    r"\b" + _BET_DECISIONS + r"\b(?:\s+" + _NOT_INSTR_WORD
    + r"[a-z0-9'&-]+){0,4}?\s+(?:was|is|were|are|became|looked|looks|proved|remains?|remained|"
    r"seemed|seems|turned out to be|looked like|looks like)\s+(?:(?:still|clearly|always|arguably)"
    r"\s+)?(?:an?|the|one)\s+(?:(?:very|truly|really)\s+)?$"
    r"|\b(?:made|make|makes|making|took|take|takes|taking|placed|place|places|placing)\s+(?:a|an|"
    r"its|one|another|the|this|that|such an?)\s+(?:(?:very|truly|really|big|huge|bold)\s+)?$"
    r"|\b(?:was|is|were|are)\s+(?:the|this|that|its|their)\s+(?:[a-z-]+\s+)?" + _BET_DECISIONS
    + r"\s+(?:an?|the)\s+$"
)
#: Whoever MADE the bet: investors / owners / "you" betting is a bet on an instrument ("Early
#: investors made a smart bet"), a company or its team betting is a business decision.
_BET_INVESTOR_BETTOR_RE = re.compile(
    r"\b(?:investors?|shareholders?|owners?|holders?|you|your|traders?|backers?|savers?|buyers?|"
    r"anyone|everyone|people|beginners)\b")
#: The verdict's subject is the company or an instrument ("TSMC is the safest bet", "it was a
#: risky bet" — in a case study "it" is the company).
_BET_INSTRUMENT_SUBJECT_RE = re.compile(
    r"\b" + _BET_INSTRUMENT + r"(?:'s)?\s+(?:[a-z]+ly\s+)?(?:is|was|are|were|looks?|looked|seems?|"
    r"seemed|remains?|remained|became|becomes|stays?|stayed|still)\s+(?:still\s+|now\s+|clearly\s+)?"
    r"(?:an?|the|one of the)\s+(?:[a-z-]+\s+)?$"
)
_BET_ON_RE = re.compile(r"\s+(?:on|in)\s+(?:(?:the|its|their|a|an|this|that)\s+)?([a-z0-9&'-]+)")
_BET_FOR_OWNERS_RE = re.compile(
    r"\s+for\s+(?:(?:long-term|patient|new|most|every|any|your)\s+)?(?:investors|you\b|your\b|"
    r"shareholders|savers|beginners|a portfolio|portfolios|anyone|owners|retirement)")
_BET_WORD_RE = re.compile(r"bets?$")
_BET_INSTRUMENT_RE = re.compile(_BET_INSTRUMENT + r"$")


def _business_bet(view: str, m: "re.Match[str]") -> bool:
    """`m` (the `_INVEST_VERDICT` row) is "a <verdict> bet" about a business DECISION, not about
    an instrument. Bounded look-around: linear."""
    if not _BET_WORD_RE.search(m.group(0)):
        return False
    after = view[m.end():m.end() + 60]
    if _BET_FOR_OWNERS_RE.match(after):
        return False                               # "a safe bet for investors"
    obj = _BET_ON_RE.match(after)
    if obj and _BET_INSTRUMENT_RE.match(obj.group(1)):
        return False                               # "a smart bet on NVIDIA", "on its stock"
    before = _after_last(_HARD_CUT_RE, view[max(0, m.start() - 80):m.start()])
    if _BET_DECISION_BEFORE_RE.search(before):
        return not _BET_INVESTOR_BETTOR_RE.search(before)      # "early investors made a smart bet"
    # "Reality Labs was a risky bet on the metaverse": a non-instrument object, and the subject
    # is not the company or an instrument.
    return bool(obj and obj.group(0).lstrip().startswith("on")
                and not _BET_INSTRUMENT_SUBJECT_RE.search(before))


#: A directive the clause negates ("Don't panic-sell when prices fall", "never buy when
#: everyone is euphoric") is the behavioural lesson, not advice.
_ADVICE_NEGATED_RE = re.compile(
    r"\b(?:don't|do not|never|avoid|no need to|stop|resist the urge to|instead of|rather "
    r"than|nobody should|no one should|shouldn't|should not)\b")
#: The directive DESCRIBES an impulse the lesson corrects ("a first-order response might be to
#: consider buying shares of the manufacturer" — journey:second_order_thinking, a real draft;
#: "The temptation is to sell when prices fall"). Advice in the same clothes ("your first move
#: should be to buy") has no descriptive copula and stays a violation.
_IMPULSE_FRAME_RE = re.compile(
    r"\b(?:response|reaction|instinct|impulse|urge|temptation|reflex|first thought|gut feeling|"
    r"mistake|trap|tendency|knee-jerk)\b[^.!?;:]{0,30}\b(?:is|was|might be|may be|"
    r"would be|could be|tends to be|becomes|became)\s+(?:to\s+)?$")


def _advice_framed(view: str, m: "re.Match[str]") -> bool:
    if m.group(0) in ("buy high", "sell low"):
        for pair in _MISTAKE_PAIR_RE.finditer(view, max(0, m.start() - 30),
                                              min(len(view), m.end() + 30)):
            if pair.start() <= m.start() < pair.end():
                return True
    # Round 3 (fresh real run, journey:fomo_cycle): "an emotional pattern that often leads
    # investors to buy when prices are high and sell when prices are low" DESCRIBES the mistake;
    # "When his fear makes prices low, you can consider buying" is still advice.
    return (_exempted_by_scope(view, m.start(), _ADVICE_NEGATED_RE)
            or bool(_IMPULSE_FRAME_RE.search(view[max(0, m.start() - 80):m.start()]))
            or _described_not_directed(view, m.start()))


_TIER1_EXEMPTIONS = {
    _INVEST_VERDICT_ROW: _business_bet,
    _CONSIDER_ROW: _advice_framed,
    _BUY_WHEN_ROW: _advice_framed,
}
#: (code, compiled, strict_only, exemption or None).
_CLASS_B_TIER1_RE = tuple((code, re.compile(p), strict, _TIER1_EXEMPTIONS.get(p))
                          for code, p, strict in CLASS_B_TIER1)


def _tier1_first(rx: re.Pattern, exempt, folded: str, views) -> "re.Match[str] | None":
    """The first match of a tier-1 row: over the folded text, or — for a row with a clause
    exemption — over each sentence's company view, skipping exempt matches."""
    if exempt is None:
        return rx.search(folded)
    for view in views():
        for m in rx.finditer(view):
            if not exempt(view, m):
                return m
    return None

#: Evaluative words that are only a verdict when the sentence is about the instrument.
_TIER2_WORDS_RE = re.compile(
    r"\b(?:cheap|cheaper|cheapest|expensive|inexpensive|overpriced|underpriced|pricey|bargain|"
    r"bargains|steal|upside|downside|attractive|compelling|discount|discounted)\b"
)
#: A tier-2 adjective on the LESSON, not the instrument (round 2, real drafts): "offers a
#: compelling case study", "a compelling example of switching costs". Only teaching nouns —
#: "a compelling story" / "an attractive valuation" / "a compelling buy" stay verdicts.
_TIER2_LESSON_NOUN_RE = re.compile(
    r"\s+(?:case study|case studies|example|examples|lesson|lessons|illustration|read|"
    r"question|contrast|comparison|study|way to learn|thought experiment)\b")


def _tier2_hit(fs: str) -> "re.Match[str] | None":
    for m in _TIER2_WORDS_RE.finditer(fs):
        if not _TIER2_LESSON_NOUN_RE.match(fs, m.end()):
            return m
    return None


_TIER2_CONTEXT_RE = re.compile(
    r"\b(?:" + "|".join(_INSTRUMENT_WORDS) + r"|valuation|valuations|investors?|investment|"
    r"investments|market|market value|multiple|multiples|p/e|to own|to buy|shareholders?)\b"
)

#: A percentage in a sentence about investment performance (App Store: "any % return figure").
#: Anchored at the START of a digit run (the look-behind), so each run is tried once: the
#: unanchored form retried from every digit and was quadratic on "9" * 6000 (~0.3 s per call).
_PERCENT_RE = re.compile(r"(?<![0-9.,])[.,]?[0-9][0-9.,]*\s?(?:%|percent\b|per cent\b|bps\b|"
                         r"basis points?\b)")
#: Words that make a percentage a return. Round 2 (W2-OB-2): "earn(s)", "compound(s)" and
#: "multiplied" are also how a case study states a business RATIO ("Visa and Mastercard earn
#: operating margins above 50 percent", "Services carry gross margins around 70%, and that is
#: where the strategy compounds"), so a sentence whose every percentage is BOUND to a ratio
#: (`_all_ratio_bound`) is not a return on the strength of these words. A compound RATE ("
#: Compounding near twenty-nine percent turns one dollar into twenty-seven") binds to nothing and
#: stays a return figure.
_RETURN_WORDS_RE = re.compile(
    r"\b(?:returns?|returned|gains?|gained|earned|earning|earns?|annuali[sz]ed|cagr|compounded|"
    r"compounding|compounds?|multiplied|outperform(?:ed|s|ing)?|beat(?:ing)? the market|profit on|"
    r"yield(?:ed|s)?|paid off)\b"
)
#: "grew"/"rose" are revenue verbs ("Revenue grew 10% in 2019" is a case-study fact); next to an
#: investor, a stake, savings, a fund or the stock market they are a return ("The index grew
#: 10% a year", "Stocks have averaged about 3% a year above inflation" — round 2, W2CB-8: a
#: sheet's inflation rate must not become an equity return). Bare "the market" is not a
#: subject: "the market for streaming grew 30%" is a market-size fact.
_INVESTOR_GROWTH_RE = re.compile(
    r"\b(?:investors?|shareholders?|owners?|holders?|stakes?|savings|portfolios?|nest eggs?|"
    r"your money|investments?|funds?|index|indexes|indices|equities|the stock market|stock markets|"
    # Determiner-bound, because each is also a verb or a ledger line: "Visa shares its network",
    # "Home Depot stocks 35,000 products", "accounts payable grew 20%".
    r"(?:the|its|their|these|those|company's|early|long-term) shares|(?:the|your|an|this|that|"
    r"brokerage|investment|retirement|savings) accounts?|(?<!\bit )(?<!\bwhich )(?<!\bthat )"
    r"(?<!\bwho )(?<!zzco )stocks)\b[^.!?\n]{0,40}\b(?:grew|grow|grows|growing|rose|"
    r"climbed|jumped|soared|surged|doubled|doubles?|doubling|tripled|triples?|tripling|"
    r"averag(?:ed|es|ing|e)|beat(?:en|s|ing)?|outpac(?:ed|es|ing|e)|outr(?:an|un|uns|unning)|"
    r"earns?|earning|earned|compounds?|compounded|compounding|multiplied|paid off|pays? off|"
    r"returned|gained|yielded)\b"
)
#: A percentage BOUND to a business ratio — "operating margins above 50 percent", "70% gross
#: margins", "about 60 percent of Amazon's operating profit", "renewal rates above 90%": a case
#: study's fact, whatever verb states it ("Visa earned operating margins above 50% in 2023" —
#: the prompt asks for dated figures in the past tense). A sentence whose EVERY percentage is
#: so bound is no return figure on the strength of a return word alone; an investor, a fund or
#: a stake in it (`_INVESTOR_GROWTH_RE`) still is ("The fund earns 12% a year on margin debt").
_RATIO_BEFORE_RE = re.compile(
    r"\b(?:margins?|markups?|mark-ups?|renewal rates?|retention rates?|take rates?|market share|"
    r"share of (?:[a-z'&.-]+ ){0,3}(?:revenue|revenues|sales|profits?|income))\b"
    r"(?:\s+(?:of|at|above|below|near|around|north of|over|under|about|roughly|nearly|close to|"
    r"sat|sit|sits|stayed|stay|stays|is|are|was|were|reached|hit|topped|exceeded|hovered|"
    r"hovering|approaching|of about|of roughly|well above|comfortably above)){0,3}\s*~?$"
)
_RATIO_AFTER_RE = re.compile(
    r"\s*\+?\s*(?:(?:gross|operating|net|profit|pre-tax|ebitda)\s+)?(?:margins?|markups?)\b|"
    r"\s*\+?\s*of\s+(?:[a-z'&.-]+\s+){0,3}?(?:(?:operating|net|gross)\s+)?(?:profits?|revenues?|"
    r"sales|income|earnings)\b"
)


def _all_ratio_bound(conv: str) -> bool:
    """Every percentage in the (converted, folded) sentence is bound to a business ratio, and
    it has no multiple. Bounded look-around per percentage: linear."""
    if _MULTIPLE_RE.search(conv):
        return False
    found = False
    for m in _PERCENT_RE.finditer(conv):
        found = True
        if not (_RATIO_BEFORE_RE.search(conv[max(0, m.start() - 60):m.start()])
                or _RATIO_AFTER_RE.match(conv, m.end(), min(len(conv), m.end() + 60))):
            return False
    return found
#: A doubling SPELLED as a verb with an INVESTMENT as its subject (round 3, W3CB-9): "Stocks
#: roughly double in about twenty-four years", "The S&P 500 can roughly double your money",
#: "Index funds doubled", "Amazon shareholders doubled their money", "saw their stake roughly
#: double". A return multiple whether or not a digit is written (`_MULTIPLE_RE` needs one).
#: SUBJECT-ADJACENT — at most three filler words between subject and verb — so the inflation
#: sheet's own "Inflation eats your savings: prices roughly double" stays clean: the verb's
#: subject there is "prices". A company's verb ("Home Depot stocks double-size packs", "The fund
#: doubled its staff", "Investors doubled down") and a count ("the number of shareholders
#: doubled") are not returns.
#: No PERSON subject here: "Retail investors doubled in 2021" is a head count; a holder's return
#: is said with its object ("shareholders doubled their money" — the second alternative).
_WORD_MULTIPLE_SUBJ = (r"(?:stocks|equities|the stock market|stock markets|the s&p 500|s&p 500|"
                       r"the index|index funds?|an index fund|etfs?|an etf|(?:the|your|an?|this|that|"
                       r"their|my) funds?|investments?|your (?:money|savings|portfolio|nest egg|"
                       r"investments?|stake|shares)|invested money|(?:the|its|their) (?:shares|stock)|"
                       + _CO_MARK.lower() + r"(?:'s)? (?:stock|shares))")
_WORD_MULTIPLE_FILL = (r"(?:(?:have|has|had|can|could|would|may|might|will|should|tend to|tends to|"
                       r"tended to|typically|usually|historically|roughly|about|nearly|almost|"
                       r"more than|often|all|also|then|each|[a-z]+ly)\s+){0,3}")
_WORD_MULTIPLE_VERB = r"(?:doubl(?:e|es|ed|ing)|tripl(?:e|es|ed|ing)|quadrupl(?:e|es|ed|ing))"
#: What a BUSINESS doubles: after the verb, an operating object ("doubled its staff", "doubled
#: their share of the market", "doubled down").
_NOT_A_RETURN_OBJECT = (r"(?!-|\s+down\b|\s+(?:(?:its|their|the|a|an|his|her|our)\s+)?(?:[a-z-]+\s+)?"
                        r"(?:staff|headcount|workforce|size|production|output|capacity|sales|"
                        r"revenues?|profits?|earnings|stores?|footprint|share of|market share|"
                        r"dividends?|payouts?|buybacks?|employees|team|budget|spending|bets?|"
                        r"efforts?|orders?|prices?|fees?|assets|holdings)\b)")
_WORD_MULTIPLE_RE = re.compile(
    r"(?<!\bit )(?<!\bwhich )(?<!\bthat )(?<!\bwho )(?<!" + _CO_MARK.lower() + r" )(?<!"
    + _CO_MARK.lower() + r"'s )\b" + _WORD_MULTIPLE_SUBJ
    + r"\s+" + _WORD_MULTIPLE_FILL + _WORD_MULTIPLE_VERB + r"\b" + _NOT_A_RETURN_OBJECT
    + r"|\b" + _WORD_MULTIPLE_VERB + r"\s+(?:their|your|his|her|investors'|shareholders'|holders'|"
    r"owners'|a saver's|an investor's)\s+(?:money|stake|investment|investments|savings|wealth|"
    r"nest eggs?|capital)\b"
    r"|\b(?:their|your|his|her) (?:money|stake|investment|savings|portfolio|nest egg|shares)\s+"
    + _WORD_MULTIPLE_FILL + _WORD_MULTIPLE_VERB + r"\b" + _NOT_A_RETURN_OBJECT
)
#: The subject is a COUNT of holders ("the number of Prime shareholders doubled").
_COUNT_OF_RE = re.compile(r"\b(?:number|count|base|ranks|share) of (?:[a-z-]+\s+){0,2}$")


def _word_multiple_hit(view: str) -> "re.Match[str] | None":
    for m in _WORD_MULTIPLE_RE.finditer(view):
        if not _COUNT_OF_RE.search(view[max(0, m.start() - 40):m.start()]):
            return m
    return None


#: A return magnitude with no digit at all ("triple-digit gains").
_NDIGIT_RETURN_RE = re.compile(
    r"\b(?:double|triple|quadruple)[- ]digits? (?:gains?|returns?|upside|rall(?:y|ies)|jumps?|"
    r"surges?|run-?ups?|moves?|percentage gains?|annual returns?)\b"
)
_NDIGIT_GROWTH_RE = re.compile(r"\b(?:double|triple|quadruple)[- ]digits? growth\b")
#: A multiple, after `words_to_digits` ("a hundredfold" → "a 100x", "a ten-bagger" →
#: "a 10-bagger"): a return next to a return word or an investor's stake (idx 9).
_MULTIPLE_RE = re.compile(r"(?<![0-9.])[0-9]+(?:\.[0-9]+)?\s?(?:x|times|-?fold|-?baggers?)(?![a-z])")
#: "an all-time high" is a price record; a record in inflation, rates or sales is not.
_RECORD_RE = re.compile(r"\b(?:all-time|record) (?:highs?|lows?)\b")
#: What may set a record without it being a price level. Round 2 (W2CB-5): the noun must BE the
#: record's subject — "Costco's membership fees hit record highs", "inflation hit a record high",
#: "record highs in sales" — never merely appear in the sentence: "NVIDIA kept hitting record
#: highs as demand grew" is the stock's record, with "demand" in a different clause.
_RECORD_NOUNS = (r"(?:inflation|unemployment|interest rates?|rates|debt|mortgages?|temperatures?|"
                 r"jobless|deficits?|borrowing|sales|revenues?|profits?|earnings|demand|membership|"
                 r"memberships|members|users|subscribers|deliveries|production|output|traffic|fees|"
                 r"renewals?|margins?|orders|bookings|shipments|downloads|visits|signups|sign-ups)")
_RECORD_EXEMPT_RE = re.compile(
    r"\b" + _RECORD_NOUNS + r"\b(?:\s+[a-z0-9'-]+){0,3}?\s+(?:hit|hits|set|sets|reached|reaches|"
    r"touched|notched|hitting|setting|reaching|kept (?:hitting|setting|reaching|notching)|"
    r"(?:climbed|rose|jumped|soared|surged|fell|dropped|sank|plunged|grew) to|(?:was|were|is|are|"
    r"stood|sat|remained|stayed) (?:at|near)|at|near|to)\s+(?:new\s+|fresh\s+|an?\s+|repeated\s+"
    r"|another\s+)?$"
)
_RECORD_AFTER_RE = re.compile(r"\s+(?:in|for|of)\s+(?:[a-z-]+\s+){0,2}?" + _RECORD_NOUNS + r"\b")

# ── class B: rows about a NAMED company (the `_company_view` placeholder is the instrument) ──
#
# A name is the instrument wherever "(its|the) (shares|stock)" is (idx 3/10): "Buy NVIDIA",
# "Costco is a great investment", "AMD will keep climbing", "Apple climbed 70%", "NVIDIA was
# worth $6.9 billion". Past-tense business history ("Microsoft bought GitHub", "Intel fell
# behind", "Tesla doubled production") is what a case study is made of and stays legal.

_Z = _CO_MARK.lower()
_CO_SUBJ = (r"\b" + _Z + r"(?:'s)?(?:\s+(?:stock|shares))?(?:\s+(?:and|&)\s+" + _Z
            + r"(?:'s)?(?:\s+(?:stock|shares))?)?")
_CO_SEEM = (r"(?:(?:can|could|may|might|will|would|should)\s+)?(?:is|was|are|were|looks?|looked|"
            r"seems?|seemed|remains?|remained|became|becomes?|become|appears?|appeared|stays?|"
            r"stayed|feels?|felt|trades? like|traded like|be)")
_CO_VERDICT = (r"(?:" + _PRICE_VERDICT + r"|attractive|compelling|on sale|an? (?:strong |clear |"
               r"screaming |easy |obvious )?(?:buy|sell|hold)\b|(?:a |an |the |one of the )?"
               + _INVEST_VERDICT + r"|(?:a |an |the )?(?:good|great|wonderful|smart|safe|safest|"
               r"solid|wise|excellent|best|perfect|ideal|terrible|bad|poor|risky|core|ultimate|"
               r"classic|quintessential) (?:long-term )?(?:stocks?|shares?|compan(?:y|ies) to own)|(?:in )?an? bubble|overhyped|over-?rated|"
               r"underrated|over-?bought|over-?sold|priced for perfection|(?:fairly|reasonably|"
               r"richly|fully) (?:priced|valued)|worth the (?:hype|price|premium|money)|(?:a |the )?"
               r"long-term winners?|(?:the |a )?(?:one|stock|name) to (?:own|buy|hold)(?: forever)?|"
               r"(?:a )?must-own|(?:a |an )?(?:easy |obvious )?no-?brainer)")
_CO_MOVE_VERB = (r"(?:rose|climbed|rallied|soared|surged|jumped|plunged|fell|tumbled|sank|crashed|"
                 r"tanked|doubled|tripled|quadrupled|gained|skyrocketed|slumped|slid|dropped|"
                 r"rebounded|popped|spiked|dipped|cratered|collapsed|rises|climbs|soars|jumps|falls|"
                 r"drops|doubles|shot (?:up|higher)|exploded higher|went through the roof|"
                 r"went parabolic|marched (?:higher|up)|rocketed(?: higher)?|nosedived)")
#: What may follow a company's price move: an amount, a time, a cause — never an object, which
#: makes it business history ("Tesla doubled production", "Intel fell behind").
_CO_MOVE_TAIL = (r"(?=\s*(?:$|[.,;:!?)]|[0-9$]|(?:as|after|on|over|when|while|since|during|by|"
                 r"again|sharply|steadily|higher|lower|overnight|nearly|almost|roughly|about|some|"
                 # "in 2020" is a year ("in [0-9]" alone left no word boundary inside it).
                 r"more than|another|in (?:[0-9]+|value|price|a (?:year|day|week|month|decade))|"
                 r"for (?:years|decades|months|weeks|days|a (?:year|decade|while)|[0-9]+)|"
                 r"that (?:year|day|week|month|decade)|this year|last year|the (?:next|following) "
                 r"(?:year|day|week|month)|[a-z]+fold)\b))")
# ── round 3: company-row ALTERNATIVES that carry their own pins (W3CB-6, W3VAC-03) ──
# Each is a named constant so `test_marketing_content_r3_entities.py` can neutralise exactly one
# alternative of its row and watch its sample lose the code: a row-level sample proves nothing
# about a sibling alternative (the label branch below survived deletion with the suite green).
#: A store's price, not a stock's: a product or a customer inside the label ("Costco: Hot Dogs
#: At A Fair Price", "Netflix: Streaming At A Fair Price").
_STORE_PRICE_WORDS = (r"(?:goods|products?|groceries|grocery|dogs?|items?|food|meals?|gas|fuel|"
                      r"streaming|subscriptions?|memberships?|shipping|deliver(?:y|ies)|rides?|"
                      r"flights?|tickets?|cars?|phones?|chips?|clothes|clothing|furniture|tools?|"
                      r"plans?|bulk|drinks?|coffee|shoppers?|customers?|members?|families|guests?)")
#: The label form of a title: "Coke: A Wide Moat At A Fair Price", "NVIDIA: Dominance At A
#: Reasonable Price". "Disney bought Pixar at a fair price" is a deal, and has no label.
#: POSITIONAL: only the HEAD word priced ("Hot Dogs At A Fair Price") exempts — a store word
#: elsewhere in the label ("Costco: A Membership Moat At A Fair Price") does not.
_CO_LABEL_PRICE_ALT = (r"\b" + _Z + r"\s*:\s*(?:[^.!?]{0,40}?\s)?(?!" + _STORE_PRICE_WORDS
                       + r"\s)[a-z0-9&'-]+\s+at an?\s+(?:great|good|fair|reasonable|attractive|"
                       r"bargain|nice|terrific|fantastic|decent)\s+(?:price|valuation)\b")
#: "Costco is the ultimate buy-and-hold stock", "Costco: The Ultimate Buy-And-Hold Stock" (a
#: title has no verb). "buy-and-hold investors" is the strategy a lesson names, not a verdict.
_BUY_AND_HOLD_NOUNS = (r"(?:stocks?|shares?|compan(?:y|ies)|names?|picks?|business(?:es)?|"
                       r"investments?|holdings?|candidates?|plays?|champions?|classics?)")
_CO_BUY_AND_HOLD_ALT = (r"\b" + _Z + r"\b[^.!?]{0,60}?\bbuy[- ]and[- ]hold " + _BUY_AND_HOLD_NOUNS
                        + r"\b|\bbuy[- ]and[- ]hold " + _BUY_AND_HOLD_NOUNS + r"\b[^.!?]{0,40}?\b"
                        + _Z + r"\b")
#: "NVIDIA is still in the early innings", "early innings for Costco". Present tense: "In 2007
#: Netflix was in the early innings of streaming" is history.
_CO_INNINGS_ALT = (r"\b" + _Z + r"\b[^.!?]{0,40}?\b(?:is|are|remains?|stays?|looks?|seems?|feels?|'s)"
                   r"\s+(?:(?:still|only|just|now|arguably|clearly|[a-z]+ly)\s+){0,2}(?:in\s+)?"
                   r"(?:the\s+|its\s+)?(?:very\s+)?(?:early|first|opening) innings\b"
                   r"|\b(?:early|first|opening) innings\s+(?:for|of)\s+" + _Z + r"\b")
#: "The best is yet to come for Costco", "Costco's best is still ahead of it" (the company row
#: runs only in a sentence that names the company).
_CO_BEST_TO_COME_ALT = (r"\b(?:the\s+)?best\s+(?:is|may be|might be|could be|has)\s+(?:still\s+)?"
                        r"(?:yet\s+to\s+come|to\s+come|still\s+ahead|ahead(?! of\b))")
#: "Costco has a long runway ahead", "NVIDIA still has plenty of runway". Present tense only:
#: "Amazon had a long runway for growth in 2000" is history.
_CO_RUNWAY_ALT = (r"\b" + _Z + r"\b[^.!?]{0,40}?\b(?:has|have|'s got|still has|still have)\s+"
                  r"(?:(?:still|now|arguably|clearly)\s+)?(?:a\s+)?(?:(?:very|really)\s+)?(?:long|"
                  r"lengthy|huge|big|massive|plenty of|lots of|a lot of|years of|decades of)\s+"
                  r"(?:growth\s+)?runway\b"
                  r"|\b" + _Z + r"(?:'s)?\s+(?:[a-z-]+\s+){0,2}?runway\s+(?:is|looks?|seems?|remains?)"
                  r"\s+(?:still\s+)?(?:long|huge|enormous|vast)\b")
_COMPANY_ROWS: Tuple[Tuple[str, str], ...] = (
    ("recommendation",
     r"(?:^|[,;:]\s*|\b(?:so|then|and|just|simply|now|today|you|investors|readers|everyone|"
     r"beginners|should|must|consider|why not|need to|ought to|want to|time to|better to|smart to|"
     r"wise to|don't|do not|never|always|instead)\s+)(?:buy(?:ing)?|sell(?:ing)?|hold(?:ing)?|"
     r"own(?:ing)?|short(?:ing)?|dump(?:ing)?|accumulat(?:e|ing)|add(?:ing)?|load(?:ing)? up on|"
     r"pick(?:ing)? up|tak(?:e|ing) profits? (?:on|in)|invest(?:ing)? in|stick(?:ing)? with|"
     r"get(?:ting)? out of|trim(?:ming)?)\s+(?:some\s+|more\s+|shares of\s+|some of\s+|"
     # "own a little NVIDIA", "own a piece of Costco" (round-2 W2V-02): the object slot is any
     # small-amount phrase, but the company placeholder must follow it — "own a piece of
     # critical infrastructure" (visa-vs-mastercard) is a business model, not a directive.
     r"(?:a |an )?(?:little|lot|bit|piece|slice|stake|position|small position|small stake|chunk|"
     r"share|few shares)(?: of| in)?\s+)?" + _Z + r"\b"),
    ("valuation", _CO_SUBJ + r"\s+" + _ADV + _CO_SEEM + r"\s+(?:still\s+|now\s+|clearly\s+|"
     r"arguably\s+)?" + _DEGREE + r"(?:like\s+)?" + _CO_VERDICT + r"(?! to\b)"),
    ("valuation", r"\b(?:is|was|are|were|does|did)\s+" + _CO_SUBJ + r"\s+(?:still\s+|now\s+|"
     r"really\s+|actually\s+)?" + _DEGREE + r"(?:like\s+)?" + _CO_VERDICT),
    ("forward", _CO_SUBJ + r"\s+" + _ADV + r"(?:will|'ll|is going to|are going to|is set to|are set "
     r"to|is likely to|are likely to|is poised to|are poised to|is expected to|are expected to|"
     r"looks set to|seems set to|is bound to|are bound to|is due to|is destined to|is sure to)\b"
     r"|\bexpect(?:s|ed|ing)?\s+" + _CO_SUBJ + r"\s+to\b|" + _CO_SUBJ + r"\s+" + _ADV
     + r"(?:has|have)\s+(?:a lot of |plenty of |more |lots of |ample )?room to (?:run|grow)\b"),
    ("valuation", r"\b" + _Z + r"(?:(?:'s)?\s+(?:stock|shares)(?:\s+price)?)?\s+" + _ADV
     + _CO_MOVE_VERB + _CO_MOVE_TAIL),
    ("valuation", r"\b" + _Z + r"(?:(?:'s)?\s+(?:stock|shares)(?:\s+price)?)?\s+" + _ADV
     + r"(?:hit|hits|reached|reaches|touched|notched)\s+(?:an?\s+|its\s+|new\s+|fresh\s+){0,2}"
     r"(?:all-time |record |new |52-week )?(?:highs?|lows?|peaks?|\$)"),
    ("valuation", _CO_SUBJ + r"\s+" + _ADV + r"(?:is|was|are|were|became|becomes|become|would be|"
     r"could be|will be|has been|had been)\s+(?:now\s+|once\s+|still\s+)?worth\b(?!\s+(?:it\b|"
     r"[a-z]+ing\b|a (?:look|closer look|read|visit)|the (?:effort|wait|trouble|time)))"),
    # ── round 2 (W2CB-4 / W2V-02): the opinion said in words the rows above never listed ──
    # A place in a portfolio: "Costco belongs in every portfolio", "Every portfolio needs a
    # little Visa", "Make room for Costco", "Visa is the kind of business long-term investors
    # dream of owning". A placement VERB + a portfolio word, never mere co-occurrence ("The
    # group is a portfolio of brands").
    ("recommendation",
     _CO_SUBJ + r"\s+" + _ADV + r"(?:belongs?|deserves? (?:a|its) "
     r"(?:place|spot|slot|home)|has (?:a|its) (?:place|spot|slot|home)|earns? (?:a|its) (?:place|"
     r"spot|slot)|has earned (?:a|its) (?:place|spot|slot))\s+in\s+(?:(?:every|your|any|a|an|the|"
     r"each|most|their)\s+)?(?:(?:long-term|core|retirement|serious|balanced|good|smart)\s+)?"
     r"(?:portfolios?|watchlists?|retirement accounts?|iras?|401\(?k\)?s?|nest eggs?)\b"
     r"|\b(?:every|your|any|each|a|the|most)\s+(?:(?:long-term|core|retirement|serious|balanced|"
     r"good|smart)\s+)?(?:portfolios?|investors?|watchlists?)\s+(?:needs?|should (?:have|hold|own|"
     r"include)|deserves?|could use|would benefit from)\s+(?:(?:a little|a bit of|a piece of|"
     r"a slice of|some|more|a position in|a stake in)\s+)?" + _Z + r"\b"
     r"|\bmake (?:some )?room for " + _Z + r"\b"
     r"|\b" + _Z + r"\b[^.!?]{0,60}?\b(?:you|investors|everyone|anyone) (?:want|need|should (?:have|"
     r"hold|own)|would (?:want|love)|will want) (?:in|inside|for) (?:your|a|their|every|any) "
     r"(?:portfolio|watchlist|retirement account)\b"
     r"|" + _CO_SUBJ + r"\s+" + _ADV + r"(?:is|was|remains?|would be|could be|makes?)\s+" + _ADV
     + r"(?:one|a stock|a company|"
     r"a business|a name|the (?:kind|sort|type) of (?:business|company|stock|name)|an? (?:rare|"
     r"great|wonderful|good|perfect|ideal) (?:business|company|stock))\s+(?:that\s+)?(?:you|"
     r"investors|anyone|beginners|long-term investors|every investor|most investors)\s+"
     r"(?:(?:can|could|would|should|might|will|may)\s+)?(?:(?:want to|love to|dream of|be glad to|"
     r"be happy to|happily|safely)\s+)?(?:own|owning|hold|holding)\b"),
    # One of the best things to own: "Visa and Mastercard are two of the best businesses to
    # own", "TSMC is a wonderful thing to own", "Amazon is one of the greatest compounders of
    # all time". A superlative + an ownership or all-time tail.
    ("recommendation",
     # "of all time" / "ever" only for an INVESTMENT noun: "one of the greatest companies of all
     # time" praises the business; "the greatest compounders of all time" is its stock.
     _CO_SUBJ + r"\s+" + _ADV + r"(?:is|are|was|were|remains?|remained)\s+" + _ADV
     + r"(?:(?:one|two|three) of\s+)?"
     r"(?:the|a|an)\s+(?:(?:very|single)\s+)?(?:best|greatest|finest|top|wonderful|great|ideal|"
     r"perfect|ultimate|most wonderful|most attractive)\s+(?:[a-z-]+\s+){0,2}?(?:(?:business(?:es)?|"
     r"compan(?:y|ies)|stocks?|things?|compounders?|investments?|assets?|holdings?|names?)\s+"
     r"(?:(?:you|investors|anyone)\s+(?:can|could|will ever|would|should)\s+)?(?:to own|own|to "
     r"hold|to buy|money can buy)|(?:stocks?|compounders?|investments?|holdings?)\s+(?:of all time|"
     r"ever|in history))\b"
     r"|" + _CO_BUY_AND_HOLD_ALT),
    # The run is not over: "NVIDIA's run is far from over", "Apple's best years may still be
    # ahead", "NVIDIA has years of growth ahead", "Costco can keep compounding", "TSMC's lead
    # should last for years", "NVIDIA looks unstoppable". Intransitive continuation verbs only:
    # "Costco can keep prices low" is a business fact.
    ("forward",
     r"\b(?:" + _Z + r"(?:'s)?|its|their)\s+(?:[a-z-]+\s+){0,2}?(?:run|rally|story|growth|boom|"
     r"dominance|lead|streak|momentum|golden age|best (?:days|years)|runway|reign|era|rise|ascent)"
     r"\s+(?:(?:is|are|may|might|could|should|will|looks?|seems?|lie|lies|be|still|only|just|yet)"
     r"\s+){0,4}(?:far from over|nowhere near (?:over|done)|not (?:yet )?over|(?:getting )?"
     r"(?:started|beginning)|ahead(?! of\b)|ahead of (?:it|them)|yet to come|to come)\b"
     r"|\b" + _Z + r"\b[^.!?]{0,40}?\b(?:years|decades) of (?:growth|gains|compounding|upside|"
     r"dominance|profits?|runway) (?:ahead|left|to come|in front of)"
     r"|" + _CO_SUBJ + r"\s+" + _ADV + r"(?:can|could|should|may|might|would)\s+(?:(?:easily|still|likely|"
     r"probably|surely)\s+)?(?:keep|continue(?: to)?|go on)\s+(?:compound|compounding|grow|growing|"
     r"win|winning|climb|climbing|rise|rising|dominat\w*|outperform\w*|gain|gaining|rally|"
     r"rallying|soar|soaring)\b"
     r"|\b" + _Z + r"(?:'s)?\s+(?:[a-z-]+\s+){0,2}?(?:lead|advantage|moat|dominance|edge|monopoly|"
     r"position)\s+(?:should|will|may|might|could|is likely to|looks set to|seems set to)\s+"
     r"(?:last|endure|continue|persist|hold|stay|widen)\b"
     r"|" + _CO_SUBJ + r"\s+" + _ADV + r"(?:looks?|seems?|is|appears?|feels?|remains?|stays?)\s+"
     + _ADV + r"(?:(?:almost|nearly|practically|virtually|all but)\s+)?"
     r"(?:unstoppable|invincible|untouchable|unbeatable|bulletproof)\b"
     r"|" + _CO_INNINGS_ALT + r"|" + _CO_BEST_TO_COME_ALT + r"|" + _CO_RUNWAY_ALT),
    # A price verdict on the instrument in other words: "Costco's stock is a great deal",
    # "Costco's shares still look reasonable", "Costco's premium is justified", "Visa has a
    # wide moat and a great price", "Costco: A Great Deal For Investors". The instrument (or
    # "for investors") must be there: "Costco is a great deal for members" is the store.
    ("valuation",
     r"\b(?:" + _Z + r"(?:'s)?\s+(?:stock|shares|share price|stock price|valuation|multiple|"
     r"price tag)|(?:its|the|their)\s+(?:stock|shares|share price|stock price|valuation|multiple))"
     r"\s+(?:[a-z]+ly\s+|still\s+)?(?:is|are|was|were|looks?|looked|seems?|seemed|remains?|"
     r"remained|feels?)\s+(?:still\s+|fully\s+|more than\s+|well\s+|very\s+)?(?:an?\s+)?"
     r"(?:(?:great|good|fair|reasonable|real|decent|terrific|fantastic|sensible)\s+)?(?:deal|value|"
     r"price|buy|justified|deserved|warranted|reasonable|fair|sensible|worth it|no-?brainer)\b"
     # "premium" is also Costco's membership tier: only the valuation-premium verdict words.
     r"|\b" + _Z + r"(?:'s)?\s+(?:premium|multiple)\s+(?:is|was|looks?|seems?|remains?)\s+(?:still\s+|"
     r"fully\s+|well\s+)?(?:justified|deserved|warranted|earned)\b"
     # Round 3 (W3OB-7): a customer in the clause makes it the store's price ("Costco wins
     # loyalty with a fair price", "Amazon wins shoppers with fast shipping and a great price");
     # "Visa pairs a wide moat with a fair price" has none and stays a verdict.
     r"|\b" + _Z + r"\b(?:(?!\b(?:sells?|sold|selling|offers?|offered|offering|charges?|charged|"
     r"charging|priced|pricing|keeps?|kept|keeping|held|holds|delivers?|delivered|bought|buys|"
     r"buying|acquired|acquires|paid|pays|purchased|leased|built|sourced|sourcing|shoppers?|"
     r"customers?|members?|buyers?|pros|contractors?|loyalty|trust|families|guests?|subscribers?|"
     r"users?|drivers?|clients?|diners?|travell?ers?|homeowners?|consumers?|patients?|fans?)\b)"
     r"[^.!?]){0,60}?"
     r"\b(?:and|with)\s+an?\s+(?:great|good|fair|reasonable|attractive|bargain|nice|"
     r"terrific|fantastic|decent)\s+(?:price|valuation)\b(?!\s+(?:on|of|to|for)\b)"
     # The label form of a title (`_CO_LABEL_PRICE_ALT`).
     r"|" + _CO_LABEL_PRICE_ALT
     + r"|\b" + _Z + r"\b[^.!?]{0,60}?\b(?:an?|the)\s+(?:great|good|fair|real|terrific|fantastic|"
     r"decent|steal of a)\s+(?:deal|price|value|buy)\s+(?:for|to)\s+(?:investors|shareholders|"
     r"owners|long-term investors)\b"),
)
_COMPANY_ROWS_RE = tuple((code, re.compile(p)) for code, p in _COMPANY_ROWS)

# ── class B: forecasts and directives about the market, an index or a fund (idx 10) ─────────

#: "…go up and down", "…rise and fall": a description of volatility, the opposite of a promise.
_NOT_BOTH_WAYS = r"(?!\s*(?:and|or|,)\s*(?:down|fall|falls|fell|drop|drops|decline|declines|sink|sinks|shrink|shrinks|go down|goes down))"

_FORECAST_SUBJ = (r"(?:the (?:stock |broader |overall |whole )?market|(?:stock )?markets|stocks|"
                  r"equities|shares|the s&p 500|s&p 500|the (?:dow|nasdaq)|the index|index funds?|"
                  r"(?:the )?etfs?|bitcoin|crypto)")
_FUTURE = (r"(?:will|'ll|is going to|are going to|is set to|are set to|is likely to|are likely to|"
           r"is poised to|are poised to|is expected to|are expected to|is bound to|are bound to|"
           r"is sure to|are sure to|should|must|can only)")
_FC_VERB = (r"(?:(?:keep|continue to|continue|soon|eventually|probably|definitely|certainly|likely|"
            r"surely|still|only|always)\s+){0,3}(?:climb|rise|grow|fall|drop|go up|go down|go "
            r"higher|go lower|crash|win|recover|rally|soar|surge|double|triple|outperform|"
            r"underperform|beat|decline|sink|tumble|rebound|bounce back|hit|reach|climbing|rising|"
            r"growing|winning|falling|dropping|make (?:you|investors|shareholders|owners|people)|"
            r"reward (?:you|investors|shareholders|owners|people|patience|patient)|"
            r"pay off|deliver|dominate|lose|keep going up)\b")
_FORECAST_RES = (
    re.compile(r"\b" + _FORECAST_SUBJ + r"\s+" + _FUTURE + r"\s+" + _FC_VERB + _NOT_BOTH_WAYS),
    re.compile(r"\b(?:is|are|looks?|seems?)\s+(?:long\s+|over)?due for an?\s+(?:crash|correction|"
               r"rally|pullback|drop|rebound|bounce|fall|decline|downturn|bear market|recession|"
               r"breakout|rise)\b"),
    re.compile(r"\b(?:crash(?:es)?|recessions?|corrections?|bear markets?|bull markets?|"
               r"rall(?:y|ies)|downturns?|pullbacks?|meltdowns?|collapses?|bubble bursts?)\s+"
               r"(?:is|are)\s+(?:coming|near|imminent|around the corner|on (?:the|its) way|overdue|"
               r"inevitable|looming|about to (?:hit|start|begin|come))\b"),
)
#: A forecast that is reported, doubted or warned about IN ITS OWN CLAUSE is teaching ("nobody
#: knows whether the market will rise", "beware anyone who says a crash is coming"). A bare
#: "if"/"whether"/"unless" is NOT here: "If you stay patient, the S&P 500 will keep climbing" is
#: a conditional wrapped around an unconditional main-clause prediction (round-2 W2CB-2). Only
#: the complementiser after a doubt/report verb ("knows whether", "ask if") exempts.
#: "whether"/"if" as the complement of a doubt or report verb ("nobody knows whether", "ask
#: yourself if", "it is unclear whether"), never as a conditional.
_DOUBT_COMPLEMENT = (r"\b(?:know|knows|knew|sure|certain|say|says|tell|tells|predict|guess|wonder|"
                     r"wonders|wondering|ask|asks|asking|question|unclear|uncertain|decide|check|"
                     r"matter|matters)\b(?:\s+[a-z']+){0,2}\s+(?:whether|if)\b")
#: Round 3 (W3CB-1): REPORT words ("Some claim…", "Pundits predict…", "the hype says…") are no
#: longer an own-clause exemption — a reported forecast is a belief frame and needs a correction
#: (`_forecast_exempt`); a WARNED-about one ("Ignore claims that…") is a warning object.
_FORECAST_EXEMPT_RE = re.compile(
    r"\b(?:no one|nobody|noone|can't|cannot|won't|never|nor|neither|doubt|doubts|impossible|"
    r"hard to|no way to|don't know|doesn't know|beware|be wary|watch out|myth)\b|"
    + _DOUBT_COMPLEMENT
)
_INVESTABLE = (r"(?:(?:an?|the|your|every|more|some|one|any|all|each|that|this|these|those|two|"
               r"three|a few|cheap|low-cost|broad|total|good|great|single|whole|first|next|own|new|"
               r"very|simple|boring|diversified|global)\s+){0,3}"
               r"(?:s&p 500\s+|index\s+|total[- ]market\s+|broad[- ]market\s+|low-cost\s+|"
               r"dividend\s+|growth\s+|value\s+|tech\s+)?(?:etfs?|index funds?|mutual funds?|funds?|"
               r"stocks?|shares|bonds?|crypto(?:currenc(?:y|ies))?|bitcoin|gold|(?:stock )?market|"
               r"s&p 500|index|" + _Z + r")\b")
#: A POSITIVE imperative or an advisory modal ("Buy an S&P 500 ETF", "you should put your savings
#: in an index fund"). A negated one ("Don't sell in a panic", "never buy what you don't
#: understand") is the behavioural lesson Journey teaches, and past tense is history.
#: Round 2 (W2CB-2) added the clause-initial imperative after the sentence's opening phrase
#: ("When everyone panics, buy stocks", "For most people, pick an index fund"), after an
#: imperative + "and" ("Open an account and buy an index fund"), and the prompt-shaped leads
#: ("your sign to", "that is when you"). The object (`_INVESTABLE`) is what makes it a
#: directive, so "If you buy one ETF, you own a slice" (not an imperative) and "investors often
#: buy stocks at the top" (description) stay legal. No bare "add": "many people use a broad ETF
#: as the core of their plan, then add a few single stocks" is an eligible etfs_101 fact.
#: Round 3 (W3CB-7): an imperative after ANY comma ("If you are new, keep it simple, pick an
#: index fund") and after a spaced dash ("Keep it simple - buy an index fund"; an em-dash folds
#: to " - "). A comma-led verb that continues a THIRD-PERSON subject's list of verbs ("Some
#: investors panic, sell stocks, and miss the rebound") is description, not a directive — that is
#: decided per match by `_described_not_directed`, never by narrowing the lead.
_DIRECTIVE_LEAD = (r"(?:^|[;:]\s*|^[^,;:.!?]{1,60},\s*|"
                   r",\s*(?:(?:so|and|then|or|but|just|simply|now|instead)\s+)?|"
                   r"\s-{1,2}\s+(?:(?:just|simply|then|now)\s+)?|"
                   r"^(?:(?:just|now|simply|then)\s+)?(?:open|set up|stay|keep|be|get|start|save|"
                   r"wait|learn|find|skip|ignore|relax|breathe|sit tight|take|make|turn off|tune out|"
                   r"stop|forget|trust|stick to|pay|build|do|go|stay calm)\b[^.!?,;:]{0,60}?\band\s+"
                   r"(?:then\s+)?|\b(?:so|then|just|simply|now|today|instead)\s+|"
                   r"\b(?:you|investors|readers|beginners|everyone|savers|people)\s+"
                   r"(?:should|must|need to|ought to|have to|had better|might want to|may want to)\s+"
                   r"(?:(?:just|simply|always|also)\s+)?|\bshould\s+(?:you\s+)?|\b(?:time|better|"
                   r"smarter|smart|wise|wiser|best) to\s+|\b(?:your|this is your) (?:sign|signal|cue|"
                   r"reminder|chance|excuse) to\s+|\b(?:that|this|now|here)(?:'s| is)? (?:when|the "
                   r"time|the moment|your chance|your moment) (?:you |to )(?:should |can |must )?)")
_DIRECTIVE_VERBS = (r"(?:buy|sell|dump|short|load up on|pile into|accumulate|own|hold|"
                    r"invest (?:in|into)|put (?:[a-z']+\s+){0,3}?(?:in|into)|move (?:[a-z']+\s+){0,3}?"
                    r"(?:in|into|out of)|get out of|stay out of|go all in on|switch (?:[a-z']+\s+){0,2}?"
                    r"(?:to|into)|liquidate|cash out of|pick|choose|start with|grab|stick with|"
                    r"stay with|keep (?:buying|adding(?: to)?|holding|investing in)|snap up|scoop up|"
                    r"get into|bet on|go with)")
_SUITABLE = (r"(?:right|best|smartest|smart|perfect|ideal|obvious|natural|safest|safe|sure|good|"
             r"great|solid|wise|wisest|simplest|easiest|sensible|no-brainer)")
#: A price or mood CONDITION on a trade (round 3, W3VAC-02): fear / greed / panic, a dip or a
#: crash, low or high prices, a bargain.
_TIMING = (r"\b(?:fear|fears|fearful|greed|greedy|panic\w*|euphori\w*|crash\w*|dips?|dipped|"
           r"drops?|dropped|plunge\w*|slump\w*|sell-?offs?|bargains?|on sale|(?:low|high|cheap|"
           r"falling|rising|lower|higher|bargain) prices?|prices? (?:are |get |gets |look |looks |"
           r"is |fall |falls |fell |drop |drops |dip |dips |go |goes |run |runs )?(?:low|high|cheap|"
           r"lower|higher|down|up))\b")
_DIRECTIVE_RES = (
    re.compile(_DIRECTIVE_LEAD + r"(?P<verb>" + _DIRECTIVE_VERBS + r")\s+" + _INVESTABLE),
    re.compile(_DIRECTIVE_LEAD + r"(?P<verb>sell|dump|liquidate|cash out)\s+(?:everything|it all|"
               r"all of it|all your [a-z]+)\b"),
    # Suitability, the directive's other shape: "For most people, an S&P 500 index fund is the
    # right choice". Named products only; "a simple way to start" is teaching. A COMPANY is the
    # subject only for an investor audience: "Home Depot is the natural choice for contractors"
    # is the case study's customers (`_customer_choice`, round 3 W3OB-5).
    re.compile(r"\b(?:etfs?|index funds?|mutual funds?|funds?|stocks?|bonds?|s&p 500|" + _Z
               + r")\b[^.!?]{0,20}?\b(?:is|are|remains?|makes?)\s+(?:the|a|an)?\s*" + _SUITABLE
               + r" (?:choice|option|pick|bet|move|place to start|starting point|first step|way to "
               r"(?:invest|start|begin))\b"),
    # Round 3 (W3CB-7): the same verdict in the inverted order a writer uses to soften it — "The
    # right choice for most beginners is an S&P 500 index fund", "For beginners, the simplest move
    # is to buy an index fund". The investable object after the copula is what makes it advice:
    # "The right choice is the one you can stick with" and "The smartest move is often doing
    # nothing" have none. Present tense only: "Amazon's smartest move was to buy Whole Foods" is
    # an acquisition in a case study.
    re.compile(r"\b(?:the|a|an|your|my|our)\s+(?:(?:single|very)\s+)?" + _SUITABLE
               + r"\s+(?:first\s+)?(?:choice|move|pick|option|bet|step|starting point|place to start|"
               r"way to (?:start|begin|invest)|investment|holding)\b[^.!?]{0,40}?\b(?:is|are|remains?|"
               r"would be|will be)\s+(?:(?:probably|usually|often|simply|just|still|always|clearly|"
               r"likely)\s+)?(?:to\s+" + _DIRECTIVE_VERBS + r"\s+)?" + _INVESTABLE),
    re.compile(r"\b(?:can't|cannot|can not|won't|never|hard to|impossible to|difficult to)\s+go wrong\s+"
               r"(?:with|by\s+(?:buying|owning|holding|picking|choosing|starting with))\s+"
               + _INVESTABLE),
    # Round 3 (W3VAC-02): a TIMED trade with no object — the Mr. Market lines the real model
    # wrote twice: "When his fear leads to low prices, you might find a chance to buy", "His
    # fear can present opportunities to buy", "When his greed makes prices high, you can choose
    # to pass or sell". A price or mood condition, then a permission ("you can / might …") or an
    # opportunity noun, then a bare buy or sell ending its clause. The parable's narration ("he
    # might offer to buy your shares … if you choose to sell"), "every day he offers you a chance
    # to buy or sell" (no condition), "knowing when to sell" and "you can choose to pass" stay
    # legal; a negated clause ("Never treat a dip as a chance to buy") is the lesson.
    re.compile(_TIMING + r"[^.!?]{0,80}?\b(?:you\s+(?:can|could|might|may)\s+(?:(?:simply|just|"
               r"then|always|now)\s+)?(?:(?:choose|decide|opt|want|find a chance|find the chance|"
               r"get the chance|take the chance|seize the chance|use the chance|see a chance)\s+to"
               r"\s+)?|(?:an?\s+|the\s+|your\s+)?(?:(?:good|great|rare|golden|perfect|real|clear)"
               r"\s+)?(?:chances?|opportunit(?:y|ies)|moments?|signals?|times?|cues?)\s+to\s+)"
               r"(?P<verb>(?:(?:pass|hold|wait)\s+or\s+)?(?:buy|sell)(?:\s+or\s+(?:simply\s+|"
               r"just\s+)?(?:pass|hold|wait|buy|sell))?)(?=\s*(?:[.!?,;:)]|$))"),
)

# ── round 3: a verb DESCRIBED, not directed (W3OB-1, W3CB-7, the fomo_cycle real draft) ──
#
# The leads above are positional, but "just", "then", a comma and a "to" also sit in front of a
# third-person verb: "Designers just go with TSMC", "Investors might then pick a stock near the
# peak", "Some investors panic, sell stocks, and miss the rebound", "an emotional pattern that
# leads investors to buy when prices are high". Each is what people DO, the behaviour a lesson
# describes. The exemption reads the verb's own clause (and, for a serial list, the clause that
# carries its subject) and is fail-closed three ways: the subject must be a listed actor noun
# ("you" / "we" never are) opening its clause after only listed determiners — so an approving
# adjective ("Smart investors simply buy an index fund", "Wise savers just pick…") leaves the
# verb a recommendation —, an advisory modal ("should", "can", "must") between the subject and
# the verb keeps it one too, and in a serial list an approving word anywhere in the subject's
# clause ("Many investors wisely stay calm, buy stocks, and hold") does as well.
_ACTOR = (r"(?:investors|people|beginners|traders|savers|newcomers|novices|folks|buyers|sellers|"
          r"shoppers|customers|consumers|users|members|households|families|companies|firms|"
          r"businesses|rivals|competitors|designers|developers|engineers|researchers|scientists|labs|"
          r"makers|manufacturers|carmakers|automakers|chipmakers|retailers|brands|banks|merchants|"
          r"advertisers|studios|creators|viewers|subscribers|fans|gamers|drivers|airlines|startups|"
          r"managers|analysts|clients|partners|suppliers|centers|centres|platforms|others|crowds|"
          r"many|most|some|both|everyone|everybody|the crowd|the herd)")
#: One actor word, whole ("investors", "people"): a plural subject makes a verb-brand after it
#: the verb ("Why Investors Chase Rising Stocks" — `_verdict_brand`).
_ACTOR_WORD_RE = re.compile(_ACTOR)
#: What may sit between the actor and the verb: descriptive adverbs and modals of habit or
#: hypothesis. Never an advisory modal ("should", "must", "can", "need to").
_DESC_BETWEEN = (r"(?:(?:might|may|would|will|often|usually|typically|frequently|sometimes|also|"
                 r"still|all|both|then|later|eventually|soon|just|simply|now|today|instead|quickly|"
                 r"rushed to|rush to|tend to|tends to|tended to|end up|ended up|try to|tried to|"
                 r"want to|wanted to|start to|started to|begin to|began to)\s+)")
#: Approval in a serial list's subject clause, whose verb phrase is free text.
_APPROVING_RE = re.compile(
    r"\b(?:smart|smarter|smartest|wise|wiser|wisest|savvy|successful|great|good|best|patient|"
    r"disciplined|experienced|seasoned|clever|rational|sensible|prudent|winning|top|rich|wealthy|"
    r"legendary|famous|expert|professional|shrewd|intelligent|informed|careful|thoughtful|"
    r"long-term|value|serious|pro|wisely|smartly|sensibly|rightly|prudently|shrewdly|patiently|"
    r"calmly|cleverly|rationally)\b")
#: What may precede the actor in its noun phrase: determiners, quantifiers and a few noun
#: modifiers ("chip designers", "PC makers", "data centers"). Closed on purpose: "Ask others
#: then buy stocks" opens with a VERB, and an unlisted word leaves the verb a directive.
_ACTOR_DET = (r"(?:(?:many|most|some|few|other|the|these|those|all|both|new|young|first-time|"
              r"nervous|emotional|anxious|excited|scared|greedy|fearful|a lot of|lots of|plenty of|"
              r"millions of|thousands of|chip|pc|data|retail|individual|ordinary|everyday|novice|"
              r"rival|competing|big|small|large|early|later|such|their|its)\s+){0,2}")
#: The verb's whole clause is its actor noun phrase plus descriptive words ("chip designers now
#: ", "investors might then ").
_ACTOR_BEFORE_RE = re.compile(r"^\s*" + _ACTOR_DET + _ACTOR + r"\s+" + _DESC_BETWEEN + r"{0,2}$")
#: A clause that opens with its actor and a verb ("some investors panic", "many beginners see a
#: rally") — the subject of a serial list the comma-led verb continues.
_ACTOR_CLAUSE_RE = re.compile(r"^\s*" + _ACTOR_DET + _ACTOR + r"\s+" + _DESC_BETWEEN
                              + r"{0,2}(?!(?:should|must|can|could|need|ought|have to|had better)\b)"
                              r"[a-z]+")
#: The causative frame of a described mistake: "leads investors to buy when…", "makes people
#: sell…".
_CAUSED = (
    r"\b(?:(?:leads?|led|leading|push(?:es|ed|ing)?|drives?|drove|driving|causes?|caused|causing|"
    r"tempts?|tempted|tempting|prompts?|prompted|prompting|pressures?|pressured|gets?|got|"
    r"encourages?|encouraged|nudges?|nudged|convinces?|convinced|forces?|forced)\s+"
    r"(?:(?:many|most|some|new|nervous|emotional|anxious|excited|scared|greedy|fearful|the|these|"
    r"those)\s+){0,2}(?:" + _ACTOR + r"|them|us|investor|beginner|someone)(?:\s+(?:often|usually|"
    r"then|repeatedly))?\s+to"
    r"|(?:makes?|made|making|lets?|letting)\s+(?:(?:many|most|some|new|nervous|emotional|the|"
    r"these|those)\s+){0,2}(?:" + _ACTOR + r"|them|us)(?:\s+(?:often|usually|then))?)")
_CAUSED_BEFORE_RE = re.compile(_CAUSED + r"\s+$")
#: The same frame opening an earlier conjunct: "…leads investors to buy when prices are high AND
#: sell when prices are low" — the second verb is governed by the first one's frame.
_CAUSED_IN_CLAUSE_RE = re.compile(_CAUSED + r"\s+[a-z]")
#: Only descriptive adverbs / modals between a list comma and the verb (", then sell stocks").
_ONLY_DESC_RE = re.compile(r"\s*" + _DESC_BETWEEN + r"{0,2}")
_AND_RE = re.compile(r"\b(?:and|or)\b")
#: A clause that ENDS the walk back through a list: a subordinate clause ("When everyone
#: panics, buy stocks") or a second-person one. A bare verb phrase ("get excited", "keep it
#: simple") is a list item and is walked past; the list is description only if an ACTOR clause
#: opens it.
_STOP_CLAUSE_RE = re.compile(
    r"^\s*(?:when|whenever|if|as|once|while|after|before|unless|since|until|because|for|in|"
    r"during|at|with|by|to|you|your|you're|we|i|let|let's|please|don't|do)\b")
#: A negated verb is the lesson ("A new rival can't just get into the market"), except "why not".
_NEGATED_VERB_RE = re.compile(r"(?<!why )\b(?:can't|cannot|couldn't|won't|don't|doesn't|didn't|"
                              r"never|not)\s+(?:(?:just|simply|then|now|always|ever|really)\s+)?$")
_LIST_CUT_RE = re.compile(r",|\b(?:and|or|while)\b")


def _described_not_directed(view: str, verb: int) -> bool:
    """The verb at `view[verb:]` is a third-person behaviour or a negated one, not an order.
    Bounded look-behind (the scope window) and a bounded walk over at most four list clauses."""
    before = view[max(0, verb - _SCOPE_WINDOW):verb]
    segment = _after_last(_HARD_CUT_RE, before)
    if _NEGATED_VERB_RE.search(segment):
        return True
    if _CAUSED_BEFORE_RE.search(segment):
        return True
    parts = _LIST_CUT_RE.split(segment)
    own = parts[-1]
    if _ACTOR_BEFORE_RE.search(own):
        return True
    if not _ONLY_DESC_RE.fullmatch(own):
        return False
    # A comma- or "and"-led verb: it continues a list only when an "and" closes the list (at
    # the verb or after it) and an earlier clause of the list opens with its third-person subject.
    if not _AND_RE.search(view, max(0, verb - 6), min(len(view), verb + _SCOPE_WINDOW)):
        return False
    for clause in reversed(parts[-5:-1]):
        if _CAUSED_IN_CLAUSE_RE.search(clause):
            return True
        if _STOP_CLAUSE_RE.match(clause):
            return False
        if _ACTOR_CLAUSE_RE.match(clause):
            return not _APPROVING_RE.search(clause)
    return False


#: A company's customers choosing it (W3OB-5): an audience phrase right after the verdict, and no
#: investor word anywhere in the sentence ("TSMC is the safe choice for designers" is the case
#: study; "…the smart choice for investors / your portfolio / anyone investing" is not).
_CUSTOMER_AUDIENCE_RE = re.compile(
    r"\s+(?:for|among|with)\s+(?:(?:a|an|the|many|most|all|every|its|their)\s+)?(?:[a-z-]+\s+){0,2}?"
    r"(?:contractors?|pros|professionals?|designers?|researchers?|developers?|engineers?|merchants?|"
    r"shoppers?|customers?|members?|families|family|homeowners?|labs?|carmakers?|automakers?|"
    r"retailers?|banks?|weekend projects?|diy projects?|data cent(?:er|re)s?|gamers?|travell?ers?|"
    r"drivers?|diners?|advertisers?|studios?|clients?|consumers?|users?|companies|firms|"
    r"hospitals?|airlines?)\b")
_INVESTOR_AUDIENCE_RE = re.compile(
    r"\b(?:invest\w*|portfolios?|beginners?|savers?|shareholders?|stockholders?|you|your|own|owns|"
    r"owning|owned|stocks?|shares?|owners?|holders?|traders?|retirement|nest eggs?)\b")


def _customer_choice(view: str, m: "re.Match[str]", idx: int) -> bool:
    """A suitability verdict whose subject (row 2) or object (row 3) is a COMPANY and whose
    audience is that company's customers, in a sentence with no investor word."""
    if _INVESTOR_AUDIENCE_RE.search(view):
        return False
    if idx == 2:
        return m.group(0).startswith(_Z) and bool(_CUSTOMER_AUDIENCE_RE.match(view, m.end()))
    return m.group(0).endswith(_Z) and bool(_CUSTOMER_AUDIENCE_RE.search(m.group(0)))


#: `_DIRECTIVE_RES` index of the timed-trade row (W3VAC-02).
_TIMED_TRADE_ROW = 5


def _directive_hit(idx: int, rx: re.Pattern, view: str) -> "re.Match[str] | None":
    """The first match of a `_DIRECTIVE_RES` row that is advice, not a description."""
    for m in rx.finditer(view):
        if idx in (0, 1) and _described_not_directed(view, m.start("verb")):
            continue
        if idx in (2, 3) and _customer_choice(view, m, idx):
            continue
        if idx == _TIMED_TRADE_ROW and (
                _exempted_by_scope(view, m.start("verb"), _ADVICE_NEGATED_RE, governing=False)
                or _IMPULSE_FRAME_RE.search(view[max(0, m.start("verb") - 80):m.start("verb")])):
            continue
        return m
    return None


def own_word_brands(company_terms: FrozenSet[str]) -> FrozenSet[str]:
    """The item's own company terms that are lexicon WORD-brands ("visa", "apple", "meta"):
    the only terms a clause-initial capital makes the issuer on its own (W3CB-3). The other
    company terms ("ceo", "store", "prime", "depot", "taiwan") are never read as a company."""
    words = company_lexicon().words
    return frozenset(t for t in company_terms if t.capitalize() in words)


def _prepared_sentences(sk: str, own: FrozenSet[str] = frozenset()
                        ) -> List[Tuple[str, List[Tuple[str, int, int]], str, str]]:
    """(sentence, company mentions, folded, folded company view) per sentence of the skeleton —
    computed ONCE per scan and shared by the tier-1 exemptions and `_sentence_hits`."""
    out = []
    for sent in sentences(sk):
        mentions = sentence_company_mentions(sent, own)
        fs = fold(sent)
        out.append((sent, mentions, fs, fold(_company_view(sent, mentions)) if mentions else fs))
    return out


def _sentence_hits(sk: str, strict: bool, company_terms: FrozenSet[str],
                   sheet_words: FrozenSet[str] = frozenset(),
                   prepared: "List[Tuple[str, List[Tuple[str, int, int]], str, str]] | None" = None,
                   *, myth_framed: bool = False, next_first: str = ""
                   ) -> List[Tuple[str, str]]:
    """Per-sentence class-B, return-figure and forecast hits over the SKELETON, as (code, detail).

    * A sentence that names a company (the lexicon, or one of the item's `company_terms`) or,
      in a Journey post, any other proper noun, runs EVERY strict row and the tier-2 check, in
      both modes: "Journey posts name no instrument" is enforced, not assumed (idx 4/11/12).
    * The company is the instrument of the `_COMPANY_ROWS` (`_company_view`).
    * Forecasts and directives about the market, an index or a fund apply everywhere (idx 10).
    * A percentage is read after `words_to_digits`, so "seventy percent" is a return figure
      exactly where "70%" is (idx 7).
    * A forecast is exempt only by its own clause (`_forecast_exempt`); `myth_framed` (the body
      of a card titled "Myth") labels the FIRST sentence, and `next_first` is the sentence that
      follows the last one (the next script line, or the body under a title).
    Linear: every row is bounded and runs once per sentence."""
    out: List[Tuple[str, str]] = []
    seen: set = set()

    def add(code: str, key: Tuple[str, int], detail: str) -> None:
        if key not in seen:
            seen.add(key)
            out.append((code, detail))

    rows = prepared if prepared is not None else _prepared_sentences(
        sk, own_word_brands(company_terms))
    for i, (sent, mentions, fs, view) in enumerate(rows):
        names_co = bool(mentions) or _names_company(fs, company_terms)
        nxt = rows[i + 1][2] if i + 1 < len(rows) else next_first
        escalate = not strict and (names_co or _proper_noun_signal(sent, sheet_words))
        if escalate:
            for idx, (code, rx, strict_only, exempt) in enumerate(_CLASS_B_TIER1_RE):
                if strict_only:
                    m = _tier1_first(rx, exempt, fs, lambda: (view,))
                    if m:
                        add(f"class_b_{code}", ("t1", idx), m.group(0))
        if strict or escalate:
            m = _tier2_hit(fs)
            if m and (_TIER2_CONTEXT_RE.search(fs) or names_co):
                out.append(("class_b_evaluative", m.group(0)))
        if mentions:
            label = fold(sent[mentions[0][1]:mentions[0][2]])
            for idx, (code, rx) in enumerate(_COMPANY_ROWS_RE):
                m = rx.search(view)
                if m:
                    add(f"class_b_{code}", ("co", idx), m.group(0).replace(_Z, label))
        for idx, rx in enumerate(_FORECAST_RES):
            for m in rx.finditer(view):
                if not _forecast_exempt(view, m, labelled=myth_framed and i == 0,
                                        next_sentence=nxt, names_co=names_co):
                    add("class_b_forward", ("fc", idx), m.group(0))
                    break
        label = fold(sent[mentions[0][1]:mentions[0][2]]) if mentions else _Z
        for idx, rx in enumerate(_DIRECTIVE_RES):
            m = _directive_hit(idx, rx, view)
            if m:
                # The mention's own words, never the placeholder: a repair prompt that quotes
                # "now choose zzco" names a sentence the model cannot find (W3OB-1).
                add("class_b_recommendation", ("dir", idx), m.group(0).strip().replace(_Z, label))
        for m in _RECORD_RE.finditer(fs):
            if not (_RECORD_EXEMPT_RE.search(fs[max(0, m.start() - 60):m.start()])
                    or _RECORD_AFTER_RE.match(fs, m.end(), min(len(fs), m.end() + 60))):
                add("class_b_valuation", ("rec", 0), m.group(0))
                break
        conv = words_to_digits(fs)
        if ((_PERCENT_RE.search(conv) or _MULTIPLE_RE.search(conv))
                and (_INVESTOR_GROWTH_RE.search(view)
                     or (_RETURN_WORDS_RE.search(fs) and not _all_ratio_bound(conv)))) \
                or _NDIGIT_RETURN_RE.search(fs) or _word_multiple_hit(view) \
                or (_NDIGIT_GROWTH_RE.search(fs) and (_TIER2_CONTEXT_RE.search(fs) or names_co)):
            out.append(("return_figure", fs[:80]))
    return out

# ── banned phrases ────────────────────────────────────────────────────────────

#: App Store "Also avoid" list + advice directives + disclaimers (code-owned, so the model
#: writing one is an error, not a help).
BANNED_PHRASES = (
    "top picks", "top pick", "best stocks", "best stock", "beat the market", "beating the market",
    "beats the market", "guaranteed return", "guaranteed returns", "guaranteed profit",
    "guaranteed profits", "guaranteed gain", "guaranteed gains", "guaranteed income",
    "guaranteed to", "proven returns", "proven strategy",
    "trading signal", "trading signals", "buy now", "sell now", "financial advisor",
    "financial adviser",
    "investment advisor", "investment adviser", "portfolio manager", "financial advice",
    "investment advice", "not a recommendation", "risk-free", "risk free", "can't lose",
    "cannot lose", "get rich", "act now", "don't miss", "before it's too late", "load up",
    "back up the truck", "you should buy", "you should invest", "invest now", "moonshot",
    "financial freedom", "passive income", "insider tip", "financial planner",
    "certified financial planner", "wealth manager", "wealth advisor",
)

#: Wording whose only popular form is a misattribution (mirrors
#: `tests/test_learn_content_misattributions.py::BANNED_PHRASES`; a test pins the superset).
MISATTRIBUTIONS = (
    "eighth wonder", "einstein", "compound interest is the most powerful",
    "most powerful force in the universe", "impatient to the patient",
    "irrational longer than you can remain solvent", "when the facts change",
    "blood in the streets", "investment in knowledge pays", "roughly right than precisely wrong",
    "sells to optimists", "four most dangerous words", "rolls royce", "sitting in the shade",
    "make money while you sleep", "spend what is left after saving",
    "protection against ignorance", "best time to plant a tree",
)

IDENTITY_TERMS = (
    "gemini", "google ai", "google's ai", "google deepmind", "deepmind", "bard", "vertex ai",
    "openai", "open ai", "chatgpt", "chat gpt", "gpt", "gpt-4", "gpt-5", "claude", "anthropic",
    "llm", "llms", "large language model", "language model", "as an ai", "i am an ai",
    "i'm an ai", "ai model", "generated by ai", "ai-generated", "ai generated",
)

#: Brand, CTA and disclaimer are code-owned; a model that writes them is overstepping.
BRAND_TERMS = (
    "caydex", "cay ai", "cay", "our app", "this app", "the caydex app", "in-app", "download",
    "link in bio", "link in the bio", "follow us", "follow for more", "on google play",
)
#: An app store as the place to GET an app (round 2, real draft): "Find it on the App Store",
#: "Available on the App Store". Apple's own App Store is the subject of a case study ("Apple
#: focused on the App Store, iCloud, and various subscriptions", "revenue streams from the App
#: Store") and is not a brand mention. "download" is a brand term on its own.
_APP_STORE = r"(?:app store|apple app store|play store|google play store)"
_APP_STORE_CTA_RE = re.compile(
    # Getting THIS app: the object is it / us / the app — "Developers get 70% of sales on the App
    # Store" and "users install apps from the App Store" are Apple's case study.
    r"\b(?:get|grab|find|search for|look for|look up|install|try)\s+(?:it|us|the app|our app|this "
    r"app|caydex)\b[^.!?]{0,20}?\b(?:on|in|from) the " + _APP_STORE + r"\b"
    r"|\b(?:it's|it is|we're|we are|now|our app is|the app is|this app is)\s+(?:(?:now|also)\s+)?"
    r"(?:(?:available|live|out)\s+)?(?:on|in) the " + _APP_STORE + r"\b"
    r"|(?:^|[.!?]\s+)(?:now\s+)?(?:available|live|out now)\s+(?:on|in) the " + _APP_STORE + r"\b"
    r"|\b(?:app store|play store) (?:link|listing|page)\b"
    r"|\brated\s+(?:[0-9]|five|four)[^.!?]{0,15}?\b(?:on|in) the " + _APP_STORE + r"\b")
#: "signals" as the THING a product sells ("trading signals", "buy signals", "our signals",
#: "signals to buy") — the App Store "Also avoid" word. The verb ("Growth in revenue and
#: earnings signals expansion", "This transition signals a maturation of the market" — real
#: drafts) and a lesson's plain noun ("Some signals don't shout", journey:red_flags) are not it.
_SIGNALS_RE = re.compile(
    r"\b(?:buy|sell|trading|trade|stock|entry|exit|technical|bullish|"
    r"bearish|premium|exclusive|app|in-app|daily|weekly|our|free|pro|crypto|options|forex|alpha|"
    r"proprietary|ai|real-time|instant|winning|profitable|hot)[- ]signals?\b|\bsignals? (?:to|for) "
    r"(?:buy|sell|trade|invest|enter|exit|buying|selling|trading)\b|\bsignal service\b")

_BANNED_RE = _phrase_re(BANNED_PHRASES)
_MISATTR_RE = _phrase_re(MISATTRIBUTIONS)
_IDENTITY_RE = _phrase_re(IDENTITY_TERMS)
_BRAND_RE = _phrase_re(BRAND_TERMS)
#: "I" is case-sensitive (a lower-case "i" is "i.e." or a list marker); the rest are not, so a
#: sentence-initial testimonial ("My portfolio tripled.", "Mine grew faster.") is caught too.
_FIRST_PERSON_RE = re.compile(
    r"(?<![A-Za-z'])(?:I(?:'(?:m|M|ve|VE|d|D|ll|LL))?|(?i:me|my|mine|myself))(?![A-Za-z'])"
)
#: The brand's own "we/our" ("Our readers say…", "We have taught this to thousands") is the same
#: self-reference as "I". "us" is deliberately absent: "The future loves to surprise us." is
#: corpus teaching, and a case-insensitive "us" is also the country.
_FIRST_PLURAL_RE = re.compile(
    r"(?<![A-Za-z'])(?i:we|we've|we're|we'd|we'll|our|ours|ourselves)(?![A-Za-z'])"
)
_AUX = r"(?i:am|do|did|does|should|shall|can|could|will|would|have|has|must|is|was|might|may)"
#: The reader's self-question the corpus teaches with ("Ask yourself: am I giving it time?",
#: "ask: do I understand this risk?"): the FIRST first-person word is the subject right after a
#: clause-opening auxiliary. A narrated experience in question form ("Want to know how I stopped
#: panic selling?", "Why did I buy at the top?") is not — it is a testimonial.
_SELF_QUESTION_RES = (
    re.compile(r"(?:^|[:;,]\s*)" + _AUX + r"\s+$"),
    re.compile(r"(?i:\bask(?:\s+yourself)?)\s*[:,]?\s*(?:(?i:what|why|how|where|when|which|who|"
               r"whether)\s+(?:[A-Za-z']+\s+){0,2})?" + _AUX + r"\s+$"),
)
#: A quoted QUESTION — the reader's own thought-question the corpus teaches with ('ask yourself,
#: "Would I be happy to own this whole company for ten years?"'). It is the ONLY quoted text the
#: first-person scan skips: a quoted declarative ('One reader put it best: "This lesson changed
#: how I invest."') is a testimonial with quote marks around it, and the promise scan skips no
#: quote at all ('Remember the rule: "the market always recovers."' is the promise, published).
#: The writer is told to quote nothing (round-2 W2CB-1 / W2CB-9).
_QUOTED_QUESTION_RE = re.compile(
    r'"[^"\n]{0,300}?\?\s*"|(?:(?<=[\s:(])|^)\'[^\'\n]{1,300}?\?\'(?=[\s.,;:!?)]|$)'
    # A quoted THOUGHT or self-question, introduced by a thinking/asking verb ('She thought, "I
    # will wait."', 'The reader asks: 'am I being patient'', 'tell yourself: "I own a
    # business"'): a teaching device, not reported speech. "said", "told us",
    # "wrote" and "put it" are not thinking verbs — a reader's quoted words stay a testimonial.
    r'|(?i:\b(?:thinks?|thought|wonders?|wondered|asks?|asked|(?:tells?|told|remind|reminds|'
    r'reminded) (?:yourself|himself|herself|themselves)))\s*[:,]?\s*'
    r'(?:"[^"\n]{0,300}"|\'[^\'\n]{1,300}\'(?=[\s.,;:!?)]|$))'
)
_HASH_ONE_RE = re.compile(r"#\s?1(?![0-9])|\bnumber one\b|\bno\. ?1\b")
#: A LIST label is not a ranking claim: "Myth #1:", "Mistake number one", "Step no. 1"
#: (round 2, W2-OB-3 — the myth_vs_fact and checklist templates number their items).
_LIST_LABEL_BEFORE_RE = re.compile(
    r"\b(?:myth|myths|mistake|mistakes|step|steps|tip|tips|rule|rules|lesson|lessons|sign|signs|"
    r"reason|reasons|takeaway|takeaways|fact|facts|question|questions|day|part|item|point|flag|"
    r"habit|trap|lens|round|check|chapter|misconception)\s*$")


def _ranking_claim(folded: str) -> bool:
    for m in _HASH_ONE_RE.finditer(folded):
        if not _LIST_LABEL_BEFORE_RE.search(folded[max(0, m.start() - 20):m.start()]):
            return True
    return False


#: Round 2 (W2-OB-8): the reader's self-question the prompt permits, whatever first-person word
#: it holds — "Is this a business I'd be glad to hold for years?" (journey:stock_vs_business's
#: own question), "Ask yourself: what would make me sell?", "Ask: is this my plan talking, or my
#: fear?", "What would I do if the price fell by half?". Its QUESTION clause (after an "ask
#: (yourself)" or a colon) opens with a present/modal auxiliary, or a wh-word and one within two
#: words — never "did"/"was"/"had", which narrate.
_ASK_LEAD_RE = re.compile(r"(?i:\bask(?:s|ed)?(?:\s+(?:yourself|themselves|himself|herself|"
                          r"yourselves))?)\s*[:,]?\s*|:\s*")
_PRESENT_AUX = (r"(?:is|am|are|would|will|can|could|should|do|does|have|has|must|might|may|"
                r"shall|'d|makes?)")
_SELF_Q_OPEN_RE = re.compile(
    r"(?i:^\s*[\"']?(?:" + _PRESENT_AUX + r"\b|(?:what|why|how|where|when|which|who|whom|whether)"
    r"\s+(?:[a-z']+\s+){0,2}?" + _PRESENT_AUX + r"\b))")
#: A narrated EXPERIENCE: the speaker as the subject of a past event ("how I stopped panic
#: selling", "what I did next", "I made my money back", "my portfolio tripled"), or a past
#: auxiliary before I/me/my ("Why did I buy at the top?", "Was I wrong?"). A closed verb list:
#: an inverted "Am I scared…" is an adjective, not a story.
_NARRATED_RE = re.compile(
    r"(?i:\b(?:did|was|were|had)\s+(?:I|me|my)\b)"
    r"|\bI(?:'ve|'d| have| had)?\s+(?:(?i:just|finally|once|really|actually|then|always|never)\s+)?"
    r"(?i:bought|sold|made|lost|won|got|went|took|kept|held|spent|paid|quit|left|became|grew|ran|"
    r"turned|stopped|started|doubled|tripled|invested|panicked|learned|learnt|realized|realised|"
    r"decided|switched|moved|dumped|missed|earned|gained|built|saved|retired|figured|discovered|"
    r"found|chased|followed|tried|used|picked|chose|did|was|knew|saw|felt|began|thought|watched|"
    r"traded|cashed|sat|waited|held on)\b"
    r"|(?i:\bmy\s+(?:[a-z-]+\s+){0,2}?(?:portfolio|money|savings|investments?|stake|shares|account|"
    r"returns?|stocks?|gains?|nest egg|biggest|worst|best)\b[^?]{0,30}?\b(?:grew|doubled|tripled|"
    r"rose|soared|made|returned|beat|jumped|climbed|went up|was|were)\b)")


def _is_self_question(sent: str, fp: "re.Match[str]") -> bool:
    if not sent.rstrip().endswith("?"):
        return False
    if fp.group(0) == "I":
        prefix = sent[max(0, fp.start() - 80):fp.start()]
        if any(rx.search(prefix) for rx in _SELF_QUESTION_RES):
            return not _NARRATED_RE.search(sent)
    if _NARRATED_RE.search(sent):
        return False
    clause = sent
    for m in _ASK_LEAD_RE.finditer(sent[:fp.start()]):
        clause = sent[m.end():]
    return bool(_SELF_Q_OPEN_RE.match(clause))


# ── code-owned text: the disclaimer's subject matter, CTAs, endorsements ──────
#
# The disclaimer says "not investment advice, not a recommendation … Investing involves risk,
# including loss of principal … AI-assisted" and code appends it (`post_copy.py`). Model text has
# no legitimate reason to touch that subject, and every way of touching it either duplicates the
# notice or contradicts it right above it.

#: Advice/recommendation framing, the notice itself, and claims about how the text was made.
#: "recommendation feed/engine/algorithm" is product vocabulary (the TikTok case study).
_CODE_OWNED_RES = (
    re.compile(r"\badvi(?:ce|ser|sers|sor|sors|sory)\b"),
    # Round 2 (W2-OB-4): "recommendations" only as ADVICE — "TikTok's recommendations came from
    # what you watched" and "Netflix's recommendations" are the case study's product.
    re.compile(r"\b(?:our|my|your|personal|personali[sz]ed|investment|investing|financial|stock|buy|"
               r"sell|hold|expert|professional|analyst|analysts'|analysts|official|specific|tailored|"
               r"top|trade|trading)\s+recommendations?\b|\bnot (?:a |an )?(?:investment |financial )?"
               r"recommendations?\b|\brecommendations? (?:to|on what to|on which) (?:buy|sell|invest|"
               r"hold|own|stocks?|funds?)\b|\b(?:take|treat|read|see|consider|this is|that is|it is|"
               r"it's|this isn't|it isn't) (?:this |it |these )?(?:as )?(?:a |an )?(?:investment |"
               r"financial )?recommendations?\b"),
    re.compile(r"\bdisclaimers?\b|\b(?:fine|small) print\b"),
    # Round 2 (W2-OB-4): a claim about how THIS text was made, never a contrast in the lesson
    # ("built for games, not AI", "No AI can supply your patience", "Your judgment, not AI, makes
    # the final call", "almost no AI research ran on these cards").
    re.compile(r"\b(?:no|zero|without|free of)\s+(?:any\s+)?ai\s+(?:(?:was|were|is|has been|had "
               r"been)\s+)?(?:used|involved|help|helped|assist|assisted|assistance|wrote|written|"
               r"behind (?:it|this|these|the (?:post|lesson|video|words))|here|at all|touched|in "
               r"(?:this|these|the making))\b"
               r"|\b(?:written|crafted|authored|composed|penned|generated|produced|put together|"
               r"typed)\s+(?:entirely\s+|only\s+|purely\s+|completely\s+)?(?:without|with no|with "
               r"zero|free of)\s+(?:any\s+)?ai\b"
               r"|\b(?:this|these|our|every|each)\s+(?:lesson|lessons|post|posts|video|videos|caption|"
               r"captions|script|scripts|content|thread|word|words|breakdown|carousel|article|slide|"
               r"slides|explainer|summary)\b[^.!?]{0,40}?\b(?:no|without|zero|free of|not)\s+(?:any\s+|"
               r"by\s+)?ai\b"
               r"|\bnot\s+ai[- ](?:generated|written|assisted|made|created|produced|powered)\b|\bai-free"
               r"\b|\b(?:100%|fully|entirely|purely|completely)\s+human(?:[- ](?:written|made|"
               r"authored|crafted))?\b"),
    re.compile(r"\bhuman-(?:written|made|authored)\b|\bwritten (?:entirely |only )?by "
               r"(?:a |real )?(?:human|humans|people|person|hand)\b"),
    re.compile(r"\bnot (?:educational|for education(?:al purposes)?)\b"),
    # Denying machine authorship in other words (round-2 W2CB-1), right above "AI-assisted":
    # "Crafted by people, not machines", "No robots wrote this lesson", "Every word here was
    # written by hand". Scoped to THIS text or to a writing verb: "decisions made by people, not
    # machines" (journey:ai_and_beyond) is the lesson, not a claim about the post.
    re.compile(r"\b(?:crafted|written|authored|composed|penned|hand-?written)\s+(?:entirely\s+|"
               r"only\s+|purely\s+)?by\s+(?:real\s+)?(?:people|humans?|a (?:real )?person|hand)"
               r"\s*(?:,|-)?\s*(?:and\s+)?not\s+(?:by\s+)?(?:an?\s+)?(?:ai|machines?|robots?|bots?|"
               r"algorithms?|computers?|chatbots?)\b"),
    # A participial LABEL on this text, sentence-initial: "Made by humans, not AI.", "Written by
    # people." — never "decisions made by people, not machines" (the lesson has a subject).
    re.compile(r"(?:^|[.!?:]\s+)(?:(?:proudly|lovingly|carefully|all)\s+)?(?:made|written|created|"
               r"crafted|built|produced|authored)\s+(?:entirely\s+|only\s+|purely\s+)?by\s+(?:real\s+)?"
               r"(?:humans?|people|a person|hand|writers?|editors?)\b"),
    re.compile(r"\bno (?:robots?|bots?|machines?|algorithms?|computers?|chatbots?|ai) (?:wrote|"
               r"write|made|created|generated|produced|touched|helped (?:write|with))\b"),
    # "each post came from friends, not an algorithm" (Instagram) and "each piece is made by
    # hand" (LVMH) are case studies: THIS content + an authorship verb only.
    re.compile(r"\b(?:this|these|every|each) (?:lesson|lessons|post|video|caption|script|thread|"
               r"breakdown|carousel|article|slide|slides)\b[^.!?]{0,50}?\b(?:written|crafted|made|"
               r"created|authored|produced|generated|composed)\s+(?:entirely\s+|only\s+|purely\s+)?"
               r"(?:by (?:real )?(?:people|humans?|hand|a (?:real )?person|writers?|editors?)|(?:without|"
               r"with no|by no)\s+(?:an?\s+)?(?:ai|machines?|robots?|bots?|algorithms?|chatbots?))\b"),
    # Waving the notice away: "Ignore the small text below", "skip the legal bit".
    re.compile(r"\b(?:ignore|skip|disregard|never mind|forget about|forget)\s+(?:the\s+|that\s+|"
               r"this\s+|all\s+the\s+)?(?:small|fine|legal|tiny|boring|grey|gray)\s+(?:text|print|"
               r"note|notes|line|lines|stuff|bit|bits|part|words|type)\b|\b(?:ignore|skip|"
               r"disregard)\s+(?:the\s+)?(?:legalese|legal notice|notice below|warning below|"
               r"text below|words below)\b"),
)

#: A certainty word + an investing outcome: the promissory claim the disclaimer's "Investing
#: involves risk, including loss of principal" sits directly under (FINRA 2210-style). Each row
#: is a SHAPE, not a word — "always", "safe", "protect", "guarantee" alone are ordinary corpus
#: vocabulary ("The two are always linked", "a moat protects a company's profits").
_UP = (r"(?:go(?:es)? up|went up|goes? higher|rise|rises|rose|risen|recover|recovers|recovered|"
       r"bounce[sd]? back|comes? back|came back|pays? off|paid off|wins?|beats?|outperform(?:s|ed)?|"
       r"grow|grows|grew|makes? money|made money|climb(?:s|ed)?|trend(?:s|ed)? (?:up|higher)|"
       r"ends? (?:up|higher)|ended (?:up|higher)|gain(?:s|ed)?|rewards?|rewarded|works? out)")
_OUTCOME = (r"(?:growth|gains?|returns?|profits?|income|wealth|success|money|results?|payoffs?|"
            r"riches|wins?|retirement|savings|dividends?|appreciation|upside|recovery|future|"
            r"growing|grow|grows|rise|riches)")
#: The outcome a track record promises after "never failed to" — an optional adverb, then an
#: investing outcome. "grow" only bare or with an investor's money ("never failed to grow its
#: sales" is a business fact); "deliver" only with a return noun.
_NEVER_FAILED_OUTCOME = (
    r"(?:(?:eventually|ultimately|always|quickly|reliably|steadily|consistently)\s+)?"
    r"(?:recover|bounce back|come back|rebound|rise|go up|climb|reward|pay off|beat|outperform|"
    r"win|make money|gain|profit|reach (?:a |new |fresh |record )+highs?|hit (?:a |new |fresh |"
    r"record )+highs?|deliver (?:[a-z-]+\s+)?(?:returns|gains|profits|growth|results)|"
    r"grow (?:your|their|investors'|people's|a saver's|an investor's)\s+(?:money|wealth|savings|"
    r"portfolios?|nest eggs?)|grow(?=\s*(?:$|[.,;:!?)]|\s(?:over|in the long|again|eventually|in "
    r"time|with time|through)\b)))\b")
_PROMISE_RES = (
    re.compile(r"\b(?:always|consistently|inevitably|reliably|invariably) " + _UP + r"\b"
               + _NOT_BOTH_WAYS),
    re.compile(r"\b(?:has|have|had) always (?:gone up|risen|recovered|bounced back|come back|"
               r"paid off|won|beaten|outperformed|grown|made money|climbed|rewarded|gained)\b"
               + _NOT_BOTH_WAYS),
    # No "lose out": "Don't lose out on decades of compounding" is encouragement, not a promise.
    re.compile(r"\b(?:never|can't|cannot|won't|will not|don't|do not|doesn't|does not) "
               r"(?:lose|loses|lost) (?:money|a (?:dime|penny|cent)|anything|value)\b"),
    # Nobody ever lost money / never failed / works every time (round-2 W2CB-1): the promise
    # stated as a track record. "No one wants to lose money" has no "ever"/past tense and stays.
    re.compile(r"\b(?:no one|nobody|noone|no investor|no shareholder|no saver|none of (?:them|us|"
               r"those investors))\b[^.!?]{0,60}?\b(?:ever\s+(?:lost|loses?)|(?:has|have|had)\s+"
               r"(?:ever\s+)?lost|lost)\s+(?:money|a (?:dime|penny|cent)|a single (?:dollar|penny|"
               r"cent))\b"),
    # Round 3 (W3CB-8): "never failed TO <an outcome>" is the same track record ("The market has
    # never failed to bounce back", "Compounding never fails to grow your money"); "Never fail
    # to diversify", "never fails to surprise" and "never failed to raise its dividend" are not.
    re.compile(r"\bnever\s+(?:once\s+)?(?:failed|fails|fail|let (?:\w+ )?down|disappointed)\b"
               r"(?!\s+to\s+(?!" + _NEVER_FAILED_OUTCOME + r"))"),
    re.compile(r"\b(?:works?|worked|pays? off|paid off|wins?|won)\s+(?:every single time|every time|"
               r"each time|without fail|100% of the time|all the time)\b"),
    re.compile(r"\b(?:never|can't|cannot|won't|will not) lose\b(?! sleep| sight| track| hope| "
               r"touch| heart| interest| your (?:cool|nerve|temper|head|way))"),
    # "Never fall in love with a stock", "never fall for a pitch" are warnings, not promises.
    re.compile(r"\b(?:never|can't|cannot|won't|will not|doesn't|does not) (?:go(?:es)? down|"
               r"falls?(?! in love| for\b| into| behind| prey| victim| asleep| apart| short| out of)|"
               r"decline|declines|drop|drops|crash|crashes|go to zero|lose value)\b"),
    re.compile(r"\bguarantee(?:s|d)? (?:(?:you|your|a|an|the|that|steady|solid|big|high|higher|"
               r"strong|great|long-term|future|safe|positive|real|some|more) ){0,3}" + _OUTCOME
               + r"\b"),
    re.compile(r"(?<!make )(?<!be )(?<!making )\b(?:certain|sure|bound|destined) to (?:grow|rise|go up|recover|pay off|win|"
               r"beat|double|work out|make money|succeed|gain)\b"),
    re.compile(r"^guaranteed\s*[:!]"),
    re.compile(r"\b(?:growth|gains?|returns?|profits?|income|success|results?|wealth|payoffs?|"
               r"recovery) (?:is|are|was|were|will be) (?:(?:almost|practically|virtually|"
               r"basically|all but|nearly|essentially|pretty much) )?(?:guaranteed|certain|"
               r"assured|a sure thing|a given)\b"),
    re.compile(r"\b(?:no|zero|without(?: any)?|little to no|virtually no|almost no|free of) "
               r"(?:real |actual |meaningful |serious )?(?:risk|risks|downside)\b(?!-| management|"
               r" tolerance| assessment| capacity| appetite| controls?| limits?| budget| plan)"),
    re.compile(r"\b(?:riskless|no-lose)\b|\b(?:sure|surefire|sure-fire|foolproof|fool-proof|"
               r"bulletproof|can't-miss|cannot-miss) (?:bet|thing|investment|win|way|path|plan)\b"),
    re.compile(r"\b(?:remove|removes|removed|eliminate|eliminates|eliminated|erase|erases|erased|"
               r"take away|takes away|wipe out|wipes out)(?: all| the| any| your| every)? "
               r"(?:risk|risks|the downside|downside)\b"),
    # "retire rich", "will make you wealthy" (content-B review, idx 10) — "get rich" is banned
    # outright; "won't make you rich overnight" is the warning and stays (negation).
    re.compile(r"\b(?:retire|make you|makes you|made you|will (?:be|become|get|grow|end up)|"
               r"become|becomes|grow|grows|end up)\s+(?:very\s+|really\s+|so\s+|super\s+)?"
               r"(?:rich|wealthy|a millionaire|millionaires)\b"),
)
#: Safety claims about an investment ("a safe way to grow your money", "your money is
#: protected"). Kept apart because deposit safety is a FACT, not a promise ("A savings account is
#: a safe place for an emergency fund"): these rows do not fire in a sentence about insured cash.
_SAFETY_RES = (
    # Round 2 (real draft, journey:risk_reward): the outcome must be the safe thing's own —
    # "Safe options grow slowly, while stocks can grow faster but also drop" describes the
    # trade-off (a contrast clause, and growth that is SLOW), it promises nothing.
    re.compile(r"\b(?:safe|safer|safest|risk-?less)(?:,? [a-z-]+){0,2} (?:way|bet|choice|option|"
               r"investment|place|path|route|haven|harbor|harbour|strategy|plan|home)s?\b"
               r"(?:(?!\b(?:while|but|whereas|although|though|yet|unlike)\b)[^.!?;]){0,40}"
               r"\b(?:invest\w*|grow\w*|money|savings|wealth|returns?|retire\w*|stocks?|funds?|"
               r"portfolio|profit\w*|nest egg)\b(?!\s+(?:slowly|slow|little|less|modestly|barely|"
               r"at a crawl|only a little|more slowly))"),
    re.compile(r"\b(?:money|savings|principal|capital|investments?|nest egg|portfolio|wealth|"
               r"deposits?) (?:is|are|stays?|remains?|will (?:be|stay|remain)) (?:always |"
               r"completely |fully |totally |perfectly |100% )?(?:protected|safe|secure|"
               r"guaranteed|risk-?free)\b"),
)
#: A prediction of an investing outcome. A worked example ("If you invest…, your money will
#: double", "at 7% a year…") is arithmetic, not a promise, so a conditional frame exempts it.
_FORECAST_RE = re.compile(
    r"\b(?:money|savings|wealth|investments?|portfolio|nest egg|returns?|stocks?|shares|markets?|"
    r"index funds?|etfs?) will (?:always |surely |definitely |certainly )?(?:grow|rise|double|"
    r"triple|increase|multiply|go up|keep growing|keep rising|recover|beat|outperform|make you "
    r"rich)\b" + _NOT_BOTH_WAYS
    # Round 2 (W2CB-8): an investment DOUBLING your money, stated as a capability with no rate
    # ("Stocks can double your money in about twenty-four years", "Your investments can double
    # in about twenty-four years" — a sheet's inflation arithmetic moved onto investments). A
    # worked example with its rate ("at 7% a year your money can double in ten years") is
    # arithmetic and the conditional frame exempts it; "prices can double" has no investment.
    + r"|\b(?:stocks?|shares|equities|investing|investments?|index funds?|etfs?|funds?|the (?:stock )?"
    r"market|your (?:money|savings|portfolio|nest egg))\s+(?:can|could|would|should|may|might)\s+"
    r"(?:easily\s+|reliably\s+|quickly\s+)?(?:double|triple|quadruple|multiply)\b(?:\s+(?:your|"
    r"their|a saver's|an investor's)\s+(?:money|savings|investment|wealth|nest egg))?"
)
_CONDITIONAL_RE = re.compile(r"\b(?:if|assum\w*|suppose|imagine|say you|for example|at an? "
                             r"\d|at \d)\b")
#: Negative framing BEFORE the claim in the same sentence ("can't have high reward with zero
#: risk", "If someone promises big rewards with no risk at all", "No investment is without
#: risk"). A bare "promise" is NOT here: "Index funds promise steady growth with no risk" is the
#: claim itself.
_NEGATED_BEFORE_RE = re.compile(
    r"\b(?:no|not|never|nothing|none|nobody|neither|nor|isn't|aren't|wasn't|weren't|doesn't|"
    r"don't|didn't|can't|cannot|won't|wouldn't|couldn't|shouldn't|if (?:someone|anyone|a stranger|"
    r"a seller|a salesperson|an ad|a pitch|a scheme|a friend|a stranger)|beware|be wary|"
    r"watch out)\b"
)
#: The promise-side exemption: a negation, or a doubt complement, in the claim's own clause.
_PROMISE_EXEMPT_RE = re.compile(_NEGATED_BEFORE_RE.pattern + "|" + _DOUBT_COMPLEMENT)
_WARNING_NEGATED_RE = re.compile(r"\b(?:not|no|isn't|aren't|wasn't|never|hardly)\s+(?:just\s+|a\s+"
                                 r"|an\s+|really\s+)?$")

# ── clause scope (round 2: W2CB-1, W2V-01, W2CB-2) ──
#
# The negation / doubt / conditional exemptions used to read a flat 120-character window before
# the claim, so a negation in a NEIGHBOURING clause exempted it: "Don't panic, the market always
# recovers", "You can't time it, but the S&P 500 will keep climbing", "Don't wait: compounding
# guarantees your money grows". Sentences split only on . ! ?, so the window has to be cut at the
# claim's own clause. Two cuts, both linear over a bounded window:
#
# * a HARD cut (; : a spaced dash, "but", "yet", "so", "instead", "and then", "because") ends
#   every exemption — nothing before it can govern the claim;
# * a SOFT cut (a comma, "and", "then", "while") ends a plain negation's reach, but a negated
#   belief/speech verb with its complement ("Never assume that, over time, …", "Don't assume, as
#   many do, that …", "Beware anyone who says, …", "Nobody knows whether, after a crash, …")
#   still governs across it.
_HARD_CUT_RE = re.compile(r"[;:()\[\]]|\s-{1,2}\s|--|\b(?:but|yet|so|instead|because|although|"
                          r"though|whereas|and then|except)\b")
_SOFT_CUT_RE = re.compile(r",|\b(?:and|then|while)\b")
#: A NEGATED belief or speech verb, or a warning about whoever SAYS it, whose complement is the
#: claim. These may cross a comma (never a hard cut). Round 3 (W3CB-1) moved the W2-OB-3 frames
#: ("Ignore the hype", "Many believe") OUT of this pattern: a frame with no complement across a
#: comma is a neighbouring clause ("Ignore the hype, compounding guarantees your money grows"),
#: and they now live in `_warning_object_governs` / `_belief_governs`, which are positional.
_GOVERNING_RE = re.compile(
    r"\b(?:don't|do not|never|shouldn't|should not|can't|cannot|won't|no one should|nobody should)"
    r"\s+(?:just\s+|simply\s+|blindly\s+|ever\s+)?(?:assume|believe|think|expect|count on|bank on|"
    r"rely on|trust|accept|imagine|suppose|buy the (?:idea|claim|line|story|pitch|myth))\b"
    r"[^.!?]{0,80}?\bthat\b"
    r"|\b(?:no one|nobody|noone|can't|cannot|impossible to|hard to|no way to)\s+(?:can\s+|could\s+|"
    r"really\s+|truly\s+)?(?:promise|guarantee|know|knows|predict|say|tell|be sure)\b[^.!?]{0,40}?"
    r"\b(?:that|whether|if|when)\b"
    r"|\b(?:beware|be wary|watch out|be careful|be skeptical|be suspicious|walk away|distrust)\b"
    r"[^.!?]{0,40}?\b(?:who|that|which|when (?:someone|anyone|they))\s+(?:ever\s+)?(?:says?|said|"
    r"claims?|claimed|promises?|promised|tells? you|insists?|swears?|pitch(?:es)?|brags?)\b"
    r"|\bif (?:someone|anyone|a stranger|a seller|a salesperson|an ad|a pitch|a friend|a broker|"
    r"an advert\w*|a scheme|they)\s+(?:ever\s+)?(?:says?|claims?|promises?|tells? you|insists?|"
    r"swears?|guarantees?)\b"
    # A negative-quantifier SUBJECT across exactly one parenthetical ("No investment, however
    # diversified, is without risk"). Anchored at the segment start, one parenthetical, no
    # further comma or "and", and a subject noun — so "No joke, index funds always go up" and
    # "No investment…, is without risk, and stocks always go up" are not governed.
    r"|^\s*(?:no|not every|not all|not one|none of (?:the|these|those|your))\s+(?:[a-z-]+\s+){0,2}?"
    r"(?:investments?|stocks?|funds?|assets?|markets?|strateg(?:y|ies)|portfolios?|index(?:es)?|"
    r"etfs?|compan(?:y|ies)|business(?:es)?|bonds?|plans?|investors?|approach(?:es)?|methods?|"
    r"securit(?:y|ies)|shares?|holdings?|one)\s*,[^,]{1,40},(?:(?!\band\b)[^,]){0,60}$"
)
_SCOPE_WINDOW = 120


def _after_last(rx: re.Pattern, text: str) -> str:
    """`text` after the last match of `rx` (all of it when there is none). Linear."""
    end = 0
    for m in rx.finditer(text):
        end = m.end()
    return text[end:]


def _claim_scope(s: str, start: int) -> Tuple[str, str]:
    """(clause, segment) before the claim at `s[start:]`: the text after the last soft / hard cut
    in the bounded look-behind window."""
    segment = _after_last(_HARD_CUT_RE, s[max(0, start - _SCOPE_WINDOW):start])
    return _after_last(_SOFT_CUT_RE, segment), segment


#: A negation that AFFIRMS: "It is not a myth that…", "It's no secret that…", "No doubt…",
#: "There's no question…". Blanked before the clause's negation test, or the "not"/"no" in it
#: exempts the very claim it asserts.
_AFFIRMING_NEGATION_RE = re.compile(
    r"\b(?:not|no|isn't|is not|aren't|never)\s+(?:a\s+|an\s+|just\s+|really\s+)?(?:myth|lie|"
    r"scam|fraud|joke|secret|coincidence|accident|exaggeration|surprise|wonder|doubt|question|"
    r"mystery|fluke|fairy tale|gimmick|hype)\b|\bno (?:denying|arguing|two ways about it)\b|"
    r"\bnot (?:surprising|by chance|an accident)\b|\bcan't (?:deny|argue with)\b"
    # A rhetorical question that asserts: "Isn't it true that stocks always go up?"
    r"|\b(?:isn't|wasn't|aren't|doesn't|don't|didn't) (?:it|that|this|you|we) (?:true|obvious|clear|"
    r"amazing|funny|great|seem|feel|know|see)\b"
    # Round 3: a negated DOUBT asserts ("No one should doubt the market always recovers", "Nobody
    # can doubt the market will keep climbing", "Never forget that…", "Don't hesitate to buy
    # when…") — blanked, so its "no one" / "never" / "don't" exempts nothing.
    r"|\b(?:no one|nobody|noone|none of us|no investor)\s+(?:(?:should|can|could|would|will|"
    r"really|seriously|ever)\s+){0,2}(?:doubt|doubts|doubted|question|questions|questioned|deny|"
    r"denies|denied|dispute|disputes|disputed|argue with|argues with)\b"
    r"|\b(?:never|don't|do not|can't|cannot|shouldn't|should not|won't)\s+(?:(?:ever|really|"
    r"seriously)\s+)?(?:doubt|question|deny|dispute|underestimate|bet against|forget|ignore|"
    r"overlook|hesitate|be afraid|fear|miss out)\b"
)


def _exempted_by_scope(s: str, start: int, exempt_rx: re.Pattern, *,
                       governing: bool = True) -> bool:
    """The claim at `s[start:]` is negated, doubted or reported by ITS OWN clause, or (unless
    `governing=False`) by a negated belief/speech verb that governs it across commas (never
    across a hard cut). The positional frames are NOT here (`_promise_exempt` /
    `_forecast_exempt` decide them), so a tier-1 advice row or a forecast that calls this gets
    no belief or warning-object exemption (round 3, W3CB-1)."""
    clause, segment = _claim_scope(s, start)
    clause = _AFFIRMING_NEGATION_RE.sub(" ", clause)
    if exempt_rx.search(clause):
        return True
    return governing and any(_governs_from(segment, m.end())
                             for m in _GOVERNING_RE.finditer(segment))


def _governs_from(segment: str, at: int) -> bool:
    """The governing verb's complement, from `at` to the claim, is ONE clause: no cut at all, or
    only a parenthetical opening right after the complementiser ("Never assume that, over time,
    …", "Nobody knows whether, after a crash, …"). "Never assume that stocks are safe, the market
    always recovers" and "If someone says stocks can fall, the market always recovers" are two
    clauses — the second is the author's own (round 3: every exemption positional)."""
    between = segment[at:]
    if not _SOFT_CUT_RE.search(between):
        return True
    return (between.lstrip().startswith(",") and between.count(",") <= 2
            and not _CLAUSE_JOIN_RE.search(between))


def _segment_bounds(s: str, start: int, end: int) -> Tuple[int, int]:
    """The hard-cut segment of `s` that holds `s[start:end]`: from the last hard cut before the
    claim to the first one after it (bounded both ways by the scope window)."""
    lo = max(0, start - _SCOPE_WINDOW)
    seg_lo = lo
    for m in _HARD_CUT_RE.finditer(s, lo, start):
        seg_lo = m.end()
    m = _HARD_CUT_RE.search(s, end, min(len(s), end + _SCOPE_WINDOW))
    return seg_lo, (m.start() if m else min(len(s), end + _SCOPE_WINDOW))


_INSURED_CASH_RE = re.compile(r"\b(?:savings accounts?|emergency|fdic|insured|bank accounts?|"
                              r"deposit insurance|treasury bills?|t-bills?)\b")


# ── frames: a claim WARNED about, DEBUNKED or REPORTED (rounds 2-3) ──
#
# Round 2 (W2-OB-3) taught the promise rows five frames, and round 3 (W3CB-1/2/5) found each one
# wider than its reason: a frame NOUN anywhere in the segment ("Unlike crypto scams, the S&P 500
# always recovers"), a warning OBJECT across a comma ("Ignore the hype, compounding guarantees
# your money grows"), a BELIEF the sentence then agreed with ("Most investors think stocks always
# recover, and they are usually right") and a yes/no QUESTION answered yes ("Can index funds make
# you rich? Slowly, yes."). Every frame below is POSITIONAL — the claim's own clause, its
# complement or its predicate — and every veto is structural rather than a list of affirmations:
#
# * a WARNING frame (`_warning_framed`) holds the claim as its SUBJECT ("Promises of big rewards
#   with no risk are a warning", "Stocks always going up is a myth") or takes it as its
#   COMPLEMENT ("It's a myth that…", "It's a red flag when someone says…", "Scams promise…"),
#   or a pronoun right after the claim's clause debunks it ("…, but that's a common myth");
# * a WARNING OBJECT ("beware of promises", "ignore claims") governs only through its complement
#   ("…of", "…that", "…promising") and only inside the claim's own clause;
# * a BELIEF frame ("Many beginners believe…", "A common belief is that…") governs only the
#   clause it opens, and only while the sentence goes on to nothing but a turn or a correction —
#   ", and they're right", ", and history backs them up" or any other continuation vetoes it;
# * a YES/NO question exempts only when nothing answers it or the answer debunks it (`scan_text`
#   reads the answer in the next sentence, script line or card body).
# A FORECAST gets the belief and warning-object frames only with a correction (`_forecast_exempt`).

#: PREDICATES carry their own copula and hold the claim as their subject ("…is a myth", "…doesn't
#: exist", "…are a warning"); NOUNS (group `noun`) need a copula between the claim and them
#: ("…is running a scam") or a complement when they come first ("It's a myth that…"). Negated
#: ("It's not a myth: index funds always go up", "no scam") a frame frames nothing.
_WARNING_FRAME_RE = re.compile(
    r"\b(?:doesn't exist|does not exist|don't exist|do not exist|is a myth|isn't true|is not true|"
    r"is false|is a lie|aren't a gift|is not a gift|isn't a gift|"
    # Round 2 (real draft, journey:risk_reward): "Promises of big rewards with no risk are a
    # warning", "…is a common misconception".
    r"(?:is|are|was|were|should be) (?:a |an )?(?:clear |big |loud )?warning(?: signs?)?|"
    r"(?:is|are|was|were) (?:a |an )?(?:common |popular )?misconceptions?|"
    r"(?:is|are|was|were) (?:simply |just |dead )?(?:mistaken|misleading|untrue)|"
    # Round 3: "Believing stocks always go up is a common mistake", "…is a trap".
    r"(?:is|are|was|were) (?:a |an )?(?:common |classic |costly |big |dangerous |beginner's )?"
    r"(?:mistake|error|trap|fallacy)|"
    r"(?P<noun>(?:an?|the)\s+(?:(?:common|popular|big|classic|old|persistent|dangerous|widespread|"
    r"stubborn|costly|total|complete|great)\s+)?(?:myth|misconception|lie|fairy tale)|red flags?|"
    r"warning signs?|too good to be true|scams?|frauds?|ponzi schemes?))\b"
)
#: Between a claim and a frame NOUN in predicate position ("Anyone who promises that is running a
#: scam", "Guaranteed returns are a red flag", "…should be seen as a warning sign").
_COPULA_RE = re.compile(r"(?:\b(?:is|are|was|were|be|been|being|sounds?|sounded|looks?|looked|"
                        r"seems?|seemed|smells?|smelled|signals?|signall?ed|means?|meant|runs?|"
                        r"running|ran|remains?|remained)|'s|'re)\b")
#: What a frame NOUN before the claim must take the claim with: "It's a myth THAT…", "a red flag
#: WHEN SOMEONE SAYS…", "Scams PROMISE…", "red flags LIKE…".
_FRAME_COMPLEMENT_RE = re.compile(
    r"\s+(?:that|when\s+(?:someone|somebody|anyone|people|they|a\s+[a-z-]+|an\s+[a-z-]+)\s+"
    r"(?:says?|claims?|promises?|tells? you|insists?|swears?|pitch(?:es)?|guarantees?|offers?)|"
    r"(?:that|which|who)\s+(?:says?|claim|claims|promises?|promised|guarantees?|tells? you|"
    r"pitch(?:es)?|swears?|insists?|offers?)|promis(?:e|es|ed|ing)|claim(?:s|ed|ing)?|"
    r"say(?:s|ing)?|tell(?:s|ing)? you|pitch(?:es|ing)?|swear(?:s|ing)?|insist(?:s|ing)?|"
    r"guarantee(?:s|d|ing)?|offer(?:s|ed|ing)?|like)\b")
#: Coordinated promise adjectives ("promises of high, guaranteed returns"): that comma is inside
#: one noun phrase and cuts nothing. A closed list on purpose — a missing adjective leaves the
#: comma a cut (fail-closed).
_PROMISE_ADJ = (r"(?:high|higher|big|bigger|huge|large|quick|fast|easy|steady|safe|sure|massive|"
                r"consistent|reliable|solid|strong|great|juicy|outsized|enormous|instant|overnight|"
                r"guaranteed|risk-free|certain|stable|double-digit|triple-digit|impressive|"
                r"incredible|unbelievable|endless|effortless|passive|regular|predictable|smooth)")
_ADJ_COMMA_RE = re.compile(r"\b" + _PROMISE_ADJ + r"\s*,(?=\s*" + _PROMISE_ADJ + r"\b)")
#: A debunk NARROWED to something else debunks nothing: "…, which is not true OF SINGLE STOCKS",
#: "That's wrong FOR most funds".
_NOT_NARROWED = (r"(?!\s+(?:for|of|about|in|with|when|if|unless|except|outside|beyond|regarding|"
                 r"on)\b)")
#: A pronoun right after the claim's clause that debunks it: "…, but that's a common myth", "…,
#: which is not true", "…, and this isn't quite true". Not "that's not a myth" / "that's no
#: myth" — a negated debunk affirms.
_DEBUNK_CLAUSE_RE = re.compile(
    r"\s*(?:(?:and|but|yet|though|however)\s*,?\s*)?(?:that|this|it|which)(?:'s|\s+is|\s+was)\s+"
    r"(?:(?:just|simply|only|pure|a|an|common|old|dangerous|total|complete|popular|classic|"
    r"widespread|big)\s+){0,3}(?:myth|misconception|lie|fairy tale|fantasy|red flag|warning sign|"
    r"scam|fraud|false|untrue|not true|wrong|mistaken|too good to be true)\b" + _NOT_NARROWED
    + r"|\s*(?:(?:and|but|yet|though|however)\s*,?\s*)?(?:that|this|it|which)\s+(?:isn't|is not|"
    r"wasn't|was not|'s not)\s+(?:(?:quite|really|entirely|always|exactly|necessarily)\s+)?"
    r"(?:true|so|right|the case|correct|accurate)\b" + _NOT_NARROWED)
#: The first cut after a claim — where its own clause ends.
_TAIL_CUT_RE = re.compile(r"[,;:()]|\s-{1,2}\s|--|\b(?:and|but|yet|so|though|although|however|"
                          r"while|whereas|because|instead|except|which)\b")
_TAIL_SOFT = frozenset({",", "and", "while", "which"})


def _cut_between(s: str, a: int, b: int) -> bool:
    """A soft or hard cut in `s[a:b]`, a comma between coordinated promise adjectives aside."""
    part = s[a:b]
    if "," in part:
        # Length-preserving, and read a little past `b`: the second adjective may BE the claim
        # ("…of high, guaranteed returns").
        part = _ADJ_COMMA_RE.sub(lambda m: m.group(0).replace(",", " "),
                                 s[a:min(len(s), b + 40)])[:b - a]
    return bool(_SOFT_CUT_RE.search(part) or _HARD_CUT_RE.search(part))


def _after_claim_debunked(s: str, end: int) -> bool:
    """A pronoun debunk opens the clause right after the claim's own clause."""
    cut = _TAIL_CUT_RE.search(s, end, min(len(s), end + _SCOPE_WINDOW))
    if cut is None:
        return False
    at = cut.start() if cut.group(0).isalpha() else cut.end()
    return bool(_DEBUNK_CLAUSE_RE.match(s, at))


def _warning_framed(s: str, start: int, end: int, *, debunk_after: bool = True) -> bool:
    """A warning frame whose SUBJECT or COMPLEMENT is the claim at `s[start:end]` (W3CB-5): after
    the claim with no cut in between (a frame noun also needs a copula there), before it with a
    complement that takes it, or — `debunk_after` — a pronoun debunk right after its clause.
    "Unlike crypto scams, the S&P 500 always recovers", "Crypto is full of scams, while index
    funds always recover" and "Hot tips are a red flag, and index funds are a safe way to grow
    your money" frame nothing: the frame is about something else."""
    lo, hi = _segment_bounds(s, start, end)
    for m in _WARNING_FRAME_RE.finditer(s, lo, hi):
        if _WARNING_NEGATED_RE.search(s[max(0, m.start() - 14):m.start()]):
            continue
        if m.start() >= end:
            if not _cut_between(s, end, m.start()) and (
                    m.group("noun") is None or _COPULA_RE.search(s, end, m.start())):
                return True
        elif m.end() <= start:
            # "It is a myth THAT…" (a predicate), "a red flag WHEN SOMEONE SAYS…" (a noun).
            comp = _FRAME_COMPLEMENT_RE.match(s, m.end(), start)
            if comp and not _cut_between(s, comp.end(), start):
                return True
    return debunk_after and _after_claim_debunked(s, end)


#: A warning about an OBJECT that takes the claim as its complement: "Beware of promises of high,
#: guaranteed returns", "Be wary of promises for big rewards with no risk", "Ignore claims that
#: stocks always go up", "Avoid anything promising guaranteed returns". The complement is
#: required: "Ignore the hype, compounding guarantees your money grows" has none.
_WARNING_OBJECT_RE = re.compile(
    r"\b(?:beware|be (?:[a-z]+ly\s+)?(?:wary|careful|skeptical|sceptical|suspicious|cautious|alert)|"
    r"stay (?:[a-z]+ly\s+)?alert|watch out|look out|walk away|steer clear|distrust|avoid|ignore|"
    r"question|resist|reject)"
    r"\s+(?:(?:of|about|with|for|to|from)\s+)?(?:(?:any|all|the|such|those|these|every)\s+)?"
    r"(?:[a-z-]+\s+)?(?:promises?|claims?|pitch(?:es)?|offers?|ads?|adverts?|advertisements?|talk|"
    r"hype|guarantees?|sales ?pitch(?:es)?|tips?|anything|anyone|products?|schemes?|headlines?|"
    r"posts?|videos?|sellers?|salespeople|marketers?|influencers?)"
    r"\s+(?:of|about|for|that|like|claiming|saying|promising|offering|touting|which|who|with)\b")


def _warning_object_governs(s: str, start: int, end: int) -> bool:
    lo, _hi = _segment_bounds(s, start, end)
    for m in _WARNING_OBJECT_RE.finditer(s, lo, start):
        if not _cut_between(s, m.end(), start):
            return True
    return False


#: A belief REPORTED as one: "Many beginners believe…", "Some think…", "It is tempting to
#: think…", "You may hear…", "A common belief is that…".
_BELIEF_FRAME_RE = re.compile(
    r"\b(?:many|some|most|plenty of|a lot of|lots of|few|other|countless)\s+"
    r"(?:(?:new|young|first-time|nervous|beginner|novice|retail|ordinary|everyday)\s+)?"
    r"(?:people|beginners|investors|savers|newcomers|folks|traders|of us|others)?\s*"
    r"(?:still\s+|wrongly\s+|mistakenly\s+|often\s+|naively\s+)?"
    r"(?:think|believe|assume|expect|imagine|feel|are told|hear|say|claim|insist|are convinced)\b"
    r"|\b(?:it's|it is|it can be)\s+(?:tempting|easy|natural|common|wrong|naive|dangerous|a\s+"
    r"(?:(?:common|classic|costly|big)\s+)?(?:mistake|error))\s+to\s+(?:think|believe|assume|"
    r"expect|imagine|feel)\b"
    r"|\byou (?:might|may|could|will|'ll|often) (?:think|believe|assume|hear|have heard|be told)\b"
    r"|\b(?:a|the|one)\s+(?:common|popular|widespread|frequent|typical|classic|old|persistent)\s+"
    r"(?:belief|assumption|idea|view|notion|thought)\s+(?:is|was)\b"
)
#: For a FORECAST only, a report by a third party is the same frame ("Pundits predict…", "The
#: hype says…", "Some promise…") and needs the same correction. Not on the promise side: "Experts
#: say index funds always go up" is an appeal to authority, not a misconception stated as one.
_FORECAST_REPORT_RE = re.compile(
    _BELIEF_FRAME_RE.pattern
    + r"|\b(?:pundits|experts|analysts|forecasters|strategists|commentators|headlines|the hype|"
    r"hype|ads|adverts|sellers|salespeople|promoters|influencers|people|others)\s+(?:often\s+|"
    r"still\s+|confidently\s+|loudly\s+)?(?:say|says|claim|claims|predict|predicts|promise|"
    r"promises|insist|insists|forecast|forecasts|pitch|pitches|swear|swears)\b"
    r"|\b(?:many|some|most|few)\s+(?:(?:[a-z-]+)\s+)?(?:predict|forecast|promise|pretend|guess)\b")
_THAT_RE = re.compile(r"\s+that\b")
_CLAUSE_JOIN_RE = re.compile(r"\b(?:and|then|while)\b")
#: After a soft cut, what keeps a reported belief a report: a turn or a correction.
_TURN_START_RE = re.compile(
    r"\s*(?:(?:so|because)\b(?!\s+(?:they|it|that|this|these|those|stocks|shares|markets?|the "
    r"market|index funds|funds|etfs)(?:'re|'s|\s+(?:are|is|were|was|will be|remain|stay)\b))|"
    r"(?:but|yet|though|although|however|whereas|instead|except|not|no|never|in fact|in reality|"
    r"actually|when in fact)\b)")
#: A bare "and" / "while" that opens a NEW clause (its own subject) rather than continuing one.
_NEW_SUBJECT_RE = re.compile(r"\s+(?:they|it|that|this|he|she|history|the data|data|evidence|"
                             r"experience|time|the record|the numbers|the past|research|studies|"
                             r"experts|many|most|you|we)\b")
#: After a comma, an adverbial phrase continues the same clause ("…, especially after a long
#: rally", "…, even in a crash"); it is skipped, not read as the author's own continuation.
#: Not "as" (", as history shows" agrees) and not "with"/"for" (", with good reason").
_ADVERBIAL_START_RE = re.compile(r"\s*(?:especially|particularly|even|after|during|whenever|"
                                 r"in the short run|over (?:a|the|any)|until|unless|like|before|at "
                                 r"(?:least|first|the))\b")


def _belief_tail_ok(s: str, end: int) -> bool:
    """The OPEN affirmation veto (W3CB-1): a reported belief stays a report only if its sentence
    goes on to nothing, a hard cut, a turn or a debunk. ", and they're right", ", and they are
    usually right", ", and history backs them up", ", as history shows" — and any other
    continuation — make the sentence the author's own claim. No list of affirmations to miss."""
    hi = min(len(s), end + _SCOPE_WINDOW)
    for cut in _TAIL_CUT_RE.finditer(s, end, hi):
        w = cut.group(0)
        if w in ("and", "while") and not _NEW_SUBJECT_RE.match(s, cut.end()):
            continue                   # "…always go up if you wait and hold": one clause
        if w == "," and _ADVERBIAL_START_RE.match(s, cut.end()):
            continue                   # "…, especially after a long rally"
        if w in _TAIL_SOFT:
            at = cut.end() if w == "," else cut.start()
            return bool(_TURN_START_RE.match(s, at) or _DEBUNK_CLAUSE_RE.match(s, at))
        return True                    # a hard cut or a turn word ends the belief's clause
    return True


def _belief_governs(s: str, start: int, end: int, frames: re.Pattern = _BELIEF_FRAME_RE) -> bool:
    """A belief frame opens the claim's own clause (or reaches it through "…believe that, …")
    and the sentence does not go on to agree with it."""
    lo, _hi = _segment_bounds(s, start, end)
    for m in frames.finditer(s, lo, start):
        if _cut_between(s, m.end(), start):
            that = _THAT_RE.match(s, m.end(), start)
            if that is None or _CLAUSE_JOIN_RE.search(s, that.end(), start) \
                    or _HARD_CUT_RE.search(s, that.end(), start):
                continue
        return _belief_tail_ok(s, end)
    return False


# ── a misconception stated AS one (round 2, W2-OB-3) ──
#
# The myth_vs_fact template asks the writer to "open with a common misconception the source
# corrects" and to make the cards "the myth, then the facts". For a risk lesson the classic
# misconception is promise-shaped ("stocks always go up"), and every natural way to state it was
# a `promissory` rejection. Besides the frames above:
#
# * a myth LABEL whose clause holds the claim — "Myth: stocks always go up", "Here is a common
#   myth: …", "A popular myth is that …", "A common misconception about ETFs is that …" — up to
#   the next hard cut or a "fact / truth / in reality" turn ("Myth: stocks are risky; fact: index
#   funds always go up" frames nothing);
# * a YES/NO question that opens its segment with a positive auxiliary — "Can you get big rewards
#   with zero risk?", "ETFs: Are They Really Without Risk?" — never a pitch or a presupposition
#   ("Want returns that always go up?", "Do you want…", "Have you noticed how…", "Is it a
#   coincidence that…", "Would you rather…"), and only when it is not answered with anything
#   but a debunk;
# * the NEXT sentence debunking it — "Big rewards with no risk? It's a myth.", "… Not always.";
# * a card or slide TITLED as the myth (`scan_text(..., myth_framed=True)`), for its body's
#   first sentence only.
_MYTH_LABEL_RE = re.compile(
    r"\b(?:(?:the|a|an|one|another|our)\s+)?(?:(?:common|popular|big|biggest|classic|old|"
    r"persistent|dangerous|widespread|stubborn|costly|first|second|third|top|favou?rite)\s+)?"
    r"(?:myths?|misconceptions?)(?:\s*(?:#|no\.?|number)?\s*(?:[0-9]+|one|two|three|four|five))?"
    # Round 3 (fresh real run, journey:etfs_101): "A common misconception ABOUT ETFS is that…",
    # "…AMONG NEW INVESTORS is that…" — a short prepositional phrase before the copula.
    r"(?:\s+(?:about|among|regarding|surrounding|around|with|for)\s+(?:[a-z0-9&'-]+\s+){0,3}?"
    r"[a-z0-9&'-]+)?"
    r"\s*(?::|-(?=\s)|\s+(?:is|says|goes)\b(?:\s+that\b)?)")
#: "Truth vs myth:", "Fact or myth:" — a label of BOTH, which frames nothing.
_MYTH_VERSUS_RE = re.compile(r"\b(?:vs\.?|versus|and|or|&|/)\s*$|/\s*$")
#: A turn from the myth to the correction: nothing after it is labelled.
_MYTH_TURN_RE = re.compile(r"\b(?:fact|facts|truth|reality|actually|in fact|in reality|however|"
                           r"but|yet)\b")
#: A short parenthetical gloss between a label and its claim ("… Exchange Traded Funds (ETFs) are
#: entirely risk-free") is an aside, not a cut — unless it holds a cut or a turn itself.
_GLOSS_RE = re.compile(r"\([^()]{1,30}\)")


def _unglossed(between: str) -> str:
    if "(" not in between:
        return between
    return _GLOSS_RE.sub(lambda g: g.group(0) if (_HARD_CUT_RE.search(g.group(0)[1:-1])
                                                   or _MYTH_TURN_RE.search(g.group(0)[1:-1]))
                         else " ", between)


#: A yes/no question opening the claim's segment. Not a pitch ("Do you want…", "Ready to…") and
#: not a PRESUPPOSITION (round 3, W3CB-2): "Have you noticed how the market always bounces
#: back?", "Would you rather own a fund that never loses value?", "Is it a coincidence that…",
#: "Did you ever wonder why…" all assert the claim.
_YES_NO_QUESTION_RE = re.compile(
    r"\s*(?:can|could|do|does|did|is|are|was|were|will|would|should|has|have|must|might|may)\b"
    r"(?!\s+(?:you|we|they|i)\s+(?:ever\s+|still\s+|really\s+|already\s+)?(?:want|like|wish|love|"
    r"know|need|ready|tired|looking|still want|dream|believe|imagine|guess|see|feel|agree|notice|"
    r"noticed|realize|realized|realise|realised|seen|watched|heard|rather|prefer|wonder|wondered|"
    r"remember|think)\b)"
    r"(?!\s+(?:it|this|that)\s+(?:just\s+|really\s+|simply\s+|any\s+|all\s+|pure\s+)?(?:an?\s+)?"
    r"(?:wonder|surprise|surprising|coincidence|accident|luck|fluke|mystery|secret|shock|"
    r"shocking|obvious)\b)")
_ANSWER_LEAD = (r"(?:sadly|unfortunately|actually|honestly|historically|in fact|in short|short "
                r"answer|the (?:short |honest |real )?answer(?: is)?|history says|the record "
                r"says)\s*[,:-]?\s*")
#: The next sentence DEBUNKS the claim: "It's a myth.", "This isn't quite true.", "No.", "Not
#: always.", "Not over every stretch.", "The short answer is no." — never "Not only…" or "No
#: doubt…", and a bare "Not" only before a hedge ("Not in a straight line" debunks nothing).
_DEBUNK_NEXT_RE = re.compile(
    r"\s*(?:(?:that|this|it)(?:'s|\s+is|\s+was)\s+(?:(?:just|simply|only|pure|a|an|a common|an "
    r"old|a dangerous)\s+){0,2}(?:myth|misconception|lie|fairy tale|fantasy|red flag|warning sign|"
    r"scam|fraud|false|untrue|not true|wrong|too good to be true)\b" + _NOT_NARROWED
    + r"|(?:not true|false|wrong|a myth|myth|busted)\s*[.!]?\s*$)"
    r"|\s*(?:that|this|it)\s+(?:isn't|is not|wasn't|was not|'s not)\s+(?:(?:quite|really|entirely|"
    r"always|exactly|necessarily)\s+)?(?:true|so|right|the case|correct|accurate)\b" + _NOT_NARROWED
    # A bare "No." / "Nope." / "Never.", or "No, <a negation>" ("No, they don't") — never "No,
    # really." — and a hedge ("Not always.", "Not over every stretch.") that does not turn back
    # to the claim ("Not every day, but always over time" reasserts it).
    + r"|\s*(?:" + _ANSWER_LEAD + r")?(?:no|nope|nah|never)\s*(?:[.!]|$|[,;:-]\s*(?:[a-z']+\s+){0,3}?"
    r"(?:not|never|nothing|rarely|hardly|[a-z]+n't)\b)"
    r"|\s*(?:" + _ANSWER_LEAD + r")?(?:not\s+(?:always|quite|really|necessarily|exactly|true|so|at "
    r"all|guaranteed|for everyone|by a long shot|(?:over |in |during |through )?every)|rarely|"
    r"hardly ever|far from it|nothing is (?:guaranteed|certain))\b"
    r"(?![^.!?]*\b(?:but|yet|though|however|still|eventually|in the end|in the long run|over "
    r"time|always)\b)")


def _myth_labelled(s: str, start: int, *, labelled_from: int = -1) -> bool:
    """The claim at `s[start:]` sits in the clause a myth label opens: no hard cut and no turn
    between the label (or position `labelled_from`, a card title's label) and the claim."""
    # A NEGATED label frames nothing: "It's not a myth: index funds always go up".
    ends = [m.end() for m in _MYTH_LABEL_RE.finditer(s, max(0, start - _SCOPE_WINDOW), start)
            if not _WARNING_NEGATED_RE.search(s[max(0, m.start() - 14):m.start()])
            and not _MYTH_VERSUS_RE.search(s[max(0, m.start() - 14):m.start()])]
    if labelled_from >= 0:
        ends.append(labelled_from)
    for end in ends:
        between = _unglossed(s[end:start])
        if not (_HARD_CUT_RE.search(between) or _MYTH_TURN_RE.search(between)):
            return True
    return False


def _is_yes_no_question(s: str, start: int, end: int) -> bool:
    """The claim's own hard-cut segment is a yes/no question: "Do stocks always go up?", "ETFs:
    Myth vs. Fact - Are They Really Without Risk?". Positional: a question after a label or a
    dash counts, a claim BEFORE the question ("Stocks always go up - do you agree?") does not."""
    if not s.rstrip().endswith("?") or _HARD_CUT_RE.search(s, end):
        return False                   # "Can index funds make you rich, so why wait?" asks nothing
    lo = max(0, start - _SCOPE_WINDOW)
    seg = lo
    for m in _HARD_CUT_RE.finditer(s, lo, start):
        seg = m.end()
    if seg == lo and lo > 0:
        return False                   # the question's opening is out of reach
    return bool(_YES_NO_QUESTION_RE.match(s, seg))


#: A frame the sentence then AFFIRMS frames nothing: "It is a myth that stocks always go up, and
#: that's true", "…; they are usually right", "…, as history shows". Round 3 (W3CB-1) added the
#: qualifier slot ("usually right") and the evidence verbs; a reported BELIEF does not rely on
#: this list at all (`_belief_tail_ok` vetoes any continuation).
_AFFIRM_QUAL = (r"(?:(?:absolutely|completely|totally|quite|dead|so|100%|usually|mostly|often|"
                r"generally|largely|historically|probably|basically|essentially|pretty much|almost "
                r"always|always|clearly|certainly|definitely|really|actually)\s+){0,2}")
_AFFIRM_EVIDENCE = (r"(?:history|the data|data|the evidence|evidence|experience|the record|the "
                    r"numbers|the past|time|research|the facts|facts|decades of data)")
_AFFIRMED_AFTER_RE = re.compile(
    r"(?:\b(?:and|but|so|because|since)\b|[,;:-])\s*(?:(?:[a-z']+\s+){0,3}?(?:they|it|that|this|he|"
    r"she|which)(?:'re|'s|\s+are|\s+is|\s+were|\s+was|\s+have been|\s+has been)\s+" + _AFFIRM_QUAL
    + r"(?:right|true|correct|spot on|onto something|not wrong)\b|" + _AFFIRM_EVIDENCE
    + r"\s+(?:(?:usually|mostly|often|generally|largely|clearly|certainly|strongly)\s+)?(?:backs?|"
    r"backed|proves?|proved|confirms?|confirmed|supports?|supported|bears?|bore)\s+(?:them|it|"
    r"this|that|the (?:idea|belief|view|claim))\b|" + _AFFIRM_EVIDENCE + r"\s+(?:agrees?|agreed|"
    r"is on (?:their|its) side|sides with (?:them|it)|says so)\b|rightly(?: so)?\b|(?:for|with) good "
    r"reason\b"
    r"|(?:they|it|this|that|he|she)\s+" + _AFFIRM_QUAL + r"(?:have a point|has a point|got it "
    r"right|get it right|are onto something|is onto something|aren't wrong|are not wrong|isn't "
    r"wrong|is not wrong|do|does|did|will)\s*(?:[).!?,;]|$))"
    r"|\bas\s+" + _AFFIRM_EVIDENCE + r"\s+(?:shows?|showed|proves?|proved|confirms?|confirmed|"
    r"suggests?|tells us)\b")
#: The NEXT sentence affirms: "Yes.", "They're right.", "They are usually right.", "History backs
#: them up.", "True." (kept apart from the debunk: a question answered this way is the claim).
_AFFIRM_NEXT_RE = re.compile(
    r"\s*(?:yes|yep|yeah|absolutely|of course|definitely|always|you bet|sure|exactly|correct|"
    r"it does|they do|it can|they can|it will|they will|(?:and\s+)?(?:they're|they are|it's|it is|"
    r"that's|that is|they were|it was)\s+" + _AFFIRM_QUAL + r"(?:right|true|correct|spot on|"
    r"onto something)|(?:and\s+)?" + _AFFIRM_EVIDENCE + r"\s+(?:(?:usually|mostly|often|generally|"
    r"largely|clearly|certainly|strongly)\s+)?(?:backs?|backed|proves?|proved|confirms?|confirmed|"
    r"supports?|supported|bears?|bore|agrees?|agreed|says (?:so|yes))\b|(?:and\s+)?(?:they|it)\s+"
    + _AFFIRM_QUAL + r"(?:have a point|has a point|got it right|are onto something|aren't wrong|"
    r"are not wrong|isn't wrong|is not wrong))\b"
    r"|\s*(?:and\s+)?(?:they|it)\s+(?:do|does|did)\s*[.!]?\s*$|\s*true\s*[.!]?\s*$")


def _promise_exempt(s: str, start: int, end: int, *, labelled: bool = False,
                    affirmed: bool = False, unanswered: bool = True) -> bool:
    """A promise-shaped match at `s[start:end]` (one folded sentence) that its own clause negates
    or doubts, a warning frame holds as its subject or complement, a warning object or a belief
    frame governs positionally, a myth label frames, or a yes/no question only asks — unless the
    sentence then affirms it. `affirmed` (the NEXT sentence affirms it: "Yes.", "They're
    right.") leaves only the clause's own negation. `unanswered=False` (something follows the
    question — the next sentence, script line or card body) takes the question exemption away:
    a question answered with anything but a debunk is the claim (W3CB-2); the debunk itself is
    `scan_text`'s `_DEBUNK_NEXT_RE`."""
    if _AFFIRMED_AFTER_RE.search(s, end, min(len(s), end + _SCOPE_WINDOW)):
        return False
    if affirmed:
        return _exempted_by_scope(s, start, _PROMISE_EXEMPT_RE, governing=False)
    return (_exempted_by_scope(s, start, _PROMISE_EXEMPT_RE) or _warning_framed(s, start, end)
            or _warning_object_governs(s, start, end) or _belief_governs(s, start, end)
            or _myth_labelled(s, start, labelled_from=0 if labelled else -1)
            or (unanswered and _is_yes_no_question(s, start, end)))


def _promissory_hit(folded_sentence: str, *, labelled: bool = False, affirmed: bool = False,
                    unanswered: bool = True, framable: bool = True) -> str:
    """The first promise in ONE folded sentence that `_promise_exempt` does not exempt. Every
    match of every row is tried, so an exempt first occurrence cannot shadow a later, unexempt
    one. `labelled`: the sentence is the body a "Myth" title labels; `affirmed`: the next
    sentence agrees with it; `unanswered`: nothing follows it. `framable=False` (the sentence
    names a company): no frame exempts it — "Myth: Apple stock always goes up." still talks
    about a named company's share price, which a public post never does (EU MAR)."""
    s = folded_sentence

    def exempt(m: "re.Match[str]") -> bool:
        return framable and _promise_exempt(s, m.start(), m.end(), labelled=labelled,
                                            affirmed=affirmed, unanswered=unanswered)

    rows = _PROMISE_RES if _INSURED_CASH_RE.search(s) else _PROMISE_RES + _SAFETY_RES
    for rx in rows:
        for m in rx.finditer(s):
            if not exempt(m):
                return m.group(0)
    if not _CONDITIONAL_RE.search(s):
        for m in _FORECAST_RE.finditer(s):
            if not exempt(m):
                return m.group(0)
    return ""


#: A forecast only a UNIVERSAL claim can be the myth of: "Myth: the market will always recover"
#: denies a certainty, while "Myth: the S&P 500 will rise next year" is a forecast of the
#: opposite (W3OB-4 trap).
_UNIVERSAL_RE = re.compile(r"\b(?:always|never|forever|every (?:time|single time|year|decade)|"
                           r"no matter what|without fail|inevitably)\b")
#: A correction that is pure DOUBT, never a counter-forecast: "…, but nobody knows", "…; no one
#: can predict it", or a next sentence "Nobody knows." / "It's impossible to say."
_DOUBT_TURN_RE = re.compile(
    r"(?:[;:]|\s-{1,2}\s|--|\b(?:but|yet|though|although|however|whereas|in fact|in reality|"
    r"actually)\b)[^.!?]{0,60}?\b(?:nobody|no one|noone|can't|cannot|impossible|unclear|"
    r"uncertain|unknowable|unpredictable|guess|guessing|doubt|don't know|doesn't know|no way to "
    r"know|no guarantee)\b")
_DOUBT_NEXT_RE = re.compile(
    r"\s*(?:(?:but|in reality|in fact|actually|truthfully|honestly)\s*,?\s*)?(?:nobody|no one|"
    r"noone)\s+(?:really\s+)?(?:knows|can (?:know|say|predict|tell))\b|\s*(?:it's|it is|that's|"
    r"that is)\s+(?:impossible|unclear|uncertain|unknowable|a guess)\b")
#: A correction that NEGATES — allowed only for a universal claim (its negation is no forecast).
_NEGATING_TURN_RE = re.compile(
    r"(?:[;:]|\s-{1,2}\s|--|\b(?:but|yet|though|although|however|whereas|in fact|in reality|"
    r"actually)\b)[^.!?]{0,60}?\b(?:not|never|isn't|aren't|won't|doesn't|don't|myth|"
    r"misconception|wrong|false|untrue|mistaken)\b")


def _forecast_exempt(view: str, m: "re.Match[str]", *, labelled: bool = False,
                     next_sentence: str = "", names_co: bool = False) -> bool:
    """A class-B forecast at `m` in one sentence's company view that is not the text's own
    prediction: its own clause doubts or negates it, a warning frame holds it, or a warning
    object takes it as its complement ("Ignore claims that…" — the warning is the correction).
    A REPORTED belief ("Many think…", "Pundits predict…") exempts a forecast only with a
    correction — pure doubt ("…, but nobody knows"), or, for a UNIVERSAL claim, a negation or a
    debunk. A myth label or a next-sentence debunk exempts a universal claim only, and nothing
    exempts one about a named company (round 3: W3CB-1, W3OB-4)."""
    start, end = m.start(), m.end()
    hi = min(len(view), end + _SCOPE_WINDOW)
    if _AFFIRMED_AFTER_RE.search(view, end, hi):
        return False
    universal = bool(_UNIVERSAL_RE.search(m.group(0)))
    if _exempted_by_scope(view, start, _FORECAST_EXEMPT_RE) or _warning_framed(
            view, start, end, debunk_after=universal):
        return True
    if names_co:
        return False
    debunked_next = bool(next_sentence and _DEBUNK_NEXT_RE.match(next_sentence))
    if universal and (debunked_next
                      or _myth_labelled(view, start, labelled_from=0 if labelled else -1)):
        return True
    if _warning_object_governs(view, start, end):
        return True                    # "Ignore claims that the market will…": the warning corrects
    if not _belief_governs(view, start, end, _FORECAST_REPORT_RE):
        return False
    if _DOUBT_TURN_RE.search(view, end, hi) or (next_sentence and _DOUBT_NEXT_RE.match(next_sentence)):
        return True
    return universal and bool(_NEGATING_TURN_RE.search(view, end, hi))


#: Banned phrases that are a PROMISE ("guaranteed returns", "risk-free", "can't lose"): the
#: same clause frames exempt them — 'Beware of "Guaranteed Returns"' and "ETFs make investing
#: simpler and steadier, not magically risk-free" warn against the promise (round 2, real
#: drafts / the etfs_101 sheet). The rest of the list is exempt from nothing.
_PROMISE_BANNED = frozenset({
    "guaranteed return", "guaranteed returns", "guaranteed profit", "guaranteed profits",
    "guaranteed gain", "guaranteed gains", "guaranteed income", "guaranteed to", "proven returns",
    "proven strategy", "risk-free", "risk free", "can't lose", "cannot lose", "get rich",
})


def _sentence_spans(folded: str) -> List[Tuple[int, int]]:
    """(start, end) of each `sentences()` part inside `folded`. Linear."""
    out: List[Tuple[int, int]] = []
    cursor = 0
    for part in sentences(folded):
        i = folded.find(part, cursor)
        if i < 0:
            continue
        out.append((i, i + len(part)))
        cursor = i + len(part)
    return out


#: Calls to action are code-owned (per-platform CTA in `post_copy`). Matched as a VERB + an
#: engagement TARGET, never a bare verb: the corpus says "Follow the money.", "tap a card", "in
#: one click", "sign up billions of cardholders", "an installed base" and "subscribers".
_CTA_RES = (
    re.compile(r"\b(?:tap|click|press|hit|swipe up|swipe|scroll down|scroll up)\s+(?:on\s+)?"
               r"(?:the\s+|this\s+|that\s+|our\s+)?(?:link|button|here|below|above|bell)\b"),
    re.compile(r"\blink\s+(?:below|above|here|in (?:the )?(?:bio|description|comments?|profile|"
               r"caption|post))\b|\b(?:the|our) link (?:is )?(?:below|above)\b"),
    re.compile(r"\b(?:tap|click|use|visit|open|hit|check|follow|see) (?:the|this|that|our) link\b"),
    # "follow the plan today" and "members who join today" are corpus/case-study prose: only
    # subscribe/sign up/install take a bare time word; follow/join need an engagement target.
    re.compile(r"\b(?:subscribe|sign up|install)\b(?:\s+[a-z']+){0,2}?\s+(?:for (?:more|daily|"
               r"weekly|free|updates|lessons|tips)|today|now|free|to (?:learn|get|read|see) more)\b"),
    re.compile(r"\bfollow (?:along|us|for (?:more|daily|weekly|updates|lessons|tips))\b|\bjoin "
               r"(?:us|the (?:channel|community|newsletter|list|waitlist)|for free)\b|\bsubscribe "
               r"(?:to (?:the|our|this) )?(?:channel|newsletter|podcast|feed)\b"),
    re.compile(r"(?:^|[.!?]\s+)(?:subscribe|sign up|install it|install the app|join us|join now|"
               r"join today|follow along|follow for)\b"),
    # "users open the app" / "try the app" are case-study facts (TikTok, Instagram); only the
    # promotional verbs, or "our/this app", are a CTA.
    re.compile(r"\b(?:get|grab|install) (?:the|our|this) app\b|\b(?:try|open|use) (?:our|this) app\b|"
               r"\bfull (?:lesson|lessons|"
               # "The full story of the App Store is a lesson in recurring revenue" (Apple).
               r"story|breakdown|article|guide|course|video)\b[^.!?]{0,40}\b(?:app(?!\s*store)|"
               r"link|profile|bio|channel)\b"),
    # Round 2 (W2-OB-4): "install" and "share this" need an engagement target — "Customers used
    # to install the software from a box" (Microsoft) and "Visa and Mastercard share this model"
    # are case studies.
    re.compile(r"\bfree\s+(?:trial|for\s+(?:a|one|\d+)\s+(?:day|week|month)s?|download|forever|"
               r"to (?:try|join|start|download))\b|\btry (?:it|us|this) (?:free|for free|today|now)"
               r"\b|\b(?:start|begin) (?:your|a) (?:free )?trial\b|\binstall (?:it|this)\s+(?:now|"
               r"today|free|for free|here)\b|\binstall (?:the|our|this) app\b"),
    re.compile(r"\b(?:save|bookmark) this\b|\bsave (?:this|it) for later\b|\b(?:share|repost|"
               r"retweet|forward) (?:this|it) (?:with|to)\b|\bshare this\b(?=\s*(?:[.!?,]|$|\s+(?:post|"
               r"video|reel|thread|clip|lesson|carousel|tip|tips|now|today|if|and|around|everywhere)"
               r"\b))|\bsend (?:this|it) to (?:a|your) friend\b|\btag (?:a|your) friend\b"),
    re.compile(r"\bcomment\s+(?:below|yes|here|[a-z]+\s+below)\b|\b(?:drop|leave) a comment\b|"
               r"\bin the comments\b|\b(?:let us|tell us) know\b|\bdm (?:us|me)\b"),
    re.compile(r"\b(?:check out|head to|visit|go to) (?:the|our|my) (?:profile|bio|page|channel|"
               r"website|site)\b|\bstay tuned\b|\bturn on notifications\b|\bhit the bell\b"),
    # ── round 2 (W2CB-11): engagement bait the rows above never listed. Each is anchored to an
    # engagement target: "part two of the plan", "save it for retirement", "users stay in the
    # app" and "The App Store" are case-study prose. ──
    re.compile(r"\b(?:in|check|see|visit) (?:the|my|our) bio\b(?!-)(?=\s*[.!?,;:)]|\s*$|\s+(?:for|to|"
               r"and|if|now|today)\b)|\bhit (?:follow|subscribe|like if|like and|the like button|that "
               r"like button|the follow button)\b|\bdouble[- ]?tap\b|\blike this (?:post|video|reel|"
               r"thread|clip)\b(?!-)|\bbookmark (?:it|this)\b|\b(?:send|share) (?:this|it) (?:to|with) "
               r"someone\b|\b(?:send|share|forward) (?:to|with) someone (?:who|that|you)\b|"
               r"\btag someone\b"),
    re.compile(r"\b(?:for|see|watch for|stay tuned for|look out for) part (?:two|2|ii)\b(?! of\b)|"
               r"(?:^|[.!?]\s+)part (?:two|2|ii)\s+(?:drops|is coming|coming|comes|tomorrow|next|soon|"
               r"is next|is up|is live|is out|out now)\b"),
    re.compile(r"\bfree (?:guide|ebook|e-book|cheat ?sheet|checklist|course|pdf|template|"
               r"masterclass|webinar|workbook)s?\b|\b(?:read|get|find|see|watch) the rest (?:in|on|"
               r"at) (?:the|our|my) (?:app|bio|profile|channel|site|website|page|link)\b|\bthe rest "
               r"is in the app\b|\bthe app has (?:the rest|more|it all|the full)\b"),
)
#: Social proof and endorsement: no regulator, professional or audience vouches for a post, and
#: nothing counts how many people use or like it. Shaped as claims, not nouns: the corpus has
#: "thousands of aircraft", "millions of merchants", "trusted by all of them" and "state-backed".
#: What an endorsement vouches for: THIS content or its advice ("this rule", "these tips"), never
#: "these terms" ("Many people use these terms as if they are the same" — a real draft).
_VOUCHED_THING = (r"(?:[a-z-]+\s+)?(?:rule|rules|lesson|lessons|strategy|strategies|trick|tricks|"
                  r"habit|habits|tip|tips|method|methods|approach|system|framework|post|posts|video|"
                  r"videos|guide|course|playbook|advice|mindset|hack|hacks)\b")
#: THIS content only — never a strategy, an approach or a system, which a case study's company
#: has too ("This strategy transformed how millions of businesses buy software", "This strategy
#: helped millions of people save" — Costco). Round 3 (W3CB-12).
_CONTENT_THING = (r"(?:[a-z-]+\s+)?(?:lesson|lessons|rule|rules|tip|tips|post|posts|video|videos|"
                  r"guide|course|habit|habits|trick|tricks|advice|series|channel|content|hack|"
                  r"hacks|mindset)\b")
_ENDORSEMENT_RES = (
    # Vouched for by a regulator or a finance professional. The AGENT is what makes it a claim:
    # "approved by regulators" (a drug) and "certified by the FAA" (a jet) are case-study facts.
    re.compile(r"\b(?:approved|endorsed|accredited|vetted|recommended|reviewed|verified|certified|"
               r"licensed|sanctioned) by (?:a |an |the |our )?(?:certified |licensed |registered |"
               r"independent )?(?:sec|finra|cfps?|cfas?|financial (?:experts?|professionals?|"
               r"planners?|advis[eo]rs?|pros?)|investment (?:experts?|professionals?|advis[eo]rs?|"
               r"pros?)|wall street (?:experts?|pros?)|advis[eo]rs?|planners?)\b"),
    re.compile(r"\b(?:endorsed|vetted|recommended|praised) by (?:a |the |our )?(?:experts?|"
               r"professionals?|pros|investors|analysts|economists|thousands|millions)\b"),
    # THIS content vouched for, by anyone.
    re.compile(r"\b(?:this|these|our) (?:lesson|lessons|post|posts|content|course|guide|video|"
               r"series|method|tips?|rule|rules|strategy|trick|habit|approach|system)\b[^.!?]{0,40}"
               r"\b(?:approved|endorsed|certified|accredited|vetted|recommended|reviewed|licensed|"
               r"verified|backed|trusted|loved|used|followed) by\b"),
    re.compile(r"\b(?:sec|finra|cfp|cfa|fdic|regulator)[- ](?:approved|registered|endorsed|"
               r"certified|licensed|vetted|reviewed|backed)\b"),
    # The audience of THIS content, never a case study's customers ("Members love the treasure
    # hunt" is Costco, "viewers love franchises" is Netflix).
    # No "fans"/"students": "Fans love Disney's franchises" and "graduate students loved CUDA"
    # are a case study's customers (round 2, W2-OB-4). A fan or student vouching for THIS
    # content is caught by the "this lesson / these tips" rows.
    re.compile(r"\b(?:readers|learners|listeners|followers) (?:say|said|love|loved|"
               r"told|tell|swear|rave|agree|call it|call this|call these|rate)\b"),
    re.compile(r"\b(?:thousands|millions|hundreds) of (?:readers|users|learners|students|"
               r"subscribers|listeners|viewers|followers|beginners|people|investors)\b[^.!?]{0,30}"
               r"\b(?:this|these|our) (?:lesson|lessons|post|posts|course|guide|video|series|"
               r"content|method|rule|rules|strategy|trick|habit|tip|tips|approach|system)\b"),
    # ── round 2 (W2CB-9): the same claim with the number, the noun or the grammar changed ──
    # One audience member vouching: "One reader told us this lesson changed how they invest", "A
    # listener wrote in", "A beginner told us: …". The audience of THIS content only — a
    # case study's members, customers, viewers and subscribers act, they do not vouch.
    re.compile(r"\b(?:a|one|another) (?:reader|listener|learner|beginner)\b[^.!?]{0,30}?"
               r"\b(?:told|tells|wrote|writes|said|says|put it|shared|messaged|emailed|commented|"
               r"swears?|swore|raved|admitted|confessed)\b"),
    # A count of people who use or love THIS thing: "Thousands of beginners already use this
    # rule", "Countless beginners swear by this strategy". The object is what makes it a claim:
    # "many investors follow the crowd" is the lesson.
    re.compile(r"\b(?:thousands|millions|hundreds|countless|many|so many|lots|tons) (?:of )?"
               r"(?:readers|learners|listeners|followers|beginners|investors|people|savers|newcomers|"
               r"pros|experts|traders)\b[^.!?]{0,25}?\b(?:use|uses|used|follow|follows|followed|love|"
               r"loves|loved|swear by|swore by|trust|trusts|trusted|rely on|relied on)\s+(?:[a-z-]+\s+)?"
               r"(?:this|these|our)\s+" + _VOUCHED_THING),
    # Round 3 (W3CB-12): "Beginners everywhere are loving this simple rule", "Readers keep
    # raving about these tips".
    re.compile(r"\b(?:readers|learners|listeners|followers|beginners|investors|pros|experts|"
               r"professionals|traders|savers|fans|students|viewers|subscribers)\s+(?:everywhere\s+)?"
               r"(?:(?:already|all|really|truly)\s+)?(?:(?:are|keep|have been)\s+)?(?:love|loved|"
               r"loving|swear by|swore by|swearing by|trust|trusting|rave about|raving about|can't "
               r"stop (?:using|sharing|talking about))\s+(?:this|these|our)\s+" + _VOUCHED_THING),
    # A case study's fans/students/viewers act; vouching for THIS content is the claim ("Fans
    # say this lesson changed everything", "Students told us it's the best").
    re.compile(r"\b(?:fans|students|viewers|subscribers)\s+(?:say|said|tell us|told us|agree|swear|"
               r"rave|call it|call this)\b[^.!?]{0,40}?\b(?:this|these|our|it's|it is)\b"),
    re.compile(r"(?:^|[.!?]\s+)(?:loved|trusted|used|followed|adored) by (?:thousands|millions|"
               r"hundreds|countless|many|so many) (?:of )?(?:readers|learners|beginners|investors|"
               r"followers|listeners|people like you|savers)\b"
               # Only THIS content helping people: "Costco helped millions of people save" is
               # the case study — and so is "This strategy helped millions of people save"
               # (W3CB-12: content nouns only, never a strategy or a system).
               r"|\b(?:this|these|our)\s+" + _CONTENT_THING + r"[^.!?]{0,30}?\bhelped (?:thousands|"
               r"millions|hundreds|countless|so many|many) (?:of )?(?:readers|learners|beginners|"
               r"investors|people|savers|newcomers)\b"),
    # Authority by consensus: "Experts agree: discipline wins", "Financial pros swear by this".
    re.compile(r"\b(?:pros|experts|professionals|money pros|financial pros|wall street pros)\b"
               r"[^.!?]{0,15}?\b(?:swear by|agree|recommend|endorse|approve of|(?:love|use|follow|"
               r"trust) (?:this|these|our)\s+" + _VOUCHED_THING + r")"),
    re.compile(r"\brated\b[^.!?]{0,30}\bby (?:users|readers|learners|experts|critics|investors)\b|"
               r"\b(?:top|best|highest|#1)[- ]rated\b"),
    re.compile(r"\b(?:this|these) (?:lesson|lessons|post|course|guide|video|method) "
               r"(?:changed|saved|transformed) (?:my|your|their|his|her|everything|lives)\b"),
    # ── round 3 (W3CB-12) ──
    # One member of THIS content's audience vouching, by a noun a case study also uses for its
    # customers ("A subscriber said this lesson changed how she invests", "A member told us this
    # rule…"). The "this / these / our / it's" anchor is what makes it a testimonial: "A
    # follower shared the video with friends" and "a member renewed" are the case study.
    re.compile(r"\b(?:a|one|another) (?:subscriber|viewer|member|student|user|fan)\b[^.!?]{0,30}?"
               r"\b(?:told|tells|wrote|writes|wrote in|said|says|shared|messaged|emailed|commented|"
               r"swears?|swore|raved|admitted|confessed)\b[^.!?]{0,40}?\b(?:this|these|our|it's|"
               r"it is)\b"),
    # THIS content changing how a crowd invests: "This lesson has changed how thousands of
    # people invest", "Thousands of people have changed how they invest after this lesson". A
    # COUNT word, never "many" ("This habit has changed how many people think" is a question).
    re.compile(r"\b(?:this|these|our)\s+" + _CONTENT_THING + r"[^.!?]{0,30}?\b(?:changed|"
               r"transformed|reshaped|improved)\b[^.!?]{0,20}?\b(?:thousands|millions|hundreds|"
               r"countless)\b"
               r"|\b(?:thousands|millions|hundreds|countless) of (?:[a-z-]+ )?(?:people|readers|"
               r"learners|beginners|investors|savers|viewers|listeners|students|subscribers)\b"
               r"[^.!?]{0,40}?\b(?:changed|transformed|improved|reshaped)\b[^.!?]{0,40}?\b(?:after|"
               r"with|thanks to|because of|since) (?:this|these|our)\s+" + _CONTENT_THING),
)

# ── links, handles, markup ────────────────────────────────────────────────────

#: TLDs recognised in a SPOKEN or SPACED address ("caydexinvest dot com", "investor . gov"). A
#: written domain needs no list: `BARE_DOMAIN_RE` is structural.
_TLDS = ("com|net|org|io|co|app|ai|finance|money|xyz|info|biz|us|uk|me|ly|gg|tv|news|to|"
         "dev|link|site|online|store|shop|live|club|ca|de|fr|in|au|jp|cn|ru|eu|gov|edu|mil|int|"
         "page|gl|so|fund|fyi|top|lol|ws|cc|tk|is|it|be|nl|es|ch|se|no|pro|mobi|gd|vc|sh|im")
#: Any written domain: a label (letters/digits/hyphens, ANY case) + dot + a LOWER-CASE TLD of
#: 2-24 letters, e.g. "investor.gov", "SEC.gov", "goo.gl/abc", "caydexinvest.page". The TLD is
#: case-sensitive on purpose: a missing space after a full stop ("sell.Then", "day.The") is a
#: typo, not a link, while "e.g.", "U.S.", "Mr. Market", "vs. Mastercard", "3.5x" and "~$85B+"
#: never match (one-letter or non-letter "TLD", or a space after the dot). Run on `_link_view`,
#: where ideographic/halfwidth/small full stops and inner interpuncts have become ".". Exported
#: for `post_copy`'s X length count: a bare domain is a URL to X (23 characters), whatever the
#: validator decided.
BARE_DOMAIN_RE = re.compile(
    r"(?<![A-Za-z0-9.\-])[A-Za-z0-9][A-Za-z0-9-]{0,62}(?:\.[A-Za-z0-9-]{1,63}){0,8}"
    r"\.[a-z]{2,24}(?![A-Za-z0-9\-])"
)
_LINK_RES = (
    re.compile(r"\b(?:https?|ftp)://", re.IGNORECASE),
    re.compile(r"\bwww\.", re.IGNORECASE),
    BARE_DOMAIN_RE,
    # Spoken: only TLDs that are not also English words ("a dot in the chart", "each dot is").
    re.compile(r"\bdot\s?(?:com|net|org|io|co|app|gov|edu|ai|ly|gl|xyz|info|biz|page|dev|tv)\b",
               re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9][A-Za-z0-9-]{1,62}\s+\.\s*(?:" + _TLDS + r")\b"),
    re.compile(r"\[\s?(?:\.|dot)\s?\]|\(\s?(?:\.|dot)\s?\)", re.IGNORECASE),
    re.compile(r"\b(?:javascript|data|mailto|tel|file|sms|vbscript):", re.IGNORECASE),
)
#: Dots a reader (and an autolinker) sees as ".": IDEOGRAPHIC / HALFWIDTH IDEOGRAPHIC / SMALL /
#: FULLWIDTH FULL STOP, ONE DOT LEADER, HYPHENATION POINT, SYRIAC SUPRALINEAR FULL STOP.
_LINK_DOT_FOLD = str.maketrans({c: "." for c in "。｡﹒．․‧܁⸮"})
#: An interpunct between two lower-case letters/digits with no space is a disguised dot
#: ("caydexinvest·com"); a list separator is spaced or capitalised ("Marvel·Star").
_LINK_INTERPUNCT_RE = re.compile(r"(?<=[a-z0-9])[" + _INTERPUNCTS + r"](?=[a-z])")


#: Lower-case abbreviations a missing space can glue to the next word ("vs.the", "etc.and").
_LINK_ABBREVIATIONS = _ABBREVIATIONS | frozenset({"etc", "viz", "cf", "eg", "ie", "approx"})


_KNOWN_TLDS = frozenset(_TLDS.split("|"))
#: A Title-Case or upper-case TLD an autolinker still links (round 2, W2CB-13): "Visit
#: Learn.Money for more", "The Moat.App", "Read at Money.Com". Only gTLDs of three or more
#: letters that are not English words glued after a full stop — never a 2-letter ccTLD, so a
#: missing space ("Prices rose.It was a boom", "Stay calm.So what?", "It fell.No one knew") and
#: "sell.Then" / "day.The" stay typos.
_CASED_GTLDS = ("com|net|org|app|money|finance|gov|edu|page|fund|news|shop|store|live|link|site|"
                "online|club|info|biz|dev|xyz|fyi|lol|mobi|int|mil|io|ai")
_CASED_GTLD_RE = re.compile(
    r"(?<![A-Za-z0-9.\-])[A-Za-z0-9][A-Za-z0-9-]{0,62}(?:\.[A-Za-z0-9-]{1,63}){0,8}"
    r"\.(?i:" + _CASED_GTLDS + r")(?![A-Za-z0-9\-])")


#: Round 4 (residual d): a glued abbreviation with a Title- or upper-case tail ("U.S.Markets",
#: "e.g.Bank", "vs.Best"). X autolinks a TLD in any case, but `BARE_DOMAIN_RE` needs a lower-case
#: TLD and `_CASED_GTLDS` lists a few gTLDs only, so these reached neither the validator nor the X
#: counter. Only an abbreviation-run HEAD (two or more one-letter labels, or a known
#: abbreviation), so a plain missing space ("sell.Then", "day.The", "calm.So") is untouched;
#: `_is_abbreviation_run` then exempts it only when its tail is KNOWN not to be a TLD.
_ABBREV_HEADS = "|".join(sorted(_LINK_ABBREVIATIONS, key=len, reverse=True))
_CASED_ABBREV_RUN_RE = re.compile(
    r"(?<![A-Za-z0-9.\-])(?:(?:[A-Za-z]\.){2,8}|(?i:" + _ABBREV_HEADS + r")\.)[A-Za-z]{2,24}"
    r"(?![A-Za-z0-9\-])")
#: Every domain-shaped span a reader's autolinker (and X) may link — the validator and the X
#: length counter (`post_copy`) iterate the same tuple, so they agree on what a link is.
DOMAIN_SHAPE_RES = (BARE_DOMAIN_RE, _CASED_GTLD_RE, _CASED_ABBREV_RUN_RE)


def _is_abbreviation_run(domain: str) -> bool:
    """A typo, not a domain: an initialism glued to a word ("U.S.dollar", "e.g.the", "i.e.the":
    two or more one-letter labels) or a known abbreviation ("vs.the") — and only when the tail
    is KNOWN not to be a TLD (`tlds.NON_TLD_TAILS`, case-insensitive). Round 4 (residual d)
    inverted the test: it used to exempt any tail missing from the short spoken-TLD list, and
    ".markets", ".bank", ".one", ".you", ".best" are real gTLDs X links ("U.S.markets",
    "e.g.bank", "vs.best"). "x.y.com" and "vs.com" stay links; one one-letter label is still a
    real host ("t.co", "x.com")."""
    labels = domain.split(".")
    head, tail = labels[:-1], labels[-1]
    if not is_non_tld_tail(tail):
        return False
    if len(head) >= 2 and all(len(label) == 1 for label in head):
        return True
    return len(head) == 1 and head[0].lower() in _LINK_ABBREVIATIONS


def _link_hit(view: str) -> str:
    for rx in _LINK_RES:
        if rx is BARE_DOMAIN_RE:
            for bare in DOMAIN_SHAPE_RES:
                for m in bare.finditer(view):
                    if not _is_abbreviation_run(m.group(0).lower()):
                        return m.group(0)
            continue
        m = rx.search(view)
        if m:
            return m.group(0)
    return ""


def _link_view(text: str) -> str:
    """What an autolinker sees: disguised dots folded to ".", accents removed ("caydexinvést")."""
    if text.isascii():
        return text
    return skeleton(_LINK_INTERPUNCT_RE.sub(".", text.translate(_LINK_DOT_FOLD)))


_HANDLE_RE = re.compile(r"(?<![A-Za-z0-9_@.])@[A-Za-z0-9_]{2,}")
_MARKUP_RES = (
    re.compile(r"<\s*/?\s*[A-Za-z!?]"),
    re.compile(r"\]\s?\("),
    re.compile(r"!\["),
    re.compile(r"\*\*|__|`"),
    # A heading or block quote marker opening a line ("# Mr. Market", "> quote"); a hashtag has
    # no space after its "#" and is reported as `hashtag`.
    re.compile(r"(?m)^[ \t]*(?:#{1,6}|>)[ \t]"),
    re.compile(r"\|\|"),
    # Paired single/double emphasis ("*moody*", "_moody_", "~~never~~"). The pair must match
    # and hug non-space text, so "~70%", "from ~20 to ~70%", "snake_case" and "5*3" stay legal.
    re.compile(r"(?<![\w*_~])([*_~]{1,2})(?=[^\s*_~])[^\n]{0,200}?(?<=[^\s*_~])\1(?![\w*_~])"),
    # A character reference that survived `clean()`'s single decode ("&amp;lt;").
    _ENTITY_RE,
    # Round 4 (residual h): an ASS/libass override. Phase 3 burns captions through libass, where
    # "{…}" is an override block ("{\p1}m 0 0{\p0}" draws a shape, "{\alpha&HFF&}" hides text)
    # and "\N", "\n", "\h" are line and space escapes. None of the three characters has a use in
    # this copy, so each is refused in EVERY field — the same text feeds captions and cards.
    re.compile(r"[{}\\]"),
)
_HASHTAG_RE = re.compile(r"(?<![A-Za-z0-9&])#[A-Za-z][A-Za-z0-9_]*")
_CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9$])\$\s?[A-Za-z]{1,6}(?![A-Za-z])")


def _is_latin_letter(ch: str) -> bool:
    o = ord(ch)
    return o < 0x0250 or 0x1E00 <= o <= 0x1EFF


def _is_emoji(ch: str) -> bool:
    o = ord(ch)
    return (o >= 0x1F000 or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF
            or o in (0xFE0F, 0x20E3) or unicodedata.category(ch) == "So")


# ── the scan ──────────────────────────────────────────────────────────────────


_COMPANY_TOKEN_RE = re.compile(r"[a-z0-9&]+(?:-[a-z0-9&]+)*")


def _names_company(folded_sentence: str, company_terms: FrozenSet[str]) -> bool:
    if not company_terms:
        return False
    # Whole hyphenated tokens ("coca-cola") AND their parts ("visa" in "visa-free"), so a
    # hyphenated company term can match at all.
    words = set()
    for tok in _COMPANY_TOKEN_RE.findall(folded_sentence.replace("'s", "")):
        words.add(tok)
        if "-" in tok:
            words.update(p for p in tok.split("-") if p)
    return bool(words & company_terms)


def scan_text(field: str, text: str, *, allow_emoji: bool = False,
              strict_instruments: bool = True, company_terms: FrozenSet[str] = frozenset(),
              max_chars: int = _SCAN_CAP, myth_framed: bool = False,
              sheet_words: FrozenSet[str] = frozenset(), next_text: str = "") -> List[Violation]:
    """Every compliance violation in one field. `text` must already be `clean()`ed — the
    caller stores exactly what was scanned. `strict_instruments` (default ON, fail-closed) is
    turned off only for Journey items, whose posts name no instrument (see CLASS_B_TIER1) — and
    even there a sentence that names a company gets the strict rows. `company_terms` (lower-case
    proper nouns of the item) make a tier-2 evaluative word a violation in any sentence that
    names one: "Costco looked expensive" is a verdict on an issuer even with no "stock" in the
    sentence; the company lexicon does the same for every company it knows. `myth_framed`: the
    text is the body of a card or slide whose TITLE labels it a myth — its first sentence states
    the misconception (the promise rows exempt it, nothing else). `sheet_words`: the item's
    fact-sheet vocabulary, lower case — a capitalised word the sheet itself uses is no company
    name for the Journey escalation (`_proper_noun_signal`). `next_text`: the field a reader
    meets right after this one (the next script line, the body under a title, the script after
    the hook) — its first sentence ANSWERS a closing question, debunks or affirms a closing
    claim exactly as a next sentence in this field would (round 3, W3CB-2). Never raises."""
    out: List[Violation] = []

    def v(code: str, detail: str) -> None:
        out.append(Violation(field, code, detail[:160]))

    if not text or not text.strip():
        v("empty", "field is empty")
        return out
    # The scan never looks past `_SCAN_CAP`, whatever the caller asks for — and text it did not
    # scan cannot pass, so a larger `max_chars` is clamped to the cap (fail-closed).
    limit = min(max_chars, _SCAN_CAP)
    if len(text) > limit:
        v("too_long", f"{len(text)} chars > {limit}")
        text = text[:limit]
    folded = fold(text)
    # Accent-free, case-kept view for every name/link lexicon ("Buffétt", "Gémini", "Cáydex");
    # the non-Latin and digit checks below still read the original.
    sk = skeleton(text)

    if has_non_ascii_digit(text):
        v("non_ascii_digit", "digits must be ASCII 0-9")
    bad_letters = sorted({ch for ch in text if ch.isalpha() and not _is_latin_letter(ch)})
    if bad_letters:
        v("non_latin", "".join(bad_letters[:8]))
    if not allow_emoji:
        emoji = sorted({ch for ch in text if _is_emoji(ch)})
        if emoji:
            v("emoji", "".join(emoji[:8]))

    for hit in _person_hits(folded, sk):
        v("person_named", hit)
    for hit in _ambiguous_surname_hits(text):
        v("person_named", hit)
    for hit in _given_name_hits(sk, strict_instruments, company_terms):
        v("person_named", hit)
    for hit in _described_person_hits(folded, sk, strict_instruments):
        v("person_named", hit)
    quote = _famous_quote_hit(folded)
    if quote:
        v("famous_quote", quote)

    prepared = _prepared_sentences(sk, own_word_brands(company_terms))
    views_all = tuple(view for _s, _m, _f, view in prepared)

    def views() -> Tuple[str, ...]:
        return views_all

    for code, rx, strict_only, exempt in _CLASS_B_TIER1_RE:
        if strict_only and not strict_instruments:
            continue
        m = _tier1_first(rx, exempt, folded, views)
        if m:
            v(f"class_b_{code}", m.group(0))
    # Only the first sentence of the next field is read, from a bounded prefix.
    upcoming = sentences(fold(next_text[:_NEXT_TEXT_CAP])) if next_text else []
    next_first = upcoming[0] if upcoming else ""
    for code, detail in _sentence_hits(sk, strict_instruments, company_terms, sheet_words,
                                       prepared, myth_framed=myth_framed, next_first=next_first):
        v(code, detail)

    spans = _sentence_spans(folded)
    span_starts = [a for a, _b in spans]
    # The companies the case-kept scan found anywhere in the field (a word-brand is only a
    # company in a name position, so detection needs the case the folded spans have lost).
    named = sorted({name for _s, mentions, _f, _v in prepared for name, _a, _b in mentions},
                   key=len, reverse=True)
    named_re = (re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(n) for n in named)
                           + r")(?![a-z0-9])") if named else None)

    def names_company(i: int) -> bool:
        return named_re is not None and named_re.search(folded, *spans[i]) is not None

    def after(i: int) -> str:
        """What a reader meets after sentence i: the next sentence, or the next field's first."""
        return folded[spans[i + 1][0]:spans[i + 1][1]] if i + 1 < len(spans) else next_first

    def exempt_in(i: int, pos: int, end: int) -> bool:
        if names_company(i):
            return False  # no frame licenses a promise about a named company (see below)
        a, b = spans[i]
        nxt = after(i)
        return (_promise_exempt(folded[a:b], pos - a, min(end, b) - a,
                                labelled=myth_framed and i == 0,
                                affirmed=bool(nxt and _AFFIRM_NEXT_RE.match(nxt)),
                                unanswered=not nxt)
                or bool(nxt and _DEBUNK_NEXT_RE.match(nxt)))

    def promise_framed(pos: int, end: int) -> bool:
        i = bisect_right(span_starts, pos) - 1          # O(log n): a repetition loop of
        if i < 0 or pos >= spans[i][1]:                 # "risk-free. " stays linear
            return False
        return exempt_in(i, pos, end)

    for rx, code in ((_BANNED_RE, "banned_phrase"), (_MISATTR_RE, "misattribution"),
                     (_IDENTITY_RE, "identity_leak"), (_BRAND_RE, "brand_mention")):
        for m in rx.finditer(folded):
            if rx is _BANNED_RE and m.group(0) in _PROMISE_BANNED \
                    and promise_framed(m.start(), m.end()):
                continue
            v(code, m.group(0))
    m = _SIGNALS_RE.search(folded)
    if m:
        v("banned_phrase", m.group(0))
    m = _APP_STORE_CTA_RE.search(folded)
    if m:
        v("brand_mention", m.group(0))
    if _ranking_claim(folded):
        v("banned_phrase", "#1 claim")
    for rx in _CODE_OWNED_RES:
        m = rx.search(folded)
        if m:
            v("code_owned", m.group(0))
    for rx in _CTA_RES:
        m = rx.search(folded)
        if m:
            v("cta", m.group(0))
    for rx in _ENDORSEMENT_RES:
        m = rx.search(folded)
        if m:
            v("endorsement", m.group(0))
    # First person is a testimonial/self-reference ("I made 300%", "let me show you", "our
    # readers say"). A reader's self-question ("ask: am I giving it time?") and a quoted
    # thought-QUESTION are the corpus's own teaching devices and are exempt; a narrated experience
    # in question form ("Want to know how I stopped panic selling?") and a quoted declarative
    # testimonial are not.
    straight = _QUOTED_QUESTION_RE.sub(" ", text.translate(_QUOTE_FOLD))
    for sent in sentences(straight):
        fp = _FIRST_PERSON_RE.search(sent)
        if fp is not None and _is_self_question(sent, fp):
            fp = None
        if fp is None:
            fp = _FIRST_PLURAL_RE.search(sent)
        if fp is not None:
            v("first_person", fp.group(0))
            break
    # Promises and predictions — quoted or not. A quoted pitch is exempt only by the same clause
    # rules as an unquoted one ('The words "guaranteed high returns" aren't a gift').
    for i, (a, b) in enumerate(spans):
        nxt = after(i)
        framable = not names_company(i)
        hit = _promissory_hit(folded[a:b], labelled=myth_framed and i == 0,
                              affirmed=bool(nxt and _AFFIRM_NEXT_RE.match(nxt)),
                              unanswered=not nxt, framable=framable)
        if hit and not (framable and nxt and _DEBUNK_NEXT_RE.match(nxt)):
            v("promissory", hit)
            break

    link = _link_hit(_link_view(text))
    if link:
        v("link", link)
    m = _HANDLE_RE.search(sk)
    if m:
        v("handle", m.group(0))
    for rx in _MARKUP_RES:
        m = rx.search(text)
        if m:
            v("markup", m.group(0))
            break
    m = _HASHTAG_RE.search(sk)
    if m:
        v("hashtag", m.group(0))
    m = _CASHTAG_RE.search(sk)
    if m:
        v("cashtag", m.group(0))
    return out


def scan_many(fields: Sequence[Tuple[str, str]], *, allow_emoji: bool = False) -> List[Violation]:
    out: List[Violation] = []
    for name, value in fields:
        out.extend(scan_text(name, value, allow_emoji=allow_emoji))
    return out
