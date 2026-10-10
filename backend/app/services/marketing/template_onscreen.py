"""
The closed on-screen allow-list of a TEMPLATE (news) post — drop 2, contract D9 (2026-10-09).

A news day's image and its video's opening card are drawn by the worker from two code-owned
structures the templates compose (`news_templates.compose`):

* `image_spec` — the 1080×1350 post image: one of five generic LAYOUTS (`rows`, `spotlight`,
  `pair`, `bars`, `grid`) plus the common `{layout, version, kicker, footer}`;
* `opening_card` — the video's first card: `{kicker, logos[1..2], chip?, figure?, headline}`.

Both are CLOSED schemas: an unknown key, a missing key, a string that is not drawable, a list
outside its bounds, a ratio that is not a finite number in [0, 1], a style outside BAR_STYLES or
a logo key the run does not carry is refused (`validate_image_spec` / `validate_opening_card`
return the first problem, None when the structure is valid). The server cannot read pixels, so
what a well-behaved worker may DRAW is exactly the strings these structures hold, in draw order,
plus each referenced logo's company name (the wordmark tile drawn when the logo itself is missing
or unverified) — `image_strings` / `opening_strings`. `run_service` checks the worker's declared
on-screen text against them (`_check_post_image_text`, `_check_onscreen_text`); the worker
mirrors LAYOUTS and the walk (`marketing/cards.py`, pinned equal by tests).

Rules every drawable string obeys: 1..ONSCREEN_TEXT_MAX_CHARS characters, no padding, one line,
already canonical (`compliance.clean(s) == s`), and never a URL or a content hash — a logo entry's
`url` and `sha256` are never read here, and a string carrying "://", "www." or a 32+ hex run is
refused. At most ONSCREEN_TEXT_MAX strings in all (the logo names counted), so the worker can
always declare everything it may draw.

Phasing: 2a shipped `rows`, `spotlight`, `bars` and the opening card. Drop 2b (2026-10-10) ships
`pair` (company_stakes) and `grid` (theme_explainer) with their series: SHIPPED_LAYOUTS here and
`cards.SHIPPED_LAYOUTS` in the worker were flipped together, in the change that added those series to
`selection.SHIPPED_SERIES` (the schema note `company-weekly-drop2b-layouts.md` lists the steps; deploy
the WORKER first — an old worker refuses a pair / grid loudly). A layout outside SHIPPED_LAYOUTS is
refused as `layout_not_shipped`: a spec the worker cannot draw must never reach it (it would fail
loudly, never fall back to the lesson layout).

Drop 2b, a pair's investee may carry NO logo key (`right: {name, logo?}`): a private company has no
symbol, so no `output.logos` entry — the worker then draws its name as a wordmark plate, and that
name is already one of the spec's own strings (PAIR_SIDE_KEYS). The investor (`left`) always has one.

Pure: stdlib + `compliance.clean` + the on-screen caps of `schemas.marketing`. No FMP, no I/O.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Collection, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from app.schemas.marketing import ONSCREEN_TEXT_MAX, ONSCREEN_TEXT_MAX_CHARS
from app.services.marketing.compliance import clean

logger = logging.getLogger(__name__)

#: The five generic layouts (contract D9); the worker mirrors this tuple.
LAYOUTS: Tuple[str, ...] = ("rows", "spotlight", "pair", "bars", "grid")
#: What validates (the worker mirrors it as `cards.SHIPPED_LAYOUTS`, same members, same order). Drop
#: 2a shipped `rows`, `spotlight`, `bars`; drop 2b (2026-10-10) ships `pair` and `grid` with their
#: series (company_stakes, theme_explainer). A layout dropped from here answers `layout_not_shipped`.
SHIPPED_LAYOUTS: Tuple[str, ...] = ("rows", "spotlight", "pair", "bars", "grid")
SPEC_VERSION = 1

MAX_LOGOS = 12          # distinct logo keys one image may reference
MAX_ROWS = 8            # rows across every section of a `rows` image
MAX_SECTIONS = 3
MAX_SECTION_ROWS = 5
MAX_CELLS = 3
MAX_NOTES = 2
MAX_LINES = 2
MIN_BARS, MAX_BARS = 2, 7
MIN_FLOW, MAX_FLOW = 2, 4
MIN_TILES, MAX_TILES = 3, 12
MAX_OPENING_LOGOS = 2
#: A logo key is a canonical symbol ("BRK-B"); never drawn, so only bounded here.
LOGO_KEY_MAX_CHARS = 16
BAR_STYLES: Tuple[str, ...] = ("fill", "outline")

#: Closed keys: (required, optional). An optional key may be absent or null.
COMMON_KEYS: Tuple[str, ...] = ("layout", "version", "kicker", "footer")
LAYOUT_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "rows": (("title", "sections"), ("header", "subtitle", "notes")),
    "spotlight": (("header", "figure", "headline"), ("lines",)),
    "pair": (("left", "right", "figure", "label"), ("lines",)),
    "bars": (("header", "title", "subtitle", "segments", "flow", "callout"), ()),
    "grid": (("title", "subtitle", "tiles"), ("more",)),
}
OPENING_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("kicker", "logos", "headline"), ("chip", "figure"))
#: A `pair`'s two sides (drop 2b, company_stakes), (required, optional): the investor is a listed
#: company and always references its logo entry; the investee may be private (no symbol, so no logo
#: entry) — then the worker draws its `name` as a wordmark plate.
PAIR_SIDE_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "left": (("logo", "name"), ()),
    "right": (("name",), ("logo",)),
}
#: A `grid` tile (drop 2b, theme_explainer), (required, optional): the member's logo, its company
#: name, and an optional one-line caption (e.g. its largest segment and that segment's share).
GRID_TILE_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("logo", "name"), ("line",))

#: One line: every character `str.splitlines()` splits on, and a tab.
_LINE_BREAKS = "\t\r\n\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
#: A link or a content hash is never drawable (a logo's url / sha256 must not become on-screen text).
_NOT_DRAWABLE_RE = re.compile(r"://|\bwww\.|[0-9A-Fa-f]{32,}", re.IGNORECASE)


def _fail(code: str, path: str, detail: str = "") -> None:
    raise _Walk.Invalid(f"{code}: {path}" + (f" ({detail})" if detail else ""))


def drawable_problem(value: Any) -> Optional[str]:
    """Why `value` cannot be drawn as one on-screen string, or None. Pure."""
    if not isinstance(value, str):
        return f"not a string ({type(value).__name__})"
    if not value or not value.strip():
        return "blank"
    if len(value) > ONSCREEN_TEXT_MAX_CHARS:
        return f"{len(value)} chars (max {ONSCREEN_TEXT_MAX_CHARS})"
    if value != value.strip():
        return "padded"
    if any(ch in _LINE_BREAKS for ch in value):
        return "not one line"
    if clean(value) != value:
        return "not canonical (invisible, control or non-NFKC characters)"
    if _NOT_DRAWABLE_RE.search(value):
        return "a link or a content hash"
    return None


class _Walk:
    """Validates a structure while recording what it draws, in draw order: a string, or a
    ("logo", key) marker the logo's company name stands for (its wordmark)."""

    class Invalid(Exception):
        """The first thing wrong with a structure (`code: path (detail)`). Internal control flow
        only: every public function catches it and returns the problem (or [] for the string
        lists), so it never reaches an endpoint or `classify_exception` — which is why it is
        nested here, not a module-level marketing exception class."""

    def __init__(self, logo_keys: Collection[str]) -> None:
        self.keys = _key_set(logo_keys)
        self.out: List[Any] = []
        self.strings = 0
        self.logos: List[str] = []

    def obj(self, value: Any, path: str, required: Sequence[str], optional: Sequence[str]) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            _fail("not_an_object", path, type(value).__name__)
        allowed = set(required) | set(optional)
        for k in value:
            if not isinstance(k, str) or k not in allowed:
                _fail("unknown_key", f"{path}.{str(k)[:40]}")
        for k in required:
            if value.get(k) is None:
                _fail("missing_key", f"{path}.{k}")
        return value

    def text(self, value: Any, path: str) -> None:
        problem = drawable_problem(value)
        if problem:
            _fail("bad_string", path, problem)
        self.strings += 1
        self.out.append(value)

    def opt_text(self, obj: Mapping[str, Any], key: str, path: str) -> None:
        if obj.get(key) is not None:
            self.text(obj[key], f"{path}.{key}" if path else key)

    def logo(self, value: Any, path: str) -> None:
        if not isinstance(value, str) or not value or len(value) > LOGO_KEY_MAX_CHARS:
            _fail("bad_logo_key", path)
        if value not in self.keys:
            _fail("unknown_logo", path, value)
        if value not in self.logos:
            self.logos.append(value)
            if len(self.logos) > MAX_LOGOS:
                _fail("too_many_logos", path, f"> {MAX_LOGOS}")
        self.out.append(("logo", value))

    @staticmethod
    def seq(value: Any, path: str, lo: int, hi: int) -> Sequence[Any]:
        if not isinstance(value, (list, tuple)):
            _fail("bad_list", path, type(value).__name__)
        if not lo <= len(value) <= hi:
            _fail("bad_count", path, f"{len(value)} not in {lo}..{hi}")
        return value

    def opt_seq(self, obj: Mapping[str, Any], key: str, path: str, hi: int) -> Sequence[Any]:
        value = obj.get(key)
        return () if value is None else self.seq(value, path, 0, hi)


