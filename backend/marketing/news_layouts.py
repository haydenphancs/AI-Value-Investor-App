"""
The 4:5 POST IMAGE of a TEMPLATE (news) script — drop 2a, contract D9 / D14: the `rows`,
`spotlight` and `bars` layouts of the closed `image_spec` the web's templates compose
(app/services/marketing/template_onscreen.py is the server's half; `cards.LAYOUTS` and the caps
mirror it, pinned equal by tests/test_marketing_news_layouts.py) — and, drop 2b (2026-10-09), the
`pair` (company_stakes: investor plate → arrow → investee plate, the names, the figure, its label,
≤ 2 short lines) and `grid` (theme_explainer: ≤ 12 member tiles of logo + name + optional caption
line, and a "+k more" line) layouts (tests/test_marketing_layouts_2b.py).

`DRAWERS` is everything this worker can draw; `_WALKERS` / `_LAYOUTS` — what it ACCEPTS — are only
the SHIPPED layouts (`cards.SHIPPED_LAYOUTS`, the server's mirror). Since drop 2b (2026-10-10) all
five are shipped: `pair` and `grid` were switched on with their series, on both sides together.

A news image is 1080×1350, like the lesson's (`cards.render_image`): a CARD panel on the PAGE
with the kicker pill at its top, the layout's content under it, and the server's code-owned
`image_footer` burned on the page below — but its strings come from `image_spec`, never from the
script's `image_post` (on a template that is name-free ALT TEXT; it is never drawn).

* **What is drawn is what the spec holds, and nothing else.** `walk` reads the spec in the
  server's draw order (template_onscreen.image_strings: kicker, the layout's strings, footer) and
  validates it as a CLOSED schema — an unknown key, a missing key, a count out of bounds, a string
  that is not one drawable line, a ratio outside [0, 1], a style outside BAR_STYLES, a logo key the
  script carries no entry for, a layout this worker does not ship (outside `cards.SHIPPED_LAYOUTS`)
  is a ValueError: the render fails loudly (RenderInputError), it never falls back to the lesson
  layout (which would draw the alt text).
* **Logos.** Each referenced logo is a light plate: the verified logo, drawn unaltered and only
  scaled (`cards.draw_plate`), or — missing, failed or unverified — the company name as a
  wordmark. A wordmark's name is a drawn string, so `TemplateImage.strings` (what the asset
  declares in `metadata.onscreen_text`) is the walk with each WORDMARK's name at its logo's place;
  `TemplateImage.allowed` (every logo's name) is exactly the server's allow-list, and the declared
  strings are always a subset of it, footer included.
* **Never truncated.** Every string is word-wrapped whole; every size (and every tile) steps down
  together along `cards.RAMP_STEPS`; text that does not fit at the floor is `CardOverflow` (the
  render stage skips the day `unrenderable_text`), never a clipped line. Every ink box is checked
  inside the panel (the footer inside its band).
* **Neutral palette.** The brand's four colours and their blends — ACCENT for figures, TEXT and a
  muted TEXT for words, a faint track for bars — and the logos' own colours on their plates. No
  green, no red: a gain/loss colour on a named company would be a verdict.
* **Deterministic bytes**: the same spec, logos, Pillow and engine give the same JPEG
  (`cards.encode_jpeg`, the same quality ladder and byte cap as the lesson image).

Pure apart from Pillow (imported inside functions). Nothing here imports app.*.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from marketing import cards
from marketing.cards import (
    ACCENT, BADGE_FILL, CARD, PAGE, TEXT, Box, CardOverflow, DrawnLine, LogoArt, _NoFit, _Style,
    _font, _mix, _rgb, _within, _wrap,
)

logger = logging.getLogger("marketing.news_layouts")

# ── sizes (px; every one steps down together from start to floor, cards.RAMP_STEPS) ─────────

KICKER_STYLE = _Style(30, 22, 1.0)          # the kicker pill's text (ACCENT on BADGE_FILL)
KICKER_PAD_X, KICKER_PAD_Y = 20, 10
HEADER_TILE = _Style(132, 72, 1.0)          # a header's logo plate (side)
ROW_TILE = _Style(88, 48, 1.0)              # a row's logo plate (side)
NAME_STYLE = _Style(50, 28, 1.15)           # a header's company name (TEXT)
CHIP_STYLE = _Style(28, 20, 1.0)            # a header's chip pill (ACCENT on BADGE_FILL)
TITLE_STYLE = _Style(60, 32, 1.12)          # title (ACCENT)
SUBTITLE_STYLE = _Style(34, 22, 1.25)       # subtitle (MUTED)
HEADING_STYLE = _Style(32, 20, 1.2)         # a section heading (ACCENT)
CELL_STYLE = _Style(40, 22, 1.18)           # a row's first cell (TEXT)
CELL_FIGURE_STYLE = _Style(40, 22, 1.18)    # a row's last cell when there are 2-3 (ACCENT)
CELL_SECONDARY_STYLE = _Style(30, 18, 1.22)  # a row's middle cell (MUTED)
NOTE_STYLE = _Style(28, 18, 1.28)           # notes, "more" lines (MUTED)
FIGURE_STYLE = _Style(190, 90, 1.04)        # the spotlight figure (ACCENT)
HEADLINE_STYLE = _Style(52, 30, 1.18)       # the spotlight headline (TEXT)
LINE_STYLE = _Style(36, 22, 1.28)           # the spotlight's short lines (MUTED)
BAR_TEXT_STYLE = _Style(32, 20, 1.2)        # a bar's label (TEXT) and value (ACCENT)
BAR_HEIGHT = _Style(26, 14, 1.0)            # a bar's track height
CALLOUT_STYLE = _Style(40, 24, 1.22)        # the bars' callout (TEXT)
# drop 2b — `pair` (company_stakes) and `grid` (theme_explainer)
PAIR_TILE = _Style(272, 140, 1.0)           # each side's logo plate (side)
PAIR_NAME_STYLE = _Style(40, 24, 1.15)      # a side's name, centred under its plate (TEXT)
PAIR_FIGURE_STYLE = _Style(176, 88, 1.04)   # the figure, centred (ACCENT)
PAIR_LABEL_STYLE = _Style(46, 28, 1.2)      # the figure's label — the stake's verb (TEXT)
GRID_TILE = _Style(84, 44, 1.0)             # a grid tile's logo plate (side)
GRID_NAME_STYLE = _Style(34, 22, 1.15)      # a grid tile's company name (TEXT)
GRID_LINE_STYLE = _Style(26, 17, 1.22)      # a grid tile's caption line (MUTED)
GRID_ROW_GAP = _Style(26, 10, 1.0)          # between two rows of tiles

TILE_GAP = 28                  # a plate → the text beside it
CELL_GAP = 24                  # a row's left text → its right-aligned figure
CHIP_GAP = 12                  # a header's name → its chip
BAR_GAP = 10                   # a bar's label line → its track
OUTLINE_W = 3                  # an `outline` bar's stroke
#: A right-aligned figure may take at most this share of a row's text column; a wider one is
#: stacked under the row's other cells instead (never squeezed into a sliver).
RIGHT_SHARE = 0.5
#: The most lines one wrapped string is allowed before it counts as not fitting (bounds the work).
MAX_WRAP_LINES = 8
PAIR_HALF_GAP = 32             # between the pair's two half columns (each side's plate and name)
ARROW_PAD = 18                 # a pair plate → the arrow's tail / head
ARROW_MIN_SHAFT = 24           # the arrow's shortest shaft; less room is "does not fit"
GRID_COLUMNS = 2
GRID_COL_GAP = 28              # between two grid columns
GRID_TEXT_GAP = 18             # a grid tile's plate → its text

MUTED = _mix(TEXT, CARD, 0.72)
TRACK = _mix(TEXT, CARD, 0.10)
DIVIDER = _mix(TEXT, CARD, 0.16)


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

    def text(self, value: Any, path: str) -> None:
        if not cards.drawable_text(value):
            raise _bad(path, "not one drawable string")
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
        sec = w.obj(section, path, ("rows",), ("heading", "more"))
        w.opt_text(sec, "heading", path)
        rows = w.seq(sec["rows"], f"{path}.rows", 1, cards.MAX_SECTION_ROWS)
        total += len(rows)
        if total > cards.MAX_ROWS:
            raise _bad("sections", f"more than {cards.MAX_ROWS} rows")
        for j, raw in enumerate(rows):
            rpath = f"{path}.rows[{j}]"
            row = w.obj(raw, rpath, ("cells",), ("logo",))
            if row.get("logo") is not None:
                w.logo(row["logo"], f"{rpath}.logo")
            for k, cell in enumerate(w.seq(row["cells"], f"{rpath}.cells", 1, cards.MAX_CELLS)):
                w.text(cell, f"{rpath}.cells[{k}]")
        w.opt_text(sec, "more", path)
    _w_lines(w, spec, "notes", cards.MAX_NOTES)


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


def _w_bar_list(w: _Walker, spec: Mapping[str, Any], key: str, lo: int, hi: int) -> None:
    for i, raw in enumerate(w.seq(spec[key], key, lo, hi)):
        path = f"{key}[{i}]"
        bar = w.obj(raw, path, ("label", "value", "ratio", "style"), ())
        w.text(bar["label"], f"{path}.label")
        w.text(bar["value"], f"{path}.value")
        if not _ratio_ok(bar["ratio"]):
            raise _bad(f"{path}.ratio", f"{str(bar['ratio'])[:20]} is not a finite number in [0, 1]")
        if not isinstance(bar["style"], str) or bar["style"] not in cards.BAR_STYLES:
            raise _bad(f"{path}.style", f"{str(bar['style'])[:20]!r} is not one of {cards.BAR_STYLES}")


def _w_bars(w: _Walker, spec: Mapping[str, Any]) -> None:
    _w_company(w, spec["header"], "header", chip=False)
    w.text(spec["title"], "title")
    w.text(spec["subtitle"], "subtitle")
    _w_bar_list(w, spec, "segments", cards.MIN_BARS, cards.MAX_BARS)
    _w_bar_list(w, spec, "flow", cards.MIN_FLOW, cards.MAX_FLOW)
    w.text(spec["callout"], "callout")


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


def _w_grid(w: _Walker, spec: Mapping[str, Any]) -> None:
    w.text(spec["title"], "title")
    w.text(spec["subtitle"], "subtitle")
    required, optional = cards.GRID_TILE_KEYS
    for i, raw in enumerate(w.seq(spec["tiles"], "tiles", cards.MIN_TILES, cards.MAX_TILES)):
        path = f"tiles[{i}]"
        tile = w.obj(raw, path, required, optional)
        w.logo(tile["logo"], f"{path}.logo")
        w.text(tile["name"], f"{path}.name")
        w.opt_text(tile, "line", path)
    w.opt_text(spec, "more", "")


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
    the server's template_onscreen.image_strings)."""

    layout: str
    spec: Mapping[str, Any]
    logos: Mapping[str, LogoArt]
    strings: Tuple[str, ...]
    allowed: Tuple[str, ...]

    @property
    def footer(self) -> str:
        return str(self.spec["footer"])


