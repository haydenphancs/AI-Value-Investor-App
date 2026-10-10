"""
The still cards of the day's 9:16 video (Phase 4, SYSTEM_DESIGN_GUIDELINES §12.8): 1080×1920 PNGs
that `render.py` lays under the burned captions — and the day's 4:5 POST IMAGE (drop 1,
2026-10-09: "an image on every post"), a 1080×1350 baseline JPEG. PURE apart from Pillow and
fontTools (imported inside functions, like captions.py); no app.*, no network.

What a video shows, in order:

* the accepted script's 3-4 TEXT cards (title + body, always together, in order). The FIRST is on
  screen from frame 0, through the hook (drop 1: "videos open on the company, never on our logo"
  — the brand card that used to cover the hook is gone). The hook itself is shown ONLY by the
  burned captions; no card ever draws it.
* the closing DISCLAIMER card after the narration: the server-supplied disclaimer text verbatim,
  a small logo and the CTA "caydexinvest.com".
* (the BRAND card — the logo and the wordmark "Caydex" — is drawn only when a script carries no
  text card at all, so the narration still has a card under it; the writer always emits three.)
* ("stat" — a big figure, a label and an optional body — exists for the later reportorial class C
  and is laid out and tested now; class A never builds one.)

A TEMPLATE (news) script's video (drop 2a, `video_layout == "per_line"`) is different: its
OPENING card (`opening_card`: kicker, 1-2 company logos, chip, figure, headline) is shown alone
over the hook, then one text card per narration line, then the disclaimer (`cards_for_script`,
timed by `video.timeline(..., hook_card=True)`). A company logo is drawn UNALTERED — only scaled —
on a light rounded plate (`draw_plate`); a logo that is missing or did not verify is a WORDMARK
plate carrying the company name, which is then a drawn (declared) string. Its post image is drawn
by `news_layouts.py` from the closed `image_spec` (`render_post_image` dispatches; a template never
falls back to the lesson layout, which would draw its alt text).

What the post image shows (`ImageSpec`, `render_image`): the accepted output's `image_post` title
and its 2-4 paragraphs on a panel, and the server's code-owned `image_footer` burned under it — no
logo, no brand card. Same rules as a card: every string declared verbatim (`image_onscreen_strings`
— the server allows only the accepted `image_post` strings and the footer, and requires the
footer), never truncated, a glyph Inter lacks refused up front. Encoded as a BASELINE JPEG
(sRGB by convention: RGB, no ICC profile, no EXIF; 4:4:4 so coloured text stays crisp), stepping
the quality down until it is at most POST_IMAGE_MAX_BYTES — `ImageTooLarge` when even the lowest
step is not, never a bigger file.

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

#: Part of the video's AND the post image's reuse keys: bump with any change to layout, sizes,
#: colours, assets, the card set or the JPEG encoding. v2 (drop 1): the video lost its opening
#: brand card; the 4:5 post image was added. v3 (drop 2a): the template opening card, the company
#: logo plates and wordmark tiles, the template post-image layouts (news_layouts.py).
CARD_RENDER_VERSION = "cards/v3"

KINDS: Tuple[str, ...] = ("brand", "text", "stat", "disclaimer", "opening")

# ── drop 2a: template (news) scripts — mirrors of app.* (the worker never imports app.*; ──────
# tests/test_marketing_cards.py and tests/test_marketing_news_layouts.py pin every one equal)

#: app.schemas.marketing.TEMPLATE_AUTHORSHIP — a script built by a fixed template, not the writer.
TEMPLATE_AUTHORSHIP = "template"
#: app.schemas.marketing.VIDEO_LAYOUT_PER_LINE — [opening] + one text card per narration line.
VIDEO_LAYOUT_PER_LINE = "per_line"
#: app.services.marketing.template_onscreen: the closed image_spec / opening_card schema.
LAYOUTS: Tuple[str, ...] = ("rows", "spotlight", "pair", "bars", "grid")
#: What this worker ACCEPTS from the server (mirrors template_onscreen.SHIPPED_LAYOUTS, same members,
#: same order). Drop 2b (2026-10-10) ships `pair` and `grid` with their series — both sides' literals
#: were flipped together; news_layouts derives what it accepts from this tuple.
SHIPPED_LAYOUTS: Tuple[str, ...] = ("rows", "spotlight", "pair", "bars", "grid")
SPEC_VERSION = 1
MAX_LOGOS = 12
MAX_ROWS = 8
MAX_SECTIONS = 3
MAX_SECTION_ROWS = 5
MAX_CELLS = 3
MAX_NOTES = 2
MAX_LINES = 2
MIN_BARS, MAX_BARS = 2, 7
MIN_FLOW, MAX_FLOW = 2, 4
MIN_TILES, MAX_TILES = 3, 12
MAX_OPENING_LOGOS = 2
LOGO_KEY_MAX_CHARS = 16
BAR_STYLES: Tuple[str, ...] = ("fill", "outline")
COMMON_KEYS: Tuple[str, ...] = ("layout", "version", "kicker", "footer")
LAYOUT_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "rows": (("title", "sections"), ("header", "subtitle", "notes")),
    "spotlight": (("header", "figure", "headline"), ("lines",)),
    "pair": (("left", "right", "figure", "label"), ("lines",)),
    "bars": (("header", "title", "subtitle", "segments", "flow", "callout"), ()),
    "grid": (("title", "subtitle", "tiles"), ("more",)),
}
OPENING_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("kicker", "logos", "headline"), ("chip", "figure"))
#: Drop 2b: a pair's sides (the investee may have no logo key — a wordmark plate of its name) and a
#: grid tile, (required, optional) — template_onscreen's, pinned equal by tests/test_marketing_layouts_2b.py.
PAIR_SIDE_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "left": (("logo", "name"), ()),
    "right": (("name",), ("logo",)),
}
GRID_TILE_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("logo", "name"), ("line",))
#: app.schemas.marketing.ONSCREEN_TEXT_MAX: the most strings one asset may declare.
ONSCREEN_TEXT_MAX = 64
#: What `str.splitlines()` splits on, and a tab: a drawn string is one line (template_onscreen).
LINE_BREAKS = "\t\r\n\x0b\x0c\x1c\x1d\x1e\x85  "
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
#: The template opening card (drop 2a): the logo tile(s) step down with everything else.
OPENING_TILE_ONE = _Style(400, 240, 1.0)        # one company: its logo plate's side, px
OPENING_TILE_TWO = _Style(320, 200, 1.0)        # two companies, side by side
OPENING_TILE_GAP = 64                           # between two tiles
OPENING_LOGO_GAP = 56                           # tiles → the first line under them
OPENING_HEADLINE_STYLE = _Style(72, 40, 1.16)   # the headline (TEXT)
#: A logo plate: corner radius and inner padding as fractions of its shorter side; the padding
#: keeps the logo clear of the rounded corners (it exceeds radius × (1 − 1/√2)).
PLATE_RADIUS_RATIO = 0.14
PLATE_PAD_RATIO = 0.12
#: A wordmark (the company name drawn on a plate whose logo is missing): its largest size as a
#: fraction of the plate's shorter side, its line pitch, and the smallest LEGIBLE size. A name that
#: does not fit whole at that size (a long word on a small row plate) leaves the plate BLANK — the
#: row still names the company in its own cells — never a broken word, never a clipped one.
WORDMARK_START_RATIO = 0.2
WORDMARK_LEADING = 1.12
WORDMARK_MIN_PX = 9

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


def drawable_text(value: Any) -> bool:
    """Can `value` be drawn (and declared) as ONE on-screen string: a str, not blank, not padded,
    one line, at most MAX_STRING_CHARS. The worker's half of template_onscreen.drawable_problem —
    the server also checks canonical form and refuses links/hashes before it sends anything."""
    return (isinstance(value, str) and bool(value.strip()) and value == value.strip()
            and len(value) <= MAX_STRING_CHARS and not any(ch in LINE_BREAKS for ch in value))


@dataclass(frozen=True)
class LogoArt:
    """One company logo a template card or image may draw: `path` is a VERIFIED local file
    (logos.resolve_logos) drawn unaltered on a light plate, or None — then the plate carries the
    company `name` as a wordmark, and the name is a drawn (declared) string. `sha256` is the stored
    object's, set only when `path` is (it is part of the render keys)."""

    key: str
    name: str
    path: Optional[str] = None
    sha256: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not 1 <= len(self.key) <= LOGO_KEY_MAX_CHARS:
            raise ValueError(f"LogoArt.key must be a str of 1-{LOGO_KEY_MAX_CHARS} characters, not {self.key!r}")
        if not drawable_text(self.name):
            raise ValueError(f"LogoArt {self.key}: the company name is not one drawable string")
        if self.path is not None and not isinstance(self.path, str):
            raise TypeError(f"LogoArt.path must be a str or None, not {type(self.path).__name__}")
        if self.sha256 is not None and (not isinstance(self.sha256, str) or self.path is None):
            raise ValueError("LogoArt.sha256 is set only with a verified path")

    @property
    def wordmark(self) -> bool:
        return self.path is None


