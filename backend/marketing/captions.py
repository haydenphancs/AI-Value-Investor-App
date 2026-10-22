"""
Word-timed captions as an ASS subtitle file for libass (Phase 3 builds it, Phase 4 burns it;
SYSTEM_DESIGN_GUIDELINES §12.7). PURE apart from the optional font measurement (Pillow and
fontTools, imported inside functions): no app.*, no torch.

Layout — one line of 3-5 words in the lower middle of a 1080×1920 frame, the word being spoken
drawn in the brand colour, the others in white, all with a dark outline:

* Phrases never cross a narrated line and end early at a sentence or clause mark once they have
  a few words; a phrase wider than the safe width is split, and a single word wider than it is
  shrunk horizontally (`\\fscx`) rather than wrapped (WrapStyle 2 = never wrap).
* A split never strands a figure from the word it is read with (LIVE-CAP-1, 2026-10-20): "Amazon
  invested $50 | billion" showed "$50" alone through the 500 ms platform cover of a company_stakes
  video. A figure ("$50", "12.4", "10%") whose next word is a scale word (thousand, million,
  billion, trillion, percent) or "a share" / "per share" is carried into the next phrase, like a
  trailing function word ("…offers a | price") — even when that leaves the phrase it ends wider
  than the safe width, which is then shrunk like a single long word.
* ONE Dialogue event per word window: each event draws the whole phrase with the active word
  coloured, and ends exactly when the next begins, so the text never flickers. Colour only —
  scaling the active word would shift a centred line on every word.
* Colours are ASS `&HAABBGGRR`: BGR order, and alpha is INVERTED (00 = opaque).
* The text is model-written. Braces and backslashes are ASS override syntax (`{\\p1}` draws
  shapes, `\\N` breaks lines), so they are neutralised here even though the server's markup rule
  already refuses them — defence in depth, since a caption file is where they would execute.
* A HOOK card's words are not captioned twice (Batch 2, 2026-10-19): while a lesson video opens on
  its hook card — the accepted hook drawn big on card 0 — the narration's hook line is not burned
  under it as well (`without_hook_card_words`). Only the words that card really shows, and only
  while it is on screen.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("marketing.captions")

PLAY_RES = (1080, 1920)
FONT_NAME = "Inter"                   # the family name inside Inter-Bold.ttf
#: The style's Fontsize. libass (like VSFilter) sizes a face so that usWinAscent+usWinDescent =
#: Fontsize, and Inter's are 1.21 em — so 96 draws an em of ~79 px. Every width is MEASURED at
#: that em (`em_px`), never at Fontsize, or the layout maths is ~21% off.
FONT_SIZE = 96
#: Inter 4.1 Bold: unitsPerEm 2048, usWinAscent 1984, usWinDescent 494 (read from the vendored
#: file by `font_em_ratio`; this is the fallback when fontTools cannot read it).
_INTER_EM_RATIO = 2048 / (1984 + 494)
OUTLINE = 6
SAFE_WIDTH = 920                      # px at PlayRes: 80 px margins each side
POS = (540, 1240)                     # \an5 anchor: centre, a little below the middle
MIN_PHRASE_WORDS, MAX_PHRASE_WORDS = 2, 5
MIN_EVENT_SECONDS = 0.12
#: How long a phrase lingers after its last word when nothing follows at once.
PHRASE_TAIL_SECONDS = 0.35

WHITE = "&H00FFFFFF"                  # style colours: &HAABBGGRR
#: In-line `\c` overrides take 6-digit `&HBBGGRR&` (alpha lives in `\1a`, never in `\c`).
WHITE_C = "&HFFFFFF&"
HIGHLIGHT_C = "&HFAA560&"             # #60A5FA (brand primary text) in BGR
OUTLINE_COLOUR = "&H00261B17"         # #171B26 (page) in BGR
SHADOW_COLOUR = "&H96000000"          # 0x96 alpha = ~41% opaque black

#: The narration line that is the hook (timings.narrated_lines: the hook first, when there is one).
HOOK_LINE = 0

_CLAUSE_END = re.compile(r"[.!?;:—–,]$")
_SENTENCE_END = re.compile(r"[.!?]$")
_OVERRIDE_CHARS = str.maketrans({"{": "(", "}": ")", "\\": "/"})
#: A dot that ends an abbreviation ends no phrase ("Meet Mr. Market" stays together).
#: Only abbreviations that almost never END a sentence: "No.", "etc.", "Co." and "p.m." often do
#: (a debunk frame answers "No."), so they end a phrase like any full stop.
_ABBREVIATIONS = frozenset({
    "mr.", "mrs.", "ms.", "dr.", "st.", "jr.", "sr.", "u.s.", "u.k.", "e.g.", "i.e.", "vs.",
    "inc.", "corp.", "ltd.", "approx.",
})
_CLOSERS = "\"'”’)]"
_QUOTES = "\"'“”‘’()"
_FUNCTION_WORDS = frozenset({
    "a", "an", "the", "of", "to", "and", "or", "but", "for", "in", "on", "at", "by", "with", "from",
    "as", "his", "her", "your", "its", "their", "our", "my", "this", "that", "is", "are", "was",
})
#: A FIGURE as narrated ("$50", "12.4", "1,000,000", "10%", "€3"): it ends in a digit or "%", so a
#: figure that ends a clause ("$50,") is not one — the clause mark ends that phrase on purpose.
_FIGURE_RE = re.compile(r"[$€£]?[0-9](?:[0-9.,]*[0-9])?%?\Z")
#: …and what it is read with (LIVE-CAP-1): ONE scale word ("$50 billion", "12 percent"; a hyphenated
#: "billion-dollar" reads as its first part), or a pair ("$4.10 a share", "per share").
_SCALE_WORDS = frozenset({"thousand", "million", "billion", "trillion", "percent"})
_SCALE_PAIRS = frozenset({("a", "share"), ("per", "share")})
_TRAILING_MARKS = ".,;:!?…—–" + _CLOSERS


def _ends(pattern: "re.Pattern[str]", word: str) -> bool:
    """Does `word` end a sentence/clause? Closing quotes and brackets after the mark count
    ('luck.”', 'acts.)'); an abbreviation's own dot does not."""
    core = word.rstrip(_CLOSERS)
    return bool(pattern.search(core)) and core.lower().lstrip(_QUOTES) not in _ABBREVIATIONS