def _key_set(logo_keys: Any) -> FrozenSet[str]:
    if logo_keys is None or isinstance(logo_keys, (str, bytes)):
        return frozenset()   # a bare string is not a collection of keys ("AAPL" is not {"A", "P", "L"})
    try:
        return frozenset(k for k in logo_keys if isinstance(k, str))
    except TypeError:
        return frozenset()


# ── image_spec ────────────────────────────────────────────────────────────────


def _company(w: _Walk, value: Any, path: str, *, chip: bool) -> None:
    """A `{logo, name, chip?}` header (chip=False: a `bars` header): the logo tile, then its name."""
    obj = w.obj(value, path, ("logo", "name"), ("chip",) if chip else ())
    w.logo(obj["logo"], f"{path}.logo")
    w.text(obj["name"], f"{path}.name")
    if chip:
        w.opt_text(obj, "chip", path)


def _lines(w: _Walk, spec: Mapping[str, Any], key: str, hi: int) -> None:
    for i, line in enumerate(w.opt_seq(spec, key, key, hi)):
        w.text(line, f"{key}[{i}]")


def _rows(w: _Walk, spec: Mapping[str, Any]) -> None:
    if spec.get("header") is not None:
        _company(w, spec["header"], "header", chip=True)
    w.text(spec["title"], "title")
    w.opt_text(spec, "subtitle", "")
    total = 0
    for i, section in enumerate(w.seq(spec["sections"], "sections", 1, MAX_SECTIONS)):
        path = f"sections[{i}]"
        sec = w.obj(section, path, ("rows",), ("heading", "more"))
        w.opt_text(sec, "heading", path)
        rows = w.seq(sec["rows"], f"{path}.rows", 1, MAX_SECTION_ROWS)
        total += len(rows)
        if total > MAX_ROWS:
            _fail("too_many_rows", "sections", f"> {MAX_ROWS}")
        for j, raw in enumerate(rows):
            rpath = f"{path}.rows[{j}]"
            row = w.obj(raw, rpath, ("cells",), ("logo",))
            if row.get("logo") is not None:
                w.logo(row["logo"], f"{rpath}.logo")
            for k, cell in enumerate(w.seq(row["cells"], f"{rpath}.cells", 1, MAX_CELLS)):
                w.text(cell, f"{rpath}.cells[{k}]")
        w.opt_text(sec, "more", path)
    _lines(w, spec, "notes", MAX_NOTES)