@dataclass(frozen=True)
class CardSpec:
    """One card. Which fields a kind draws (and requires) is fixed, and a field the kind does not
    draw must be empty — so `onscreen_strings` is always exactly what `render_card` draws:

    * brand      — nothing (draws the logo and WORDMARK)
    * text       — title and/or body; optional badge
    * stat       — figure (required), label, body; optional badge
    * disclaimer — body (required; the server's disclaimer card text; draws it + logo + CTA)
    * opening    — a template video's first card (drop 2a, `opening_card`): badge = kicker
      (required), `logos` = 1-2 company logos (a wordmark tile — the company name — for one that
      is not verified), label = chip, figure, title = headline (required). Drawn centred on the
      page, over the hook.
    """

    kind: str
    title: str = ""
    body: str = ""
    figure: str = ""
    label: str = ""
    badge: str = ""
    logos: Tuple[LogoArt, ...] = ()

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
            "opening": ("title", "figure", "label", "badge"),
        }[self.kind]
        stray = [n for n in ("title", "body", "figure", "label", "badge")
                 if n not in allowed and getattr(self, n)]
        if stray:
            raise ValueError(f"a {self.kind} card draws no {', '.join(stray)} (it would be silently dropped)")
        if not isinstance(self.logos, tuple) or not all(isinstance(a, LogoArt) for a in self.logos):
            raise TypeError("CardSpec.logos must be a tuple of LogoArt")
        if self.logos and self.kind != "opening":
            raise ValueError(f"a {self.kind} card draws no company logo")
        if self.kind == "text" and not (_present(self.title) or _present(self.body)):
            raise ValueError("a text card needs a title or a body")
        if self.kind == "stat" and not _present(self.figure):
            raise ValueError("a stat card needs a figure")
        if self.kind == "disclaimer" and not _present(self.body):
            raise ValueError("a disclaimer card needs its text (body)")
        if self.kind == "opening":
            if not (_present(self.badge) and _present(self.title)):
                raise ValueError("an opening card needs its kicker (badge) and its headline (title)")
            keys = [a.key for a in self.logos]
            if not 1 <= len(keys) <= MAX_OPENING_LOGOS or len(set(keys)) != len(keys):
                raise ValueError(f"an opening card draws 1-{MAX_OPENING_LOGOS} distinct company logos, "
                                 f"not {keys}")

    @property
    def logo_keys(self) -> Tuple[str, ...]:
        return tuple(a.key for a in self.logos)