@dataclass(frozen=True)
class Event:
    start: float
    end: float
    text: str


def sanitize(text: str) -> str:
    """Model text → safe ASS dialogue text: no override braces, no backslash escapes, no
    control or format characters, single spaces."""
    text = unicodedata.normalize("NFC", text).translate(_OVERRIDE_CHARS)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())


def ts(seconds: float) -> str:
    """ASS time `H:MM:SS.cc` (centiseconds)."""
    cs = max(int(round(seconds * 100)), 0)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def font_em_ratio(font_path: Optional[str]) -> float:
    """em / Fontsize as libass draws this face: unitsPerEm / (usWinAscent + usWinDescent)."""
    if font_path:
        try:
            from fontTools.ttLib import TTFont

            f = TTFont(font_path, lazy=True)
            return f["head"].unitsPerEm / float(f["OS/2"].usWinAscent + f["OS/2"].usWinDescent)
        except Exception:  # noqa: BLE001 — fall back to the vendored face's measured ratio
            pass
    return _INTER_EM_RATIO


def em_px(font_path: Optional[str] = None) -> float:
    return FONT_SIZE * font_em_ratio(font_path)


def _heuristic_width(text: str) -> float:
    """Conservative width at the drawn em when no font is available (~0.58 em per character;
    Inter Bold averages ~0.55 on English copy)."""
    return len(text) * em_px() * 0.58


def font_measurer(font_path: str) -> Callable[[str], float]:
    """Pillow's advance width at the em libass will actually draw; the heuristic if Pillow or
    the font is missing."""
    try:
        from PIL import ImageFont

        font = ImageFont.truetype(font_path, max(int(round(em_px(font_path))), 1))
    except Exception:  # noqa: BLE001 — measurement is best-effort; the heuristic is conservative
        return _heuristic_width
    return lambda text: float(font.getlength(text))


