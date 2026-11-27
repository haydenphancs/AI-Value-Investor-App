"""
The 4:5 POST IMAGE of a TEMPLATE (news) script — drop 2a, contract D9 / D14: the `rows`, `spotlight` and `bars`
layouts of the closed `image_spec` the web's templates compose (app/services/marketing/template_onscreen.py is the
server's half; `cards.LAYOUTS` and the caps mirror it, pinned equal by tests/test_marketing_news_layouts.py), the
`pair` and `grid` layouts (drop 2b, tests/test_marketing_layouts_2b.py) and the `feature` layout (2026-11-15, the
spotlight series).

**The look** (news-v23, owner 2026-11-19: "i need exactly these styles, colors, words"; `marketing/look.py`, ported
from the approved design kit and drawn after the reference mocks in out/marketing_v2_preview/FINAL_REFERENCE): the
navy→black gradient with its gold glow, the brand row (the Caydex tile + "Caydex") top-left with the spec's KICKER in
the gold pill right-aligned on the same row, the headline in two-tone UPPERCASE (white, then gold), warm-white cards
with real company logos on a white disc (a DARK disc for a logo made for a dark background), and the spec's FOOTER as
one short grey line. Per layout:

* `rows` — the title as the headline, its subtitle, a company card for the header, then one card per row: the logo
  disc, the row's ticker chip (news-v23, `rows[].chip`) over the first cell (a name), the middle cell (a status /
  role) and the LAST cell right-aligned big — the row cards flex in height before the page steps — its colour
  the series' tone (`look.figure_tone`: a Form 4 purchase GREEN, a 13F change GREEN up / RED down, an earnings figure
  neutral); a section's heading is a grey column label, its "more" a grey line; the notes on a lifted dark panel.
* `spotlight` — the headline (its own 80 → 60 ladder, ≤ 3 lines), then ONE card filling the room to the footer:
  the logo disc, the chip and the name, the figure huge between two hairlines (the series' tone; it takes the room
  the tiles leave), and each line as a row of stat tiles (split at its " · " separators; a leading figure set large).
* `bars` (Money Map) — the title (its HEAD LADDER 80 / 72 / 66 while the cards take mock_money_map's card LEVELS,
  `BARS_LEVELS` — the ramp is the data card's and the last resort; under the plate when its words do not fit
  beside it) beside the company plate (the logo disc in a gold ring, the TICKER chip in a gold pill
  — the company's name for a spec without one), the subtitle, then the segments as ONE stacked revenue bar under the flow's first line (Revenue) with a
  colour legend (`look.SEG_COLORS`; a negative segment, or more segments than colours, is drawn as legend rows only —
  never a wrong bar), the flow as a stepped list of bars (its last line GREEN for a profit, RED for a loss), and the
  callout on a card with a stripe of the same colour.
* `pair` (company stakes) — the investor's name as the headline beside its plate, then the investee's card: its disc
  and name, the label small above the figure in GOLD_INK, the lines on a tile.
* `grid` (a theme) — the title and subtitle, then two columns of tile cards (disc, name, line — its trailing share
  in GOLD_INK) and the "+k more" pill.
* `feature` (the spotlight series) — with per-post art: the art across the top edge to edge, the brand row on a navy
  scrim over it, the logo disc in a gold ring on the band's edge, the "AI illustration" label at its bottom-right;
  without art: the disc in its ring under the brand row. Then the name with its gold chip, the headline, and ONE card:
  the figure (the series' tone) with its label beside it, then the fact rows.

`DRAWERS` is everything this worker can draw; `_WALKERS` / `_LAYOUTS` — what it ACCEPTS — are only the SHIPPED
layouts (`cards.SHIPPED_LAYOUTS`, the server's mirror).

Per-post ART (workstream C, owner decisions 2026-11-15): only a `feature` picture (ART_LAYOUTS) is offered the run's
verified art (`TemplateImage.art`, from `art.resolve_art`); it draws it as the band — centre-cropped through
`cards.art_tile` — with the code-owned cards.ART_LABEL burned on it; the declared strings then carry the label
(`drawn_strings`). When the art cannot be drawn (ART_SKIP_REASONS) the picture is rendered once more without it and
`RenderedImage.art_skipped` says why: never a skipped day because of art.

* **What is drawn is what the spec holds, and nothing else** — plus the brand row's word (BRAND_ROW_WORD, allowed
  on every picture by the server: `app.schemas.marketing.PICTURE_BRAND_TEXT`). `walk` reads the spec in the
  server's draw order and validates it as a CLOSED schema; a bad spec is a ValueError (RenderInputError), never the
  lesson layout. A string may be DRAWN upper-cased (a headline), split into differently coloured runs or broken at
  its " · " separators into tiles — its words, in order, are always all drawn — but never another word.
* **Logos.** Each referenced logo is a disc: the verified logo, drawn unaltered and only scaled, on the white or the
  dark disc (`logos.disc_tone`), or — missing, failed or unverified — the company name as a wordmark, a drawn and
  declared string. `TemplateImage.strings` is the walk with each WORDMARK's name at its logo's place;
  `TemplateImage.allowed` (every logo's name) is exactly the server's allow-list.
* **Never truncated.** Every string is word-wrapped whole; every size (and every disc) steps down together along
  `cards.RAMP_STEPS`; text that does not fit at the floor is `CardOverflow` (the render stage skips the day
  `unrenderable_text`), never a clipped line. Every ink box is checked inside its card, pill or column.
* **Deterministic bytes**: the same spec, logos, series, Pillow and engine give the same JPEG (`cards.encode_jpeg`,
  the same quality ladder and byte cap as the lesson image).

**The DATA CARD** (Batch 2, the frozen video feature `data_card`): the template video's text card the read-back
names (`data_card_slot`) may be replaced by this same picture — the same `TemplateImage`, the same placers, the same
strings — laid out on the 9:16 video card (`look.VIDEO_FRAME`: the brand row 140 px from the top, the content in
cards.SAFE_X above the caption band, the footer under it, nothing in the safe bottom 420 px) instead of the 4:5 page.
A picture that fits only with some drawn line under DATA_CARD_MIN_PX is NOT a data card (`layout_data_card` raises
CardOverflow and `cards.cards_for_script` keeps the text card). `feature` is never a data card.

Pure apart from Pillow (imported inside functions). Nothing here imports app.*.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from marketing import cards, look, theme_art
from marketing.cards import Box, CardOverflow, DrawnLine, LogoArt, _NoFit, _Style, _within
from marketing.look import Frame, Rect

logger = logging.getLogger("marketing.news_layouts")

# ── sizes (px; every one steps down together from start to floor, cards.RAMP_STEPS) ─────────

HEADLINE_STYLE = _Style(80, 32, 1.08)       # the two-tone headline (WHITE, then GOLD)
SUBTITLE_STYLE = _Style(32, 22, 1.25)       # the sub line under it (SUB)
ROW_DISC = _Style(74, 20, 1.0)              # a row card's logo disc (radius)
ROW_NAME_STYLE = _Style(42, 22, 1.12)       # a row's first cell (INK)
ROW_SECOND_STYLE = _Style(28, 22, 1.2)      # a row's middle cell (INK2)
ROW_FIGURE_STYLE = _Style(72, 30, 1.0)      # a row's last cell, right-aligned (the series' tone)
ROW_CHIP_PX = _Style(22, 22, 1.0)           # a row's ticker chip (mock_form4.wide_row_card: the kit chip, 22 px)
#: How far a row card's disc may flex below its step's size before the page steps instead (`_lay_rows`): the
#: roundup mock's 174 px rows keep a 74 px disc; a long list never squeezes its discs past two thirds.
ROW_FLEX_MIN_SHARE = 0.65
CHIP_PX = _Style(24, 22, 1.0)               # a ticker chip's text
HEADING_STYLE = _Style(28, 22, 1.2)         # a section heading: a column label (GREY)
NOTE_STYLE = _Style(26, 22, 1.32)           # the notes on their dark panel (SUB)
MORE_STYLE = _Style(28, 22, 1.2)            # a "+k more" line (GREY)
SPOT_DISC = _Style(84, 44, 1.0)             # the spotlight card's logo disc (radius)
SPOT_NAME_STYLE = _Style(64, 32, 1.1)       # its company name (INK)
SPOT_FIGURE_STYLE = _Style(240, 80, 1.0)    # its figure (the series' tone)
#: The spotlight headline's own ladder (mock_form4.spotlight: `headline2(size=80, max_lines=3, min_size=60)`).
SPOT_HEAD_PX: Tuple[int, ...] = tuple(range(80, 58, -2))
#: The spotlight card's ticker chip (mock_form4.spotlight: `chip(..., size=26)`) and the gap under it to the name
#: (the mock's `block = 44 + 14 + name_size`: the name 58 px under the chip's top).
SPOT_CHIP_PX = _Style(26, 22, 1.0)
SPOT_CHIP_GAP = 16
SPOT_FOOTER_GAP = 36
#: A Form 4 ROUNDUP's headline ladder on the picture (mock_form4.roundup: `headline2(size=76, max_lines=4,
#: min_size=56)`) — the generic `rows` path; the ramp's size only when even 56 px takes more than 4 lines.
ROWS_HEAD_PX: Tuple[int, ...] = tuple(range(76, 54, -2))
#: Owner decisions 2026-11-20 (form4_roundup5.jpg, the designers' notes: "the headline drops one size step to 3 lines
#: at 65 px; the approved 3-row version used 4 lines"): a roundup PICTURE of this many rows or more takes its headline in
#: THREE lines, from 76 px down one pixel at a time, so its five chip-above rows keep ~150 px each.
ROWS_THREE_LINE_ROWS = 4
ROWS_HEAD_PX_MANY: Tuple[int, ...] = tuple(range(76, 54, -1))
#: The row card's block (mock_form4.wide_row_card: `block = 38 + 10 + name_size + 8 + 28`): the chip, ROW_CHIP_GAP,
#: the name advanced by its font SIZE, ROW_INNER_GAP, the role by its size.
ROW_CHIP_GAP = 10
ROW_INNER_GAP = 8
TILE_H = _Style(104, 64, 1.0)               # a stat tile's height
TILE_BIG = _Style(60, 30, 1.0)              # a stat tile's leading figure (INK)
TILE_SMALL = _Style(32, 22, 1.0)            # a stat tile's other words (INK2)
PLATE_DISC = _Style(86, 46, 1.0)            # the bars / pair plate's disc (radius), in its gold ring
BAR_LABEL_STYLE = _Style(32, 22, 1.2)       # a bar's label, a legend label (INK)
BAR_VALUE_STYLE = _Style(36, 22, 1.2)       # a bar's value (INK; the flow's last line in its tone)
BAR_H = _Style(24, 10, 1.0)                 # a flow bar's height
STACK_H = _Style(64, 28, 1.0)               # the stacked revenue bar's height
CALLOUT_LEAD_STYLE = _Style(30, 22, 1.2)    # the callout's first clause (INK)
CALLOUT_STYLE = _Style(50, 26, 1.12)        # the callout's figure clause (GREEN / RED)
PAIR_NAME_STYLE = _Style(42, 26, 1.15)      # the investee's name (INK)
PAIR_FIGURE_STYLE = _Style(96, 44, 1.0)     # the stake's figure (GOLD_INK)
PAIR_LABEL_STYLE = _Style(28, 22, 1.2)      # its label ("invested"; INK2)
PAIR_LINE_STYLE = _Style(28, 22, 1.25)      # the lines on the tile (INK2)
GRID_DISC = _Style(44, 18, 1.0)             # a grid tile's logo disc (radius)
GRID_NAME_STYLE = _Style(30, 22, 1.15)      # a grid tile's company name (INK)
GRID_LINE_STYLE = _Style(26, 22, 1.2)       # its line (INK2, its trailing share GOLD_INK)
FEATURE_DISC = _Style(92, 48, 1.0)          # the feature's logo disc (radius)
FEATURE_NAME_STYLE = _Style(56, 30, 1.1)    # the company name (WHITE)
FEATURE_HEADLINE_STYLE = _Style(66, 30, 1.08)
FEATURE_FIGURE_STYLE = _Style(136, 56, 1.0)  # the figure (the series' tone)
FEATURE_LABEL_STYLE = _Style(30, 22, 1.25)  # the figure's label (INK2)
FACT_LABEL_STYLE = _Style(27, 22, 1.2)      # a fact's label (INK2)
FACT_VALUE_STYLE = _Style(30, 22, 1.2)      # a fact's value (INK)
FACT_ROW = _Style(56, 32, 1.0)              # a fact row's height (mock_spotlight INSIDER_LAYOUTS["overlay"] row_h)
#: The figure label's column beside the feature's figure (mock_spotlight's two-line label: "of stock by the CEO," /
#: "per Form 4" — 297 px at 30 px); a label that takes more than 3 lines there is balanced across the full room.
FEATURE_LABEL_MAX_W = 300
#: The feature figure's block height, of its size (mock_spotlight: `fig_h = int(136 × 0.76)` — a cap-height box, not
#: the "$" ink), the label's LAST line on the figure's baseline, the hairline FEATURE_DIVIDER_GAP under it.
FEATURE_FIG_H_OF_SIZE = 0.76
FEATURE_DIVIDER_GAP = 30
#: The feature headline's own ladder on the picture (mock_spotlight.headline_lines: `start=66, floor=50`, 1 px at a
#: time), ≤ 3 lines; never above the ramp's size at the step (the floor step is unchanged).
FEATURE_HEAD_PX: Tuple[int, ...] = tuple(range(66, 49, -1))
FEATURE_HEAD_MAX_LINES = 3
#: The feature glow under the art band (mock_spotlight.canvas_under_band: [W − 470, band − 40, W + 300, band + 420]),
#: relative to (the canvas' right edge, the band's bottom).
FEATURE_GLOW = (-470, -40, 300, 420)

#: A right-aligned figure may take at most this share of a row's text column; a wider one is stacked under the
#: row's other cells instead (never squeezed into a sliver).
RIGHT_SHARE = 0.5
CELL_GAP = 24
#: A legend row's least gap between its label and its value (mock_money_map LEG_VAL_GAP).
LEGEND_VALUE_GAP = 18
#: The Money Map's HEAD LADDER (mock_money_map.render: `for hsize in (80, 72, 66)` outside its card levels): the
#: headline's sizes, tried in turn while only the cards step down the ramp; then the whole page steps together.
BARS_HEAD_PX: Tuple[int, ...] = (80, 72, 66)
#: The narrowest column a Money Map title is set in beside its plate; narrower, it goes under the plate.
BARS_HEAD_MIN_COLUMN = 240
#: The Money Map PICTURE's card LEVELS (mock_money_map.LEVELS — label px, value px, flow bar px, flow row gap, card pad
#: y — biggest first; the first that fits wins, each tried with its legend at full size then `tight`): under the head
#: ladder, these — never the ramp — size the cards, the stacked bar (64 / 60 / 56 by the pad), the legend and the gaps
#: (`_bars_sizes`). The ramp stays the data card's and the picture's last resort.
BARS_LEVELS: Tuple[Tuple[int, int, int, int, int], ...] = (
    (32, 36, 24, 30, 34), (30, 34, 22, 26, 32), (29, 32, 20, 22, 30), (28, 30, 18, 18, 28), (26, 28, 16, 14, 24),
    (24, 26, 14, 12, 22), (22, 24, 12, 10, 18))
#: The level path's fixed metrics (mock_money_map): the space kept above the footer, the least gap between two cards
#: (plus up to BARS_GAP_EXTRA of the spare), the legend's dot radius / dot → label / label → value / column gaps, the
#: flow's text → bar gap, and the callout (30 / 50 px, padded 30 over / 28 under, 8 between).
BARS_FLOOR_GAP = 30
BARS_BASE_GAP, BARS_GAP_EXTRA = 18, 22
BARS_LEG_DOT, BARS_LEG_DOT_GAP, BARS_LEG_COL_GAP = 11, 14, 40
BARS_TEXT_BAR_GAP = 10
BARS_CALLOUT_PX = (30, 50)
BARS_CALLOUT_PAD = (30, 28)
BARS_CALLOUT_INNER = 8


@dataclass(frozen=True)
class CostsSizes:
    """A Money Map LOSS year's COSTS card (owner decision 2026-11-20, "A: costs vs revenue"; mock_money_map_loss.a_sizes)
    and its two-line callout, px: the bar labels (`lab`), their values (`val`), each bar's height and corner radius, the
    legend, the note, the card's pad, the gap between the two bars, the row 1 text → the revenue bar, the costs row →
    the legend, the legend → the note, the costs bar → its row, and the callout's (lead, figure line) sizes."""

    lab: int
    val: int
    bar: int
    rad: int
    leg: int
    note: int
    pad: int
    gap_bars: int
    after_t: int
    before_leg: int
    note_gap: int
    bar_gap_text: int
    callout: Tuple[int, int]


#: The picture's costs card (money_map_loss_A_rivian.jpg: `a_sizes()`, the callout `callout2(s1=28, s2=44)`).
COSTS_PICTURE = CostsSizes(lab=28, val=30, bar=42, rad=10, leg=26, note=24, pad=22, gap_bars=12, after_t=8,
                           before_leg=14, note_gap=10, bar_gap_text=8, callout=(28, 44))
#: The video COVER's costs card, biggest first (money_map_loss_A_rivian_cover.jpg: `a_sizes(cover=True)` and
#: `callout2(s1=30, s2=52)`): the first that fits wins, its headline on the head ladder (BARS_HEAD_PX) — then the ramp.
#: The mock's cover ran its callout into the burned-caption band (y 1170-1310); the worker keeps every cover line above
#: cards.TEXT_ZONE_Y[1] (the 9:16 frame's area), so a three-line headline takes a smaller level than the mock's.
COSTS_COVER_LEVELS: Tuple[CostsSizes, ...] = (
    CostsSizes(lab=32, val=38, bar=68, rad=14, leg=28, note=24, pad=36, gap_bars=18, after_t=14, before_leg=22,
               note_gap=14, bar_gap_text=12, callout=(30, 52)),
    CostsSizes(lab=30, val=36, bar=60, rad=13, leg=27, note=24, pad=30, gap_bars=16, after_t=12, before_leg=18,
               note_gap=12, bar_gap_text=10, callout=(30, 48)),
    CostsSizes(lab=28, val=34, bar=52, rad=12, leg=26, note=24, pad=26, gap_bars=14, after_t=10, before_leg=16,
               note_gap=10, bar_gap_text=8, callout=(28, 44)),
    COSTS_PICTURE,
)
#: The costs card's fixed metrics (mock_money_map_loss): the card's side pad, row 1's label → value gap and the least
#: room between "Revenue $X" and "Net loss $Y", the net-loss box's gap after the revenue bar and its dashed edge (dash,
#: gap, stroke), the marker through both bars (width, overhang), the bar's seam, the legend (dot radius, dot → label,
#: label → value, item → item, row → row), the note's hatched swatch (side, corner radius, rim) and its text's indent.
COSTS_PAD_X = 34
COSTS_LABEL_GAP = 12
COSTS_ROW_CLEAR = 30
COSTS_BOX_GAP = 4
COSTS_DASH, COSTS_DASH_GAP, COSTS_DASH_W = 12, 7, 3
COSTS_MARKER_W, COSTS_MARKER_OVER = 3, 6
COSTS_SEAM = 4
COSTS_LEG_DOT, COSTS_LEG_TEXT_GAP, COSTS_LEG_VALUE_GAP, COSTS_LEG_ITEM_GAP, COSTS_LEG_ROW_GAP = 11, 12, 10, 44, 10
COSTS_SWATCH, COSTS_SWATCH_RADIUS, COSTS_SWATCH_RIM = 22, 5, 2
COSTS_NOTE_INDENT = 34
COSTS_NOTE_LEADING = 4
#: The thinnest a non-zero piece of the costs bar is drawn (mock_money_map_loss.a_card: an END piece — it carries a
#: rounded end, or the hatched stretch — THIN_END, an inner piece THIN_MID), widened by taking the width from operating
#: expenses, never from cost of sales: where cost of sales ends against the revenue marker is a fact
#: (`costs_bar_widths`).
COSTS_THIN_END, COSTS_THIN_MID = 16, 10
#: The costs callout (mock_money_map_loss.callout2): padded 24 over / 22 under, 6 between its lines, its stripe 24 px
#: inside the card's top and bottom.
COSTS_CALLOUT_PAD = (24, 22)
COSTS_CALLOUT_INNER = 6
#: The picture's gaps between its three cards on a loss year (mock_money_map_loss.render_A: 16 + up to 10 of the
#: spare).
COSTS_BASE_GAP, COSTS_GAP_EXTRA = 16, 10
#: The cover (render_D): the costs card → its callout, and the room above the card — 40 px plus 40 % of whatever is
#: left (the block sits a little above the middle), never under COSTS_COVER_MIN_TOP.
COSTS_COVER_CALLOUT_GAP = 28
COSTS_COVER_TOP, COSTS_COVER_TOP_SHARE, COSTS_COVER_MIN_TOP = 40, 0.4, 24
#: The most lines one wrapped string is allowed before it counts as not fitting (bounds the work).
MAX_WRAP_LINES = look.MAX_WRAP_LINES
GRID_COLUMNS = 2
GRID_COL_GAP = 18
#: Workstream C's band on the `feature` picture: edge to edge across the top (mock_spotlight "overlay").
FEATURE_BAND_H = 432
#: The air between the feature card and the footer (mock_spotlight: the card 34 px over the footer's top).
FEATURE_FOOT_GAP = 34
FEATURE_SCRIM_H = 250

#: The 4:5 post image and the 9:16 data card (look.POST_FRAME / look.VIDEO_FRAME).
POST_IMAGE_FRAME: Frame = look.POST_FRAME
DATA_CARD_FRAME: Frame = look.VIDEO_FRAME
#: The smallest text a data card may draw, px on the 1080×1920 frame (a company name on a WORDMARK disc is exempt:
#: it stands in for a logo and is sized by its disc).
DATA_CARD_MIN_PX = 22


# ── the spec: walk + validate (the worker's mirror of template_onscreen) ────────────────────


def _bad(path: str, why: str) -> ValueError:
    return ValueError(f"image_spec.{path}: {why}" if path else f"image_spec: {why}")


class _Walker:
    """Records the spec's draw order — ("text", path, string) or ("logo", path, key) — while
    checking it, exactly as template_onscreen._Walk does (minus `compliance.clean`, which the
    server applied before the spec was sent)."""

    def __init__(self, table: Mapping[str, LogoArt]) -> None:
        self.table = table
        self.items: List[Tuple[str, str, str]] = []
        self.strings = 0
        self.logos: List[str] = []

    def obj(self, value: Any, path: str, required: Sequence[str], optional: Sequence[str]) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            raise _bad(path, f"not an object ({type(value).__name__})")
        allowed = set(required) | set(optional)
        unknown = [str(k)[:40] for k in value if not isinstance(k, str) or k not in allowed]
        if unknown:
            raise _bad(path, f"unknown key(s) {unknown}")
        for k in required:
            if value.get(k) is None:
                raise _bad(f"{path}.{k}" if path else k, "missing")
        return value

    def text(self, value: Any, path: str, *, record: bool = True) -> None:
        """Check `value` is one drawable string and record it at `path` — or, `record=False` (owner decision
        2026-11-20: a Money Map loss year's flow steps and one-line callout, which its COSTS card replaces), check it
        only: the server still allows it (template_onscreen walks it), this worker never draws nor declares it."""
        if not cards.drawable_text(value):
            raise _bad(path, "not one drawable string")
        if not record:
            return
        self.strings += 1
        self.items.append(("text", path, value))

    def opt_text(self, obj: Mapping[str, Any], key: str, path: str) -> None:
        if obj.get(key) is not None:
            self.text(obj[key], f"{path}.{key}" if path else key)

    def logo(self, value: Any, path: str) -> None:
        if not isinstance(value, str) or not 1 <= len(value) <= cards.LOGO_KEY_MAX_CHARS:
            raise _bad(path, "not a logo key")
        if value not in self.table:
            raise _bad(path, f"names no logo entry of the script ({value[:16]!r})")
        if value not in self.logos:
            self.logos.append(value)
            if len(self.logos) > cards.MAX_LOGOS:
                raise _bad(path, f"more than {cards.MAX_LOGOS} logos")
        self.items.append(("logo", path, value))

    @staticmethod
    def seq(value: Any, path: str, lo: int, hi: int) -> Sequence[Any]:
        if not isinstance(value, (list, tuple)):
            raise _bad(path, f"not a list ({type(value).__name__})")
        if not lo <= len(value) <= hi:
            raise _bad(path, f"{len(value)} entries, not {lo}..{hi}")
        return value

    def opt_seq(self, obj: Mapping[str, Any], key: str, hi: int) -> Sequence[Any]:
        value = obj.get(key)
        return () if value is None else self.seq(value, key, 0, hi)


def _w_company(w: _Walker, value: Any, path: str, *, chip: bool) -> None:
    obj = w.obj(value, path, ("logo", "name"), ("chip",) if chip else ())
    w.logo(obj["logo"], f"{path}.logo")
    w.text(obj["name"], f"{path}.name")
    if chip:
        w.opt_text(obj, "chip", path)


def _w_lines(w: _Walker, spec: Mapping[str, Any], key: str, hi: int) -> None:
    for i, line in enumerate(w.opt_seq(spec, key, hi)):
        w.text(line, f"{key}[{i}]")


def _w_rows(w: _Walker, spec: Mapping[str, Any]) -> None:
    if spec.get("header") is not None:
        _w_company(w, spec["header"], "header", chip=True)
    w.text(spec["title"], "title")
    w.opt_text(spec, "subtitle", "")
    total = 0
    for i, section in enumerate(w.seq(spec["sections"], "sections", 1, cards.MAX_SECTIONS)):
        path = f"sections[{i}]"
        sec = w.obj(section, path, ("rows",), ("heading", "columns", "more"))
        w.opt_text(sec, "heading", path)
        tabled = _w_columns(w, sec, path)
        rows = w.seq(sec["rows"], f"{path}.rows", 1, cards.MAX_SECTION_ROWS)
        total += len(rows)
        if total > cards.MAX_ROWS:
            raise _bad("sections", f"more than {cards.MAX_ROWS} rows")
        for j, raw in enumerate(rows):
            _w_row(w, raw, f"{path}.rows[{j}]", table=tabled)
        w.opt_text(sec, "more", path)
    _w_lines(w, spec, "notes", cards.MAX_NOTES)


def _w_columns(w: _Walker, sec: Mapping[str, Any], path: str) -> bool:
    """A section's `columns` (news-v23, the earnings table — template_onscreen._columns): exactly TABLE_CELLS − 1
    labels; True when it has them (its every row then carries a `table`)."""
    if sec.get("columns") is None:
        return False
    cols = w.seq(sec["columns"], f"{path}.columns", cards.TABLE_CELLS - 1, cards.TABLE_CELLS - 1)
    for k, label in enumerate(cols):
        w.text(label, f"{path}.columns[{k}]")
    return True


def _w_row(w: _Walker, raw: Any, rpath: str, *, table: bool = False) -> None:
    """One `rows` row in the server's draw order (template_onscreen._row, ROW_KEYS): its logo, its chip, its cells,
    its `label` (company stakes: the basis word over the figure), its `detail` (the insider list's grey line, owner
    decisions 2026-11-20), then — under a section with `columns` — its `table` lines' cells."""
    required, optional = cards.ROW_KEYS
    row = w.obj(raw, rpath, required + (("table",) if table else ()), optional)
    if row.get("logo") is not None:
        w.logo(row["logo"], f"{rpath}.logo")
    if row.get("chip") is not None:
        if not isinstance(row["chip"], str) or len(row["chip"]) > cards.ROW_CHIP_MAX_CHARS:
            raise _bad(f"{rpath}.chip", f"not a chip of at most {cards.ROW_CHIP_MAX_CHARS} characters")
        w.text(row["chip"], f"{rpath}.chip")
    for k, cell in enumerate(w.seq(row["cells"], f"{rpath}.cells", 1, cards.MAX_CELLS)):
        w.text(cell, f"{rpath}.cells[{k}]")
    if row.get("label") is not None:
        if not isinstance(row["label"], str) or len(row["label"]) > cards.ROW_LABEL_MAX_CHARS:
            raise _bad(f"{rpath}.label", f"not a label of at most {cards.ROW_LABEL_MAX_CHARS} characters")
        w.text(row["label"], f"{rpath}.label")
    if row.get("detail") is not None:
        if not isinstance(row["detail"], str) or len(row["detail"]) > cards.ROW_DETAIL_MAX_CHARS:
            raise _bad(f"{rpath}.detail", f"not a detail line of at most {cards.ROW_DETAIL_MAX_CHARS} characters")
        w.text(row["detail"], f"{rpath}.detail")
    if not table:
        return
    required, optional = cards.TABLE_KEYS
    for t, raw_line in enumerate(w.seq(row["table"], f"{rpath}.table", 1, cards.MAX_TABLE_LINES)):
        tpath = f"{rpath}.table[{t}]"
        line = w.obj(raw_line, tpath, required, optional)
        for k, cell in enumerate(w.seq(line["cells"], f"{tpath}.cells", cards.TABLE_CELLS, cards.TABLE_CELLS)):
            w.text(cell, f"{tpath}.cells[{k}]")


def _w_spotlight(w: _Walker, spec: Mapping[str, Any]) -> None:
    _w_company(w, spec["header"], "header", chip=True)
    w.text(spec["figure"], "figure")
    w.text(spec["headline"], "headline")
    _w_lines(w, spec, "lines", cards.MAX_LINES)