def _script_str(value: Any, where: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string, not {type(value).__name__}")
    return value


def is_template(script: Dict[str, Any]) -> bool:
    """Is this a TEMPLATE (news) script — `authorship == TEMPLATE_AUTHORSHIP`? None or "ai" is a
    lesson (the writer's). Anything else is contract drift: ValueError, never a guess (a template
    image drawn with the lesson layout would draw its alt text)."""
    authorship = script.get("authorship") if isinstance(script, dict) else None
    if authorship is None or authorship == "ai":
        return False
    if authorship == TEMPLATE_AUTHORSHIP:
        return True
    raise ValueError(f"script.authorship {str(authorship)[:40]!r} is not 'ai' or {TEMPLATE_AUTHORSHIP!r}")


def logo_table(script: Dict[str, Any], resolved: Optional[Dict[str, Any]] = None) -> Dict[str, LogoArt]:
    """{key: LogoArt} for every well-formed entry of `script["logos"]` (`{key, name, url, sha256,
    …}`; a key of 1-LOGO_KEY_MAX_CHARS characters and a drawable company name; the first entry of a
    key wins — the server's own rule, template_onscreen.logo_keys). `resolved` is
    `logos.resolve_logos`'s answer: a key with a verified file draws its logo, every other key its
    wordmark. A malformed entry is skipped (WARNING): a spec that references it fails loudly later."""
    if not isinstance(script, dict):
        raise ValueError(f"script must be a dict, not {type(script).__name__}")
    raw = script.get("logos")
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise ValueError(f"script.logos must be a list, not {type(raw).__name__}")
    table: Dict[str, LogoArt] = {}
    for i, entry in enumerate(raw):
        key = entry.get("key") if isinstance(entry, dict) else None
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(key, str) or not 1 <= len(key) <= LOGO_KEY_MAX_CHARS or not drawable_text(name):
            logger.warning("script.logos[%d] is not a usable {key, name} entry — skipped", i)
            continue
        if key in table:
            continue
        path = (resolved or {}).get(key)
        sha = entry.get("sha256") if path else None
        table[key] = LogoArt(key=key, name=name, path=str(path) if path else None,
                             sha256=sha if isinstance(sha, str) else None)
    return table


def opening_spec(card: Any, logos: Dict[str, LogoArt]) -> CardSpec:
    """The template video's opening CardSpec from the script's `opening_card` (`{kicker, logos[1-2],
    chip?, figure?, headline}`, closed — the server validated it with
    template_onscreen.validate_opening_card): kicker → badge, chip → label, figure → figure,
    headline → title, each VERBATIM. ValueError on anything else (an unknown key, a logo key the
    script carries no entry for, a string that is not drawable) — contract drift, never guessed."""
    if not isinstance(card, dict):
        raise ValueError(f"script.opening_card must be an object, not {type(card).__name__}")
    required, optional = OPENING_KEYS
    unknown = [str(k)[:40] for k in card if k not in required + optional]
    if unknown:
        raise ValueError(f"script.opening_card has unknown key(s) {unknown}")
    for k in required:
        if card.get(k) is None:
            raise ValueError(f"script.opening_card has no {k}")
    texts: Dict[str, str] = {}
    for k in ("kicker", "chip", "figure", "headline"):
        value = card.get(k)
        if value is None:
            texts[k] = ""
            continue
        if not drawable_text(value):
            raise ValueError(f"script.opening_card.{k} is not one drawable string")
        texts[k] = value
    keys = card["logos"]
    if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_OPENING_LOGOS:
        raise ValueError(f"script.opening_card.logos must be a list of 1-{MAX_OPENING_LOGOS} keys")
    arts: List[LogoArt] = []
    for i, key in enumerate(keys):
        if not isinstance(key, str) or key not in logos:
            raise ValueError(f"script.opening_card.logos[{i}] names no logo entry of the script")
        if any(a.key == key for a in arts):
            raise ValueError(f"script.opening_card.logos[{i}] repeats {key!r}")
        arts.append(logos[key])
    return CardSpec("opening", badge=texts["kicker"], label=texts["chip"], figure=texts["figure"],
                    title=texts["headline"], logos=tuple(arts))


def cards_for_script(script: Dict[str, Any], logos: Optional[Dict[str, LogoArt]] = None) -> List[CardSpec]:
    """One text card per `script["cards"]` entry (title/body stripped; an entry with neither is
    skipped) + [disclaimer card, its text verbatim]. No brand card opens the video (drop 1): the
    first text card does, from frame 0 (`video.timeline(..., hook_card=False)`). Only when NO
    text card is left is a brand card put first — the narration needs a card under it, and the
    wordmark is the one string besides the script's that the server allows (VIDEO_BRAND_TEXT).
    ValueError when the disclaimer card is missing/empty — a video without its disclaimer must not
    render — or the script is malformed.

    A TEMPLATE script (drop 2a, `video_layout == "per_line"`) is [the opening card] + ONE text card
    per `cards[i]` — exactly one per narration line, so `len(cards) == len(video_script)` — +
    [disclaimer]; the render times it with `hook_card=True` (the opening over the hook, card i+1
    from line i+1). `logos` is its `logo_table` (None: every logo a wordmark). A template script
    without that layout, an unknown layout, a missing opening card or a card that draws nothing is
    a ValueError: a template video never falls back to the lesson shape."""
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
    layout = script.get("video_layout")
    template = is_template(script)
    if layout is not None and layout != VIDEO_LAYOUT_PER_LINE:
        raise ValueError(f"script.video_layout {str(layout)[:40]!r} is not {VIDEO_LAYOUT_PER_LINE!r}")
    if template and layout != VIDEO_LAYOUT_PER_LINE:
        raise ValueError(f"a template script needs video_layout {VIDEO_LAYOUT_PER_LINE!r}, not {layout!r}")
    if layout == VIDEO_LAYOUT_PER_LINE:
        table = logo_table(script) if logos is None else logos
        opening = opening_spec(script.get("opening_card"), table)
        lines = script.get("video_script")
        if not isinstance(lines, list) or len(lines) != len(raw_cards) or not raw_cards:
            raise ValueError(f"a per-line video needs one card per narration line: {len(raw_cards)} card(s) "
                             f"for {len(lines) if isinstance(lines, list) else '?'} line(s)")
        per_line: List[CardSpec] = [opening]
        for i, entry in enumerate(raw_cards):
            if not isinstance(entry, dict):
                raise ValueError(f"script.cards[{i}] must be an object, not {type(entry).__name__}")
            title = _script_str(entry.get("title"), f"script.cards[{i}].title").strip()
            body = _script_str(entry.get("body"), f"script.cards[{i}].body").strip()
            if not title and not body:
                raise ValueError(f"script.cards[{i}] draws nothing: a per-line video cannot skip a line's card")
            per_line.append(CardSpec("text", title=title, body=body))
        per_line.append(CardSpec("disclaimer", body=disclaimer))
        return per_line
    specs: List[CardSpec] = []
    for i, entry in enumerate(raw_cards):
        if not isinstance(entry, dict):
            raise ValueError(f"script.cards[{i}] must be an object, not {type(entry).__name__}")
        title = _script_str(entry.get("title"), f"script.cards[{i}].title").strip()
        body = _script_str(entry.get("body"), f"script.cards[{i}].body").strip()
        if not title and not body:
            logger.warning("script.cards[%d] has neither title nor body — no card drawn for it", i)
            continue
        specs.append(CardSpec("text", title=title, body=body))
    if not specs:
        # Never silent: the writer always emits three cards, so this is a malformed script that
        # still deserves a video (its narration and disclaimer are fine).
        logger.warning("script has no drawable text card — the video opens on the brand card instead")
        specs.append(CardSpec("brand"))
    specs.append(CardSpec("disclaimer", body=disclaimer))
    return specs


def onscreen_strings(spec: CardSpec) -> List[str]:
    """EXACTLY the strings `render_card` draws for `spec`, verbatim (the server compares them with
    the accepted script). A whitespace-only field is neither drawn nor returned.

    An opening card returns them in template_onscreen.opening_strings' order — kicker, the company
    name of each logo drawn as a WORDMARK (a verified logo draws no string), chip, figure,
    headline — so it is always a subset of the server's allow-list, and equal to it when no logo
    verified."""
    if spec.kind == "brand":
        fields = [WORDMARK]
    elif spec.kind == "text":
        fields = [spec.title, spec.body, spec.badge]
    elif spec.kind == "stat":
        fields = [spec.figure, spec.label, spec.body, spec.badge]
    elif spec.kind == "opening":
        fields = [spec.badge, *[a.name for a in spec.logos if a.wordmark], spec.label, spec.figure, spec.title]
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


@lru_cache(maxsize=512)
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
#: Drop 2a: the light plate every company logo (or its wordmark) sits on — the brand's white, so
#: a logo keeps its own colours unaltered — and the wordmark's ink on it (the page colour). The
#: opening card's chip is TEXT at 78 % over the page. Still four brand hexes and their blends:
#: no green or red anywhere (a verdict colour on a named company would be an opinion).
PLATE = TEXT
LOGO_INK = PAGE
CHIP_FILL = _mix(TEXT, PAGE, 0.78)


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
    #: Company logo plates (drop 2a): each plate's box and what it shows (a verified logo, or the
    #: company name as a wordmark — whose lines are in `lines`, field "wordmark:<key>").
    tiles: Tuple[Tuple[Box, "LogoArt"], ...] = ()

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
    if spec.kind == "opening":
        # kicker pill → the logo plate(s) → chip → figure → headline, centred on the page.
        st.badge(spec.badge)
        n = len(spec.logos)
        tile = (OPENING_TILE_ONE if n == 1 else OPENING_TILE_TWO).size(step)
        st.box("logos", n * tile + (n - 1) * OPENING_TILE_GAP, tile)
        after_logos = {"gap_px": OPENING_LOGO_GAP}
        if _present(spec.label):
            st.text("label", spec.label, LABEL_STYLE, CHIP_FILL, **after_logos)
            after_logos = {"gap_em": 0.3}
        if _present(spec.figure):
            st.text("figure", spec.figure, FIGURE_STYLE, _rgb(ACCENT), **after_logos)
            after_logos = {"gap_em": 0.35}
        st.text("title", spec.title, OPENING_HEADLINE_STYLE, _rgb(TEXT), **after_logos)
        return st, False
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
    tiles: List[Tuple[Box, LogoArt]] = []
    if spec.kind == "opening":
        row = boxes["logos"]
        side = row[3] - row[1]
        for i, art in enumerate(spec.logos):
            x0 = row[0] + i * (side + OPENING_TILE_GAP)
            tile_box: Box = (x0, row[1], x0 + side, row[3])
            tiles.append((tile_box, art))
            if art.wordmark:
                lines.extend(wordmark_lines(art, tile_box, font_path, engine))
    return CardLayout(kind=spec.kind, step=step, engine=engine, panel=panel, rule=boxes.get("rule"),
                      badge=boxes.get("badge"), logo=boxes.get("logo"), lines=tuple(lines),
                      tiles=tuple(tiles))


# ── company logo plates (drop 2a) ────────────────────────────────────────────


def plate_geometry(box: Box) -> Tuple[int, int]:
    """(corner radius, inner padding) of a logo plate drawn in `box`."""
    side = min(box[2] - box[0], box[3] - box[1])
    return max(2, int(round(side * PLATE_RADIUS_RATIO))), max(3, int(round(side * PLATE_PAD_RATIO)))


@lru_cache(maxsize=1024)
def _fit_wordmark(name: str, w: int, h: int, font_path: str, engine: str) -> Optional[Tuple[int, Tuple[str, ...], int]]:
    """(size, wrapped lines, line pitch) of the largest size, from WORDMARK_START_RATIO of the
    plate's shorter side down to WORDMARK_MIN_PX, at which `name` wraps whole (never a word
    broken) inside the w×h inner box — or None when it does not fit even at WORDMARK_MIN_PX."""
    size = max(WORDMARK_MIN_PX, int(round(min(w, h) / (1 - 2 * PLATE_PAD_RATIO) * WORDMARK_START_RATIO)))
    words = name.split()
    while size >= WORDMARK_MIN_PX:
        font = _font(font_path, size, engine)
        asc, desc = font.getmetrics()
        pitch = int(round(size * WORDMARK_LEADING))
        max_lines = 0 if h < asc + desc else (h - (asc + desc)) // pitch + 1
        if max_lines > 0:
            try:
                return size, tuple(_wrap(words, font, w, max_lines, "wordmark")), pitch
            except _NoFit:
                pass
        size = min(size - 1, int(size * 0.92))   # ≤ ~30 tries from the largest plate's start
    return None


def wordmark_lines(art: LogoArt, box: Box, font_path: str, engine: str) -> List[DrawnLine]:
    """The company name of `art` laid out on its plate `box` (centred, LOGO_INK), as DrawnLines of
    field "wordmark:<key>" whose ink stays inside the plate. [] — a BLANK plate — when the name
    does not fit whole at a legible size (WORDMARK_MIN_PX): the name may then be declared without
    being drawn (declared ⊇ drawn, the safe direction: never a drawn string that is not declared)."""
    _, pad = plate_geometry(box)
    inner: Box = (box[0] + pad, box[1] + pad, box[2] - pad, box[3] - pad)
    w, h = inner[2] - inner[0], inner[3] - inner[1]
    fit = _fit_wordmark(art.name, w, h, font_path, engine) if w > 0 and h > 0 else None
    if fit is None:
        return []
    size, wrapped, pitch = fit
    font = _font(font_path, size, engine)
    asc, desc = font.getmetrics()
    total = (len(wrapped) - 1) * pitch + asc + desc
    y0 = inner[1] + (h - total) // 2
    cx = (inner[0] + inner[2]) // 2
    out: List[DrawnLine] = []
    for i, text in enumerate(wrapped):
        y = y0 + i * pitch
        l, t, r, b = font.getbbox(text, anchor="ma")
        ink = (cx + l, y + t, cx + r, y + b)
        if not _within(ink, box):
            return []          # a glyph overhanging its advance past the plate: blank, never clipped
        out.append(DrawnLine(f"wordmark:{art.key}", text, size, cx, y, "ma", _rgb(LOGO_INK), ink))
    return out


def blank_wordmarks(layout: Any) -> List[str]:
    """The keys of the wordmark plates of a laid-out card or image that are drawn BLANK (the name
    did not fit legibly) — logged by the callers once the layout is final."""
    drawn = {ln.field for ln in getattr(layout, "lines", ())}
    return [art.key for _box, art in getattr(layout, "tiles", ()) if art.wordmark
            and f"wordmark:{art.key}" not in drawn]


def draw_plate(img, box: Box, art: LogoArt) -> None:
    """The light rounded plate in `box`, and on it the verified logo — scaled (LANCZOS) to fit the
    padded inner box, aspect kept, centred, alpha composited over the plate; never recoloured,
    cropped or otherwise altered. A wordmark plate is the plate alone (its lines are drawn with
    the card's text). CardAssetError when a verified logo file is unreadable now."""
    from PIL import Image

    w, h = box[2] - box[0], box[3] - box[1]
    radius, pad = plate_geometry(box)
    plate = Image.new("RGBA", (w, h), _rgb(PLATE) + (255,))
    if art.path is not None:
        try:
            with Image.open(art.path) as src:
                src.load()
                rgba = src.convert("RGBA")
        except Exception as e:  # noqa: BLE001 — re-raised typed, with the key (never the path's bytes)
            raise CardAssetError(f"logo {art.key} unreadable: {type(e).__name__}: {e}") from e
        lw, lh = rgba.size
        iw, ih = w - 2 * pad, h - 2 * pad
        scale = min(iw / lw, ih / lh)
        sw, sh = max(1, int(round(lw * scale))), max(1, int(round(lh * scale)))
        scaled = rgba.resize((sw, sh), Image.LANCZOS)
        plate.alpha_composite(scaled, (pad + (iw - sw) // 2, pad + (ih - sh) // 2))
    img.paste(plate.convert("RGB"), (box[0], box[1]), _rounded_mask(w, h, radius))


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
    blank = blank_wordmarks(layout)
    if blank:
        logger.warning("cards: the %s card draws a blank plate for %s (the name does not fit legibly)",
                       spec.kind, blank)
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
    for tile_box, art in layout.tiles:          # company logos on their plates (opening card)
        draw_plate(img, tile_box, art)
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


# ── the post image (drop 1, 2026-10-09) ──────────────────────────────────────

#: 4:5 — the one canonical render every image outlet takes (Instagram, Threads, Facebook,
#: LinkedIn, X, Bluesky, Telegram).
IMAGE_WIDTH, IMAGE_HEIGHT = 1080, 1350
#: Mirrors of app.schemas.marketing (POST_IMAGE_MAX_BYTES, POST_IMAGE_EXT, IMAGE_ROLE_POST,
#: IMAGE_POST_PARAGRAPHS_MIN/MAX; tests/test_marketing_cards.py pins them equal): the server
#: refuses a bigger, differently typed or differently shaped post image at registration.
POST_IMAGE_MAX_BYTES = 950_000
POST_IMAGE_EXT = "jpg"
IMAGE_ROLE_POST = "post_image"
IMAGE_PARAGRAPHS_MIN, IMAGE_PARAGRAPHS_MAX = 2, 4
#: The JPEG quality ladder, best first: the first step whose file is at most the cap wins. A
#: text card on flat colour is ~0.2-0.5 MB at the top step, so the ladder is a guard, not a dial.
JPEG_QUALITIES: Tuple[int, ...] = (92, 88, 84, 80, 75, 70, 65, 60)

IMAGE_MARGIN = 64                          # page margin on every side: nothing is drawn in it
IMAGE_PANEL_PAD_X, IMAGE_PANEL_PAD_Y = 64, 60
IMAGE_FOOTER_GAP = 32                      # between the panel's area and the footer
#: The most of the height the footer may take (it is two lines; a longer one steps down).
IMAGE_FOOTER_MAX_H = 240
#: Sized so the writer's maximum (a 70-character title, four 220-character paragraphs of long
#: words) fits one step above the floors with room to spare, on both layout engines (measured
#: 2026-10-09; tests/test_marketing_cards.py pins that it fits).
IMAGE_TITLE_STYLE = _Style(80, 40, 1.12)       # the title (ACCENT)
IMAGE_PARAGRAPH_STYLE = _Style(48, 26, 1.32)   # each paragraph (TEXT)
IMAGE_FOOTER_STYLE = _Style(30, 24, 1.3)       # the footer (TEXT at 72 % over PAGE)
IMAGE_TITLE_GAP_EM = 0.8                   # title → first paragraph
IMAGE_PARAGRAPH_GAP_EM = 0.75              # between paragraphs
FOOTER_FILL = _mix(TEXT, PAGE, 0.72)


class ImageTooLarge(Exception):
    """Even the lowest JPEG_QUALITIES step is over the byte cap. Never shipped bigger."""


@dataclass(frozen=True)
class ImageSpec:
    """The post image: the accepted output's `image_post` (a title and 2-4 paragraphs) and the
    server's code-owned `image_footer`. Every string is drawn and declared VERBATIM — never
    stripped or re-cased (the server compares them string for string); the wrap only re-flows
    whitespace when drawing."""

    title: str
    paragraphs: Tuple[str, ...]
    footer: str

    def __post_init__(self) -> None:
        for name in ("title", "footer"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"ImageSpec.{name} must be a str, not {type(getattr(self, name)).__name__}")
        if not isinstance(self.paragraphs, tuple):
            raise TypeError(f"ImageSpec.paragraphs must be a tuple, not {type(self.paragraphs).__name__}")
        if not _present(self.title):
            raise ValueError("the post image needs its title")
        if not _present(self.footer):
            raise ValueError("the post image needs its footer (it is the image's disclaimer)")
        if not IMAGE_PARAGRAPHS_MIN <= len(self.paragraphs) <= IMAGE_PARAGRAPHS_MAX:
            raise ValueError(f"the post image takes {IMAGE_PARAGRAPHS_MIN}-{IMAGE_PARAGRAPHS_MAX} "
                             f"paragraphs, not {len(self.paragraphs)}")
        for i, p in enumerate(self.paragraphs):
            if not isinstance(p, str):
                raise TypeError(f"ImageSpec.paragraphs[{i}] must be a str, not {type(p).__name__}")
            if not _present(p):
                raise ValueError(f"ImageSpec.paragraphs[{i}] is blank")


def image_for_script(script: Dict[str, Any]) -> Optional[ImageSpec]:
    """The worker script's `image_post` + `image_footer` as an ImageSpec, or None when the script
    carries no `image_post` (an older script, or a day whose image was dropped). ValueError when
    it is there but malformed, or has no footer — an image without its footer must not render."""
    if not isinstance(script, dict):
        raise ValueError(f"script must be a dict, not {type(script).__name__}")
    raw = script.get("image_post")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"script.image_post must be an object, not {type(raw).__name__}")
    paragraphs = raw.get("paragraphs")
    if not isinstance(paragraphs, list):
        raise ValueError(f"script.image_post.paragraphs must be a list, not {type(paragraphs).__name__}")
    footer = script.get("image_footer")
    if not isinstance(footer, str) or not _present(footer):
        raise ValueError("the script has an image_post but no image_footer: an image without its "
                         "footer must not render")
    try:
        return ImageSpec(title=raw.get("title"), paragraphs=tuple(paragraphs), footer=footer)  # type: ignore[arg-type]
    except TypeError as e:
        raise ValueError(f"script.image_post is malformed: {e}") from e


def image_onscreen_strings(spec: ImageSpec) -> List[str]:
    """EXACTLY the strings the post image draws, verbatim and in order — title, paragraphs, footer
    (a repeated string once): the image's `metadata.onscreen_text` and its alt text."""
    out: List[str] = []
    for s in (spec.title, *spec.paragraphs, spec.footer):
        if s not in out:
            out.append(s)
    return out


def check_image_glyphs(spec: ImageSpec, font_path: str) -> None:
    """MissingGlyphs naming every character of the image's strings the face lacks."""
    check_glyphs([], font_path, extra=image_onscreen_strings(spec))


def _place_image(spec: ImageSpec, step: int, font_path: str, engine: str) -> CardLayout:
    col_w = IMAGE_WIDTH - 2 * IMAGE_MARGIN
    # The footer first: it sits on the page at the bottom, and what it leaves is the panel's.
    foot = _Stack(col_w, IMAGE_FOOTER_MAX_H, "center", font_path, engine, step)
    foot.text("footer", spec.footer, IMAGE_FOOTER_STYLE, FOOTER_FILL)
    footer_top = IMAGE_HEIGHT - IMAGE_MARGIN - foot.y
    area_top, area_h = IMAGE_MARGIN, footer_top - IMAGE_FOOTER_GAP - IMAGE_MARGIN
    st = _Stack(col_w - 2 * IMAGE_PANEL_PAD_X, area_h - 2 * IMAGE_PANEL_PAD_Y, "left", font_path, engine, step)
    st.box("rule", RULE_W, RULE_H, lead_gap=RULE_GAP)
    st.text("title", spec.title, IMAGE_TITLE_STYLE, _rgb(ACCENT))
    for i, p in enumerate(spec.paragraphs):
        st.text(f"paragraph{i}", p, IMAGE_PARAGRAPH_STYLE, _rgb(TEXT),
                gap_em=IMAGE_TITLE_GAP_EM if i == 0 else IMAGE_PARAGRAPH_GAP_EM)
    panel_h = st.y + 2 * IMAGE_PANEL_PAD_Y
    top = area_top + (area_h - panel_h) // 2
    panel: Box = (IMAGE_MARGIN, top, IMAGE_WIDTH - IMAGE_MARGIN, top + panel_h)
    footer_zone: Box = (IMAGE_MARGIN, footer_top, IMAGE_WIDTH - IMAGE_MARGIN, IMAGE_HEIGHT - IMAGE_MARGIN)
    lines: List[DrawnLine] = []
    for stack, dx, dy, bounds in ((st, IMAGE_MARGIN + IMAGE_PANEL_PAD_X, top + IMAGE_PANEL_PAD_Y, panel),
                                  (foot, IMAGE_MARGIN, footer_top, footer_zone)):
        for name, text, size, x, y, anchor, fill in stack.lines:
            l, t, r, b = _font(font_path, size, engine).getbbox(text, anchor=anchor)
            ink = (x + dx + l, y + dy + t, x + dx + r, y + dy + b)
            if not _within(ink, bounds):
                raise _NoFit(f"{name}: the ink of {text[:40]!r} at {size}px leaves its area")
            lines.append(DrawnLine(name, text, size, x + dx, y + dy, anchor, fill, ink))
    rx0, ry0, rx1, ry1 = st.boxes["rule"]
    dx, dy = IMAGE_MARGIN + IMAGE_PANEL_PAD_X, top + IMAGE_PANEL_PAD_Y
    rule: Box = (rx0 + dx, ry0 + dy, rx1 + dx, ry1 + dy)
    if not _within(rule, panel) or footer_zone[1] < panel[3] + IMAGE_FOOTER_GAP or panel[1] < IMAGE_MARGIN:
        raise _NoFit("post image: the panel, its rule or the footer leave their areas")   # defence in depth
    return CardLayout(kind="post_image", step=step, engine=engine, panel=panel, rule=rule,
                      lines=tuple(lines))


def layout_image(spec: ImageSpec, *, font_path: str, layout_engine: Optional[str] = None) -> CardLayout:
    """Where everything on the post image goes: the largest ramp step at which the title, every
    paragraph and the footer fit whole. CardOverflow (with the floor step's reason) when none
    does, or when a string is over MAX_STRING_CHARS (refused before any measuring)."""
    engine = resolve_layout_engine(layout_engine)
    for s in image_onscreen_strings(spec):
        if len(s) > MAX_STRING_CHARS:
            raise CardOverflow(f"post image: a string of {len(s)} characters is over {MAX_STRING_CHARS} "
                               f"(the server refuses it): {s[:60]!r}…")
    reason = ""
    for step in range(RAMP_STEPS + 1):
        try:
            return _place_image(spec, step, font_path, engine)
        except _NoFit as e:
            reason = str(e)
    raise CardOverflow(f"post image does not fit at the floor sizes: {reason}")


def draw_image(spec: ImageSpec, *, font_path: str, layout_engine: Optional[str] = None):
    """(the post image as an RGB PIL image, its layout) — before any JPEG encoding, so tests can
    check its pixels exactly. CardOverflow / CardAssetError as for a card."""
    from PIL import Image, ImageDraw

    layout = layout_image(spec, font_path=font_path, layout_engine=layout_engine)
    img = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), _rgb(PAGE))
    if layout.panel:
        _fill_rounded(img, layout.panel, PANEL_RADIUS, _rgb(CARD))
    if layout.rule:
        _fill_rounded(img, layout.rule, RULE_H // 2, _rgb(ACCENT))
    draw = ImageDraw.Draw(img)
    for ln in layout.lines:
        draw.text((ln.x, ln.y), ln.text, font=_font(font_path, ln.size, layout.engine), fill=ln.fill,
                  anchor=ln.anchor)
    return img, layout


def encode_jpeg(img, quality: int) -> bytes:
    """Baseline (never progressive), 4:4:4, no ICC profile and no EXIF — deterministic bytes for
    the same pixels on the same libjpeg."""
    if isinstance(quality, bool) or not isinstance(quality, int) or not 1 <= quality <= 95:
        raise ValueError(f"quality must be an int 1-95, not {quality!r}")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, subsampling=0, progressive=False, optimize=False)
    return buf.getvalue()


