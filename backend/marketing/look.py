"""
The approved Caydex LOOK (owner 2026-11-19: "that's perfect!!! i love these style." / "i need exactly these
styles, colors, words") — the palette and the drawing primitives every post picture (`news_layouts.py`, the lesson
image in `cards.py`) and every video card (`cards.py`) is built from. Ported from the design kit that drew the
approved mocks (`out/marketing_v2_preview/FINAL_REFERENCE/scripts/style_kit.py`): a navy→black vertical gradient
with a soft gold glow top-right, the Caydex brand row (the vendored logo tile + the word "Caydex") top-left with the
gold-on-navy kicker pill right-aligned on the same row, a two-tone UPPERCASE headline (white, then gold), warm-white
rounded cards, ticker chips, real company logos on a white disc — or on a DARK disc when the logo is made for a dark
background (`logos.disc_tone`) — and one short grey footer line.

Colour rules (text-safe on the warm card): GREEN = a purchase, an increase, a new holding, a profit, a positive
surprise; RED = a sale, a decrease, a holding closed out, a loss, a negative surprise; GOLD / GOLD_INK = neutral
emphasis (a stake's amount, a theme's share); everything else INK / INK2 on a card, WHITE / SUB / GREY on the page.

Everything here is deterministic for the same inputs, Pillow and layout engine: the canvas is built from fixed
integer geometry (and cached as bytes), every curved edge is drawn supersampled at cards._AA× and box-reduced, every
text line is a cards.DrawnLine whose ink box is checked inside the region it belongs to. Inter Bold only (the one
face the worker ships). Pure apart from Pillow and fontTools (imported inside functions); nothing here imports app.*.
"""

from __future__ import annotations

import dataclasses
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from marketing import cards
from marketing.cards import Box, DrawnLine, LogoArt, _font, _NoFit, _Style, _within

RGB = Tuple[int, int, int]

# ── palette (style_kit.py, verbatim) ──────────────────────────────────────────

NAVY_TOP: RGB = (14, 24, 48)
NAVY_BOT: RGB = (6, 9, 18)
GLOW: RGB = (60, 50, 10)
GOLD: RGB = (246, 196, 69)
WHITE: RGB = (255, 255, 255)
SUB: RGB = (203, 213, 225)          # a sub line on the dark page
CARD: RGB = (250, 248, 242)         # the warm white card
CARD_EDGE: RGB = (226, 232, 240)    # a logo disc's rim
INK: RGB = (17, 24, 39)             # text on a card
INK2: RGB = (75, 85, 99)            # secondary text on a card (7.0:1 on CARD)
GREEN: RGB = (21, 128, 61)          # text-safe on CARD (5.0:1)
RED: RGB = (185, 28, 28)            # text-safe on CARD (6.4:1)
GOLD_INK: RGB = (146, 100, 0)       # gold that reads on the card (5.1:1)
NAVY_CHIP: RGB = (17, 24, 39)       # a ticker chip; the DARK logo disc
PILL: RGB = (40, 52, 84)            # the kicker pill on the dark page
GREY: RGB = (156, 163, 175)         # the footer and column labels on the dark page
BAR_TRACK: RGB = (229, 231, 235)    # the empty part of a bar on a card
BAR_FILL: RGB = (30, 58, 138)       # a neutral bar on a card
NOTE_PANEL: RGB = (24, 34, 60)      # the notes' lifted dark panel (SUB text on it)
TILE: RGB = (240, 236, 227)         # a stat tile on the card (INK / INK2 on it)
DIVIDER: RGB = (226, 222, 212)      # a hairline on the card
FACT_DIVIDER: RGB = (226, 221, 208)  # the feature card's hairlines (mock_spotlight.DIVIDER)
PALE_RED: RGB = (254, 226, 226)     # the track behind a loss bar (graphic only, never behind text)
BRAND_OUTLINE: RGB = (71, 85, 105)  # the brand tile's rim
#: news-v23 — the earnings table and the Congress Count (mock_earnings_multi.py / mock_congress_multi.py): the
#: estimate's recessed pill (INK / INK2 on it), a positive surprise's tint (GREEN on it), a negative one's (RED on it)
#: — each ≥ 4.5:1 with its ink (the mock's own contrast asserts, pinned in tests/test_marketing_cards.py); the
#: Congress count's tile is the GREEN tint, its figure GREEN.
PILL_LIGHT: RGB = (238, 234, 223)
#: Owner decisions 2026-11-20 (the insider list, mock_insider_multi.py / r6_kit.DETAIL): a row's small grey DETAIL line
#: on the warm card ("28K shares · $71.20–$71.80 a share, as filed") — ≥ 4.5:1 on CARD (the mock's own contrast
#: assert, pinned in tests/test_marketing_news_layouts.py).
DETAIL: RGB = (99, 107, 120)
GREEN_TINT: RGB = (234, 248, 238)
RED_TINT: RGB = (253, 234, 234)
#: Owner decision 2026-11-20 ("A: costs vs revenue", money_map_loss_A_rivian.jpg / mock_money_map_loss.py): a Money Map
#: LOSS year's costs bar — cost of sales in RED, operating expenses in COST_MID_RED, interest / tax / other items (when
#: they are a COST) in COST_MAROON — beside the navy (BAR_FILL) revenue bar. Graphic roles on the warm card (every value
#: is also printed as text in the legend); the mock's own colours, verbatim.
COST_MID_RED: RGB = (232, 96, 96)
COST_MAROON: RGB = (127, 29, 29)
#: The Money Map's stacked-bar parts, in bar order (mock_money_map.SEG_COLORS: chosen to stay apart for protan /
#: deutan / tritan viewers; no red — red is a loss; every value is also in the legend as text).
SEG_COLORS: Tuple[RGB, ...] = ((21, 128, 61), (37, 99, 235), (208, 144, 0), (170, 70, 150), (56, 170, 230),
                               (55, 65, 81))

#: The vendored brand logo (the worker image copies `marketing/`), drawn in the brand row of every surface.
BRAND_LOGO_PATH = str(Path(__file__).resolve().parent / "assets" / "brand" / "caydex-logo.png")
#: Mirrors app.schemas.marketing.BRAND_ROW_WORD / LESSON_KICKER and post_copy.IMAGE_FOOTER_HEAD (pinned equal by
#: tests/test_marketing_cards.py): the brand row's word, the lesson's kicker pill, the lesson's footer.
BRAND_ROW_WORD = "Caydex"
LESSON_KICKER = "INVESTING LESSON"
LESSON_FOOTER = "Not investment advice"

# ── geometry shared by every surface ──────────────────────────────────────────

GLOW_BLUR = 120
#: The glow ellipse relative to the brand row's top y and the canvas' right edge (the kit's [W-520, -260, W+260,
#: 420] for a brand row at y 48).
GLOW_DX0, GLOW_DX1, GLOW_DY0, GLOW_DY1 = -520, 260, -308, 372
BRAND_TILE_PX = 52
BRAND_GAP = 14                     # the tile → the word
BRAND_WORD_RATIO = 0.62            # the word's size, of the tile's side
KICKER_PX = 26
KICKER_FLOOR_PX = 22
KICKER_PAD_X, KICKER_PAD_Y, KICKER_RADIUS = 14, 9, 10
KICKER_DY = 6                      # the pill's top under the brand row's top
FOOTER_PX, FOOTER_FLOOR_PX = 24, 22
FOOTER_LEADING = 1.3
CARD_RADIUS = 26
CHIP_PAD_X, CHIP_PAD_Y, CHIP_RADIUS = 12, 8, 9
#: The kit's chip (style_kit.chip): the box is `size + 2 × CHIP_PAD_Y` tall (size + 16) and its text is drawn at the
#: box top + CHIP_TEXT_DY with the ascender anchor ("la") — not the font's ascent + descent (6 px taller at 22 px).
CHIP_TEXT_DY = 6
DISC_RIM = 3
#: A logo is scaled so its farthest SOLID pixel (alpha ≥ 128) from its centre sits at this share of the disc's radius
#: (`_reach`): a square mark fills ~0.62 of the diameter, a wide wordmark ~0.8, a round seal ~0.88 — the per-logo
#: fills the approved mocks were tuned to, from one rule. The reach is measured on the logo's MAIN mass
#: (`_main_reach`): a detached mark smaller than LOGO_MINOR_MARK_SHARE of its solid cells (a ® or ™ beside a seal)
#: does not set it — it is still drawn whole inside the disc (its farthest solid pixel at most LOGO_EDGE_CLEAR px
#: inside the edge; it may sit on the rim, as the mock's Starbucks ® does).
LOGO_REACH = 0.86
LOGO_MINOR_MARK_SHARE = 0.01
LOGO_EDGE_CLEAR = 1
#: A DIAMOND-shaped mark (`_diamond_like`: its points on the bbox's edge midpoints — Rivian's) has its long side capped
#: at this share of the disc's diameter (mock_money_map LOGO_FILL["RIVN"] = 0.70: at 0.84 / 0.76 its side points sat
#: 5-10 px from the disc's edge). Round seals, squares and wordmarks are not diamonds and keep the reach rule.
DIAMOND_FILL = 0.70
#: news-v23 — the approved mock_f13_lists `real_logo` sizing for a logo served on an opaque plate: on a LIGHT plate
#: (`logos.light_plate`) the disc takes the plate's colour and the logo's MARKS reach PLATE_REACH of the radius, a
#: long low wordmark up to PLATE_REACH_LONG_BONUS more (aspect 2 → 4); an opaque DARK tile (Berkshire's navy square)
#: is the logo itself, its corners at TILE_REACH. Either is clipped to the disc (only plate margin is ever cut).
PLATE_REACH = 0.80
PLATE_REACH_LONG_BONUS = 0.10
TILE_REACH = 0.92
#: news-v23 (visual-fidelity round, 2026-11-20): a DARK IMAGE TILE (`logos.dark_image_tile`: an opaque, about-square
#: dark picture that is not one flat plate — Rapid7's) IS the disc: scaled so its longer side is IMAGE_TILE_COVER of
#: the disc's diameter (mock_theme_insiders FILL["RPD"] = 1.06, `circle_tile`) and clipped to it, its MARKS never past
#: the rim's inner edge (r − DISC_RIM: a large mark scales the tile down) — and when that leaves the disc uncovered
#: the tile is drawn as before (whole on the white disc), never with a mark under the rim.
IMAGE_TILE_COVER = 1.06
GOLD_RING_GAP, GOLD_RING_W = 9, 3  # the plate's gold ring: its radius past the disc's, its stroke
SOLID_RING_W = 5                   # the feature's solid gold band ring (mock_spotlight.logo_badge: r + 5)
#: The two-tone headline: at most this many lines, the line pitch, and the narrowing step of the balanced wrap.
HEADLINE_MAX_LINES = 4
HEADLINE_LEADING = 1.08
BALANCE_STEP = 16
#: A headline's GOLD part: the trailing phrase of the templates' own words that each family ends on (mock_form4 /
#: mock_f13_lists / mock_money_map), matched at the END of the string, case-insensitively — else (2026-11-20: the
#: lesson's "A MOODY PARTNER OFFERS YOU A PRICE" / "EVERY DAY…" and "MR." / "MARKET'S MOOD" split mid-phrase) the
#: part after a SENTENCE boundary, else after a CLAUSE boundary (`two_tone`), else no split at all — the headline is
#: then ONE block whose LAST line is gold (`headline`). A drawing rule only: every word is drawn, in order.
GOLD_TAILS: Tuple[str, ...] = ("buying their own company's stock", "disclosed buying stock", "latest 13F",
                               "makes money", "analyst estimates", "disclosed purchases",
                               # news-v23: company stakes (stakes_nvidia.jpg, "NVIDIA'S STAKES" + "IN PUBLIC COMPANIES")
                               "in public companies")
