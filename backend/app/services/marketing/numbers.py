"""
Numbers in marketing copy — parse, normalise, compare (SYSTEM_DESIGN_GUIDELINES §12.5).

Why this exists: the Learn corpus the writer is fed SPELLS numbers out (it was written to be
narrated — "forty-seven billion dollars") while its `statistics` rows use digits ("$47B"), and
the writer is told to answer in digits. The grounding validator (`grounding.py`) has to decide
"is every number in the draft one the source actually states?", so both sides must reduce to
the same `(value, unit)` pair whatever the spelling. This module is that reduction and nothing
else: no policy, no I/O, stdlib only.

Deliberately conservative:
* Only ASCII digits are numbers here. A fullwidth "４７", an Arabic-Indic digit or a vulgar
  fraction is not parsed — it is REJECTED upstream (`non_ascii_digits`), because a regex `\\d`
  matches them and a platform renders them, so parsing them would let an unground number
  through in a costume. (NFKC turns "¾" into "3⁄4" — ASCII digits around a FRACTION SLASH — so
  a vulgar fraction survives `clean()`; it and "3/4" are parsed as ONE `FRACTION` number valued
  0.75, a strict unit class, never as two exempt small counts.)
* A spelled number is converted only when it is a WELL-FORMED cardinal. "one two three" is three
  numbers, not 6, and "twenty twenty" is two, not 40: a run of number words that no English
  speaker would read as one number is split at the first word that breaks the grammar.
* Every pattern is linear (no nested quantifiers) and the spelled-number parser consumes each
  word at most once: these run on the single uvicorn worker over model output, and a
  catastrophic-backtracking regex (or a quadratic rescan of a "hundred hundred …" repetition
  loop) there stalls every user request.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

# ── spelled-out cardinals ─────────────────────────────────────────────────────

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_SCALES = {"thousand": 10**3, "million": 10**6, "billion": 10**9, "trillion": 10**12}

#: A single word run: letters, optionally hyphen-joined ("forty-seven", "one-time").
_ALPHA_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")

#: Longest spelled phrase converted. The grammar below already bounds a phrase (scales strictly
#: decrease, so "nine hundred and ninety-nine trillion …" is the worst case), this is the
#: belt-and-braces cap on top of it: past it the phrase ends and a new one starts.
MAX_PHRASE_WORDS = 16

# Word classes for the cardinal grammar.
_Z, _U, _T, _D, _DU, _H, _S, _DZ, _A, _AND, _HALF = (
    "zero", "unit", "teen", "tens", "tens_unit", "hundred", "scale", "dozen", "article", "and",
    "half",
)
#: After these a scale / "hundred" / "dozen" may multiply the group.
_GROUP_VALUE = (_U, _T, _D, _DU, _A, _HALF)


def _classify(tok: str) -> Tuple[Optional[str], int]:
    """(class, value) of one lower-cased word; (None, 0) for a word outside the grammar."""
    if tok in _UNITS:
        v = _UNITS[tok]
        if v == 0:
            return _Z, 0
        return (_U, v) if v < 10 else (_T, v)
    if tok in _TENS:
        return _D, _TENS[tok]
    if tok == "hundred":
        return _H, 100
    if tok in _SCALES:
        return _S, _SCALES[tok]
    if tok == "dozen":
        return _DZ, 12
    if tok in ("a", "an"):
        return _A, 1
    if tok == "and":
        return _AND, 0
    if tok == "half":
        return _HALF, 0
    if "-" in tok:
        a, _, b = tok.partition("-")
        if a in _TENS and b in _UNITS and 0 < _UNITS[b] < 10:
            return _DU, _TENS[a] + _UNITS[b]
    return None, 0


def _parse_at(words: List[Tuple[str, int, int]], adjacent: List[bool], i: int
              ) -> Optional[Tuple[int, int]]:
    """Longest well-formed cardinal starting at word `i`: `(value_in_halves, end_index)`, or
    None. Values are kept in HALVES so "half a million" and "two and a half billion" stay exact
    integers. Greedy and single-pass: a word is consumed only if the phrase so far plus that word
    is still a complete cardinal (the lookaheads for "a", "and", "half" are bounded), so the
    caller can resume at `end_index` and the whole scan stays linear."""
    n = len(words)

    def cls_at(k: int) -> Tuple[Optional[str], int]:
        return _classify(words[k][0]) if k < n else (None, 0)

    def joined(k: int) -> bool:
        """Word k and word k+1 are separated by exactly one space."""
        return k + 1 < n and adjacent[k]

    total2 = 0          # completed scale groups, in halves
    group2 = 0          # the group being built (< 1000), in halves
    has_h = False       # the group already carries "hundred"
    last_scale: Optional[int] = None
    j = i
    c0, v0 = cls_at(i)
    multipliers = (_H, _S, _DZ)
    #: Set by a lead-in ("a", "half a", "a half") that is only a number with a multiplier.
    needs_multiplier = False

    # ── the first word ──
    if c0 == _Z:
        return 0, i + 1                                   # "zero" is a whole number alone
    if c0 in (_U, _T, _D, _DU):
        group2, last, j = 2 * v0, c0, i + 1
    elif c0 == _A:
        c1 = cls_at(i + 1)[0] if joined(i) else None
        if c1 in multipliers:                             # "a hundred", "a million", "a dozen"
            group2, last, j = 2, _A, i + 1
            needs_multiplier = True
        elif c1 == _HALF and joined(i + 1) and cls_at(i + 2)[0] in multipliers:
            group2, last, j = 1, _HALF, i + 2             # "a half million"
            needs_multiplier = True
        else:
            return None                                   # "a lot", "a two-step plan"
    elif c0 == _HALF:
        if (joined(i) and cls_at(i + 1)[0] == _A and joined(i + 1)
                and cls_at(i + 2)[0] in multipliers):
            group2, last, j = 1, _HALF, i + 2             # "half a million", "half a dozen"
            needs_multiplier = True
        else:
            return None                                   # "half of it"
    else:
        # A bare scale word is a SUFFIX of a digit number ("200 million", "$40 billion"),
        # which the digit parser owns. Converting it alone turned "200 million" into
        # "200 1000000" — two ungrounded numbers where the source had one.
        return None

    # ── every following word: consumed only if the phrase stays a complete cardinal ──
    while j - i < MAX_PHRASE_WORDS and joined(j - 1):
        c, v = cls_at(j)
        if c in (_U, _T, _D, _DU):
            ok = (last in (_S, _H, _AND)) or (last == _D and c == _U)
            if not ok:
                break                                     # "one two", "twenty twenty"
            group2 += 2 * v
            last, j = c, j + 1
        elif c == _H:
            if has_h or last not in _GROUP_VALUE:
                break
            if last in (_T, _D, _DU) and total2:          # "fifteen hundred" only up front
                break
            group2 *= 100
            has_h, last, j = True, _H, j + 1
            needs_multiplier = False
        elif c == _S:
            if last not in _GROUP_VALUE + (_H,) or group2 <= 0:
                break
            if last_scale is not None and v >= last_scale:
                break                                     # scales strictly decrease
            total2 += group2 * v
            group2, has_h, last_scale = 0, False, v
            last, j = _S, j + 1
            needs_multiplier = False
        elif c == _DZ:
            if last not in _GROUP_VALUE or has_h or total2:
                break
            group2 *= 12
            j += 1
            needs_multiplier = False
            break                                         # "two dozen" ends the phrase
        elif c == _AND:
            if not joined(j):
                break
            c1 = cls_at(j + 1)[0]
            if c1 in (_U, _T, _D, _DU) and last in (_H, _S):
                last, j = _AND, j + 1                     # "one hundred and five"
                continue
            if (c1 == _A and joined(j + 1) and cls_at(j + 2)[0] == _HALF
                    and last in (_U, _T, _D, _DU, _S)):
                if last == _S:                            # "a million and a half"
                    total2 += last_scale or 0
                    j += 3
                    break
                group2 += 1                               # "two and a half (billion)"
                last, j = _HALF, j + 3
                continue
            break
        else:
            break
    if last == _AND:
        j -= 1                    # the length cap stopped right after "and": it is not part of it
    if needs_multiplier:
        # "a", "half a", "a half" never stand alone. The lead-in checked that a multiplier
        # follows, so this is unreachable today; it keeps a future edit from converting "a".
        return None
    return total2 + group2, j


def _halves_to_str(v2: int) -> str:
    whole, rem = divmod(v2, 2)
    return f"{whole}.5" if rem else str(whole)


def _halves_to_value(v2: int) -> Union[int, float]:
    return v2 // 2 if v2 % 2 == 0 else v2 / 2


def _spelled_spans(text: str) -> List[Tuple[Union[int, float], str, int, int]]:
    """Every well-formed spelled cardinal as `(value, phrase, start, end)`. Linear: the parser
    resumes where the previous phrase ended, and a word that cannot start a phrase is skipped
    after an O(1) look."""
    text = text or ""
    matches = list(_ALPHA_RE.finditer(text))
    words = [(m.group(0).lower(), m.start(), m.end()) for m in matches]
    adjacent = [
        k + 1 < len(words) and words[k + 1][1] == words[k][2] + 1 and text[words[k][2]] == " "
        for k in range(len(words))
    ]
    out: List[Tuple[Union[int, float], str, int, int]] = []
    i = 0
    n = len(words)
    while i < n:
        parsed = _parse_at(words, adjacent, i)
        if parsed is None:
            i += 1
            continue
        v2, j = parsed
        start, end = words[i][1], words[j - 1][2]
        out.append((_halves_to_value(v2), text[start:end], start, end))
        i = j
    return out


def spelled_numbers(text: str) -> List[Tuple[Union[int, float], str]]:
    """Every spelled-out cardinal phrase in `text` as `(value, phrase)`."""
    return [(v, p) for v, p, _s, _e in _spelled_spans(text)]


#: A spelled MULTIPLE ("twentyfold", "five-fold", "a hundredfold", "ten-bagger"): the head must
#: be a cardinal word, so "manifold", "scaffold", "unfold", "billfold" and "blindfold" are not.
_FOLD_WORD_RE = re.compile(r"(?<![A-Za-z-])([A-Za-z]+(?:-[A-Za-z]+)?)-?(fold|baggers?)(?![A-Za-z])",
                           re.IGNORECASE)
_FOLD_HEADS = (_U, _T, _D, _DU, _H, _S)


def _fold_repl(m: "re.Match[str]") -> str:
    cls, value = _classify(m.group(1).lower())
    if cls not in _FOLD_HEADS or value <= 0:
        return m.group(0)
    # "10x" and "10-bagger" are what the digit parser reads as a MULTIPLE (a strict unit).
    return f"{value}x" if m.group(2).lower() == "fold" else f"{value}-{m.group(2)}"


def words_to_digits(text: str) -> str:
    """Replace every spelled-out cardinal with its digits ("forty-seven billion" → "47000000000",
    "half a million" → "500000", "two and a half" → "2.5"), and a spelled multiple with its digit
    form ("twentyfold" → "20x", "ten-bagger" → "10-bagger") — content-B review, idx 9: the
    spelled form held a return multiple no validator ever saw.

    Used on SOURCE text so the fact sheet's numbers are extractable; the prompt itself still
    shows the source as written. Ordinals, fractions ("one-third") and compounds ("one-time")
    are left alone.
    """
    text = text or ""
    if "fold" in text.lower() or "bagger" in text.lower():
        text = _FOLD_WORD_RE.sub(_fold_repl, text)
    spans = _spelled_spans(text)
    if not spans:
        return text
    out: List[str] = []
    last = 0
    for value, _phrase, start, end in spans:
        # "one-time", "two-thirds": a hyphen straight after the phrase makes it a compound.
        if end < len(text) and text[end] == "-":
            continue
        if start > 0 and text[start - 1] == "-":
            continue
        out.append(text[last:start])
        out.append(_halves_to_str(int(round(value * 2))))
        last = end
    out.append(text[last:])
    return "".join(out)


# ── digit numbers ─────────────────────────────────────────────────────────────

#: Numbers that are part of a NAME, never a quantity. Blanked before extraction and handled
#: as entities by `grounding.py`.
#: Letters glued to digits that are a quantity suffix, not a name: 47B, 1.2k, 10x, 1990s,
#: 2nd. Everything else glued to digits (5G, 3D, B2B, Web3, Q3) is a name.
_QUANTITY_SUFFIX = r"(?i:k|m|b|t|bn|mn|x|s|st|nd|rd|th)\b"
NAME_NUMBER_RE = re.compile(
    r"S&P\s?500|401\s?\(k\)|\b10-[KQ]\b|\b13[FD]\b|\bForm\s[0-9]{1,3}\b|\b24/7\b"
    r"|\b[A-Za-z]{1,12}[0-9]{1,4}[A-Za-z]{0,4}\b"
    r"|\b[0-9]{1,4}(?!" + _QUANTITY_SUFFIX + r")[A-Za-z]{1,3}[0-9]{0,2}\b",
)

_CURRENCY_PREFIX = r"(?:US\$|\$|€|£)"
#: Every other currency symbol (Unicode category Sc: ¥ ₹ ₩ ₽ ₺ ₫ ₱ ₪ ฿ …), a dollar that is not
#: the US dollar ("NT$", "C$", "HK$"), or an ISO code. A number carrying one is FOREIGN_CURRENCY
#: — never PLAIN, which interchanges with anything (content-B review, idx 48: "¥50" grounded on
#: a source's unit-less 50, and "NT$40" on a source's "$40").
_OTHER_SC = "".join(ch for ch in map(chr, range(0x20, 0x3100))
                    if unicodedata.category(ch) == "Sc" and ch not in "$€£")
_FOREIGN_PREFIX = (r"(?-i:(?:NT|HK|NZ|MX|CA|AU|C|A|S|R)\$|(?:JPY|CNY|RMB|TWD|KRW|INR|CHF|CAD|AUD|"
                   r"HKD|SGD|BRL|MXN|RUB|ZAR|SEK|NOK|DKK|NZD)\s?)|[" + re.escape(_OTHER_SC) + r"]")
_FOREIGN_WORDS = (r"yen|yuan|renminbi|rupees?|francs?|pesos?|rubles?|roubles?|reais|kronor|krona|"
                  r"kroner|baht|ringgit|lira|liras|lire|shekels?|dinars?|dirhams?|riyals?")
#: A decimal may start with its point (".7%", "$.50") — but not after a letter, digit or a
#: blanked name-number ("v2.5"), which `extract_numbers` checks on the original text.
_NUM_CORE = r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+"
#: SOLIDUS, FRACTION SLASH (what NFKC makes of "¾"), DIVISION SLASH.
FRACTION_SLASHES = "/\u2044\u2215"
_DIGIT_RE = re.compile(
    r"(?:(?P<fcur>" + _FOREIGN_PREFIX + r")|(?P<cur>" + _CURRENCY_PREFIX + r"))?\s?"
    r"(?P<num>" + _NUM_CORE + r")"
    r"(?:\s?[" + FRACTION_SLASHES + r"]\s?(?P<den>[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+))?"
    r"(?P<decade>s\b)?"
    r"(?P<sfx>[kKmMbBtT](?![A-Za-z])|\s?(?:thousand|million|billion|trillion|bn|mn)\b)?"
    r"(?P<plus>\+)?"
    r"(?P<unit>\s?%|\s?percent\b|\s?per cent\b|\s?percentage points?\b|\s?bps\b"
    r"|\s?basis points?\b|x(?![A-Za-z])|\s?times\b|\s?-?\s?fold\b|\s?-?\s?baggers?\b"
    r"|\s?(?:dollars|USD|euros|pounds)\b|\s?(?:" + _FOREIGN_WORDS + r")\b)?",
    re.IGNORECASE,
)
#: Between the two bounds of a range ("15-20%", "15 to 20 percent", "$15-20").
_RANGE_GAP_RE = re.compile(r"\s?(?:-|\u2013|\u2014|to|and)\s?", re.IGNORECASE)
_SFX_SCALE = {
    "k": 10**3, "thousand": 10**3, "m": 10**6, "mn": 10**6, "million": 10**6,
    "b": 10**9, "bn": 10**9, "billion": 10**9, "t": 10**12, "trillion": 10**12,
}

#: A digit run longer than this is not a quantity anyone writes; it is parsed as +inf, which
#: `same_number` never matches, so it is always an ungrounded number — without handing float()
#: a model repetition loop.
_MAX_DIGITS = 40

PERCENT = "percent"
CURRENCY = "currency"
#: Any currency but the dollar, euro and pound ("¥40B", "40 billion yen", "NT$40").
FOREIGN_CURRENCY = "foreign_currency"
MULTIPLE = "multiple"
YEAR = "year"
PLAIN = "plain"
#: "3/4", "3⁄4" (NFKC of "¾"): ONE quantity, valued num/den. It used to parse as two unit-less
#: integers ≤ 5 — both exempt as "counts" — so "3/4 of profit" passed against a source's 60%.
FRACTION = "fraction"

#: Classes that must match exactly. PLAIN and YEAR are interchangeable ("in 2014" vs the
#: source's "2014 launch" — the same integer, told apart only by context).
STRICT_UNITS = frozenset({PERCENT, CURRENCY, MULTIPLE, FRACTION, FOREIGN_CURRENCY})


@dataclass(frozen=True)
class NumberMention:
    value: float
    unit: str
    raw: str
    start: int
    end: int
    decade: bool = False


def has_non_ascii_digit(text: str) -> bool:
    """True if `text` contains any numeric character outside 0-9 (fullwidth, Arabic-Indic,
    superscripts, vulgar fractions, circled numbers…). Those are rejected, not parsed."""
    for ch in text or "":
        if ch.isascii():
            continue
        if unicodedata.numeric(ch, None) is not None:
            return True
    return False


def _blank(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def _parse_float(raw_num: str) -> float:
    digits = raw_num.replace(",", "")
    if len(digits) > _MAX_DIGITS:
        return math.inf
    try:
        return float(digits)
    except ValueError:
        return math.nan


def extract_numbers(text: str) -> List[NumberMention]:
    """Every digit number in `text` as a `NumberMention` (spelled words are NOT converted
    here — call `words_to_digits` first when that is wanted). Name-numbers are skipped. An
    absurdly long digit run is kept (it is still a number the text states) with value +inf."""
    text = text or ""
    src = _blank(text, NAME_NUMBER_RE)
    out: List[NumberMention] = []
    for m in _DIGIT_RE.finditer(src):
        raw_num = m.group("num")
        if raw_num.startswith(".") and not m.group("cur") and not m.group("fcur"):
            before = text[m.start("num") - 1:m.start("num")]
            if before and (before.isalnum() or before == "."):
                continue          # "v2.5" (a blanked name-number), "end.5", "3..5"
        value = _parse_float(raw_num)
        if math.isnan(value):
            continue
        den_raw = m.group("den")
        if den_raw is not None:
            den = _parse_float(den_raw)
            if math.isnan(den):
                continue
            # 1/0 or an overflowing side: a number no fact can equal (inf never matches).
            value = value / den if den and math.isfinite(den) and math.isfinite(value) \
                else math.inf
        sfx = (m.group("sfx") or "").strip().lower()
        if sfx:
            value *= _SFX_SCALE.get(sfx, 1)
        unit_raw = re.sub(r"[\s-]+", " ", (m.group("unit") or "")).strip().lower()
        cur = m.group("cur")
        decade = bool(m.group("decade"))
        if unit_raw in ("%", "percent", "per cent") or unit_raw.startswith("percentage point"):
            unit = PERCENT
        elif unit_raw in ("bps", "basis point", "basis points"):
            # 50 basis points ARE 0.5%: compared as a percent, never as a plain 50.
            unit, value = PERCENT, value / 100
        elif unit_raw in ("x", "times", "fold", "bagger", "baggers"):
            unit = MULTIPLE
        elif m.group("fcur") or (unit_raw and re.fullmatch(_FOREIGN_WORDS, unit_raw)):
            unit = FOREIGN_CURRENCY
        elif cur or unit_raw in ("dollars", "usd", "euros", "pounds"):
            unit = CURRENCY
        elif den_raw is not None and not sfx:
            unit = FRACTION
        elif decade or (
            "," not in raw_num and "." not in raw_num and not sfx
            and len(raw_num) == 4 and 1800 <= int(raw_num) <= 2100
        ):
            unit = YEAR
        else:
            unit = PLAIN
        out.append(NumberMention(
            value=value, unit=unit, raw=m.group(0).strip(), start=m.start(), end=m.end(),
            decade=decade,
        ))
    return _range_units(out, src)


_SUFFIX_UNITS = frozenset({PERCENT, MULTIPLE})
_PREFIX_UNITS = frozenset({CURRENCY, FOREIGN_CURRENCY})


def _range_units(out: List[NumberMention], src: str) -> List[NumberMention]:
    """A range's bounds share one unit: "15-20%" and "15 to 20 percent" are two percentages, not a
    unit-less 15 (which interchanges with anything) and a 20%; "$15-20" is two dollar amounts.
    Only a unit that is actually written is copied, and a year never takes one ("2010-2015")."""
    for i in range(len(out) - 1):
        a, b = out[i], out[i + 1]
        if not _RANGE_GAP_RE.fullmatch(src[a.end:b.start]):
            continue
        if a.unit == PLAIN and not a.decade and b.unit in _SUFFIX_UNITS:
            value = a.value / 100 if b.raw.lower().endswith(("bps", "point", "points")) \
                and "percentage" not in b.raw.lower() else a.value
            out[i] = NumberMention(value=value, unit=b.unit, raw=a.raw, start=a.start,
                                   end=a.end, decade=False)
        elif b.unit == PLAIN and not b.decade and a.unit in _PREFIX_UNITS:
            out[i + 1] = NumberMention(value=b.value, unit=a.unit, raw=b.raw, start=b.start,
                                       end=b.end, decade=False)
    return out


def same_number(a: NumberMention, b: NumberMention) -> bool:
    """Same value, compatible unit class. A non-finite value (an overflowing digit run) never
    equals anything — two different 400-digit numbers are not "the same" because both are inf."""
    if not (math.isfinite(a.value) and math.isfinite(b.value)):
        return False
    if a.unit in STRICT_UNITS or b.unit in STRICT_UNITS:
        if a.unit != b.unit:
            return False
    if a.value == b.value:
        return True
    scale = max(abs(a.value), abs(b.value), 1.0)
    return abs(a.value - b.value) / scale < 1e-9
