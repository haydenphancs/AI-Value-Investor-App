"""Conclusion lead-in stripping — the server-side twin of the iOS stripper.

PURE (stdlib only). The Updates Insights card and the per-article news bullets both
end with a conclusion that iOS marks with its own ↳ icon, so a lead-in in front of it
("The takeaway for everyday investors, …", "Investors should care because …") is
scaffolding the reader has to read past. The prompts forbid them; the model still
writes them (14 of 31 live cards on 2026-09-27 opened "Investors should…").

Two consumers, one set of rules:

* ``lead_in_remainder`` — the SERVER version. Applied to the conclusion before it is
  stored, so every app version (and the chat snapshot that reads the same bullets)
  gets clean text. No colon rewrite; at most ``_MAX_PASSES`` passes for stacked
  lead-ins ("In short, investors should care because …"); idempotent.
* ``display_strip`` — a faithful port of Swift's ``strippingConclusionLeadIn()``
  (``frontend/ios/ios/Core/Utilities/BulletTextFormatting.swift``) INCLUDING its
  ``normalizingLeadInColon`` fallback. It exists so a test can hold Python and Swift
  to the same case table; ``tests/test_conclusion_lead_in.py`` also parses the Swift
  stem arrays and fails on any drift from the constants below.

THREE STEM CLASSES, and the split is load-bearing:

* ``EXACT`` stems must BE the whole clause before the first ``,`` ``:`` ``—``. "So," is
  a lead-in; "So the Fed cut rates," is a sentence.
* ``OPEN_NOUN`` stems may be followed by more words ("The takeaway for everyday
  investors,"), because their head noun cannot begin an ordinary clause.
* ``PHRASE`` stems need no punctuation at all ("Investors should care because X"):
  they end in a conjunction, so the clause test can never see them.

GUARDS learned from review (2026-09-27), each one a real mangling:

* the remainder must not open with a CONTINUATION word — "For investors, especially
  retirees, the cut…" would become "Especially retirees, the cut…", "This matters
  because of Oracle's backlog" would become "Of Oracle's backlog";
* after a PHRASE, not with a pronoun either — "…care because they may face higher
  costs" would lose what "they" refers to;
* not a paired em-dash parenthetical, and not "X, is …" — "What this means, in
  practice, is higher rates" would become "In practice, is higher rates";
* capitalise the first letter only when the first word has no later capital, so
  "iPhone demand" never becomes "IPhone demand".

A bare "Investors should watch …" is deliberately NOT stripped: what remains ("Watch
…") is an imperative, i.e. advice. The prompt and the repair step handle it.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

EXACT_STEMS: Tuple[Tuple[str, ...], ...] = (
    ("so",),
    ("so", "what"),
    ("in", "short"),
    ("in", "summary"),
    ("in", "brief"),
    ("ultimately",),
    ("overall",),
    ("bottom", "line"),
    ("the", "bottom", "line"),
    ("net-net",),
    ("what", "this", "means"),
    ("what", "it", "means"),
    ("why", "it", "matters"),
    ("why", "this", "matters"),
    ("for", "investors"),
    ("for", "everyday", "investors"),
    ("what", "this", "means", "for", "investors"),
    ("what", "it", "means", "for", "investors"),
    ("the", "bottom", "line", "for", "investors"),
)

OPEN_NOUN_STEMS: Tuple[Tuple[str, ...], ...] = (
    ("the", "takeaway"),
    ("takeaway",),
    ("key", "takeaway"),
    ("the", "key", "takeaway"),
    ("the", "upshot"),
    ("upshot",),
)

PHRASE_STEMS: Tuple[Tuple[str, ...], ...] = (
    ("investors", "should", "care", "because"),
    ("everyday", "investors", "should", "care", "because"),
    ("why", "should", "investors", "care", "because"),
    ("this", "matters", "because"),
    ("this", "matters", "for", "investors", "because"),
    ("it", "matters", "because"),
    ("why", "it", "matters", "is", "that"),
    ("why", "this", "matters", "is", "that"),
)

# A remainder that opens with one of these is the middle of a sentence, not its start.
CONTINUATION_WORDS = frozenset({
    "and", "or", "but", "nor", "especially", "particularly", "notably",
    "including", "though", "however", "of", "is", "are", "was", "were",
    "which", "according", "because", "beyond",
})
# After a PHRASE only: the stripped words held the pronoun's antecedent — ANYWHERE in the
# remainder ("…because rising yields raise their borrowing costs" would read as the
# yields' costs). Such a sentence goes to the repair instead.
PHRASE_PRONOUNS = frozenset({"they", "their", "them", "theirs"})

MAX_WORDS = 6          # a real lead-in is short; past this the clause carries content
MAX_LEAD_IN = 48       # characters before the clause end
MIN_REST = 20          # a lead-in with nothing behind it is not a lead-in
_COLON_MAX_LEAD_IN = 40
_MAX_PASSES = 3

_EM_DASH = "—"
_CLAUSE_END = re.compile(r"[,:—]")
# Swift: split(whereSeparator: { !$0.isLetter && $0 != "-" }) — runs of letters/hyphens.
_WORD = re.compile(r"(?:[^\W\d_]|-)+")
_X_COMMA_IS = re.compile(r"^[^,:—]{1,40}[,—]\s*(is|are|was|were)\b", re.IGNORECASE)
_PHRASE_TRAIL = " ,:?—"
# A remainder must START like a sentence: a letter, a digit, an opening quote or a
# currency sign — never "– unlike peers –" or a closing quote.
_SENTENCE_START_EXTRA = "\"“'‘$€£¥"
_LETTERS = re.compile(r"[^\W\d_]+")
_TIME_COLON = re.compile(r"\d:\d")


def _words(text: str) -> List[str]:
    return [m.group(0).lower() for m in _WORD.finditer(text)]


def _first_word(text: str) -> str:
    """The first whitespace-delimited token, edge punctuation trimmed, lowercased.

    A TOKEN, not the first run of letters: "80% of Oracle's revenue…" starts with "80",
    and reading "of" there refused an honest strip (review 2026-09-27).
    """
    m = re.match(r"\s*(\S+)", text)
    if not m:
        return ""
    return re.sub(r"^[^\w-]+|[^\w-]+$", "", m.group(1)).lower()


def _leading_is_blank(text: str) -> bool:
    """Only whitespace before the first word — a quoted lead-in is content, not a lead-in."""
    m = _WORD.search(text)
    return m is not None and not text[: m.start()].strip()


def _capitalise(rest: str) -> str:
    """Upper-case the first character unless the first word is already cased
    internally (iPhone, eBay, xAI)."""
    if not rest:
        return rest
    m = _WORD.match(rest)
    if m and any(c.isupper() for c in m.group(0)[1:]):
        return rest
    return rest[:1].upper() + rest[1:]


def _plausible_remainder(rest: str, *, after_dash: bool) -> bool:
    if not rest or not (rest[0].isalnum() or rest[0] in _SENTENCE_START_EXTRA):
        return False
    if _first_word(rest) in CONTINUATION_WORDS:
        return False
    if after_dash and _EM_DASH in rest:
        return False
    if _X_COMMA_IS.match(rest):
        return False
    return True


def _after_phrase(text: str, max_lead_in: int = MAX_LEAD_IN) -> Optional[str]:
    """The text after a leading PHRASE stem, or None when none applies."""
    tokens = list(_WORD.finditer(text[: max_lead_in + 1]))
    if not tokens or text[: tokens[0].start()].strip():
        return None
    for stem in PHRASE_STEMS:
        if len(tokens) < len(stem):
            continue
        if all(tokens[i].group(0).lower() == stem[i] for i in range(len(stem))):
            end = tokens[len(stem) - 1].end()
            if end > max_lead_in:
                continue
            tail = text[end:]
            rest = tail.lstrip(_PHRASE_TRAIL).strip()
            dropped = tail[: len(tail) - len(tail.lstrip(_PHRASE_TRAIL))]
            if len(rest) < MIN_REST:
                return None
            if any(w.lower() in PHRASE_PRONOUNS for w in _LETTERS.findall(rest)):
                return None
            if not _plausible_remainder(rest, after_dash=_EM_DASH in dropped):
                return None
            return rest
    return None


def _after_clause(text: str, max_lead_in: int = MAX_LEAD_IN) -> Optional[str]:
    """The text after an EXACT / OPEN_NOUN clause, or None when none applies."""
    m = _CLAUSE_END.search(text)
    if m is None or m.start() > max_lead_in:
        return None
    if not _leading_is_blank(text[: m.start()]):
        return None
    words = _words(text[: m.start()])
    if not words or len(words) > MAX_WORDS:
        return None
    key = tuple(words)
    is_exact = key in EXACT_STEMS
    is_open = any(key[: len(stem)] == stem for stem in OPEN_NOUN_STEMS)
    if not (is_exact or is_open):
        return None
    rest = text[m.end():].strip()
    if len(rest) < MIN_REST:
        return None
    if not _plausible_remainder(rest, after_dash=m.group(0) == _EM_DASH):
        return None
    return rest


def normalizing_lead_in_colon(text: str, max_lead_in: int = _COLON_MAX_LEAD_IN) -> str:
    """Swift's `normalizingLeadInColon()`: an early colon becomes ", "."""
    idx = text.find(":")
    if idx < 0 or idx > max_lead_in:
        return text
    # Never a colon between digits — "at 4:05 p.m." is a time, not a label.
    if _TIME_COLON.match(text, max(0, idx - 1)):
        return text
    return f"{text[:idx]}, {text[idx + 1:].lstrip(' ')}"


def display_strip(text: str) -> str:
    """Port of Swift `strippingConclusionLeadIn()` — ONE pass, colon fallback included."""
    rest = _after_phrase(text)
    if rest is not None:
        return _capitalise(rest)
    rest = _after_clause(text)
    if rest is None:
        return normalizing_lead_in_colon(text)
    return _capitalise(rest)


def lead_in_remainder(text: str) -> str:
    """SERVER stripping: remove stacked lead-ins, never rewrite a colon.

    Returns ``text`` unchanged (whitespace-trimmed) when nothing applies. Never
    returns an empty string or a remainder shorter than ``MIN_REST``. Idempotent.
    """
    current = (text or "").strip()
    for _ in range(_MAX_PASSES):
        rest = _after_phrase(current)
        if rest is None:
            rest = _after_clause(current)
        if rest is None:
            break
        current = _capitalise(rest)
    return current
