"""
learn_narrator/timings.py — the read-along table the worker sends with `/jobs/{id}/complete`.

PURE: stdlib only (no torch, no numpy, no app.*). The parent process builds the table here from
what the children measured (aligner word spans for Chatterbox, Kokoro's own word timestamps), so
it — and the tests — never load a model.

Four parts:

1. **The server's sentence split.** Sentence texts must EQUAL the server's split of each block, or
   `complete` is refused. `strip_markup`, `_SENT` and `split_sentences` are copies of
   `scripts/_forced_align.py` (which the web side copies too), pinned by an AST test — never
   imported: the scripts tree imports torch at module top and is not in the image.
2. **The spoken form of a word** (`spoken_words_per_word`). The aligner's alphabet is A-Z and the
   apostrophe, and the per-block words/s gate must count what the voice SAYS: "$574.8 billion" is
   two whitespace words but eight spoken ones. Digits, money, percentages, years, ordinals and a
   few symbols are expanded; anything else keeps its letters.
3. **Kokoro's native timestamps** — `Token` … `tokens_from_engine` are vendored VERBATIM from
   `marketing/timings.py` (AST-pinned equal; the marketing worker cannot be imported from this
   image, and its grouping rules were measured on the same kokoro 0.9.4 / misaki 0.9.4).
4. **The table and its self-check.** `build_block` turns one block's word spans into sentence spans;
   `check_table` mirrors the server's `validate_timings` (same bands, same order rules) so a table
   the server would refuse becomes a typed worker failure (`timings_invalid`) with the reason, not
   a 422 after the upload.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

# ── 1. the server's sentence split (copied from scripts/_forced_align.py, AST-pinned) ─────────


def strip_markup(s: str) -> str:
    """Remove the **bold** markers — matches the text the TTS spoke and iOS displays."""
    return re.sub(r"\*\*", "", s or "")


# Split on whitespace that follows a sentence terminator (. ! ?), optionally followed by a closing
# quote — the quote stays in the LEFT sentence (it's in the lookbehind, only \s+ is consumed) so the
# span text rejoins to the original. Same splitter as gen_book_read_along.split_sentences.
_SENT = re.compile(r'(?:(?<=[.!?])|(?<=[.!?]["”’]))\s+(?=["“‘A-Z0-9])')


def split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [s.strip() for s in _SENT.split(text) if s.strip()]


def sentence_split(text: str) -> List[str]:
    """The server's split of one narration block: `split_sentences(strip_markup(text))`."""
    return split_sentences(strip_markup(text))


def sentence_word_ranges(text: str) -> List[Tuple[str, int, int]]:
    """[(sentence text, first word, end word)] over `strip_markup(text).split()`. The split consumes
    only whitespace, so the sentences' words concatenate back to the block's words exactly."""
    words = strip_markup(text).split()
    out: List[Tuple[str, int, int]] = []
    k = 0
    for sent in sentence_split(text):
        n = len(sent.split())
        out.append((sent, k, k + n))
        k += n
    if k != len(words):
        raise ValueError(f"sentence split lost words ({k} of {len(words)}) in {text[:60]!r}")
    return out


# ── the server's timing gate, mirrored (app/services/learn_weekly/narration.py, AST-pinned) ───

#: Seconds per spoken word, per SENTENCE, in the final audio. Below the floor = a dropped sentence.
SPW_BAND: Tuple[float, float] = (0.15, 1.2)
DURATION_SLACK_SECONDS = 0.5
MAX_SILENCE_SECONDS = 6.0
ORDER_EPSILON_SECONDS = 0.02
_SPOKEN_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?")


def server_spoken_words(text: str) -> int:
    """The SERVER's word count for the seconds-per-word band (letter/digit runs, never 0)."""
    return max(1, len(_SPOKEN_WORD.findall(text or "")))


# ── 2. the spoken form ────────────────────────────────────────────────────────────────────────

_ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
         "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_SCALES = ((10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand"))
_SCALE_WORDS = {"thousand", "million", "billion", "trillion"}
_SUFFIX_SCALE = {"k": "thousand", "m": "million", "b": "billion", "bn": "billion", "t": "trillion",
                 "tn": "trillion"}
_ORDINAL = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
            "nine": "ninth", "twelve": "twelfth"}