def _spotlight(w: _Walk, spec: Mapping[str, Any]) -> None:
    _company(w, spec["header"], "header", chip=True)
    w.text(spec["figure"], "figure")
    w.text(spec["headline"], "headline")
    _lines(w, spec, "lines", MAX_LINES)


def _side(w: _Walk, value: Any, path: str) -> None:
    """A pair side (`left` / `right`, PAIR_SIDE_KEYS): its logo tile when it has one, then its name."""
    required, optional = PAIR_SIDE_KEYS[path]
    obj = w.obj(value, path, required, optional)
    if obj.get("logo") is not None:
        w.logo(obj["logo"], f"{path}.logo")
    w.text(obj["name"], f"{path}.name")


def _pair(w: _Walk, spec: Mapping[str, Any]) -> None:
    _side(w, spec["left"], "left")
    _side(w, spec["right"], "right")
    w.text(spec["figure"], "figure")
    w.text(spec["label"], "label")
    _lines(w, spec, "lines", MAX_LINES)


def _ratio_problem(value: Any) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"not a number ({type(value).__name__})"
    if isinstance(value, float) and not math.isfinite(value):
        return f"{value!r} is not finite"
    # Compared exactly, never converted: a huge int (10**400) is out of range, not an OverflowError.
    if not 0 <= value <= 1:
        return f"{str(value)[:20]} not in [0, 1]"
    return None