def missing_glyphs(font_path: str, texts: Iterable[str]) -> List[str]:
    """Characters the font cannot draw (libass would silently fall back to DejaVu for them)."""
    from fontTools.ttLib import TTFont

    cmap = TTFont(font_path, lazy=True).getBestCmap() or {}
    missing = set()
    for t in texts:
        for ch in t:
            if not ch.isspace() and ord(ch) not in cmap:
                missing.add(ch)
    return sorted(missing)


def _read_as(words: Sequence[Dict]) -> List[str]:
    """The first two words of `words` as they are READ: lower-cased, quotes and closing punctuation
    stripped, a hyphenated word split into its parts ("per-share" → "per", "share")."""
    out: List[str] = []
    for w in words:
        core = sanitize(str(w.get("w", ""))).lower().strip(_QUOTES).rstrip(_TRAILING_MARKS)
        out.extend(part for part in core.split("-") if part)
        if len(out) >= 2:
            break
    return out[:2]


def _binds_forward(word: Dict, following: Sequence[Dict]) -> bool:
    """Is `word` a FIGURE that the words after it (`following`, in order) are read with — "$50 billion",
    "12 percent", "$4.10 a share"? No phrase may end between them (LIVE-CAP-1)."""
    if not _FIGURE_RE.match(sanitize(str(word.get("w", ""))).strip(_QUOTES)):
        return False
    lead = _read_as(following)
    return bool(lead) and (lead[0] in _SCALE_WORDS or tuple(lead) in _SCALE_PAIRS)


def phrases(words: Sequence[Dict], measure: Callable[[str], float] = _heuristic_width) -> List[List[Dict]]:
    """Group timed words into on-screen phrases (see the module docstring)."""
    out: List[List[Dict]] = []
    cur: List[Dict] = []

    def flush():
        nonlocal cur
        if cur:
            out.append(cur)
        cur = []

    for i, w in enumerate(words):
        if cur and cur[-1].get("line") != w.get("line"):
            flush()
        candidate = cur + [w]
        text = " ".join(sanitize(x["w"]) for x in candidate)
        if cur and (len(candidate) > MAX_PHRASE_WORDS or measure(text) > SAFE_WIDTH):
            # Never end a full phrase on a figure its scale word follows ("…invested $50 | billion") —
            # wherever the phrase is cut — nor, while it keeps two words, on a function word ("…offers a |
            # price"): carry them over. The words after `w` are looked at on its own line only.
            following = [w, *[x for x in words[i + 1:i + 2] if x.get("line") == w.get("line")]]
            carry: List[Dict] = []
            while cur:
                if _binds_forward(cur[-1], carry + following):
                    carry.insert(0, cur.pop())
                elif len(cur) > 2 and sanitize(cur[-1]["w"]).lower().strip(_QUOTES) in _FUNCTION_WORDS:
                    carry.insert(0, cur.pop())
                else:
                    break
            flush()
            candidate = carry + [w]
        cur = candidate
        last = sanitize(w["w"])
        if _ends(_SENTENCE_END, last) or (len(cur) >= MIN_PHRASE_WORDS and _ends(_CLAUSE_END, last)):
            flush()
    flush()
    return out


def events(words: Sequence[Dict], measure: Callable[[str], float] = _heuristic_width) -> List[Event]:
    """One event per word window; each phrase stays up until the next phrase starts (at most
    PHRASE_TAIL_SECONDS after its last word). Windows shorter than MIN_EVENT_SECONDS merge into
    the next one instead of pushing later words back."""
    groups = phrases(words, measure)
    evs: List[Event] = []
    for gi, group in enumerate(groups):
        texts = [sanitize(x["w"]) for x in group]
        nxt_start = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else None
        phrase_end = group[-1]["e"] + PHRASE_TAIL_SECONDS
        if nxt_start is not None:
            phrase_end = min(phrase_end, nxt_start)
        phrase_end = max(phrase_end, group[-1]["e"])
        full = " ".join(texts)
        scale = 100 if measure(full) <= SAFE_WIDTH else max(int(100 * SAFE_WIDTH / measure(full)), 40)
        pending_start: Optional[float] = None
        for i, w in enumerate(group):
            start = w["s"] if pending_start is None else pending_start
            end = group[i + 1]["s"] if i + 1 < len(group) else phrase_end
            if end - start < MIN_EVENT_SECONDS and i + 1 < len(group):
                pending_start = start      # too short to read: fold into the next window
                continue
            pending_start = None
            body = " ".join(f"{{\\c{HIGHLIGHT_C}}}{t}{{\\c{WHITE_C}}}" if j == i else t
                            for j, t in enumerate(texts))
            prefix = f"{{\\an5\\pos({POS[0]},{POS[1]})" + (f"\\fscx{scale}" if scale != 100 else "") + "}"
            evs.append(Event(start, max(end, start + 0.01), prefix + body))
    return evs