def template_image_for_script(script: Mapping[str, Any], logos: Mapping[str, LogoArt]) -> TemplateImage:
    """The script's `image_spec` + `image_footer` as a TemplateImage. ValueError when either is
    missing or malformed, or the spec's footer is not the script's `image_footer` (the server
    requires that exact footer among the drawn strings)."""
    if not isinstance(script, Mapping):
        raise ValueError(f"script must be an object, not {type(script).__name__}")
    spec = script.get("image_spec")
    if spec is None:
        raise ValueError("the template script carries no image_spec: its post image cannot be drawn")
    footer = script.get("image_footer")
    if not cards.drawable_text(footer):
        raise ValueError("the template script has no image_footer: an image without its footer must not render")
    items = walk(spec, logos)
    if spec["footer"] != footer:
        raise ValueError("image_spec.footer is not the script's image_footer")
    return TemplateImage(layout=spec["layout"], spec=spec, logos=dict(logos),
                         strings=tuple(_strings(items, logos, every_name=False)),
                         allowed=tuple(_strings(items, logos, every_name=True)))


# ── layout ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rect:
    box: Box
    radius: int
    fill: Tuple[int, int, int]
    outline: int = 0          # > 0: draw only the stroke, this many px wide


@dataclass(frozen=True)
class TemplateLayout:
    kind: str                         # "template:<layout>"
    step: int
    engine: str
    panel: Box
    rects: Tuple[Rect, ...]
    tiles: Tuple[Tuple[Box, LogoArt], ...]
    lines: Tuple[DrawnLine, ...]
    #: Filled ACCENT polygons (drop 2b: the pair's arrow) — shapes, never text.
    shapes: Tuple[Tuple[Tuple[int, int], ...], ...] = ()

    def field_lines(self, name: str) -> List[str]:
        return [ln.text for ln in self.lines if ln.field == name]

    def fields(self) -> List[str]:
        """Every field drawn, in first-drawn order."""
        out: List[str] = []
        for ln in self.lines:
            if ln.field not in out:
                out.append(ln.field)
        return out