@dataclass(frozen=True)
class RenderedImage:
    data: bytes
    quality: int                # the JPEG_QUALITIES step that fit the cap
    layout: CardLayout


def render_image(spec: ImageSpec, *, font_path: str, layout_engine: Optional[str] = None,
                 max_bytes: int = POST_IMAGE_MAX_BYTES) -> RenderedImage:
    """The post image as a baseline JPEG of at most `max_bytes`: the quality steps down
    JPEG_QUALITIES until it fits. ImageTooLarge when no step does (never a bigger file);
    CardOverflow when the text cannot fit whole; CardAssetError when the font is unreadable."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError(f"max_bytes must be a positive int, not {max_bytes!r}")
    img, layout = draw_image(spec, font_path=font_path, layout_engine=layout_engine)
    sizes: List[str] = []
    for quality in JPEG_QUALITIES:
        data = encode_jpeg(img, quality)
        if len(data) <= max_bytes:
            logger.debug("post image rendered step=%d engine=%s quality=%d bytes=%d",
                         layout.step, layout.engine, quality, len(data))
            return RenderedImage(data=data, quality=quality, layout=layout)
        sizes.append(f"q{quality}={len(data)}")
    raise ImageTooLarge(f"the post image is over {max_bytes} bytes at every JPEG quality step ({', '.join(sizes)})")


# ── the post image, either kind (drop 2a) ────────────────────────────────────


def render_post_image(script: Dict[str, Any], logos: Optional[Dict[str, LogoArt]] = None, *, font_path: str,
                      layout_engine: Optional[str] = None, max_bytes: int = POST_IMAGE_MAX_BYTES) -> RenderedImage:
    """The day's post image for `script`: a TEMPLATE script (`authorship == "template"`) draws its
    `image_spec` (news_layouts, a SHIPPED_LAYOUTS layout; any other or a missing layout is a
    ValueError — never the lesson layout, which would draw the alt text), with `logos` its
    `logo_table` (None: every logo a wordmark); any other script draws the drop-1 lesson image of
    its `image_post` (ValueError when it carries none)."""
    if is_template(script):
        from marketing import news_layouts

        image = news_layouts.template_image_for_script(script, logo_table(script) if logos is None else logos)
        return news_layouts.render_template_image(image, font_path=font_path, layout_engine=layout_engine,
                                                  max_bytes=max_bytes)
    spec = image_for_script(script)
    if spec is None:
        raise ValueError("the script carries no image_post: there is no post image to draw")
    return render_image(spec, font_path=font_path, layout_engine=layout_engine, max_bytes=max_bytes)