#: Words ending in "." that never END a sentence (an honorific, an abbreviation): a two-tone split never follows one
#: ("MR." / "MARKET'S MOOD"). Matched case-insensitively on the word without its trailing quotes or brackets; a
#: single-letter initial ("J.") and a dotted acronym ("U.S.", "e.g.") never end one either (`_SENTENCE_END_RE`).
NOT_SENTENCE_ENDS: FrozenSet[str] = frozenset({
    "mr.", "mrs.", "ms.", "mx.", "dr.", "prof.", "st.", "sr.", "jr.", "rev.", "gen.", "sen.", "rep.", "gov.", "pres.",
    "capt.", "lt.", "col.", "sgt.", "mt.", "ft.", "ave.", "blvd.", "no.", "nos.", "vs.", "etc.", "approx.", "est.",
    "inc.", "co.", "corp.", "ltd.", "llc.", "plc.", "dept.", "fig.", "vol.", "jan.", "feb.", "mar.", "apr.", "jun.",
    "jul.", "aug.", "sep.", "sept.", "oct.", "nov.", "dec.",
})
#: A word that ends a sentence: its last letter-run is followed by ".", "!" or "?" (then only closing quotes or
#: brackets) — never an ellipsis ("…" / "..."), which reads as a pause inside the sentence (a clause boundary).
_SENTENCE_END_RE = re.compile(r".*[^.](?:[.!?])[\"'”’)\]]*")
_DOTTED_ABBREV_RE = re.compile(r"(?:[A-Za-z]\.){2,}|[A-Za-z]\.")
#: A word that ends a CLAUSE: a comma, semicolon, colon, dash or ellipsis at its end (then closing quotes or
#: brackets); a dash standing alone is a clause boundary after it.
_CLAUSE_END_RE = re.compile(r".*(?:[,;:—–]|…|\.\.\.)[\"'”’)\]]*")
_LONE_DASHES = frozenset({"—", "–", "-", "--"})
#: An acronym plural keeps its lower-case s when the headline is upper-cased ("CEOs", never "CEOS").
_ACRONYM_PLURAL = re.compile(r"[A-Z0-9]{2,}s")

#: The most lines one wrapped string may take before it counts as not fitting (bounds the work).
MAX_WRAP_LINES = 8


@dataclass(frozen=True)
class Frame:
    """Where a surface is drawn: its `canvas` (w, h), the BRAND ROW's top-left (x, y) and its right edge
    (`right`: the kicker pill's right side), the content `area` (x0, y0, x1, y1) — the headline at its top, every
    card inside it, never below its bottom nor below the footer's top less its gap — and the footer's bottom edge (`footer_bottom`; the footer is centred on the canvas, inside
    [x0, x1] of `footer_span`). `name` prefixes a layout's kind ("template:rows", "data:rows")."""

    name: str
    canvas: Tuple[int, int]
    brand: Tuple[int, int]
    right: int
    area: Box
    footer_bottom: int
    footer_span: Tuple[int, int]
    #: The card edge's inset from the area's sides (the cards are wider than the text column).
    card_inset: int = 0
    #: The brand row's sizes on this surface (style_kit.brand / kicker_right): the logo tile's side, the word's size
    #: (int(side × 0.62), as the kit draws it) and the kicker pill's text size (stepping down to KICKER_FLOOR_PX).
    brand_px: int = BRAND_TILE_PX
    brand_word_px: int = int(BRAND_TILE_PX * BRAND_WORD_RATIO)
    kicker_px: int = KICKER_PX
    #: The gold glow's ellipse top and bottom y (its x span is GLOW_DX0 / GLOW_DX1 from the right edge).
    glow_y: Tuple[int, int] = (-260, 420)


#: The 4:5 post image (1080×1350): the kit's MARGIN 56 card edge, TEXT_X 64 text column, the headline at y 140.
POST_FRAME = Frame(name="template", canvas=(1080, 1350), brand=(64, 48), right=1080 - 64,
                   area=(56, 140, 1080 - 56, 1350 - 34), footer_bottom=1350 - 34,
                   footer_span=(60, 1080 - 60), card_inset=0)
#: A 9:16 video card (1080×1920), cover_13f_berkshire.jpg (mock_f13_lists.render_cover): the brand row 140 px from
#: the top (clear of the platforms' top chrome) at the mock's sizes — a 56 px tile, a 34 px word, a 28 px kicker —
#: on the TEXT column (the area's side + 8, as the mock's CTEXT_X = CSIDE + 8 and the picture's 64 = 56 + 8: the
#: brand mark, the kicker's right edge and the headline line up), and the glow lowered with it (−120…560); the
#: content in cards.SAFE_X × [the brand row's foot + 36, cards.TEXT_ZONE_Y[1]] (clear of the burned captions), the
#: footer under the caption band and inside the safe bottom (nothing below y 1500).
#: Deliberately NOT the mock's 90…990 card edge: the cards keep the caption-safe 80…1000 column (10 px wider a side)
#: because the SERVER's measured fit bounds (the 20-letter name word, the widest cover chip, the 13F and Congress
#: worst cases — tests/test_marketing_news_layouts.py lays them out with this column) were sized on it; at 90…990
#: those worst cases no longer fit and a legitimate day would be skipped (`unrenderable_text`).
VIDEO_SAFE_BOTTOM = 1920 - 420
VIDEO_BRAND_X = cards.SAFE_X[0] + 8
VIDEO_BRAND_PX = 56
VIDEO_FRAME = Frame(name="data", canvas=(1080, 1920), brand=(VIDEO_BRAND_X, 140), right=1080 - VIDEO_BRAND_X,
                    area=(cards.SAFE_X[0], 140 + VIDEO_BRAND_PX + 36, cards.SAFE_X[1], cards.TEXT_ZONE_Y[1]),
                    footer_bottom=VIDEO_SAFE_BOTTOM - 24, footer_span=cards.SAFE_X, card_inset=0,
                    brand_px=VIDEO_BRAND_PX, brand_word_px=int(VIDEO_BRAND_PX * BRAND_WORD_RATIO), kicker_px=28,
                    glow_y=(140 - 260, 140 + 420))


# ── drawn primitives (besides text: cards.DrawnLine) ──────────────────────────


@dataclass(frozen=True)
class Rect:
    box: Box
    radius: int
    fill: RGB
    outline: int = 0          # > 0: draw only the stroke, this many px wide


@dataclass(frozen=True)
class Ellipse:
    box: Box
    fill: Optional[RGB]
    outline: Optional[RGB] = None
    width: int = 0


@dataclass(frozen=True)
class Stack:
    """ONE stacked bar: `box`, its parts' widths (px, left to right, summing to the box's width) and colours, a
    `seam` px gap of `bg` between two parts, both ends rounded.

    Owner decision 2026-11-20 (the Money Map loss year, money_map_loss_A_rivian.jpg) adds three optional fields, each
    defaulting to what every stack drew before (its bytes unchanged): `radius` — the ends' corner radius (None: a pill,
    half the height); `hatch` — the span (x0, x1), px from the box's left, drawn as diagonal stripes of each part's own
    colour on `bg` (the costs the interest, tax and other INCOME covered; the legend swatch); `outline` — a stroke
    (colour, width) around the box, drawn over it (the swatch's rim)."""

    box: Box
    widths: Tuple[float, ...]
    colours: Tuple[RGB, ...]
    seam: int
    bg: RGB
    radius: Optional[int] = None
    hatch: Optional[Tuple[float, float]] = None
    outline: Optional[Tuple[RGB, int]] = None


@dataclass(frozen=True)
class Disc:
    """A company logo disc: `box` (a square), what it shows (`art`: a verified logo, or the company name as a
    wordmark — its lines are in the layout's `lines`, field "wordmark:<key>"), and an optional gold `ring`."""

    box: Box
    art: LogoArt
    ring: bool = False
    #: The page colour cut out around a ring that sits on an art band's edge (None: no cut-out).
    cutout: Optional[RGB] = None
    #: The ring drawn as the feature's SOLID gold band (mock_spotlight.logo_badge: a gold ellipse 5 px past the disc,
    #: touching it) rather than the plate's thin stroke after a gap — the cut-out ring always is.
    solid_ring: bool = False


# ── fonts, words, cases ───────────────────────────────────────────────────────


@lru_cache(maxsize=4)
def _cmap(font_path: str) -> frozenset:
    from fontTools.ttLib import TTFont

    try:
        return frozenset((TTFont(font_path, lazy=True).getBestCmap() or {}).keys())
    except Exception as e:  # noqa: BLE001 — re-raised typed, with the path
        raise cards.CardAssetError(f"font {font_path!r} cmap unreadable: {type(e).__name__}: {e}") from e


