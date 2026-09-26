"""
Word timings for the narration (Phase 3, SYSTEM_DESIGN_GUIDELINES §12.7). PURE: stdlib only, no
torch, no app.* — the worker package's rule (rules marketing.md §2), and so tests need no model.

The words a caption shows are the SCRIPT's own words — a whitespace split of each narrated line
— never the TTS engine's. Kokoro's G2P (misaki) tokenises differently: punctuation is its own
token, "$" comes back with no timestamp (it is spoken after the number), a digit token such as
"2019" or "3%" carries the duration of its spoken expansion. So engine tokens only SUPPLY TIMES:
they are grouped on their `whitespace` flag (a token followed by a space ends a display word),
and each group's first known start / last known end become that word's window. When the groups
do not reconstruct the line's words exactly, the line falls back to proportional timing by
character length inside the span the engine did time, instead of guessing an alignment.

Measured on kokoro 0.9.4 / misaki 0.9.4 (2026-09-26 spike): "$7.5" → "$"(no time) + "7.5";
"“It’s" → "“" + "It’s"; "snapshot,”" → "snapshot" + "," + "”"; "—" is a word of its own;
"U.S.", "S&P", "10-nanometer", "Intel's", "3%" are single tokens.

The output table is what the audio asset carries in `metadata.words` and what the server checks
against the accepted script (`run_service._check_timed_words`): one entry per display word,
`{"w", "s", "e", "line"}`, starts non-decreasing, every word at least MIN_WORD_SECONDS long.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence

#: Shortest window a word may get (a 40 ms word cannot be read on screen). Neighbours give way.
MIN_WORD_SECONDS = 0.12
#: Silence inserted between narrated lines (the hook and each script line).
LINE_PAUSE_SECONDS = 0.28
SAMPLE_RATE = 24000


@dataclass(frozen=True)
class Token:
    """One engine token, times relative to its own line's audio (seconds, or None)."""
    text: str
    whitespace: str
    start: Optional[float]
    end: Optional[float]


@dataclass
class Word:
    w: str
    s: float
    e: float
    line: int

    def as_dict(self) -> dict:
        # enforce_order leaves s and e on whole milliseconds, so this rounding is exact.
        return {"w": self.w, "s": round(self.s, 3), "e": round(self.e, 3), "line": self.line}


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


def assemble(lines: Sequence[str], per_line: Sequence[tuple]) -> List[Word]:
    """Place each line's words on the narration timeline: line i starts after the audio of
    lines 0..i-1 plus LINE_PAUSE_SECONDS each. `per_line[i]` = (timed words, line duration)."""
    out: List[Word] = []
    offset = 0.0
    for i, (line, (timed, duration)) in enumerate(zip(lines, per_line)):
        for w, s, e in timed:
            out.append(Word(w, offset + max(s, 0.0), offset + max(e, 0.0), i))
        offset += duration + LINE_PAUSE_SECONDS
    return enforce_order(out, total=max(offset - LINE_PAUSE_SECONDS, 0.0))


def enforce_order(words: List[Word], *, total: float) -> List[Word]:
    """ONE forward pass in whole MILLISECONDS (the table's resolution), so rounding can never
    collapse a window or make a start precede the previous end (a float 1 ms sliver rounded to
    3 decimals used to land on s == e about 6% of the time, and the server refuses such a table
    on every retry). Each start is at least the previous end; a word shorter than
    MIN_WORD_SECONDS extends into the gap before the next word's start (never past it — that
    start is the engine's time); with no gap at all it keeps 1 ms and the next word starts after
    it. Always monotonic with every s < e."""
    min_ms = int(round(MIN_WORD_SECONDS * 1000))
    total_ms = int(round(max(total, 0.0) * 1000))
    starts = [int(round(max(w.s, 0.0) * 1000)) for w in words]
    ends = [int(round(max(w.e, 0.0) * 1000)) for w in words]
    prev_end = 0
    for i, w in enumerate(words):
        s_ms = max(starts[i], prev_end)
        e_ms = max(ends[i], s_ms)
        if e_ms - s_ms < min_ms:
            nxt = starts[i + 1] if i + 1 < len(words) else max(total_ms, s_ms + min_ms)
            e_ms = max(e_ms, min(s_ms + min_ms, nxt))
        if e_ms <= s_ms:
            e_ms = s_ms + 1
        w.s, w.e = s_ms / 1000.0, e_ms / 1000.0
        prev_end = e_ms
    return words


def narrated_lines(script: dict) -> List[str]:
    """What is spoken, in order: the hook, then every script line (blank ones skipped) — the
    same order `run_service.narration_words` checks against."""
    out: List[str] = []
    hook = str(script.get("hook") or "").strip()
    if hook:
        out.append(hook)
    out += [str(x).strip() for x in (script.get("video_script") or []) if str(x).strip()]
    return out


def as_table(words: Iterable[Word]) -> List[dict]:
    return [w.as_dict() for w in words]


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