def _bar_list(w: _Walk, spec: Mapping[str, Any], key: str, lo: int, hi: int) -> None:
    for i, raw in enumerate(w.seq(spec[key], key, lo, hi)):
        path = f"{key}[{i}]"
        bar = w.obj(raw, path, ("label", "value", "ratio", "style"), ())
        w.text(bar["label"], f"{path}.label")
        w.text(bar["value"], f"{path}.value")
        problem = _ratio_problem(bar["ratio"])
        if problem:
            _fail("bad_ratio", f"{path}.ratio", problem)
        if not isinstance(bar["style"], str) or bar["style"] not in BAR_STYLES:
            _fail("bad_style", f"{path}.style", str(bar["style"])[:20])


def _bars(w: _Walk, spec: Mapping[str, Any]) -> None:
    _company(w, spec["header"], "header", chip=False)
    w.text(spec["title"], "title")
    w.text(spec["subtitle"], "subtitle")
    _bar_list(w, spec, "segments", MIN_BARS, MAX_BARS)
    _bar_list(w, spec, "flow", MIN_FLOW, MAX_FLOW)
    w.text(spec["callout"], "callout")


def _grid(w: _Walk, spec: Mapping[str, Any]) -> None:
    w.text(spec["title"], "title")
    w.text(spec["subtitle"], "subtitle")
    required, optional = GRID_TILE_KEYS
    for i, raw in enumerate(w.seq(spec["tiles"], "tiles", MIN_TILES, MAX_TILES)):
        path = f"tiles[{i}]"
        tile = w.obj(raw, path, required, optional)
        w.logo(tile["logo"], f"{path}.logo")
        w.text(tile["name"], f"{path}.name")
        w.opt_text(tile, "line", path)
    w.opt_text(spec, "more", "")


_WALKERS = {"rows": _rows, "spotlight": _spotlight, "pair": _pair, "bars": _bars, "grid": _grid}