def caps(text: str, font_path: str) -> str:
    """`text` UPPER-CASED for a headline: every character's single-character upper case the face can draw (a
    character whose upper case is several characters, or one the face lacks, is kept as it is — never tofu), and
    an acronym plural keeps its lower-case s ("CEOs"). Word for word the same words: the declared string is the
    original; this is how it is DRAWN (like a wrap)."""
    cmap = _cmap(font_path)

    def up(ch: str) -> str:
        u = ch.upper()
        return u if len(u) == 1 and ord(u) in cmap else ch

    words = []
    for w in text.split(" "):
        words.append(w if _ACRONYM_PLURAL.fullmatch(w) else "".join(up(c) for c in w))
    return " ".join(words)


def _ends_sentence(word: str) -> bool:
    """Does `word` end a sentence ("day.", "it!", "why?\u201d") — never an honorific or abbreviation ("Mr.", "St.",
    "Inc."), an initial ("J.") or a dotted acronym ("U.S.", "e.g."), never an ellipsis."""
    if word.endswith(("…", "...")) or not _SENTENCE_END_RE.fullmatch(word):
        return False
    bare = word.rstrip("\"'”’)]")
    if bare.endswith(("!", "?")):
        return True
    return bare.casefold() not in NOT_SENTENCE_ENDS and not _DOTTED_ABBREV_RE.fullmatch(bare.lstrip("(\"'“‘["))


def _ends_clause(word: str) -> bool:
    """Does `word` end a clause (a comma, semicolon, colon, dash or ellipsis at its end), or is it a dash alone?"""
    return word in _LONE_DASHES or bool(_CLAUSE_END_RE.fullmatch(word))


def _boundary(words: Sequence[str]) -> Optional[int]:
    """Where a headline's gold part starts (an index into `words`, 1..len − 1): after its SENTENCE boundary nearest the
    middle of its characters (the later one on a tie), else after its CLAUSE boundary nearest it; None when it has
    neither (never after an honorific: `_ends_sentence`)."""
    n = len(words)
    total = sum(len(w) for w in words) + n - 1
    for test in (_ends_sentence, _ends_clause):
        best: Optional[Tuple[float, int]] = None
        left = 0
        for i in range(n - 1):
            left += len(words[i]) + (1 if i else 0)
            if not test(words[i]):
                continue
            cost = abs(left - (total - left - 1))
            if best is None or cost <= best[0]:
                best = (cost, i + 1)
        if best is not None:
            return best[1]
    return None


def _two_tone_parts(text: str) -> Tuple[str, str, bool]:
    """(white part, gold part, ONE block?) — `two_tone`'s split, and whether the text has no boundary at all (the
    headline is then one block whose last line is gold). A GOLD_TAILS phrase is a boundary even when it is the whole
    text (the gold part then is all of it, every line gold, as before)."""
    words = text.split()
    low = " ".join(words).casefold()
    for tail in GOLD_TAILS:
        t = tail.casefold()
        if low.endswith(t) and (len(low) == len(t) or low[-len(t) - 1] == " "):
            k = len(t.split())
            return " ".join(words[:-k]), " ".join(words[-k:]), False
    cut = _boundary(words)
    if cut is None:
        return "", " ".join(words), True
    return " ".join(words[:cut]), " ".join(words[cut:]), False


def two_tone(text: str) -> Tuple[str, str]:
    """(white part, gold part) of a headline, verbatim words: the GOLD_TAILS phrase it ends on; else (2026-11-20) the
    part after a sentence boundary, else after a clause boundary (`_boundary`: never after "Mr." or "U.S."); else
    ("", the whole text) — no good boundary: `headline` draws it as ONE block whose LAST line is gold (a one-line
    headline, and a one-word one, all gold)."""
    white, gold, _block = _two_tone_parts(text)
    return white, gold


def wrap(words: Sequence[str], font: Any, width: float, max_lines: int, what: str) -> List[str]:
    """The greedy word wrap (`cards._wrap`) that never breaks inside a DATE (`date_units`: "Dec 26, 2025" stays on
    one line — visual-fidelity round 2026-11-20, a stake's card read "… as of Dec 26," / "2025"); when a date unit
    cannot be kept whole (wider than the column, or the lines would exceed `max_lines`) the plain words are wrapped
    instead — the rule never refuses a surface. _NoFit as `cards._wrap`."""
    units = date_units(words)
    if len(units) < len(words):
        try:
            return cards._wrap(units, font, width, max_lines, what)
        except _NoFit:
            pass
    return cards._wrap(words, font, width, max_lines, what)


#: A DATE is never broken across lines (visual-fidelity round 2026-11-20): a month name (or its abbreviation,
#: capitalised or upper case — never "may" or "march" the verbs — an opening bracket or quote allowed before it), then a day ("26,", a range "16–17,", a cross-month range
#: "27–Nov" "24,"), then a year — or a month and a year ("November 2026"). `date_units` joins each into one unit.
_MONTH_WORD = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
               r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DAY_NUMBER = r"(?:[1-9]|[12][0-9]|3[01])"
_DATE_MONTH_RE = re.compile(r"[(\[\"'“‘]*" + _MONTH_WORD, re.IGNORECASE)
_DATE_DAY_TO_MONTH_RE = re.compile(_DAY_NUMBER + r"[–-]" + _MONTH_WORD, re.IGNORECASE)
_DATE_DAY_RE = re.compile(_DAY_NUMBER + r"(?:[–-]" + _DAY_NUMBER + r")?[)\]\"'”’.,;:]*")
_DATE_YEAR_RE = re.compile(r"(?:19|20)[0-9]{2}[)\]\"'”’.,;:!?]*")


def _capitalised(word: str) -> bool:
    """Is the first LETTER of `word` upper case ("Dec", "(DEC", "27–Nov")?"""
    return next((c.isupper() for c in word if c.isalpha()), False)


def _date_span(words: Sequence[str], i: int) -> int:
    """How many words from `words[i]` make one DATE (0: none starts there)."""
    n = len(words)
    if not _DATE_MONTH_RE.fullmatch(words[i]) or not _capitalised(words[i]):
        return 0
    j = i + 1
    while j < n and _DATE_DAY_TO_MONTH_RE.fullmatch(words[j]) and _capitalised(words[j]):
        j += 1
    if j < n and _DATE_DAY_RE.fullmatch(words[j]):
        j += 1
    if j < n and _DATE_YEAR_RE.fullmatch(words[j]):
        j += 1
    return j - i if j - i >= 2 else 0


def date_units(words: Sequence[str]) -> List[str]:
    """`words` with each DATE joined into ONE unit (its words space-joined — the same text a line holds), every other
    word its own unit."""
    out: List[str] = []
    i, n = 0, len(words)
    while i < n:
        span = _date_span(words, i)
        if span:
            out.append(" ".join(words[i:i + span]))
            i += span
        else:
            out.append(words[i])
            i += 1
    return out


#: Phrases a balanced headline keeps WHOLE on one line (mock_spotlight's hand-set breaks: "Contoso's CEO and" /
#: "at least 2 other insiders" — never "… AND AT" / "LEAST 2 …"): "at least" and the word after it. Matched on the
#: words case-insensitively; a drawing rule only (every word is drawn, in order).
NO_BREAK_LEADS: Tuple[Tuple[str, ...], ...] = (("at", "least"),)


def no_break_units(words: Sequence[str]) -> List[str]:
    """`words` with each DATE (`date_units`) and each NO_BREAK_LEADS phrase with the word after it joined into ONE unit
    (its words space-joined — the same text a line holds), every other word its own unit."""
    out: List[str] = []
    i, n = 0, len(words)
    while i < n:
        span = _date_span(words, i)
        if span:
            out.append(" ".join(words[i:i + span]))
            i += span
            continue
        for lead in NO_BREAK_LEADS:
            k = len(lead)
            if i + k < n and tuple(w.casefold() for w in words[i:i + k]) == lead:
                out.append(" ".join(words[i:i + k + 1]))
                i += k + 1
                break
        else:
            out.append(words[i])
            i += 1
    return out


def _min_max_split(units: Sequence[str], font: Any, n: int) -> Optional[List[str]]:
    """`units` split into exactly `n` lines whose widest is the narrowest possible (what the narrowing search finds
    on plain words). None when `n` > the units. Exact (a DP over the cut points; n ≤ MAX_WRAP_LINES, units ≤ a
    headline's words)."""
    u = len(units)
    if n > u:
        return None
    widths = {}

    def w(i: int, j: int) -> float:
        if (i, j) not in widths:
            widths[(i, j)] = font.getlength(" ".join(units[i:j]))
        return widths[(i, j)]

    inf = float("inf")
    # best[k][i]: the narrowest widest line splitting units[:i] into k lines, and the cut before line k
    best = [[(inf, -1)] * (u + 1) for _ in range(n)]
    for i in range(1, u + 1):
        best[0][i] = (w(0, i), 0)
    for k in range(1, n - 1):
        for i in range(k + 1, u + 1):
            out = (inf, -1)
            for j in range(k, i):
                cand = max(best[k - 1][j][0], w(j, i))
                if cand < out[0]:
                    out = (cand, j)
            best[k][i] = out
    if n == 1:
        return [" ".join(units)]
    choice: Tuple[float, int] = (inf, -1)
    for j in range(n - 1, u):
        cand = max(best[n - 2][j][0], w(j, u))
        if cand < choice[0]:
            choice = (cand, j)
    cuts = [u]
    j, k = choice[1], n - 2
    while k >= 0:
        cuts.append(j)
        j = best[k][j][1]
        k -= 1
    cuts.append(0)
    cuts = sorted(set(cuts))
    return [" ".join(units[a:b]) for a, b in zip(cuts, cuts[1:])]


#: A balanced headline's last line is an ORPHAN when it is one word (or one NO_BREAK unit) narrower than this share of
#: the widest line ("… AND AT LEAST ONE OTHER" / "INSIDER"); a full word ("AT LEAST 3 CEOs" / "DISCLOSED", 0.66 of
#: it — mock_form4's own break) is not.
ORPHAN_SHARE = 0.5