def without_hook_card_words(words: Sequence[Dict], hook: str, until: float) -> List[Dict]:
    """The word table to CAPTION when the video opens on a HOOK card (Batch 2): `words` minus the hook
    line's (HOOK_LINE) words that START before `until` — the moment the hook card leaves the screen
    (the timeline's first boundary) — because the card already shows those words, verbatim. PURE but
    for logging; `words` is never mutated.

    It never hides a word the card does not show: only when the hook line's words, in order, ARE
    `hook`'s whitespace split (timings splits every narrated line that way, and the server checks the
    table against the accepted script). Otherwise — no hook line, another text, an odd entry — the
    table comes back whole, logged WARNING: a hook card never costs a caption it does not replace. A
    hook word that starts at or after `until` keeps its caption (the timeline can end card 0 before
    the hook does when later cards need their minimum time); every other line is untouched."""
    table = list(words)
    hook_words = [w for w in table if isinstance(w, dict) and w.get("line") == HOOK_LINE]
    spoken = [w.get("w") for w in hook_words]
    if not isinstance(hook, str) or not hook_words or spoken != hook.split():
        logger.warning("captions: the hook card's text is not the narration's line %d (%d word(s)) — every "
                       "caption is kept", HOOK_LINE, len(hook_words))
        return table
    if (isinstance(until, bool) or not isinstance(until, (int, float)) or not until == until
            or any(isinstance(w.get("s"), bool) or not isinstance(w.get("s"), (int, float)) for w in hook_words)):
        logger.warning("captions: no usable time for the hook card (until=%r) — every caption is kept", until)
        return table
    return [w for w in table if not (isinstance(w, dict) and w.get("line") == HOOK_LINE and w["s"] < until)]


def quantize(evs: Sequence[Event]) -> List[Event]:
    """Events on whole CENTISECONDS (ASS time resolution), never overlapping, never empty: each
    start is at least the previous end, each end at most the next start; an event with no time
    left is dropped (the one before it already covers the moment). Python's round-half-even on a
    float could otherwise give an event start == end, or two phrases drawn on top of each other."""
    out: List[Event] = []
    cs = [(int(round(e.start * 100)), int(round(e.end * 100)), e.text) for e in evs]
    for i, (s_cs, e_cs, text) in enumerate(cs):
        if out:
            s_cs = max(s_cs, int(round(out[-1].end * 100)))
        if i + 1 < len(cs):
            e_cs = min(e_cs, max(cs[i + 1][0], s_cs))
        if e_cs <= s_cs:
            continue
        out.append(Event(s_cs / 100.0, e_cs / 100.0, text))
    return out


def build_ass(words: Sequence[Dict], measure: Callable[[str], float] = _heuristic_width) -> str:
    """The complete .ass file for `words` (the audio asset's `metadata.words` table)."""
    header = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {PLAY_RES[0]}",
        f"PlayResY: {PLAY_RES[1]}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: None",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{FONT_NAME},{FONT_SIZE},{WHITE},{WHITE},{OUTLINE_COLOUR},{SHADOW_COLOUR},"
        f"-1,0,0,0,100,100,0,0,1,{OUTLINE},0,5,80,80,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])
    lines = [f"Dialogue: 0,{ts(e.start)},{ts(e.end)},Caption,,0,0,0,,{e.text}"
             for e in quantize(events(words, measure))]
    return header + "\n" + "\n".join(lines) + "\n"


def timeline_bounds(words: Sequence[Dict]) -> Tuple[float, float]:
    if not words:
        return 0.0, 0.0
    return float(words[0]["s"]), float(words[-1]["e"])