@dataclass(frozen=True)
class _Text:
    lines: Tuple[str, ...]
    size: int
    pitch: int
    height: int
    width: float


class _Canvas:
    """The panel's content column, (0, 0) at its top-left: `width` px wide and at most `budget`
    tall. Collects text lines, rectangles and logo plates; `y` is the next free row."""

    def __init__(self, width: int, budget: int, font_path: str, engine: str, step: int) -> None:
        self.width, self.budget = width, budget
        self.font_path, self.engine, self.step = font_path, engine, step
        self.y = 0
        self.lines: List[Tuple[str, str, int, int, int, str, Tuple[int, int, int]]] = []
        self.rects: List[Rect] = []
        self.tiles: List[Tuple[Box, LogoArt]] = []
        self.shapes: List[Tuple[Tuple[int, int], ...]] = []

    def px(self, style: _Style) -> int:
        return style.size(self.step)

    def check(self, what: str) -> None:
        if self.y > self.budget:
            raise _NoFit(f"{what}: the panel is {self.y}px tall, room for {self.budget}px")

    def gap(self, px: int) -> None:
        if self.y:
            self.y += px

    def measure(self, text: str, style: _Style, width: float, what: str) -> _Text:
        size = self.px(style)
        font = _font(self.font_path, size, self.engine)
        asc, desc = font.getmetrics()
        pitch = int(round(size * style.leading))
        wrapped = _wrap(text.split(), font, width, MAX_WRAP_LINES, what)
        if not wrapped:
            raise _NoFit(f"{what}: nothing to draw")
        widest = max(font.getlength(ln) for ln in wrapped)
        return _Text(tuple(wrapped), size, pitch, (len(wrapped) - 1) * pitch + asc + desc, widest)

    def put(self, field: str, t: _Text, x: int, y: int, anchor: str, fill: Tuple[int, int, int]) -> None:
        for i, line in enumerate(t.lines):
            self.lines.append((field, line, t.size, x, y + i * t.pitch, anchor, fill))

    def para(self, field: str, text: str, style: _Style, fill: Tuple[int, int, int], *, gap: int = 0,
             center: bool = False) -> None:
        t = self.measure(text, style, self.width, field)
        self.gap(gap)
        if center:
            self.put(field, t, self.width // 2, self.y, "ma", fill)
        else:
            self.put(field, t, 0, self.y, "la", fill)
        self.y += t.height
        self.check(field)

    def pill_size(self, text: str, style: _Style) -> Tuple[int, int, int]:
        size = self.px(style)
        font = _font(self.font_path, size, self.engine)
        asc, desc = font.getmetrics()
        return int(math.ceil(font.getlength(text))) + 2 * KICKER_PAD_X, asc + desc + 2 * KICKER_PAD_Y, size

    def pill(self, field: str, text: str, style: _Style, x: int, y: int) -> int:
        w, h, size = self.pill_size(text, style)
        if x + w > self.width:
            raise _NoFit(f"{field}: the pill is {w}px wide, room for {self.width - x}px")
        self.rects.append(Rect((x, y, x + w, y + h), h // 2, BADGE_FILL))
        self.lines.append((field, text, size, x + KICKER_PAD_X, y + h // 2, "lm", _rgb(ACCENT)))
        return h


def _header(cv: _Canvas, header: Mapping[str, Any], logos: Mapping[str, LogoArt], *, chip: bool) -> None:
    """[plate] name / chip — the plate and the text block centred on each other."""
    side = cv.px(HEADER_TILE)
    nx = side + TILE_GAP
    name = cv.measure(header["name"], NAME_STYLE, cv.width - nx, "header.name")
    chip_text = header.get("chip") if chip else None
    chip_h = cv.pill_size(chip_text, CHIP_STYLE)[1] if chip_text else 0
    block = name.height + (CHIP_GAP + chip_h if chip_text else 0)
    h = max(side, block)
    y0 = cv.y
    ty = y0 + (h - side) // 2
    cv.tiles.append(((0, ty, side, ty + side), logos[header["logo"]]))
    top = y0 + (h - block) // 2
    cv.put("header.name", name, nx, top, "la", _rgb(TEXT))
    if chip_text:
        cv.pill("header.chip", chip_text, CHIP_STYLE, nx, top + name.height + CHIP_GAP)
    cv.y = y0 + h
    cv.check("header")


def _cells(cv: _Canvas, x: int, width: int, cells: Sequence[str], path: str,
           styles: Tuple[_Style, _Style, _Style], fills: Tuple[Any, Any, Any]) -> Tuple[List[Tuple], int]:
    """Lay a row's 1-3 cells out in a `width` column at `x` (y relative to the row's top).
    Returns ([(field, _Text, x, y, anchor, fill)], height). One cell: wrapped. Two or three: the
    first (and the middle one under it) on the left, the LAST right-aligned beside them — or,
    when it would take more than RIGHT_SHARE of the column, stacked under the others."""
    first_style, mid_style, last_style = styles
    first_fill, mid_fill, last_fill = fills
    placed: List[Tuple] = []
    if len(cells) == 1:
        t = cv.measure(cells[0], first_style, width, f"{path}.cells[0]")
        return [(f"{path}.cells[0]", t, x, 0, "la", first_fill)], t.height
    last_i = len(cells) - 1
    right = cells[last_i]
    font = _font(cv.font_path, cv.px(last_style), cv.engine)
    wr = font.getlength(right)
    if wr <= width * RIGHT_SHARE:
        left_w = int(width - wr - CELL_GAP)
        y = 0
        t0 = cv.measure(cells[0], first_style, left_w, f"{path}.cells[0]")
        placed.append((f"{path}.cells[0]", t0, x, 0, "la", first_fill))
        y = t0.height
        if len(cells) == 3:
            t1 = cv.measure(cells[1], mid_style, left_w, f"{path}.cells[1]")
            y += int(round(t1.size * 0.25))
            placed.append((f"{path}.cells[1]", t1, x, y, "la", mid_fill))
            y += t1.height
        tr = cv.measure(right, last_style, wr + 1, f"{path}.cells[{last_i}]")
        placed.append((f"{path}.cells[{last_i}]", tr, x + width, 0, "ra", last_fill))
        return placed, max(y, tr.height)
    y = 0
    for i, cell in enumerate(cells):
        style, fill = ((first_style, first_fill) if i == 0 else
                       (last_style, last_fill) if i == last_i else (mid_style, mid_fill))
        t = cv.measure(cell, style, width, f"{path}.cells[{i}]")
        if i:
            y += int(round(t.size * 0.25))
        placed.append((f"{path}.cells[{i}]", t, x, y, "la", fill))
        y += t.height
    return placed, y


def _place_cells(cv: _Canvas, placed: List[Tuple], top: int) -> None:
    for field, t, x, y, anchor, fill in placed:
        cv.put(field, t, x, top + y, anchor, fill)


def _row(cv: _Canvas, row: Mapping[str, Any], path: str, logos: Mapping[str, LogoArt]) -> None:
    side = cv.px(ROW_TILE) if row.get("logo") is not None else 0
    tx = side + TILE_GAP if side else 0
    placed, text_h = _cells(cv, tx, cv.width - tx, list(row["cells"]), path,
                            (CELL_STYLE, CELL_SECONDARY_STYLE, CELL_FIGURE_STYLE),
                            (_rgb(TEXT), MUTED, _rgb(ACCENT)))
    h = max(side, text_h)
    y0 = cv.y
    if side:
        ty = y0 + (h - side) // 2
        cv.tiles.append(((0, ty, side, ty + side), logos[row["logo"]]))
    _place_cells(cv, placed, y0 + (h - text_h) // 2)
    cv.y = y0 + h
    cv.check(path)


def _lay_rows(cv: _Canvas, spec: Mapping[str, Any], logos: Mapping[str, LogoArt]) -> None:
    title_px = cv.px(TITLE_STYLE)
    if spec.get("header") is not None:
        cv.gap(int(round(title_px * 0.5)))
        _header(cv, spec["header"], logos, chip=True)
    cv.para("title", spec["title"], TITLE_STYLE, _rgb(ACCENT), gap=int(round(title_px * 0.5)))
    if spec.get("subtitle") is not None:
        cv.para("subtitle", spec["subtitle"], SUBTITLE_STYLE, MUTED, gap=int(round(title_px * 0.2)))
    row_gap = int(round(cv.px(CELL_STYLE) * 0.45))
    for i, sec in enumerate(spec["sections"]):
        path = f"sections[{i}]"
        cv.gap(int(round(title_px * 0.55)))
        if sec.get("heading") is not None:
            cv.para(f"{path}.heading", sec["heading"], HEADING_STYLE, _rgb(ACCENT))
            cv.gap(int(round(cv.px(HEADING_STYLE) * 0.45)))
        for j, row in enumerate(sec["rows"]):
            if j:
                cv.gap(row_gap)
            _row(cv, row, f"{path}.rows[{j}]", logos)
        if sec.get("more") is not None:
            cv.para(f"{path}.more", sec["more"], NOTE_STYLE, MUTED, gap=int(round(row_gap * 0.8)))
    for k, note in enumerate(spec.get("notes") or ()):
        cv.para(f"notes[{k}]", note, NOTE_STYLE, MUTED, gap=int(round(cv.px(NOTE_STYLE) * (0.9 if k == 0 else 0.4))))


def _lay_spotlight(cv: _Canvas, spec: Mapping[str, Any], logos: Mapping[str, LogoArt]) -> None:
    cv.gap(int(round(cv.px(TITLE_STYLE) * 0.5)))
    _header(cv, spec["header"], logos, chip=True)
    cv.para("figure", spec["figure"], FIGURE_STYLE, _rgb(ACCENT), gap=int(round(cv.px(HEADLINE_STYLE) * 0.7)))
    cv.para("headline", spec["headline"], HEADLINE_STYLE, _rgb(TEXT), gap=int(round(cv.px(HEADLINE_STYLE) * 0.35)))
    for i, line in enumerate(spec.get("lines") or ()):
        cv.para(f"lines[{i}]", line, LINE_STYLE, MUTED, gap=int(round(cv.px(LINE_STYLE) * (0.8 if i == 0 else 0.4))))


def _bar(cv: _Canvas, bar: Mapping[str, Any], path: str) -> None:
    placed, text_h = _cells(cv, 0, cv.width, [bar["label"], bar["value"]], path,
                            (BAR_TEXT_STYLE, BAR_TEXT_STYLE, BAR_TEXT_STYLE),
                            (_rgb(TEXT), MUTED, _rgb(ACCENT)))
    # The cells walk names them label/value (not cells[i]): rename the fields.
    renamed = [(f"{path}.{'label' if f.endswith('cells[0]') else 'value'}", *rest) for f, *rest in placed]
    y0 = cv.y
    _place_cells(cv, renamed, y0)
    bh = cv.px(BAR_HEIGHT)
    ty = y0 + text_h + BAR_GAP
    cv.rects.append(Rect((0, ty, cv.width, ty + bh), bh // 2, TRACK))
    bw = int(round(float(bar["ratio"]) * cv.width))
    if bw > 0:
        outline = OUTLINE_W if bar["style"] == "outline" and bw > 2 * OUTLINE_W else 0
        cv.rects.append(Rect((0, ty, bw, ty + bh), min(bh // 2, bw // 2), _rgb(ACCENT), outline))
    cv.y = ty + bh
    cv.check(path)


def _lay_bars(cv: _Canvas, spec: Mapping[str, Any], logos: Mapping[str, LogoArt]) -> None:
    title_px = cv.px(TITLE_STYLE)
    cv.gap(int(round(title_px * 0.5)))
    _header(cv, spec["header"], logos, chip=False)
    cv.para("title", spec["title"], TITLE_STYLE, _rgb(ACCENT), gap=int(round(title_px * 0.5)))
    cv.para("subtitle", spec["subtitle"], SUBTITLE_STYLE, MUTED, gap=int(round(title_px * 0.2)))
    bar_gap = int(round(cv.px(BAR_TEXT_STYLE) * 0.6))
    for key in ("segments", "flow"):
        cv.gap(int(round(title_px * 0.5)))
        if key == "flow":
            cv.rects.append(Rect((0, cv.y, cv.width, cv.y + 2), 0, DIVIDER))
            cv.y += 2
            cv.gap(int(round(title_px * 0.4)))
        for i, bar in enumerate(spec[key]):
            if i:
                cv.gap(bar_gap)
            _bar(cv, bar, f"{key}[{i}]")
    cv.para("callout", spec["callout"], CALLOUT_STYLE, _rgb(TEXT), gap=int(round(title_px * 0.55)))


def _side_art(side: Mapping[str, Any], logos: Mapping[str, LogoArt], path: str) -> LogoArt:
    """The plate a pair side draws: its logo entry — or, for a side with no logo key (a private
    investee has no symbol), a WORDMARK of its own name. That name is already one of the spec's
    strings (`<path>.name`), so the plate adds nothing to declare; its lines are field
    "wordmark:<path>.name"."""
    if side.get("logo") is not None:
        return logos[side["logo"]]
    return LogoArt(key=f"{path}.name", name=side["name"])


def _arrow(x0: int, x1: int, cy: int, thick: int, head: int, half_h: int) -> Tuple[Tuple[int, int], ...]:
    """A left-to-right arrow from x0 to x1 centred on row cy: a `thick` px shaft and a `head` px long
    triangular head `2 * half_h` px tall — one closed polygon (a shape, never a glyph)."""
    t0, t1, hx = cy - thick // 2, cy - thick // 2 + thick, x1 - head
    return ((x0, t0), (hx, t0), (hx, cy - half_h), (x1, cy), (hx, cy + half_h), (hx, t1), (x0, t1))


def _lay_pair(cv: _Canvas, spec: Mapping[str, Any], logos: Mapping[str, LogoArt]) -> None:
    """[investor plate] → [investee plate], each name centred under its plate, then the figure,
    its label and the lines, centred. Each side owns half the column."""
    cv.gap(int(round(cv.px(TITLE_STYLE) * 0.6)))
    side = cv.px(PAIR_TILE)
    half = (cv.width - PAIR_HALF_GAP) // 2
    if side > half:
        raise _NoFit(f"pair: a {side}px plate is wider than its half column ({half}px)")
    centres = (half // 2, cv.width - half // 2)
    y0 = cv.y
    boxes: List[Box] = []
    for c, key in zip(centres, ("left", "right")):
        x0 = c - side // 2
        boxes.append((x0, y0, x0 + side, y0 + side))
        cv.tiles.append((boxes[-1], _side_art(spec[key], logos, key)))
    thick = max(6, side // 18)
    head, half_h = int(round(thick * 2.4)), int(round(thick * 1.7))
    ax0, ax1 = boxes[0][2] + ARROW_PAD, boxes[1][0] - ARROW_PAD
    if ax1 - ax0 < head + ARROW_MIN_SHAFT:
        raise _NoFit(f"pair: {ax1 - ax0}px between the plates, too little for the arrow")
    cv.shapes.append(_arrow(ax0, ax1, y0 + side // 2, thick, head, half_h))
    cv.y = y0 + side
    cv.check("pair plates")
    names = [cv.measure(spec[k]["name"], PAIR_NAME_STYLE, half, f"{k}.name") for k in ("left", "right")]
    cv.gap(int(round(cv.px(PAIR_NAME_STYLE) * 0.5)))
    for c, key, t in zip(centres, ("left", "right"), names):
        cv.put(f"{key}.name", t, c, cv.y, "ma", _rgb(TEXT))
    cv.y += max(t.height for t in names)
    cv.check("pair names")
    label_px = cv.px(PAIR_LABEL_STYLE)
    cv.para("figure", spec["figure"], PAIR_FIGURE_STYLE, _rgb(ACCENT), gap=int(round(label_px * 0.8)), center=True)
    cv.para("label", spec["label"], PAIR_LABEL_STYLE, _rgb(TEXT), gap=int(round(label_px * 0.25)), center=True)
    for i, line in enumerate(spec.get("lines") or ()):
        cv.para(f"lines[{i}]", line, LINE_STYLE, MUTED, center=True,
                gap=int(round(cv.px(LINE_STYLE) * (0.8 if i == 0 else 0.35))))


def _lay_grid(cv: _Canvas, spec: Mapping[str, Any], logos: Mapping[str, LogoArt]) -> None:
    """Title, subtitle, then the tiles in GRID_COLUMNS columns, row by row (a row as tall as its
    tallest tile; each tile's plate and text centred on each other), then the "+k more" line. A
    tile whose logo is a wordmark leaves its plate slot empty (below)."""
    title_px = cv.px(TITLE_STYLE)
    cv.para("title", spec["title"], TITLE_STYLE, _rgb(ACCENT), gap=int(round(title_px * 0.5)))
    cv.para("subtitle", spec["subtitle"], SUBTITLE_STYLE, MUTED, gap=int(round(title_px * 0.2)))
    col_w = (cv.width - (GRID_COLUMNS - 1) * GRID_COL_GAP) // GRID_COLUMNS
    side = cv.px(GRID_TILE)
    text_x = side + GRID_TEXT_GAP
    text_w = col_w - text_x
    if text_w <= 0:
        raise _NoFit(f"grid: no room for text beside a {side}px plate in a {col_w}px column")
    row_gap = cv.px(GRID_ROW_GAP)
    tiles = list(spec["tiles"])
    cv.gap(int(round(title_px * 0.55)))
    for r0 in range(0, len(tiles), GRID_COLUMNS):
        if r0:
            cv.gap(row_gap)
        row = []
        for c, tile in enumerate(tiles[r0:r0 + GRID_COLUMNS]):
            path = f"tiles[{r0 + c}]"
            name = cv.measure(tile["name"], GRID_NAME_STYLE, text_w, f"{path}.name")
            line = (cv.measure(tile["line"], GRID_LINE_STYLE, text_w, f"{path}.line")
                    if tile.get("line") is not None else None)
            inner = int(round(line.size * 0.3)) if line else 0
            text_h = name.height + (inner + line.height if line else 0)
            row.append((c, path, tile, name, line, inner, text_h))
        y0 = cv.y
        for c, path, tile, name, line, inner, text_h in row:
            x0 = c * (col_w + GRID_COL_GAP)
            h = max(side, text_h)
            ty = y0 + (h - side) // 2
            art = logos[tile["logo"]]
            # A wordmark (missing or unverified logo) draws NO plate here: the tile's own name is
            # already beside the slot, and a name rarely fits a grid plate legibly (a blank white
            # square). Its name stays declared (declared ⊇ drawn); the slot keeps the column aligned.
            if not art.wordmark:
                cv.tiles.append(((x0, ty, x0 + side, ty + side), art))
            top = y0 + (h - text_h) // 2
            cv.put(f"{path}.name", name, x0 + text_x, top, "la", _rgb(TEXT))
            if line is not None:
                cv.put(f"{path}.line", line, x0 + text_x, top + name.height + inner, "la", MUTED)
        cv.y = y0 + max(max(side, r[6]) for r in row)
        cv.check(f"tiles row {r0 // GRID_COLUMNS}")
    if spec.get("more") is not None:
        cv.para("more", spec["more"], NOTE_STYLE, MUTED, gap=int(round(row_gap * 1.2)))


Walker = Callable[[_Walker, Mapping[str, Any]], None]
Placer = Callable[[_Canvas, Mapping[str, Any], Mapping[str, LogoArt]], None]
#: Everything this worker can DRAW — the contract's five layouts (pinned == cards.LAYOUTS): the
#: walker that checks a spec (the server's mirror) and the placer that lays it out.
DRAWERS: Dict[str, Tuple[Walker, Placer]] = {
    "rows": (_w_rows, _lay_rows), "spotlight": (_w_spotlight, _lay_spotlight), "pair": (_w_pair, _lay_pair),
    "bars": (_w_bars, _lay_bars), "grid": (_w_grid, _lay_grid),
}
#: What it ACCEPTS: only the SHIPPED layouts (cards.SHIPPED_LAYOUTS mirrors the server's). A layout
#: in SHIPPED_LAYOUTS that this worker cannot draw fails the import (KeyError) — never at render time.
_WALKERS: Dict[str, Walker] = {k: DRAWERS[k][0] for k in cards.SHIPPED_LAYOUTS}
_LAYOUTS: Dict[str, Placer] = {k: DRAWERS[k][1] for k in cards.SHIPPED_LAYOUTS}


def _place(image: TemplateImage, step: int, font_path: str, engine: str) -> TemplateLayout:
    W, H, M = cards.IMAGE_WIDTH, cards.IMAGE_HEIGHT, cards.IMAGE_MARGIN
    col_w = W - 2 * M
    # The footer first: it sits on the page at the bottom, and what it leaves is the panel's.
    foot = cards._Stack(col_w, cards.IMAGE_FOOTER_MAX_H, "center", font_path, engine, step)
    foot.text("footer", image.footer, cards.IMAGE_FOOTER_STYLE, cards.FOOTER_FILL)
    footer_top = H - M - foot.y
    area_top, area_h = M, footer_top - cards.IMAGE_FOOTER_GAP - M
    pad_x, pad_y = cards.IMAGE_PANEL_PAD_X, cards.IMAGE_PANEL_PAD_Y
    cv = _Canvas(col_w - 2 * pad_x, area_h - 2 * pad_y, font_path, engine, step)
    h = cv.pill("kicker", image.spec["kicker"], KICKER_STYLE, 0, 0)
    cv.y = h
    _LAYOUTS[image.layout](cv, image.spec, image.logos)
    panel_h = cv.y + 2 * pad_y
    top = area_top + (area_h - panel_h) // 2
    panel: Box = (M, top, W - M, top + panel_h)
    footer_zone: Box = (M, footer_top, W - M, H - M)
    dx, dy = M + pad_x, top + pad_y

    def shift(b: Box) -> Box:
        return b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy

    lines: List[DrawnLine] = []
    for stack_lines, ox, oy, bounds in ((cv.lines, dx, dy, panel), (foot.lines, M, footer_top, footer_zone)):
        for name, text, size, x, y, anchor, fill in stack_lines:
            l, t, r, b = _font(font_path, size, engine).getbbox(text, anchor=anchor)
            ink = (x + ox + l, y + oy + t, x + ox + r, y + oy + b)
            if not _within(ink, bounds):
                raise _NoFit(f"{name}: the ink of {text[:40]!r} at {size}px leaves its area")
            lines.append(DrawnLine(name, text, size, x + ox, y + oy, anchor, fill, ink))
    rects = tuple(Rect(shift(r.box), r.radius, r.fill, r.outline) for r in cv.rects)
    tiles = tuple((shift(b), art) for b, art in cv.tiles)
    shapes = tuple(tuple((x + dx, y + dy) for x, y in poly) for poly in cv.shapes)
    for r in rects:
        if not _within(r.box, panel):
            raise _NoFit("a bar or pill leaves the panel")      # defence in depth
    for poly in shapes:
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        if not _within((min(xs), min(ys), max(xs), max(ys)), panel):
            raise _NoFit("a shape leaves the panel")            # defence in depth
    for b, art in tiles:
        if not _within(b, panel):
            raise _NoFit(f"the plate of {art.key} leaves the panel")
        if art.wordmark:
            lines.extend(cards.wordmark_lines(art, b, font_path, engine))
    if footer_zone[1] < panel[3] + cards.IMAGE_FOOTER_GAP or panel[1] < M:
        raise _NoFit("the panel or the footer leave their areas")   # defence in depth
    return TemplateLayout(kind=f"template:{image.layout}", step=step, engine=engine, panel=panel,
                          rects=rects, tiles=tiles, lines=tuple(lines), shapes=shapes)


def layout_template(image: TemplateImage, *, font_path: str, layout_engine: Optional[str] = None) -> TemplateLayout:
    """Where everything on the template image goes: the largest ramp step at which every string
    fits whole. CardOverflow (with the floor step's reason) when none does."""
    engine = cards.resolve_layout_engine(layout_engine)
    reason = ""
    for step in range(cards.RAMP_STEPS + 1):
        try:
            layout = _place(image, step, font_path, engine)
        except _NoFit as e:
            reason = str(e)
            continue
        blank = cards.blank_wordmarks(layout)
        if blank:
            logger.warning("template image (%s): blank plate for %s — the company name does not fit its "
                           "plate legibly (the name stays declared, never drawn clipped)", image.layout, blank)
        return layout
    raise CardOverflow(f"template image ({image.layout}) does not fit at the floor sizes: {reason}")


def _outline_rounded(img, box: Box, radius: int, colour: Tuple[int, int, int], width: int) -> None:
    from PIL import Image, ImageDraw

    w, h = box[2] - box[0], box[3] - box[1]
    a = cards._AA
    big = Image.new("L", (w * a, h * a), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * a - 1, h * a - 1), radius=radius * a, outline=255,
                                          width=width * a)
    img.paste(Image.new("RGB", (w, h), colour), (box[0], box[1]), big.reduce(a))


def _fill_polygon(img, points: Sequence[Tuple[int, int]], colour: Tuple[int, int, int]) -> None:
    """An anti-aliased filled polygon (drawn at cards._AA× and box-reduced: deterministic)."""
    from PIL import Image, ImageDraw

    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, y0 = min(xs), min(ys)
    w, h = max(xs) - x0, max(ys) - y0
    if w <= 0 or h <= 0:
        return
    a = cards._AA
    big = Image.new("L", (w * a, h * a), 0)
    ImageDraw.Draw(big).polygon([((x - x0) * a, (y - y0) * a) for x, y in points], fill=255)
    img.paste(Image.new("RGB", (w, h), colour), (x0, y0), big.reduce(a))


def draw_template(image: TemplateImage, *, font_path: str, layout_engine: Optional[str] = None):
    """(the template image as an RGB PIL image, its layout) — before any JPEG encoding, so tests can
    check its pixels exactly. CardOverflow / CardAssetError as for a card."""
    from PIL import Image, ImageDraw

    layout = layout_template(image, font_path=font_path, layout_engine=layout_engine)
    img = Image.new("RGB", (cards.IMAGE_WIDTH, cards.IMAGE_HEIGHT), _rgb(PAGE))
    cards._fill_rounded(img, layout.panel, cards.PANEL_RADIUS, _rgb(CARD))
    for r in layout.rects:
        w, h = r.box[2] - r.box[0], r.box[3] - r.box[1]
        if w <= 0 or h <= 0:
            continue
        if r.outline:
            _outline_rounded(img, r.box, r.radius, r.fill, r.outline)
        elif r.radius:
            cards._fill_rounded(img, r.box, r.radius, r.fill)
        else:
            img.paste(Image.new("RGB", (w, h), r.fill), r.box[:2])
    for poly in layout.shapes:
        _fill_polygon(img, poly, _rgb(ACCENT))
    for box, art in layout.tiles:
        cards.draw_plate(img, box, art)
    draw = ImageDraw.Draw(img)
    for ln in layout.lines:
        draw.text((ln.x, ln.y), ln.text, font=_font(font_path, ln.size, layout.engine), fill=ln.fill,
                  anchor=ln.anchor)
    return img, layout


def check_glyphs(image: TemplateImage, font_path: str) -> None:
    """cards.MissingGlyphs naming every character of every string the image MAY draw (every logo's
    name included: a logo that fails later still has its wordmark drawable)."""
    cards.check_glyphs([], font_path, extra=image.allowed)


def render_template_image(image: TemplateImage, *, font_path: str, layout_engine: Optional[str] = None,
                          max_bytes: int = cards.POST_IMAGE_MAX_BYTES) -> "cards.RenderedImage":
    """The template image as a baseline JPEG of at most `max_bytes` (cards.JPEG_QUALITIES steps
    down until it fits). cards.ImageTooLarge when none does; CardOverflow when the text cannot fit
    whole; CardAssetError when the font or a verified logo is unreadable."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError(f"max_bytes must be a positive int, not {max_bytes!r}")
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