def _narrowest(words: Sequence[str], font: Any, width: float, max_lines: int, what: str) -> List[str]:
    """The greedy wrap's line count, at the narrowest width (BALANCE_STEP px at a time) that keeps it."""
    lines = wrap(words, font, width, max_lines, what)
    if len(lines) < 2:
        return lines
    n, limit = len(lines), width
    while limit - BALANCE_STEP > 0:
        try:
            trial = wrap(words, font, limit - BALANCE_STEP, n, what)
        except _NoFit:
            break
        if len(trial) > n:
            break
        limit -= BALANCE_STEP
        lines = trial
    return lines


def balanced(words: Sequence[str], font: Any, width: float, max_lines: int, what: str) -> List[str]:
    """The greedy wrap's line count, at the narrowest width (BALANCE_STEP px at a time) that keeps it — no lone word
    on the last line. When the words hold a NO_BREAK_LEADS phrase ("at least 2"), the same search runs on the units
    (`no_break_units`: the phrase never splits); should that leave an ORPHAN last line (`ORPHAN_SHARE`), the lines are
    the exact narrowest split into that count instead (`_min_max_split`). A unit wider than the column falls back to
    the plain words. _NoFit as `wrap`."""
    units = no_break_units(words)
    if len(units) < len(words):
        try:
            lines = _narrowest(units, font, width, max_lines, what)
        except _NoFit:
            lines = None
        if lines is not None:
            last = lines[-1]
            if (len(lines) >= 2 and last in units
                    and font.getlength(last) < ORPHAN_SHARE * max(font.getlength(ln) for ln in lines)):
                split = _min_max_split(units, font, len(lines))
                if split is not None and all(font.getlength(ln) <= width for ln in split):
                    return split
            return lines
    return _narrowest(words, font, width, max_lines, what)


@dataclass(frozen=True)
class Text:
    lines: Tuple[str, ...]
    size: int
    pitch: int
    height: int
    width: float


class Illegible(Exception):
    """A line would be drawn under the page's legibility floor (a data card's DATA_CARD_MIN_PX). Every size steps
    down together along the ramp, so no later step can be legible either — the search stops."""


# ── the page: everything a surface draws, in absolute coordinates ─────────────


class Page:
    """One surface being laid out at ramp `step`: the text lines (each with the region its ink must stay in),
    rectangles, ellipses, stacked bars and logo discs, in absolute coordinates on `frame.canvas`. `mark` /
    `shift` move everything added since a mark (a block centred in the space left)."""

    def __init__(self, frame: Frame, step: int, font_path: str, engine: str, min_px: int = 0) -> None:
        self.frame, self.step, self.font_path, self.engine, self.min_px = frame, step, font_path, engine, min_px
        self.lines: List[DrawnLine] = []
        self.bounds: List[Box] = []
        self.rects: List[Rect] = []
        self.ellipses: List[Ellipse] = []
        self.stacks: List[Stack] = []
        self.discs: List[Disc] = []
        #: news-v23 phase 3: a theme picture's decoration (`marketing.theme_art.Icon`), set by its placer; None — none.
        self.icon: Any = None

    # sizes and fonts
    def px(self, style: _Style) -> int:
        return style.size(self.step)

    def font(self, size: int):
        return _font(self.font_path, size, self.engine)

    def legible(self, size: int, what: str) -> None:
        if size < self.min_px:
            raise Illegible(f"{what} is drawn at {size}px from ramp step {self.step} — under the {self.min_px}px "
                            "legibility floor")

    def length(self, text: str, size: int) -> float:
        return self.font(size).getlength(text)

    def measure(self, text: str, size: int, width: float, what: str, *, leading: float = 1.2,
                max_lines: int = MAX_WRAP_LINES, upper: bool = False, balance: bool = False) -> Text:
        self.legible(size, what)
        font = self.font(size)
        asc, desc = font.getmetrics()
        pitch = int(round(size * leading))
        shown = caps(text, self.font_path) if upper else text
        lines = (balanced if balance else wrap)(shown.split(), font, width, max_lines, what)
        if not lines:
            raise _NoFit(f"{what}: nothing to draw")
        widest = max(font.getlength(ln) for ln in lines)
        return Text(tuple(lines), size, pitch, (len(lines) - 1) * pitch + asc + desc, widest)

    # placing
    def mark(self) -> Tuple[int, int, int, int, int]:
        return len(self.lines), len(self.rects), len(self.ellipses), len(self.stacks), len(self.discs)

    def rollback(self, mark: Tuple[int, int, int, int, int]) -> None:
        """Forget everything added since `mark` (a placer re-laying a block another way)."""
        a, b, c, d, e = mark
        del self.lines[a:], self.bounds[a:], self.rects[b:], self.ellipses[c:], self.stacks[d:], self.discs[e:]

    def shift(self, mark: Tuple[int, int, int, int, int], dy: int) -> None:
        if not dy:
            return
        a, b, c, d, e = mark

        def mv(box: Box) -> Box:
            return box[0], box[1] + dy, box[2], box[3] + dy

        for i in range(a, len(self.lines)):
            ln = self.lines[i]
            self.lines[i] = DrawnLine(ln.field, ln.text, ln.size, ln.x, ln.y + dy, ln.anchor, ln.fill, mv(ln.ink))
            self.bounds[i] = mv(self.bounds[i])
        self.rects[b:] = [Rect(mv(r.box), r.radius, r.fill, r.outline) for r in self.rects[b:]]
        self.ellipses[c:] = [Ellipse(mv(el.box), el.fill, el.outline, el.width) for el in self.ellipses[c:]]
        self.stacks[d:] = [dataclasses.replace(s, box=mv(s.box)) for s in self.stacks[d:]]
        self.discs[e:] = [Disc(mv(dc.box), dc.art, dc.ring, dc.cutout, dc.solid_ring) for dc in self.discs[e:]]

    def line(self, field_name: str, text: str, size: int, x: float, y: float, anchor: str, fill: RGB,
             bound: Box) -> DrawnLine:
        x, y = int(round(x)), int(round(y))
        l, t, r, b = self.font(size).getbbox(text, anchor=anchor)
        ln = DrawnLine(field_name, text, size, x, y, anchor, fill, (x + l, y + t, x + r, y + b))
        self.lines.append(ln)
        self.bounds.append(bound)
        return ln

    def put(self, field_name: str, t: Text, x: float, y: float, anchor: str, fill: RGB, bound: Box) -> None:
        for i, text in enumerate(t.lines):
            self.line(field_name, text, t.size, x, y + i * t.pitch, anchor, fill, bound)

    def rect(self, box: Sequence[float], radius: int, fill: RGB, outline: int = 0) -> Box:
        b: Box = tuple(int(round(v)) for v in box)  # type: ignore[assignment]
        self.rects.append(Rect(b, radius, fill, outline))
        return b

    def ellipse(self, box: Sequence[float], fill: Optional[RGB], outline: Optional[RGB] = None, width: int = 0) -> None:
        self.ellipses.append(Ellipse(tuple(int(round(v)) for v in box), fill, outline, width))  # type: ignore[arg-type]

    def disc(self, cx: float, cy: float, r: int, art: LogoArt, *, ring: bool = False,
             cutout: Optional[RGB] = None, wordmark: bool = True, solid_ring: bool = False,
             ticker: Optional[str] = None) -> Box:
        """A logo disc of radius `r` centred on (cx, cy): the verified logo (`draw_disc` picks the white or the
        dark disc), or the company name as a wordmark — laid out here (field "wordmark:<key>"; [] when it does not
        fit legibly: a blank disc, the name stays declared). `ticker` (final review of 2026-11-26): the row's own
        ticker CHIP — a string the row already draws, so the server already allows it — drawn in the disc instead
        (field "ticker:<key>", `disc_ticker_lines`) when the name fits only under WORDMARK_PREFER_TICKER_BELOW_PX or
        not at all: a small theme-row disc never stays blank (or unreadable) for a company whose logo is missing."""
        x0, y0 = int(round(cx - r)), int(round(cy - r))
        box: Box = (x0, y0, x0 + 2 * r, y0 + 2 * r)
        self.discs.append(Disc(box, art, ring, cutout, solid_ring))
        if art.wordmark and wordmark:
            lines = disc_wordmark_lines(art, box, self.font_path, self.engine)
            if ticker and (not lines or lines[0].size < WORDMARK_PREFER_TICKER_BELOW_PX):
                lines = disc_ticker_lines(art, ticker, box, self.font_path, self.engine) or lines
            for ln in lines:
                self.lines.append(ln)
                self.bounds.append(box)
        return box

    def check(self) -> None:
        """_NoFit when any text line's ink leaves its region, or anything leaves the canvas."""
        cw, ch = self.frame.canvas
        canvas: Box = (0, 0, cw, ch)
        for ln, bound in zip(self.lines, self.bounds):
            if not _within(ln.ink, bound) or not _within(ln.ink, canvas):
                raise _NoFit(f"{ln.field}: the ink of {ln.text[:40]!r} at {ln.size}px leaves its area")
        for shape in (*[r.box for r in self.rects], *[e.box for e in self.ellipses], *[s.box for s in self.stacks],
                      *[d.box for d in self.discs]):
            if not _within(shape, canvas):
                raise _NoFit("a shape leaves the canvas")


# ── the shared pieces ─────────────────────────────────────────────────────────


def brand_row(page: Page, kicker: Optional[str], *, kicker_field: str = "kicker",
              word: bool = True) -> Tuple[Box, Optional[Box]]:
    """The brand row: the logo tile + "Caydex" (field "brand"; `word=False`: the tile alone) top-left at
    `frame.brand`, at the frame's sizes, and the kicker pill (field `kicker_field`; GOLD on PILL) right-aligned to
    `frame.right` on the same row — the kicker steps down to KICKER_FLOOR_PX before it is "does not fit"; a blank
    `kicker` draws no pill. Returns (the tile's box, the pill's box or None)."""
    fr = page.frame
    fx, fy = fr.brand
    tile: Box = (fx, fy, fx + fr.brand_px, fy + fr.brand_px)
    word_right = tile[2]
    if word:
        ln = page.line("brand", BRAND_ROW_WORD, fr.brand_word_px, fx + fr.brand_px + BRAND_GAP,
                       fy + fr.brand_px / 2, "lm", WHITE, (fx, fy - 8, fr.right, fy + fr.brand_px + 8))
        word_right = ln.ink[2]
    if not (kicker or "").strip():
        return tile, None
    room = fr.right - (word_right + 32)
    size = fr.kicker_px
    while size > KICKER_FLOOR_PX and page.length(kicker, size) + 2 * KICKER_PAD_X > room:
        size -= 1
    w = int(math.ceil(page.length(kicker, size))) + 2 * KICKER_PAD_X
    h = size + 2 * KICKER_PAD_Y
    if w > room:
        raise _NoFit(f"{kicker_field}: the pill is {w}px wide, {room}px beside the brand")
    py = fy + KICKER_DY
    pill: Box = (fr.right - w, py, fr.right, py + h)
    page.rect(pill, KICKER_RADIUS, PILL)
    page.line(kicker_field, kicker, size, pill[0] + KICKER_PAD_X, (pill[1] + pill[3]) / 2, "lm", GOLD, pill)
    return tile, pill