def _ratio_ok(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return 0 <= value <= 1


def _w_bar_list(w: _Walker, spec: Mapping[str, Any], key: str, lo: int, hi: int,
                record_upto: Optional[int] = None) -> None:
    """A bar list (`segments` / `flow`), each bar checked; `record_upto` (a loss year's flow under its COSTS card): only
    the first that many bars are recorded as drawn (the flow's Revenue line, the revenue card's total) — the rest checked
    only."""
    for i, raw in enumerate(w.seq(spec[key], key, lo, hi)):
        path = f"{key}[{i}]"
        record = record_upto is None or i < record_upto
        bar = w.obj(raw, path, ("label", "value", "ratio", "style"), ())
        w.text(bar["label"], f"{path}.label", record=record)
        w.text(bar["value"], f"{path}.value", record=record)
        if not _ratio_ok(bar["ratio"]):
            raise _bad(f"{path}.ratio", f"{str(bar['ratio'])[:20]} is not a finite number in [0, 1]")
        if not isinstance(bar["style"], str) or bar["style"] not in cards.BAR_STYLES:
            raise _bad(f"{path}.style", f"{str(bar['style'])[:20]!r} is not one of {cards.BAR_STYLES}")


def _w_bars(w: _Walker, spec: Mapping[str, Any]) -> None:
    """`bars` in the server's draw order (template_onscreen._bars). Owner decision 2026-11-20: a loss year's `costs` card
    (cards.COSTS_KEYS) is drawn in place of the flow's steps after its first (Revenue: the revenue card's total) and of
    the one-line callout — those are checked, never recorded (never drawn, never declared)."""
    _w_company(w, spec["header"], "header", chip=True)
    w.text(spec["title"], "title")
    w.text(spec["subtitle"], "subtitle")
    _w_bar_list(w, spec, "segments", cards.MIN_BARS, cards.MAX_BARS)
    costs = spec.get("costs")
    _w_bar_list(w, spec, "flow", cards.MIN_FLOW, cards.MAX_FLOW, record_upto=None if costs is None else 1)
    w.text(spec["callout"], "callout", record=costs is None)
    if costs is not None:
        _w_costs(w, costs, "costs")


def _w_costs(w: _Walker, value: Any, path: str) -> None:
    """A Money Map loss year's COSTS card (owner decision 2026-11-20; template_onscreen._costs, mirrored): the revenue
    bar's label and value, the net loss's, the costs bar's, each part's, the note, the callout's two lines — every ratio
    a finite number in [0, 1], the revenue bar never longer than the costs bar; 0, 2 or 3 parts; a note only beside
    parts."""
    required, optional = cards.COSTS_KEYS
    c = w.obj(value, path, required, optional)
    ratios: Dict[str, float] = {}
    for key, (req, opt) in (("revenue", cards.COSTS_BAR_KEYS), ("net", cards.COSTS_NET_KEYS),
                            ("total", cards.COSTS_BAR_KEYS)):
        bar = w.obj(c[key], f"{path}.{key}", req, opt)
        w.text(bar["label"], f"{path}.{key}.label")
        w.text(bar["value"], f"{path}.{key}.value")
        if "ratio" in req:
            if not _ratio_ok(bar["ratio"]):
                raise _bad(f"{path}.{key}.ratio", f"{str(bar['ratio'])[:20]} is not a finite number in [0, 1]")
            ratios[key] = float(bar["ratio"])
    if ratios["revenue"] > ratios["total"]:
        raise _bad(f"{path}.revenue.ratio", "the revenue bar is longer than the costs bar (not a loss year)")
    parts = w.seq(c["parts"], f"{path}.parts", 0, max(cards.COSTS_PARTS))
    if len(parts) not in cards.COSTS_PARTS:
        raise _bad(f"{path}.parts", f"{len(parts)} entries, not one of {cards.COSTS_PARTS}")
    req, opt = cards.COSTS_BAR_KEYS
    for i, raw in enumerate(parts):
        ppath = f"{path}.parts[{i}]"
        part = w.obj(raw, ppath, req, opt)
        w.text(part["label"], f"{ppath}.label")
        w.text(part["value"], f"{ppath}.value")
        if not _ratio_ok(part["ratio"]):
            raise _bad(f"{ppath}.ratio", f"{str(part['ratio'])[:20]} is not a finite number in [0, 1]")
    if c.get("note") is not None:
        if not parts:
            raise _bad(f"{path}.parts", "a note explains the hatched end of the parts: there are none")
        w.text(c["note"], f"{path}.note")
    for k, line in enumerate(w.seq(c["callout"], f"{path}.callout", cards.COSTS_CALLOUT_LINES,
                                   cards.COSTS_CALLOUT_LINES)):
        w.text(line, f"{path}.callout[{k}]")


def _w_side(w: _Walker, value: Any, path: str) -> None:
    """A pair side (cards.PAIR_SIDE_KEYS): its logo when it has one (the investee may not), its name."""
    required, optional = cards.PAIR_SIDE_KEYS[path]
    obj = w.obj(value, path, required, optional)
    if obj.get("logo") is not None:
        w.logo(obj["logo"], f"{path}.logo")
    w.text(obj["name"], f"{path}.name")


def _w_pair(w: _Walker, spec: Mapping[str, Any]) -> None:
    _w_side(w, spec["left"], "left")
    _w_side(w, spec["right"], "right")
    w.text(spec["figure"], "figure")
    w.text(spec["label"], "label")
    _w_lines(w, spec, "lines", cards.MAX_LINES)


def _w_icon(spec: Mapping[str, Any]) -> None:
    """A theme picture's decoration token (news-v23 phase 3, template_onscreen._icon): absent or one of THEME_ICONS."""
    icon = spec.get("icon")
    if icon is not None and (not isinstance(icon, str) or icon not in cards.THEME_ICONS):
        raise _bad("icon", f"{str(icon)[:20]!r} is not one of {cards.THEME_ICONS}")


def _w_chip(w: _Walker, obj: Mapping[str, Any], path: str) -> None:
    if obj.get("chip") is not None:
        if not isinstance(obj["chip"], str) or len(obj["chip"]) > cards.ROW_CHIP_MAX_CHARS:
            raise _bad(f"{path}.chip", f"not a chip of at most {cards.ROW_CHIP_MAX_CHARS} characters")
        w.text(obj["chip"], f"{path}.chip")


def _w_grid(w: _Walker, spec: Mapping[str, Any]) -> None:
    """The grid in the server's draw order (template_onscreen._grid): title, subtitle (its `sub_break` checked, never
    drawn), each tile's logo, chip, name and line, "+N more"; the `icon` token checked."""
    w.text(spec["title"], "title")
    w.text(spec["subtitle"], "subtitle")
    brk = spec.get("sub_break")
    if brk is not None and (isinstance(brk, bool) or not isinstance(brk, int)
                            or not 1 <= brk < len(str(spec["subtitle"]).split())):
        raise _bad("sub_break", f"{str(brk)[:20]!r} is not a break inside the subtitle")
    _w_icon(spec)
    required, optional = cards.GRID_TILE_KEYS
    for i, raw in enumerate(w.seq(spec["tiles"], "tiles", cards.MIN_TILES, cards.MAX_TILES)):
        path = f"tiles[{i}]"
        tile = w.obj(raw, path, required, optional)
        w.logo(tile["logo"], f"{path}.logo")
        _w_chip(w, tile, path)
        w.text(tile["name"], f"{path}.name")
        w.opt_text(tile, "line", path)
    w.opt_text(spec, "more", "")


def _w_ranked(w: _Walker, spec: Mapping[str, Any]) -> None:
    """`ranked` (news-v23 phase 3) in the server's draw order (template_onscreen._ranked): title, subtitle, each row's
    rank (exactly its position), logo, chip, name and value (its ratio in [0, 1], never drawn), "+N more"."""
    w.text(spec["title"], "title")
    w.text(spec["subtitle"], "subtitle")
    _w_icon(spec)
    required, optional = cards.RANKED_ROW_KEYS
    for i, raw in enumerate(w.seq(spec["rows"], "rows", cards.MIN_RANKED_ROWS, cards.MAX_RANKED_ROWS)):
        path = f"rows[{i}]"
        row = w.obj(raw, path, required, optional)
        if row["rank"] != str(i + 1):
            raise _bad(f"{path}.rank", f"{str(row['rank'])[:8]!r} is not {i + 1}")
        w.text(row["rank"], f"{path}.rank")
        w.logo(row["logo"], f"{path}.logo")
        _w_chip(w, row, path)
        w.text(row["name"], f"{path}.name")
        w.text(row["value"], f"{path}.value")
        if not _ratio_ok(row["ratio"]):
            raise _bad(f"{path}.ratio", f"{str(row['ratio'])[:20]} is not a finite number in [0, 1]")
    w.opt_text(spec, "more", "")


def _w_heads(w: _Walker, value: Any, path: str) -> None:
    for i, head in enumerate(w.seq(value, path, cards.THEME_HEADS, cards.THEME_HEADS)):
        for k, line in enumerate(w.seq(head, f"{path}[{i}]", 1, cards.MAX_HEAD_LINES)):
            w.text(line, f"{path}[{i}][{k}]")


def _w_shaped(w: _Walker, value: Any, path: str, shape: "re.Pattern[str]") -> None:
    w.text(value, path)
    if shape.fullmatch(value) is None:
        raise _bad(path, f"{str(value)[:20]!r} is not in its figure shape")


def _w_table_row(w: _Walker, layout: str, raw: Any, path: str) -> None:
    """One theme-table row in the server's draw order (template_onscreen._theme_table_row)."""
    required, optional = cards.THEME_TABLE_ROW_KEYS[layout]
    row = w.obj(raw, path, required, optional)
    w.logo(row["logo"], f"{path}.logo")
    _w_chip(w, row, path)
    w.text(row["name"], f"{path}.name")
    if layout == "figures":
        _w_shaped(w, row["growth"], f"{path}.growth", cards.GROWTH_FIGURE_RE)
        if not _ratio_ok(row["ratio"]):
            raise _bad(f"{path}.ratio", f"{str(row['ratio'])[:20]} is not a finite number in [0, 1]")
        _w_shaped(w, row["profit"], f"{path}.profit", cards.PROFIT_FIGURE_RE)
    elif layout == "ledger":
        # EMPTY_CELL ("—", no filing) is a marker: checked, never drawn, never declared (the server's own rule)
        for key in ("buys", "sells"):
            if row[key] != cards.EMPTY_CELL:
                _w_shaped(w, row[key], f"{path}.{key}", cards.LEDGER_AMOUNT_RE)
        if row["buys"] == cards.EMPTY_CELL and row["sells"] == cards.EMPTY_CELL:
            raise _bad(path, "a scorecard row with neither a purchase nor a sale")
    else:
        _w_shaped(w, row["count"], f"{path}.count", cards.TALLY_COUNT_RE)
        if not 2 <= int(row["count"]) <= 535:
            raise _bad(f"{path}.count", "not a count of 2..535 members")


def _w_theme_table(layout: str) -> "Walker":
    """A theme TABLE (news-v23 phase 3, 2026-11-20: `figures`, `ledger`, `tally`) in the server's draw order
    (template_onscreen._theme_table): title, subtitle, the heads (or the unit), each row, "+N more", the note."""
    def walk(w: _Walker, spec: Mapping[str, Any]) -> None:
        w.text(spec["title"], "title")
        w.text(spec["subtitle"], "subtitle")
        _w_icon(spec)
        if layout == "tally":
            w.text(spec["unit"], "unit")
        else:
            _w_heads(w, spec["heads"], "heads")
        for i, raw in enumerate(w.seq(spec["rows"], "rows", cards.MIN_THEME_TABLE_ROWS,
                                      cards.MAX_THEME_TABLE_ROWS[layout])):
            _w_table_row(w, layout, raw, f"rows[{i}]")
        w.opt_text(spec, "more", "")
        w.opt_text(spec, "note", "")
    return walk


def _w_feature(w: _Walker, spec: Mapping[str, Any]) -> None:
    """`feature` (2026-11-15), in the server's draw order: header (logo, name, chip), figure, figure_label,
    headline, then each fact's label and value (cards.FACT_KEYS, MIN_FACTS..MAX_FACTS)."""
    _w_company(w, spec["header"], "header", chip=True)
    w.text(spec["figure"], "figure")
    w.text(spec["figure_label"], "figure_label")
    w.text(spec["headline"], "headline")
    required, optional = cards.FACT_KEYS
    for i, raw in enumerate(w.seq(spec["facts"], "facts", cards.MIN_FACTS, cards.MAX_FACTS)):
        path = f"facts[{i}]"
        fact = w.obj(raw, path, required, optional)
        w.text(fact["label"], f"{path}.label")
        w.text(fact["value"], f"{path}.value")


def walk(spec: Any, logos: Mapping[str, LogoArt]) -> List[Tuple[str, str, str]]:
    """The spec's draw order — ("text", path, string) or ("logo", path, key) — after checking it
    as the closed schema (module docstring). ValueError names the first problem."""
    if not isinstance(spec, dict):
        raise _bad("", f"not an object ({type(spec).__name__})")
    layout = spec.get("layout")
    if not isinstance(layout, str) or layout not in cards.LAYOUTS:
        raise _bad("layout", f"{str(layout)[:40]!r} is not one of {cards.LAYOUTS}")
    if layout not in cards.SHIPPED_LAYOUTS or layout not in _WALKERS:
        raise _bad("layout", f"{layout!r} is not shipped yet (drop 2b: accepted only once the server "
                             f"and this worker both list it in SHIPPED_LAYOUTS)")
    required, optional = cards.LAYOUT_KEYS[layout]
    w = _Walker(logos)
    w.obj(spec, "", cards.COMMON_KEYS + required, optional)
    version = spec["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != cards.SPEC_VERSION:
        raise _bad("version", f"{str(version)[:20]} is not {cards.SPEC_VERSION}")
    w.text(spec["kicker"], "kicker")
    _WALKERS[layout](w, spec)
    w.text(spec["footer"], "footer")
    if w.strings + len(w.logos) > cards.ONSCREEN_TEXT_MAX:
        raise _bad("", f"{w.strings} strings + {len(w.logos)} logo names > {cards.ONSCREEN_TEXT_MAX}")
    return w.items


def _strings(items: Sequence[Tuple[str, str, str]], logos: Mapping[str, LogoArt], *, every_name: bool) -> List[str]:
    out: List[str] = []
    for kind, _path, value in items:
        if kind == "logo":
            art = logos[value]
            if not (every_name or art.wordmark):
                continue
            value = art.name
        if value not in out:
            out.append(value)
    return out


@dataclass(frozen=True)
class TemplateImage:
    """A checked template post image: its layout, the spec as the server sent it, the script's logo
    table, `strings` (what it draws: the walk with each wordmark's company name; de-duplicated, in
    draw order — the asset's `metadata.onscreen_text`) and `allowed` (every referenced logo's name:
    the server's template_onscreen.image_strings). `art` (workstream C): the per-post art OFFERED to a
    `feature` picture's band (a cards.ArtImage), or None — every other layout never carries it. Whether it
    is DRAWN is the render's answer (`TemplateLayout.art`; the declared strings then end with ART_LABEL —
    `drawn_strings`). `series` (news-v23): the script read-back's series, which picks the figures' colours
    (`look.figure_tone`) — None draws them neutral."""

    layout: str
    spec: Mapping[str, Any]
    logos: Mapping[str, LogoArt]
    strings: Tuple[str, ...]
    allowed: Tuple[str, ...]
    art: Optional[cards.ArtImage] = None
    series: Optional[str] = None
    #: news-v23 — the read-back says its web allows the look's own strings on a picture (`cards.new_look`): the
    #: brand row's word is drawn and declared. False (a web before news-v23, which refuses "Caydex" on a picture):
    #: the brand row is the tile alone. A data card (a video surface: VIDEO_BRAND_TEXT) always draws the word.
    look: bool = True
    #: Visual-fidelity round (2026-11-20): a theme video COVER's sub line breaks where its PICTURE's breaks — the
    #: picture spec's hand-set `sub_break` (theme_ai_chips.jpg), carried here when the cover's heading IS the
    #: picture's subtitle (`cover_image`'s `picture`); None everywhere else (the spec's own `sub_break` is read).
    sub_break: Optional[int] = None
    #: Owner decisions 2026-11-20: the image is a template video's list COVER (`cover_image`) — a Form 4 list's cover is
    #: drawn compact (`_lay_insider_list`, insider_multi5_cover_alt_all5.jpg), never as its picture or data card is.
    cover: bool = False

    @property
    def footer(self) -> str:
        return str(self.spec["footer"])


#: The only layout whose picture draws per-post art (owner decisions 2026-11-15: the spotlight series' `feature`
#: band; the main lane's pictures keep their layouts — "Video cover now, pictures later").
ART_LAYOUTS: Tuple[str, ...] = ("feature",)


def template_image_for_script(script: Mapping[str, Any], logos: Mapping[str, LogoArt],
                              art: Optional[cards.ArtImage] = None) -> TemplateImage:
    """The script's `image_spec` + `image_footer` as a TemplateImage. ValueError when either is
    missing or malformed, or the spec's footer is not the script's `image_footer` (the server
    requires that exact footer among the drawn strings). `art` (workstream C) is offered only to an
    ART_LAYOUTS picture: on any other layout it is dropped with a WARNING (a main-lane picture never draws
    art — the server would refuse it, `art_surface_not_allowed`); TypeError when it is not an ArtImage."""
    if not isinstance(script, Mapping):
        raise ValueError(f"script must be an object, not {type(script).__name__}")
    spec = script.get("image_spec")
    if spec is None:
        raise ValueError("the template script carries no image_spec: its post image cannot be drawn")
    footer = script.get("image_footer")
    if not cards.drawable_text(footer):
        raise ValueError("the template script has no image_footer: an image without its footer must not render")
    if art is not None and not isinstance(art, cards.ArtImage):
        raise TypeError(f"art must be a cards.ArtImage or None, not {type(art).__name__}")
    items = walk(spec, logos)
    if spec["footer"] != footer:
        raise ValueError("image_spec.footer is not the script's image_footer")
    if art is not None and spec["layout"] not in ART_LAYOUTS:
        logger.warning("template image (%s): art is drawn only on a %s picture — ignored here (sha=%s…)",
                       spec["layout"], "/".join(ART_LAYOUTS), art.sha256[:12])
        art = None
    series = script.get("series")
    return TemplateImage(layout=spec["layout"], spec=spec, logos=dict(logos),
                         strings=tuple(_strings(items, logos, every_name=False)),
                         allowed=tuple(_strings(items, logos, every_name=True)), art=art,
                         series=series if isinstance(series, str) and len(series) <= 40 else None,
                         look=cards.new_look(script))


def cover_image(card: Mapping[str, Any], logos: Mapping[str, LogoArt], *, footer: str, series: Any = None,
                look: bool = True, picture: Any = None) -> TemplateImage:
    """A 13F LIST cover (news-v23, cover_13f_berkshire.jpg) as a TemplateImage the 13F list placer draws on the 9:16
    frame: the opening card's kicker and headline (the title), the filer's plate — its first logo and its `chip` — only
    beside a chip (a listed filer; an unlisted filer's wordmark key is never drawn here), its `list` (the column label,
    1..cards.MAX_OPENING_ROWS rows, the "+N more" line) as the one section, and the run's short `footer`. Every string
    is one of the card's own (the header's name is its first logo's company name: template_onscreen.opening_strings
    allows it) plus the footer (allowed on every template video card). ValueError (a malformed list, a row the closed
    `rows` walk refuses) — contract drift, never guessed.

    news-v23 (the same cover idea on every template video): an EARNINGS cover's list names its table's `columns` (each
    row a `table`), drawn by the earnings placer; a CONGRESS COUNT cover's list has no column label, drawn by the
    Congress placer (`_lay_rows` picks by the series).

    `picture` (visual-fidelity round 2026-11-20): the read-back's `image_spec`, or None. When the list's heading IS that
    picture's subtitle and the picture breaks it by hand (`sub_break`), the cover's sub line breaks there too
    (`TemplateImage.sub_break`) — never "… and its" / "share of total revenue …"."""
    raw = card.get("list") if isinstance(card, Mapping) else None
    if not isinstance(raw, dict):
        raise ValueError("opening_card.list must be an object")
    required, optional = cards.OPENING_LIST_KEYS
    unknown = [str(k)[:40] for k in raw if k not in required + optional]
    if unknown:
        raise ValueError(f"opening_card.list has unknown key(s) {unknown}")
    for k in required:
        if raw.get(k) is None:
            raise ValueError(f"opening_card.list has no {k}")
    rows = raw["rows"]
    layout = raw.get("layout")
    sub_break = _picture_sub_break(picture, raw.get("heading"))
    if layout is not None:
        # news-v23 phase 3 (2026-11-20): a theme TABLE's cover — the picture's own layout on the 9:16 frame, its rows
        # (the picture's first 1..MAX_OPENING_TABLE_ROWS), the list's heading as the sub line, its heads or unit, its
        # "+N more" and its note, under the card's kicker and headline
        if layout not in cards.THEME_TABLE_LAYOUTS:
            raise ValueError(f"opening_card.list.layout {str(layout)[:20]!r} is not a theme table")
        if not isinstance(rows, list) or not 1 <= len(rows) <= cards.MAX_OPENING_TABLE_ROWS:
            raise ValueError(f"opening_card.list.rows must be a list of 1-{cards.MAX_OPENING_TABLE_ROWS} rows")
        tspec: Dict[str, Any] = {"layout": layout, "version": cards.SPEC_VERSION, "kicker": card.get("kicker"),
                                 "title": card.get("headline"), "subtitle": raw.get("heading"), "rows": rows,
                                 "footer": footer}
        for k in ("heads", "unit", "more", "note"):
            if raw.get(k) is not None:
                tspec[k] = raw[k]
        items = walk(tspec, logos)
        return TemplateImage(layout=layout, spec=tspec, logos=dict(logos),
                             strings=tuple(_strings(items, logos, every_name=False)),
                             allowed=tuple(_strings(items, logos, every_name=True)),
                             series=series if isinstance(series, str) and len(series) <= 40 else None, look=look,
                             sub_break=sub_break, cover=True)
    if not isinstance(rows, list) or not 1 <= len(rows) <= cards.MAX_OPENING_ROWS:
        raise ValueError(f"opening_card.list.rows must be a list of 1-{cards.MAX_OPENING_ROWS} rows")
    if any(isinstance(r, Mapping) and r.get("detail") is not None for r in rows):
        # owner decisions 2026-11-20 ("All rows, compact"): a cover row never carries the picture's detail line (the
        # filed cost per share stays off every video frame) — template_onscreen refuses it first, mirrored here
        raise ValueError("opening_card.list.rows: a cover row never carries the detail line")
    section: Dict[str, Any] = {"rows": rows}
    for k in ("heads", "unit", "note"):
        if raw.get(k) is not None:
            raise ValueError(f"opening_card.list.{k} belongs to a theme table's cover only")
    for k in ("heading", "columns", "more"):
        if raw.get(k) is not None:
            section[k] = raw[k]
    spec: Dict[str, Any] = {"layout": "rows", "version": cards.SPEC_VERSION, "kicker": card.get("kicker"),
                            "title": card.get("headline"), "sections": [section], "footer": footer}
    keys = card.get("logos")
    if card.get("chip") is not None and isinstance(keys, list) and keys and keys[0] in logos:
        spec["header"] = {"logo": keys[0], "name": logos[keys[0]].name, "chip": card["chip"]}
    items = walk(spec, logos)
    return TemplateImage(layout="rows", spec=spec, logos=dict(logos),
                         strings=tuple(_strings(items, logos, every_name=False)),
                         allowed=tuple(_strings(items, logos, every_name=True)),
                         series=series if isinstance(series, str) and len(series) <= 40 else None, look=look,
                         sub_break=sub_break, cover=True)


def _picture_sub_break(picture: Any, heading: Any) -> Optional[int]:
    """The picture spec's hand-set `sub_break` when `heading` is exactly that picture's subtitle and the break falls
    inside it (1 .. words − 1); else None (a break that does not read back is never guessed at)."""
    if not isinstance(picture, Mapping) or not isinstance(heading, str) or picture.get("subtitle") != heading:
        return None
    brk = picture.get("sub_break")
    if isinstance(brk, bool) or not isinstance(brk, int) or not 0 < brk < len(heading.split()):
        return None
    return brk


def drawn_strings(image: TemplateImage, layout: Any) -> List[str]:
    """What a laid-out template picture DECLARES (`metadata.onscreen_text`): its strings, plus ART_LABEL when its
    layout drew the per-post art (`TemplateLayout.art`; the server allows the label only beside the picture's
    `art_sha256` declaration, template_onscreen.art_allowed), plus — last — the brand row's word BRAND_ROW_WORD
    (news-v23: drawn on every picture; app.schemas.marketing.PICTURE_BRAND_TEXT)."""
    out = list(image.strings)
    if getattr(layout, "art", None) is not None and cards.ART_LABEL not in out:
        out.append(cards.ART_LABEL)
    if image.look and look.BRAND_ROW_WORD not in out:
        out.append(look.BRAND_ROW_WORD)
    return out


def _brand_word(image: TemplateImage, frame: Frame) -> bool:
    """Whether the brand row draws its word on `frame`: on a picture only for a news-v23 read-back
    (`TemplateImage.look`); on a data card always (a video surface — the word is VIDEO_BRAND_TEXT on every web)."""
    return image.look or frame is not POST_IMAGE_FRAME



# ── layout ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TemplateLayout:
    kind: str                         # "template:<layout>" / "data:<layout>"
    step: int
    engine: str
    #: The content area every card sits in (the frame's area above the footer's gap).
    panel: Box
    rects: Tuple[Rect, ...]
    #: Every logo disc's box and what it shows (a verified logo, or a wordmark — its lines field "wordmark:<key>").
    tiles: Tuple[Tuple[Box, LogoArt], ...]
    lines: Tuple[DrawnLine, ...]
    #: Unused since news-v23 (the pair's arrow is gone): kept for the layout's shape.
    shapes: Tuple[Tuple[Tuple[int, int], ...], ...] = ()
    #: The `feature` picture's ART BAND (only when it draws art): edge to edge across the top.
    band: Optional[Box] = None
    #: Workstream C: the per-post art DRAWN in the band, or None.
    art: Optional[cards.ArtImage] = None
    #: The ART_LABEL pill's box (its text is a line of field "art_label"), or None.
    label: Optional[Box] = None
    ellipses: Tuple[look.Ellipse, ...] = ()
    stacks: Tuple[look.Stack, ...] = ()
    discs: Tuple[look.Disc, ...] = ()
    #: The brand row's logo tile and the kicker pill.
    brand: Optional[Box] = None
    pill: Optional[Box] = None
    #: Where the footer's top is (nothing is drawn below it but the footer).
    footer_top: int = 0
    #: A Money Map laid on its HEAD LADDER (`_first_fit`): the headline's size; None — the whole page at `step`.
    head_px: Optional[int] = None
    #: …and its card LEVEL (an index into BARS_LEVELS, the legend `tight` or not); None — the ramp's `step`.
    bars_level: Optional[Tuple[int, bool]] = None
    #: news-v23 phase 3: a theme picture's quiet decoration (`theme_art.Icon`), drawn first; None — none.
    icon: Optional[theme_art.Icon] = None

    def field_lines(self, name: str) -> List[str]:
        return [ln.text for ln in self.lines if ln.field == name]

    def fields(self) -> List[str]:
        """Every field drawn, in first-drawn order."""
        out: List[str] = []
        for ln in self.lines:
            if ln.field not in out:
                out.append(ln.field)
        return out


_Illegible = look.Illegible


def _gap(page: look.Page, px: int, floor: Optional[int] = None) -> int:
    """A gap of `px` at ramp step 0, stepping down with the text to `floor` (default 60 % of it) at the floor step."""
    low = px * 0.6 if floor is None else floor
    return int(round(px - (px - low) * page.step / cards.RAMP_STEPS))


def _cap(page: look.Page, lines: int) -> int:
    """A text's line cap: `lines` above the floor step (the look's shape), MAX_WRAP_LINES at the floor (a worst case
    the server's bounds let through must still fit if the height allows — never refused for its shape)."""
    return lines if page.step < cards.RAMP_STEPS else MAX_WRAP_LINES


def _columns(page: look.Page) -> Tuple[int, int, int, int]:
    """(card x0, card x1, text x0, text x1): the cards span the area; the headline column is 8 px inside it (the
    kit's MARGIN 56 / TEXT_X 64 on the picture)."""
    x0, _y0, x1, _y1 = page.frame.area
    return x0, x1, x0 + 8, x1 - 8


def _tone(image: TemplateImage, value: str, heading: Optional[str] = None) -> look.RGB:
    return look.tone_colour(look.figure_tone(image.series, value, heading))


def _company_block(page: look.Page, header: Mapping[str, Any], logos: Mapping[str, LogoArt], x0: int, x1: int,
                   y: int, path: str, *, chip: bool) -> int:
    """A company card (rows' header): the logo disc, the chip (NAVY) above the name (INK). Returns its bottom."""
    r = page.px(ROW_DISC)
    pad_y = _gap(page, 20)
    tx = x0 + 22 + 2 * r + 28
    width = x1 - 36 - tx
    name = page.measure(header["name"], page.px(ROW_NAME_STYLE), width, f"{path}.name", leading=1.15)
    chip_text = header.get("chip") if chip else None
    cs = page.px(CHIP_PX)
    chip_h = look.chip_size(page, chip_text, cs)[1] if chip_text else 0
    block = name.height + (chip_h + _gap(page, 10) if chip_text else 0)
    h = max(2 * r, block) + 2 * pad_y
    box = look.card(page, (x0, y, x1, y + h))
    page.disc(x0 + 22 + r, y + h / 2, r, logos[header["logo"]])
    ty = y + (h - block) / 2
    if chip_text:
        look.chip(page, f"{path}.chip", chip_text, cs, tx, ty, bound=box)
        ty += chip_h + _gap(page, 10)
    page.put(f"{path}.name", name, tx, ty, "la", look.INK, box)
    return y + h


def _row_card(page: look.Page, image: TemplateImage, row: Mapping[str, Any], path: str, x0: int, x1: int, y: int,
              heading: Optional[str], row_h: Optional[int] = None, row_h_rows: int = 0) -> int:
    """One row as a card (mock_form4.wide_row_card): the logo disc (when it has one), the row's ticker CHIP (NAVY,
    when it has one) over the first cell (INK) and the middle one (INK2), the last cell (of 2-3) right-aligned big in
    the series' tone — or stacked under the others when it would take more than RIGHT_SHARE of the text column.
    `row_h` (`_lay_rows`' flex): the card is that tall (never shorter than its text) and its disc
    min(ROW_DISC, (row_h − 22) / 2) — the mock's `h = min(190, room per row)`, `r = min(74, (h − 22) // 2)`.
    Returns the card's bottom."""
    cells = list(row["cells"])
    art = image.logos[row["logo"]] if row.get("logo") is not None else None
    r = page.px(ROW_DISC) if art is not None else 0
    if art is not None and row_h is not None:
        r = min(r, max(ROW_DISC.floor, (row_h - 22) // 2))
    pad_y = _gap(page, 21, 7)
    tx = x0 + _gap(page, 22, 12) + 2 * r + _gap(page, 28, 16) if art is not None else x0 + 36
    right = x1 - _gap(page, 36, 20)
    width = right - tx
    fig = cells[-1] if len(cells) >= 2 else None
    fsize = page.px(ROW_FIGURE_STYLE)
    stacked = False
    left_w = width
    if fig is not None:
        page.legible(fsize, f"{path}.cells[{len(cells) - 1}]")
        fw = page.length(fig, fsize)
        if fw <= width * RIGHT_SHARE:
            left_w = int(width - fw - CELL_GAP)
        else:
            stacked = True
    chip_text = row.get("chip")
    cs = page.px(ROW_CHIP_PX)
    chip_w, chip_h = look.chip_size(page, chip_text, cs) if chip_text is not None else (0, 0)
    if chip_text is not None and chip_w > left_w:
        raise _NoFit(f"{path}.chip: the chip is {chip_w}px wide, the column {left_w}px")
    chip_gap = _gap(page, ROW_CHIP_GAP, 6)
    name = page.measure(cells[0], page.px(ROW_NAME_STYLE), left_w, f"{path}.cells[0]", leading=1.15)
    second = (page.measure(cells[1], page.px(ROW_SECOND_STYLE), left_w, f"{path}.cells[1]", leading=1.2)
              if len(cells) == 3 else None)
    fig_t = (page.measure(fig, fsize, width, f"{path}.cells[{len(cells) - 1}]", leading=1.0)
             if fig is not None and stacked else None)
    inner = _gap(page, ROW_INNER_GAP, 3)

    def adv(t: look.Text) -> int:
        # a text block advanced by its font SIZE (the mock's `name_size + 8`), not its ascent + descent
        return (len(t.lines) - 1) * t.pitch + t.size

    last = fig_t or second or name
    asc_l, desc_l = page.font(last.size).getmetrics()
    spill = max(0, asc_l + desc_l - last.size)      # the last line's descenders under the mock's block
    text_h = ((chip_h + chip_gap if chip_text is not None else 0) + adv(name)
              + (inner + adv(second) if second else 0) + (inner + adv(fig_t) if fig_t else 0))
    h = max(2 * r, text_h + spill) + 2 * pad_y
    if row_h is not None:
        # owner decisions 2026-11-20 (form4_roundup5.jpg): a longer roundup's flexed card keeps 8 px over and under its
        # text (the mock's five rows of ~155 px), a three-row card 10 as before
        air = 8 if row_h_rows >= ROWS_THREE_LINE_ROWS else 10
        h = max(min(h, row_h), text_h + spill + 2 * _gap(page, air, 4), 2 * r + 2 * _gap(page, 11, 4))
    box = look.card(page, (x0, y, x1, y + h))
    if art is not None:
        page.disc(x0 + _gap(page, 22, 12) + r, y + h / 2, r, art)
    ty = y + (h - text_h) / 2
    if chip_text is not None:
        look.chip(page, f"{path}.chip", chip_text, cs, tx, ty, bound=box)
        ty += chip_h + chip_gap
    page.put(f"{path}.cells[0]", name, tx, ty, "la", look.INK, box)
    ty += adv(name)
    if second is not None:
        ty += inner
        page.put(f"{path}.cells[1]", second, tx, ty, "la", look.INK2, box)
        ty += adv(second)
    if fig is not None:
        colour = _tone(image, fig, heading)
        if fig_t is not None:
            page.put(f"{path}.cells[{len(cells) - 1}]", fig_t, tx, ty + inner, "la", colour, box)
        else:
            page.line(f"{path}.cells[{len(cells) - 1}]", fig, fsize, right, y + h / 2, "rm", colour, box)
    return y + h


def _notes_panel(page: look.Page, notes: Sequence[str], x0: int, x1: int, y: int) -> int:
    """The notes, each after a gold dot, on a lifted dark panel (SUB). Returns its bottom."""
    pad_x, pad_y, dot_gap = 30, _gap(page, 22), 30
    width = x1 - x0 - 2 * pad_x - dot_gap
    size = page.px(NOTE_STYLE)
    texts = [page.measure(n, size, width, f"notes[{k}]", leading=NOTE_STYLE.leading, balance=True)
             for k, n in enumerate(notes)]
    sep = _gap(page, 10)
    h = 2 * pad_y + sum(t.height for t in texts) + sep * (len(texts) - 1)
    box = page.rect((x0, y, x1, y + h), 20, look.NOTE_PANEL)
    ty = y + pad_y
    asc = page.font(size).getmetrics()[0]
    for k, t in enumerate(texts):
        cy = ty + asc * 0.62
        page.ellipse((x0 + pad_x, cy - 5, x0 + pad_x + 10, cy + 5), look.GOLD)
        page.put(f"notes[{k}]", t, x0 + pad_x + dot_gap, ty, "la", look.SUB, box)
        ty += t.height + sep
    return y + h


def _lay_rows(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """The headline (and subtitle), the header card, each section's rows as cards, the notes panel. When the cards do
    not fit at this step, the ROW CARDS flex first (mock_form4.roundup: `h = min(190, room per row)`, the disc
    `min(74, (h − 22) // 2)`) — the headline, the names and the figures keep their size; only a row that would be
    shorter than its own text makes the whole page step down the ramp."""
    if _is_f13_list(image):
        # news-v23: a 13F series' list (f13_list_*.jpg; the video cover and data card on the 9:16 frame)
        _lay_f13_list(page, image, top, bottom)
        return
    if _is_earnings_table(image):
        # news-v23: the earnings table of 2-4 companies (earnings_multi*.jpg; its video cover on the 9:16 frame)
        _lay_earnings(page, image, top, bottom)
        return
    if _is_congress_list(image):
        # news-v23: the Congress Count of 2-3 companies (congress_multi3.jpg; its video cover on the 9:16 frame)
        _lay_congress(page, image, top, bottom)
        return
    if _is_stakes_list(image):
        # news-v23: one investor's stakes in 1-4 public companies (stakes_nvidia*.jpg; its video cover on 9:16)
        _lay_stakes(page, image, top, bottom)
        return
    if _is_theme_list(image):
        # news-v23 phase 3: the weekly theme post's video cover — its picture's first companies
        _lay_theme_list(page, image, top, bottom)
        return
    if _is_insider_list(image):
        # owner decisions 2026-11-20: the insider list (insider_multi*.jpg) and every Form 4 list's compact cover
        _lay_insider_list(page, image, top, bottom)
        return
    spec = image.spec
    x0, x1, tx0, tx1 = _columns(page)
    y: Optional[int] = None
    if page.frame is POST_IMAGE_FRAME:
        # the roundup's own headline ladder (ROWS_HEAD_PX: mock_form4.roundup's 76 → 56, ≤ 4 lines), never larger
        # than the ramp's size at this step (the page's later steps only ever shrink it — the floor is unchanged).
        # Owner decisions 2026-11-20 (form4_roundup5.jpg): a roundup of ROWS_THREE_LINE_ROWS rows or more sets its
        # headline in THREE lines (one-pixel steps: the mock's 65 px), so each row keeps ~150 px
        many = sum(len(sec["rows"]) for sec in spec["sections"]) >= ROWS_THREE_LINE_ROWS
        for hpx in (ROWS_HEAD_PX_MANY if many else ROWS_HEAD_PX):
            if hpx > page.px(HEADLINE_STYLE):
                continue
            mk = page.mark()
            try:
                y = look.headline(page, "title", spec["title"], HEADLINE_STYLE, tx0, top, tx1 - tx0,
                                  max_lines=3 if many else 4, size=hpx)
                break
            except _NoFit:
                page.rollback(mk)
    if y is None:
        y = look.headline(page, "title", spec["title"], HEADLINE_STYLE, tx0, top, tx1 - tx0)
    if spec.get("subtitle") is not None:
        t = page.measure(spec["subtitle"], page.px(SUBTITLE_STYLE), tx1 - tx0, "subtitle",
                         leading=SUBTITLE_STYLE.leading)
        y += _gap(page, 14)
        page.put("subtitle", t, tx0, y, "la", look.SUB, page.frame.area)
        y += t.height
    mark = page.mark()
    start = y + _gap(page, 34, 12)
    cy, logo_rows = _rows_body(page, image, start, None)
    if cy > bottom and logo_rows:
        # the overflow shared by the logo rows: each one that much shorter (never under its own text — `_row_card`),
        # its disc no smaller than ROW_FLEX_MIN_SHARE of this step's (a deeper squeeze steps the page instead)
        natural = 2 * page.px(ROW_DISC) + 2 * _gap(page, 21, 7)
        row_h = natural - int(math.ceil((cy - bottom) / logo_rows))
        min_r = max(ROW_DISC.floor, int(math.ceil(page.px(ROW_DISC) * ROW_FLEX_MIN_SHARE)))
        if row_h >= 2 * min_r + 22:
            page.rollback(mark)
            cy, _n = _rows_body(page, image, start, row_h)
    if cy > bottom:
        raise _NoFit(f"rows: the cards end at {cy}px, room to {bottom}px")
    spare = bottom - cy
    if spare > 60 and start < bottom:
        page.shift(mark, spare // 2)


def _rows_body(page: look.Page, image: TemplateImage, cy: int, row_h: Optional[int]) -> Tuple[int, int]:
    """`_lay_rows`' cards from `cy` down (each logo row `row_h` tall when given). Returns (their bottom, the number
    of rows with a logo)."""
    spec, logos = image.spec, image.logos
    x0, x1, _tx0, _tx1 = _columns(page)
    logo_rows = 0
    if spec.get("header") is not None:
        cy = _company_block(page, spec["header"], logos, x0, x1, cy, "header", chip=True) + _gap(page, 18, 6)
    for i, sec in enumerate(spec["sections"]):
        path = f"sections[{i}]"
        if i:
            cy += _gap(page, 16, 6)
        heading = sec.get("heading")
        if heading is not None:
            t = page.measure(heading, page.px(HEADING_STYLE), x1 - x0 - 72, f"{path}.heading", leading=1.2)
            right = all(len(r["cells"]) >= 2 for r in sec["rows"])
            if right:
                page.put(f"{path}.heading", t, x1 - 36, cy, "ra", look.GREY, page.frame.area)
            else:
                page.put(f"{path}.heading", t, x0 + 36, cy, "la", look.GREY, page.frame.area)
            cy += t.height + _gap(page, 12, 4)
        for j, row in enumerate(sec["rows"]):
            if j:
                # owner decisions 2026-11-20 (form4_roundup5.jpg: `gap = 14`): a longer roundup's rows sit closer
                cy += _gap(page, 14 if len(sec["rows"]) >= ROWS_THREE_LINE_ROWS else 18, 5)
            if row.get("logo") is not None:
                logo_rows += 1
            cy = _row_card(page, image, row, f"{path}.rows[{j}]", x0, x1, cy, heading,
                           row_h if row.get("logo") is not None else None, row_h_rows=len(sec["rows"]))
        if sec.get("more") is not None:
            t = page.measure(sec["more"], page.px(MORE_STYLE), x1 - x0, f"{path}.more", leading=1.2)
            cy += _gap(page, 14, 4)
            page.put(f"{path}.more", t, (x0 + x1) / 2, cy, "ma", look.GREY, page.frame.area)
            cy += t.height
    notes = spec.get("notes") or ()
    if notes:
        cy = _notes_panel(page, notes, x0, x1, cy + _gap(page, 26, 10))
    return int(cy), logo_rows


# ── the 13F list (news-v23, f13_list_berkshire.jpg / f13_list_pershing.jpg / cover_13f_berkshire.jpg) ─────────────

#: The 13F LIST's row metrics at ramp step 0 — the approved 160-px list row (mock_f13_lists.sizes_for: logo disc r 56,
#: name 40, status 26, the signed figure 62, the chip 22; a row shorter than F13_ROW_SPEC scales them down, the
#: figure never below 90 % and the name never below its floor) — and the column label / "+N more" line (24 px on the
#: picture, 26 on the video cover). Every size still steps down the ramp with the page.
F13_DISC = _Style(56, 24, 1.0)
F13_NAME_STYLE = _Style(40, 22, 1.12)
F13_STATUS_STYLE = _Style(26, 22, 1.2)
F13_FIGURE_STYLE = _Style(62, 30, 1.0)
F13_CHIP_PX = _Style(22, 22, 1.0)
F13_LABEL_PX = _Style(24, 22, 1.2)
F13_VIDEO_LABEL_PX = _Style(26, 22, 1.2)
#: The approved row height the metrics are drawn at, and the tallest a row card grows when the list is short (the
#: mock's Pershing: three rows "only get taller cards (cap 210 px)", the SAME type — never a bigger type scale).
F13_ROW_SPEC = 160
F13_ROW_CAP = 210
#: A row shorter than this puts its chip at the end of the name's line (the mock's six-row `inline_chip`).
F13_INLINE_CHIP_BELOW = 136
#: The share of a TWO-row 13F picture's slack placed above its column label (the rest under the list) — the 13F mock
#: script's default `list_block(air=0.4)`; three or more rows rest on the footer (PSH_AIR = 1.0). Review 2026-11-20: no
#: approved mock draws two rows — the owner's eye on fabrikam2.jpg decides.
F13_TWO_ROW_AIR = 0.4
#: A figure narrower than this (on a long name) is never chosen: the name shrinks to its floor first.
F13_FIGURE_FLOOR_PX = 44
#: The name's floor before it wraps / shrinks to the ramp's floor (mock name_floor 30).
F13_NAME_FLOOR_PX = 30
#: The 13F PICTURE's headline ladder (mock_f13_lists, art director round 3: ONE headline size for every 13F post — the
#: largest both approved filers fit, 72 px — stepping down only when a title does not fit in 3 lines); the video cover
#: keeps the ramp's 80 (cover_13f_berkshire.jpg). Never above the ramp's size at the step (the floor is unchanged).
F13_PICTURE_HEAD_PX: Tuple[int, ...] = tuple(range(72, 54, -2))
#: The gap from the title's foot (the mock's `n × int(size × 1.08) + 10`) to the column label: the picture's 22
#: (`list_block(top=y + 22)`), the video cover's 26 (render_cover).
F13_LIST_GAP, F13_VIDEO_LIST_GAP = 22, 26
#: A short PICTURE list (fewer than five rows) is drawn with the five-row list's metrics in its room — only its cards
#: are taller (mock_f13_lists: Pershing's three rows take Berkshire's five-row `spec_h`, "the SAME type").
F13_SPEC_ROWS = 5


def _is_f13_list(image: TemplateImage) -> bool:
    """Is `image` a news-v23 13F LIST — a 13F series' `rows` picture of ONE headed section whose rows each carry a logo
    and three cells (name, status, signed figure), with no subtitle and no notes? A news-v22 13F roundup (several
    kind sections under a counts subtitle — an older web) is not: it keeps the generic rows layout."""
    spec = image.spec
    if image.layout != "rows" or image.series not in look.SIGNED_SERIES:
        return False
    sections = spec.get("sections") or ()
    if len(sections) != 1 or spec.get("subtitle") is not None or spec.get("notes"):
        return False
    sec = sections[0]
    if sec.get("heading") is None:
        return False
    return all(r.get("logo") is not None and len(r["cells"]) == 3 for r in sec["rows"])


def _f13_title(page: look.Page, image: TemplateImage, top: int) -> int:
    """The two-tone title ("BERKSHIRE HATHAWAY'S" white, "LATEST 13F" gold, at most three lines) and — for a listed
    filer (the spec's `header`) — the FILER's plate top-right: its logo disc in a gold ring, its ticker in a gold pill
    over the disc's foot (the Money Map plate, mock_f13_lists.title_block), the title in the column beside it. Returns
    the block's bottom."""
    spec, logos = image.spec, image.logos
    _x0, _x1, tx0, tx1 = _columns(page)
    header = spec.get("header")
    col_w = tx1 - tx0
    plate_bottom = top
    ladder: Tuple[Optional[int], ...] = (None,)
    if page.frame is POST_IMAGE_FRAME:
        ladder = tuple(px for px in F13_PICTURE_HEAD_PX if px <= page.px(HEADLINE_STYLE)) + (None,)
    if header is not None:
        r = page.px(PLATE_DISC)
        ring = r + look.GOLD_RING_GAP
        cx, cy = tx1 - ring, top + 6 + ring
        page.disc(cx, cy, r, logos[header["logo"]], ring=True)
        cs = page.px(CHIP_PX)
        chip_text, chip_field = ((header["chip"], "header.chip") if header.get("chip") is not None
                                 else (header["name"], "header.name"))
        cw, _ch = look.chip_size(page, chip_text, cs)
        pill = look.chip(page, chip_field, chip_text, cs, min(cx - cw / 2, tx1 - cw), cy + r - 4,
                         fill=look.GOLD, ink=look.INK)
        plate_bottom = pill[3] + _gap(page, 12)
        col_w = int(min(cx - ring, pill[0]) - tx0 - 28)

    def title(x_w: float, start: float) -> Optional[int]:
        """The title at the first ladder size that sets it in ≤ 3 lines in `x_w` (None: the ramp's size), its
        bottom as the mock's (`n × int(size × 1.08) + 10` under its top); None when no size fits."""
        for px in ladder:
            mk = page.mark()
            try:
                foot = look.headline(page, "title", spec["title"], HEADLINE_STYLE, tx0, start, x_w, max_lines=3,
                                     size=px)
            except _NoFit:
                page.rollback(mk)
                continue
            size = page.px(HEADLINE_STYLE) if px is None else px
            asc, desc = page.font(size).getmetrics()
            return int(round(foot - (asc + desc) + round(size * look.HEADLINE_LEADING))) + _gap(page, 10)
        return None

    y: Optional[int] = title(col_w, top) if col_w >= BARS_HEAD_MIN_COLUMN else None
    if y is None:
        # a title whose words do not fit beside the plate: full width under it (`_lay_bars`' rule)
        start = plate_bottom + _gap(page, 16) if header is not None else top
        y = title(tx1 - tx0, start)
        if y is None:
            raise _NoFit("title: the 13F title does not fit in 3 lines")
        return y
    return max(y, plate_bottom)


def _f13_fit(page: look.Page, text: str, size: int, width: float, floor: int) -> int:
    """The largest size ≤ `size` (down to `floor`) at which `text` fits `width` on one line."""
    while size > floor and page.length(text, size) > width:
        size -= 1
    return size


def _f13_name_plan(page: look.Page, name: str, size: int, width: float, *, wrap: bool,
                   floor: int) -> Tuple[int, Tuple[str, ...]]:
    """(size, lines) of a row's company name in `width`: its size; else down to 90 % of it on one line; else (a tall
    row, `wrap`) two balanced lines at the largest size that fits them, else one line down to `floor`. _NoFit when even
    that does not fit (the page steps)."""
    if page.length(name, size) <= width:
        return size, (name,)
    light = _f13_fit(page, name, size, width, max(floor, int(round(size * 0.9))))
    if page.length(name, light) <= width:
        return light, (name,)
    words = name.split()
    if wrap and len(words) > 1:
        s = size
        while s >= floor:
            best = min(range(1, len(words)), key=lambda i: max(page.length(" ".join(words[:i]), s),
                                                              page.length(" ".join(words[i:]), s)))
            lines = (" ".join(words[:best]), " ".join(words[best:]))
            if max(page.length(ln, s) for ln in lines) <= width:
                return s, lines
            s -= 1
    s = _f13_fit(page, name, size, width, floor)
    if page.length(name, s) > width:
        raise _NoFit(f"a 13F row's name {name[:40]!r} does not fit {int(width)}px at {s}px")
    return s, (name,)


def _lay_f13_list(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """The 13F LIST (news-v23 — f13_list_berkshire.jpg, f13_list_pershing.jpg; on the 9:16 frame the cover,
    cover_13f_berkshire.jpg): the title (and the filer's plate), the grey column label "Change in shares" over the
    figure column, one full-width warm card per row — the logo disc, the ticker chip in the row's tone (GREEN for an
    increase or a new holding, RED for a decrease or a holding closed out: the figure's own sign, `look.signed_tone`),
    the name, the grey status line and the signed change in shares right-aligned, big, in that tone — and the grey
    "+N more changes in the caption" line. The rows take the height (up to F13_ROW_CAP); a short list's slack sits
    above the label (the picture: the list rests on the footer) or is shared (the video). One figure, name and status
    size for every row; a row shorter than F13_ROW_SPEC scales its metrics down (mock_f13_lists.sizes_for)."""
    spec, logos = image.spec, image.logos
    x0, x1, _tx0, _tx1 = _columns(page)
    sec = spec["sections"][0]
    rows = list(sec["rows"])
    n = len(rows)
    y = _f13_title(page, image, top)
    video = page.frame is not POST_IMAGE_FRAME
    label_px = page.px(F13_VIDEO_LABEL_PX if video else F13_LABEL_PX)
    page.legible(label_px, "sections[0].heading")
    more = sec.get("more")
    list_top = y + _gap(page, F13_VIDEO_LIST_GAP if video else F13_LIST_GAP)
    gap = _gap(page, 16 if n >= 4 else 22, 6)
    label_h = label_px + _gap(page, 16, 6)
    more_t = (page.measure(more, label_px, x1 - x0, "sections[0].more", leading=1.2) if more is not None else None)
    more_h = (_gap(page, 20, 8) + more_t.height) if more_t is not None else 0
    avail = (bottom - list_top) - label_h - more_h
    row_h = int(min(F13_ROW_CAP, (avail - gap * (n - 1)) / n))
    # the metrics' row: a short PICTURE list takes the five-row list's metrics in its room (mock_f13_lists' spec_h:
    # Pershing's three rows drawn with Berkshire's type, only their cards taller)
    spec_h = row_h
    if not video and n < F13_SPEC_ROWS:
        g5 = _gap(page, 16, 6)
        spec_h = min(row_h, int((avail - g5 * (F13_SPEC_ROWS - 1)) / F13_SPEC_ROWS))
    s = min(1.0, spec_h / F13_ROW_SPEC)
    # mock_f13_lists.sizes_for: the disc scales with the row, the name and the status never below 90 % / 24-of-26
    r = max(16, int(round(page.px(F13_DISC) * s)))
    pad, text_gap, right_pad = _gap(page, 28, 16), _gap(page, 28, 16), _gap(page, 36, 20)
    name_px = max(F13_NAME_STYLE.floor, int(round(page.px(F13_NAME_STYLE) * max(0.9, s))))
    name_floor = max(min(F13_NAME_FLOOR_PX, name_px), F13_NAME_STYLE.floor)
    status_px = max(F13_STATUS_STYLE.floor, int(round(page.px(F13_STATUS_STYLE) * max(24 / 26, s))))
    chip_px = page.px(F13_CHIP_PX)
    inline = spec_h < F13_INLINE_CHIP_BELOW
    wrap = spec_h > 170
    chip_gap, status_gap = _gap(page, 10, 4), _gap(page, 14, 6)
    tx = x0 + pad + 2 * r + text_gap
    right = x1 - right_pad
    name_gap = _gap(page, 36, 16)
    figs = [row["cells"][2] for row in rows]
    # ONE figure size: the largest at which every name keeps `name_gap` clear of its figure at the name's floor
    fig_px = int(round(page.px(F13_FIGURE_STYLE) * max(s, 0.9)))
    fig_floor = max(min(F13_FIGURE_FLOOR_PX, fig_px), F13_FIGURE_STYLE.floor)
    chip_w = [look.chip_size(page, row["chip"], chip_px)[0] + 16 if (inline and row.get("chip")) else 0
              for row in rows]

    def fits(fs: int) -> bool:
        for row, fig, cw in zip(rows, figs, chip_w):
            room = right - page.length(fig, fs) - name_gap - tx - cw
            name = row["cells"][0]
            narrowest = page.length(name, name_floor)
            if wrap and len(name.split()) > 1:
                words = name.split()
                narrowest = min(narrowest, min(max(page.length(" ".join(words[:i]), name_floor),
                                                   page.length(" ".join(words[i:]), name_floor))
                                               for i in range(1, len(words))))
            if narrowest > room:
                return False
        return True

    while fig_px > fig_floor and not fits(fig_px):
        fig_px -= 1
    page.legible(fig_px, "sections[0].rows[0].cells[2]")
    # one name size and one status size for the list (the smallest any row needs)
    plans = []
    for j, (row, fig, cw) in enumerate(zip(rows, figs, chip_w)):
        room = right - page.length(fig, fig_px) - name_gap - tx
        plans.append(_f13_name_plan(page, row["cells"][0], name_px, room - cw, wrap=wrap, floor=name_floor))
        status_room = room
        if page.length(row["cells"][1], _f13_fit(page, row["cells"][1], status_px, status_room,
                                                  F13_STATUS_STYLE.floor)) > status_room:
            raise _NoFit(f"sections[0].rows[{j}].cells[1]: the status does not fit {int(status_room)}px")
    ns = min(p[0] for p in plans)
    ss = min(_f13_fit(page, row["cells"][1], status_px, right - page.length(fig, fig_px) - name_gap - tx,
                      F13_STATUS_STYLE.floor) for row, fig in zip(rows, figs))
    page.legible(ns, "sections[0].rows[0].cells[0]")
    page.legible(ss, "sections[0].rows[0].cells[1]")
    # re-plan every name at the list's one size (a wrap decided at a larger size may now fit one line)
    names = [_f13_name_plan(page, row["cells"][0], ns, right - page.length(fig, fig_px) - name_gap - tx - cw,
                            wrap=wrap, floor=ns)[1] for row, fig, cw in zip(rows, figs, chip_w)]
    used = n * row_h + gap * (n - 1)
    slack = max(0, avail - used)
    # the picture: the slack above the label, the list resting on the footer (mock_f13_lists, PSH_AIR = 1.0) — but a
    # TWO-row list (no approved mock: review 2026-11-20) takes the mock's default share (`list_block(air=0.4)`), else
    # ~470 px of empty navy sat under the headline; the video shares its slack
    air = 0.5 if video else F13_TWO_ROW_AIR if n == 2 else 1.0
    yy = list_top + int(slack * air)
    # the column label, right-aligned over the figure column
    heading = page.measure(sec["heading"], label_px, x1 - x0 - 72, "sections[0].heading", leading=1.2)
    # its INK top on the label row's top (the mock: `text(…, y − textbbox[1])`)
    page.put("sections[0].heading", heading, right, yy - page.font(label_px).getbbox(heading.lines[0], anchor="ra")[1],
             "ra", look.GREY, page.frame.area)
    yy += label_h
    asc_n = page.font(ns).getbbox("H", anchor="ls")[1]
    asc_s = page.font(ss).getbbox("H", anchor="ls")[1]
    cap_n, cap_s = -asc_n, -asc_s
    nlh = int(round(ns * 1.12))
    for j, (row, lines, fig) in enumerate(zip(rows, names, figs)):
        path = f"sections[0].rows[{j}]"
        box = look.card(page, (x0, yy, x1, yy + row_h))
        cy = yy + row_h / 2
        page.disc(x0 + pad + r, cy, r, logos[row["logo"]])
        colour = look.tone_colour(look.signed_tone(fig))
        page.line(f"{path}.cells[2]", fig, fig_px, right, cy, "rm", colour, box)
        chip_text = row.get("chip")
        chip_h = look.chip_size(page, chip_text, chip_px)[1] if chip_text is not None else 0
        if inline or chip_text is None:
            block = (len(lines) - 1) * nlh + cap_n + status_gap + cap_s
            base = cy - block / 2 + cap_n
        else:
            block = chip_h + chip_gap + (len(lines) - 1) * nlh + cap_n + status_gap + cap_s
            ctop = cy - block / 2
            look.chip(page, f"{path}.chip", chip_text, chip_px, tx, ctop, fill=colour, ink=look.WHITE, bound=box)
            base = ctop + chip_h + chip_gap + cap_n
        if block > row_h - 2 * _gap(page, 10, 4):
            raise _NoFit(f"{path}: its text block ({int(block)}px) does not fit the row ({row_h}px)")
        for i, ln in enumerate(lines):
            page.line(f"{path}.cells[0]", ln, ns, tx, base + i * nlh, "ls", look.INK, box)
        if inline and chip_text is not None:
            last = lines[-1]
            cx0 = tx + page.length(last, ns) + 16
            mid = base + (len(lines) - 1) * nlh - cap_n / 2
            look.chip(page, f"{path}.chip", chip_text, chip_px, cx0, mid - chip_h / 2, fill=colour, ink=look.WHITE,
                      bound=box)
        sbase = base + (len(lines) - 1) * nlh + status_gap + cap_s
        page.line(f"{path}.cells[1]", row["cells"][1], ss, tx, sbase, "ls", look.INK2, box)
        yy += row_h + gap
    yy -= gap
    if more_t is not None:
        # its INK top 20 px under the last card (the mock: `y + 20 − textbbox[1]`)
        ink_top = page.font(label_px).getbbox(more_t.lines[0], anchor="ma")[1]
        page.put("sections[0].more", more_t, (x0 + x1) / 2, yy + _gap(page, 20, 8) - ink_top, "ma", look.GREY,
                 page.frame.area)
        yy += _gap(page, 20, 8) + more_t.height
    if yy > bottom + 0.5:
        raise _NoFit(f"13F list: the rows end at {int(yy)}px, room to {bottom}px")


# ── the Form 4 lists (owner decisions 2026-11-20 — insider_multi3.jpg / insider_multi5.jpg /
#    insider_multi5_alt_with_note.jpg / insider_multi5_cover_alt_all5.jpg; mock_insider_multi.py) ─────────────────────

#: The series whose lists `_lay_insider_list` draws: the insider list's PICTURE and COVER, and the weekly roundups'
#: COVER only (their picture keeps the approved chip-above row: the generic `_lay_rows`, form4_roundup5.jpg).
INSIDER_LIST_SERIES = frozenset({"insider_spotlight"})
INSIDER_COVER_SERIES = frozenset({"ceo_buys", "insider_buys", "insider_spotlight"})


@dataclass(frozen=True)
class _IlSizes:
    """One of the mock's row size sets (mock_insider_multi.SIZES_*) at ramp step 0: the logo disc's radius, the name,
    the roles line, the detail line and the total (px), the roles' and the detail's leading (of their size), the
    tallest a row grows and the gap between rows."""

    r: _Style
    name: _Style
    role: _Style
    detail: _Style
    amount: _Style
    lead2: float
    lead3: float
    cap: int
    gap: int
    #: the least room the text block keeps from the card's top and bottom (the mock's 14; the tight rows 10)
    air: int = 14


#: 3 rows: the approved roundup's scale (SIZES_BIG, ≤ 232 px rows, 18 px apart); 4-5 rows: SIZES_FIVE (≤ 190 px, 14
#: apart); five rows WITH the note panel: SIZES_FIVE_TIGHT (insider_multi5_alt_with_note.jpg, ~141 px rows); the cover:
#: SIZES_COMPACT (≤ 200 px, 12 apart, no detail line).
IL_BIG = _IlSizes(_Style(74, 24, 1.0), _Style(44, 22, 1.12), _Style(30, 22, 1.2), _Style(26, 22, 1.2),
                  _Style(76, 30, 1.0), 1.40, 1.55, 232, 18)
IL_FIVE = _IlSizes(_Style(62, 24, 1.0), _Style(38, 22, 1.12), _Style(28, 22, 1.2), _Style(24, 22, 1.2),
                   _Style(62, 30, 1.0), 1.40, 1.55, 190, 14)
IL_TIGHT = _IlSizes(_Style(58, 24, 1.0), _Style(36, 22, 1.12), _Style(26, 22, 1.2), _Style(24, 22, 1.2),
                    _Style(58, 30, 1.0), 1.34, 1.45, 190, 14, air=10)
IL_COMPACT = _IlSizes(_Style(48, 24, 1.0), _Style(36, 22, 1.12), _Style(28, 22, 1.2), _Style(24, 22, 1.2),
                      _Style(56, 30, 1.0), 1.40, 1.55, 200, 12)
#: The headline: TWO lines (insider_multi3.jpg: "INSIDERS AT 3 COMPANIES" / "DISCLOSED BUYING STOCK" at 68 px), the
#: largest size from 80 that sets it there; the ramp's own size (any lines the height allows) only when none does.
IL_HEAD_PX: Tuple[int, ...] = tuple(range(80, 51, -1))
#: The grey column label ("Total purchases", 24 px on its own baseline 32 px under the headline's line box), and the
#: room from its baseline to the first card (8 + 18 px) — mock_insider_multi.label_row.
IL_LABEL_PX = _Style(24, 22, 1.2)
#: The name's floor before it wraps to two lines (the mock's `fit_size(..., floor=30)`), the roles line's floor, and
#: the smallest share of its size the list's ONE total size may step to before the page steps.
IL_NAME_FLOOR_PX = 30
IL_ROLE_FLOOR_PX = 22
IL_AMOUNT_MIN_SHARE = 0.75
#: Inside a row card (mock_insider_multi.row_card): the disc's inset, the disc → text gap, the right pad, the name →
#: chip gap, the text column → total gap; and the least room the text block keeps from the card's top and bottom.
IL_DISC_INSET, IL_TEXT_GAP, IL_RIGHT_PAD, IL_CHIP_GAP, IL_AMOUNT_GAP = 24, 26, 34, 14, 28
IL_CHIP_PX = _Style(22, 22, 1.0)


def _is_insider_list(image: TemplateImage) -> bool:
    """A Form 4 LIST this placer draws: the insider list's picture or cover (INSIDER_LIST_SERIES), or any Form 4 list's
    video COVER (`TemplateImage.cover`, INSIDER_COVER_SERIES) — one section whose rows each carry a logo and three
    cells (name, roles, the total)."""
    if image.layout != "rows" or not (image.series in INSIDER_LIST_SERIES
                                      or (image.cover and image.series in INSIDER_COVER_SERIES)):
        return False
    sections = image.spec.get("sections") or ()
    if len(sections) != 1 or image.spec.get("subtitle") is not None:
        return False
    return all(r.get("logo") is not None and len(r["cells"]) == 3 for r in sections[0]["rows"])


def _il_name_plan(page: look.Page, name: str, start: int, width: float, floor: int) -> Tuple[int, Tuple[str, ...]]:
    """(size, lines) of a row's company name in `width`: the largest size from `start` down to `floor` on ONE line
    (the mock's fit_size); else two balanced lines at the largest size that fits them; _NoFit when not even that."""
    size = start
    while size > floor and page.length(name, size) > width:
        size -= 1
    if page.length(name, size) <= width:
        return size, (name,)
    words = name.split()
    if len(words) > 1:
        s = start
        while s >= max(floor, IL_ROLE_FLOOR_PX):
            best = min(range(1, len(words)), key=lambda i: max(page.length(" ".join(words[:i]), s),
                                                              page.length(" ".join(words[i:]), s)))
            lines = (" ".join(words[:best]), " ".join(words[best:]))
            if max(page.length(ln, s) for ln in lines) <= width:
                return s, lines
            s -= 1
    raise _NoFit(f"an insider row's name {name[:40]!r} does not fit {int(width)}px at {floor}px")


def _lay_insider_list(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """A Form 4 LIST (owner decisions 2026-11-20 — mock_insider_multi.py): the two-tone headline in two lines, the grey
    column label right-aligned over the totals (the list's `heading`, "Total purchases"; a roundup cover has none), then
    one warm card per row — the logo disc, the company name with its ticker chip BESIDE it, the roles line under it
    (INK2), the GREEN total right-aligned and centred on the name + roles block, and (the picture only) ONE small grey
    detail line under them ("28K shares · $71.20–$71.80 a share, as filed") running the text column's width — the
    "+N more companies in the caption" line, and the notes on their dark panel. On the 9:16 cover the rows are compact
    (insider_multi5_cover_alt_all5.jpg: no detail line). Sizes: IL_BIG for ≤ 3 rows, IL_FIVE for more, IL_TIGHT when
    five rows share the page with the note panel; the page steps the ramp only when none fits."""
    spec, logos = image.spec, image.logos
    x0, x1, tx0, tx1 = _columns(page)
    sec = spec["sections"][0]
    rows = list(sec["rows"])
    n = len(rows)
    video = page.frame is not POST_IMAGE_FRAME
    notes = list(spec.get("notes") or ())
    # the headline: two lines from 80 px down (insider_multi3.jpg: 68 px), its foot as the mock's (`n × int(size ×
    # 1.08) + 10` under its top); the ramp's own size when no ladder size sets it in two lines
    y: Optional[int] = None
    for lines_max, ladder in ((2, IL_HEAD_PX), (3, ROWS_HEAD_PX_MANY)):
        # two lines (the insider list's headline); a roundup COVER's longer title ("At least 5 CEOs disclosed buying
        # their own company's stock") takes three, as its five-row picture does (form4_roundup5.jpg)
        for hpx in ladder:
            if hpx > page.px(HEADLINE_STYLE):
                continue
            mk = page.mark()
            try:
                foot = look.headline(page, "title", spec["title"], HEADLINE_STYLE, tx0, top, tx1 - tx0,
                                     max_lines=lines_max, size=hpx)
            except _NoFit:
                page.rollback(mk)
                continue
            asc, desc = page.font(hpx).getmetrics()
            y = int(round(foot - (asc + desc) + round(hpx * look.HEADLINE_LEADING))) + _gap(page, 10)
            break
        if y is not None:
            break
    if y is None:
        y = look.headline(page, "title", spec["title"], HEADLINE_STYLE, tx0, top, tx1 - tx0) + _gap(page, 10)
    right = x1 - _gap(page, IL_RIGHT_PAD, 20)
    label_px = page.px(IL_LABEL_PX)
    heading = sec.get("heading")
    if heading is not None:
        page.legible(label_px, "sections[0].heading")
        base = y + _gap(page, 4, 2) + label_px + 4
        page.line("sections[0].heading", heading, label_px, right, base, "rs", look.GREY, page.frame.area)
        rows_top = base + _gap(page, 8, 4) + _gap(page, 18, 8)
    else:
        rows_top = y + _gap(page, 22, 10)
    more = sec.get("more")
    more_t = page.measure(more, label_px, x1 - x0, "sections[0].more", leading=1.2) if more is not None else None
    more_h = (_gap(page, 20, 8) + more_t.height) if more_t is not None else 0
    note_h = 0
    if notes:
        size = page.px(NOTE_STYLE)
        width = x1 - x0 - 2 * 30 - 30
        texts = [page.measure(t, size, width, f"notes[{k}]", leading=NOTE_STYLE.leading, balance=True)
                 for k, t in enumerate(notes)]
        note_h = 2 * _gap(page, 22) + sum(t.height for t in texts) + _gap(page, 10) * (len(texts) - 1) + _gap(page, 26, 10)
    avail = bottom - rows_top - more_h - note_h
    if video:
        plans = (IL_COMPACT,)
    elif n <= 3:
        plans = (IL_BIG, IL_FIVE, IL_TIGHT)
    else:
        plans = (IL_FIVE, IL_TIGHT)
    detail = not video
    reason = ""
    for sz in plans:
        mark = page.mark()
        try:
            end = _il_rows(page, image, rows, sz, rows_top, avail, x0, x1, right, detail=detail)
        except _NoFit as e:
            page.rollback(mark)
            reason = str(e)
            continue
        break
    else:
        raise _NoFit(f"insider list: {reason}")
    yy = end
    if more_t is not None:
        ink_top = page.font(label_px).getbbox(more_t.lines[0], anchor="ma")[1]
        page.put("sections[0].more", more_t, (x0 + x1) / 2, yy + _gap(page, 20, 8) - ink_top, "ma", look.GREY,
                 page.frame.area)
        yy += _gap(page, 20, 8) + more_t.height
    if notes:
        yy = _notes_panel(page, notes, x0, x1, yy + _gap(page, 26, 10))
    if yy > bottom + 0.5:
        raise _NoFit(f"insider list: the list ends at {int(yy)}px, room to {bottom}px")


def _il_rows(page: look.Page, image: TemplateImage, rows: Sequence[Mapping[str, Any]], sz: _IlSizes, top: int,
             avail: int, x0: int, x1: int, right: int, *, detail: bool) -> int:
    """The row cards at one size set (mock_insider_multi.row_card) from `top`, sharing `avail` px; returns the last
    card's bottom. ONE total size for the list (the largest, down to IL_AMOUNT_MIN_SHARE of the set's, at which every
    row's name — at its floor, with its chip — and roles line fit left of it); _NoFit when a row's text block does not
    fit its card."""
    logos = image.logos
    n = len(rows)
    gap = _gap(page, sz.gap, 6)
    h = int(min(sz.cap, (avail - gap * (n - 1)) // n))
    r = min(page.px(sz.r), (h - 36) // 2)
    if r < sz.r.floor:
        raise _NoFit(f"insider list: rows of {h}px leave the logo disc under {sz.r.floor}px")
    tx = x0 + _gap(page, IL_DISC_INSET, 12) + 2 * r + _gap(page, IL_TEXT_GAP, 14)
    chip_px = page.px(IL_CHIP_PX)
    name_px, role_px, det_px = page.px(sz.name), page.px(sz.role), page.px(sz.detail)
    amount_gap = _gap(page, IL_AMOUNT_GAP, 14)
    chip_gap = _gap(page, IL_CHIP_GAP, 8)

    def fits(amt_px: int) -> bool:
        for row in rows:
            col_right = right - page.length(row["cells"][2], amt_px) - amount_gap
            chip_w = look.chip_size(page, row["chip"], chip_px)[0] + chip_gap if row.get("chip") else 0
            if page.length(row["cells"][1], IL_ROLE_FLOOR_PX) > col_right - tx:
                return False
            words = row["cells"][0].split()
            floor = min(IL_NAME_FLOOR_PX, name_px)
            narrow = min([page.length(row["cells"][0], floor)] + [
                max(page.length(" ".join(words[:i]), floor), page.length(" ".join(words[i:]), floor))
                for i in range(1, len(words))])
            if narrow + chip_w > col_right - tx:
                return False
        return True

    amt_px = page.px(sz.amount)
    amt_floor = max(sz.amount.floor, int(round(amt_px * IL_AMOUNT_MIN_SHARE)))
    while amt_px > amt_floor and not fits(amt_px):
        amt_px -= 1
    if not fits(amt_px):
        raise _NoFit("insider list: a name or a roles line does not fit beside the totals")
    page.legible(amt_px, "sections[0].rows[0].cells[2]")
    cap_of = {}

    def cap(size: int) -> float:
        if size not in cap_of:
            cap_of[size] = -page.font(size).getbbox("H", anchor="ls")[1]
        return cap_of[size]

    yy = top
    for j, row in enumerate(rows):
        path = f"sections[0].rows[{j}]"
        box = look.card(page, (x0, yy, x1, yy + h))
        cy = yy + h / 2
        page.disc(x0 + _gap(page, IL_DISC_INSET, 12) + r, cy, r, logos[row["logo"]])
        name, role, total = row["cells"]
        col_right = right - page.length(total, amt_px) - amount_gap
        chip_text = row.get("chip")
        chip_w, chip_h = look.chip_size(page, chip_text, chip_px) if chip_text is not None else (0, 0)
        ns, lines = _il_name_plan(page, name, name_px, col_right - tx - (chip_w + chip_gap if chip_text else 0),
                                  min(IL_NAME_FLOOR_PX, name_px))
        page.legible(ns, f"{path}.cells[0]")
        rs = role_px
        while rs > IL_ROLE_FLOOR_PX and page.length(role, rs) > col_right - tx:
            rs -= 1
        page.legible(rs, f"{path}.cells[1]")
        nlh = int(round(ns * 1.12))
        lead2 = rs * sz.lead2
        lead3 = det_px * sz.lead3 if detail and row.get("detail") is not None else 0
        block = (len(lines) - 1) * nlh + cap(ns) + lead2 + lead3
        if block > h - 2 * sz.air:
            raise _NoFit(f"{path}: its text block ({int(block)}px) does not fit the row ({h}px)")
        b1 = cy - block / 2 + cap(ns)
        for i, ln in enumerate(lines):
            page.line(f"{path}.cells[0]", ln, ns, tx, b1 + i * nlh, "ls", look.INK, box)
        last_b = b1 + (len(lines) - 1) * nlh
        if chip_text is not None:
            cx = tx + page.length(lines[-1], ns) + chip_gap
            look.chip(page, f"{path}.chip", chip_text, chip_px, cx, last_b - cap(ns) / 2 - chip_h / 2, bound=box)
        b2 = last_b + lead2
        role_ln = page.line(f"{path}.cells[1]", role, rs, tx, b2, "ls", look.INK2, box)
        if role_ln.ink[2] > col_right + 0.5:
            raise _NoFit(f"{path}.cells[1]: the roles line runs into the total")
        if lead3:
            b3 = b2 + lead3
            ds = det_px
            while ds > sz.detail.floor and page.length(row["detail"], ds) > right - tx:
                ds -= 1
            page.legible(ds, f"{path}.detail")
            det = page.line(f"{path}.detail", row["detail"], ds, tx, b3, "ls", look.DETAIL, box)
            if det.ink[2] > right + 0.5:
                raise _NoFit(f"{path}.detail: the detail line is wider than the row at {ds}px")
            if det.ink[3] > yy + h - sz.air + 0.5:
                raise _NoFit(f"{path}.detail: the detail line is too low in its row")
        mid = (b1 - cap(ns) + b2) / 2
        page.line(f"{path}.cells[2]", total, amt_px, right, mid + cap(amt_px) / 2, "rs",
                  look.tone_colour(look.figure_tone(image.series, total)), box)
        yy += h + gap
    return yy - gap


# ── the earnings table (news-v23, earnings_multi4.jpg / earnings_multi2.jpg / mock_earnings_multi.py) ──────────────

#: The table's sizes at ramp step 0 (mock_earnings_multi.earnings_multi): the column labels over the table (GREY), and —
#: COMPACT, three or four companies — the row label ("EPS" / "Revenue", INK2), a figure (the estimate INK on its light
#: pill, the reported WHITE on its dark pill), the surprise (GREEN / RED on its tint, `look.signed_tone`), a pill's
#: height, the company name (≤ 2 lines, one size for every card, never below ER_NAME_FLOOR_C before the page steps) and
#: the logo disc; WIDE, two companies: the header row's disc, chip and name (one line, 46 → 34), the table across the
#: card with bigger figures (tried from ER_VALUE_W down to ER_VALUE_W_FLOOR before the page steps).
ER_COL_PX = _Style(22, 22, 1.2)
ER_LABEL_C = _Style(24, 22, 1.0)
ER_VALUE_C = _Style(32, 22, 1.0)
ER_SUR_C = _Style(30, 22, 1.0)
ER_PILL_C = _Style(58, 34, 1.0)
ER_NAME_C = _Style(32, 22, 1.12)
ER_NAME_FLOOR_C = 26
ER_DISC_C = _Style(52, 26, 1.0)
ER_CHIP_C = _Style(22, 22, 1.0)
ER_LABEL_W = _Style(30, 22, 1.0)
ER_VALUE_W = _Style(54, 28, 1.0)
ER_VALUE_W_FLOOR = 41
ER_PILL_W = _Style(70, 40, 1.0)
ER_NAME_W = _Style(46, 26, 1.12)
ER_NAME_W_FLOOR = 34
ER_DISC_W = _Style(52, 26, 1.0)
ER_CHIP_W = _Style(24, 22, 1.0)
#: A STACKED compact card's disc (three or four companies whose names the compact column cannot hold, `_er_lay_wide`).
ER_DISC_S = _Style(36, 22, 1.0)
#: The tallest a card grows when the table has room (three compact companies, two wide ones on the 4:5 picture): the
#: rest is shared above the column labels and below the cards.
ER_CARD_CAP_C = 196
ER_CARD_CAP_W = 340


def _is_earnings_table(image: TemplateImage) -> bool:
    """Is `image` a news-v23 earnings TABLE — the earnings series' `rows` picture of ONE section that names its
    `columns`, every row carrying a `table` (the picture, or its cover on the 9:16 frame)?"""
    spec = image.spec
    if image.layout != "rows" or image.series != "earnings":
        return False
    sections = spec.get("sections") or ()
    return (len(sections) == 1 and sections[0].get("columns") is not None
            and all(r.get("table") for r in sections[0]["rows"]))


class _ErTable:
    """The table's geometry, ONE for every card so the columns line up under their labels (mock_earnings_multi.Table):
    the row-label column, then Estimate | Reported (one shared width: the wider column's widest figure, padded) |
    Surprise (its widest cell padded, never narrower than its label), right-aligned to `right`; with `left`, the spare
    width up to `right` is shared by the three columns. _NoFit when the table needs more than `right − left`."""

    def __init__(self, page: look.Page, rows: Sequence[Mapping[str, Any]], columns: Sequence[str], *, value_px: int,
                 label_px: int, sur_px: int, col_px: int, pill_h: int, pad: int, gap: int, row_gap: int,
                 label_gap: int, right: float, left: Optional[float] = None, sur_pad: Optional[int] = None) -> None:
        self.value_px, self.label_px, self.sur_px = value_px, label_px, sur_px
        self.pill_h, self.gap, self.row_gap, self.label_gap = pill_h, gap, row_gap, label_gap
        lines = [ln["cells"] for r in rows for ln in r["table"]]
        self.label_w = max(page.length(c[0], label_px) for c in lines)
        fig_w = max(page.length(c[k], value_px) for c in lines for k in (1, 2)) + 2 * pad
        fig_w = max(fig_w, max(page.length(columns[k], col_px) for k in (0, 1)) + 8)
        sur_pad = pad if sur_pad is None else sur_pad
        sur_w = max(max(page.length(c[3], sur_px) for c in lines) + 2 * sur_pad, page.length(columns[2], col_px) + 8)
        est_w = rep_w = fig_w
        need = self.label_w + label_gap + est_w + gap + rep_w + gap + sur_w
        if left is not None:
            spare = right - left - need
            if spare < 0:
                raise _NoFit(f"the earnings table needs {int(need)}px, {int(right - left)}px across the card")
            est_w, rep_w, sur_w = est_w + spare / 3, rep_w + spare / 3, sur_w + spare / 3
        self.est_w, self.rep_w, self.sur_w = est_w, rep_w, sur_w
        self.sur_x = right - sur_w
        self.rep_x = self.sur_x - gap - rep_w
        self.est_x = self.rep_x - gap - est_w
        self.label_x = self.est_x - label_gap - self.label_w

    def height(self, row: Mapping[str, Any]) -> int:
        k = len(row["table"])
        return k * self.pill_h + (k - 1) * self.row_gap

    def centres(self) -> Tuple[float, float, float]:
        return (self.est_x + self.est_w / 2, self.rep_x + self.rep_w / 2, self.sur_x + self.sur_w / 2)

    def draw(self, page: look.Page, row: Mapping[str, Any], path: str, top: float, card_box: Box) -> None:
        """The row's table lines from `top`: the label (INK2, right-aligned before the estimate), the estimate (INK on
        PILL_LIGHT), the reported figure (WHITE on NAVY_CHIP), the surprise on its tint — GREEN for a "+", RED for a
        "−" (`look.signed_tone`), INK2 on PILL_LIGHT for "0.0%" / "—" — each centred in its pill."""
        rad = min(16, self.pill_h // 3)
        for i, line in enumerate(row["table"]):
            cells, lp = line["cells"], f"{path}.table[{i}]"
            py = top + i * (self.pill_h + self.row_gap)
            mid = py + self.pill_h / 2
            page.line(f"{lp}.cells[0]", cells[0], self.label_px, self.est_x - self.label_gap, mid, "rm", look.INK2,
                      card_box)
            box = page.rect((self.est_x, py, self.est_x + self.est_w, py + self.pill_h), rad, look.PILL_LIGHT)
            page.line(f"{lp}.cells[1]", cells[1], self.value_px, self.est_x + self.est_w / 2, mid, "mm", look.INK, box)
            box = page.rect((self.rep_x, py, self.rep_x + self.rep_w, py + self.pill_h), rad, look.NAVY_CHIP)
            page.line(f"{lp}.cells[2]", cells[2], self.value_px, self.rep_x + self.rep_w / 2, mid, "mm", look.WHITE,
                      box)
            tone = look.signed_tone(cells[3])
            tint = {"gain": look.GREEN_TINT, "loss": look.RED_TINT}.get(tone or "", look.PILL_LIGHT)
            ink = {"gain": look.GREEN, "loss": look.RED}.get(tone or "", look.INK2)
            box = page.rect((self.sur_x, py, self.sur_x + self.sur_w, py + self.pill_h), rad, tint)
            page.line(f"{lp}.cells[3]", cells[3], self.sur_px, self.sur_x + self.sur_w / 2, mid, "mm", ink, box)


def _er_measure_notes(page: look.Page, notes: Sequence[str], x0: int, x1: int) -> int:
    """The notes panel's height at this step (laid out, then forgotten)."""
    if not notes:
        return 0
    mark = page.mark()
    h = _notes_panel(page, notes, x0, x1, 0)
    page.rollback(mark)
    return h


def _er_name_lines(page: look.Page, names: Sequence[str], width: float, start: int, floor: int,
                   path: str) -> Tuple[int, List[Tuple[str, ...]]]:
    """ONE name size for every card — the largest from `start` down to `floor` at which each name takes at most two
    balanced lines of `width` — and each name's lines. _NoFit when none does (the page steps)."""
    size = start
    while True:
        try:
            plans = [page.measure(n, size, width, f"{path}.rows[{j}].cells[0]", leading=1.12, max_lines=2,
                                  balance=True).lines for j, n in enumerate(names)]
            return size, plans
        except _NoFit:
            if size <= floor:
                raise
            size -= 1


def _lay_earnings(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """The earnings TABLE (news-v23, earnings_multi4.jpg / earnings_multi2.jpg; on the 9:16 frame its cover): the
    two-tone title, the subtitle (the report day or window), the grey column labels "Estimate" | "Reported" |
    "Surprise" centred over their columns, one warm card per company, the basis note on its panel. Three or four
    companies: COMPACT cards — the logo disc, the ticker chip over the name (≤ 2 lines) on the left, the table on the
    right. Two: WIDE cards — a header row (disc, chip over the name), the table across the card with bigger figures.
    The cards take the height (up to ER_CARD_CAP_C / ER_CARD_CAP_W), the rest shared above and below them."""
    spec, logos = image.spec, image.logos
    x0, x1, tx0, tx1 = _columns(page)
    sec = spec["sections"][0]
    rows, columns = list(sec["rows"]), list(sec["columns"])
    n = len(rows)
    path = "sections[0]"
    y = _ladder_title(page, spec["title"], tx0, top, tx1 - tx0)
    if spec.get("subtitle") is not None:
        t = page.measure(spec["subtitle"], page.px(SUBTITLE_STYLE), tx1 - tx0, "subtitle",
                         leading=SUBTITLE_STYLE.leading)
        y += _gap(page, 8)
        page.put("subtitle", t, tx0, y, "la", look.SUB, page.frame.area)
        y += t.height
    notes = list(spec.get("notes") or ())
    notes_h = _er_measure_notes(page, notes, x0, x1)
    cards_bot = bottom - (notes_h + _gap(page, 26, 10) if notes else 0)
    col_px = page.px(ER_COL_PX)
    page.legible(col_px, f"{path}.columns[0]")
    label_h = col_px + _gap(page, 14, 6)
    first_top = y + _gap(page, 30, 12) + label_h
    avail = cards_bot - first_top
    if n <= 2:
        _er_lay_wide(page, image, rows, columns, path, x0, x1, first_top, avail, col_px, compact=False)
    else:
        try:
            plan = _er_compact_plan(page, rows, columns, path, x0, x1, avail, col_px)
        except _NoFit as e:
            # a name the left column cannot hold (a long word, or more than two lines): the STACKED compact card —
            # the header row over the table, at the compact sizes (the page steps until it fits)
            logger.debug("earnings table: the compact name column does not fit (%s) — stacked cards", e)
            plan = None
        if plan is None:
            _er_lay_wide(page, image, rows, columns, path, x0, x1, first_top, avail, col_px, compact=True)
        else:
            table, ch, gap, r, ix, ns, plans = plan
            cards_top = first_top + max(0, avail - (n * ch + gap * (n - 1))) // 2
            _er_column_labels(page, table, columns, col_px, cards_top, path)
            for j, (row, lines) in enumerate(zip(rows, plans)):
                box = (x0, cards_top + j * (ch + gap), x1, cards_top + j * (ch + gap) + ch)
                _er_card_compact(page, image, row, f"{path}.rows[{j}]", box, table, r, ix, ns, lines)
    if notes:
        _notes_panel(page, notes, x0, x1, cards_bot + _gap(page, 26, 10))


def _er_column_labels(page: look.Page, table: "_ErTable", columns: Sequence[str], col_px: int, cards_top: float,
                      path: str) -> None:
    """The grey column labels, centred over their columns, just above the first card."""
    for k, (cx, label) in enumerate(zip(table.centres(), columns)):
        page.line(f"{path}.columns[{k}]", label, col_px, cx, cards_top - _gap(page, 14, 6), "ms", look.GREY,
                  page.frame.area)


def _er_compact_plan(page: look.Page, rows: Sequence[Mapping[str, Any]], columns: Sequence[str], path: str, x0: int,
                     x1: int, avail: float, col_px: int):
    """The COMPACT cards' plan (three or four companies, mock_earnings_multi.card_compact) — (table, card height, gap,
    disc radius, the name column's x, the name size, each name's lines) — or _NoFit (the cards, a name or a chip do
    not fit: the caller stacks the cards)."""
    n = len(rows)
    table = _ErTable(page, rows, columns, value_px=page.px(ER_VALUE_C), label_px=page.px(ER_LABEL_C),
                     sur_px=page.px(ER_SUR_C), col_px=col_px, pill_h=page.px(ER_PILL_C), pad=_gap(page, 15, 8),
                     gap=_gap(page, 10, 4), row_gap=_gap(page, 8, 3), label_gap=_gap(page, 18, 8),
                     right=x1 - _gap(page, 22, 12), sur_pad=_gap(page, 16, 8))
    page.legible(table.value_px, f"{path}.rows[0].table[0].cells[1]")
    page.legible(table.label_px, f"{path}.rows[0].table[0].cells[0]")
    page.legible(table.sur_px, f"{path}.rows[0].table[0].cells[3]")
    gap = _gap(page, 14, 6)
    ch = int(min(ER_CARD_CAP_C, (avail - gap * (n - 1)) / n))
    need = max(table.height(row) for row in rows) + 2 * _gap(page, 16, 6)
    if ch < need:
        raise _NoFit(f"earnings table: a card would be {ch}px, its table needs {need}px")
    r = max(ER_DISC_C.floor, min(page.px(ER_DISC_C), (ch - 40) // 2))
    ix = x0 + _gap(page, 22, 12) + 2 * r + _gap(page, 20, 10)
    name_w = table.label_x - _gap(page, 16, 8) - ix
    if name_w < 80:
        raise _NoFit(f"earnings table: {int(name_w)}px left for the company names")
    start = page.px(ER_NAME_C)
    ns, plans = _er_name_lines(page, [row["cells"][0] for row in rows], name_w, start,
                               max(ER_NAME_C.floor, min(start, ER_NAME_FLOOR_C)), path)
    chip_px = page.px(ER_CHIP_C)
    chip_w = max((look.chip_size(page, row["chip"], chip_px)[0] for row in rows if row.get("chip")), default=0)
    if chip_w > name_w:
        raise _NoFit(f"earnings table: a {chip_w}px chip in a {int(name_w)}px name column")
    lh = int(round(ns * 1.12))
    chip_h = look.chip_size(page, "W", chip_px)[1]
    if chip_h + _gap(page, 12, 5) + (max(len(p) for p in plans) - 1) * lh + ns > ch - 2 * _gap(page, 10, 4):
        raise _NoFit("earnings table: a company's chip and name are taller than its card")
    return table, ch, gap, r, ix, ns, plans


def _er_lay_wide(page: look.Page, image: TemplateImage, rows: Sequence[Mapping[str, Any]], columns: Sequence[str],
                 path: str, x0: int, x1: int, first_top: float, avail: float, col_px: int, *, compact: bool) -> None:
    """WIDE cards (two companies, mock_earnings_multi.card_wide): a header row — the logo disc, the chip over the name
    — then the table across the card with bigger figures (tried from ER_VALUE_W down to ER_VALUE_W_FLOOR); or, with
    `compact` (three or four companies whose names the compact column cannot hold), the same STACKED card at the
    compact sizes."""
    n = len(rows)
    pad = _gap(page, 24, 12) if not compact else _gap(page, 16, 8)
    gap = _gap(page, 26, 8) if not compact else _gap(page, 14, 6)
    if compact:
        table = _ErTable(page, rows, columns, value_px=page.px(ER_VALUE_C), label_px=page.px(ER_LABEL_C),
                         sur_px=page.px(ER_SUR_C), col_px=col_px, pill_h=page.px(ER_PILL_C), pad=_gap(page, 15, 8),
                         gap=_gap(page, 10, 4), row_gap=_gap(page, 8, 3), label_gap=_gap(page, 18, 8),
                         left=x0 + pad + 6, right=x1 - pad, sur_pad=_gap(page, 16, 8))
        r = page.px(ER_DISC_S)
        name_style, name_floor, chip_px, cap = ER_NAME_C, ER_NAME_FLOOR_C, page.px(ER_CHIP_C), ER_CARD_CAP_C
    else:
        table = None
        vs = page.px(ER_VALUE_W)
        # mock_earnings_multi: the wide figures are tried from 54 down to 41 (scaled with the step) before the page steps
        floor = min(vs, max(ER_VALUE_W.floor, int(round(vs * ER_VALUE_W_FLOOR / ER_VALUE_W.start))))
        while table is None:
            try:
                table = _ErTable(page, rows, columns, value_px=vs, label_px=page.px(ER_LABEL_W),
                                 sur_px=max(22, vs - 10), col_px=col_px, pill_h=page.px(ER_PILL_W),
                                 pad=_gap(page, 26, 12), gap=_gap(page, 14, 6), row_gap=_gap(page, 10, 4),
                                 label_gap=_gap(page, 20, 8), left=x0 + pad + 6, right=x1 - pad)
            except _NoFit:
                if vs <= floor:
                    raise
                vs -= 1
        r = page.px(ER_DISC_W)
        name_style, name_floor, chip_px, cap = ER_NAME_W, ER_NAME_W_FLOOR, page.px(ER_CHIP_W), ER_CARD_CAP_W
    for px_, what in ((table.value_px, "cells[1]"), (table.sur_px, "cells[3]"), (table.label_px, "cells[0]")):
        page.legible(px_, f"{path}.rows[0].table[0].{what}")
    width = x1 - pad - (x0 + pad + 2 * r + _gap(page, 26, 12))
    names = [_er_wide_name(page, row["cells"][0], width, name_style, name_floor, f"{path}.rows[{j}].cells[0]")
             for j, row in enumerate(rows)]
    for j, row in enumerate(rows):
        if row.get("chip") and look.chip_size(page, row["chip"], chip_px)[0] > width:
            raise _NoFit(f"{path}.rows[{j}].chip: wider than the name column ({int(width)}px)")
    head = max(max(2 * r, _er_name_block_h(page, row, lines, size, chip_px))
               for row, (size, lines) in zip(rows, names))
    need = max(pad + head + _gap(page, 18, 8) + table.height(row) + pad for row in rows)
    ch = int(min(max(cap, need), (avail - gap * (n - 1)) / n))
    if ch < need:
        raise _NoFit(f"earnings table: a card would be {ch}px, it needs {need}px")
    cards_top = first_top + max(0, avail - (n * ch + gap * (n - 1))) // 2
    _er_column_labels(page, table, columns, col_px, cards_top, path)
    for j, (row, name) in enumerate(zip(rows, names)):
        box = (x0, cards_top + j * (ch + gap), x1, cards_top + j * (ch + gap) + ch)
        _er_card_wide(page, image, row, f"{path}.rows[{j}]", box, table, r, pad, name, head, chip_px)


def _er_name_block_h(page: look.Page, row: Mapping[str, Any], lines: Sequence[str], size: int, chip_px: int) -> int:
    """The height of a row's chip-over-name block (`_er_name_block`)."""
    chip_text = row.get("chip")
    chip_h = look.chip_size(page, chip_text, chip_px)[1] if chip_text else 0
    cap = -page.font(size).getbbox("H", anchor="ls")[1]
    desc = page.font(size).getmetrics()[1]
    return int((chip_h + _gap(page, 12, 5) if chip_text else 0) + (len(lines) - 1) * int(round(size * 1.12)) + cap
               + desc // 2)


def _er_name_block(page: look.Page, row: Mapping[str, Any], path: str, x: float, cy: float, lines: Sequence[str],
                   size: int, chip_px: int, card_box: Box) -> None:
    """The ticker chip (NAVY) over the company name (INK, its lines), the block centred on `cy` at `x`."""
    chip_text = row.get("chip")
    chip_h = look.chip_size(page, chip_text, chip_px)[1] if chip_text else 0
    chip_gap = _gap(page, 12, 5)
    cap = -page.font(size).getbbox("H", anchor="ls")[1]
    lh = int(round(size * 1.12))
    block = _er_name_block_h(page, row, lines, size, chip_px)
    y = cy - block / 2
    if chip_text:
        look.chip(page, f"{path}.chip", chip_text, chip_px, x, y, bound=card_box)
        y += chip_h + chip_gap
    for i, ln in enumerate(lines):
        page.line(f"{path}.cells[0]", ln, size, x, y + cap + i * lh, "ls", look.INK, card_box)


def _er_card_compact(page: look.Page, image: TemplateImage, row: Mapping[str, Any], path: str, box: Sequence[float],
                     table: _ErTable, r: int, ix: float, ns: int, lines: Sequence[str]) -> None:
    """A compact card (mock_earnings_multi.card_compact): the logo disc, the chip over the name, the table right."""
    card_box = look.card(page, box)
    x0, y0, _x1, y1 = card_box
    cy = (y0 + y1) / 2
    page.disc(x0 + _gap(page, 22, 12) + r, cy, r, image.logos[row["logo"]])
    _er_name_block(page, row, path, ix, cy, lines, ns, page.px(ER_CHIP_C), card_box)
    table.draw(page, row, path, cy - table.height(row) / 2, card_box)


def _er_wide_name(page: look.Page, name: str, width: float, name_style: _Style, name_floor: int,
                  path: str) -> Tuple[int, Sequence[str]]:
    """A wide (or stacked) card's name: one line from `name_style` down to `name_floor`, else two balanced lines."""
    start = page.px(name_style)
    size = start
    floor = max(name_style.floor, min(start, name_floor))
    while size > floor and page.length(name, size) > width:
        size -= 1
    if page.length(name, size) <= width:
        return size, (name,)
    size, plans = _er_name_lines(page, [name], width, start, name_style.floor, path)
    return size, plans[0]


def _er_card_wide(page: look.Page, image: TemplateImage, row: Mapping[str, Any], path: str, box: Sequence[float],
                  table: _ErTable, r: int, pad: int, name: Tuple[int, Sequence[str]], head_h: int, chip_px: int) -> None:
    """A wide card (mock_earnings_multi.card_wide): the header row — the logo disc, the chip over the name (its planned
    size and lines; the row as tall as the taller of the two, `head_h`) — then the table across the card, centred in
    the room under it."""
    card_box = look.card(page, box)
    x0, y0, x1, y1 = card_box
    lcx, lcy = x0 + pad + r, y0 + pad + head_h / 2
    page.disc(lcx, lcy, r, image.logos[row["logo"]])
    ix = lcx + r + _gap(page, 26, 12)
    size, lines = name
    _er_name_block(page, row, path, ix, lcy, lines, size, chip_px, card_box)
    room_top, room_bot = y0 + pad + head_h + _gap(page, 18, 8), y1 - pad
    th = table.height(row)
    if room_bot - room_top < th:
        raise _NoFit(f"{path}: the table needs {th}px, {int(room_bot - room_top)}px under the header")
    table.draw(page, row, path, room_top + (room_bot - room_top - th) / 2, card_box)


# ── the Congress Count list (news-v23, congress_multi3.jpg + theme_congress_defense.jpg / mock_congress_multi.py) ───

#: The Congress card's sizes at ramp step 0 (mock_congress_multi.company_card): the logo disc, the name (one line,
#: 50 → 32), the line under the hairline (≤ 2 lines, 30 → 24), the chip; the count fills its tile (GREEN on the GREEN
#: tint: owner 2026-11-19 "numbers like 6,4,3,2,2, use green color, same green as the purchases"), and "purchases" in
#: the line sits on a solid GREEN pill, WHITE ("use green color for 'purchases'") — a drawing rule, the string is the
#: server's whole line.
CG_DISC = _Style(76, 34, 1.0)
CG_NAME = _Style(50, 22, 1.0)
CG_NAME_FLOOR = 32
CG_LINE = _Style(30, 22, 1.3)
CG_LINE_FLOOR = 24
CG_CHIP = _Style(22, 22, 1.0)
CG_PAD = 26
#: The tallest a card grows on the 9:16 COVER (its list rests under the title, as every list cover's). The PICTURE's
#: cards take the whole room between the sub line and the note (mock_congress_multi: `h = (bottom − top − gaps) / n`,
#: the count tile `h − 2 × pad` square) — two companies included: a 300 px cap left ~63 px of empty navy above and
#: below them (visual-fidelity round 2026-11-20).
CG_CARD_CAP = 300
#: The word on the GREEN pill inside each card's line.
#: The least air above and below a Congress card's text block (chip to pill) — the mock's own check
#: (mock_congress_multi.company_card asserts 14 px both ways), review 2026-11-20; the hairline gap gives way to
#: CG_G2_FLOOR, then the chip gap to CG_G1_FLOOR, before the page steps.
CG_TEXT_AIR = 14
CG_G2_FLOOR = 14
CG_G1_FLOOR = 6
CG_PILL_WORD = "purchases"
CG_PILL_PAD_X = 10
#: The narrowest the count tile gets to keep every card's line on TWO lines (final review 2026-11-20): a two-company
#: card's CG_CARD_CAP square tile (248 px) left ~432 px of text on the picture, ~384 on the cover, and "purchases of
#: General Dynamics stock" (445 px at 24) fell to three lines — "… of General" / "Dynamics stock", "… of Accenture" /
#: "stock". The tile narrows CG_TILE_NARROW_STEP px at a time down to this — about a three-company card's own square
#: (~183 px on the picture, ~162 on the cover), which still holds a three-digit count — before three lines are tried.
CG_TILE_NARROW_FLOOR = 160
CG_TILE_NARROW_STEP = 4
_CG_COUNT_RE = re.compile(r"[0-9]{1,3}")


def _is_congress_list(image: TemplateImage) -> bool:
    """Is `image` a news-v23 Congress Count LIST — the congress_count series' `rows` picture of ONE section whose rows
    each carry a logo and three cells (name, line, count: digits)?"""
    spec = image.spec
    if image.layout != "rows" or image.series != "congress_count":
        return False
    sections = spec.get("sections") or ()
    if len(sections) != 1 or sections[0].get("columns") is not None:
        return False
    return all(r.get("logo") is not None and len(r["cells"]) == 3 and _CG_COUNT_RE.fullmatch(r["cells"][2])
               for r in sections[0]["rows"])


def _cg_line(page: look.Page, field: str, lines: Sequence[str], size: int, x: float, y: float, lh: int,
             card_box: Box) -> None:
    """The card's line (INK2), its lines from baseline `y`; the word CG_PILL_WORD on a solid GREEN pill in WHITE (the
    theme mock's pill: its left edge where the word would start, the word inset CG_PILL_PAD_X, the line going on after
    the pill and a space). The words are drawn in order, the pill's word as its own run (joined with single spaces,
    the string comes back)."""
    space = page.length(" ", size)
    asc, desc = page.font(size).getmetrics()
    for i, ln in enumerate(lines):
        base = y + i * lh
        words = ln.split(" ")
        if CG_PILL_WORD not in words:
            page.line(field, ln, size, x, base, "ls", look.INK2, card_box)
            continue
        k = words.index(CG_PILL_WORD)
        cx = x
        before, after = " ".join(words[:k]), " ".join(words[k + 1:])
        if before:
            page.line(field, before, size, cx, base, "ls", look.INK2, card_box)
            cx += page.length(before, size) + space
        pw = page.length(CG_PILL_WORD, size)
        pill = page.rect((cx, base - asc + 1, cx + pw + 2 * CG_PILL_PAD_X, base + desc + 1), 9, look.GREEN)
        if not _within(pill, card_box):
            raise _NoFit(f"{field}: the pill leaves its card")
        page.line(field, CG_PILL_WORD, size, cx + CG_PILL_PAD_X, base, "ls", look.WHITE, pill)
        cx = pill[2] + space
        if after:
            page.line(field, after, size, cx, base, "ls", look.INK2, card_box)


def _cg_split_plans(page: look.Page, rows: Sequence[Mapping[str, Any]], size: int, width: float, max_lines: int,
                    path: str, *, probe: bool = False) -> Optional[List[Tuple[str, ...]]]:
    """Every card's line broken at the SAME place (fidelity review 2026-11-20 — congress_multi3.jpg wraps every card
    alike: "members disclosed" / "purchases of <Co> stock"): line 1 the words before CG_PILL_WORD, the rest from the
    pill's word on, wrapped in `width` to at most `max_lines` − 1 more lines — so the green pill always opens line 2.
    None when any card's line has no pill word to break at or does not fit that way at `size`. A `probe` (the tile's
    narrowing, `_lay_congress`) only asks whether the lines fit: it never checks the size's legibility."""
    font = page.font(size)
    out: List[Tuple[str, ...]] = []
    for j, row in enumerate(rows):
        words = row["cells"][1].split()
        if CG_PILL_WORD not in words or words.index(CG_PILL_WORD) == 0:
            return None
        k = words.index(CG_PILL_WORD)
        head = " ".join(words[:k])
        if font.getlength(head) > width:
            return None
        what, rest = f"{path}.rows[{j}].cells[1]", " ".join(words[k + 1:])
        try:
            if max_lines > 2 and rest and font.getlength(" ".join(words[k:])) > width >= font.getlength(rest):
                # final review 2026-11-20: when the words after the pill's cannot join it on one line, the pill's word
                # stands alone and "of <Co> stock" stays WHOLE — never "purchases of General" / "Dynamics stock", never
                # "purchases of Accenture" / "stock". Only a name too long for that line wraps greedily (its last line
                # then holds two words or more: a lone "stock" would mean the rest fitted one line)
                tail = [words[k], rest]
            else:
                tail = look.wrap(words[k:], font, width, max_lines - 1, what)
        except _NoFit:
            return None
        out.append((head, *tail))
    if not probe:
        page.legible(size, f"{path}.rows[0].cells[1]")
    return out


def _ladder_title(page: look.Page, text: str, x: float, top: float, width: float, *, lines: int = 2,
                  floor_px: int = 56) -> int:
    """The two-tone title on at most `lines` lines at the largest size from this step's down to `floor_px` (the mock's
    `headline(..., size=80, max_lines=2, min_size=56)`: "MEMBERS OF CONGRESS" is ~1000 px at 80 and the mock shrinks
    the title, never the cards); else the step's own headline (any lines the height allows). Returns its bottom."""
    start = page.px(HEADLINE_STYLE)
    for size in range(start, min(start, floor_px) - 1, -2):
        mark = page.mark()
        try:
            return look.headline(page, "title", text, HEADLINE_STYLE, x, top, width, max_lines=lines, size=size)
        except _NoFit:
            page.rollback(mark)
    return look.headline(page, "title", text, HEADLINE_STYLE, x, top, width)


def _lay_congress(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """The Congress Count LIST (news-v23, congress_multi3.jpg with theme_congress_defense.jpg's colours; on the 9:16
    frame its cover): the two-tone title ("MEMBERS OF CONGRESS" white, "DISCLOSED PURCHASES" gold), the subtitle (the
    month · as of), one warm card per company — the count GREEN filling its GREEN-tint tile on the left, the logo disc,
    the ticker chip over the name, a hairline, the line "members disclosed purchases of {co} stock" (≤ 2 lines,
    "purchases" on its GREEN pill) — and the note panel. On the picture the cards take the height (the mock's rule);
    on the cover up to CG_CARD_CAP, the rest shared above and below them; one name size and one line size for every
    card."""
    spec, logos = image.spec, image.logos
    x0, x1, tx0, tx1 = _columns(page)
    sec = spec["sections"][0]
    rows = list(sec["rows"])
    n = len(rows)
    path = "sections[0]"
    y = _ladder_title(page, spec["title"], tx0, top, tx1 - tx0)
    if spec.get("subtitle") is not None:
        t = page.measure(spec["subtitle"], page.px(SUBTITLE_STYLE), tx1 - tx0, "subtitle",
                         leading=SUBTITLE_STYLE.leading)
        y += _gap(page, 10)
        page.put("subtitle", t, tx0, y, "la", look.SUB, page.frame.area)
        y += t.height
    notes = list(spec.get("notes") or ())
    notes_h = _er_measure_notes(page, notes, x0, x1)
    cards_bot = bottom - (notes_h + _gap(page, 26, 10) if notes else 0)
    first_top = y + _gap(page, 34, 12)
    gap = _gap(page, 22, 8)
    avail = cards_bot - first_top
    fill = (avail - gap * (n - 1)) / n
    ch = int(fill if page.frame is POST_IMAGE_FRAME else min(CG_CARD_CAP, fill))
    pad = _gap(page, CG_PAD, 12)
    tile_h = ch - 2 * pad
    # the count tile spans the card's height (the mock's `h − 2 × pad`) but is never wider than a CG_CARD_CAP card's
    # square: two companies' taller cards keep the name and its line their column (visual-fidelity round 2026-11-20)
    tile = min(tile_h, CG_CARD_CAP - 2 * pad)
    if tile < 90:
        raise _NoFit(f"Congress list: a card would be {ch}px")
    line_start = page.px(CG_LINE)
    lfloor = max(CG_LINE.floor, min(line_start, CG_LINE_FLOOR))

    def text_x(t: int) -> Tuple[int, float]:
        """The disc's radius and the text column's left edge for a count tile `t` wide."""
        rad = min(page.px(CG_DISC), max(CG_DISC.floor, int(t * 0.42)))
        return rad, x0 + pad + t + _gap(page, 30, 14) + 2 * rad + _gap(page, 28, 14)

    right = x1 - _gap(page, 32, 16)
    # final review (2026-11-20): before any card's line takes three lines, the tile narrows — CG_TILE_NARROW_STEP at a
    # time, never under CG_TILE_NARROW_FLOOR (a three-company card's own square) — until every card's line keeps TWO,
    # "members disclosed" / "purchases of <Co> stock", at the mock's floor (a two-company card's 248 px tile broke
    # "General Dynamics", "Northrop Grumman" and left "stock" alone on the cover)
    while tile > CG_TILE_NARROW_FLOOR and _cg_split_plans(
            page, rows, lfloor, right - text_x(tile)[1] - 2 * CG_PILL_PAD_X, 2, path, probe=True) is None:
        tile = max(CG_TILE_NARROW_FLOOR, tile - CG_TILE_NARROW_STEP)
    r, tx = text_x(tile)
    used = n * ch + gap * (n - 1)
    cards_top = first_top + max(0, avail - used) // 2
    # one count size: the largest that fills the tile (mock: ink ≤ 66 % of its height, ≤ 62 % of its width)
    counts = [row["cells"][2] for row in rows]
    fs = 24
    while True:
        nxt = fs + 2
        boxes = [page.font(nxt).getbbox(c, anchor="ls") for c in counts]
        if any(b[3] - b[1] > tile_h * 0.66 or b[2] - b[0] > tile * 0.62 for b in boxes):
            break
        fs = nxt
    page.legible(fs, f"{path}.rows[0].cells[2]")
    avail_w = right - tx
    # one name size (≤ 2 balanced lines) and one line size (≤ 3 lines, the pill's padding counted) for every card: the
    # mock's one line of 50 → 32 and two lines of 30 → 24 first, longer names (a 32-character name of 20-character
    # words, the server's bound) wrap before the page steps
    name_start = page.px(CG_NAME)
    floor = max(CG_NAME.floor, min(name_start, CG_NAME_FLOOR))
    ns = name_start
    while ns > floor and any(page.length(row["cells"][0], ns) > avail_w for row in rows):
        ns -= 1
    if any(page.length(row["cells"][0], ns) > avail_w for row in rows):
        ns, names = _er_name_lines(page, [row["cells"][0] for row in rows], avail_w, name_start, CG_NAME.floor, path)
    else:
        names = [(row["cells"][0],) for row in rows]
    line_w = avail_w - 2 * CG_PILL_PAD_X
    plans: Optional[List[Tuple[str, ...]]] = None
    # first: ONE break for every card, the pill opening line 2 (two lines down to the mock's floor, then three); only
    # when no card's line can break there, each card's own balanced lines (the line's shape never refuses a picture)
    for max_lines, low in ((2, lfloor), (3, CG_LINE.floor)):
        ls = line_start
        while plans is None:
            plans = _cg_split_plans(page, rows, ls, line_w, max_lines, path)
            if plans is None:
                if ls <= low:
                    break
                ls -= 1
        if plans is not None:
            break
    for max_lines, low in ((2, lfloor), (3, CG_LINE.floor)):
        if plans is not None:
            break
        ls = line_start
        while plans is None:
            try:
                plans = [page.measure(row["cells"][1], ls, line_w, f"{path}.rows[{j}].cells[1]",
                                      leading=CG_LINE.leading, max_lines=max_lines, balance=True).lines
                         for j, row in enumerate(rows)]
            except _NoFit:
                if ls <= low:
                    break
                ls -= 1
    if plans is None:
        raise _NoFit(f"Congress list: a card's line does not fit {int(line_w)}px in three lines")
    chip_px = page.px(CG_CHIP)
    ncap = -page.font(ns).getbbox("H", anchor="ls")[1]
    nlh = int(round(ns * 1.1))
    lcap = -page.font(ls).getbbox("H", anchor="ls")[1]
    ldesc = page.font(ls).getmetrics()[1]
    llh = int(round(ls * CG_LINE.leading))
    g1, g2 = _gap(page, 14, 6), _gap(page, 34, 14)
    # the text block of each card: chip + g1, the name, g2 (the hairline in its middle), the line — its pill reaching
    # one pixel under the last line's descent (`_cg_line`) — with at least CG_TEXT_AIR above and below it (the mock's own
    # check, mock_congress_multi.company_card: 14). Review 2026-11-20: a stepped 9:16 cover kept 9-10 px; the hairline gap
    # (g2, to its floor) and then the chip gap (g1) give way FIRST — one size for every card — before the page steps.
    chips_h = [look.chip_size(page, row.get("chip"), chip_px)[1] if row.get("chip") else 0 for row in rows]
    names_h = [(len(nl) - 1) * nlh + ncap for nl in names]

    def block_of(j: int, a: int, b: int) -> int:
        return ((chips_h[j] + a if rows[j].get("chip") else 0) + names_h[j] + b + (len(plans[j]) - 1) * llh + lcap
                + ldesc + 1)

    room = ch - 2 * CG_TEXT_AIR
    while g2 > CG_G2_FLOOR and max(block_of(j, g1, g2) for j in range(n)) > room:
        g2 -= 1
    while g1 > CG_G1_FLOOR and max(block_of(j, g1, g2) for j in range(n)) > room:
        g1 -= 1
    for j, (row, lines, name_lines) in enumerate(zip(rows, plans, names)):
        rp = f"{path}.rows[{j}]"
        cy0 = cards_top + j * (ch + gap)
        card_box = look.card(page, (x0, cy0, x1, cy0 + ch))
        cy = cy0 + ch / 2
        tile_box = page.rect((x0 + pad, cy0 + pad, x0 + pad + tile, cy0 + pad + tile_h), 22, look.GREEN_TINT)
        page.line(f"{rp}.cells[2]", row["cells"][2], fs, (tile_box[0] + tile_box[2]) / 2,
                  (tile_box[1] + tile_box[3]) / 2, "mm", look.GREEN, tile_box)
        lcx = x0 + pad + tile + _gap(page, 30, 14) + r
        page.disc(lcx, cy, r, logos[row["logo"]])
        chip_text = row.get("chip")
        chip_h = chips_h[j]
        name_h = names_h[j]
        block = block_of(j, g1, g2)
        by = cy - block / 2
        if by < cy0 + CG_TEXT_AIR or by + block > cy0 + ch - CG_TEXT_AIR:
            raise _NoFit(f"{rp}: its text block ({int(block)}px) does not keep {CG_TEXT_AIR}px of air in the card "
                         f"({ch}px)")
        if chip_text:
            look.chip(page, f"{rp}.chip", chip_text, chip_px, tx, by, bound=card_box)
            by += chip_h + g1
        for k, ln in enumerate(name_lines):
            page.line(f"{rp}.cells[0]", ln, ns, tx, by + ncap + k * nlh, "ls", look.INK, card_box)
        rule_y = by + name_h + g2 / 2
        page.rect((tx, rule_y - 1, right, rule_y + 2), 0, look.DIVIDER)
        _cg_line(page, f"{rp}.cells[1]", lines, ls, tx, by + name_h + g2 + lcap, llh, card_box)
    if notes:
        _notes_panel(page, notes, x0, x1, cards_bot + _gap(page, 26, 10))


# ── company stakes (news-v23, stakes_nvidia.jpg / stakes_nvidia3.jpg / mock_stakes_public.py) ──────────────────────

#: A stake card's sizes at ramp step 0 (mock_stakes_public.metrics): a TALL card (≥ ST_TALL_CARD px: three or fewer
#: stakes) draws the bigger set, a shorter one (four) the smaller — the card's foot inset, the as-of tile's height and
#: its text (shrinking to the tile, then two lines), the name, the basis word, the amount (GOLD_INK), the ticker chip
#: and the gaps under the chip and over the amount. A card shorter than ST_ROW_SPEC (the mock's four-card height:
#: four stakes and a "+N more" line, the video cover) scales them down with it (the 13F list's `sizes_for` rule: the
#: name never below 85 %, the amount never below 70 %, every text ≥ its floor) and, under ST_INLINE_CHIP_BELOW, puts
#: the chip at the end of the name's line. The logo disc is centred on the WHOLE card, its radius
#: min(ST_DISC, (card − 40) / 2). One size of each for every card; every size steps down the ramp with the page.
ST_TALL_CARD = 230
ST_ROW_SPEC = 186
ST_INLINE_CHIP_BELOW = 160
#: The least air between a stake card's amount (its baseline) and the as-of tile under it — the mock's own check
#: (mock_stakes_public.stake_card asserts `base <= t0 - 8`), at every step (review 2026-11-20).
ST_FIGURE_TILE_AIR = 8
ST_INSET = (_Style(14, 6, 1.0), _Style(12, 6, 1.0))
ST_TILE_H = (_Style(56, 32, 1.0), _Style(48, 32, 1.0))
ST_META = (_Style(26, 22, 1.25), _Style(24, 22, 1.25))
ST_NAME = (_Style(46, 22, 1.12), _Style(42, 22, 1.12))
ST_LABEL = (_Style(26, 22, 1.0), _Style(24, 22, 1.0))
ST_FIGURE = (_Style(84, 30, 1.0), _Style(68, 30, 1.0))
ST_CHIP = (_Style(24, 22, 1.0), _Style(22, 22, 1.0))
ST_GAP_CHIP = (16, 12)
ST_GAP_LABEL = (16, 12)
ST_DISC = _Style(92, 24, 1.0)
#: The tallest a card grows when the list is short (one or two stakes: the cards keep about the three-card size, the
#: slack shared above and below them), the card gap (four cards / fewer), the name's one-line floor before it wraps,
#: and the title's own ladder beside the investor's plate (mock: 80 px; the 9:16 cover's narrower column steps it down
#: before the title would leave the plate's side).
ST_CARD_CAP = 270
ST_GAP = (20, 24)
ST_NAME_ONE_LINE_SHARE = 0.75
ST_TITLE_FLOOR_PX = 60


def _is_stakes_list(image: TemplateImage) -> bool:
    """Is `image` a news-v23 company stakes LIST — the company_stakes series' `rows` picture (or cover) of ONE section
    whose rows each carry a logo, three cells (name, as-of line, amount) and a basis `label`?"""
    spec = image.spec
    if image.layout != "rows" or image.series != "company_stakes":
        return False
    sections = spec.get("sections") or ()
    if len(sections) != 1 or sections[0].get("columns") is not None or sections[0].get("heading") is not None:
        return False
    return all(r.get("logo") is not None and len(r["cells"]) == 3 and isinstance(r.get("label"), str)
               for r in sections[0]["rows"])


def _st_cap(page: look.Page, size: int) -> float:
    return -page.font(size).getbbox("H", anchor="ls")[1]


def _st_title(page: look.Page, image: TemplateImage, top: int) -> int:
    """The investor's plate (`_f13_title`'s: its logo in a gold ring, its ticker on a gold pill) and the two-tone title
    beside it — at the step's size, else down a ladder to ST_TITLE_FLOOR_PX (at most three lines: the 9:16 cover's
    column is narrower than the picture's) — else `_f13_title`'s own fallback (full width under the plate). Returns the
    bottom of the title's ink or the plate's pill, whichever is lower."""
    spec, logos = image.spec, image.logos
    _x0, _x1, tx0, tx1 = _columns(page)
    header = spec.get("header")
    start = page.px(HEADLINE_STYLE)
    if header is None:
        return look.headline(page, "title", spec["title"], HEADLINE_STYLE, tx0, top, tx1 - tx0, max_lines=3)
    mark = page.mark()
    r = page.px(PLATE_DISC)
    ring = r + look.GOLD_RING_GAP
    cx, cy = tx1 - ring, top + 6 + ring
    page.disc(cx, cy, r, logos[header["logo"]], ring=True)
    cs = page.px(CHIP_PX)
    cw, _ch = look.chip_size(page, header["chip"], cs)
    pill = look.chip(page, "header.chip", header["chip"], cs, min(cx - cw / 2, tx1 - cw), cy + r - 4,
                     fill=look.GOLD, ink=look.INK)
    col_w = int(min(cx - ring, pill[0]) - tx0 - 28)
    for size in range(start, min(start, ST_TITLE_FLOOR_PX) - 1, -2):
        title_mark = page.mark()
        try:
            y = look.headline(page, "title", spec["title"], HEADLINE_STYLE, tx0, top, col_w, max_lines=3, size=size)
        except _NoFit:
            page.rollback(title_mark)
            continue
        return max(y, pill[3])
    page.rollback(mark)
    return _f13_title(page, image, top) - _gap(page, 10)


def _st_name_plan(page: look.Page, names: Sequence[str], start: int, floor: int, width: Sequence[float],
                  room_h: float, above: float, path: str) -> Tuple[int, List[Tuple[str, ...]]]:
    """ONE name size for every card, and each name's lines (`width[j]`: row j's room): one line from `start` down to
    ST_NAME_ONE_LINE_SHARE of it; else two balanced lines (a name of two or more words) at the largest size whose block
    — `above` (the chip and its gap) plus the lines — fits `room_h`; else _NoFit (the page steps)."""
    floor = max(page.min_px, floor)
    one_floor = max(floor, int(start * ST_NAME_ONE_LINE_SHARE))
    for size in range(start, one_floor - 1, -1):
        if all(page.length(n, size) <= w for n, w in zip(names, width)):
            page.legible(size, f"{path}.rows[0].cells[0]")
            return size, [(n,) for n in names]
    for size in range(start, floor - 1, -1):
        plans: List[Tuple[str, ...]] = []
        for n, w in zip(names, width):
            if page.length(n, size) <= w:
                plans.append((n,))
                continue
            words = n.split()
            if len(words) < 2:
                break
            best = min(range(1, len(words)), key=lambda i: max(page.length(" ".join(words[:i]), size),
                                                              page.length(" ".join(words[i:]), size)))
            lines = (" ".join(words[:best]), " ".join(words[best:]))
            if max(page.length(ln, size) for ln in lines) > w:
                break
            plans.append(lines)
        else:
            tallest = max(len(p) for p in plans)
            if above + (tallest - 1) * int(round(size * ST_NAME[0].leading)) + _st_cap(page, size) <= room_h:
                page.legible(size, f"{path}.rows[0].cells[0]")
                return size, plans
    raise _NoFit(f"{path}: a stake's name does not fit its card")


def _lay_stakes(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """The company stakes LIST (news-v23 — stakes_nvidia.jpg / stakes_nvidia3.jpg; on the 9:16 frame its cover): the
    title (the investor's possessive white, "IN PUBLIC COMPANIES" gold) beside the INVESTOR's plate (its logo in a gold
    ring, its ticker on a gold pill — `_st_title`), one warm card per stake — the logo disc centred on the card, the
    ticker chip (NAVY) over the name (INK), the basis word small (INK2) over the amount (GOLD_INK) right-aligned, the
    line "As of {date} · {source}" (INK2) on a tile across the card's foot — and the grey "+N more stakes in the
    caption" line. The cards take the height (up to ST_CARD_CAP, the rest shared above and below them); one size of
    each text for every card."""
    spec, logos = image.spec, image.logos
    x0, x1, _tx0, _tx1 = _columns(page)
    sec = spec["sections"][0]
    rows = list(sec["rows"])
    n = len(rows)
    path = "sections[0]"
    y = _st_title(page, image, top)
    video = page.frame is not POST_IMAGE_FRAME
    more = sec.get("more")
    more_px = page.px(F13_VIDEO_LABEL_PX if video else F13_LABEL_PX)
    more_t = (page.measure(more, more_px, x1 - x0, f"{path}.more", leading=1.2) if more is not None else None)
    more_h = (_gap(page, 20, 8) + more_t.height) if more_t is not None else 0
    first_top = y + _gap(page, 22)
    gap = _gap(page, ST_GAP[0] if n >= 4 else ST_GAP[1], 8)
    avail = bottom - first_top - more_h
    ch = int(min(ST_CARD_CAP, (avail - gap * (n - 1)) / n))
    k = 0 if ch >= ST_TALL_CARD else 1
    sc = min(1.0, ch / ST_ROW_SPEC)

    def scaled(style: _Style, low: float) -> int:
        return max(style.floor, int(round(page.px(style) * max(low, sc))))

    inset, tile_h0 = scaled(ST_INSET[k], 0.0), scaled(ST_TILE_H[k], 0.0)
    meta_px, name_px = scaled(ST_META[k], 0.0), scaled(ST_NAME[k], 0.85)
    label_px, fig_px, chip_px = scaled(ST_LABEL[k], 0.0), scaled(ST_FIGURE[k], 0.7), page.px(ST_CHIP[k])
    g_chip, g_label = _gap(page, ST_GAP_CHIP[k], 4), _gap(page, ST_GAP_LABEL[k], 4)
    if sc < 1.0:
        g_chip, g_label = max(4, int(g_chip * sc)), max(4, int(g_label * sc))
    r = min(page.px(ST_DISC), int((ch - 40) / 2))
    if r < ST_DISC.floor:
        raise _NoFit(f"stakes list: a card would be {ch}px")
    pad_l = _gap(page, 22, 12)
    tx = x0 + pad_l + 2 * r + _gap(page, 28, 14)
    right = x1 - _gap(page, 32, 18)
    tile_x0, tile_x1 = tx - 16, x1 - inset
    meta_w = (tile_x1 - tile_x0) - 36
    # the as-of lines: one size for every card — the step's, shrinking to the floor on one line; else two lines there
    metas = [row["cells"][1] for row in rows]
    ms = meta_px
    while ms > max(ST_META[k].floor, page.min_px) and any(page.length(m, ms) > meta_w for m in metas):
        ms -= 1
    page.legible(ms, f"{path}.rows[0].cells[1]")
    meta_texts = [page.measure(m, ms, meta_w, f"{path}.rows[{j}].cells[1]", leading=ST_META[k].leading, max_lines=2)
                  for j, m in enumerate(metas)]
    tile_h = max(tile_h0, max(t.height for t in meta_texts) + 2 * _gap(page, 8, 4))
    region = ch - inset - tile_h
    # the mock's air (mock_stakes_public.stake_card: `base <= t0 - 8`): the amount's baseline ends at least
    # ST_FIGURE_TILE_AIR above the as-of tile at every step — review 2026-11-20: a stepped 9:16 cover kept 4 px
    room = region - 2 * max(ST_FIGURE_TILE_AIR, _gap(page, 8, 3))
    # the amount: one size for every card, never wider than half the text column, its block inside the upper region
    figs = [row["cells"][2] for row in rows]
    lcap = _st_cap(page, label_px)
    while fig_px > ST_FIGURE[k].floor and (max(page.length(f, fig_px) for f in figs) > (right - tx) * 0.5
                                           or lcap + g_label + _st_cap(page, fig_px) > room):
        fig_px -= 1
    page.legible(fig_px, f"{path}.rows[0].cells[2]")
    page.legible(label_px, f"{path}.rows[0].label")
    fcap = _st_cap(page, fig_px)
    if lcap + g_label + fcap > room:
        raise _NoFit(f"stakes list: the amount ({int(lcap + g_label + fcap)}px) does not fit the card's {region}px")
    inline = ch < ST_INLINE_CHIP_BELOW
    chips = [row.get("chip") for row in rows]
    chip_h = max((look.chip_size(page, c, chip_px)[1] for c in chips if c), default=0)
    chip_w = [look.chip_size(page, c, chip_px)[0] + 14 if (inline and c) else 0 for c in chips]
    above = (chip_h + g_chip) if (chip_h and not inline) else 0
    name_room = right - max(page.length(f, fig_px) for f in figs) - 30 - tx
    ns, names = _st_name_plan(page, [row["cells"][0] for row in rows], name_px, ST_NAME[k].floor,
                              [name_room - cw for cw in chip_w], room, above, path)
    nlh = int(round(ns * ST_NAME[0].leading))
    ncap = _st_cap(page, ns)
    used = n * ch + gap * (n - 1)
    cards_top = first_top + max(0, avail - used) // 2
    for j, (row, lines, meta) in enumerate(zip(rows, names, meta_texts)):
        rp = f"{path}.rows[{j}]"
        cy0 = cards_top + j * (ch + gap)
        box = look.card(page, (x0, cy0, x1, cy0 + ch))
        page.disc(x0 + pad_l + r, cy0 + ch / 2, r, logos[row["logo"]])
        # the as-of tile at the card's foot
        t0 = cy0 + region
        tile = page.rect((tile_x0, t0, tile_x1, t0 + tile_h), 14, look.TILE)
        page.put(f"{rp}.cells[1]", meta, tile_x0 + 16, t0 + (tile_h - meta.height) / 2, "la", look.INK2, tile)
        # the upper region: the chip and the name on the left, the basis word over the amount on the right — the
        # name's last baseline is the amount's
        # (the left block: the chip and its gap, then the name's lines; the right: the basis word, its gap, the
        # amount — both end on ONE baseline, the pair centred in the upper region as the mock's `mid` does)
        mid = cy0 + region / 2
        chip_text = row.get("chip")
        lead = above if chip_text else 0
        left_h = lead + (len(lines) - 1) * nlh + ncap
        right_h = lcap + g_label + fcap
        block = max(left_h, right_h)
        base = mid + block / 2
        first = base - (len(lines) - 1) * nlh
        if chip_text and not inline:
            look.chip(page, f"{rp}.chip", chip_text, chip_px, tx, first - ncap - g_chip - chip_h, bound=box)
        for i, ln in enumerate(lines):
            page.line(f"{rp}.cells[0]", ln, ns, tx, first + i * nlh, "ls", look.INK, box)
        if chip_text and inline:
            cx0 = tx + page.length(lines[-1], ns) + 14
            look.chip(page, f"{rp}.chip", chip_text, chip_px, cx0, base - ncap / 2 - chip_h / 2, bound=box)
        page.line(f"{rp}.cells[2]", row["cells"][2], fig_px, right, base, "rs", _tone(image, row["cells"][2]), box)
        page.line(f"{rp}.label", row["label"], label_px, right, base - fcap - g_label, "rs", look.INK2, box)
        if base - block < cy0 + 3 or base > t0 - ST_FIGURE_TILE_AIR:
            raise _NoFit(f"{rp}: its text does not fit the card ({ch}px)")
    yy = cards_top + used
    if more_t is not None:
        page.put(f"{path}.more", more_t, (x0 + x1) / 2, yy + _gap(page, 20, 8), "ma", look.GREY, page.frame.area)
        yy += _gap(page, 20, 8) + more_t.height
    if yy > bottom + 0.5:
        raise _NoFit(f"stakes list: the cards end at {int(yy)}px, room to {bottom}px")


def _tile_runs(page: look.Page, text: str, field: str, box: Box) -> None:
    """One stat tile's text, verbatim, on one baseline: a leading figure (a first word with a digit) large in INK,
    the words after it in INK2 ("2" + "purchases"); a part with a figure later sets its first word in INK2 and the
    rest in INK ("Filed" + "Nov 10–11, 2026"); else all of it INK2. Shrinks together to fit the tile."""
    x0, y0, x1, y1 = box
    head, _, tail = text.partition(" ")
    big_px, small_px = page.px(TILE_BIG), page.px(TILE_SMALL)
    if re.search(r"\d", head):
        runs = [(head, "big", look.INK), (tail, "small", look.INK2)]
    elif tail and re.search(r"\d", tail):
        runs = [(head, "small", look.INK2), (tail, "mid", look.INK)]
        big_px = max(small_px, int(round(big_px * 0.73)))
    else:
        runs = [(text, "small", look.INK2)]
        tail = ""
    runs = [rn for rn in runs if rn[0]]
    inner = x1 - x0 - 56
    gap = 14

    def width(bp: int, sp: int) -> float:
        return sum(page.length(t, bp if k != "small" else sp) for t, k, _c in runs) + gap * (len(runs) - 1)

    floor = max(look.FOOTER_FLOOR_PX, page.min_px)
    while width(big_px, small_px) > inner and (big_px > floor or small_px > floor):
        big_px = max(floor, big_px - 2)
        small_px = max(floor, small_px - 1)
    if width(big_px, small_px) > inner:
        raise _NoFit(f"{field}: {text[:40]!r} does not fit its {inner}px tile")
    tall = max(big_px if k != "small" else small_px for _t, k, _c in runs)
    base = y0 + (y1 - y0) / 2 + tall * 0.36
    x = x0 + 28
    for t, k, colour in runs:
        size = big_px if k != "small" else small_px
        page.legible(size, field)
        page.line(field, t, size, x, base, "ls", colour, box)
        x += page.length(t, size) + gap


def _lay_spotlight(page: look.Page, image: TemplateImage, top: int, bottom: int, *, ladder: bool = True) -> None:
    """The headline, then ONE card filling the room to the footer (form4_ceo_spotlight.jpg, mock_form4.spotlight): the
    logo disc, the chip above the name; a hairline; the figure big in the series' tone, centred in its band; a
    hairline; each line as a row of stat tiles, laid from the card's foot up — so the FIGURE takes every pixel left:
    the largest size ≤ SPOT_FIGURE_STYLE whose ink fits the band's width and its height less 2 × 28 px. `ladder` (the
    picture): the headline takes its OWN size ladder first — SPOT_HEAD_PX, from 80 down to 60 by 2, the first that
    sets it in ≤ 3 lines (mock_form4.headline2) — independent of the ramp, which only steps the card's contents
    (and the headline too, at its ramp size, when even 60 px takes more than 3 lines). The ramp steps on when the
    figure would go under its floor (80 px)."""
    spec, logos = image.spec, image.logos
    x0, x1, tx0, tx1 = _columns(page)
    y: Optional[int] = None
    if ladder:
        for hpx in SPOT_HEAD_PX:
            try:
                y = look.headline(page, "headline", spec["headline"], HEADLINE_STYLE, tx0, top, tx1 - tx0,
                                  max_lines=3, size=hpx)
                break
            except _NoFit:
                continue
    if y is None:
        y = look.headline(page, "headline", spec["headline"], HEADLINE_STYLE, tx0, top, tx1 - tx0, max_lines=4)
    card_top = y + _gap(page, 30, 12)
    # the picture's card ends 36 px over the footer (mock_form4.spotlight: `bottom = foot_top - 36`), not the rows' 34
    card_bottom = bottom - (SPOT_FOOTER_GAP - 34) if ladder else bottom
    pad = _gap(page, 44, 22)
    ix0, ix1 = x0 + pad, x1 - pad
    header = spec["header"]
    r = page.px(SPOT_DISC)
    tx = ix0 + 2 * r + 34
    cs = page.px(SPOT_CHIP_PX)
    name = page.measure(header["name"], page.px(SPOT_NAME_STYLE), ix1 - tx, "header.name", leading=1.1,
                        max_lines=_cap(page, 2))
    chip_text = header.get("chip")
    chip_h = look.chip_size(page, chip_text, cs)[1] if chip_text else 0
    chip_gap = _gap(page, SPOT_CHIP_GAP, 6)
    # the mock's block: the chip, its gap, the name advanced by its SIZE (its descenders below are `spill`)
    name_adv = (len(name.lines) - 1) * name.pitch + name.size
    asc_n, desc_n = page.font(name.size).getmetrics()
    block = name_adv + (chip_h + chip_gap if chip_text else 0)
    hh = max(2 * r, block + max(0, asc_n + desc_n - name.size))
    div1 = card_top + pad + hh + _gap(page, 30, 12)
    lines = list(spec.get("lines") or ())
    tile_h, tile_gap = page.px(TILE_H), _gap(page, 16, 8)
    tiles_h = len(lines) * tile_h + max(0, len(lines) - 1) * tile_gap
    tiles_top = card_bottom - pad - tiles_h
    div2 = tiles_top - _gap(page, 30, 12) if lines else card_bottom - pad
    band_top, band_bot = div1 + 3, div2
    margin = _gap(page, 28, 12)
    fig = spec["figure"]
    size = page.px(SPOT_FIGURE_STYLE)
    room_w, room_h = ix1 - ix0, (band_bot - band_top) - 2 * margin

    def ink(sz: int) -> Tuple[int, int, int, int]:
        return page.font(sz).getbbox(fig, anchor="ls")

    fl, ft, fr, fb = ink(size)
    while size > SPOT_FIGURE_STYLE.floor and (fr - fl > room_w or fb - ft > room_h):
        size = max(SPOT_FIGURE_STYLE.floor, size - 4)
        fl, ft, fr, fb = ink(size)
    if fr - fl > room_w or fb - ft > room_h:
        raise _NoFit(f"figure: {fig[:20]!r} needs {fr - fl}×{fb - ft}px at {size}px, the band holds "
                     f"{room_w}×{max(0, room_h)}px")
    page.legible(size, "figure")
    box = look.card(page, (x0, card_top, x1, card_bottom), radius=30)
    page.disc(ix0 + r, card_top + pad + hh / 2, r, logos[header["logo"]])
    ty = card_top + pad + (hh - block) / 2
    if chip_text:
        look.chip(page, "header.chip", chip_text, cs, tx, ty, bound=box)
        ty += chip_h + chip_gap
    page.put("header.name", name, tx, ty, "la", look.INK, box)
    page.rect((ix0, div1, ix1, div1 + 3), 0, look.DIVIDER)
    base = band_top + (band_bot - band_top - (fb - ft)) / 2 - ft
    page.line("figure", fig, size, ix0 - fl, base, "ls", _tone(image, fig), box)
    if lines:
        page.rect((ix0, div2, ix1, div2 + 3), 0, look.DIVIDER)
    ty2 = tiles_top
    for i, ln in enumerate(lines):
        parts = ln.split(" · ")
        n = len(parts)
        each = (ix1 - ix0 - (n - 1) * tile_gap) / n
        for j, part in enumerate(parts):
            tx0_ = ix0 + j * (each + tile_gap)
            tbox = page.rect((tx0_, ty2, tx0_ + each, ty2 + tile_h), 18, look.TILE)
            _tile_runs(page, part, f"lines[{i}]", tbox)
        ty2 += tile_h + tile_gap


def _bar_row(page: look.Page, x0: float, x1: float, y: float, label: str, value: str, lpath: str, vpath: str,
             lsize: int, vsize: int, value_fill: look.RGB, bound: Box, *, label_fill: look.RGB = look.INK,
             gap: int = 24) -> int:
    """A label left and its value right-aligned on one baseline, at least `gap` apart (the label never runs into its
    value: it wraps). Returns the y under them."""
    vw = page.length(value, vsize)
    room = (x1 - x0) - vw - gap
    if room <= 0:
        raise _NoFit(f"{vpath}: {value[:20]!r} leaves no room for its label")
    lt = page.measure(label, lsize, room, lpath, leading=1.15, max_lines=_cap(page, 2))
    asc, desc = page.font(max(lsize, vsize)).getmetrics()
    base = y + asc
    page.put(lpath, look.Text(lt.lines, lt.size, lt.pitch, lt.height, lt.width), x0, base, "ls", label_fill, bound)
    page.line(vpath, value, vsize, x1, base, "rs", value_fill, bound)
    return int(base + desc + (len(lt.lines) - 1) * lt.pitch)


def _seg_widths(ratios: Sequence[float], span: float, bar_h: int, seam: int) -> List[float]:
    """Each part's width, proportional to its ratio (normalised); a part too thin to see is widened to a floor taken
    from the widest part, so the bar's total width never changes (mock_money_map.seg_widths)."""
    tot = sum(ratios)
    ws = [span * r / tot for r in ratios]
    n = len(ws)
    for i, w in enumerate(ws):
        floor = (seam + 10) if 0 < i < n - 1 else (seam / 2 + bar_h / 2 + 6)
        if w < floor:
            big = max(range(n), key=lambda k: ws[k])
            ws[big] -= floor - w
            ws[i] = floor
    return ws


def _stackable(spec: Mapping[str, Any]) -> bool:
    segs = spec["segments"]
    return (len(segs) <= len(look.SEG_COLORS) and sum(float(s["ratio"]) for s in segs) > 0
            and all(s["style"] == "fill" and look.signed_tone(s["value"]) != "loss" for s in segs))


def _bars_header(page: look.Page, image: TemplateImage, top: int, head_px: Optional[int]) -> int:
    """The Money Map's header (mock_money_map.header_block): the plate — the logo disc in a gold ring, its right edge
    on the kicker pill's, the TICKER in a gold pill over the disc's foot (the company's name for a spec without one,
    a news-v22 spec) — the headline in the column beside it (under it when its words do not fit), and the sub line
    under the headline: beside the plate when it clears it, else under the chip. `head_px` (the picture's head
    ladder): the header at its step-0 sizes and the headline at that size; None: everything at the ramp's step.
    Returns the y under the header (the mock's `max(sub's end, chip foot + 12)`)."""
    spec, logos = image.spec, image.logos
    _x0, _x1, tx0, tx1 = _columns(page)
    header = spec["header"]
    hstep = 0 if head_px is not None else page.step
    r = PLATE_DISC.size(hstep)
    ring = r + look.GOLD_RING_GAP
    cx, cy = tx1 - ring, top + 6 + ring
    page.disc(cx, cy, r, logos[header["logo"]], ring=True)
    cs = CHIP_PX.size(hstep)
    page.legible(cs, "header.chip")
    chip_text, chip_field = ((header["chip"], "header.chip") if header.get("chip") is not None
                             else (header["name"], "header.name"))
    cw, ch = look.chip_size(page, chip_text, cs)
    pill = look.chip(page, chip_field, chip_text, cs, min(cx - cw / 2, tx1 - cw), cy + r - 4,
                     fill=look.GOLD, ink=look.INK)
    plate_bottom = pill[3] + _gap(page, 12)
    col_w = (cx - ring) - tx0 - 28
    if pill[0] < tx0 + col_w + 8 and pill[3] > top:
        col_w = min(col_w, int(pill[0] - 16 - tx0))
    foot: Optional[int] = None
    start = top
    beside = col_w >= BARS_HEAD_MIN_COLUMN
    if beside:
        try:
            foot = look.headline(page, "title", spec["title"], HEADLINE_STYLE, tx0, top, col_w, max_lines=3,
                                 size=head_px)
        except _NoFit:
            beside = False
    if foot is None:
        # a title whose words do not fit beside the plate (a long company name): full width under the plate
        # (card_layouts._opening's rule) — never a refused picture for a name the server's bounds let through
        start = pill[3] + _gap(page, 24)
        foot = look.headline(page, "title", spec["title"], HEADLINE_STYLE, tx0, start, tx1 - tx0, max_lines=3,
                             size=head_px)
    hsize = HEADLINE_STYLE.size(page.step) if head_px is None else head_px
    asc, desc = page.font(hsize).getmetrics()
    # the mock's headline bottom: its lines' pitch × their count + 10 (not the last line's foot)
    yb = int(round(foot - (asc + desc) + round(hsize * look.HEADLINE_LEADING))) + _gap(page, 10)
    ssize = SUBTITLE_STYLE.size(hstep)
    page.legible(ssize, "subtitle")
    if beside and page.length(spec["subtitle"], ssize) <= col_w:
        t = page.measure(spec["subtitle"], ssize, col_w, "subtitle", leading=SUBTITLE_STYLE.leading)
        page.put("subtitle", t, tx0, yb, "la", look.SUB, page.frame.area)
        return max(yb + len(t.lines) * int(ssize * SUBTITLE_STYLE.leading) + _gap(page, 8), plate_bottom)
    sy = max(yb, plate_bottom)
    t = page.measure(spec["subtitle"], ssize, tx1 - tx0, "subtitle", leading=SUBTITLE_STYLE.leading)
    page.put("subtitle", t, tx0, sy, "la", look.SUB, page.frame.area)
    return sy + len(t.lines) * int(ssize * SUBTITLE_STYLE.leading) + _gap(page, 8)


def _fit_line(page: look.Page, text: str, size: int, width: float, floor: int, what: str) -> int:
    """The largest size ≤ `size` (down to `floor`) at which `text` fits `width` on ONE line (mock_money_map's
    shrink-to-fit). _NoFit when even `floor` does not."""
    while size > floor and page.length(text, size) > width:
        size -= 1
    if page.length(text, size) > width:
        raise _NoFit(f"{what}: {text[:40]!r} does not fit {int(width)}px on one line at {size}px")
    page.legible(size, what)
    return size


def _lay_bars(page: look.Page, image: TemplateImage, top: int, bottom: int, head_px: Optional[int] = None,
              level: Optional[Tuple[int, bool]] = None) -> None:
    """The Money Map (money_map_*.jpg). `head_px` with `level` (the picture: layout_template's HEAD LADDER × the card
    LEVELS, mock_money_map.render's `hsize in (80, 72, 66)` × `LEVELS` × `tight`): the header at its step-0 sizes and
    the headline at `head_px`; the cards sized by the level (`_lay_bars_level`) — every size, the stacked bar, the
    legend, the gaps and the callout exactly as the mock draws them. None (the data card, and the picture's last
    resort): everything steps together along the ramp."""
    if image.cover and image.spec.get("costs") is not None:
        # owner decision 2026-11-20: a Money Map loss year's video COVER (costs_cover_image)
        _lay_costs_cover(page, image, top, bottom)
        return
    y = _bars_header(page, image, top, head_px)
    if level is not None:
        _lay_bars_level(page, image, y + 6, bottom, level)
        return
    spec = image.spec
    x0, x1, tx0, tx1 = _columns(page)
    hstep = 0 if head_px is not None else page.step
    mark = page.mark()
    gap = _gap(page, 22)
    cy = y + gap
    pad_x, pad_y = 34, _gap(page, 30)
    lsize, vsize = page.px(BAR_LABEL_STYLE), page.px(BAR_VALUE_STYLE)
    # 1 — the segments: ONE stacked bar under the flow's first line (the total), and its legend
    segs, total = spec["segments"], spec["flow"][0]
    card_top = cy
    card_rects = len(page.rects)
    ix0, ix1 = x0 + pad_x, x1 - pad_x
    stacked = _stackable(spec)
    rows_box: Box = (x0, card_top, x1, bottom)
    tot = "" if spec.get("costs") is not None else "#total"        # drawn once on a loss year (no flow card)
    cy = _bar_row(page, ix0, ix1, card_top + pad_y, total["label"], total["value"], f"flow[0].label{tot}",
                  f"flow[0].value{tot}", lsize, vsize + 8, look.INK, rows_box)
    if stacked:
        bh = page.px(STACK_H)
        cy += _gap(page, 14)
        widths = _seg_widths([float(s["ratio"]) for s in segs], ix1 - ix0, bh, 4)
        page.stacks.append(look.Stack((ix0, int(cy), ix1, int(cy) + bh), tuple(widths),
                                      tuple(look.SEG_COLORS[:len(segs)]), 4, look.CARD))
        cy += bh + _gap(page, 22)
    else:
        cy += _gap(page, 16)
    dot = 11 if stacked else 0
    lead = (2 * dot + 14) if dot else 0
    leg_gap = _gap(page, 12)
    rows = len(segs)
    cols: List[Tuple[float, float, Sequence[Any], int]] = [(ix0, ix1, segs, 0)]
    leg_size = lsize
    if len(segs) >= 5:
        # five or more segments: two content-sized columns, the legend's size stepping down (to the 22 px floor) until
        # they fit side by side (mock_money_map.legend_fit_two_cols); else one column at the ramp's size
        half = (len(segs) + 1) // 2
        size = lsize
        floor = max(22, page.min_px)
        while True:
            need = []
            for col in (segs[:half], segs[half:]):
                lw = max(page.length(s["label"], size) for s in col)
                vw = max(page.length(s["value"], size) for s in col)
                need.append(lead + lw + LEGEND_VALUE_GAP + vw + 1)
            if sum(need) + 40 <= ix1 - ix0 or size <= floor:
                break
            size -= 1
        if sum(need) + 40 <= ix1 - ix0:
            cols = [(ix0, ix0 + need[0], segs[:half], 0), (ix1 - need[1], ix1, segs[half:], half)]
            rows = half
            leg_size = size
    leg_top = cy
    leg_bottom = cy
    asc_l = page.font(leg_size).getmetrics()[0]
    hb = page.font(leg_size).getbbox("H", anchor="ls")
    for cx0, cx1, col, first in cols:
        ry = leg_top
        for k, s in enumerate(col):
            i = first + k
            if k:
                ry += leg_gap
            yb = _bar_row(page, cx0 + lead, cx1, ry, s["label"], s["value"], f"segments[{i}].label",
                          f"segments[{i}].value", leg_size, leg_size, look.INK, rows_box, gap=LEGEND_VALUE_GAP)
            if dot:
                mid = ry + asc_l + (hb[1] + hb[3]) / 2
                page.ellipse((cx0, mid - dot, cx0 + 2 * dot, mid + dot), look.SEG_COLORS[i])
            ry = yb
        leg_bottom = max(leg_bottom, ry)
    del rows
    card_bottom = int(leg_bottom + pad_y)
    page.rects.insert(card_rects, Rect((x0, card_top, x1, card_bottom), look.CARD_RADIUS, look.CARD))
    if spec.get("costs") is not None:
        # owner decision 2026-11-20: a loss year's costs card and its callout, at the ramp's step
        z = _costs_at_step(page, COSTS_PICTURE)
        y3 = _costs_card(page, spec["costs"], x0, x1, card_bottom + gap, z) + gap
        end = _costs_callout(page, spec["costs"], x0, x1, y3, z)
        if end > bottom:
            raise _NoFit(f"bars: the cards end at {end}px, room to {bottom}px")
        if bottom - end > 0:
            page.shift(mark, (bottom - end) // 2)
        return
    # 2 — the flow: a stepped list, one bar per step; its last line GREEN for a profit, RED for a loss
    flow = spec["flow"]
    cy = card_bottom + gap
    f_top = cy
    rail_x = x0 + pad_x + 6
    fx0, fx1 = x0 + pad_x + 40, x1 - pad_x
    span = fx1 - fx0
    loss = [b["style"] == "outline" for b in flow]
    pmax = max([float(b["ratio"]) for b, l in zip(flow, loss) if not l] or [0.0])
    nmax = max([float(b["ratio"]) for b, l in zip(flow, loss) if l] or [0.0])
    scale = span / (pmax + nmax) if (pmax + nmax) > 0 else span
    ax = fx0 + nmax * scale
    bh = page.px(BAR_H)
    cy += pad_y
    dots: List[Tuple[float, look.RGB]] = []
    fbox: Box = (x0, f_top, x1, bottom)
    marks_rects = len(page.rects)
    for i, b in enumerate(flow):
        last = i == len(flow) - 1
        tone = look.RED if loss[i] else (look.GREEN if last else None)
        vfill = tone or look.INK
        row_top = cy
        base_bottom = _bar_row(page, fx0, fx1, cy, b["label"], b["value"], f"flow[{i}].label", f"flow[{i}].value",
                               lsize, vsize, vfill, fbox)
        asc = page.font(vsize).getmetrics()[0]
        dots.append((row_top + asc * 0.62, tone or look.BAR_FILL))
        by = base_bottom + _gap(page, 8)
        ratio = float(b["ratio"])
        w = 0.0 if ratio <= 0 else max(8.0, ratio * (scale if nmax > 0 else span))
        fill = tone or look.BAR_FILL
        if nmax > 0:
            if ax > fx0:
                page.rect((fx0, by, ax, by + bh), bh // 2, look.PALE_RED)
            page.rect((ax, by, fx1, by + bh), bh // 2, look.BAR_TRACK)
            if w:
                page.rect((ax - w, by, ax, by + bh) if loss[i] else (ax, by, ax + w, by + bh), bh // 2, fill)
            page.rect((ax - 1, by - 6, ax + 2, by + bh + 6), 0, look.INK)
        else:
            page.rect((fx0, by, fx1, by + bh), bh // 2, look.BAR_TRACK)
            if w:
                page.rect((fx0, by, fx0 + min(w, span), by + bh), bh // 2, fill)
        cy = by + bh + _gap(page, 18)
    cy -= _gap(page, 18)
    f_bottom = int(cy + pad_y)
    page.rects.insert(marks_rects, Rect((x0, f_top, x1, f_bottom), look.CARD_RADIUS, look.CARD))
    if len(dots) > 1:
        page.rects.insert(marks_rects + 1, Rect((rail_x - 2, int(dots[0][0]), rail_x + 2, int(dots[-1][0])), 0,
                                                look.BAR_TRACK))
    for dy, colour in dots:
        page.ellipse((rail_x - 9, dy - 9, rail_x + 9, dy + 9), colour)
    # 3 — the callout: its first clause (INK) over its figure clause in the net line's tone, a stripe of that tone
    cy = f_bottom + gap
    c_top = cy
    tone = look.RED if loss[-1] else look.GREEN
    text = spec["callout"]
    i = text.find(", ")
    lead, rest = (text[:i + 1], text[i + 2:]) if i >= 0 else ("", text)
    ctx = x0 + 60
    width = x1 - 30 - ctx
    cpad = _gap(page, 28)
    parts = []
    if lead:
        parts.append(page.measure(lead, CALLOUT_LEAD_STYLE.size(hstep), width, "callout", leading=1.2,
                                  max_lines=_cap(page, 2)))
    parts.append(page.measure(rest, CALLOUT_STYLE.size(hstep), width, "callout", leading=CALLOUT_STYLE.leading,
                              max_lines=_cap(page, 3)))
    inner = _gap(page, 8)
    h = 2 * cpad + sum(t.height for t in parts) + inner * (len(parts) - 1)
    cbox = look.card(page, (x0, c_top, x1, c_top + h), radius=24)
    page.rect((x0 + 26, c_top + 26, x0 + 34, c_top + h - 26), 4, tone)
    ty = c_top + cpad
    for k, t in enumerate(parts):
        colour = look.INK if (lead and k == 0) else tone
        page.put("callout", t, ctx, ty, "la", colour, cbox)
        ty += t.height + inner
    end = c_top + h
    if end > bottom:
        raise _NoFit(f"bars: the cards end at {end}px, room to {bottom}px")
    spare = bottom - end
    if spare > 0:
        page.shift(mark, spare // 2)


def _lay_bars_level(page: look.Page, image: TemplateImage, top: int, bottom: int, level: Tuple[int, bool]) -> None:
    """The Money Map's three cards at card LEVEL `level` = (an index into BARS_LEVELS, the legend `tight`) between
    `top` (the header's bottom + 6) and `bottom` (the footer's top − BARS_FLOOR_GAP), drawn as mock_money_map does:
    1 — the total (the flow's first line) over ONE stacked bar (64 / 60 / 56 px by the level's pad) and its legend
    (a colour dot, the label, the value; five or more segments in two content-sized columns whose label shrinks to
    fit, the value at most 2 px larger); 2 — the flow as a stepped list (a rail of dots, one bar per step, its last
    line GREEN for a profit, RED for a loss); 3 — the callout (30 / 50 px, padded 30 / 28) with its stripe. Every label
    shrinks (to 22 px) to fit ONE line beside its value, never wraps; the cards are BARS_BASE_GAP apart plus up to
    BARS_GAP_EXTRA of the spare, the block centred in what is left. _NoFit when they do not fit — the next level."""
    spec = image.spec
    x0, x1, _tx0, _tx1 = _columns(page)
    ls, vs, bh, rg, pad = BARS_LEVELS[level[0]]
    tight = level[1]
    tot_l, tot_v = ls, vs + 8
    stack_h = 64 if pad >= 32 else (60 if pad >= 28 else 56)
    leg = ls - 2 if tight else ls
    leg_gap = max(10, rg - (10 if tight else 8))
    after_tot, after_bar = 14, (24 if pad >= 28 else 18)
    pad_x = 34
    ix0, ix1 = x0 + pad_x, x1 - pad_x
    floor = max(22, page.min_px)
    segs, flow = spec["segments"], spec["flow"]
    total = flow[0]
    stacked = _stackable(spec)
    n = len(segs)

    def metrics(size: int) -> Tuple[int, int]:
        return page.font(size).getmetrics()

    # 1 — the legend's columns and sizes (mock_money_map.legend_fit_two_cols)
    dot = BARS_LEG_DOT if stacked else 0
    lead = (2 * dot + BARS_LEG_DOT_GAP) if dot else 0
    lsz = vsz = leg
    if n >= 5:
        half = (n + 1) // 2
        groups = [segs[:half], segs[half:]]
        while True:
            need = [lead + max(page.length(sg["label"], lsz) for sg in col) + LEGEND_VALUE_GAP
                    + max(page.length(sg["value"], vsz) for sg in col) for col in groups]
            if sum(need) + BARS_LEG_COL_GAP <= ix1 - ix0 or lsz <= floor:
                break
            lsz -= 1
            vsz = max(floor, min(vsz, lsz + 2))
        if sum(need) + BARS_LEG_COL_GAP > ix1 - ix0:
            raise _NoFit("segments: the two legend columns do not fit side by side")
        cols = [(ix0, ix0 + need[0], 0, half), (ix1 - need[1], ix1, half, n)]
        rows = half
    else:
        cols = [(ix0, ix1, 0, n)]
        rows = n
    ta, td = metrics(tot_v)
    la, ld = metrics(leg)
    rh = la + ld
    h1 = (2 * pad + ta + td + (after_tot + stack_h + after_bar if stacked else 16) + rows * rh
          + (rows - 1) * leg_gap)
    fa, fd = metrics(vs)
    row_f = fa + fd + BARS_TEXT_BAR_GAP + bh
    c1, c2 = BARS_CALLOUT_PX
    a1, d1 = metrics(c1)
    a2, d2 = metrics(c2)
    costs = spec.get("costs")
    if costs is not None:
        # owner decision 2026-11-20 (money_map_loss_A_rivian.jpg, mock_money_map_loss.render_A): a loss year's COSTS
        # card and its two-line callout at the mock's own sizes, 16 px apart plus up to 10 of the spare
        h2 = _costs_card_h(page, costs, x0 + COSTS_PAD_X, x1 - COSTS_PAD_X, COSTS_PICTURE)
        h3 = _costs_callout_h(page, COSTS_PICTURE)
        base_gap, gap_extra = COSTS_BASE_GAP, COSTS_GAP_EXTRA
    else:
        h2 = 2 * pad + len(flow) * row_f + (len(flow) - 1) * rg
        h3 = BARS_CALLOUT_PAD[0] + a1 + d1 + BARS_CALLOUT_INNER + a2 + d2 + BARS_CALLOUT_PAD[1]
        base_gap, gap_extra = BARS_BASE_GAP, BARS_GAP_EXTRA
    need_h = h1 + h2 + h3 + 2 * base_gap
    avail = bottom - top
    if need_h > avail:
        raise _NoFit(f"bars: level {level} needs {need_h}px, {avail}px are left")
    spare = avail - need_h
    gap = base_gap + min(gap_extra, spare // 3)
    y = top + max(0, avail - (need_h - 2 * base_gap + 2 * gap)) // 2

    # 1 — the stacked card: the total, the bar, the legend
    box1 = look.card(page, (x0, y, x1, y + h1))
    base = y + pad + ta
    vw = page.length(total["value"], tot_v)
    # the flow's Revenue line drawn as the revenue card's total — a second time on a profit year ("#total": the flow card
    # draws it again), the only time on a loss year (the costs card replaces the flow card)
    tot = "" if costs is not None else "#total"
    tsz = _fit_line(page, total["label"], tot_l, (ix1 - ix0) - vw - 24, floor, f"flow[0].label{tot}")
    page.line(f"flow[0].label{tot}", total["label"], tsz, ix0, base, "ls", look.INK, box1)
    page.line(f"flow[0].value{tot}", total["value"], tot_v, ix1, base, "rs", look.INK, box1)
    if stacked:
        by = base + td + after_tot
        widths = _seg_widths([float(sg["ratio"]) for sg in segs], ix1 - ix0, stack_h, 4)
        page.stacks.append(look.Stack((ix0, int(by), ix1, int(by) + stack_h), tuple(widths),
                                      tuple(look.SEG_COLORS[:n]), 4, look.CARD))
        ly = by + stack_h + after_bar
    else:
        ly = base + td + 16
    for cx0, cx1, first, last in cols:
        for k, i in enumerate(range(first, last)):
            sg = segs[i]
            bl = ly + k * (rh + leg_gap) + la
            tx = cx0 + lead
            room = (cx1 - tx) - page.length(sg["value"], vsz) - LEGEND_VALUE_GAP
            lz = _fit_line(page, sg["label"], lsz, room, floor, f"segments[{i}].label")
            if dot:
                hb = page.font(lsz).getbbox("H", anchor="ls")
                mid = bl + (hb[1] + hb[3]) / 2
                page.ellipse((cx0, mid - dot, cx0 + 2 * dot, mid + dot), look.SEG_COLORS[i])
            page.line(f"segments[{i}].label", sg["label"], lz, tx, bl, "ls", look.INK, box1)
            page.line(f"segments[{i}].value", sg["value"], vsz, cx1, bl, "rs", look.INK, box1)

    if costs is not None:
        # 2 + 3 — a loss year: the costs card, then its callout
        y3 = _costs_card(page, costs, x0, x1, y + h1 + gap, COSTS_PICTURE) + gap
        end = _costs_callout(page, costs, x0, x1, y3, COSTS_PICTURE)
        if end > bottom:
            raise _NoFit(f"bars: the cards end at {end}px, room to {bottom}px")
        return

    # 2 — the flow card: a rail of step dots, one bar per step (losses run LEFT from the axis, in red)
    y2 = y + h1 + gap
    rects0 = len(page.rects)
    box2 = look.card(page, (x0, y2, x1, y2 + h2))
    rail_x = x0 + pad_x + 6
    fx0, fx1 = x0 + pad_x + 40, x1 - pad_x
    span = fx1 - fx0
    loss = [b["style"] == "outline" for b in flow]
    pmax = max([float(b["ratio"]) for b, l_ in zip(flow, loss) if not l_] or [0.0])
    nmax = max([float(b["ratio"]) for b, l_ in zip(flow, loss) if l_] or [0.0])
    scale = span / (pmax + nmax) if (pmax + nmax) > 0 else span
    ax = fx0 + nmax * scale
    dots: List[Tuple[float, look.RGB]] = []
    for i, b in enumerate(flow):
        ry = y2 + pad + i * (row_f + rg)
        last_row = i == len(flow) - 1
        tone = look.RED if loss[i] else (look.GREEN if last_row else None)
        base = ry + fa
        vwf = page.length(b["value"], vs)
        lz = _fit_line(page, b["label"], ls, span - vwf - 24, floor, f"flow[{i}].label")
        page.line(f"flow[{i}].label", b["label"], lz, fx0, base, "ls", look.INK, box2)
        page.line(f"flow[{i}].value", b["value"], vs, fx1, base, "rs", tone or look.INK, box2)
        dots.append((ry + (fa + fd) / 2 + 2, tone or look.BAR_FILL))
        by = ry + fa + fd + BARS_TEXT_BAR_GAP
        ratio = float(b["ratio"])
        w = 0.0 if ratio <= 0 else max(8.0, ratio * (scale if nmax > 0 else span))
        fill = tone or look.BAR_FILL
        if nmax > 0:
            if ax > fx0:
                page.rect((fx0, by, ax, by + bh), bh // 2, look.PALE_RED)
            page.rect((ax, by, fx1, by + bh), bh // 2, look.BAR_TRACK)
            if w:
                page.rect((ax - w, by, ax, by + bh) if loss[i] else (ax, by, ax + w, by + bh), bh // 2, fill)
            page.rect((ax - 1, by - 6, ax + 2, by + bh + 6), 0, look.INK)
        else:
            page.rect((fx0, by, fx1, by + bh), bh // 2, look.BAR_TRACK)
            if w:
                page.rect((fx0, by, fx0 + min(w, span), by + bh), bh // 2, fill)
    if len(dots) > 1:
        page.rects.insert(rects0 + 1, Rect((rail_x - 2, int(dots[0][0]), rail_x + 2, int(dots[-1][0])), 0,
                                           look.BAR_TRACK))
    for dy, colour in dots:
        page.ellipse((rail_x - 9, dy - 9, rail_x + 9, dy + 9), colour)

    # 3 — the callout: its first clause (INK) over its figure clause in the net line's tone, a stripe of that tone
    y3 = y2 + h2 + gap
    tone = look.RED if loss[-1] else look.GREEN
    text = spec["callout"]
    j = text.find(", ")
    first, rest = (text[:j + 1], text[j + 2:]) if j >= 0 else ("", text)
    ctx = x0 + 60
    width = x1 - 30 - ctx
    cbox = look.card(page, (x0, y3, x1, y3 + h3), radius=24)
    page.rect((x0 + 26, y3 + 26, x0 + 34, y3 + h3 - 26), 4, tone)
    if first:
        s1 = _fit_line(page, first, c1, width, floor, "callout")
        page.line("callout", first, s1, ctx, y3 + BARS_CALLOUT_PAD[0] + a1, "ls", look.INK, cbox)
    s2 = _fit_line(page, rest, c2, width, floor, "callout")
    page.line("callout", rest, s2, ctx, y3 + BARS_CALLOUT_PAD[0] + a1 + d1 + BARS_CALLOUT_INNER + a2, "ls", tone,
              cbox)
    if y3 + h3 > bottom:
        raise _NoFit(f"bars: the cards end at {y3 + h3}px, room to {bottom}px")


# ── the Money Map LOSS year: the costs card (owner decision 2026-11-20, "A: costs vs revenue") ─────────────────────


def costs_bar_widths(costs: Mapping[str, Any], span: float) -> Tuple[List[float], float, float]:
    """(each part's width in px, left to right — one undivided [costs] piece when the card has no parts —, where the
    SOLID red ends, where the revenue bar ends), px from the bars' left edge on a `span` px scale (the ratios' one scale,
    mock_money_map_loss.a_card):

    * a part whose ratios sum past the span (each is rounded to RATIO_PLACES) is scaled back to it;
    * a non-zero piece thinner than its floor — cost of sales and the third part (interest, tax and other items as a
      cost) COSTS_THIN_END, operating expenses COSTS_THIN_END as the last part else COSTS_THIN_MID — is widened by
      taking the width from operating expenses, never from cost of sales (where it ends against the revenue marker is
      a fact: a gross loss runs it a few pixels past the marker); operating expenses itself, when thin, takes its width
      from the third part, else — the one case left — from cost of sales;
    * the hatched end (a `note`: interest, tax and other INCOME, the costs it covered) runs from where costs end to the
      bar's end, at least COSTS_THIN_END wide (its width taken from operating expenses' solid red) — but the floor
      never pulls the solid red back to the revenue marker (re-check round 2026-11-26: a small net loss beside other
      income drew the costs ENDING BEFORE revenue): how far the costs run past revenue is the net loss itself, so the
      solid red keeps at least COSTS_BOX_GAP of it (its own end when that is less) and the hatch is narrower instead;
      without a note the solid red is the whole bar (operating expenses' bar already absorbed any sliver of other
      items).

    The bar's total length never changes. Pure."""
    rev_w = float(costs["revenue"]["ratio"]) * span
    parts = costs.get("parts") or []
    if not parts:
        whole = min(float(costs["total"]["ratio"]), 1.0) * span
        return [whole], whole, rev_w
    ws = [float(p["ratio"]) * span for p in parts]
    bar = sum(ws)
    if bar > span:
        ws = [w * span / bar for w in ws]
        bar = span
    n = len(ws)
    floors = [COSTS_THIN_END, COSTS_THIN_MID if n == 3 else COSTS_THIN_END] + ([COSTS_THIN_END] if n == 3 else [])
    for i in ([0, 2] if n == 3 else [0]):
        if 0 < ws[i] < floors[i]:
            ws[1] -= floors[i] - ws[i]
            ws[i] = float(floors[i])
    if ws[1] < floors[1]:
        need = floors[1] - ws[1]
        donors = [i for i in ((2, 0) if n == 3 else (0,)) if ws[i] - need >= floors[i]]
        if donors:
            ws[donors[0]] -= need
            ws[1] = float(floors[1])
    bar = sum(ws)
    if costs.get("note") is not None:
        solid = min(float(costs["total"]["ratio"]) * span, bar)
        if bar - solid < COSTS_THIN_END:
            solid = max(bar - COSTS_THIN_END, min(solid, rev_w + COSTS_BOX_GAP))
    else:
        solid = bar
    return ws, solid, rev_w


def _costs_metrics(page: look.Page, size: int) -> Tuple[int, int]:
    return page.font(size).getmetrics()


def _costs_legend_rows(page: look.Page, costs: Mapping[str, Any], width: float, size: int) -> List[List[int]]:
    """The legend's items (one per part) laid left to right, wrapping to a new row (mock_money_map_loss.legend_items):
    the part indices of each row. An item wider than the row alone is _NoFit."""
    rows: List[List[int]] = []
    cur: List[int] = []
    cur_w = 0.0
    for i, p in enumerate(costs.get("parts") or []):
        w = (2 * COSTS_LEG_DOT + COSTS_LEG_TEXT_GAP + page.length(p["label"], size) + COSTS_LEG_VALUE_GAP
             + page.length(p["value"], size))
        if w > width:
            raise _NoFit(f"costs.parts[{i}]: the legend item is {int(w)}px, the card {int(width)}px")
        if cur and cur_w + COSTS_LEG_ITEM_GAP + w > width:
            rows.append(cur)
            cur, cur_w = [], 0.0
        cur_w = cur_w + (COSTS_LEG_ITEM_GAP if cur else 0) + w
        cur.append(i)
    if cur:
        rows.append(cur)
    return rows


def _costs_note(page: look.Page, costs: Mapping[str, Any], width: float, z: CostsSizes,
               path: str) -> Optional[look.Text]:
    note = costs.get("note")
    if note is None:
        return None
    return page.measure(note, z.note, width - COSTS_NOTE_INDENT, f"{path}.note", leading=1.0,
                        max_lines=_cap(page, 2))


def _costs_card_h(page: look.Page, costs: Mapping[str, Any], ix0: float, ix1: float, z: CostsSizes,
                  path: str = "costs") -> int:
    """The costs card's height (mock_money_map_loss.a_card_h): its pad, row 1, the two bars, the costs row, the legend's
    rows, the note's lines, its pad."""
    va, vd = _costs_metrics(page, z.val)
    la, ld = _costs_metrics(page, z.leg)
    rows = _costs_legend_rows(page, costs, ix1 - ix0, z.leg)
    note = _costs_note(page, costs, ix1 - ix0, z, path)
    na, nd = _costs_metrics(page, z.note)
    h = (z.pad + va + vd + z.after_t + z.bar + z.gap_bars + z.bar + z.bar_gap_text + va + vd
         + (z.before_leg + len(rows) * (la + ld) + (len(rows) - 1) * COSTS_LEG_ROW_GAP if rows else 0)
         + (z.note_gap + len(note.lines) * (na + nd + COSTS_NOTE_LEADING) if note is not None else 0) + z.pad)
    return int(h)


def _costs_dashes(page: look.Page, box: Sequence[float], colour: look.RGB, radius: int) -> None:
    """The net-loss box's dashed edge (mock_money_map_loss.dashed_rect): its top, its bottom and its right side — never
    its left, the revenue marker — each a run of COSTS_DASH px dashes COSTS_DASH_GAP apart, COSTS_DASH_W px thick."""
    x0, y0, x1, y1 = box
    half = COSTS_DASH_W // 2
    for y in (y0, y1):
        x = x0 + radius / 2
        while x < x1 - radius / 2:
            page.rect((x, y - half, min(x + COSTS_DASH, x1 - radius / 2), y - half + COSTS_DASH_W), 0, colour)
            x += COSTS_DASH + COSTS_DASH_GAP
    y = y0 + radius / 2
    while y < y1 - radius / 2:
        page.rect((x1 - half, y, x1 - half + COSTS_DASH_W, min(y + COSTS_DASH, y1 - radius / 2)), 0, colour)
        y += COSTS_DASH + COSTS_DASH_GAP


def _costs_card(page: look.Page, costs: Mapping[str, Any], x0: int, x1: int, y: float, z: CostsSizes,
                path: str = "costs") -> int:
    """The COSTS card at `y` (money_map_loss_A_rivian.jpg, mock_money_map_loss.a_card), between the card edges `x0` and
    `x1`: "Revenue $X" left and "Net loss $Y" (RED) right — over the gap, its value's right edge where the solid red ends
    (at the card's right edge when that would run into the revenue text); the NAVY revenue bar and, past its end, the
    pale net-loss box with its dashed RED edge; the longer costs bar (cost of sales RED, operating expenses MID RED,
    interest / tax / other items as a cost MAROON — the hatched end when they were income), a black marker where revenue
    ends through both bars; "Costs and expenses" with its value in RED; the legend (a dot, the label, the value per
    part); the note beside a hatched swatch. Returns the card's bottom."""
    ix0, ix1 = x0 + COSTS_PAD_X, x1 - COSTS_PAD_X
    span = ix1 - ix0
    h = _costs_card_h(page, costs, ix0, ix1, z, path)
    box = look.card(page, (x0, y, x1, y + h))
    for size, what in ((z.lab, f"{path}.revenue.label"), (z.val, f"{path}.revenue.value"),
                       (z.leg, f"{path}.parts"), (z.note, f"{path}.note")):
        page.legible(size, what)
    ws, solid_w, rev_w = costs_bar_widths(costs, span)
    va, vd = _costs_metrics(page, z.val)
    rev, net, tot = costs["revenue"], costs["net"], costs["total"]
    # row 1: Revenue + its value (left); Net loss + its value (RED, right — over the gap)
    bl = y + z.pad + va
    lw = page.length(rev["label"], z.lab)
    page.line(f"{path}.revenue.label", rev["label"], z.lab, ix0, bl, "ls", look.INK, box)
    page.line(f"{path}.revenue.value", rev["value"], z.val, ix0 + lw + COSTS_LABEL_GAP, bl, "ls", look.INK, box)
    left_end = ix0 + lw + COSTS_LABEL_GAP + page.length(rev["value"], z.val)
    net_w = page.length(net["label"] + " ", z.lab) + page.length(net["value"], z.val)
    anchor = ix0 + solid_w
    if anchor - net_w < left_end + COSTS_ROW_CLEAR:
        anchor = ix1                       # a small net loss beside a long bar: right-aligned on the card instead
    if anchor - net_w < left_end + COSTS_ROW_CLEAR:
        raise _NoFit(f"{path}.net: 'Net loss' runs into 'Revenue' on row 1")
    page.line(f"{path}.net.label", net["label"], z.lab, anchor - net_w, bl, "ls", look.RED, box)
    page.line(f"{path}.net.value", net["value"], z.val, anchor, bl, "rs", look.RED, box)
    # bar 1: revenue, then the net-loss box up to where the solid red ends
    b1 = int(round(bl + vd + z.after_t))
    rev_x = ix0 + rev_w
    page.rect((ix0, b1, rev_x, b1 + z.bar), z.rad, look.BAR_FILL)
    solid_x = ix0 + solid_w
    if solid_x - (rev_x + COSTS_BOX_GAP) >= 1:
        gap_box = (rev_x + COSTS_BOX_GAP, b1, solid_x, b1 + z.bar)
        page.rect(gap_box, z.rad, look.PALE_RED)
        _costs_dashes(page, gap_box, look.RED, z.rad)
    # bar 2: the costs, stacked (its hatched end: the costs other income covered)
    b2 = b1 + z.bar + z.gap_bars
    parts = costs.get("parts") or []
    colours = (look.RED, look.COST_MID_RED, look.COST_MAROON)[:len(ws)] if parts else (look.RED,)
    hatch = (solid_w, sum(ws)) if costs.get("note") is not None else None
    end = int(round(ix0 + sum(ws)))
    page.stacks.append(look.Stack((ix0, b2, end, b2 + z.bar), tuple(ws), tuple(colours), COSTS_SEAM, look.CARD,
                                  radius=z.rad, hatch=hatch))
    # the marker where revenue ends, through both bars (a one-part stack: drawn over the bars)
    mx = int(round(rev_x))
    page.stacks.append(look.Stack((mx - COSTS_MARKER_W // 2, b1 - COSTS_MARKER_OVER, mx - COSTS_MARKER_W // 2
                                   + COSTS_MARKER_W, b2 + z.bar + COSTS_MARKER_OVER), (float(COSTS_MARKER_W),),
                                  (look.INK,), 0, look.INK, radius=0))
    # the costs row: "Costs and expenses" + its value (RED)
    bl2 = b2 + z.bar + z.bar_gap_text + va
    tw_ = page.length(tot["label"], z.lab)
    page.line(f"{path}.total.label", tot["label"], z.lab, ix0, bl2, "ls", look.INK, box)
    page.line(f"{path}.total.value", tot["value"], z.val, ix0 + tw_ + COSTS_LABEL_GAP, bl2, "ls", look.RED, box)
    ly = bl2 + vd
    # the legend: one item per part
    rows = _costs_legend_rows(page, costs, span, z.leg)
    if rows:
        ly += z.before_leg
        la, ld = _costs_metrics(page, z.leg)
        cap = page.font(z.leg).getbbox("H", anchor="ls")[1]
        for r, row in enumerate(rows):
            lbl = ly + la
            x = float(ix0)
            for i in row:
                p = parts[i]
                cy = lbl + cap / 2
                page.ellipse((x, cy - COSTS_LEG_DOT, x + 2 * COSTS_LEG_DOT, cy + COSTS_LEG_DOT), colours[i])
                tx = x + 2 * COSTS_LEG_DOT + COSTS_LEG_TEXT_GAP
                page.line(f"{path}.parts[{i}].label", p["label"], z.leg, tx, lbl, "ls", look.INK, box)
                vx = tx + page.length(p["label"], z.leg) + COSTS_LEG_VALUE_GAP
                page.line(f"{path}.parts[{i}].value", p["value"], z.leg, vx, lbl, "ls", look.INK, box)
                x = vx + page.length(p["value"], z.leg) + COSTS_LEG_ITEM_GAP
            ly = lbl + ld + (COSTS_LEG_ROW_GAP if r < len(rows) - 1 else 0)
    # the note: a hatched swatch, then the plain grey line(s)
    note = _costs_note(page, costs, span, z, path)
    if note is not None:
        na, nd = _costs_metrics(page, z.note)
        ny = ly + z.note_gap
        cap = page.font(z.note).getbbox("H", anchor="ls")[1]
        sy = int(round(ny + na + cap / 2 - COSTS_SWATCH / 2))
        page.stacks.append(look.Stack((ix0, sy, ix0 + COSTS_SWATCH, sy + COSTS_SWATCH), (float(COSTS_SWATCH),),
                                      (look.COST_MID_RED,), 0, look.CARD, radius=COSTS_SWATCH_RADIUS,
                                      hatch=(0.0, float(COSTS_SWATCH)), outline=(look.COST_MID_RED, COSTS_SWATCH_RIM)))
        for k, text in enumerate(note.lines):
            page.line(f"{path}.note", text, z.note, ix0 + COSTS_NOTE_INDENT, ny + na, "ls", look.INK2, box)
            ny += na + nd + COSTS_NOTE_LEADING
    return int(y + h)


def _costs_callout_h(page: look.Page, z: CostsSizes) -> int:
    a1, d1 = _costs_metrics(page, z.callout[0])
    a2, d2 = _costs_metrics(page, z.callout[1])
    return COSTS_CALLOUT_PAD[0] + a1 + d1 + COSTS_CALLOUT_INNER + a2 + d2 + COSTS_CALLOUT_PAD[1]


def _costs_callout(page: look.Page, costs: Mapping[str, Any], x0: int, x1: int, y: float, z: CostsSizes,
                   path: str = "costs") -> int:
    """The costs card's callout (mock_money_map_loss.callout2): the warm banner with a RED stripe, the lead line in INK
    ("For every $100 of revenue, costs were $166.38,"), then the figure line in RED ("so the net loss was $66.38"), each
    shrunk to fit its width (to the 22 px floor). Returns its bottom."""
    h = _costs_callout_h(page, z)
    y = int(round(y))
    cbox = look.card(page, (x0, y, x1, y + h), radius=24)
    page.rect((x0 + 26, y + COSTS_CALLOUT_PAD[0], x0 + 34, y + h - COSTS_CALLOUT_PAD[0]), 4, look.RED)
    a1, d1 = _costs_metrics(page, z.callout[0])
    a2, _d2 = _costs_metrics(page, z.callout[1])
    tx = x0 + 60
    width = x1 - 30 - tx
    floor = max(22, page.min_px)
    lead, figure = costs["callout"]
    s1 = _fit_line(page, lead, z.callout[0], width, floor, f"{path}.callout[0]")
    s2 = _fit_line(page, figure, z.callout[1], width, floor, f"{path}.callout[1]")
    page.line(f"{path}.callout[0]", lead, s1, tx, y + COSTS_CALLOUT_PAD[0] + a1, "ls", look.INK, cbox)
    page.line(f"{path}.callout[1]", figure, s2, tx, y + COSTS_CALLOUT_PAD[0] + a1 + d1 + COSTS_CALLOUT_INNER + a2,
              "ls", look.RED, cbox)
    return y + h


def _costs_at_step(page: look.Page, base: CostsSizes) -> CostsSizes:
    """`base` at the page's ramp step: every text size steps down to the 22 px floor, the bar to 60 % (never under 24
    px), every gap with the text (`_gap`) — the data card's and the last resort's costs card."""
    def t(px: int) -> int:
        return _Style(px, min(px, 22), 1.0).size(page.step)

    bar = _Style(base.bar, max(24, int(base.bar * 0.6)), 1.0).size(page.step)
    return CostsSizes(lab=t(base.lab), val=t(base.val), bar=bar, rad=max(6, min(base.rad, bar // 4)), leg=t(base.leg),
                      note=t(base.note), pad=_gap(page, base.pad), gap_bars=_gap(page, base.gap_bars),
                      after_t=_gap(page, base.after_t), before_leg=_gap(page, base.before_leg),
                      note_gap=_gap(page, base.note_gap), bar_gap_text=_gap(page, base.bar_gap_text),
                      callout=(t(base.callout[0]), t(base.callout[1])))


def _lay_costs_cover(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """A Money Map LOSS year's video COVER (money_map_loss_A_rivian_cover.jpg, mock_money_map_loss.render_D): the header
    (the headline beside the plate, its sub line), then the costs card and its callout COSTS_COVER_CALLOUT_GAP apart,
    COSTS_COVER_TOP plus COSTS_COVER_TOP_SHARE of the room left under the header (never under COSTS_COVER_MIN_TOP), all
    above `bottom` (the caption band's top: the burned captions never cross the card). At ramp step 0 the headline
    tries the head ladder (BARS_HEAD_PX) and the card COSTS_COVER_LEVELS, biggest first; past it, the ramp."""
    costs = image.spec["costs"]
    x0, x1, _tx0, _tx1 = _columns(page)
    tries: List[Tuple[Optional[int], CostsSizes]] = (
        [(hp, z) for hp in BARS_HEAD_PX for z in COSTS_COVER_LEVELS] if page.step == 0
        else [(None, _costs_at_step(page, COSTS_PICTURE))])
    reason = ""
    for head_px, z in tries:
        mark = page.mark()
        try:
            y = _bars_header(page, image, top, head_px)
            ch = _costs_card_h(page, costs, x0 + COSTS_PAD_X, x1 - COSTS_PAD_X, z)
            kh = _costs_callout_h(page, z)
            free = bottom - y - (ch + COSTS_COVER_CALLOUT_GAP + kh)
            if free < COSTS_COVER_MIN_TOP:
                raise _NoFit(f"costs cover: the card and its callout need {ch + kh + COSTS_COVER_CALLOUT_GAP}px, "
                             f"{bottom - y}px are left")
            lift = min(COSTS_COVER_TOP, free) + int(max(0, free - COSTS_COVER_TOP) * COSTS_COVER_TOP_SHARE)
            end = _costs_card(page, costs, x0, x1, y + lift, z)
            end = _costs_callout(page, costs, x0, x1, end + COSTS_COVER_CALLOUT_GAP, z)
            if end > bottom:
                raise _NoFit(f"costs cover: ends at {end}px, room to {bottom}px")
            return
        except _NoFit as e:
            page.rollback(mark)
            reason = str(e)
    raise _NoFit(reason)


def walk_costs_cover(spec: Mapping[str, Any], logos: Mapping[str, LogoArt]) -> List[Tuple[str, str, str]]:
    """A costs cover's spec (`costs_cover_image`) in its draw order — the kicker, the header (logo, name, chip), the
    title, the sub line, the costs card, the footer — checked as `walk` checks a picture's. ValueError on drift."""
    w = _Walker(logos)
    w.text(spec.get("kicker"), "kicker")
    _w_company(w, spec.get("header"), "header", chip=True)
    w.text(spec.get("title"), "title")
    w.text(spec.get("subtitle"), "subtitle")
    _w_costs(w, spec.get("costs"), "costs")
    w.text(spec.get("footer"), "footer")
    return w.items


def costs_cover_image(card: Mapping[str, Any], logos: Mapping[str, LogoArt], *, footer: str, series: Any = None,
                      look: bool = True) -> TemplateImage:
    """A Money Map LOSS year's video COVER (owner decision 2026-11-20, money_map_loss_A_rivian_cover.jpg) as a
    TemplateImage the costs-cover placer draws on the 9:16 frame (`_lay_costs_cover`): the opening card's kicker, its ONE
    company's plate (the logo, the ticker `chip` in the pill), its headline, its `subtitle` and its `costs` card, and the
    run's short `footer`. Every string is the card's own (template_onscreen.opening_strings allows each, the header's
    name being its logo's company name) plus the footer (allowed on every template video card). ValueError on a card it
    cannot read — contract drift, never guessed."""
    keys = card.get("logos") if isinstance(card, Mapping) else None
    if not isinstance(keys, list) or len(keys) != 1 or keys[0] not in logos:
        raise ValueError("opening_card.logos: a costs cover draws its ONE company's plate")
    header: Dict[str, Any] = {"logo": keys[0], "name": logos[keys[0]].name}
    if card.get("chip") is not None:
        header["chip"] = card["chip"]
    spec: Dict[str, Any] = {"layout": "bars", "version": cards.SPEC_VERSION, "kicker": card.get("kicker"),
                            "header": header, "title": card.get("headline"), "subtitle": card.get("subtitle"),
                            "costs": card.get("costs"), "footer": footer}
    items = walk_costs_cover(spec, logos)
    return TemplateImage(layout="bars", spec=spec, logos=dict(logos),
                         strings=tuple(_strings(items, logos, every_name=False)),
                         allowed=tuple(_strings(items, logos, every_name=True)),
                         series=series if isinstance(series, str) and len(series) <= 40 else None, look=look,
                         cover=True)


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _side_art(side: Mapping[str, Any], logos: Mapping[str, LogoArt], path: str) -> LogoArt:
    """The disc a pair side draws: its logo entry — or, for a side with no logo key (a private investee has no
    symbol), a WORDMARK of its own name (already one of the spec's strings; its lines are field
    "wordmark:<path>.name")."""
    if side.get("logo") is not None:
        return logos[side["logo"]]
    return LogoArt(key=f"{path}.name", name=side["name"])


def _lay_pair(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """The investor's plate (its disc in a gold ring, top-right) beside its name as the headline; then the
    investee's card: its disc and name, the label small above the figure (GOLD_INK) right-aligned, the lines on a
    tile across the card's foot."""
    spec, logos = image.spec, image.logos
    x0, x1, tx0, tx1 = _columns(page)
    r = page.px(PLATE_DISC)
    ring = r + look.GOLD_RING_GAP
    cx, cyy = tx1 - ring, top + 6 + ring
    page.disc(cx, cyy, r, _side_art(spec["left"], logos, "left"), ring=True)
    col_w = (cx - ring) - tx0 - 28
    y = look.headline(page, "left.name", spec["left"]["name"], HEADLINE_STYLE, tx0, top, col_w, max_lines=3,
                      tone="white")
    y = max(y, cyy + ring)
    mark = page.mark()
    c_top = y + _gap(page, 30)
    pad = _gap(page, 22)
    rr = page.px(ROW_DISC)
    tx = x0 + 22 + 2 * rr + 28
    right = x1 - 30
    fsize, lsize = page.px(PAIR_FIGURE_STYLE), page.px(PAIR_LABEL_STYLE)
    fw = page.length(spec["figure"], fsize)
    lw = page.length(spec["label"], lsize)
    side_w = max(fw, lw)
    if side_w > (right - tx) * 0.62:
        raise _NoFit(f"figure: {spec['figure'][:20]!r} is {fw:.0f}px wide beside the name")
    name = page.measure(spec["right"]["name"], page.px(PAIR_NAME_STYLE), right - tx - side_w - CELL_GAP,
                        "right.name", leading=1.15, max_lines=_cap(page, 3))
    lab_asc, lab_desc = page.font(lsize).getmetrics()
    f_l, f_t, f_r, f_b = page.font(fsize).getbbox(spec["figure"], anchor="ls")
    side_h = lab_asc + lab_desc + _gap(page, 4) + (f_b - f_t)
    head_h = max(2 * rr, name.height, side_h)
    lines = list(spec.get("lines") or ())
    lt = [page.measure(ln, page.px(PAIR_LINE_STYLE), x1 - 12 - (tx - 16) - 32, f"lines[{i}]", leading=1.25)
          for i, ln in enumerate(lines)]
    strip_h = (2 * _gap(page, 12) + sum(t.height for t in lt) + _gap(page, 6) * (len(lt) - 1)) if lt else 0
    h = pad + head_h + (_gap(page, 12) + strip_h if lt else 0) + pad
    box = look.card(page, (x0, c_top, x1, c_top + h))
    page.disc(x0 + 22 + rr, c_top + pad + head_h / 2, rr, _side_art(spec["right"], logos, "right"))
    page.put("right.name", name, tx, c_top + pad + (head_h - name.height) / 2, "la", look.INK, box)
    sy = c_top + pad + (head_h - side_h) / 2
    page.line("label", spec["label"], lsize, right, sy + lab_asc, "rs", look.INK2, box)
    page.line("figure", spec["figure"], fsize, right, sy + lab_asc + lab_desc + _gap(page, 4) - f_t, "rs",
              look.GOLD_INK, box)
    if lt:
        s_top = c_top + pad + head_h + _gap(page, 12)
        sbox = page.rect((tx - 16, s_top, x1 - 12, s_top + strip_h), 16, look.TILE)
        ty = s_top + _gap(page, 12)
        for i, t in enumerate(lt):
            page.put(f"lines[{i}]", t, tx, ty, "la", look.INK2, sbox)
            ty += t.height + _gap(page, 6)
    end = c_top + h
    if end > bottom:
        raise _NoFit(f"pair: the card ends at {end}px, room to {bottom}px")
    spare = bottom - end
    if spare > 0:
        page.shift(mark, spare // 2)


_SHARE = re.compile(r"[0-9][0-9.,]*%\Z")


# ── the weekly theme post (news-v23 phase 3: theme_ai_chips.jpg, theme_biggest_power.jpg, theme_family.py) ─────────

#: The theme frame's sizes at ramp step 0 (theme_family.py / mock_theme.py / mock_theme_biggest.py): the sub line(s)
#: (SUB, 28 px, pitch 35), a tile's name (26) and line (23, its share GOLD_INK), the kit's 22 px ticker chip (its
#: height = its size + 16), a tile's logo disc (r 44) and card radius (22), the grid's gaps (14 across, 10 down), the
#: "+N more" pill (GOLD on PILL, 26 px), the ranked row's rank disc (r 17), name (27) and value (40) and its bar (10).
TH_SUB = _Style(28, 22, 1.25)
TH_NAME = _Style(26, 22, 1.15)
TH_LINE = _Style(23, 22, 1.15)
TH_CHIP_PX = 22
TH_DISC = _Style(44, 30, 1.0)
TH_TILE_RADIUS = 22
TH_GAP_X, TH_GAP_Y = 14, 10
TH_PAD = 14
TH_MORE_PX = _Style(26, 22, 1.0)
TH_RANK_R = 17
TH_RANK_PX = 22
TH_RANK_NAME = _Style(27, 22, 1.0)
TH_VALUE = _Style(40, 28, 1.0)
TH_BAR_H = 10
TH_ROW_GAP = 8
#: An icon keeps this far from any text (theme_family.ART_CLEAR); beside the title its box ends at TH_ICON_RIGHT and is
#: centred TH_ICON_TITLE_DY under the title's top (the chip at (914, 170), the wafer at 174, the shield's middle at 170).
TH_ART_CLEAR = 14
TH_ICON_RIGHT = 1013
TH_ICON_TITLE_DY = 30
#: The picture's bottom gap above the footer for the theme layouts (mock_theme: the grid ends 16 px over the footer —
#: with "+N more", 10 + the pill + 16; mock_theme_biggest: the pill 18 px over it) — `_place`.
TH_BOTTOM_GAP = {"grid": 16, "ranked": 18}
#: The Congress angle's sub-line pill: its word's padding each side (theme_congress_defense.jpg: 12).
TH_PILL_PAD = 12
#: A tile's line ends in a share of revenue: "<segment> · 88% of revenue" — the share GOLD_INK, the rest INK2.
_TH_SHARE_TAIL = re.compile(r"((?:less than |more than )?[0-9][0-9.,]*%)( of revenue)\Z")


def _is_theme(image: TemplateImage) -> bool:
    return image.series == "theme_explainer"


def _theme_chip(page: look.Page, field: str, text: str, x: float, y: float, bound: Box) -> Box:
    """The kit's navy ticker chip (style_kit.chip: TH_CHIP_PX text, 12 px each side, its height the size + 16) with its
    top-left at (x, y)."""
    size = TH_CHIP_PX
    page.legible(size, field)
    w = int(math.ceil(page.length(text, size))) + 24
    box = page.rect((x, y, x + w, y + size + 16), look.CHIP_RADIUS, look.NAVY_CHIP)
    page.line(field, text, size, box[0] + 12, (box[1] + box[3]) / 2, "lm", look.WHITE, box)
    if not _within(box, bound):
        raise _NoFit(f"{field}: the chip leaves its card")
    return box


def _th_icon(image: TemplateImage, kind_size: int, cx: float, cy: float) -> Optional[theme_art.Icon]:
    kind = image.spec.get("icon")
    if kind not in cards.THEME_ICONS:
        return None
    return theme_art.Icon(kind, cx, cy, kind_size)


def _clear(box: Box, inks: Sequence[Box]) -> bool:
    c = TH_ART_CLEAR
    return all(box[0] >= i[2] + c or box[2] <= i[0] - c or box[1] >= i[3] + c or box[3] <= i[1] - c for i in inks)


#: The separator a theme sub line's phrases are joined with ("21 companies · each company's largest business …"):
#: a sub line that needs two or three lines breaks only after one (`_th_sub_lines`) when the phrases fit.
SUB_SEPARATOR = "·"


def _th_sub_lines(page: look.Page, text: str, brk: Any, size: int, room: float, *,
                  balance: bool = False) -> Optional[List[str]]:
    """A theme sub line's lines in `room` at `size` (visual-fidelity round 2026-11-20: a theme video cover broke it
    mid-phrase, "… largest business and its" / "share of total revenue …"), the first that fits: the hand-set break
    `brk` (a word index — the picture's `sub_break`) as two lines; one line; broken only after a SUB_SEPARATOR (the
    separator ends its line; the narrowest two- or three-line split, `look._min_max_split`); else word-wrapped to at
    most three lines (`balance`: balanced). None when nothing fits three lines."""
    words = str(text).split()
    font = page.font(size)
    if isinstance(brk, int) and not isinstance(brk, bool) and 0 < brk < len(words):
        pair = [" ".join(words[:brk]), " ".join(words[brk:])]
        if all(font.getlength(ln) <= room for ln in pair):
            return pair
    joined = " ".join(words)
    if font.getlength(joined) <= room:
        return [joined]
    units: List[str] = []
    cur: List[str] = []
    for w in words:
        cur.append(w)
        if w == SUB_SEPARATOR and len(cur) > 1:
            units.append(" ".join(cur))
            cur = []
    if cur:
        units.append(" ".join(cur))
    for n in (2, 3):
        split = look._min_max_split(units, font, n) if len(units) >= n else None
        if split is not None and all(font.getlength(ln) <= room for ln in split):
            return split
    try:
        return list(page.measure(joined, size, room, "subtitle", leading=TH_SUB.leading, max_lines=3,
                                 balance=balance).lines)
    except _NoFit:
        return None


def _theme_top(page: look.Page, image: TemplateImage, top: int, pill: Optional[str] = None,
               balance: bool = False) -> int:
    """The theme frame's top block (theme_family.Frame.top): the two-tone title (the HEADLINE ladder; one line when it
    fits), the sub line — two lines broken where the spec says (`sub_break`, mock_theme's hand-set break; a cover's comes
    from its picture, `TemplateImage.sub_break`) when each fits, else one line, else broken only after a " · ", else
    word-wrapped (`_th_sub_lines`) — and the quiet decoration (`icon`) top-right: beside the title when the title leaves it room,
    else beside the sub line(s) when they do, else none (never over text). Returns where the content may start (under
    the sub block, and under the decoration)."""
    spec = image.spec
    _x0, _x1, tx0, tx1 = _columns(page)
    marks = len(page.lines)
    y = look.headline(page, "title", spec["title"], HEADLINE_STYLE, tx0, top, tx1 - tx0, max_lines=3, tone="last")
    title_inks = [ln.ink for ln in page.lines[marks:]]
    icon: Optional[theme_art.Icon] = None
    kind = spec.get("icon")
    on_page = page.frame is POST_IMAGE_FRAME
    if on_page and kind in cards.THEME_ICONS:
        size = theme_art.SIZES[kind][0]
        ex = theme_art.extent(kind, size)
        # centred TH_ICON_TITLE_DY under the title's top, but never within TH_ART_CLEAR of the brand row
        row_bottom = page.frame.brand[1] + page.frame.brand_px
        trial = _th_icon(image, size, TH_ICON_RIGHT - ex[2], max(top + TH_ICON_TITLE_DY, row_bottom + TH_ART_CLEAR - ex[1]))
        if trial is not None and _clear(trial.box, title_inks):
            icon = trial
    size_px = page.px(TH_SUB)
    page.legible(size_px, "subtitle")
    pitch = int(size_px * TH_SUB.leading)
    words = str(spec["subtitle"]).split()
    brk = spec.get("sub_break", image.sub_break)
    width = tx1 - tx0
    lines: Optional[List[str]] = None
    side: Optional[theme_art.Icon] = None
    if on_page and icon is None and kind in cards.THEME_ICONS:
        size = theme_art.SIZES[kind][1]
        ex = theme_art.extent(kind, size)
        n_sub = len(_th_sub_lines(page, spec["subtitle"], brk, size_px, width) or (1, 1, 1))
        cy = y + (n_sub * pitch) / 2 + 3
        cy = max(cy, (max(i[3] for i in title_inks) if title_inks else y) + TH_ART_CLEAR - ex[1])
        side = _th_icon(image, size, TH_ICON_RIGHT - ex[2], cy)
        if side is not None:
            width = int(side.box[0] - TH_ART_CLEAR - tx0)
    # news-v23 phase 3, the Congress angle (theme_congress_defense.jpg): the sub line's LAST word `pill` sits on a solid
    # GREEN pill (WHITE ink, TH_PILL_PAD each side), so its line must leave that padding
    extra = 2 * TH_PILL_PAD if pill is not None and words and words[-1] == pill else 0
    for room, keep in ((width - extra, side), (tx1 - tx0 - extra, None)):
        got = _th_sub_lines(page, spec["subtitle"], brk, size_px, room, balance=balance)
        if got is not None:
            lines, side = got, keep
            break
    if lines is None:
        raise _NoFit("subtitle: does not fit three lines")
    for i, ln in enumerate(lines):
        parts = ln.split(" ")
        if extra and i == len(lines) - 1 and parts[-1] == pill:
            lead = " ".join(parts[:-1])
            px = tx0
            if lead:
                page.line("subtitle", lead, size_px, tx0, y + i * pitch, "la", look.SUB, page.frame.area)
                px = tx0 + page.length(lead + " ", size_px)
            pw = page.length(pill, size_px) + extra
            ytop = y + i * pitch
            box = page.rect((px, ytop - 3, px + pw, ytop + size_px + 9), 9, look.GREEN)
            page.line("subtitle", pill, size_px, px + TH_PILL_PAD, ytop, "la", look.WHITE, box)
            continue
        page.line("subtitle", ln, size_px, tx0, y + i * pitch, "la", look.SUB, page.frame.area)
    end = y + len(lines) * pitch + _gap(page, 8, 4)
    icon = icon or side
    if icon is not None:
        if not _clear(icon.box, [ln.ink for ln in page.lines[marks:]]):
            icon = None
        else:
            end = max(end, int(icon.box[3]) + 12 - _gap(page, 8, 4))
    page.icon = icon
    return end + _gap(page, 8, 4)


def _th_more(page: look.Page, image: TemplateImage, bottom: int) -> int:
    """The "+N more" pill (theme_family.more_pill: GOLD on PILL, TH_MORE_PX, 18 px each side), resting on `bottom`.
    Returns where the cards above it must end (its top less 10 / 12), or `bottom` when there is none."""
    more = image.spec.get("more")
    if more is None:
        return bottom
    size = page.px(TH_MORE_PX)
    page.legible(size, "more")
    w = page.length(more, size)
    h = size + 18
    cx = page.frame.canvas[0] / 2
    box = page.rect((cx - w / 2 - 18, bottom - h, cx + w / 2 + 18, bottom), 12, look.PILL)
    page.line("more", more, size, box[0] + 18, (box[1] + box[3]) / 2, "lm", look.GOLD, box)
    return int(box[1]) - (10 if image.layout == "grid" else 12)


def _th_line_runs(line: str) -> Tuple[str, Optional[str], Optional[str]]:
    """A tile's line as (head, share, tail): "Data Center · 88% of revenue" → ("Data Center", "88%", " of revenue");
    a line without a share → (line, None, None)."""
    head, sep, tail = line.rpartition(" · ")
    m = _TH_SHARE_TAIL.fullmatch(tail) if sep else None
    if m is None:
        return line, None, None
    return head, m.group(1), m.group(2)


def _th_fact_plan(page: look.Page, line: Optional[str], size: int, width: float, field: str
                  ) -> List[List[Tuple[str, look.RGB]]]:
    """The tile line's drawn lines as coloured runs (mock_theme.fact_lines): ONE line "<head> · 88% of revenue" when it
    fits, else the head word-wrapped, then "88% of revenue" — the "·" is the break; a line without a share wrapped."""
    if not line:
        return []
    head, share, tail = _th_line_runs(line)
    if share is None:
        return [[(ln, look.INK2)] for ln in page.measure(line, size, width, field, max_lines=3).lines]
    one = [(head + " · ", look.INK2), (share, look.GOLD_INK), (tail or "", look.INK2)]
    if page.length("".join(r[0] for r in one), size) <= width:
        return [one]
    return ([[(ln, look.INK2)] for ln in page.measure(head, size, width, field, max_lines=3).lines]
            + [[(share, look.GOLD_INK), (tail or "", look.INK2)]])


def _lay_grid(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """Angle 1 of the weekly theme post, "how they make money" (theme_ai_chips.jpg, mock_theme.render): the theme frame's
    top block (`_theme_top`), then two columns of warm tiles of ONE height filling the room to the "+N more" pill — each
    the logo disc (a wordmark draws no disc), the ticker chip over the name (one common size, a long name on two lines),
    and the line "<segment> · 88% of revenue" (the share GOLD_INK; "<segment>" over "88% of revenue" when one line does
    not fit) — every tile's text block centred on its tile. A spec without chips (an older web) draws the same tiles
    without them. Steps down the ramp when a tile's text does not fit its tile."""
    spec, logos = image.spec, image.logos
    x0, x1, _tx0, _tx1 = _columns(page)
    y = _theme_top(page, image, top)
    grid_bot = _th_more(page, image, bottom)
    tiles = list(spec["tiles"])
    rows = (len(tiles) + 1) // 2
    gap_y = _gap(page, TH_GAP_Y, 6)
    tile_w = (x1 - x0 - TH_GAP_X) / 2
    tile_h = (grid_bot - y - (rows - 1) * gap_y) / rows
    r = page.px(TH_DISC)
    if 2 * r > tile_h - 24:
        r = int((tile_h - 24) // 2)
    if r < TH_DISC.floor:
        raise _NoFit(f"grid: tiles of {tile_h:.0f}px leave no room for the logo disc")
    text_x = TH_PAD + 2 * page.px(TH_DISC) + TH_PAD
    avail = tile_w - text_x - TH_PAD
    # one name size for every tile (mock_theme.common_size: the largest that fits each name on ONE line, never under
    # the floor); a name that still does not fit wraps to two lines at that size
    n_px = page.px(TH_NAME)
    while n_px > TH_NAME.floor and any(page.length(t["name"], n_px) > avail for t in tiles):
        n_px -= 1
    page.legible(n_px, "tiles[0].name")
    # one line size: the largest (from the step's) that wraps no more tiles than the floor does (pick_line_size)
    l_px = page.px(TH_LINE)

    def wraps(size: int) -> int:
        return sum(len(_th_fact_plan(page, t.get("line"), size, avail, f"tiles[{i}].line")) > 1
                   for i, t in enumerate(tiles))

    floor_wraps = wraps(TH_LINE.floor)
    while l_px > TH_LINE.floor and wraps(l_px) > floor_wraps:
        l_px -= 1
    page.legible(l_px, "tiles[0].line")
    n_pitch, l_pitch = int(round(n_px * 1.15)), int(round(l_px * 1.15))
    n_cap = -page.font(n_px).getbbox("H", anchor="ls")[1]
    l_cap = -page.font(l_px).getbbox("H", anchor="ls")[1]
    l_desc = page.font(l_px).getmetrics()[1]
    for i, tile in enumerate(tiles):
        row, col = divmod(i, 2)
        bx0 = x0 + col * (tile_w + TH_GAP_X)
        by0 = y + row * (tile_h + gap_y)
        box = look.card(page, (bx0, by0, bx0 + tile_w, by0 + tile_h), radius=TH_TILE_RADIUS)
        cy = (by0 + by0 + tile_h) / 2
        art = logos[tile["logo"]]
        # A wordmark (missing or unverified logo) draws its disc too when the tile carries its ticker chip (final
        # review of 2026-11-26: the empty slot left a ~100 px gutter on 11 of FINAL_BUILD's 12 tiles): the name when it
        # fits the disc readably, else the ticker — the tile's own chip, already drawn and allowed (`look.Page.disc`).
        # A tile without a chip (an older web) keeps the old rule: no disc, the slot keeps the column.
        if not art.wordmark or tile.get("chip") is not None:
            page.disc(bx0 + TH_PAD + r, cy, r, art, ticker=tile.get("chip"))
        tx = bx0 + text_x
        path = f"tiles[{i}]"
        names = page.measure(tile["name"], n_px, avail, f"{path}.name", max_lines=cards.THEME_NAME_MAX_LINES).lines
        facts = _th_fact_plan(page, tile.get("line"), l_px, avail, f"{path}.line")
        has_chip = tile.get("chip") is not None
        if has_chip:
            _grid_tile_with_chip(page, tile, path, names, facts, n_px, l_px, tx, cy, box, avail)
            continue
        # no chip (an older web): the name's lines and the line(s), centred from the cap top to the last line's foot
        block = (len(names) - 1) * n_pitch + n_cap + (
            (len(facts) - 1) * l_pitch + (l_pitch - l_cap) + l_cap + l_desc if facts else 6)
        if block > tile_h - 18:
            raise _NoFit(f"{path}: its text ({int(block)}px) does not fit its tile ({tile_h:.0f}px)")
        base = cy - block / 2 + n_cap + (n_pitch - n_px) / 2
        for k, ln in enumerate(names):
            page.line(f"{path}.name", ln, n_px, tx, base + k * n_pitch, "ls", look.INK, box)
        base += (len(names) - 1) * n_pitch + (l_pitch - l_cap) + l_cap
        for k, runs in enumerate(facts):
            xx = tx
            for text, colour in runs:
                if text:
                    page.line(f"{path}.line", text, l_px, xx, base + k * l_pitch, "ls", colour, box)
                    xx += page.length(text, l_px)
            if xx - tx > avail + 0.5:
                raise _NoFit(f"{path}.line: wider than its tile")


#: A grid tile's text keeps this much air inside its tile, ink included (mock_theme.tile asserts 9 px both ways).
TH_TILE_AIR = 9


def _grid_tile_with_chip(page: look.Page, tile: Mapping[str, Any], path: str, names: Sequence[str],
                         facts: Sequence[Sequence[Tuple[str, Any]]], n_px: int, l_px: int, tx: float, cy: float,
                         box: Box, avail: float) -> None:
    """A grid tile's text under its ticker chip, as mock_theme.tile_layout draws it ("la": each line hangs from its
    ascender line) — the chip, the name 2 px under it (its cap top ~8 px under the chip: the worker's cap-top rule left
    4-5 px, visual-fidelity round 2026-11-20), the fact line round(name × 1.15) under the name's line, the next one a
    pitch further — the block centred from the chip's top to its last BASELINE + 2 (descenders dip into the lower
    margin) and kept TH_TILE_AIR inside the tile, its last line's ink included (_NoFit otherwise: the page steps)."""
    n_font, l_font = page.font(n_px), page.font(l_px)
    n_asc, l_asc = n_font.getmetrics()[0], l_font.getmetrics()[0]
    n_pitch, l_pitch = int(round(n_px * 1.15)), int(round(l_px * 1.15))
    chip_h = TH_CHIP_PX + 16
    # the lines' draw tops ("la"), from the chip's top
    name_tops = [chip_h + 2 + k * n_pitch for k in range(len(names))]
    fact_tops = [name_tops[-1] + n_pitch + k * l_pitch for k in range(len(facts))]
    if facts:
        last_base = fact_tops[-1] + l_asc
        last_ink = max(l_font.getbbox(text, anchor="ls")[3] for text, _c in facts[-1] if text)
    else:
        last_base = name_tops[-1] + n_asc
        last_ink = n_font.getbbox(names[-1], anchor="ls")[3]
    top = cy - (last_base + 2) / 2
    if top < box[1] + TH_TILE_AIR or top + last_base + last_ink > box[3] - TH_TILE_AIR:
        raise _NoFit(f"{path}: its text ({int(last_base + last_ink)}px) does not fit its tile "
                     f"({box[3] - box[1]}px) with {TH_TILE_AIR}px of air")
    _theme_chip(page, f"{path}.chip", tile["chip"], tx, top, box)
    for k, ln in enumerate(names):
        page.line(f"{path}.name", ln, n_px, tx, top + name_tops[k] + n_asc, "ls", look.INK, box)
    for k, runs in enumerate(facts):
        xx = tx
        for text, colour in runs:
            if text:
                page.line(f"{path}.line", text, l_px, xx, top + fact_tops[k] + l_asc, "ls", colour, box)
                xx += page.length(text, l_px)
        if xx - tx > avail + 0.5:
            raise _NoFit(f"{path}.line: wider than its tile")


def _lay_ranked(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """Angle 2 of the weekly theme post, "who's biggest" by market value (theme_biggest_power.jpg, mock_theme_biggest):
    the theme frame's top block, then one full-width card per row, the rows sharing the room to the "+N more" pill —
    the rank on a GOLD disc, the logo disc, the ticker chip and the name on one line (names on one column), a thin bar
    in proportion to the largest value under them (BAR_FILL on BAR_TRACK; the spec's `ratio`) and the value
    right-aligned, big, INK. Never a price, a % move or a colour verdict."""
    spec, logos = image.spec, image.logos
    x0, x1, _tx0, _tx1 = _columns(page)
    list_top = _theme_top(page, image, top)
    list_bot = _th_more(page, image, bottom)
    rows = list(spec["rows"])
    n = len(rows)
    gap = _gap(page, TH_ROW_GAP, 4)
    row_h = (list_bot - list_top - (n - 1) * gap) / n
    chip_h = TH_CHIP_PX + 16
    bar_h = TH_BAR_H
    block_h = chip_h + 10 + bar_h
    if row_h < block_h + 2 * 8:
        raise _NoFit(f"ranked: rows of {row_h:.0f}px are shorter than their block")
    rank_cx = x0 + 18 + TH_RANK_R
    logo_r = int(min(31, (row_h - 16) / 2))
    logo_cx = rank_cx + TH_RANK_R + 12 + logo_r
    chip_x = logo_cx + logo_r + 16
    chip_ws = [int(math.ceil(page.length(r["chip"], TH_CHIP_PX))) + 24 for r in rows if r.get("chip") is not None]
    name_x = chip_x + (max(chip_ws) + 12 if chip_ws else 0)
    v_px = page.px(TH_VALUE)
    val_right = x1 - 26
    val_w = max(page.length(r["value"], v_px) for r in rows)
    bar_x1 = val_right - val_w - 28
    name_w = bar_x1 - name_x - 8
    n_px = page.px(TH_RANK_NAME)
    while n_px > TH_RANK_NAME.floor and any(page.length(r["name"], n_px) > name_w for r in rows):
        n_px -= 1
    page.legible(n_px, "rows[0].name")
    page.legible(v_px, "rows[0].value")
    for i, row in enumerate(rows):
        path = f"rows[{i}]"
        if page.length(row["name"], n_px) > name_w:
            raise _NoFit(f"{path}.name: does not fit its {int(name_w)}px column")
        ry = list_top + i * (row_h + gap)
        box = look.card(page, (x0, ry, x1, ry + row_h))
        cy = ry + row_h / 2
        page.ellipse((rank_cx - TH_RANK_R, cy - TH_RANK_R, rank_cx + TH_RANK_R, cy + TH_RANK_R), look.GOLD)
        page.legible(TH_RANK_PX, f"{path}.rank")
        page.line(f"{path}.rank", row["rank"], TH_RANK_PX, rank_cx, cy + 1, "mm", look.INK, box)
        page.disc(logo_cx, cy, logo_r, logos[row["logo"]], ticker=row.get("chip"))   # a missing logo: its ticker
        btop = cy - block_h / 2
        if row.get("chip") is not None:
            _theme_chip(page, f"{path}.chip", row["chip"], chip_x, btop, box)
        page.line(f"{path}.name", row["name"], n_px, name_x, btop + chip_h / 2, "lm", look.INK, box)
        by0 = btop + chip_h + 10
        page.rect((chip_x, by0, bar_x1, by0 + bar_h), bar_h // 2, look.BAR_TRACK)
        ratio = float(row["ratio"])
        page.rect((chip_x, by0, chip_x + max(bar_h, (bar_x1 - chip_x) * ratio), by0 + bar_h), bar_h // 2,
                  look.BAR_FILL)
        page.line(f"{path}.value", row["value"], v_px, val_right, cy, "rm", look.INK, box)


# ── the theme's last three angles (news-v23 phase 3, 2026-11-20: theme_growth_profit_silicon.jpg,
#    theme_insiders_cyber.jpg, theme_congress_defense.jpg — mock_theme_growth_profit / _insiders / _congress) ────────

#: The tables' sizes at ramp step 0 (the mocks'): GREY column labels (LABEL 22, pitch 1.22); `figures` — a row's name
#: (26), its two figures (30), the logo disc (r 26), the growth bar (146 × 12, never under TH_FIG_BAR_MIN: the name
#: column takes its room first), the profit tag (150 × 46, radius 14); `ledger` — the column-head pills (24, pitch
#: 1.15, padded 18 a side, 9 top and bottom, radius 12), a name (28), an amount (28) on its tag (radius 10), the disc
#: (r 28); `tally` — the disc (r 46), the count (64, GREEN), "members" (24, INK2), the name (40 → 26, then two lines
#: at the floor) under the chip, the card (up to TH_TALLY_CARD_CAP, gap 16).
TH_LABEL = _Style(22, 22, 1.22)
TH_FIG_NAME = _Style(26, 22, 1.0)
TH_FIG_VALUE = _Style(30, 24, 1.0)
TH_FIG_DISC = 26
TH_FIG_BAR_W, TH_FIG_BAR_MIN, TH_FIG_BAR_H = 146, 72, 12
TH_FIG_TAG_W, TH_FIG_TAG_H = 150, 46
TH_LEDGER_HEAD = _Style(24, 22, 1.15)
TH_LEDGER_NAME = _Style(28, 22, 1.0)
TH_LEDGER_TAG = _Style(28, 22, 1.0)
TH_LEDGER_DISC = 28
TH_TALLY_DISC = 46
TH_TALLY_COUNT = _Style(64, 36, 1.0)
TH_TALLY_UNIT = _Style(24, 22, 1.0)
TH_TALLY_NAME = _Style(40, 22, 1.12)
TH_TALLY_NAME_ONE_LINE_FLOOR = 26
TH_TALLY_CARD_CAP = 200
#: The gap between the count's ink and the unit's ink ("6" / "members"; mock_theme_congress `gap = 12`).
TH_TALLY_INK_GAP = 12
#: A theme table's note: its own panel (theme_family.notes_panel: 26 px SUB after a gold dot, padded 30 / 22).
TH_NOTE = _Style(26, 22, 1.32)


def _sign_tone(value: str) -> Tuple[look.RGB, look.RGB]:
    """(ink, tag tint) of a signed theme figure: RED on RED_TINT when it starts with MINUS (U+2212), else GREEN on
    GREEN_TINT (owner 2026-11-19: green when ≥ 0, red when negative)."""
    if value.startswith(cards.MINUS):
        return look.RED, look.RED_TINT
    return look.GREEN, look.GREEN_TINT


def _th_note(page: look.Page, field: str, text: str, x0: int, x1: int, y: float) -> int:
    """A theme table's note on its lifted dark panel (theme_family.Frame.notes_panel), top at `y`. Returns its bottom."""
    pad_x, pad_y, dot_gap = 30, _gap(page, 22, 14), 30
    size = page.px(TH_NOTE)
    t = page.measure(text, size, x1 - x0 - 2 * pad_x - dot_gap, field, leading=TH_NOTE.leading, max_lines=2,
                     balance=True)
    h = 2 * pad_y + t.height
    box = page.rect((x0, y, x1, y + h), 20, look.NOTE_PANEL)
    asc = page.font(size).getmetrics()[0]
    cy = y + pad_y + asc * 0.62
    page.ellipse((x0 + pad_x, cy - 5, x0 + pad_x + 10, cy + 5), look.GOLD)
    page.put(field, t, x0 + pad_x + dot_gap, y + pad_y, "la", look.SUB, box)
    return int(y + h)


def _th_table_bottom(page: look.Page, image: TemplateImage, bottom: int) -> int:
    """The tables' bottom stack (theme_family.Frame.bottom), resting on `bottom` (TH_BOTTOM_GAP over the footer): the
    note's panel, the "+N more" pill 12 px over it, and where the cards above must end (12 px over the pill, or 26 over
    the panel; 18 + 8 over `bottom` with neither). Returns that y."""
    spec = image.spec
    x0, x1, _tx0, _tx1 = _columns(page)
    y = bottom
    note = spec.get("note")
    if note is not None:
        mark = page.mark()
        h = _th_note(page, "note", note, x0, x1, 0) - 0
        page.rollback(mark)
        y = bottom - h
        _th_note(page, "note", note, x0, x1, y)
    more = spec.get("more")
    if more is None:
        return int(y - (_gap(page, 26, 14) if note is not None else 26))
    size = page.px(TH_MORE_PX)
    page.legible(size, "more")
    w = page.length(more, size)
    h = size + 18
    cx = page.frame.canvas[0] / 2
    pill_bot = y - (_gap(page, 12, 8) if note is not None else 18)
    box = page.rect((cx - w / 2 - 18, pill_bot - h, cx + w / 2 + 18, pill_bot), 12, look.PILL)
    page.line("more", more, size, box[0] + 18, (box[1] + box[3]) / 2, "lm", look.GOLD, box)
    return int(box[1]) - _gap(page, 12, 8)


def _th_name_column(page: look.Page, rows: Sequence[Mapping[str, Any]], chip_x: float) -> float:
    """Where a table's names start: after the widest row chip (the kit's chip: its text + 24) and 14 px
    (theme_family.name_column) — at chip_x when no row has a chip."""
    widths = [page.length(r["chip"], TH_CHIP_PX) + 24 for r in rows if r.get("chip") is not None]
    return chip_x + (max(widths) + 14 if widths else 0)


def _th_labels(page: look.Page, field: str, lines: Sequence[str], x: float, anchor: str, bottom: float,
               size: int) -> None:
    """GREY column labels (theme_family.Frame.labels), bottom-aligned on `bottom`: right-aligned on `x` ("ra") or
    centred on it ("ma")."""
    pitch = int(size * TH_LABEL.leading)
    y = bottom - len(lines) * pitch
    for k, line in enumerate(lines):
        page.line(f"{field}[{k}]", line, size, x, y + k * pitch, anchor, look.GREY, page.frame.area)


def _th_wrap_names(page: look.Page, rows: Sequence[Mapping[str, Any]], size: int, width: float
                   ) -> List[Tuple[str, ...]]:
    """Each row's name as its drawn lines at `size` in `width`: one line when it fits, else two balanced lines — the
    server's bound (`company_news_rules.theme_table_name_fits`: at most THEME_TABLE_NAME_LINES lines of the floor in
    the table's narrowest column). _NoFit when a name needs more."""
    out: List[Tuple[str, ...]] = []
    for i, row in enumerate(rows):
        name = row["name"]
        if page.length(name, size) <= width:
            out.append((name,))
            continue
        out.append(tuple(page.measure(name, size, width, f"rows[{i}].name", max_lines=2, balance=True).lines))
    return out


def _th_name_block(page: look.Page, field: str, lines: Sequence[str], size: int, x: float, cy: float,
                   box: Box) -> None:
    """A table row's name centred on `cy`: one line ("lm"), or two lines around it (INK)."""
    if len(lines) == 1:
        page.line(field, lines[0], size, x, cy, "lm", look.INK, box)
        return
    pitch = int(round(size * 1.12))
    cap = -page.font(size).getbbox("H", anchor="ls")[1]
    first = cy - ((len(lines) - 1) * pitch + cap) / 2 + cap
    for k, ln in enumerate(lines):
        page.line(field, ln, size, x, first + k * pitch, "ls", look.INK, box)


def _lay_figures(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """Angle 3 of the weekly theme post, "growth + profit" (theme_growth_profit_silicon.jpg, mock_theme_growth_profit):
    the theme frame's top block, the GREY column labels ("Revenue growth" right-aligned on the growth figures, "Profit
    per $100" / "of revenue" centred on the tags), one warm card per row filling the room — the logo disc, the ticker
    chip, the name (one size for every row, one line), a bar in proportion to the largest growth shown (GREEN, or RED
    for a decline — the spec's `ratio`), the growth figure and the profit per $100 on its tinted tag, each GREEN or RED
    by its sign — then "+N more", the note and the footer. The names take the bar's room before the page steps."""
    spec, logos = image.spec, image.logos
    x0, x1, _tx0, _tx1 = _columns(page)
    y = _theme_top(page, image, top, balance=True)
    cards_bot = _th_table_bottom(page, image, bottom)
    rows = list(spec["rows"])
    n = len(rows)
    lab = page.px(TH_LABEL)
    page.legible(lab, "heads")
    growth_head, profit_head = spec["heads"]
    cards_top = y + 6 + max(len(growth_head), len(profit_head)) * int(lab * TH_LABEL.leading) + 12
    gap = _gap(page, 6, 4)
    row_h = (cards_bot - cards_top - (n - 1) * gap) / n
    r = int(min(TH_FIG_DISC, (row_h - 12) / 2))
    if r < 14:
        raise _NoFit(f"figures: rows of {row_h:.0f}px leave no room for the logo disc")
    pad = 16
    lcx = x0 + pad + r
    chip_x = lcx + r + 16
    name_x = _th_name_column(page, rows, chip_x)
    v = page.px(TH_FIG_VALUE)
    page.legible(v, "rows[0].growth")
    tag_w = _gap(page, TH_FIG_TAG_W, 128)
    tag_x1 = x1 - 22
    tag_x0 = tag_x1 - tag_w
    tag_cx = (tag_x0 + tag_x1) / 2
    p_left = tag_cx - max(page.length(s, lab) for s in profit_head) / 2
    gfig_x1 = p_left - _gap(page, 40, 24)
    gfig_w = max(page.length(row["growth"], v) for row in rows)
    bar_x1 = gfig_x1 - gfig_w - 16
    n_px = page.px(TH_FIG_NAME)

    def need(size: int) -> float:
        return max(page.length(row["name"], size) for row in rows)

    bar_w = _gap(page, TH_FIG_BAR_W, 110)
    while n_px > TH_FIG_NAME.floor and name_x + need(n_px) + 30 > bar_x1 - bar_w:
        n_px -= 1
    if name_x + need(n_px) + 30 > bar_x1 - bar_w:
        # the names take the bar's room first (down to TH_FIG_BAR_MIN), then a long one wraps to two lines
        bar_w = max(TH_FIG_BAR_MIN, int(bar_x1 - 30 - name_x - need(n_px)))
    page.legible(n_px, "rows[0].name")
    bar_x0 = bar_x1 - bar_w
    names = _th_wrap_names(page, rows, n_px, bar_x0 - 30 - name_x)
    _th_labels(page, "heads[0]", growth_head, gfig_x1, "ra", cards_top - 12, lab)
    _th_labels(page, "heads[1]", profit_head, tag_cx, "ma", cards_top - 12, lab)
    tag_h = min(TH_FIG_TAG_H, int(row_h - 12))
    if tag_h < v + 8:
        raise _NoFit(f"figures: rows of {row_h:.0f}px leave no room for the profit tag")
    two = int(round(n_px * 1.12)) - page.font(n_px).getbbox("H", anchor="ls")[1]
    if any(len(nl_) > 1 for nl_ in names) and two + 12 > row_h:
        raise _NoFit(f"figures: a two-line name ({two}px) does not fit rows of {row_h:.0f}px")
    bh = TH_FIG_BAR_H
    for i, row in enumerate(rows):
        path = f"rows[{i}]"
        ry = cards_top + i * (row_h + gap)
        box = look.card(page, (x0, ry, x1, ry + row_h))
        cy = ry + row_h / 2
        page.disc(lcx, cy, r, logos[row["logo"]], ticker=row.get("chip"))       # a missing logo: its ticker
        if row.get("chip") is not None:
            _theme_chip(page, f"{path}.chip", row["chip"], chip_x, cy - (TH_CHIP_PX + 16) / 2, box)
        _th_name_block(page, f"{path}.name", names[i], n_px, name_x, cy, box)
        ink, tint = _sign_tone(row["growth"])
        page.rect((bar_x0, cy - bh / 2, bar_x1, cy + bh / 2), bh // 2, look.BAR_TRACK)
        fill_w = max(bh, (bar_x1 - bar_x0) * float(row["ratio"]))
        page.rect((bar_x0, cy - bh / 2, bar_x0 + fill_w, cy + bh / 2), bh // 2, ink)
        page.line(f"{path}.growth", row["growth"], v, gfig_x1, cy, "rm", ink, box)
        p_ink, p_tint = _sign_tone(row["profit"])
        tag = page.rect((tag_x0, cy - tag_h / 2, tag_x1, cy + tag_h / 2), 14, p_tint)
        if page.length(row["profit"], v) > tag_w - 24:
            raise _NoFit(f"{path}.profit: wider than its tag")
        page.line(f"{path}.profit", row["profit"], v, tag_cx, cy, "mm", p_ink, tag)


def _lay_ledger(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """Angle 4 of the weekly theme post, the "insider scorecard" (theme_insiders_cyber.jpg, mock_theme_insiders; owner
    2026-11-19: "remove 3 companies and 9 companies cards. Make the Disclosed Purchase and Disclosed sales more clearly.
    use color too. green and red."): the theme frame's top block, the two column heads on SOLID pills — GREEN
    "Disclosed" / "purchases", RED "Disclosed" / "sales", WHITE ink — right over their columns, one warm card per row
    (the logo disc, the ticker chip, the name; one size for every row) with each amount on its tinted tag (GREEN
    purchases, RED sales) — a cell where none was filed (the spec's EMPTY_CELL marker) stays blank, as the mock's
    does — then "+N more with filings", the note and the footer."""
    spec, logos = image.spec, image.logos
    x0, x1, _tx0, _tx1 = _columns(page)
    y = _theme_top(page, image, top, balance=True)
    cards_bot = _th_table_bottom(page, image, bottom)
    rows = list(spec["rows"])
    n = len(rows)
    hp = page.px(TH_LEDGER_HEAD)
    page.legible(hp, "heads")
    lh = int(hp * TH_LEDGER_HEAD.leading)
    heads = spec["heads"]
    head_h = max(len(h) for h in heads) * lh + 18
    col_w = max(page.length(word, hp) for h in heads for word in h) + 36
    sell_x1 = x1 - 22
    sell_x0 = sell_x1 - col_w
    buy_x1 = sell_x0 - 16
    buy_x0 = buy_x1 - col_w
    head_top = y + 4
    for i, (xa, xb, colour) in enumerate(((buy_x0, buy_x1, look.GREEN), (sell_x0, sell_x1, look.RED))):
        pill = page.rect((xa, head_top, xb, head_top + head_h), 12, colour)
        lines = heads[i]
        y0 = head_top + (head_h - len(lines) * lh) / 2
        for k, word in enumerate(lines):
            page.line(f"heads[{i}][{k}]", word, hp, (xa + xb) / 2, y0 + lh / 2 + k * lh, "mm", look.WHITE, pill)
    cards_top = head_top + head_h + 12
    gap = _gap(page, 6, 4)
    row_h = (cards_bot - cards_top - (n - 1) * gap) / n
    tp = page.px(TH_LEDGER_TAG)
    page.legible(tp, "rows[0].buys")
    tag_h = tp - 4 + 18
    r = int(min(TH_LEDGER_DISC, (row_h - 14) / 2))
    if r < 14 or row_h < tag_h + 12:
        raise _NoFit(f"ledger: rows of {row_h:.0f}px are too short")
    lcx = x0 + 18 + r
    chip_x = lcx + r + 16
    name_x = _th_name_column(page, rows, chip_x)
    name_max = buy_x0 - 24 - name_x
    n_px = page.px(TH_LEDGER_NAME)
    while n_px > TH_LEDGER_NAME.floor and any(page.length(row["name"], n_px) > name_max for row in rows):
        n_px -= 1
    page.legible(n_px, "rows[0].name")
    names = _th_wrap_names(page, rows, n_px, name_max)
    two = int(round(n_px * 1.12)) - page.font(n_px).getbbox("H", anchor="ls")[1]
    if any(len(nl_) > 1 for nl_ in names) and two + 12 > row_h:
        raise _NoFit(f"ledger: a two-line name ({two}px) does not fit rows of {row_h:.0f}px")
    for i, row in enumerate(rows):
        path = f"rows[{i}]"
        ry = cards_top + i * (row_h + gap)
        box = look.card(page, (x0, ry, x1, ry + row_h))
        cy = ry + row_h / 2
        page.disc(lcx, cy, r, logos[row["logo"]], ticker=row.get("chip"))       # a missing logo: its ticker
        if row.get("chip") is not None:
            _theme_chip(page, f"{path}.chip", row["chip"], chip_x, cy - (TH_CHIP_PX + 16) / 2, box)
        _th_name_block(page, f"{path}.name", names[i], n_px, name_x, cy, box)
        for key, xa, xb, ink, tint in (("buys", buy_x0, buy_x1, look.GREEN, look.GREEN_TINT),
                                       ("sells", sell_x0, sell_x1, look.RED, look.RED_TINT)):
            value = row[key]
            if value == cards.EMPTY_CELL:
                continue                  # no filing: the cell stays BLANK (theme_insiders_cyber.jpg)
            if page.length(value, tp) > col_w - 20:
                raise _NoFit(f"{path}.{key}: wider than its tag")
            tag = page.rect((xa, cy - tag_h / 2, xb, cy + tag_h / 2), 10, tint)
            page.line(f"{path}.{key}", value, tp, (xa + xb) / 2, cy, "mm", ink, tag)


def _lay_tally(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """Angle 5 of the weekly theme post, "Congress" (theme_congress_defense.jpg, mock_theme_congress; owner 2026-11-19:
    "use green color for 'purchases'. also, numbers like 6,4,3,2,2, use green color, same green as the purchases"): the
    theme frame's top block with "purchases" on a solid GREEN pill, then one warm card per company — the logo disc, the
    ticker chip over the name (one size for every card: one line from 40 down to 26, then two balanced lines), the
    count GREEN over "members" (INK2) on the right — "+N more", the note and the footer. Never a member's name, party,
    state or chamber: the spec carries none."""
    spec, logos = image.spec, image.logos
    x0, x1, _tx0, _tx1 = _columns(page)
    y = _theme_top(page, image, top, pill=TALLY_PILL_WORD, balance=True)
    cards_bot = _th_table_bottom(page, image, bottom)
    rows = list(spec["rows"])
    n = len(rows)
    first_top = y + _gap(page, 12, 6)
    gap = _gap(page, 16, 8)
    avail = cards_bot - first_top
    h = min(TH_TALLY_CARD_CAP, (avail - gap * (n - 1)) / n)
    r = int(min(TH_TALLY_DISC, (h - 2 * 14) / 2))
    if r < 18:
        raise _NoFit(f"tally: cards of {h:.0f}px leave no room for the logo disc")
    used = n * h + gap * (n - 1)
    cards_top = first_top + max(0.0, avail - used) / 2
    cp, up = page.px(TH_TALLY_COUNT), page.px(TH_TALLY_UNIT)
    page.legible(cp, "rows[0].count")
    page.legible(up, "unit")
    unit = spec["unit"]
    col_w = max(max(page.length(row["count"], cp) for row in rows), page.length(unit, up))
    col_cx = x1 - 34 - col_w / 2
    count_left = col_cx - col_w / 2
    lcx = x0 + 26 + r
    tx = lcx + r + _gap(page, 26, 16)
    avail_w = count_left - 24 - tx
    start = page.px(TH_TALLY_NAME)
    floor = max(TH_TALLY_NAME.floor, min(start, TH_TALLY_NAME_ONE_LINE_FLOOR))
    ns = start
    while ns > floor and any(page.length(row["name"], ns) > avail_w for row in rows):
        ns -= 1
    if any(page.length(row["name"], ns) > avail_w for row in rows):
        ns, names = _er_name_lines(page, [row["name"] for row in rows], avail_w, ns, TH_TALLY_NAME.floor, "rows")
    else:
        names = [(row["name"],) for row in rows]
    page.legible(ns, "rows[0].name")
    n_cap = -page.font(ns).getbbox("H", anchor="ls")[1]
    n_lh = int(round(ns * TH_TALLY_NAME.leading))
    # the count over "members", their INK boxes TH_TALLY_INK_GAP apart (mock_theme_congress.row_card: `ink_box` of each
    # string, `gap = 12`; the visual-fidelity round 2026-11-20 measured 8 px when the unit's box was its x-height —
    # "members" rises to its "b")
    ub = page.font(up).getbbox(unit, anchor="ls")
    u_top, u_bot = -ub[1], ub[3]
    c_gap = _gap(page, TH_TALLY_INK_GAP, 8)
    chip_h = TH_CHIP_PX + 16
    g1 = _gap(page, 12, 8)
    for i, row in enumerate(rows):
        path = f"rows[{i}]"
        cy0 = cards_top + i * (h + gap)
        box = look.card(page, (x0, cy0, x1, cy0 + h))
        cy = cy0 + h / 2
        page.disc(lcx, cy, r, logos[row["logo"]], ticker=row.get("chip"))       # a missing logo: its ticker
        cb = page.font(cp).getbbox(row["count"], anchor="ls")
        c_top, c_bot = -cb[1], cb[3]
        block = c_top + c_bot + c_gap + u_top + u_bot
        base = cy - block / 2 + c_top
        page.line(f"{path}.count", row["count"], cp, col_cx, base, "ms", look.GREEN, box)
        # ONE string drawn under every count: the first is the walk's `unit`, the others its repeats ("unit#k")
        page.line("unit" if i == 0 else f"unit#{i}", unit, up, col_cx, base + c_bot + c_gap + u_top, "ms",
                  look.INK2, box)
        lines = names[i]
        has_chip = row.get("chip") is not None
        tblock = (chip_h + g1 if has_chip else 0) + (len(lines) - 1) * n_lh + n_cap
        if tblock > h - 24:
            raise _NoFit(f"{path}: its text ({int(tblock)}px) does not fit its card ({h:.0f}px)")
        by = cy - tblock / 2
        if has_chip:
            _theme_chip(page, f"{path}.chip", row["chip"], tx, by, box)
            by += chip_h + g1
        for k, ln in enumerate(lines):
            page.line(f"{path}.name", ln, ns, tx, by + n_cap + k * n_lh, "ls", look.INK, box)
        for ln, b in zip(page.lines[-len(lines):], [box] * len(lines)):
            if ln.ink[2] > count_left - 24 + 1:
                raise _NoFit(f"{path}.name: runs into the count")


#: The word the Congress sub line puts on its GREEN pill (theme_congress_defense.jpg): a drawing rule — the string is
#: the server's whole sub line.
TALLY_PILL_WORD = "purchases"


#: The theme LIST cover's metrics at ramp step 0 (the 13F cover's card idea on the 9:16 frame, RESTYLE_SPEC §1 "apply
#: the same idea to every template video"): the sub line (SUB), a row card's disc, name, second line (a tile's line,
#: its share GOLD_INK) or value (INK, right), the rank disc and the "+N more" pill.
TH_COVER_SUB = _Style(30, 22, 1.25)
TH_COVER_DISC = _Style(56, 26, 1.0)
TH_COVER_NAME = _Style(40, 24, 1.12)
TH_COVER_LINE = _Style(28, 22, 1.2)
TH_COVER_VALUE = _Style(56, 30, 1.0)
TH_COVER_ROW_CAP = 190


def _is_theme_list(image: TemplateImage) -> bool:
    """A theme post's LIST cover (news-v23 phase 3, `cover_image` of a theme opening card): a `rows` image of the
    theme series, one section of logo rows."""
    spec = image.spec
    if image.layout != "rows" or not _is_theme(image):
        return False
    sections = spec.get("sections") or ()
    return len(sections) == 1 and all(r.get("logo") is not None for r in sections[0]["rows"])


def _lay_theme_list(page: look.Page, image: TemplateImage, top: int, bottom: int) -> None:
    """The theme post's video COVER (the 13F cover's idea, cover_13f_berkshire.jpg): the title two-tone, the list's
    heading as the SUB line, one warm card per row — the rank on a GOLD disc (a ranked list's row `label`), the logo
    disc, the ticker chip over the name, then either the row's line (angle 1: "<segment> · 88% of revenue", the share
    GOLD_INK) or its value right-aligned, big (angle 2) — and the "+N more" line. The rows take the room (up to
    TH_COVER_ROW_CAP), the slack shared above and below."""
    spec, logos = image.spec, image.logos
    x0, x1, tx0, tx1 = _columns(page)
    sec = spec["sections"][0]
    rows = list(sec["rows"])
    n = len(rows)
    y = look.headline(page, "title", spec["title"], HEADLINE_STYLE, tx0, top, tx1 - tx0, max_lines=3, tone="last")
    heading = sec.get("heading")
    if heading is not None:
        sub_px = page.px(TH_COVER_SUB)
        page.legible(sub_px, "sections[0].heading")
        # the picture's own break (`image.sub_break`), else only after a " · " (`_th_sub_lines`)
        got = _th_sub_lines(page, heading, image.sub_break, sub_px, tx1 - tx0)
        if got is None:
            raise _NoFit("sections[0].heading: does not fit three lines")
        asc, desc = page.font(sub_px).getmetrics()
        pitch = int(round(sub_px * TH_COVER_SUB.leading))
        t = look.Text(tuple(got), sub_px, pitch, (len(got) - 1) * pitch + asc + desc,
                      max(page.length(ln, sub_px) for ln in got))
        y += _gap(page, 12, 6)
        page.put("sections[0].heading", t, tx0, y, "la", look.SUB, page.frame.area)
        y += t.height
    more = sec.get("more")
    m_px = page.px(TH_MORE_PX)
    more_h = (m_px + 18 + _gap(page, 16, 8)) if more is not None else 0
    list_top = y + _gap(page, 26, 10)
    gap = _gap(page, 16, 6)
    avail = bottom - list_top - more_h
    row_h = int(min(TH_COVER_ROW_CAP, (avail - gap * (n - 1)) / n))
    r = min(page.px(TH_COVER_DISC), int((row_h - 20) / 2))
    if r < TH_COVER_DISC.floor:
        raise _NoFit(f"theme list: rows of {row_h}px leave no room for the logo disc")
    ranked = any(row.get("label") is not None for row in rows)
    valued = all(len(row["cells"]) == 2 for row in rows) and ranked
    rank_r = TH_RANK_R + 3
    pad = _gap(page, 24, 12)
    lead = (2 * rank_r + 14) if ranked else 0
    tx = x0 + pad + lead + 2 * r + _gap(page, 24, 12)
    right = x1 - _gap(page, 32, 16)
    n_px, l_px, v_px = page.px(TH_COVER_NAME), page.px(TH_COVER_LINE), page.px(TH_COVER_VALUE)
    v_w = max((page.length(row["cells"][1], v_px) for row in rows), default=0) if valued else 0
    name_w = (right - v_w - 24 - tx) if valued else (right - tx)
    while n_px > TH_COVER_NAME.floor and any(page.length(row["cells"][0], n_px) > name_w for row in rows):
        n_px -= 1
    page.legible(n_px, "sections[0].rows[0].cells[0]")
    used = n * row_h + gap * (n - 1)
    yy = list_top + max(0, (avail - used) // 2)
    n_cap = -page.font(n_px).getbbox("H", anchor="ls")[1]
    l_cap = -page.font(l_px).getbbox("H", anchor="ls")[1]
    chip_h = TH_CHIP_PX + 16
    for j, row in enumerate(rows):
        path = f"sections[0].rows[{j}]"
        box = look.card(page, (x0, yy, x1, yy + row_h))
        cy = yy + row_h / 2
        cx = x0 + pad
        if row.get("label") is not None:
            page.ellipse((cx, cy - rank_r, cx + 2 * rank_r, cy + rank_r), look.GOLD)
            page.line(f"{path}.label", row["label"], TH_RANK_PX + 2, cx + rank_r, cy + 1, "mm", look.INK, box)
            cx += 2 * rank_r + 14
        page.disc(cx + r, cy, r, logos[row["logo"]], ticker=row.get("chip"))    # a missing logo: its ticker
        name = row["cells"][0]
        if page.length(name, n_px) > name_w:
            raise _NoFit(f"{path}.cells[0]: does not fit its {int(name_w)}px column")
        second = row["cells"][1] if len(row["cells"]) > 1 else None
        facts = (_th_fact_plan(page, second, l_px, right - tx, f"{path}.cells[1]")
                 if (second is not None and not valued) else [])
        block = (chip_h + 8 if row.get("chip") is not None else 0) + n_cap + sum(
            int(round(l_px * 1.2)) for _ in facts) + (6 if facts else 0)
        if block > row_h - 16:
            raise _NoFit(f"{path}: its text ({block}px) does not fit the row ({row_h}px)")
        btop = cy - block / 2
        if row.get("chip") is not None:
            _theme_chip(page, f"{path}.chip", row["chip"], tx, btop, box)
            btop += chip_h + 8
        page.line(f"{path}.cells[0]", name, n_px, tx, btop + n_cap, "ls", look.INK, box)
        base = btop + n_cap + 6
        for k, runs in enumerate(facts):
            base += int(round(l_px * 1.2)) if k else l_cap + 6
            xx = tx
            for text, colour in runs:
                if text:
                    page.line(f"{path}.cells[1]", text, l_px, xx, base, "ls", colour, box)
                    xx += page.length(text, l_px)
        if valued and second is not None:
            page.legible(v_px, f"{path}.cells[1]")
            page.line(f"{path}.cells[1]", second, v_px, right, cy, "rm", look.INK, box)
        yy += row_h + gap
    yy -= gap
    if more is not None:
        page.legible(m_px, "sections[0].more")
        w = page.length(more, m_px)
        cxm = (x0 + x1) / 2
        top_m = yy + _gap(page, 16, 8)
        pbox = page.rect((cxm - w / 2 - 18, top_m, cxm + w / 2 + 18, top_m + m_px + 18), 12, look.PILL)
        page.line("sections[0].more", more, m_px, pbox[0] + 18, (pbox[1] + pbox[3]) / 2, "lm", look.GOLD, pbox)
        yy = pbox[3]
    if yy > bottom + 0.5:
        raise _NoFit(f"theme list: the rows end at {int(yy)}px, room to {bottom}px")


def _feature_card(page: look.Page, image: TemplateImage, x0: int, x1: int, top: int, bottom: int, *,
                  sit: bool = True) -> int:
    """The `feature` card — the figure (the series' tone) with its label beside it (INK2; under it when there is no
    room), a hairline, the fact rows (label INK2 left, value INK right — stacked when they do not fit one row) —
    laid with its bottom at `bottom` (`sit`: on the footer) or its top at `top`, when it fits between them. Returns
    its bottom."""
    spec = image.spec
    pad = _gap(page, 40)
    ix0, ix1 = x0 + pad, x1 - pad
    fig = spec["figure"]
    fsize = page.px(FEATURE_FIGURE_STYLE)
    page.legible(fsize, "figure")
    fl, ft, fr, fb = page.font(fsize).getbbox(fig, anchor="ls")
    fw = fr - fl
    lsize = page.px(FEATURE_LABEL_STYLE)
    beside = ix1 - (ix0 + fw + 34)
    label_beside = beside >= 200
    if fw > ix1 - ix0:
        raise _NoFit(f"figure: {fig[:20]!r} is {fw}px wide, the card holds {ix1 - ix0}px")
    label: Optional[look.Text] = None
    if label_beside and beside > FEATURE_LABEL_MAX_W:
        # mock_spotlight's label beside the figure: a narrow column ("of stock by the CEO," / "per Form 4")
        try:
            label = page.measure(spec["figure_label"], lsize, FEATURE_LABEL_MAX_W, "figure_label",
                                 leading=FEATURE_LABEL_STYLE.leading, max_lines=_cap(page, 3))
        except _NoFit:
            label = None
    if label is None:
        label = page.measure(spec["figure_label"], lsize, beside if label_beside else ix1 - ix0, "figure_label",
                             leading=FEATURE_LABEL_STYLE.leading, max_lines=_cap(page, 3), balance=True)
    if label_beside:
        # mock_spotlight: a cap-height box for the figure (`int(136 × 0.76)`), the label's LAST line on its baseline
        fig_h = int(round(fsize * FEATURE_FIG_H_OF_SIZE))
        label_up = (len(label.lines) - 1) * label.pitch + page.font(label.size).getmetrics()[0]
        block_h = max(fig_h, label_up)
    else:
        fig_h = fb - ft
        block_h = fig_h + _gap(page, 12) + label.height
    div_gap = _gap(page, FEATURE_DIVIDER_GAP) if label_beside else _gap(page, 28)
    vs, ls_ = page.px(FACT_VALUE_STYLE), page.px(FACT_LABEL_STYLE)
    row_h = page.px(FACT_ROW)
    facts = list(spec["facts"])
    rows: List[Tuple[look.Text, look.Text, bool, int]] = []
    for i, f in enumerate(facts):
        lt = page.measure(f["label"], ls_, ix1 - ix0, f"facts[{i}].label", leading=1.2, max_lines=_cap(page, 2))
        vw = page.length(f["value"], vs)
        one = lt.width + vw + 40 <= ix1 - ix0 and len(lt.lines) == 1
        if one:
            vt = page.measure(f["value"], vs, ix1 - ix0, f"facts[{i}].value", leading=1.2, max_lines=1)
            rows.append((lt, vt, True, row_h))
        else:
            vt = page.measure(f["value"], vs, ix1 - ix0, f"facts[{i}].value", leading=1.2, max_lines=_cap(page, 3))
            rows.append((lt, vt, False, lt.height + vt.height + _gap(page, 22)))
    h = pad + block_h + div_gap + 2 + _gap(page, 8) + sum(rw[3] for rw in rows) + _gap(page, 14)
    if top + h > bottom:
        raise _NoFit(f"feature: the card needs {h}px, {bottom - top}px are left")
    c_top = bottom - h if sit else top
    box = look.card(page, (x0, c_top, x1, c_top + h))
    top_in = c_top + pad
    if label_beside:
        base = top_in + block_h
        page.line("figure", fig, fsize, ix0 - fl, base, "ls", _tone(image, fig), box)
        page.put("figure_label", label, ix0 + fw + 34, base - (len(label.lines) - 1) * label.pitch, "ls",
                 look.INK2, box)
    else:
        base = top_in - ft
        page.line("figure", fig, fsize, ix0 - fl, base, "ls", _tone(image, fig), box)
        page.put("figure_label", label, ix0, top_in + fig_h + _gap(page, 12), "la", look.INK2, box)
    dy = c_top + pad + block_h + div_gap
    page.rect((ix0, dy, ix1, dy + 2), 0, look.FACT_DIVIDER)
    y = dy + _gap(page, 8)          # mock_spotlight: the fact rows from the hairline + 8 (16 px left under them)
    for i, (lt, vt, one, rh) in enumerate(rows):
        if one:
            mid = y + rh / 2
            page.line(f"facts[{i}].label", lt.lines[0], lt.size, ix0, mid, "lm", look.INK2, box)
            page.line(f"facts[{i}].value", vt.lines[0], vt.size, ix1, mid, "rm", look.INK, box)
        else:
            page.put(f"facts[{i}].label", lt, ix0, y + _gap(page, 8), "la", look.INK2, box)
            page.put(f"facts[{i}].value", vt, ix1, y + _gap(page, 8) + lt.height + _gap(page, 6), "ra", look.INK, box)
        if i < len(rows) - 1:
            page.rect((ix0, y + rh - 1, ix1, y + rh + 1), 0, look.FACT_DIVIDER)
        y += rh
    return c_top + h


def _place_feature(image: TemplateImage, step: int, font_path: str, engine: str, frame: Frame,
                   min_px: int) -> TemplateLayout:
    """The `feature` picture at `step`. With art (`image.art`): the band (0, 0, W, FEATURE_BAND_H) of art, the brand
    row over its navy scrim, the ART_LABEL pill at the band's bottom-right, the logo disc in its solid gold ring cut
    into the band's bottom edge; without: the same disc and ring, the name, the headline and the card as ONE block
    BALANCED on the page — as much air over the disc's ring as under the card, down to FEATURE_FOOT_GAP (visual-fidelity
    round 2026-11-20; it rested on the footer, the band's room left as plain canvas). Then the name (WHITE) and its gold
    chip (its foot on the name's baseline), the headline (its own ladder FEATURE_HEAD_PX first), and the card
    (`_feature_card`; with art, sitting FEATURE_FOOT_GAP over the footer)."""
    page = look.Page(frame, step, font_path, engine, min_px)
    spec = image.spec
    tile, pill = look.brand_row(page, spec["kicker"], word=_brand_word(image, frame))
    foot_top = look.footer(page, image.footer)
    x0, x1, tx0, tx1 = _columns(page)
    W = frame.canvas[0]
    r = page.px(FEATURE_DISC)
    label: Optional[Box] = None
    band: Optional[Box] = None
    art = image.art
    header = spec["header"]
    if art is not None:
        band = (0, 0, W, FEATURE_BAND_H)
        lw, lh = cards.art_label_size(font_path, engine)
        label = (W - 40 - lw, FEATURE_BAND_H - 28 - lh, W - 40, FEATURE_BAND_H - 28)
        cx, cy = x0 + 12 + r, FEATURE_BAND_H - 6
        page.disc(cx, cy, r, image.logos[header["logo"]], ring=True, cutout=look.page_colour(frame, int(cy + r)))
        name_base = FEATURE_BAND_H + _gap(page, 64)
        nx = cx + r + 12 + 30
        disc_box: Box = (int(cx - r), int(cy - r), int(cx + r), int(cy + r))
        if _overlap(label, pill) or label[1] < pill[3] + 8 or _overlap(label, disc_box) or label[0] < cx + r + 12:
            raise _NoFit("feature: the art label runs into the kicker or the disc")
    else:
        cx, cy = x0 + 12 + r, frame.area[1] + look.SOLID_RING_W + r
        mark = page.mark()
        # the art version's solid 5 px gold ring touching the disc (mock_spotlight.logo_badge), with no cut-out
        page.disc(cx, cy, r, image.logos[header["logo"]], ring=True, solid_ring=True)
        nx = cx + r + 12 + 30
        name_base = None
    nsize = page.px(FEATURE_NAME_STYLE)
    cs = page.px(CHIP_PX) + 4
    chip_text = header.get("chip")
    cw, chh = look.chip_size(page, chip_text, cs) if chip_text else (0, 0)
    name_w = tx1 - nx - (cw + 22 if chip_text else 0)
    name = page.measure(header["name"], nsize, name_w, "header.name", leading=1.1, max_lines=_cap(page, 2))
    asc = page.font(nsize).getmetrics()[0]
    if name_base is None:
        name_base = cy - name.height / 2 + asc
    page.put("header.name", name, nx, name_base, "ls", look.WHITE, page.frame.area if band is None else
             (0, 0, W, frame.canvas[1]))
    if chip_text:
        cx0 = nx + name.width + 22
        # mock_spotlight: `chip(d, chip_x, name_base - 44, …, size=28)` — the chip's foot on the name's baseline
        look.chip(page, "header.chip", chip_text, cs, cx0, name_base - chh, fill=look.GOLD, ink=look.INK)
    head_top = max(name_base + (len(name.lines) - 1) * name.pitch + _gap(page, 42),
                   (cy + r + look.SOLID_RING_W + _gap(page, 24)) if band is None else 0)
    y: Optional[int] = None
    if frame is POST_IMAGE_FRAME:
        for hpx in FEATURE_HEAD_PX:
            if hpx > page.px(FEATURE_HEADLINE_STYLE):
                continue
            mk = page.mark()
            try:
                y = look.headline(page, "headline", spec["headline"], FEATURE_HEADLINE_STYLE, tx0, head_top,
                                  tx1 - tx0, max_lines=FEATURE_HEAD_MAX_LINES, size=hpx)
                break
            except _NoFit:
                page.rollback(mk)
    if y is None:
        y = look.headline(page, "headline", spec["headline"], FEATURE_HEADLINE_STYLE, tx0, head_top, tx1 - tx0,
                          max_lines=6)
    bottom = min(frame.area[3], foot_top - FEATURE_FOOT_GAP)
    if band is not None:
        _feature_card(page, image, x0, x1, y + _gap(page, 30), bottom)
    else:
        end = _feature_card(page, image, x0, x1, y + _gap(page, 30), bottom, sit=False)
        # visual-fidelity round (2026-11-20): without the art band the block is BALANCED on the page — as much air
        # between the brand row and the disc's ring as between the card and the footer (resting it on the footer,
        # as the art version's card does, left ~260 px of plain page under the brand row)
        row_bottom = max(tile[3], pill[3] if pill is not None else tile[3])
        block_top = cy - r - look.SOLID_RING_W
        balanced = ((bottom + FEATURE_FOOT_GAP) - end - block_top + row_bottom) // 2
        page.shift(mark, int(max(0, min(bottom - end, balanced))))
    page.check()
    if band is not None:
        for ln in page.lines:
            if ln.field not in ("brand", "kicker", "header.name", "header.chip") and ln.field != f"wordmark:{header['logo']}" \
                    and ln.ink[1] < FEATURE_BAND_H:
                raise _NoFit(f"{ln.field}: drawn over the art band")
    lines = list(page.lines)
    if label is not None:
        lines.append(cards.art_label_line(label, font_path, engine))
    panel: Box = (x0, frame.area[1], x1, bottom)
    return TemplateLayout(kind=f"{frame.name}:{image.layout}", step=step, engine=engine, panel=panel,
                          rects=tuple(page.rects), tiles=tuple((d.box, d.art) for d in page.discs), lines=tuple(lines),
                          band=band, art=art, label=label, ellipses=tuple(page.ellipses), stacks=tuple(page.stacks),
                          discs=tuple(page.discs), brand=tile, pill=pill, footer_top=foot_top)


Walker = Callable[[_Walker, Mapping[str, Any]], None]
Placer = Callable[[look.Page, TemplateImage, int, int], None]
#: Everything this worker can DRAW — the contract's layouts (pinned == cards.LAYOUTS): the walker that checks a spec
#: (the server's mirror) and the placer that lays it out (`feature` is laid out whole by `_place_feature`).
DRAWERS: Dict[str, Tuple[Walker, Optional[Placer]]] = {
    "rows": (_w_rows, _lay_rows), "spotlight": (_w_spotlight, _lay_spotlight), "pair": (_w_pair, _lay_pair),
    "bars": (_w_bars, _lay_bars), "grid": (_w_grid, _lay_grid), "feature": (_w_feature, None),
    "ranked": (_w_ranked, _lay_ranked),
    "figures": (_w_theme_table("figures"), _lay_figures), "ledger": (_w_theme_table("ledger"), _lay_ledger),
    "tally": (_w_theme_table("tally"), _lay_tally),
}
#: What it ACCEPTS: only the SHIPPED layouts (cards.SHIPPED_LAYOUTS mirrors the server's). A layout in
#: SHIPPED_LAYOUTS that this worker cannot draw fails the import (KeyError) — never at render time.
_WALKERS: Dict[str, Walker] = {k: DRAWERS[k][0] for k in cards.SHIPPED_LAYOUTS}
_LAYOUTS: Dict[str, Optional[Placer]] = {k: DRAWERS[k][1] for k in cards.SHIPPED_LAYOUTS}


def _place(image: TemplateImage, step: int, font_path: str, engine: str,
           frame: Frame = POST_IMAGE_FRAME, min_px: int = 0, head_px: Optional[int] = None,
           bars_level: Optional[Tuple[int, bool]] = None) -> TemplateLayout:
    """`image` laid out on `frame` at ramp `step` (_NoFit when it does not fit there). `head_px` / `bars_level`: a
    `bars` picture's HEAD LADDER size and card LEVEL (`_lay_bars`); None everywhere else."""
    if image.layout == "feature":
        return _place_feature(image, step, font_path, engine, frame, min_px)
    page = look.Page(frame, step, font_path, engine, min_px)
    tile, pill = look.brand_row(page, image.spec["kicker"], word=_brand_word(image, frame))
    foot_top = look.footer(page, image.footer)
    # news-v23 phase 3: a theme picture's cards come closer to the footer (TH_BOTTOM_GAP, the mocks' own gaps)
    gap = TH_BOTTOM_GAP.get(image.layout, 34) if frame is POST_IMAGE_FRAME else 34
    if bars_level is not None:
        gap = BARS_FLOOR_GAP          # mock_money_map.render: `floor_gap = 30` above the footer
    bottom = min(frame.area[3], foot_top - _gap(page, gap))
    top = frame.area[1]
    if image.layout == "bars":
        _lay_bars(page, image, top, bottom, head_px, bars_level)
    elif image.layout == "spotlight":
        _lay_spotlight(page, image, top, bottom, ladder=frame is POST_IMAGE_FRAME)
    else:
        _LAYOUTS[image.layout](page, image, top, bottom)  # type: ignore[misc]
    page.check()
    x0, _y0, x1, _y1 = frame.area
    panel: Box = (x0, top, x1, bottom)
    for ln, bound in zip(page.lines, page.bounds):
        if ln.field not in ("brand", "kicker", "footer") and not _within(ln.ink, (0, top - 8, frame.canvas[0], bottom)):
            raise _NoFit(f"{ln.field}: leaves the content area")
    return TemplateLayout(kind=f"{frame.name}:{image.layout}", step=step, engine=engine, panel=panel,
                          rects=tuple(page.rects), tiles=tuple((d.box, d.art) for d in page.discs),
                          lines=tuple(page.lines), ellipses=tuple(page.ellipses), stacks=tuple(page.stacks),
                          discs=tuple(page.discs), brand=tile, pill=pill, footer_top=foot_top, head_px=head_px,
                          bars_level=bars_level, icon=page.icon)


def _first_fit(image: TemplateImage, font_path: str, engine: str) -> TemplateLayout:
    """The first layout that fits: a Money Map tries its HEAD LADDER × its card LEVELS first (BARS_HEAD_PX: the
    headline at 80, then 72, then 66; for each, BARS_LEVELS biggest first, each with its legend full then `tight` —
    mock_money_map.render), then the whole page stepping together along the ramp (a worst case the server's bounds
    let through still fits); every other layout the ramp alone. CardOverflow with the floor step's reason when
    nothing fits."""
    reason = ""
    if image.layout == "bars":
        for head_px in BARS_HEAD_PX:
            for lv in range(len(BARS_LEVELS)):
                for tight in (False, True):
                    try:
                        return _place(image, 0, font_path, engine, head_px=head_px, bars_level=(lv, tight))
                    except _NoFit as e:
                        reason = str(e)
    for step in range(cards.RAMP_STEPS + 1):
        try:
            return _place(image, step, font_path, engine)
        except _NoFit as e:
            reason = str(e)
    raise CardOverflow(f"template image ({image.layout}) does not fit at the floor sizes: {reason}")


def layout_template(image: TemplateImage, *, font_path: str, layout_engine: Optional[str] = None) -> TemplateLayout:
    """Where everything on the template image goes: the largest ramp step at which every string fits whole (a Money
    Map: its head ladder first — `_first_fit`). CardOverflow (with the floor step's reason) when none does."""
    engine = cards.resolve_layout_engine(layout_engine)
    layout = _first_fit(image, font_path, engine)
    blank = cards.blank_wordmarks(layout)
    if blank:
        logger.warning("template image (%s): blank disc for %s — the company name does not fit its disc "
                       "legibly and no ticker stands in (the name stays declared, never drawn clipped)",
                       image.layout, blank)
    return layout


def illegible_lines(layout: TemplateLayout, min_px: Optional[int] = None) -> List[DrawnLine]:
    """The drawn lines of `layout` under `min_px` (default DATA_CARD_MIN_PX) — every line but a WORDMARK disc's
    company name or a disc's ticker (logo stand-ins, sized by their disc: `look.is_logo_standin`)."""
    floor = DATA_CARD_MIN_PX if min_px is None else min_px
    return [ln for ln in layout.lines if ln.size < floor and not look.is_logo_standin(ln.field)]


def layout_data_card(image: TemplateImage, *, font_path: str, layout_engine: Optional[str] = None,
                     cover: bool = False) -> TemplateLayout:
    """The DATA CARD (Batch 2): `image` laid out on the 9:16 video card (DATA_CARD_FRAME) at the largest ramp step
    where it fits whole — and only if every line drawn there is at least DATA_CARD_MIN_PX (`illegible_lines`; a
    wordmark disc's name is exempt). Every size steps down together, so the search stops at the first step that
    would draw a line under the floor (`look.Illegible`). CardOverflow otherwise (the caller keeps the text card).
    A layout outside cards.DATA_CARD_LAYOUTS (`feature`) is never a data card: CardOverflow before anything is
    laid out. `cover` (news-v23 phase 3, 2026-11-20): the image is a theme TABLE's video COVER (`cover_image` of an
    opening card whose list names its layout, cards.THEME_TABLE_LAYOUTS) — laid out the same way, never a data card."""
    engine = cards.resolve_layout_engine(layout_engine)
    allowed = cards.DATA_CARD_LAYOUTS + (cards.THEME_TABLE_LAYOUTS if cover else ())
    if image.layout not in allowed:
        raise CardOverflow(f"data card ({image.layout}): this layout is never drawn as a data card "
                           f"(cards.DATA_CARD_LAYOUTS) — the text card stays")
    reason = ""
    for step in range(cards.RAMP_STEPS + 1):
        try:
            layout = _place(image, step, font_path, engine, DATA_CARD_FRAME, DATA_CARD_MIN_PX)
        except _NoFit as e:
            reason = str(e)
            continue
        except _Illegible as e:
            raise CardOverflow(f"data card ({image.layout}) does not fit legibly: {e}") from None
        small = illegible_lines(layout, DATA_CARD_MIN_PX)   # defence in depth
        if small:
            raise CardOverflow(f"data card ({image.layout}) fits only from ramp step {step}, where "
                               f"{small[0].field} is drawn at {small[0].size}px — under the "
                               f"{DATA_CARD_MIN_PX}px legibility floor")
        return layout
    raise CardOverflow(f"data card ({image.layout}) does not fit the card's content area at the floor sizes: {reason}")


def _art_band(img, frame: Frame, art: cards.ArtImage) -> None:
    """The `feature` picture's art: centre-cropped edge to edge into (0, 0, W, FEATURE_BAND_H) (`cards.art_tile`),
    a soft shadow under its edge, and a navy scrim over its top (the brand row reads on any art). Deterministic."""
    from PIL import Image, ImageDraw

    W = frame.canvas[0]
    h = FEATURE_BAND_H
    sh = 44
    shade = Image.new("L", (W, sh), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(sh):
        sd.line([(0, y), (W, y)], fill=int(150 * (1 - y / sh) ** 2))
    page = img.crop((0, h, W, h + sh))
    img.paste(Image.composite(Image.new("RGB", (W, sh), (0, 0, 0)), page, shade), (0, h))
    img.paste(cards.art_tile(art, W, h), (0, 0))
    mask = Image.new("L", (W, FEATURE_SCRIM_H), 0)
    md = ImageDraw.Draw(mask)
    hold = 0.30
    for y in range(FEATURE_SCRIM_H):
        u = min(1.0, max(0.0, (y / FEATURE_SCRIM_H - hold) / (1 - hold)))
        md.line([(0, y), (W, y)], fill=int(225 * (1 - u * u * (3 - 2 * u))))
    region = img.crop((0, 0, W, FEATURE_SCRIM_H))
    img.paste(Image.composite(Image.new("RGB", (W, FEATURE_SCRIM_H), (9, 14, 30)), region, mask), (0, 0))


def draw_template(image: TemplateImage, *, font_path: str, layout_engine: Optional[str] = None):
    """(the template image as an RGB PIL image, its layout) — before any JPEG encoding, so tests can check its
    pixels exactly. CardOverflow / CardAssetError as for a card."""
    layout = layout_template(image, font_path=font_path, layout_engine=layout_engine)
    return _draw(POST_IMAGE_FRAME, layout, font_path), layout


def draw_data_card(image: TemplateImage, *, font_path: str, layout_engine: Optional[str] = None,
                   cover: bool = False):
    """(the DATA CARD as a 1080×1920 RGB PIL image, its layout): `image` drawn on the 9:16 video card
    (`layout_data_card`) — nothing in the caption band or the safe bottom. CardOverflow (it does not fit legibly) /
    CardAssetError as for a card."""
    layout = layout_data_card(image, font_path=font_path, layout_engine=layout_engine, cover=cover)
    return _draw(DATA_CARD_FRAME, layout, font_path), layout


def _draw(frame: Frame, layout: TemplateLayout, font_path: str):
    """`layout` drawn on a fresh canvas of `frame`: the art band (a `feature` with art), the cards, pills, tiles and
    bars, the dots and rings, the logo discs, the brand tile, the art label's pill, then every line of text."""
    gx0, gy0, gx1, gy1 = FEATURE_GLOW
    W = frame.canvas[0]
    img = look.canvas(frame, glow_box=((W + gx0, FEATURE_BAND_H + gy0, W + gx1, FEATURE_BAND_H + gy1)
                                       if layout.art is not None else None))
    if layout.icon is not None:
        # news-v23 phase 3: the theme's decoration, composited before anything else (theme_family.Frame.art)
        img = theme_art.draw(img, layout.icon)
    if layout.art is not None:
        _art_band(img, frame, layout.art)
    if layout.label is not None:
        lb = layout.label
        cards._fill_rounded(img, lb, (lb[3] - lb[1]) // 2, look.NAVY_CHIP)
    look.draw_page(img, layout, frame, font_path, layout.engine)
    return img


def check_glyphs(image: TemplateImage, font_path: str) -> None:
    """cards.MissingGlyphs naming every character of every string the image MAY draw (every logo's name included: a
    logo that fails later still has its wordmark drawable; the brand row's word too). A headline is drawn
    upper-cased only where the face has the upper case (`look.caps`), so its original's glyphs are enough."""
    cards.check_glyphs([], font_path, extra=(*image.allowed, look.BRAND_ROW_WORD))


#: Why per-post art offered to a `feature` picture was not drawn (`cards.RenderedImage.art_skipped`): a glyph of
#: ART_LABEL the face lacks; the label would take the picture past ONSCREEN_TEXT_MAX strings; the picture with the
#: art does not fit (CardOverflow) or is over the byte cap at every JPEG quality (ImageTooLarge — noisy art
#: compresses worst); the art file cannot be read now (CardAssetError). The picture then falls back to today's
#: drawing — never a skipped day because of the art.
ART_SKIP_REASONS: Tuple[str, ...] = ("missing_glyphs", "too_many_strings", "no_fit", "too_large", "unreadable")


def _art_label_problem(image: TemplateImage, font_path: str) -> Optional[str]:
    """Why ART_LABEL cannot be added to `image`'s declared strings ("missing_glyphs" / "too_many_strings"), or None.
    CardAssetError (an unreadable font's cmap) propagates: it is not the art's."""
    try:
        cards.check_glyphs([], font_path, extra=[cards.ART_LABEL])
    except cards.MissingGlyphs:
        return "missing_glyphs"
    if len(set(image.strings) | {cards.ART_LABEL, look.BRAND_ROW_WORD}) > cards.ONSCREEN_TEXT_MAX:
        return "too_many_strings"
    return None


def render_template_image(image: TemplateImage, *, font_path: str, layout_engine: Optional[str] = None,
                          max_bytes: int = cards.POST_IMAGE_MAX_BYTES) -> "cards.RenderedImage":
    """The template image as a baseline JPEG of at most `max_bytes` (cards.JPEG_QUALITIES steps
    down until it fits). cards.ImageTooLarge when none does; CardOverflow when the text cannot fit
    whole; CardAssetError when the font or a verified logo is unreadable.

    With per-post art (`image.art`, a `feature` picture — workstream C) the art is drawn in the band; when that
    cannot be done (ART_SKIP_REASONS: a label glyph, too many strings, no fit, over the byte cap, an unreadable art
    file) the picture is rendered ONCE more WITHOUT the art — today's drawing, byte for byte — with a WARNING and
    `art_skipped` set on the result. Art never ends the day: an error of the no-art render is the picture's own,
    exactly as before."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError(f"max_bytes must be a positive int, not {max_bytes!r}")
    if image.art is None:
        return _render_template_image(image, font_path=font_path, layout_engine=layout_engine, max_bytes=max_bytes)
    reason = _art_label_problem(image, font_path)
    detail = ""
    if reason is None:
        try:
            return _render_template_image(image, font_path=font_path, layout_engine=layout_engine,
                                          max_bytes=max_bytes)
        except CardOverflow as e:
            reason, detail = "no_fit", str(e)[:200]
        except cards.ImageTooLarge as e:
            reason, detail = "too_large", str(e)[:200]
        except cards.CardAssetError as e:
            reason, detail = "unreadable", str(e)[:200]
    logger.warning("template image (%s): no art on the picture (%s%s) — today's drawing (art sha=%s…)",
                   image.layout, reason, f": {detail}" if detail else "", image.art.sha256[:12])
    plain = _render_template_image(dataclasses.replace(image, art=None), font_path=font_path,
                                   layout_engine=layout_engine, max_bytes=max_bytes)
    return dataclasses.replace(plain, art_skipped=reason)


def _render_template_image(image: TemplateImage, *, font_path: str, layout_engine: Optional[str],
                           max_bytes: int) -> "cards.RenderedImage":
    """`render_template_image`'s one render of `image` as it is (with its art, if it carries one)."""
    for s in image.allowed:
        if len(s) > cards.MAX_STRING_CHARS:
            raise CardOverflow(f"template image: a string of {len(s)} characters is over {cards.MAX_STRING_CHARS}")
    img, layout = draw_template(image, font_path=font_path, layout_engine=layout_engine)
    sizes: List[str] = []
    for quality in cards.JPEG_QUALITIES:
        data = cards.encode_jpeg(img, quality)
        if len(data) <= max_bytes:
            logger.debug("template image rendered layout=%s step=%d engine=%s quality=%d bytes=%d",
                         image.layout, layout.step, layout.engine, quality, len(data))
            return cards.RenderedImage(data=data, quality=quality, layout=layout)  # type: ignore[arg-type]
        sizes.append(f"q{quality}={len(data)}")
    raise cards.ImageTooLarge(f"the template image is over {max_bytes} bytes at every JPEG quality step "
                              f"({', '.join(sizes)})")
