"""
The still cards of the day's 9:16 video (Phase 4, SYSTEM_DESIGN_GUIDELINES §12.8): 1080×1920 PNGs
that `render.py` lays under the burned captions. PURE apart from Pillow and fontTools (imported
inside functions, like captions.py); no app.*, no network.

What a video shows, in order:

* card 0 — the BRAND card during the hook: the logo and the wordmark "Caydex". The hook itself is
  shown ONLY by the burned captions; no card ever draws it.
* the accepted script's 3-4 TEXT cards (title + body, always together, in order).
* the closing DISCLAIMER card after the narration: the server-supplied disclaimer text verbatim,
  a small logo and the CTA "caydexinvest.com".
* ("stat" — a big figure, a label and an optional body — exists for the later reportorial class C
  and is laid out and tested now; class A never builds one.)

Why each piece is shaped the way it is:

* **The strings are checked, the pixels are not.** The worker declares every string a card drew
  (`onscreen_strings`) and the server refuses anything that is not the accepted script's card
  titles/bodies, its disclaimer card or `BRAND_TEXT` (`run_service._check_onscreen_text`). So a
  string is returned VERBATIM — never normalised, re-cased or re-wrapped; wrapping is a drawing
  concern only — and nothing is drawn that `onscreen_strings` does not return (the hook included).
* **Never truncated, never ellipsised, never a word broken.** Text is greedily word-wrapped (split
  on whitespace, measured with the face's advance width at the size being tried) and auto-fitted
  by stepping every size on the card down together from its start to its floor (`RAMP_STEPS`).
  A card that does not fit at the floor — or a single word wider than the column there — raises
  `CardOverflow`; the render stage turns that into a skipped day, never a clipped card.
* **Card text stays inside `TEXT_ZONE_Y` × `SAFE_X`**, clear of the caption band (derived from
  captions.POS / FONT_SIZE / OUTLINE) and of the TikTok/Reels chrome. The INK box of every drawn
  line is checked after layout (a glyph can overhang its advance), and a line that would leave its
  panel counts as "does not fit at this size". Nothing at all is drawn outside the zone.
* **A glyph the face lacks is refused up front** (`check_glyphs`, fontTools cmap — Pillow would
  draw a tofu box and libass would silently fall back to another face). `render_card` itself does
  not re-check: it never crashes or hangs on emoji / combining marks, it just draws .notdef.
* **Deterministic bytes**: same spec + same Pillow + same layout engine → identical PNG (RGB, no
  metadata chunks, fixed zlib level). The layout engine changes the bytes (RAQM kerns with GPOS,
  BASIC does not), so an explicit `layout_engine="raqm"` without libraqm is an error, not Pillow's
  silent fallback; `resolve_layout_engine` says which one a call will use.
* **Bounded work**: at most RAMP_STEPS+1 sizes; each wrap aborts as soon as the column is full;
  any drawn string longer than the server's ONSCREEN_TEXT_MAX_CHARS (600) is refused before any
  measuring (the server would refuse it anyway).

Palette: PAGE, CARD, ACCENT and TEXT (the brand hexes), and alpha variants of them composited
over the background they sit on — nothing else (tests/test_marketing_cards.py checks every pixel
of the logo-less cards). Bump CARD_RENDER_VERSION with ANY change to what a card looks like: it is
part of the video's reuse key (`render.render_key`).
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from marketing import captions

logger = logging.getLogger("marketing.cards")

# ── geometry ─────────────────────────────────────────────────────────────────

WIDTH, HEIGHT = captions.PLAY_RES     # 1080 × 1920, the caption file's PlayRes
#: Card text lives in these rows: below the platforms' top chrome ("Following | For You"),
#: above the caption band.
TEXT_ZONE_Y: Tuple[int, int] = (160, 1120)
#: …and these columns: the captions' own safe width (920), centred.
SAFE_X: Tuple[int, int] = ((WIDTH - captions.SAFE_WIDTH) // 2, (WIDTH + captions.SAFE_WIDTH) // 2)
#: Rows a burned caption can ink, half-open [top, bottom). libass sizes the face so that
#: usWinAscent+usWinDescent = Fontsize, so a \\an5 line centred on POS spans ±FONT_SIZE/2, plus
#: its OUTLINE (the style has no shadow and WrapStyle 2 never wraps), plus a 16 px margin:
#: 1240 ± (48 + 6 + 16) = (1170, 1310). Measured through ffmpeg/libass on "ÅÉÎ gjpqy Quality|":
#: rows 1184-1293 (tests/test_marketing_cards.py burns one and checks the band covers it).
_CAPTION_MARGIN = 16
_CAPTION_HALF = captions.FONT_SIZE // 2 + captions.OUTLINE + _CAPTION_MARGIN
CAPTION_BAND_Y: Tuple[int, int] = (captions.POS[1] - _CAPTION_HALF, captions.POS[1] + _CAPTION_HALF)

# ── brand ────────────────────────────────────────────────────────────────────

PAGE = "#171B26"
CARD = "#1E2330"
ACCENT = "#60A5FA"
TEXT = "#FFFFFF"
WORDMARK = "Caydex"
CTA = "caydexinvest.com"
#: The only strings a card draws besides the script's own (mirrors
#: app.schemas.marketing.VIDEO_BRAND_TEXT; tests/test_marketing_cards.py pins them equal).
BRAND_TEXT: Tuple[str, ...] = (WORDMARK, CTA)

#: Part of the video's reuse key: bump with any change to layout, sizes, colours or assets.
CARD_RENDER_VERSION = "cards/v1"

KINDS: Tuple[str, ...] = ("brand", "text", "stat", "disclaimer")
#: Mirrors app.schemas.marketing.ONSCREEN_TEXT_MAX_CHARS: the server refuses a longer declared
#: string, so it is refused here before any measuring (and it bounds the work).
MAX_STRING_CHARS = 600

# ── typography (px = Pillow size = pixels per em) ────────────────────────────

#: Every size on a card steps down together, in RAMP_STEPS equal integer steps, from its start
#: to its floor; the first step at which everything fits wins.
RAMP_STEPS = 16


@dataclass(frozen=True)
class _Style:
    start: int
    floor: int
    leading: float          # line pitch / size

    def size(self, step: int) -> int:
        return self.start - ((self.start - self.floor) * step) // RAMP_STEPS


TITLE_STYLE = _Style(76, 44, 1.16)         # text card title (ACCENT)
BODY_STYLE = _Style(58, 34, 1.28)          # text card body (TEXT)
FIGURE_STYLE = _Style(168, 84, 1.08)       # stat figure (ACCENT)
LABEL_STYLE = _Style(52, 32, 1.2)          # stat label (TEXT)
STAT_BODY_STYLE = _Style(44, 30, 1.3)      # stat body (TEXT at 82 % over CARD)
DISCLAIMER_STYLE = _Style(46, 28, 1.3)     # disclaimer body (TEXT at 86 % over PAGE)
CTA_STYLE = _Style(60, 40, 1.2)            # "caydexinvest.com" (ACCENT)
WORDMARK_STYLE = _Style(132, 96, 1.2)      # "Caydex" on the brand card (TEXT)
BADGE_SIZE = 30                            # the pill's text; fixed, never wrapped

PANEL_PAD_X, PANEL_PAD_Y, PANEL_RADIUS = 56, 64, 40
RULE_W, RULE_H, RULE_GAP = 72, 8, 32       # the accent rule on top of a panel (no badge)
BADGE_PAD_X, BADGE_PAD_Y, BADGE_GAP = 20, 10, 32
LOGO_BRAND_PX, LOGO_SMALL_PX = 440, 176
LOGO_GAP_BRAND, LOGO_GAP_SMALL = 48, 56
_AA = 4                                    # supersampling factor for panel / pill edges
_PNG_COMPRESS_LEVEL = 6

_warned: set = set()                       # "logged once" keys (tests reset it)


# ── errors ───────────────────────────────────────────────────────────────────


class CardOverflow(Exception):
    """The card's text cannot fit its column at the floor size (or one word is wider than the
    column there, or a string is over MAX_STRING_CHARS). Never truncated to fit."""


class MissingGlyphs(Exception):
    """The font cannot draw these characters (Pillow would draw tofu; libass would fall back)."""

    def __init__(self, chars: Sequence[str]):
        self.chars: List[str] = list(chars)
        shown = ", ".join(f"{c!r} (U+{ord(c):04X})" for c in self.chars[:20])
        more = f" and {len(self.chars) - 20} more" if len(self.chars) > 20 else ""
        super().__init__(f"the font cannot draw {len(self.chars)} character(s): {shown}{more}")


class CardAssetError(RuntimeError):
    """The font or logo file is unreadable — an image/build defect, not a content problem."""


class _NoFit(Exception):
    """Internal: this step of the ramp does not fit (the reason is kept for the floor step)."""


# ── the spec ─────────────────────────────────────────────────────────────────


def _present(s: str) -> bool:
    return bool(s.strip())


@dataclass(frozen=True)
class CardSpec:
    """One card. Which fields a kind draws (and requires) is fixed, and a field the kind does not
    draw must be empty — so `onscreen_strings` is always exactly what `render_card` draws:

    * brand      — nothing (draws the logo and WORDMARK)
    * text       — title and/or body; optional badge
    * stat       — figure (required), label, body; optional badge
    * disclaimer — body (required; the server's disclaimer card text; draws it + logo + CTA)
    """

    kind: str
    title: str = ""
    body: str = ""
    figure: str = ""
    label: str = ""
    badge: str = ""

    def __post_init__(self) -> None:
        for name in ("kind", "title", "body", "figure", "label", "badge"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"CardSpec.{name} must be a str, not {type(value).__name__}")
        if self.kind not in KINDS:
            raise ValueError(f"CardSpec.kind {self.kind!r} is not one of {KINDS}")
        allowed = {
            "brand": (),
            "text": ("title", "body", "badge"),
            "stat": ("figure", "label", "body", "badge"),
            "disclaimer": ("body",),
        }[self.kind]
        stray = [n for n in ("title", "body", "figure", "label", "badge")
                 if n not in allowed and getattr(self, n)]
        if stray:
            raise ValueError(f"a {self.kind} card draws no {', '.join(stray)} (it would be silently dropped)")
        if self.kind == "text" and not (_present(self.title) or _present(self.body)):
            raise ValueError("a text card needs a title or a body")
        if self.kind == "stat" and not _present(self.figure):
            raise ValueError("a stat card needs a figure")
        if self.kind == "disclaimer" and not _present(self.body):
            raise ValueError("a disclaimer card needs its text (body)")


def _script_str(value: Any, where: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string, not {type(value).__name__}")
    return value


def cards_for_script(script: Dict[str, Any]) -> List[CardSpec]:
    """[brand] + one text card per `script["cards"]` entry (title/body stripped; an entry with
    neither is skipped) + [disclaimer card, its text verbatim]. ValueError when the disclaimer
    card is missing/empty — a video without its disclaimer must not render — or the script is
    malformed."""
    if not isinstance(script, dict):
        raise ValueError(f"script must be a dict, not {type(script).__name__}")
    disclaimer = _script_str(script.get("disclaimer_card"), "script.disclaimer_card")
    if not _present(disclaimer):
        raise ValueError("the script has no disclaimer_card: a video without its disclaimer must not render")
    raw_cards = script.get("cards")
    if raw_cards is None:
        raw_cards = []
    if not isinstance(raw_cards, list):
        raise ValueError(f"script.cards must be a list, not {type(raw_cards).__name__}")
    specs = [CardSpec("brand")]
    for i, entry in enumerate(raw_cards):
        if not isinstance(entry, dict):
            raise ValueError(f"script.cards[{i}] must be an object, not {type(entry).__name__}")
        title = _script_str(entry.get("title"), f"script.cards[{i}].title").strip()
        body = _script_str(entry.get("body"), f"script.cards[{i}].body").strip()
        if not title and not body:
            logger.warning("script.cards[%d] has neither title nor body — no card drawn for it", i)
            continue
        specs.append(CardSpec("text", title=title, body=body))
    specs.append(CardSpec("disclaimer", body=disclaimer))
    return specs


def onscreen_strings(spec: CardSpec) -> List[str]:
    """EXACTLY the strings `render_card` draws for `spec`, verbatim (the server compares them with
    the accepted script). A whitespace-only field is neither drawn nor returned."""
    if spec.kind == "brand":
        fields = [WORDMARK]
    elif spec.kind == "text":
        fields = [spec.title, spec.body, spec.badge]
    elif spec.kind == "stat":
        fields = [spec.figure, spec.label, spec.body, spec.badge]
    else:  # disclaimer
        fields = [spec.body, CTA]
    return [s for s in fields if _present(s)]


# ── fonts, glyphs, engines ───────────────────────────────────────────────────


def raqm_available() -> bool:
    """Is libraqm (HarfBuzz shaping, GPOS kerning) available to this Pillow?"""
    try:
        from PIL import features

        return bool(features.check("raqm"))
    except Exception:  # noqa: BLE001 — any failure means "not available"
        return False


def resolve_layout_engine(layout_engine: Optional[str] = None) -> str:
    """"raqm" | "basic" — the engine a render with this argument uses. None = raqm when
    available, else basic. An explicit "raqm" without libraqm is a ValueError (Pillow would
    silently fall back, and the bytes would change)."""
    if layout_engine is None:
        return "raqm" if raqm_available() else "basic"
    if layout_engine not in ("raqm", "basic"):
        raise ValueError(f"layout_engine must be 'raqm', 'basic' or None, not {layout_engine!r}")
    if layout_engine == "raqm" and not raqm_available():
        raise ValueError("layout_engine='raqm' was requested but libraqm is not available to Pillow")
    return layout_engine


@lru_cache(maxsize=128)
def _font(font_path: str, size: int, engine: str):
    from PIL import ImageFont

    layout = ImageFont.Layout.RAQM if engine == "raqm" else ImageFont.Layout.BASIC
    try:
        return ImageFont.truetype(font_path, size, layout_engine=layout)
    except Exception as e:  # noqa: BLE001 — re-raised typed, with the path
        raise CardAssetError(f"font {font_path!r} unreadable at {size}px: {type(e).__name__}: {e}") from e


def check_glyphs(specs: Sequence[CardSpec], font_path: str, extra: Iterable[str] = ()) -> None:
    """Raise MissingGlyphs naming every character of every drawn string (and of `extra`, e.g. the
    caption words) that the face's cmap lacks. Whitespace is exempt (it is never drawn)."""
    texts: List[str] = []
    for spec in specs:
        texts.extend(onscreen_strings(spec))
    texts.extend(str(t) for t in extra)
    try:
        missing = captions.missing_glyphs(font_path, texts)
    except Exception as e:  # noqa: BLE001 — re-raised typed, with the path
        raise CardAssetError(f"font {font_path!r} cmap unreadable: {type(e).__name__}: {e}") from e
    if missing:
        raise MissingGlyphs(missing)


# ── colour ───────────────────────────────────────────────────────────────────

RGB = Tuple[int, int, int]


def _rgb(hex_colour: str) -> RGB:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix(fg: str, bg: str, alpha: float) -> RGB:
    """`fg` at `alpha` composited over `bg` — how every non-brand colour on a card is made."""
    f, b = _rgb(fg), _rgb(bg)
    return tuple(int(round(bv + (fv - bv) * alpha)) for fv, bv in zip(f, b))  # type: ignore[return-value]


BADGE_FILL = _mix(ACCENT, CARD, 0.16)
STAT_BODY_FILL = _mix(TEXT, CARD, 0.82)
DISCLAIMER_FILL = _mix(TEXT, PAGE, 0.86)


# ── layout ───────────────────────────────────────────────────────────────────

Box = Tuple[int, int, int, int]            # x0, y0, x1, y1 (half-open)


@dataclass(frozen=True)
class DrawnLine:
    field: str          # which spec field (or "wordmark" / "cta" / "badge") the line belongs to
    text: str           # the wrapped line as drawn
    size: int
    x: int
    y: int
    anchor: str         # Pillow anchor: "la" (left) or "ma" (centred)
    fill: RGB
    ink: Box            # the line's measured ink box on the canvas


@dataclass(frozen=True)
class CardLayout:
    kind: str
    step: int                       # ramp step used (0 = start sizes, RAMP_STEPS = floors)
    engine: str
    panel: Optional[Box] = None
    rule: Optional[Box] = None
    badge: Optional[Box] = None
    logo: Optional[Box] = None
    lines: Tuple[DrawnLine, ...] = ()

    def field_lines(self, name: str) -> List[str]:
        return [ln.text for ln in self.lines if ln.field == name]


def _wrap(words: Sequence[str], font, max_width: float, max_lines: int, what: str) -> List[str]:
    """Greedy word wrap by advance width. Raises _NoFit when one word is wider than the column or
    the lines would exceed `max_lines` (aborting at once, so the work is bounded by the room)."""
    lines: List[str] = []
    cur: Optional[str] = None
    for w in words:
        wl = font.getlength(w)
        if wl > max_width:
            raise _NoFit(f"{what}: the word {w[:40]!r} is {wl:.0f}px wide at {font.size}px "
                         f"(column {max_width:.0f}px)")
        if cur is not None:
            cand = cur + " " + w
            if font.getlength(cand) <= max_width:
                cur = cand
                continue
            lines.append(cur)
        if len(lines) >= max_lines:
            raise _NoFit(f"{what}: needs more than {max_lines} line(s) at {font.size}px")
        cur = w
    if cur is not None:
        lines.append(cur)
    return lines


class _Stack:
    """Top-to-bottom placement in a column of `width` px and at most `budget` px, relative to
    (0, 0); the caller translates it onto the canvas. Each item names the gap it wants above it;
    a LEAD item (the accent rule or the badge) names the gap below it instead, which replaces the
    next item's own. The first item gets no gap."""

    def __init__(self, width: int, budget: int, align: str, font_path: str, engine: str, step: int):
        self.width, self.budget, self.align = width, budget, align
        self.font_path, self.engine, self.step = font_path, engine, step
        self.y = 0
        self.boxes: Dict[str, Box] = {}
        self.lines: List[Tuple[str, str, int, int, int, str, RGB]] = []
        self.badge_text: Optional[Tuple[str, int, int, int]] = None
        self._prev_lead: Optional[int] = None   # the gap a lead item owes the next one
        self._empty = True

    def _gap(self, own: int) -> int:
        if self._empty:
            return 0
        return self._prev_lead if self._prev_lead is not None else own

    def box(self, tag: str, w: int, h: int, gap: int = 0, lead_gap: Optional[int] = None) -> None:
        if w > self.width:
            raise _NoFit(f"{tag}: {w}px wider than the column ({self.width}px)")
        self.y += self._gap(gap)
        x = 0 if self.align == "left" else (self.width - w) // 2
        self.boxes[tag] = (x, self.y, x + w, self.y + h)
        self.y += h
        if self.y > self.budget:
            raise _NoFit(f"{tag}: the card is {self.y}px tall, room for {self.budget}px")
        self._prev_lead, self._empty = lead_gap, False

    def badge(self, text: str) -> None:
        font = _font(self.font_path, BADGE_SIZE, self.engine)
        asc, desc = font.getmetrics()
        w = int(math.ceil(font.getlength(text))) + 2 * BADGE_PAD_X
        self.box("badge", w, asc + desc + 2 * BADGE_PAD_Y, lead_gap=BADGE_GAP)
        x0, y0, _, y1 = self.boxes["badge"]
        self.badge_text = (text, x0 + BADGE_PAD_X, (y0 + y1) // 2, BADGE_SIZE)

    def text(self, name: str, text: str, style: _Style, fill: RGB, gap_em: float = 0.0,
             gap_px: int = 0) -> None:
        size = style.size(self.step)
        font = _font(self.font_path, size, self.engine)
        asc, desc = font.getmetrics()
        pitch = int(round(size * style.leading))
        gap = self._gap(gap_px + int(round(gap_em * size)))
        room = self.budget - self.y - gap
        max_lines = 0 if room < asc + desc else (room - (asc + desc)) // pitch + 1
        if max_lines <= 0:
            raise _NoFit(f"{name}: no room left at {size}px")
        lines = _wrap(text.split(), font, self.width, max_lines, name)
        if not lines:                            # whitespace only: nothing drawn, no gap taken
            return
        self.y += gap
        x = 0 if self.align == "left" else self.width // 2
        anchor = "la" if self.align == "left" else "ma"
        for i, line in enumerate(lines):
            self.lines.append((name, line, size, x, self.y + i * pitch, anchor, fill))
        self.y += (len(lines) - 1) * pitch + asc + desc
        self._prev_lead, self._empty = None, False


def _check_length(spec: CardSpec) -> None:
    for s in onscreen_strings(spec):
        if len(s) > MAX_STRING_CHARS:
            raise CardOverflow(f"{spec.kind} card: a string of {len(s)} characters is over "
                               f"{MAX_STRING_CHARS} (the server refuses it): {s[:60]!r}…")


def _stack_for(spec: CardSpec, step: int, font_path: str, engine: str, with_logo: bool) -> Tuple[_Stack, bool]:
    """The card's column content at `step`; returns (stack, on_panel)."""
    zone_w = SAFE_X[1] - SAFE_X[0]
    zone_h = TEXT_ZONE_Y[1] - TEXT_ZONE_Y[0]
    if spec.kind in ("text", "stat"):
        st = _Stack(zone_w - 2 * PANEL_PAD_X, zone_h - 2 * PANEL_PAD_Y, "left", font_path, engine, step)
        if _present(spec.badge):
            st.badge(spec.badge)
        else:
            st.box("rule", RULE_W, RULE_H, lead_gap=RULE_GAP)
        if spec.kind == "text":
            if _present(spec.title):
                st.text("title", spec.title, TITLE_STYLE, _rgb(ACCENT))
            if _present(spec.body):
                st.text("body", spec.body, BODY_STYLE, _rgb(TEXT), gap_em=0.55)
        else:
            st.text("figure", spec.figure, FIGURE_STYLE, _rgb(ACCENT))
            if _present(spec.label):
                st.text("label", spec.label, LABEL_STYLE, _rgb(TEXT), gap_em=0.3)
            if _present(spec.body):
                st.text("body", spec.body, STAT_BODY_STYLE, STAT_BODY_FILL, gap_em=0.7)
        return st, True
    st = _Stack(zone_w, zone_h, "center", font_path, engine, step)
    if spec.kind == "brand":
        if with_logo:
            st.box("logo", LOGO_BRAND_PX, LOGO_BRAND_PX)
        st.text("wordmark", WORDMARK, WORDMARK_STYLE, _rgb(TEXT), gap_px=LOGO_GAP_BRAND)
    else:  # disclaimer
        if with_logo:
            st.box("logo", LOGO_SMALL_PX, LOGO_SMALL_PX)
        st.text("body", spec.body, DISCLAIMER_STYLE, DISCLAIMER_FILL, gap_px=LOGO_GAP_SMALL)
        st.text("cta", CTA, CTA_STYLE, _rgb(ACCENT), gap_em=0.9)
    return st, False


def _within(inner: Box, outer: Box) -> bool:
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _place(spec: CardSpec, step: int, font_path: str, engine: str, with_logo: bool) -> CardLayout:
    st, on_panel = _stack_for(spec, step, font_path, engine, with_logo)
    zone: Box = (SAFE_X[0], TEXT_ZONE_Y[0], SAFE_X[1], TEXT_ZONE_Y[1])
    zone_h = TEXT_ZONE_Y[1] - TEXT_ZONE_Y[0]
    panel: Optional[Box] = None
    if on_panel:
        panel_h = st.y + 2 * PANEL_PAD_Y
        top = TEXT_ZONE_Y[0] + (zone_h - panel_h) // 2
        panel = (SAFE_X[0], top, SAFE_X[1], top + panel_h)
        dx, dy = SAFE_X[0] + PANEL_PAD_X, top + PANEL_PAD_Y
        ink_bounds = panel
    else:
        dx, dy = SAFE_X[0], TEXT_ZONE_Y[0] + (zone_h - st.y) // 2
        ink_bounds = zone

    def shift(b: Box) -> Box:
        return b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy

    lines: List[DrawnLine] = []
    for name, text, size, x, y, anchor, fill in st.lines:
        font = _font(font_path, size, engine)
        l, t, r, b = font.getbbox(text, anchor=anchor)
        ink = (x + dx + l, y + dy + t, x + dx + r, y + dy + b)
        if not _within(ink, ink_bounds):
            raise _NoFit(f"{name}: the ink of {text[:40]!r} at {size}px leaves its area")
        lines.append(DrawnLine(name, text, size, x + dx, y + dy, anchor, fill, ink))
    if st.badge_text is not None:
        text, bx, by, size = st.badge_text
        font = _font(font_path, size, engine)
        l, t, r, b = font.getbbox(text, anchor="lm")
        ink = (bx + dx + l, by + dy + t, bx + dx + r, by + dy + b)
        if not _within(ink, shift(st.boxes["badge"])):
            raise _NoFit(f"badge: the ink of {text[:40]!r} leaves its pill")
        lines.append(DrawnLine("badge", text, size, bx + dx, by + dy, "lm", _rgb(ACCENT), ink))
    boxes = {k: shift(v) for k, v in st.boxes.items()}
    for tag, b in boxes.items():
        if not _within(b, ink_bounds):          # defence in depth; the stack budget ensures it
            raise _NoFit(f"{tag}: leaves its area")
    return CardLayout(kind=spec.kind, step=step, engine=engine, panel=panel, rule=boxes.get("rule"),
                      badge=boxes.get("badge"), logo=boxes.get("logo"), lines=tuple(lines))


def layout_card(spec: CardSpec, *, font_path: str, with_logo: bool = True,
                layout_engine: Optional[str] = None) -> CardLayout:
    """Where everything on the card goes: the largest ramp step at which every element fits.
    CardOverflow (with the floor step's reason) when none does."""
    engine = resolve_layout_engine(layout_engine)
    _check_length(spec)
    reason = ""
    for step in range(RAMP_STEPS + 1):
        try:
            return _place(spec, step, font_path, engine, with_logo)
        except _NoFit as e:
            reason = str(e)
    raise CardOverflow(f"{spec.kind} card does not fit at the floor sizes: {reason}")


# ── drawing ──────────────────────────────────────────────────────────────────


def _rounded_mask(w: int, h: int, radius: int):
    """An anti-aliased rounded-rect mask (drawn at _AA× and box-reduced: deterministic)."""
    from PIL import Image, ImageDraw

    big = Image.new("L", (w * _AA, h * _AA), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * _AA - 1, h * _AA - 1), radius=radius * _AA, fill=255)
    return big.reduce(_AA)


def _fill_rounded(img, box: Box, radius: int, colour: RGB) -> None:
    from PIL import Image

    w, h = box[2] - box[0], box[3] - box[1]
    img.paste(Image.new("RGB", (w, h), colour), (box[0], box[1]), _rounded_mask(w, h, radius))


def _logo_tile(logo_path: str, px: int):
    """The logo scaled (LANCZOS) into a px×px tile over PAGE (any transparency composited, a
    non-square logo centred)."""
    from PIL import Image

    try:
        with Image.open(logo_path) as src:
            src.load()
            rgba = src.convert("RGBA")
    except Exception as e:  # noqa: BLE001 — re-raised typed, with the path
        raise CardAssetError(f"logo {logo_path!r} unreadable: {type(e).__name__}: {e}") from e
    w, h = rgba.size
    if w <= 0 or h <= 0:
        raise CardAssetError(f"logo {logo_path!r} is empty")
    scale = px / max(w, h)
    sw, sh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    scaled = rgba.resize((sw, sh), Image.LANCZOS)
    tile = Image.new("RGBA", (px, px), _rgb(PAGE) + (255,))
    tile.alpha_composite(scaled, ((px - sw) // 2, (px - sh) // 2))
    return tile.convert("RGB")


def _warn_once(key: str, msg: str, *args: Any) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(msg, *args)


def render_card(spec: CardSpec, *, font_path: str, logo_path: Optional[str] = None,
                layout_engine: Optional[str] = None) -> bytes:
    """The card as deterministic PNG bytes (1080×1920 RGB, no metadata). CardOverflow when it
    cannot fit; CardAssetError when the font or logo is unreadable."""
    from PIL import Image, ImageDraw

    uses_logo = spec.kind in ("brand", "disclaimer")
    if uses_logo and not logo_path:
        _warn_once("no_logo", "cards: no logo path — the brand and disclaimer cards render without the logo")
    layout = layout_card(spec, font_path=font_path, with_logo=bool(uses_logo and logo_path),
                         layout_engine=layout_engine)
    img = Image.new("RGB", (WIDTH, HEIGHT), _rgb(PAGE))
    if layout.panel:
        _fill_rounded(img, layout.panel, PANEL_RADIUS, _rgb(CARD))
    if layout.rule:
        _fill_rounded(img, layout.rule, RULE_H // 2, _rgb(ACCENT))
    if layout.badge:
        b = layout.badge
        _fill_rounded(img, b, (b[3] - b[1]) // 2, BADGE_FILL)
    if layout.logo and logo_path:
        img.paste(_logo_tile(logo_path, layout.logo[2] - layout.logo[0]), layout.logo[:2])
    draw = ImageDraw.Draw(img)
    for ln in layout.lines:
        draw.text((ln.x, ln.y), ln.text, font=_font(font_path, ln.size, layout.engine), fill=ln.fill,
                  anchor=ln.anchor)
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=_PNG_COMPRESS_LEVEL, optimize=False)
    logger.debug("card rendered kind=%s step=%d engine=%s lines=%d bytes=%d",
                 spec.kind, layout.step, layout.engine, len(layout.lines), buf.tell())
    return buf.getvalue()


def render_cards(specs: Sequence[CardSpec], *, font_path: str, logo_path: Optional[str] = None,
                 layout_engine: Optional[str] = None) -> List[bytes]:
    return [render_card(s, font_path=font_path, logo_path=logo_path, layout_engine=layout_engine)
            for s in specs]