def footer_lines(page: Page, text: str, what: str = "footer") -> Text:
    """The ONE short footer line, centred: FOOTER_PX stepping down to FOOTER_FLOOR_PX to fit `frame.footer_span`;
    still too wide, it breaks ONLY after a " · " separator (the dot ends its line: joining the lines with a space
    gives the string back) into the fewest lines whose widest is narrowest. _NoFit when even that does not fit."""
    x0, x1 = page.frame.footer_span
    width = x1 - x0
    size = FOOTER_PX
    while size > FOOTER_FLOOR_PX and page.length(text, size) > width:
        size -= 1
    page.legible(size, what)
    font = page.font(size)
    asc, desc = font.getmetrics()
    pitch = int(round(size * FOOTER_LEADING))
    if font.getlength(text) <= width:
        lines: List[str] = [text]
    else:
        parts = text.split(" · ")
        best: Optional[List[str]] = None
        for k in range(2, len(parts) + 1):
            best = _split_parts(parts, k, font, width)
            if best:
                break
        if not best:
            raise _NoFit(f"{what}: {text[:60]!r} does not fit {width}px even broken at its separators")
        lines = best
    return Text(tuple(lines), size, pitch, (len(lines) - 1) * pitch + asc + desc,
                max(font.getlength(ln) for ln in lines))


def _split_parts(parts: Sequence[str], k: int, font: Any, width: float) -> Optional[List[str]]:
    n = len(parts)

    def line(i: int, j: int) -> str:
        return " · ".join(parts[i:j]) + (" ·" if j < n else "")

    @lru_cache(None)
    def best(i: int, kk: int) -> Tuple[float, Optional[Tuple[str, ...]]]:
        if kk == 1:
            w = font.getlength(line(i, n))
            return (w, (line(i, n),)) if w <= width else (float("inf"), None)
        out: Tuple[float, Optional[Tuple[str, ...]]] = (float("inf"), None)
        for j in range(i + 1, n - kk + 2):
            w = font.getlength(line(i, j))
            if w > width:
                break
            rest = best(j, kk - 1)
            if rest[1] is not None and max(w, rest[0]) < out[0]:
                out = (max(w, rest[0]), (line(i, j),) + rest[1])
        return out

    found = best(0, k)[1]
    return list(found) if found else None


def footer(page: Page, text: str, field_name: str = "footer") -> int:
    """Draw the short footer (`footer_lines`) centred on the canvas, its last line's foot at `frame.footer_bottom`.
    Returns its top y — nothing may be drawn below it."""
    t = footer_lines(page, text, field_name)
    cx = page.frame.canvas[0] / 2
    top = page.frame.footer_bottom - t.height
    x0, x1 = page.frame.footer_span
    page.put(field_name, t, cx, top, "ma", GREY, (x0, top - 4, x1, page.frame.footer_bottom + 8))
    return top


def headline(page: Page, field_name: str, text: str, style: _Style, x: float, y: float, width: float, *,
             max_lines: int = HEADLINE_MAX_LINES, center: bool = False, tone: str = "two",
             size: Optional[int] = None) -> int:
    """The two-tone UPPERCASE headline (`two_tone`, `caps`): the white part's lines, then the gold part's, one size,
    each part balanced — or, when the text has no sentence or clause boundary to split at (2026-11-20), ONE balanced
    block whose last line is gold; _NoFit when it takes more than `max_lines` lines at this step — above the FLOOR step only
    (the shape the look wants: the ramp steps the whole surface down first); at the floor any number of lines the
    height allows (a worst case the server's bounds let through must still fit, never be refused for its shape).
    `size` (a placer's own headline ladder, mock_money_map's 80 / 72 / 66) draws it at that size instead of the
    step's, with `max_lines` binding. `tone="white"` draws it all WHITE (a name standing alone); `tone="last"` (a theme's
    title, theme_family.py) every word but the last WHITE and the last GOLD. Returns the bottom
    y (the last line's foot)."""
    fixed = size is not None
    size = page.px(style) if size is None else size
    page.legible(size, field_name)
    block = False
    if tone == "white":
        white, gold = text, ""
    elif tone == "last":
        # news-v23 phase 3, the weekly theme post (theme_family.Frame.top): every word but the LAST white, the last gold
        words = text.split()
        white, gold = " ".join(words[:-1]), " ".join(words[-1:])
    else:
        white, gold, block = _two_tone_parts(text)
    cap = max_lines if (fixed or page.step < cards.RAMP_STEPS) else MAX_WRAP_LINES
    if block:
        # 2026-11-20: no sentence or clause boundary (`two_tone`) — never a colour change mid-phrase: the headline is
        # ONE balanced block, every line WHITE but the LAST, GOLD (one line: all gold)
        whole = page.measure(gold, size, width, field_name, leading=HEADLINE_LEADING, max_lines=cap, upper=True,
                             balance=True)
        if len(whole.lines) > cap:
            raise _NoFit(f"{field_name}: more than {cap} headline lines at {size}px")
        asc, desc = page.font(size).getmetrics()
        pitch = int(round(size * HEADLINE_LEADING))
        area: Box = (int(x) - 2, int(y) - size, int(x + width) + 2, page.frame.canvas[1])
        last = len(whole.lines) - 1
        for k, line in enumerate(whole.lines):
            colour = GOLD if k == last else WHITE
            if center:
                page.line(field_name, line, size, x + width / 2, y + k * pitch, "ma", colour, area)
            else:
                page.line(field_name, line, size, x, y + k * pitch, "la", colour, area)
        return int(round(y + last * pitch + asc + desc))
    parts = [(white, WHITE), (gold, GOLD)]
    texts: List[Tuple[Text, RGB]] = []
    for part, colour in parts:
        if part:
            texts.append((page.measure(part, size, width, field_name, leading=HEADLINE_LEADING,
                                       max_lines=cap, upper=True, balance=True), colour))
    if sum(len(t.lines) for t, _c in texts) > cap:
        raise _NoFit(f"{field_name}: more than {cap} headline lines at {size}px")
    asc, desc = page.font(size).getmetrics()
    pitch = int(round(size * HEADLINE_LEADING))
    bound: Box = (int(x) - 2, int(y) - size, int(x + width) + 2, page.frame.canvas[1])
    if len(texts) == 2 and all(len(t.lines) == 1 for t, _c in texts):
        # the whole headline fits ONE line: the white run, then the gold run on the same line ("AI CHIPS")
        (tw_, cw), (tg, cg) = texts
        gap = page.length(" ", size)
        total = tw_.width + gap + tg.width
        if total <= width:
            x0 = x + (width - total) / 2 if center else x
            page.line(field_name, tw_.lines[0], size, x0, y, "la", cw, bound)
            page.line(field_name, tg.lines[0], size, x0 + tw_.width + gap, y, "la", cg, bound)
            return int(round(y + asc + desc))
    cy = y
    n = 0
    for t, colour in texts:
        for line in t.lines:
            if center:
                page.line(field_name, line, size, x + width / 2, cy, "ma", colour, bound)
            else:
                page.line(field_name, line, size, x, cy, "la", colour, bound)
            cy += pitch
            n += 1
    return int(round(y + (n - 1) * pitch + asc + desc)) if n else int(y)


def chip_size(page: Page, text: str, size: int) -> Tuple[int, int]:
    """(width, height) of a chip of `text` at `size` — the kit's box: the text's width + 2 × CHIP_PAD_X wide,
    `size` + 2 × CHIP_PAD_Y tall (style_kit.chip: `y + size + 16`)."""
    return int(math.ceil(page.length(text, size))) + 2 * CHIP_PAD_X, size + 2 * CHIP_PAD_Y


def chip(page: Page, field_name: str, text: str, size: int, x: float, y: float, *, fill: RGB = NAVY_CHIP,
         ink: RGB = WHITE, bound: Optional[Box] = None) -> Box:
    """A ticker chip (a rounded pill) at (x, y): `fill` under `ink`, the text at the kit's place (CHIP_TEXT_DY under
    the box top, "la"). Returns its box."""
    page.legible(size, field_name)
    w, h = chip_size(page, text, size)
    box = page.rect((x, y, x + w, y + h), CHIP_RADIUS, fill)
    page.line(field_name, text, size, box[0] + CHIP_PAD_X, box[1] + CHIP_TEXT_DY, "la", ink, box)
    if bound is not None and not _within(box, bound):
        raise _NoFit(f"{field_name}: the chip leaves its card")
    return box


def card(page: Page, box: Sequence[float], *, fill: RGB = CARD, radius: int = CARD_RADIUS) -> Box:
    return page.rect(box, radius, fill)


#: A wordmark disc: the name's largest size (of the disc's radius, at most WORDMARK_MAX_PX — style_kit.logo_circle
#: starts at 26) and the widest a line may be (of the radius; the kit's 1.55·r) — every line's ink must also stay
#: inside the circle (`disc_wordmark_lines`). The size steps down 1 px at a time, as the kit's does.
WORDMARK_START_OF_R, WORDMARK_MAX_PX, WORDMARK_WIDTH_OF_R = 0.36, 26, 1.55
#: Final review of 2026-11-26 (FINAL_BUILD's theme pictures: Constellation Energy, CrowdStrike, Qualcomm … drew EMPTY
#: white discs at the theme rows' 26-30 px radii, and the names that did fit printed at 9-10 px): a disc given its
#: row's ticker (`Page.disc(ticker=…)`) draws the TICKER — one line, from TICKER_START_OF_R of the radius — whenever
#: the name fits only under this size or not at all. A ticker is the row's own chip, already drawn and allowed.
WORDMARK_PREFER_TICKER_BELOW_PX = 13
TICKER_START_OF_R = 0.6
#: The field of a disc's ticker lines (`disc_ticker_lines`) — a logo stand-in like "wordmark:<key>".
TICKER_FIELD_PREFIX = "ticker:"


