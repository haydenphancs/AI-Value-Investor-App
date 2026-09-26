"""
Word-timed captions as an ASS subtitle file for libass (Phase 3 builds it, Phase 4 burns it;
SYSTEM_DESIGN_GUIDELINES §12.7). PURE apart from the optional font measurement (Pillow and
fontTools, imported inside functions): no app.*, no torch.

Layout — one line of 3-5 words in the lower middle of a 1080×1920 frame, the word being spoken
drawn in the brand colour, the others in white, all with a dark outline:

* Phrases never cross a narrated line and end early at a sentence or clause mark once they have
  a few words; a phrase wider than the safe width is split, and a single word wider than it is
  shrunk horizontally (`\\fscx`) rather than wrapped (WrapStyle 2 = never wrap).
* ONE Dialogue event per word window: each event draws the whole phrase with the active word
  coloured, and ends exactly when the next begins, so the text never flickers. Colour only —
  scaling the active word would shift a centred line on every word.
* Colours are ASS `&HAABBGGRR`: BGR order, and alpha is INVERTED (00 = opaque).
* The text is model-written. Braces and backslashes are ASS override syntax (`{\\p1}` draws
  shapes, `\\N` breaks lines), so they are neutralised here even though the server's markup rule
  already refuses them — defence in depth, since a caption file is where they would execute.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

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


def phrases(words: Sequence[Dict], measure: Callable[[str], float] = _heuristic_width) -> List[List[Dict]]:
    """Group timed words into on-screen phrases (see the module docstring)."""
    out: List[List[Dict]] = []
    cur: List[Dict] = []

    def flush():
        nonlocal cur
        if cur:
            out.append(cur)
        cur = []

    for w in words:
        if cur and cur[-1].get("line") != w.get("line"):
            flush()
        candidate = cur + [w]
        text = " ".join(sanitize(x["w"]) for x in candidate)
        if cur and (len(candidate) > MAX_PHRASE_WORDS or measure(text) > SAFE_WIDTH):
            # Never end a full phrase on a function word ("…offers a | price"): carry it over.
            carry = []
            while len(cur) > 2 and sanitize(cur[-1]["w"]).lower().strip(_QUOTES) in _FUNCTION_WORDS:
                carry.insert(0, cur.pop())
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