_SYMBOLS = {"&": "and", "%": "percent", "+": "plus", "@": "at", "=": "equals"}
#: Longest digit run read as a number; a longer one is read digit by digit.
_MAX_NUMBER_DIGITS = 15
_PIECE = re.compile(
    r"(?P<money>[$€£])?(?P<num>\d+(?:,\d{3})*(?:\.\d+)?)(?P<suffix>%|st|nd|rd|th|s|x|bn|tn|[kKmMbBtT])?(?![A-Za-z])"
    r"|(?P<alpha>[A-Za-z]+(?:'[A-Za-z]+)*)"
    r"|(?P<sym>[&%+@=])"
    r"|(?P<digits>\d+)"
)
_CURRENCY = {"$": "dollars", "€": "euros", "£": "pounds"}


def int_words(n: int) -> List[str]:
    """Cardinal words of a non-negative integer ("one hundred twenty three")."""
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10]] + ([_ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        return [_ONES[n // 100], "hundred"] + (int_words(n % 100) if n % 100 else [])
    for scale, name in _SCALES:
        if n >= scale:
            head, rest = divmod(n, scale)
            return int_words(head) + [name] + (int_words(rest) if rest else [])
    raise AssertionError("unreachable")


def year_words(n: int) -> Optional[List[str]]:
    """A four-digit year read as English says it ("twenty twenty three", "nineteen oh five",
    "two thousand eight"), or None when `n` is not read as a year."""
    if not 1100 <= n <= 2099:
        return None
    if 2000 <= n <= 2009:
        return ["two", "thousand"] + ([_ONES[n - 2000]] if n > 2000 else [])
    hi, lo = divmod(n, 100)
    if lo == 0:
        return int_words(hi) + ["hundred"]
    if lo < 10:
        return int_words(hi) + ["oh", _ONES[lo]]
    return int_words(hi) + int_words(lo)


def _ordinal(words: List[str]) -> List[str]:
    last = words[-1]
    if last in _ORDINAL:
        new = _ORDINAL[last]
    elif last.endswith("y"):
        new = last[:-1] + "ieth"
    else:
        new = last + "th"
    return words[:-1] + [new]


def _plural(words: List[str]) -> List[str]:
    last = words[-1]
    return words[:-1] + [last[:-1] + "ies" if last.endswith("y") else last + "s"]


def number_words(raw: str, *, suffix: str = "", money: bool = False) -> List[str]:
    """Spoken words of one numeric token (commas, decimals, years, ordinals, plural years)."""
    digits = raw.replace(",", "")
    whole, _, frac = digits.partition(".")
    if len(whole) > _MAX_NUMBER_DIGITS:
        return [_ONES[int(c)] for c in digits if c.isdigit()]
    n = int(whole) if whole else 0
    plain_year = (not money and not frac and "," not in raw and len(whole) == 4
                  and suffix in ("", "s"))
    words = (year_words(n) if plain_year else None) or int_words(n)
    if frac:
        words = words + ["point"] + [_ONES[int(c)] for c in frac]
    s = suffix.lower()
    if s in ("st", "nd", "rd", "th") and not frac:
        words = _ordinal(words)
    elif s == "s" and not frac:
        words = _plural(words)
    elif s == "%":
        words = words + ["percent"]
    elif s == "x":
        words = words + ["times"]
    return words


def spoken_words_per_word(words: Sequence[str]) -> List[List[str]]:
    """For each whitespace word of a block, the words the voice says for it (lowercase). A
    currency amount followed by a scale word ("$574.8 billion") says its currency AFTER the scale
    word, so the currency joins that next word — the order the aligner must expect."""
    out: List[List[str]] = [[] for _ in words]
    for wi, word in enumerate(words):
        text = word.replace("’", "'").replace("‘", "'")
        for m in _PIECE.finditer(text):
            if m.group("num") is not None:
                suffix = m.group("suffix") or ""
                # "5M", "$2.1bn", "10k": the suffix is a scale word ("five million").
                scale = _SUFFIX_SCALE.get(suffix.lower())
                num_suffix = "" if scale else suffix
                spoken = number_words(m.group("num"), suffix=num_suffix, money=bool(m.group("money")))
                if scale:
                    spoken = spoken + [scale]
                out[wi] += spoken
                currency = m.group("money")
                if currency:
                    unit = _CURRENCY[currency]
                    if spoken == ["one"]:
                        unit = unit[:-1]
                    nxt = words[wi + 1].lower().strip(".,;:!?)”’\"'") if wi + 1 < len(words) else ""
                    if not scale and nxt in _SCALE_WORDS:
                        out[wi + 1].append(unit)      # "$574.8 billion" → "… billion dollars"
                    else:
                        out[wi].append(unit)
            elif m.group("alpha") is not None:
                out[wi].append(m.group("alpha").lower())
            elif m.group("sym") is not None:
                out[wi].append(_SYMBOLS[m.group("sym")])
            elif m.group("digits") is not None:
                out[wi] += [_ONES[int(c)] for c in m.group("digits")]
    # A currency unit deferred onto the next word goes AFTER that word's own words.
    for wi in range(1, len(words)):
        if out[wi] and out[wi][0] in ("dollars", "dollar", "euros", "euro", "pounds", "pound") \
                and words[wi].lower().strip(".,;:!?)”’\"'") in _SCALE_WORDS:
            out[wi] = out[wi][1:] + [out[wi][0]]
    return out


def spoken_word_count(text: str) -> int:
    """Words the voice says in a block (the per-block words/s gate's numerator)."""
    return sum(len(x) for x in spoken_words_per_word(strip_markup(text).split()))


# ── 3. Kokoro's native timestamps (vendored VERBATIM from marketing/timings.py, AST-pinned) ───

#: Shortest window a word may get (a 40 ms word cannot be read on screen). Neighbours give way.
MIN_WORD_SECONDS = 0.12


@dataclass(frozen=True)
class Token:
    """One engine token, times relative to its own line's audio (seconds, or None)."""
    text: str
    whitespace: str
    start: Optional[float]
    end: Optional[float]


_PUNCT_ONLY = "\"'“”‘’()[]{}.,;:!?…—–-•*|/=> "


def _norm(text: str) -> str:
    return "".join(text.split())


def group_tokens(tokens: Sequence[Token]) -> List[tuple]:
    """[(text, start, end)] — tokens joined until one carries whitespace after it. start/end are
    the first/last KNOWN times in the group (None when the group has none)."""
    groups: List[tuple] = []
    text, start, end = "", None, None
    for i, t in enumerate(tokens):
        text += t.text or ""
        if t.start is not None and start is None:
            start = t.start
        if t.end is not None:
            end = t.end
        if (t.whitespace or "") != "" or i == len(tokens) - 1:
            if text.strip():
                groups.append((text, start, end))
            text, start, end = "", None, None
    return groups


def proportional(words: Sequence[str], start: float, end: float) -> List[tuple]:
    """Spread `words` over [start, end] by character length (a word's share of the line)."""
    if not words:
        return []
    end = max(end, start + MIN_WORD_SECONDS * len(words))
    weights = [max(len(w), 1) for w in words]
    total = float(sum(weights))
    out, t = [], start
    for w, k in zip(words, weights):
        d = (end - start) * k / total
        out.append((w, t, t + d))
        t += d
    return out


def align_line(line: str, tokens: Sequence[Token], duration: float) -> tuple:
    """(timed words [(w, s, e)], exact: bool) for one line; times relative to the line's audio.
    Exact = the engine's token groups reconstruct the line's whitespace words one-to-one."""
    words = line.split()
    if not words:
        return [], True
    groups = group_tokens(tokens)
    known = [t for t in tokens if t.start is not None or t.end is not None]
    span_start = min((t.start for t in tokens if t.start is not None), default=0.0)
    span_end = max((t.end for t in tokens if t.end is not None), default=duration)
    # Kokoro 0.9.4 can leave a line's LAST words untimed (a phoneme-less token such as " - "
    # advances its duration index twice, so it runs out early): the audio still runs to the end
    # of the line, so a trailing untimed word ends at the line's duration, not at the last
    # timed token (which would squeeze it to MIN_WORD_SECONDS while the voice keeps speaking).
    worded = [t for t in tokens if (t.text or "").strip(_PUNCT_ONLY)]
    if worded and worded[-1].start is None and worded[-1].end is None:
        span_end = max(span_end, duration)
    if not known:
        return proportional(words, 0.0, max(duration, 0.0)), False
    if len(groups) == len(words) and all(_norm(g[0]) == _norm(w) for g, w in zip(groups, words)):
        starts = [g[1] for g in groups]
        ends = [g[2] for g in groups]
        i = 0
        while i < len(words):
            if starts[i] is not None and ends[i] is not None:
                i += 1
                continue
            # A RUN of untimed groups ("$" alone, a stray dash, a trailing phrase Kokoro left
            # untimed): share the gap between its timed neighbours by word length — a
            # punctuation-only word ("—") weighs little.
            j = i
            while j < len(words) and (starts[j] is None or ends[j] is None):
                j += 1
            left = ends[i - 1] if i > 0 and ends[i - 1] is not None else (starts[i] if starts[i] is not None else span_start)
            right = starts[j] if j < len(words) and starts[j] is not None else max(span_end, duration)
            right = max(right, left)
            weights = [max(len(words[k].strip(_PUNCT_ONLY)), 0) or 0.3 for k in range(i, j)]
            total = sum(weights)
            t = left
            for k, wgt in zip(range(i, j), weights):
                d = (right - left) * wgt / total
                starts[k] = t if starts[k] is None else starts[k]
                ends[k] = t + d if ends[k] is None else ends[k]
                t += d
            i = j
        return [(w, s0, e0) for w, s0, e0 in zip(words, starts, ends)], True
    return proportional(words, span_start, span_end), False


def tokens_from_engine(raw_tokens: Iterable[Any], offset: float = 0.0) -> List[Token]:
    """Engine token objects (misaki MToken: text, whitespace, start_ts, end_ts) → Token, with a
    chunk offset added (Kokoro times are relative to their chunk)."""
    out = []
    for t in raw_tokens or []:
        s, e = getattr(t, "start_ts", None), getattr(t, "end_ts", None)
        out.append(Token(str(getattr(t, "text", "") or ""), str(getattr(t, "whitespace", "") or ""),
                         None if s is None else float(s) + offset,
                         None if e is None else float(e) + offset))
    return out


# ── 4. the table ──────────────────────────────────────────────────────────────────────────────

Span = Optional[Tuple[float, float]]


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def build_block(i: int, text: str, word_spans: Sequence[Span], *, window: Tuple[float, float]) -> Tuple[dict, dict]:
    """(table entry, stats) for one block.

    `word_spans[k]` is the absolute (start, end) of the block's k-th whitespace word, or None when
    nothing timed it (a word with no letters, e.g. "—"). A sentence takes the earliest start and the
    latest end of its timed words. A sentence with NO timed word shares, by spoken length, the gap
    between its timed neighbours (or the block's `window` edge) — it is counted as uncovered in the
    stats, so a block that leans on that fallback is visible in `checks`. Times are forced onto a
    monotonic millisecond grid (each start ≥ the previous end, each end ≥ its start)."""
    ranges = sentence_word_ranges(text)
    words = strip_markup(text).split()
    if len(word_spans) != len(words):
        raise ValueError(f"block {i}: {len(word_spans)} word spans for {len(words)} words")
    lo, hi = float(window[0]), float(window[1])
    known: List[Optional[Tuple[float, float]]] = []
    for _sent, a, b in ranges:
        spans = [s for s in word_spans[a:b] if s is not None and _finite(s[0]) and _finite(s[1])]
        if spans:
            known.append((min(float(s[0]) for s in spans), max(float(s[1]) for s in spans)))
        else:
            known.append(None)
    covered = sum(1 for k in known if k is not None)
    # Fill uncovered runs between timed neighbours, by the server's word count.
    filled: List[Tuple[float, float]] = []
    k = 0
    while k < len(ranges):
        if known[k] is not None:
            filled.append(known[k])  # type: ignore[arg-type]
            k += 1
            continue
        j = k
        while j < len(ranges) and known[j] is None:
            j += 1
        left = filled[-1][1] if filled else lo
        right = known[j][0] if j < len(ranges) else hi  # type: ignore[index]
        right = max(right, left)
        weights = [server_spoken_words(ranges[m][0]) for m in range(k, j)]
        total = float(sum(weights))
        t = left
        for wgt in weights:
            d = (right - left) * wgt / total
            filled.append((t, t + d))
            t += d
        k = j
    sentences = []
    prev = 0
    for (sent, _a, _b), (s, e) in zip(ranges, filled):
        s_ms = max(int(round(max(s, 0.0) * 1000)), prev)
        e_ms = max(int(round(max(e, 0.0) * 1000)), s_ms)
        sentences.append({"text": sent, "start": s_ms / 1000.0, "end": e_ms / 1000.0})
        prev = e_ms
    entry = {"i": i, "start": sentences[0]["start"] if sentences else round(lo, 3),
             "end": sentences[-1]["end"] if sentences else round(lo, 3), "sentences": sentences}
    stats = {"sentences": len(ranges), "covered": covered,
             "align_coverage": round(covered / len(ranges), 4) if ranges else 1.0,
             "words": len(words), "timed_words": sum(1 for s in word_spans if s is not None)}
    return entry, stats


def enforce_table_order(table: List[dict]) -> List[dict]:
    """One forward pass across blocks in whole milliseconds: a block (and its sentences) never
    starts before the previous block ended. Neighbouring windows can overlap by a hair after the
    raw-offset ÷ atempo maths; the server tolerates ORDER_EPSILON_SECONDS, this leaves none."""
    prev = 0
    for entry in table:
        for sent in entry["sentences"]:
            s_ms = max(int(round(sent["start"] * 1000)), prev)
            e_ms = max(int(round(sent["end"] * 1000)), s_ms)
            sent["start"], sent["end"] = s_ms / 1000.0, e_ms / 1000.0
            prev = e_ms
        if entry["sentences"]:
            entry["start"] = entry["sentences"][0]["start"]
            entry["end"] = entry["sentences"][-1]["end"]
        else:
            entry["start"] = entry["end"] = prev / 1000.0
    return table


def check_table(blocks: Sequence[Mapping[str, Any]], table: Sequence[Mapping[str, Any]],
                duration_seconds: Optional[float]) -> List[str]:
    """The server's `validate_timings`, as a list of problems (empty = the server would accept).
    `blocks` are the job's `{i, text}` blocks."""
    problems: List[str] = []
    if len(table) != len(blocks):
        return [f"{len(table)} timing entries for {len(blocks)} blocks"]
    limit = None if duration_seconds is None else float(duration_seconds) + DURATION_SLACK_SECONDS
    prev_end = 0.0
    spoken_until = 0.0
    for pos, (block, entry) in enumerate(zip(blocks, table)):
        if entry.get("i") != pos or block.get("i") != pos:
            return [f"entry {pos} is block {entry.get('i')} — entries must be in block order"]
        start, end = entry.get("start"), entry.get("end")
        if not (_finite(start) and _finite(end)) or start < 0 or end < 0:
            return [f"block {pos} has a non-finite or negative time"]
        if start > end:
            problems.append(f"block {pos} starts after it ends ({start} > {end})")
        if start + ORDER_EPSILON_SECONDS < prev_end:
            problems.append(f"block {pos} starts at {start:.2f}s before block {pos - 1} ends ({prev_end:.2f}s)")
        if limit is not None and end > limit:
            problems.append(f"block {pos} ends at {end:.2f}s, past the audio's {duration_seconds:.2f}s")
        expected = sentence_split(str(block.get("text") or ""))
        sents = list(entry.get("sentences") or [])
        if len(sents) != len(expected):
            problems.append(f"block {pos} has {len(sents)} sentence(s); the server's split has {len(expected)}")
            prev_end = end
            continue
        s_prev = start
        for k, (sent, text) in enumerate(zip(sents, expected)):
            where = f"block {pos} sentence {k}"
            if sent.get("text") != text:
                problems.append(f"{where} text is not the server's split")
                continue
            s0, s1 = sent.get("start"), sent.get("end")
            if not (_finite(s0) and _finite(s1)) or s0 < 0 or s1 < 0:
                problems.append(f"{where} has a non-finite or negative time")
                continue
            if s0 > s1:
                problems.append(f"{where} starts after it ends")
            if s0 + ORDER_EPSILON_SECONDS < s_prev:
                problems.append(f"{where} starts before the previous one ends")
            if s1 > end + ORDER_EPSILON_SECONDS:
                problems.append(f"{where} ends after its block ends")
            words = server_spoken_words(text)
            spw = (s1 - s0) / words
            lo, hi = SPW_BAND
            if not lo <= spw <= hi:
                problems.append(f"{where} spans {s1 - s0:.2f}s for {words} word(s) ({spw:.3f} s/word, "
                                f"band {lo}-{hi}) — " + ("dropped or truncated" if spw < lo else "stretched"))
            if s0 - spoken_until > MAX_SILENCE_SECONDS:
                problems.append(f"{s0 - spoken_until:.1f}s of audio before {where} is covered by no sentence")
            s_prev = s1
            spoken_until = s1
        prev_end = end
    if duration_seconds is not None and float(duration_seconds) - spoken_until > MAX_SILENCE_SECONDS:
        problems.append(f"the audio runs {float(duration_seconds) - spoken_until:.1f}s past the last sentence")
    return problems