def _walk_image_spec(spec: Any, logo_keys: Collection[str], *, shipped: Collection[str],
                     footer: Optional[str] = None) -> _Walk:
    if not isinstance(spec, dict):
        _fail("not_an_object", "image_spec", type(spec).__name__)
    layout = spec.get("layout")
    if not isinstance(layout, str) or layout not in LAYOUTS:
        _fail("unknown_layout", "layout", str(layout)[:40])
    if layout not in shipped:
        _fail("layout_not_shipped", "layout", layout)
    w = _Walk(logo_keys)
    required, optional = LAYOUT_KEYS[layout]
    w.obj(spec, "image_spec", COMMON_KEYS + required, optional)
    version = spec["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != SPEC_VERSION:
        _fail("bad_version", "version", f"{str(version)[:20]} != {SPEC_VERSION}")
    w.text(spec["kicker"], "kicker")              # drawn first (the pill at the top)
    _WALKERS[layout](w, spec)
    w.text(spec["footer"], "footer")              # drawn last (bottom-anchored)
    if footer is not None and spec["footer"] != footer:
        _fail("footer_mismatch", "footer", "not the output's image_footer")
    if w.strings + len(w.logos) > ONSCREEN_TEXT_MAX:
        _fail("too_many_strings", "image_spec", f"{w.strings} strings + {len(w.logos)} logo names "
                                                f"> {ONSCREEN_TEXT_MAX}")
    return w


def validate_image_spec(spec: Any, logo_keys: Collection[str], *, footer: Optional[str] = None) -> Optional[str]:
    """The first problem of a template post image's `image_spec`, or None when it is drawable.

    `logo_keys`: the keys the run carries a logo entry for (`logo_keys(output["logos"])`, or at
    compose time the record's symbols) — a reference to any other key is refused. `footer`: when
    given, `spec["footer"]` must equal it (the output's `image_footer`, which the server requires
    among the drawn strings). Only SHIPPED_LAYOUTS validate (all five since drop 2b); any other
    layout answers `layout_not_shipped` (both sides' SHIPPED_LAYOUTS move together). Pure."""
    try:
        _walk_image_spec(spec, logo_keys, shipped=SHIPPED_LAYOUTS, footer=footer)
    except _Walk.Invalid as e:
        return str(e)
    return None


# ── logos (the wordmark names) ────────────────────────────────────────────────


def _logo_names(logos: Any) -> Dict[str, str]:
    """{key: company name} of the well-formed entries of `output.logos` (`{key, name, url|None,
    sha256|None, …}`): a bounded string key and a drawable name. The first entry of a key wins;
    anything else is skipped (a reference to it is then an unknown logo — fail closed). Only `key`
    and `name` are read: a url or a hash is never drawable."""
    out: Dict[str, str] = {}
    if not isinstance(logos, (list, tuple)):
        return out
    for entry in logos:
        if not isinstance(entry, dict):
            continue
        key, name = entry.get("key"), entry.get("name")
        if (isinstance(key, str) and key and len(key) <= LOGO_KEY_MAX_CHARS and key not in out
                and drawable_problem(name) is None):
            out[key] = name
    return out


def logo_keys(logos: Any) -> FrozenSet[str]:
    """The keys of `output.logos` a spec or an opening card may reference: exactly the entries
    whose company name the worker could draw as a wordmark. Pass this to the validators."""
    return frozenset(_logo_names(logos))


def _resolve(out: Sequence[Any], names: Mapping[str, str]) -> List[str]:
    """The walk's draw order with each logo marker replaced by its company name, de-duplicated
    (first occurrence wins)."""
    seen: List[str] = []
    for item in out:
        text = names[item[1]] if isinstance(item, tuple) else item
        if text not in seen:
            seen.append(text)
    return seen


def image_strings(spec: Any, logos: Any) -> List[str]:
    """Every string a template post image may draw, in draw order, de-duplicated: the spec's own
    strings (kicker first, footer last) with each referenced logo's company name at the logo's
    position (the wordmark tile drawn when the logo is missing). The server's allow-list for the
    worker's declared on-screen text. An invalid spec (a refused layout included) allows NOTHING:
    [] and an ERROR — fail closed, never a guess at what was meant. Deterministic and pure."""
    names = _logo_names(logos)
    try:
        w = _walk_image_spec(spec, names, shipped=SHIPPED_LAYOUTS)
    except _Walk.Invalid as e:
        logger.error("template_onscreen: image_spec refused, no string is drawable: %s", e)
        return []
    return _resolve(w.out, names)


# ── opening_card ──────────────────────────────────────────────────────────────


def _walk_opening(card: Any, logo_keys: Collection[str]) -> _Walk:
    w = _Walk(logo_keys)
    required, optional = OPENING_KEYS
    w.obj(card, "opening_card", required, optional)
    w.text(card["kicker"], "opening_card.kicker")          # the badge at the top
    keys = w.seq(card["logos"], "opening_card.logos", 1, MAX_OPENING_LOGOS)
    for i, key in enumerate(keys):
        if key in keys[:i]:
            _fail("duplicate_logo", f"opening_card.logos[{i}]")
        w.logo(key, f"opening_card.logos[{i}]")
    w.opt_text(card, "chip", "opening_card")                # under the logo
    w.opt_text(card, "figure", "opening_card")
    w.text(card["headline"], "opening_card.headline")
    return w


def validate_opening_card(card: Any, logo_keys: Collection[str]) -> Optional[str]:
    """The first problem of a template video's `opening_card` (`{kicker, logos[1..2], chip?,
    figure?, headline}`, closed), or None when it is drawable. Pure."""
    try:
        _walk_opening(card, logo_keys)
    except _Walk.Invalid as e:
        return str(e)
    return None


def opening_strings(card: Any, logos: Any) -> List[str]:
    """Every string a template video's opening card may draw, in draw order (kicker, the logos'
    company names, chip, figure, headline), de-duplicated. An invalid card allows nothing ([] and
    an ERROR). Deterministic and pure."""
    names = _logo_names(logos)
    try:
        w = _walk_opening(card, names)
    except _Walk.Invalid as e:
        logger.error("template_onscreen: opening_card refused, no string is drawable: %s", e)
        return []
    return _resolve(w.out, names)