def is_logo_standin(field_name: str) -> bool:
    """Whether a drawn line's field is a logo disc's stand-in text — a wordmark name or a ticker (sized by its disc,
    never by the legibility floors of the copy)."""
    return field_name.startswith("wordmark:") or field_name.startswith(TICKER_FIELD_PREFIX)


def disc_ticker_lines(art: LogoArt, ticker: str, box: Box, font_path: str, engine: str) -> List[DrawnLine]:
    """`ticker` in a WORDMARK disc (field "ticker:<key>"): ONE centred INK line, the largest size from
    TICKER_START_OF_R of the radius (at most WORDMARK_MAX_PX) down to cards.WORDMARK_MIN_PX at which it is at most
    WORDMARK_WIDTH_OF_R of the radius wide with every ink corner inside the circle — [] when it does not fit."""
    if not isinstance(ticker, str) or not ticker.strip() or len(ticker.split()) != 1:
        return []
    r = (box[2] - box[0]) / 2
    return _disc_text_lines([ticker], f"{TICKER_FIELD_PREFIX}{art.key}", box, font_path, engine,
                            min(WORDMARK_MAX_PX, int(round(r * TICKER_START_OF_R))), 1)


def disc_wordmark_lines(art: LogoArt, box: Box, font_path: str, engine: str) -> List[DrawnLine]:
    """The company name of a WORDMARK disc (style_kit.logo_circle): centred, INK, the largest size from
    WORDMARK_START_OF_R of the radius down to cards.WORDMARK_MIN_PX at which it wraps whole (≤ 3 lines, each at most
    WORDMARK_WIDTH_OF_R of the radius wide) with every ink corner inside the circle — [] (a blank disc; the name
    stays declared, never clipped) when it does not fit legibly."""
    r = (box[2] - box[0]) / 2
    return _disc_text_lines(art.name.split(), f"wordmark:{art.key}", box, font_path, engine,
                            min(WORDMARK_MAX_PX, int(round(r * WORDMARK_START_OF_R))), 3)


def _disc_text_lines(words: List[str], field_name: str, box: Box, font_path: str, engine: str, start: int,
                     max_lines: int) -> List[DrawnLine]:
    r = (box[2] - box[0]) / 2
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    size = start
    while size >= cards.WORDMARK_MIN_PX:
        font = _font(font_path, size, engine)
        try:
            lines = wrap(words, font, r * WORDMARK_WIDTH_OF_R, max_lines, "wordmark")
        except _NoFit:
            lines = []
        if lines:
            asc, desc = font.getmetrics()
            pitch = int(round(size * cards.WORDMARK_LEADING))
            total = (len(lines) - 1) * pitch + asc + desc
            y0 = cy - total / 2
            out: List[DrawnLine] = []
            ok = True
            for i, text in enumerate(lines):
                y = int(round(y0 + i * pitch))
                l, t, rr, b = font.getbbox(text, anchor="ma")
                ink = (int(cx) + l, y + t, int(cx) + rr, y + b)
                if any(math.hypot(px - cx, py - cy) > r - 4 for px in (ink[0], ink[2]) for py in (ink[1], ink[3])):
                    ok = False
                    break
                out.append(DrawnLine(field_name, text, size, int(cx), y, "ma", INK, ink))
            if ok:
                return out
        size -= 1
    return []


# ── drawing ───────────────────────────────────────────────────────────────────


@lru_cache(maxsize=8)
def _canvas_bytes(w: int, h: int, glow: Box) -> bytes:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(NAVY_TOP[i] * (1 - t) + NAVY_BOT[i] * t) for i in range(3)))
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    ImageDraw.Draw(layer).ellipse(list(glow), fill=GLOW)
    img = ImageChops.add(img, layer.filter(ImageFilter.GaussianBlur(GLOW_BLUR)))
    return img.tobytes()


def canvas(frame: Frame, glow_top: Optional[int] = None, glow_box: Optional[Box] = None):
    """A fresh RGB image of `frame.canvas`: the navy→black gradient and the soft gold glow top-right at the frame's
    `glow_y` (the picture's kit glow; the video's, lowered with its brand row); `glow_top` moves it (GLOW_DY0 /
    GLOW_DY1 around that y); `glow_box` sets the glow's whole ellipse (x0, y0, x1, y1) instead — the `feature`
    picture's glow under its art band (mock_spotlight.canvas_under_band). Cached as bytes: the same frame gives the
    same pixels."""
    from PIL import Image

    w, h = frame.canvas
    if glow_box is None:
        y0, y1 = frame.glow_y if glow_top is None else (glow_top + GLOW_DY0, glow_top + GLOW_DY1)
        glow_box = (w + GLOW_DX0, y0, w + GLOW_DX1, y1)
    return Image.frombytes("RGB", (w, h), _canvas_bytes(w, h, tuple(int(v) for v in glow_box)))


def page_colour(frame: Frame, y: int) -> RGB:
    """The gradient's colour at row `y` (no glow): a cut-out ring's page colour."""
    t = y / frame.canvas[1]
    return tuple(int(NAVY_TOP[i] * (1 - t) + NAVY_BOT[i] * t) for i in range(3))  # type: ignore[return-value]


def _mask_ellipse(w: int, h: int):
    from PIL import Image, ImageDraw

    a = cards._AA
    big = Image.new("L", (w * a, h * a), 0)
    ImageDraw.Draw(big).ellipse((0, 0, w * a - 1, h * a - 1), fill=255)
    return big.reduce(a)


def _fill_ellipse(img, box: Box, colour: RGB) -> None:
    from PIL import Image

    w, h = box[2] - box[0], box[3] - box[1]
    if w <= 0 or h <= 0:
        return
    img.paste(Image.new("RGB", (w, h), colour), (box[0], box[1]), _mask_ellipse(w, h))


def _ring(img, box: Box, colour: RGB, width: int) -> None:
    from PIL import Image, ImageDraw

    w, h = box[2] - box[0], box[3] - box[1]
    a = cards._AA
    big = Image.new("L", (w * a, h * a), 0)
    ImageDraw.Draw(big).ellipse((0, 0, w * a - 1, h * a - 1), outline=255, width=width * a)
    img.paste(Image.new("RGB", (w, h), colour), (box[0], box[1]), big.reduce(a))


def _outline_rounded(img, box: Box, radius: int, colour: RGB, width: int) -> None:
    from PIL import Image, ImageDraw

    w, h = box[2] - box[0], box[3] - box[1]
    a = cards._AA
    big = Image.new("L", (w * a, h * a), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * a - 1, h * a - 1), radius=radius * a, outline=255,
                                          width=width * a)
    img.paste(Image.new("RGB", (w, h), colour), (box[0], box[1]), big.reduce(a))


def draw_rect(img, r: Rect) -> None:
    from PIL import Image

    w, h = r.box[2] - r.box[0], r.box[3] - r.box[1]
    if w <= 0 or h <= 0:
        return
    if r.outline:
        _outline_rounded(img, r.box, r.radius, r.fill, r.outline)
    elif r.radius:
        cards._fill_rounded(img, r.box, min(r.radius, w // 2, h // 2), r.fill)
    else:
        img.paste(Image.new("RGB", (w, h), r.fill), r.box[:2])


def draw_ellipse(img, e: Ellipse) -> None:
    if e.fill is not None:
        _fill_ellipse(img, e.box, e.fill)
    if e.outline is not None and e.width > 0:
        _ring(img, e.box, e.outline, e.width)


def draw_stack(img, s: Stack) -> None:
    """The stacked bar: its parts left to right, a `bg` seam between two parts, both ends rounded (mask drawn at
    cards._AA× and box-reduced: deterministic)."""
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = s.box
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return
    a = cards._AA
    layer = Image.new("RGB", (bw * a, bh * a), s.bg)
    ld = ImageDraw.Draw(layer)
    x = 0.0
    edges: List[float] = []
    for w, c in zip(s.widths, s.colours):
        ld.rectangle([int(round(x * a)), 0, int(round((x + w) * a)), bh * a], fill=c)
        x += w
        edges.append(x)
    for e in edges[:-1]:
        ld.rectangle([int(round((e - s.seam / 2) * a)), 0, int(round((e + s.seam / 2) * a)), bh * a], fill=s.bg)
    if s.hatch is not None:
        _hatch_layer(layer, s, a)
        if s.hatch[0] > 0:
            # the seam before the hatched stretch, as between two parts (mock_money_map_loss: its own bar part)
            e = float(s.hatch[0])
            ld.rectangle([int(round((e - s.seam / 2) * a)), 0, int(round((e + s.seam / 2) * a)), bh * a], fill=s.bg)
    radius = bh * a // 2 if s.radius is None else max(0, int(s.radius)) * a
    mask = Image.new("L", (bw * a, bh * a), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bw * a - 1, bh * a - 1], radius=radius, fill=255)
    img.paste(layer.reduce(a), (x0, y0), mask.reduce(a))
    if s.outline is not None:
        colour, width = s.outline
        _outline_rounded(img, s.box, bh // 2 if s.radius is None else max(0, int(s.radius)), colour, int(width))


#: The hatch's stripes (mock_money_map_loss.bar_layer): STRIPE_STEP px apart, STRIPE_W px thick, at 45° (each stripe
#: rises one bar height over one bar height).
STRIPE_STEP, STRIPE_W = 9, 4


def _hatch_layer(layer: Any, s: Stack, a: int) -> None:
    """Over `layer` (the stack drawn at a× its size), the span `s.hatch` as diagonal stripes of each part's own colour
    on `s.bg` (mock_money_map_loss.bar_layer's hatch: the same step, thickness and angle). Deterministic."""
    from PIL import Image, ImageDraw

    bh = layer.size[1]
    hx0, hx1 = s.hatch  # type: ignore[misc]
    x = 0.0
    for w, c in zip(s.widths, s.colours):
        lo, hi = max(x, float(hx0)), min(x + w, float(hx1))
        x += w
        a0, a1 = int(round(lo * a)), int(round(hi * a))
        if a1 <= a0:
            continue
        tile = Image.new("RGB", (a1 - a0, bh), s.bg)
        td = ImageDraw.Draw(tile)
        step, th = STRIPE_STEP * a, STRIPE_W * a
        # stripes laid from the box's left edge, so a span split across two parts keeps one continuous pattern
        for k in range(-bh - (a0 % step), (a1 - a0) + bh, step):
            td.polygon([(k, bh), (k + th, bh), (k + th + bh, 0), (k + bh, 0)], fill=c)
        layer.paste(tile, (a0, 0))


def _logo_rgba(art: LogoArt):
    from PIL import Image

    try:
        with Image.open(art.path) as src:   # type: ignore[arg-type]
            src.load()
            return src.convert("RGBA")
    except Exception as e:  # noqa: BLE001 — re-raised typed, with the key (never the path's bytes)
        raise cards.CardAssetError(f"logo {art.key} unreadable: {type(e).__name__}: {e}") from e


def draw_disc(img, dc: Disc, frame: Frame) -> None:
    """A logo disc: its cut-out and gold ring when it has them, the disc — WHITE, or NAVY_CHIP for a logo made for
    a dark background (`logos.disc_tone`) — with its CARD_EDGE rim, and on it the verified logo trimmed to its
    opaque box and scaled (LANCZOS) so its MAIN mass's farthest solid pixel sits at LOGO_REACH of the radius
    (`logo_scale`: a detached ® never shrinks it, a diamond is capped at DIAMOND_FILL), aspect kept, centred; never
    recoloured. A logo served on an opaque plate (`logos.opaque_plate`) is drawn by
    `_draw_plated` instead: a LIGHT plate becomes the disc and the logo is sized by its marks; a dark tile is the
    logo, whole. A DARK IMAGE TILE (`logos.dark_image_tile`: an opaque dark picture, not one flat plate) IS the disc
    (`_draw_image_tile`, checked first).
    A wordmark disc is the white disc alone (its lines are drawn with the text). CardAssetError when a verified
    logo file is unreadable now."""
    from PIL import Image, ImageChops

    from marketing import logos

    x0, y0, x1, y1 = dc.box
    side = x1 - x0
    if dc.cutout is not None:
        c = 12
        _fill_ellipse(img, (x0 - c, y0 - c, x1 + c, y1 + c), dc.cutout)
    if dc.ring:
        solid = dc.cutout is not None or dc.solid_ring
        g = SOLID_RING_W if solid else GOLD_RING_GAP
        if solid:
            _fill_ellipse(img, (x0 - g, y0 - g, x1 + g, y1 + g), GOLD)
        else:
            _ring(img, (x0 - g, y0 - g, x1 + g, y1 + g), GOLD, GOLD_RING_W)
    rgba = _logo_rgba(dc.art) if dc.art.path is not None else None
    if rgba is not None and _draw_image_tile(img, dc.box, rgba):
        return
    if rgba is not None and logos.opaque_plate(rgba) is not None and _draw_plated(img, dc.box, rgba):
        return
    tone = logos.disc_tone(rgba) if rgba is not None else "light"
    _fill_ellipse(img, dc.box, NAVY_CHIP if tone == "dark" else WHITE)
    _ring(img, dc.box, CARD_EDGE, DISC_RIM)
    if rgba is None:
        return
    bbox = rgba.getchannel("A").getbbox()
    if bbox:
        rgba = rgba.crop(bbox)
    scale = logo_scale(rgba, side)
    sw, sh = max(1, int(round(rgba.width * scale))), max(1, int(round(rgba.height * scale)))
    scaled = rgba.resize((sw, sh), Image.LANCZOS)
    # clipped to the disc (as `_draw_plated`): only faint pixels (alpha < 128 — a glow, a shadow) can sit past the
    # farthest solid one, and none may paint over the card around the disc
    layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    layer.paste(scaled, ((side - sw) // 2, (side - sh) // 2))
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), _mask_ellipse(side, side)))
    img.paste(layer, (x0, y0), layer)


def image_tile_scale(rgba: Any, side: int) -> Optional[float]:
    """The scale a DARK IMAGE TILE (`logos.dark_image_tile`) is drawn at on a disc of diameter `side`: its longer side
    at IMAGE_TILE_COVER × `side`, never so large that a mark (measured conservatively, `_far_from`) passes the rim's
    inner edge (r − DISC_RIM); None when the RGBA logo is no dark image tile, or when at that scale its shorter side no
    longer covers the disc (it is then drawn whole on the white disc, as before)."""
    from marketing import logos

    marks = logos.dark_image_tile(rgba)
    if marks is None:
        return None
    r = side / 2
    far = _far_from(marks, rgba.width / 2, rgba.height / 2)
    scale = min(IMAGE_TILE_COVER * side / max(rgba.size), (r - DISC_RIM) / max(far, 1.0))
    return scale if scale * min(rgba.size) >= side else None


def _draw_image_tile(img, box: Box, rgba: Any) -> bool:
    """A DARK IMAGE TILE drawn as the disc itself (`image_tile_scale`): centred, clipped to the disc, the CARD_EDGE rim
    over it. False (nothing drawn) when the logo is not one, or its marks would not let it cover the disc."""
    from PIL import ImageChops

    x0, y0, x1, _y1 = box
    side = x1 - x0
    scale = image_tile_scale(rgba, side)
    if scale is None:
        return False
    layer = _plated_layer(rgba, side, rgba.width / 2, rgba.height / 2, scale)
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), _mask_ellipse(side, side)))
    img.paste(layer, (x0, y0), layer)
    _ring(img, box, CARD_EDGE, DISC_RIM)
    return True


def _draw_plated(img, box: Box, rgba: Any) -> bool:
    """A logo served on an opaque plate (`logos.opaque_plate`), drawn as the approved mock_f13_lists does: on a LIGHT
    plate the disc takes the plate's colour and the logo is sized and centred by its MARKS (`logos.plate_marks`,
    PLATE_REACH + the long-wordmark bonus); a DARK tile is centred whole on the white disc, its corners at
    TILE_REACH. The scaled image is clipped to the disc — the pixels outside it are plate margin, never a mark (the
    farthest mark sits inside the radius) — and the CARD_EDGE rim drawn over it. False (nothing drawn) when a light
    plate holds no marks."""
    from PIL import Image, ImageChops

    from marketing import logos

    x0, y0, x1, _y1 = box
    side = x1 - x0
    plate = logos.light_plate(rgba)
    if plate is not None:
        marks = logos.plate_marks(rgba, plate)
        mb = marks.getbbox()
        if not mb:
            return False
        mx, my = (mb[0] + mb[2]) / 2, (mb[1] + mb[3]) / 2
        far = _far_from(marks.crop(mb), (mb[2] - mb[0]) / 2, (mb[3] - mb[1]) / 2)   # exact up to a 96 px mark
        aspect = (mb[2] - mb[0]) / max(1, mb[3] - mb[1])
        k = PLATE_REACH + PLATE_REACH_LONG_BONUS * min(1.0, max(0.0, (aspect - 2.0) / 2.0))
        disc = plate
    else:
        mx, my = rgba.width / 2, rgba.height / 2
        far = math.hypot(rgba.width / 2, rgba.height / 2)
        k, disc = TILE_REACH, WHITE
    scale = (side / 2) * k / max(far, 1.0)
    layer = _plated_layer(rgba, side, mx, my, scale)
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), _mask_ellipse(side, side)))
    _fill_ellipse(img, box, disc)
    img.paste(layer, (x0, y0), layer)
    _ring(img, box, CARD_EDGE, DISC_RIM)
    return True


#: The source pixels padded around `_plated_layer`'s window, so LANCZOS reads real neighbours at its edges.
PLATE_WINDOW_PAD = 4
#: The largest side (× the disc's) the whole plated logo is ever scaled to; past it only the window that lands on
#: the disc is resampled (`_plated_layer`).
PLATE_FULL_RESIZE_MAX = 3


def _plated_layer(rgba: Any, side: int, mx: float, my: float, scale: float):
    """The side × side RGBA layer of `rgba` scaled by `scale` about its point (mx, my), which lands at the layer's
    centre. The whole logo is scaled only while that stays within PLATE_FULL_RESIZE_MAX × the disc (every real
    plate: a downscale); past it — a tiny mark on a big plate asks for a scale of 50+, the whole logo upscaled
    gigapixels, gigabytes — ONLY the source window that lands on the layer is resampled (padded by
    PLATE_WINDOW_PAD; `crop` pads outside the image with transparency), so the work and the memory are bounded by
    the disc, never by the scale."""
    from PIL import Image

    fw, fh = max(1, int(round(rgba.width * scale))), max(1, int(round(rgba.height * scale)))
    if max(fw, fh) <= PLATE_FULL_RESIZE_MAX * side:
        layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        layer.paste(rgba.resize((fw, fh), Image.LANCZOS), (int(round(side / 2 - mx * scale)),
                                                           int(round(side / 2 - my * scale))))
        return layer
    half = side / 2 / scale
    ax0, ay0 = int(math.floor(mx - half)) - PLATE_WINDOW_PAD, int(math.floor(my - half)) - PLATE_WINDOW_PAD
    ax1, ay1 = int(math.ceil(mx + half)) + PLATE_WINDOW_PAD, int(math.ceil(my + half)) + PLATE_WINDOW_PAD
    window = rgba.crop((ax0, ay0, ax1, ay1))
    box = (mx - half - ax0, my - half - ay0, mx + half - ax0, my + half - ay0)
    return window.resize((side, side), Image.LANCZOS, box=box)


def _far_from(mask: Any, cx: float, cy: float) -> float:
    """The distance (px, at the mask's own scale) from (cx, cy) to the farthest set pixel of the L `mask`, measured
    on a ≤ 96 px copy where a block counts when any of its pixels is set (conservative: never nearer)."""
    w, h = mask.size
    k = max(1, int(math.ceil(max(w, h) / 96)))
    small = mask.reduce(k) if k > 1 else mask
    sw, sh = small.size
    data = small.tobytes()
    far = 0.0
    for y in range(sh):
        row = data[y * sw:(y + 1) * sw]
        xs = [x for x, v in enumerate(row) if v > 0]
        if not xs:
            continue
        dy = max(abs(y * k - cy), abs((y + 1) * k - cy))
        dx = max(abs(xs[0] * k - cx), abs((xs[-1] + 1) * k - cx))
        far = max(far, math.hypot(dx, dy))
    return far


def _reach(rgba: Any) -> float:
    """The distance (px, at the logo's own scale) from the centre of the trimmed RGBA logo to its farthest solid
    pixel (alpha ≥ 128) — conservative like `_far_from`: the alpha is THRESHOLDED first, then box-reduced to ≤ 96 px,
    and a block counts when any of its pixels is solid, so a thin outer stroke (a pill outline, a light-weight
    wordmark) is never lost in the reduction and the logo never scales past its disc. At least half its shorter
    side."""
    w, h = rgba.size
    solid = rgba.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    return max(_far_from(solid, w / 2, h / 2), min(w, h) / 2, 1.0)


def _solid_grid(rgba: Any) -> Tuple[bytes, int, int, int]:
    """The trimmed logo's SOLID mask (alpha ≥ 128) box-reduced to ≤ 96 px as `_far_from` reduces it (a block is set
    when any of its pixels is solid): (its bytes, its width, its height, the reduction factor)."""
    w, h = rgba.size
    solid = rgba.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    k = max(1, int(math.ceil(max(w, h) / 96)))
    small = solid.reduce(k) if k > 1 else solid
    return small.tobytes(), small.size[0], small.size[1], k


def _main_cells(data: bytes, sw: int, sh: int) -> List[int]:
    """The set cells of a reduced mask that belong to its MAIN mass: every 4-connected component holding at least
    LOGO_MINOR_MARK_SHARE of the set cells (a detached ® / ™ beside a seal is left out; a wordmark's letters, each a
    component of its own, all stay). Pure Python breadth-first search over ≤ 96 × 96 cells."""
    seen = bytearray(len(data))
    comps: List[List[int]] = []
    for start, v in enumerate(data):
        if v == 0 or seen[start]:
            continue
        seen[start] = 1
        comp, queue = [], [start]
        while queue:
            i = queue.pop()
            comp.append(i)
            x, y = i % sw, i // sw
            for j, ok in ((i - 1, x > 0), (i + 1, x < sw - 1), (i - sw, y > 0), (i + sw, y < sh - 1)):
                if ok and data[j] and not seen[j]:
                    seen[j] = 1
                    queue.append(j)
        comps.append(comp)
    total = sum(len(c) for c in comps)
    floor = LOGO_MINOR_MARK_SHARE * total
    return [i for c in comps if len(c) >= floor for i in c]


def _main_reach(rgba: Any) -> float:
    """`_reach` measured on the logo's MAIN mass only (`_main_cells`) — from the same centre (the trimmed logo's,
    where it is drawn) and as conservatively. Equal to `_reach` for a logo without a minor detached mark."""
    w, h = rgba.size
    data, sw, sh, k = _solid_grid(rgba)
    cx, cy = w / 2, h / 2
    far = 0.0
    for i in _main_cells(data, sw, sh):
        x, y = i % sw, i // sw
        dx = max(abs(x * k - cx), abs((x + 1) * k - cx))
        dy = max(abs(y * k - cy), abs((y + 1) * k - cy))
        far = max(far, math.hypot(dx, dy))
    return max(far, min(w, h) / 2, 1.0)


def _diamond_like(rgba: Any) -> bool:
    """Is the trimmed logo a DIAMOND — about square (aspect 0.9-1.1), its farthest solid pixel about half its side
    from the centre (its points on the bbox's edge midpoints: a square's corners reach ~0.7 of its side), little of
    its bbox solid (≤ 0.55: a filled round seal covers ~0.79) and, along the diagonals, solid only to ≤ 0.82 of the
    half side (a diamond's edge sits at 0.71 there; a circle's, a ring's at 1.0)?"""
    w, h = rgba.size
    if not (0.9 <= w / max(1, h) <= 1.1):
        return False
    data, sw, sh, k = _solid_grid(rgba)
    cells = [i for i, v in enumerate(data) if v]
    if not cells or len(cells) > 0.55 * sw * sh:
        return False
    half = max(w, h) / 2
    cx, cy = w / 2, h / 2
    far = diag = 0.0
    for i in cells:
        px, py = (i % sw + 0.5) * k - cx, (i // sw + 0.5) * k - cy
        d = math.hypot(px, py)
        far = max(far, d)
        if d > 0 and abs(abs(px) - abs(py)) <= 0.21 * d:      # within ~12° of a diagonal
            diag = max(diag, d)
    return far <= 1.05 * half and diag <= 0.82 * half


def logo_scale(rgba: Any, side: int) -> float:
    """The scale a trimmed logo is drawn at on a disc of diameter `side` (`draw_disc`): its MAIN mass's farthest
    solid pixel at LOGO_REACH of the radius (`_main_reach`), never so large that ANY solid pixel (a detached ®) comes
    within LOGO_EDGE_CLEAR of the edge (`_reach`: whole inside the disc, never clipped), and a diamond's long side at
    most DIAMOND_FILL of the diameter."""
    r = side / 2
    scale = min(r * LOGO_REACH / _main_reach(rgba), (r - LOGO_EDGE_CLEAR) / _reach(rgba))
    if _diamond_like(rgba):
        scale = min(scale, side * DIAMOND_FILL / max(rgba.size))
    return scale


@lru_cache(maxsize=8)
def _brand_tile_bytes(px: int) -> bytes:
    from PIL import Image

    try:
        with Image.open(BRAND_LOGO_PATH) as src:
            src.load()
            rgba = src.convert("RGBA")
    except Exception as e:  # noqa: BLE001 — re-raised typed, with the path
        raise cards.CardAssetError(f"brand logo {BRAND_LOGO_PATH!r} unreadable: {type(e).__name__}: {e}") from e
    tile = Image.new("RGBA", (px, px), NAVY_CHIP + (255,))
    tile.alpha_composite(rgba.resize((px, px), Image.LANCZOS))
    return tile.convert("RGB").tobytes()


def draw_brand_tile(img, frame: Frame) -> None:
    """The brand row's logo tile at `frame.brand`: the vendored Caydex logo, scaled to the frame's `brand_px`, its
    corners rounded (× 0.22) with a thin BRAND_OUTLINE rim."""
    from PIL import Image

    px = frame.brand_px
    x, y = frame.brand
    tile = Image.frombytes("RGB", (px, px), _brand_tile_bytes(px))
    radius = int(px * 0.22)
    img.paste(tile, (x, y), cards._rounded_mask(px, px, radius))
    _outline_rounded(img, (x, y, x + px, y + px), radius, BRAND_OUTLINE, 2)


def draw_text(img, lines: Sequence[DrawnLine], font_path: str, engine: str) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    for ln in lines:
        draw.text((ln.x, ln.y), ln.text, font=_font(font_path, ln.size, engine), fill=ln.fill, anchor=ln.anchor)


def draw_page(img, page_like: Any, frame: Frame, font_path: str, engine: str, *, brand: bool = True) -> None:
    """Everything a laid-out surface holds, in order: rectangles (cards, pills, tiles, rules), stacked bars,
    ellipses (dots, rings), logo discs, the brand tile, then every text line."""
    for r in page_like.rects:
        draw_rect(img, r)
    for s in getattr(page_like, "stacks", ()):
        draw_stack(img, s)
    for e in getattr(page_like, "ellipses", ()):
        draw_ellipse(img, e)
    for dc in getattr(page_like, "discs", ()):
        draw_disc(img, dc, frame)
    if brand:
        draw_brand_tile(img, frame)
    draw_text(img, page_like.lines, font_path, engine)


# ── colour rules ──────────────────────────────────────────────────────────────

#: The figure's tone by the script's series (the read-back's `series`): a purchase amount and a members' count are
#: GREEN; a 13F change is signed (GREEN up, RED down — `signed_tone`); a stake's amount and a theme's share GOLD_INK;
#: an earnings figure (estimate, reported) NEUTRAL. An unknown or absent series is neutral.
GAIN_SERIES = frozenset({"ceo_buys", "insider_buys", "insider_spotlight", "congress_count"})
SIGNED_SERIES = frozenset({"thirteen_f", "thirteen_f_spotlight"})
GOLD_SERIES = frozenset({"company_stakes", "theme_explainer"})
#: A news-v22 13F roundup's section headings (what an older web still sends during a worker-first deploy; the news-v23
#: list's figures are signed, so its lexicon no longer holds them — pinned by their words in
#: tests/test_marketing_news_layouts.py) and the direction each one's unsigned figures read in.
HEADING_TONES: Dict[str, str] = {"Newly reported": "gain", "No longer reported": "loss",
                                 "Reported more shares": "gain", "Reported fewer shares": "loss"}
_MINUS = ("-", "−")


#: A rounded figure's leading qualifier ("About −18.1M shares", a news-v22 web's f13s.img.figure.*.about): skipped
#: before the sign is read, so a rounded change takes the same tone as its exact twin on the picture.
_QUALIFIER = re.compile(r"(?i)about\s+")


def signed_tone(value: str) -> Optional[str]:
    """"gain" for a figure that starts with "+", "loss" for "-" / "−" — after one leading "About " (any case) —
    else None."""
    v = value.strip()
    m = _QUALIFIER.match(v)
    if m:
        v = v[m.end():]
    if v.startswith("+"):
        return "gain"
    if v.startswith(_MINUS):
        return "loss"
    return None


def tone_colour(tone: Optional[str]) -> RGB:
    return {"gain": GREEN, "loss": RED, "gold": GOLD_INK}.get(tone or "", INK)


def figure_tone(series: Optional[str], value: str, heading: Optional[str] = None) -> Optional[str]:
    """The tone of a figure `value` of a script of `series` (in a section headed `heading`): "gain", "loss",
    "gold" or None (neutral INK)."""
    if series in GAIN_SERIES:
        return "gain"
    if series in SIGNED_SERIES:
        return signed_tone(value) or HEADING_TONES.get(heading or "")
    if series in GOLD_SERIES:
        return "gold"
    return None
