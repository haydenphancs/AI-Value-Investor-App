"""
Drop 2b (contract D9 / D14, templates design §5.2 / §5.4 / §5.6 / §5.7 / §6) — the post-image
layouts the four 2b series need, on both sides: the server's closed schema
(`app/services/marketing/template_onscreen.py`) and the worker's drawing (`marketing/news_layouts.py`,
mirrors in `marketing/cards.py`).

* **pair** (company_stakes): investor plate → arrow → investee plate, both names, the figure, its
  label (the stake's verb) and ≤ 2 short lines. The investee may carry NO logo key (a private
  company has no symbol): the worker then draws its own name as a wordmark plate.
* **grid** (theme_explainer): 3..12 member tiles (logo, name, optional caption line) and a "+k more"
  line. A wordmark (missing logo) draws no plate in a grid — the name is already beside the slot.
* **congress_count** reuses `spotlight`, **earnings** reuses `rows`: both are pinned here as specs the
  shipped validators already accept and the worker already draws.
* The opening card covers all four series unchanged.

The gate: both sides accept only SHIPPED_LAYOUTS. Drop 2b (2026-10-10) flipped the literal on each
side together with `selection.SHIPPED_SERIES` (company_stakes → pair, theme_explainer → grid), so every
layout is shipped; production still runs a 2b series only while MARKETING_NEWS_SERIES lists it. The
`shipped_2b` fixture restates that state (kept so these proofs hold whatever ships);
`test_the_worker_literal_ships_every_layout_in_a_fresh_interpreter` proves the worker side at import,
and `test_pair_and_grid_outside_shipped_layouts_are_refused_on_both_sides` that a rolled-back layout is
refused loudly on both sides.

Hermetic: Pillow, the vendored Inter Bold and generated PNGs; no network. `app.*` is imported by the
TESTS only (the worker package never imports it).
"""

from __future__ import annotations

import ast
import copy
import io
import json
import logging
import math
import random
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PIL = pytest.importorskip("PIL")
np = pytest.importorskip("numpy")
from PIL import Image, ImageDraw  # noqa: E402

from app.schemas import marketing as sch  # noqa: E402
from app.services.marketing import post_copy, selection  # noqa: E402
from app.services.marketing import template_onscreen as tos  # noqa: E402
from marketing import cards  # noqa: E402
from marketing import news_layouts as nl  # noqa: E402

_BACKEND = Path(__file__).resolve().parents[1]
FONT = str(_BACKEND / "marketing" / "assets" / "fonts" / "Inter-Bold.ttf")
_ONSCREEN = _BACKEND / "app" / "services" / "marketing" / "template_onscreen.py"

STAKES_FOOTER = post_copy.image_footer(date(2026, 12, 29), "template", source="Nscale Form S-1 (Sep 18, 2026)",
                                       as_of="As of Mar 27, 2026")
THEME_FOOTER = post_copy.image_footer(date(2026, 10, 15), "template",
                                      source="company segment reporting; grouping by Caydex", as_of="Oct 15, 2026")
CONGRESS_FOOTER = post_copy.image_footer(date(2026, 12, 8), "template",
                                         source="congressional periodic transaction reports",
                                         as_of="November 2026 · as of Dec 8, 2026")
EARNINGS_FOOTER = post_copy.image_footer(date(2027, 1, 14), "template", source="company results and analyst consensus",
                                         as_of="Reported Nov 5, 2026")

#: Twelve theme members with LONG names: every one is 32 characters (COMPANY_MAX_CHARS) with two long
#: words, and every caption line is a 36-character segment (SEGMENT_MAX_CHARS) plus its share.
_PREFIXES = ["Alpha", "Bravo", "Delta", "Gamma", "Omega", "Sigma", "Kappa", "Theta", "Lamda", "Zetta", "Prima",
             "Ultra"]
GRID_KEYS = ["ALPH", "BRVO", "DLTA", "GAMA", "OMGA", "SGMA", "KAPA", "THTA", "LMDA", "ZETA", "PRMA", "ULTR"]
GRID_NAMES = [f"{p} Semiconductor Technologies" for p in _PREFIXES]
SEGMENTS = ["Intelligent Cloud and Infrastructure", "Data Center Accelerated Computing",
            "Embedded Processing and Analog", "Wireless and Networking Solutions", "Memory and Storage Solutions",
            "Automotive and Industrial Systems", "Semiconductor Systems Equipment", "Mobile and Consumer Devices",
            "Optical Interconnect Products", "Power Management Integrated", "Graphics and Visual Computing",
            "Custom Silicon and Licensing"]
GRID_LINES = [f"{s} · {10 + 7 * i}%" for i, s in enumerate(SEGMENTS)]

OTHER = [("NVDA", "NVIDIA"), ("NBIS", "Nebius Group"), ("AAPL", "Apple"), ("NWFT", "Northwind Fitness")]
ALL_KEYS = GRID_KEYS + [k for k, _ in OTHER]


def _entries() -> List[Dict[str, Any]]:
    """`output.logos` as the server stores them (url/sha only matter to logos.resolve_logos)."""
    return [{"key": k, "name": n, "url": None, "sha256": None, "bytes": None, "width": None, "height": None}
            for k, n in list(zip(GRID_KEYS, GRID_NAMES)) + OTHER]


KEYS = tos.logo_keys(_entries())


def _logo_png(path: Path, colour, *, noise: bool = False, size=(256, 256)) -> Path:
    if noise:
        rnd = random.Random(sum(path.name.encode()))
        im = Image.frombytes("RGB", size, bytes(rnd.getrandbits(8) for _ in range(size[0] * size[1] * 3)))
    else:
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((8, 8, size[0] - 8, size[1] - 8), fill=colour)
    im.save(path)
    return path


#: Pure red / green logos: the neutral-palette check must find them INSIDE the plates.
_LOGO_COLOURS = [(220, 20, 20, 255), (20, 200, 40, 255)]


@pytest.fixture
def logo_files(tmp_path) -> Dict[str, Path]:
    return {k: _logo_png(tmp_path / f"{k}.png", _LOGO_COLOURS[i % 2]) for i, k in enumerate(ALL_KEYS)}


def _table(files: Optional[Dict[str, Path]] = None) -> Dict[str, cards.LogoArt]:
    resolved = files or {}
    script = {"logos": [dict(e, sha256=("a" * 64 if e["key"] in resolved else None)) for e in _entries()]}
    return cards.logo_table(script, resolved)


@pytest.fixture
def shipped_2b(monkeypatch):
    """The shipped state (since drop 2b, the real one) — SHIPPED_LAYOUTS = LAYOUTS on both sides, and
    the worker's accepted tables rebuilt from DRAWERS — restated in memory (monkeypatch restores it)."""
    monkeypatch.setattr(tos, "SHIPPED_LAYOUTS", tos.LAYOUTS)
    monkeypatch.setattr(cards, "SHIPPED_LAYOUTS", cards.LAYOUTS)
    for layout in ("pair", "grid"):
        monkeypatch.setitem(nl._WALKERS, layout, nl.DRAWERS[layout][0])
        monkeypatch.setitem(nl._LAYOUTS, layout, nl.DRAWERS[layout][1])


# ── specs ────────────────────────────────────────────────────────────────────


def _pair(**over: Any) -> Dict[str, Any]:
    """A listed investee: both sides reference a logo entry. The investee's drawn name ("Nebius")
    differs from its logo entry's ("Nebius Group"), so the wordmark name has its own place."""
    spec = {"layout": "pair", "version": 1, "kicker": "COMPANY STAKES", "footer": STAKES_FOOTER,
            "left": {"logo": "NVDA", "name": "NVIDIA"}, "right": {"logo": "NBIS", "name": "Nebius"},
            "figure": "$777.4M", "label": "invested",
            "lines": ["As of Mar 27, 2026", "Source: Nscale Form S-1 (Sep 18, 2026)"]}
    spec.update(over)
    return spec


def _pair_private(**over: Any) -> Dict[str, Any]:
    """A private investee (S2, NVIDIA → Nscale): no logo key on the right."""
    return _pair(right={"name": "Nscale"}, **over)


def _grid(n: int = 12, *, lines: bool = True, more: Optional[str] = "+2 more", **over: Any) -> Dict[str, Any]:
    tiles = []
    for i in range(n):
        tile = {"logo": GRID_KEYS[i], "name": GRID_NAMES[i]}
        if lines:
            tile["line"] = GRID_LINES[i]
        tiles.append(tile)
    spec = {"layout": "grid", "version": 1, "kicker": "INSIDE A THEME", "footer": THEME_FOOTER,
            "title": "AI chips", "subtitle": "What 14 of its companies sell, by largest revenue segment",
            "tiles": tiles}
    if more is not None:
        spec["more"] = more
    spec.update(over)
    return spec


def _congress() -> Dict[str, Any]:
    """§5.6 count_v1 as a `spotlight` (A2: purchases only): giant count + company logo. No name slot."""
    return {"layout": "spotlight", "version": 1, "kicker": "DISCLOSED IN NOVEMBER", "footer": CONGRESS_FOOTER,
            "header": {"logo": "AAPL", "name": "Apple", "chip": "AAPL"}, "figure": "5",
            "headline": "members of Congress disclosed purchases of Apple stock",
            "lines": ["November 2026 · as of Dec 8, 2026"]}


def _earnings(order: str = "recommended") -> Dict[str, Any]:
    """§5.4 versus_v1 as `rows` (A1: the label is "EPS"; "analyst estimate", never "expected"). The
    recommended cell order puts the REPORTED figure last (the right-aligned ACCENT cell) and the
    estimate in the muted middle cell; the contract's literal order [label, actual, versus] also
    validates but would make the estimate the accent figure."""
    rows = [["EPS", "vs -$0.12 estimate", "-$0.05"], ["Revenue", "vs $543.6M estimate", "$551.9M"]]
    if order == "contract":
        rows = [[a, c, b] for a, b, c in rows]
    return {"layout": "rows", "version": 1, "kicker": "EARNINGS VS ESTIMATES", "footer": EARNINGS_FOOTER,
            "header": {"logo": "NWFT", "name": "Northwind Fitness", "chip": "NWFT"},
            "title": "Reported vs analyst estimates",
            "subtitle": "Quarter ended Sep 30, 2026 · reported Nov 5, 2026",
            "sections": [{"rows": [{"cells": r} for r in rows]}]}


SPECS_2B = {"pair": _pair, "pair_private": _pair_private, "grid_12": _grid,
            "grid_min": lambda: _grid(3, lines=False, more=None)}
FOOTERS = {"pair": STAKES_FOOTER, "pair_private": STAKES_FOOTER, "grid_12": THEME_FOOTER, "grid_min": THEME_FOOTER}


def _script(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {"authorship": "template", "image_spec": spec, "image_footer": spec["footer"], "logos": _entries()}


def _image(spec: Dict[str, Any], table: Dict[str, cards.LogoArt]) -> nl.TemplateImage:
    return nl.template_image_for_script(_script(spec), table)


def _code(problem: Optional[str]) -> Optional[str]:
    return problem.split(":", 1)[0] if problem else None


# ── the gate and the mirrors ─────────────────────────────────────────────────


def _literal_assignments(path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
            target, value = node.target, node.value
        else:
            continue
        try:
            out[target.id] = ast.literal_eval(value)
        except ValueError:
            continue
    return out


def test_the_2b_key_tables_mirror_the_server_file_text():
    """The pair-side and grid-tile schemas are literals in template_onscreen.py (read as TEXT: the
    worker cannot import app.*), equal in the worker and in the imported module."""
    text = _literal_assignments(_ONSCREEN)
    for name in ("PAIR_SIDE_KEYS", "GRID_TILE_KEYS"):
        assert name in text, f"template_onscreen.py no longer assigns {name} as a literal"
        assert getattr(cards, name) == text[name] == getattr(tos, name), name
    assert tos.PAIR_SIDE_KEYS == {"left": (("logo", "name"), ()), "right": (("name",), ("logo",))}
    assert tos.GRID_TILE_KEYS == (("logo", "name"), ("line",))
    # the 2b caps the task relies on (≤ 12 tiles; pair lines ≤ 2, so label + 2 lines ≤ 3 short lines)
    assert (cards.MIN_TILES, cards.MAX_TILES, cards.MAX_LINES, cards.MAX_LOGOS) == (3, 12, 2, 12)
    assert tos.LAYOUT_KEYS["pair"] == (("left", "right", "figure", "label"), ("lines",))
    assert tos.LAYOUT_KEYS["grid"] == (("title", "subtitle", "tiles"), ("more",))


def test_the_worker_draws_every_contract_layout():
    assert set(nl.DRAWERS) == set(cards.LAYOUTS) == set(tos.LAYOUTS)
    for layout, (walker, placer) in nl.DRAWERS.items():
        assert callable(walker) and callable(placer), layout
    assert nl.DRAWERS["pair"] == (nl._w_pair, nl._lay_pair) and nl.DRAWERS["grid"] == (nl._w_grid, nl._lay_grid)


def test_the_accepted_tables_are_exactly_the_shipped_layouts():
    """What the worker ACCEPTS is derived from cards.SHIPPED_LAYOUTS, which mirrors the server's
    (same members, same order) — never a layout the server does not ship, never one it cannot draw."""
    assert cards.SHIPPED_LAYOUTS == tos.SHIPPED_LAYOUTS
    assert nl._WALKERS == {k: nl.DRAWERS[k][0] for k in cards.SHIPPED_LAYOUTS}
    assert nl._LAYOUTS == {k: nl.DRAWERS[k][1] for k in cards.SHIPPED_LAYOUTS}


def test_the_worker_literal_ships_every_layout_in_a_fresh_interpreter():
    """The worker half of the flip is one literal: the real `cards.SHIPPED_LAYOUTS`, read at import
    time, makes news_layouts accept and draw all five (fresh interpreter, nothing patched; the worker
    imports no app.*)."""
    code = ("import sys; import marketing.cards as c; "
            "import marketing.news_layouts as nl; "
            "print(sorted(nl._WALKERS), sorted(nl._LAYOUTS), any(m.startswith('app') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(_BACKEND), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    want = sorted(cards.LAYOUTS)
    assert out.stdout.strip() == f"{want} {want} False"


#: Contract D9 "Layout per series" — the layout(s) each series' image uses.
SERIES_LAYOUTS = {
    "ceo_buys": {"rows", "spotlight"}, "insider_buys": {"rows", "spotlight"}, "thirteen_f": {"rows"},
    "money_map": {"bars"}, "congress_count": {"spotlight"}, "earnings": {"rows"},
    "company_stakes": {"pair"}, "theme_explainer": {"grid"},
}


def _unshipped_layouts_of_shipped_series(shipped_series) -> List[str]:
    problems = []
    for series in sorted(shipped_series):
        for layout in sorted(SERIES_LAYOUTS[series]):
            if layout not in tos.SHIPPED_LAYOUTS or layout not in cards.SHIPPED_LAYOUTS:
                problems.append(f"{series} → {layout}")
    return problems


def test_a_shipped_series_has_its_layout_shipped_on_both_sides():
    """Shipping company_stakes / theme_explainer without flipping SHIPPED_LAYOUTS (both sides) would
    have every candidate refused `image_spec_invalid` and the day fall back — this pins the flip to
    the series."""
    assert set(SERIES_LAYOUTS) == set(selection.SERIES_BY_ID)
    assert {"company_stakes", "theme_explainer"} <= selection.SHIPPED_SERIES     # drop 2b shipped them
    assert _unshipped_layouts_of_shipped_series(selection.SHIPPED_SERIES) == []
    # the check is not vacuous: either side's literal without `pair` (a half-done flip) is caught
    for side in (tos, cards):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(side, "SHIPPED_LAYOUTS", tuple(x for x in side.LAYOUTS if x != "pair"))
            assert _unshipped_layouts_of_shipped_series(selection.SHIPPED_SERIES) == ["company_stakes → pair"]


@pytest.mark.parametrize("name", sorted(SPECS_2B))
def test_pair_and_grid_outside_shipped_layouts_are_refused_on_both_sides(name, monkeypatch):
    """A rollback of the flip (both literals back to the 2a three): the server refuses them and allows
    nothing, the worker refuses them loudly, and the post image never falls back to the lesson layout.
    With the real (shipped) tuples the same spec walks and renders."""
    spec = SPECS_2B[name]()
    assert tos.validate_image_spec(spec, KEYS) is None                       # shipped: accepted
    nl.walk(spec, _table())
    two_a = ("rows", "spotlight", "bars")
    monkeypatch.setattr(tos, "SHIPPED_LAYOUTS", two_a)
    monkeypatch.setattr(cards, "SHIPPED_LAYOUTS", two_a)
    for layout in ("pair", "grid"):
        monkeypatch.delitem(nl._WALKERS, layout)
        monkeypatch.delitem(nl._LAYOUTS, layout)
    assert _code(tos.validate_image_spec(spec, KEYS)) == "layout_not_shipped"
    assert tos.image_strings(spec, _entries()) == []
    with pytest.raises(ValueError, match="not shipped"):
        nl.walk(spec, _table())
    with pytest.raises(ValueError):
        cards.render_post_image(_script(spec), font_path=FONT, layout_engine="basic")


# ── the server's closed schema ───────────────────────────────────────────────


def _golden(name: str) -> List[str]:
    if name == "pair":
        return ["COMPANY STAKES", "NVIDIA", "Nebius Group", "Nebius", "$777.4M", "invested", "As of Mar 27, 2026",
                "Source: Nscale Form S-1 (Sep 18, 2026)", STAKES_FOOTER]
    if name == "pair_private":
        return ["COMPANY STAKES", "NVIDIA", "Nscale", "$777.4M", "invested", "As of Mar 27, 2026",
                "Source: Nscale Form S-1 (Sep 18, 2026)", STAKES_FOOTER]
    head = ["INSIDE A THEME", "AI chips", "What 14 of its companies sell, by largest revenue segment"]
    if name == "grid_12":
        tiles = [s for pair in zip(GRID_NAMES, GRID_LINES) for s in pair]
        return head + tiles + ["+2 more", THEME_FOOTER]
    return head + GRID_NAMES[:3] + [THEME_FOOTER]


@pytest.mark.parametrize("name", sorted(SPECS_2B))
def test_a_2b_sample_validates_and_its_strings_are_pinned(name, shipped_2b):
    spec = SPECS_2B[name]()
    assert tos.validate_image_spec(spec, KEYS) is None
    assert tos.validate_image_spec(spec, KEYS, footer=FOOTERS[name]) is None
    got = tos.image_strings(spec, _entries())
    assert got == _golden(name)
    assert got[0] == spec["kicker"] and got[-1] == FOOTERS[name] and len(got) == len(set(got))
    sch.validate_onscreen_text(got)
    w = tos._walk_image_spec(spec, KEYS, shipped=tos.LAYOUTS)
    assert w.strings + len(w.logos) <= sch.ONSCREEN_TEXT_MAX


def test_the_grid_worst_case_is_declarable(shipped_2b):
    w = tos._walk_image_spec(_grid(), KEYS, shipped=tos.LAYOUTS)
    assert (w.strings, len(w.logos)) == (1 + 1 + 1 + 24 + 1 + 1, 12)   # kicker title subtitle 12×2 more footer
    assert w.strings + len(w.logos) == 41 <= sch.ONSCREEN_TEXT_MAX


@pytest.mark.parametrize("name", sorted(SPECS_2B))
def test_the_2b_strings_are_deterministic_and_independent_of_key_order(name, shipped_2b):
    spec = SPECS_2B[name]()

    def shuffled(v):
        if isinstance(v, dict):
            return {k: shuffled(v[k]) for k in reversed(list(v))}
        if isinstance(v, list):
            return [shuffled(x) for x in v]
        return v

    want = tos.image_strings(spec, _entries())
    for variant in (copy.deepcopy(spec), json.loads(json.dumps(shuffled(spec), sort_keys=True)), shuffled(spec)):
        assert tos.validate_image_spec(variant, KEYS) is None
        assert tos.image_strings(variant, _entries()) == want
    assert tos.image_strings(spec, list(reversed(_entries()))) == want


def test_a_private_investee_needs_no_logo_and_adds_no_logo_name(shipped_2b):
    for right in ({"name": "Nscale"}, {"name": "Nscale", "logo": None}):
        spec = _pair(right=right)
        assert tos.validate_image_spec(spec, KEYS, footer=STAKES_FOOTER) is None
        got = tos.image_strings(spec, _entries())
        assert got.count("Nscale") == 1 and not {"Nebius Group", "Nebius"} & set(got)
    # …but the investor's logo stays required (a listed company always has its entry)
    assert _code(tos.validate_image_spec(_pair(left={"name": "NVIDIA"}), KEYS)) == "missing_key"


_DELETE = object()

#: (why, sample, path, value or _DELETE, server code, worker needle)
_REFUSALS_2B = [
    # closed keys — including every name a person, a URL or a design-doc field could ride in on
    ("pair: a top-level arrow key", "pair", ("arrow",), "→", "unknown_key", "unknown key"),
    ("pair: the design doc's date_line", "pair", ("date_line",), "As of Mar 27, 2026", "unknown_key", "unknown key"),
    ("pair: a person slot", "pair", ("person",), "Jensen Huang", "unknown_key", "unknown key"),
    ("pair: a header", "pair", ("header",), {"logo": "NVDA", "name": "NVIDIA"}, "unknown_key", "unknown key"),
    ("pair: a chip on a side", "pair", ("left", "chip"), "NVDA", "unknown_key", "unknown key"),
    ("pair: a url on a side", "pair_private", ("right", "url"), "https://x.example/logo.png", "unknown_key",
     "unknown key"),
    ("pair: a sha on a side", "pair", ("right", "sha256"), "a" * 64, "unknown_key", "unknown key"),
    # missing / null required keys
    ("pair: no investor logo", "pair", ("left", "logo"), None, "missing_key", "missing"),
    ("pair: investor logo deleted", "pair", ("left", "logo"), _DELETE, "missing_key", "missing"),
    ("pair: no investor name", "pair", ("left", "name"), None, "missing_key", "missing"),
    ("pair: no investee name", "pair_private", ("right", "name"), None, "missing_key", "missing"),
    ("pair: no investee", "pair", ("right",), _DELETE, "missing_key", "missing"),
    ("pair: no figure", "pair", ("figure",), None, "missing_key", "missing"),
    ("pair: no label", "pair", ("label",), _DELETE, "missing_key", "missing"),
    ("pair: no footer", "pair", ("footer",), None, "missing_key", "missing"),
    # logos
    ("pair: unknown investor logo", "pair", ("left", "logo"), "XYZ", "unknown_logo", "names no logo"),
    ("pair: unknown investee logo", "pair", ("right", "logo"), "XYZ", "unknown_logo", "names no logo"),
    ("pair: empty investee logo", "pair", ("right", "logo"), "", "bad_logo_key", "not a logo key"),
    ("pair: numeric investee logo", "pair", ("right", "logo"), 5, "bad_logo_key", "not a logo key"),
    ("pair: over-long logo key", "pair", ("left", "logo"), "K" * 17, "bad_logo_key", "not a logo key"),
    # bounds and strings
    ("pair: three lines", "pair", ("lines",), ["a", "b", "c"], "bad_count", "entries"),
    ("pair: lines a string", "pair", ("lines",), "As of Mar 27, 2026", "bad_list", "not a list"),
    ("pair: a side not an object", "pair", ("left",), "NVDA", "not_an_object", "not an object"),
    ("pair: numeric figure", "pair", ("figure",), 777.4, "bad_string", "drawable"),
    ("pair: blank label", "pair", ("label",), "  ", "bad_string", "drawable"),
    ("pair: two-line name", "pair_private", ("right", "name"), "Nscale\nLtd", "bad_string", "drawable"),
    ("pair: over-cap figure", "pair", ("figure",), "9" * 601, "bad_string", "drawable"),
    ("pair: version 2", "pair", ("version",), 2, "bad_version", "version"),
    # grid — closed keys
    ("grid: a tile share field", "grid_12", ("tiles", 0, "share"), 0.43, "unknown_key", "unknown key"),
    ("grid: a tile chip", "grid_12", ("tiles", 3, "chip"), "GAMA", "unknown_key", "unknown key"),
    ("grid: a tile url", "grid_12", ("tiles", 11, "url"), "https://x.example/l.png", "unknown_key", "unknown key"),
    ("grid: a tile person", "grid_12", ("tiles", 1, "person"), "Lisa Su", "unknown_key", "unknown key"),
    ("grid: lines on a grid", "grid_12", ("lines",), ["a"], "unknown_key", "unknown key"),
    ("grid: a header on a grid", "grid_12", ("header",), {"logo": "ALPH", "name": "Alpha"}, "unknown_key",
     "unknown key"),
    # grid — missing
    ("grid: no title", "grid_12", ("title",), None, "missing_key", "missing"),
    ("grid: no subtitle", "grid_12", ("subtitle",), _DELETE, "missing_key", "missing"),
    ("grid: no tiles", "grid_12", ("tiles",), None, "missing_key", "missing"),
    ("grid: a tile without a logo", "grid_12", ("tiles", 5, "logo"), None, "missing_key", "missing"),
    ("grid: a tile logo deleted", "grid_12", ("tiles", 5, "logo"), _DELETE, "missing_key", "missing"),
    ("grid: a tile without a name", "grid_12", ("tiles", 0, "name"), None, "missing_key", "missing"),
    # grid — bounds, logos, strings
    ("grid: two tiles", "grid_12", ("tiles",), "first2", "bad_count", "entries"),
    ("grid: thirteen tiles", "grid_12", ("tiles",), "thirteen", "bad_count", "entries"),
    ("grid: no tile", "grid_12", ("tiles",), [], "bad_count", "entries"),
    ("grid: tiles an object", "grid_12", ("tiles",), {"logo": "ALPH", "name": "Alpha"}, "bad_list", "not a list"),
    ("grid: a tile not an object", "grid_12", ("tiles", 2), "DLTA", "not_an_object", "not an object"),
    ("grid: unknown tile logo", "grid_12", ("tiles", 7, "logo"), "XYZ", "unknown_logo", "names no logo"),
    ("grid: numeric tile logo", "grid_12", ("tiles", 7, "logo"), 7, "bad_logo_key", "not a logo key"),
    ("grid: empty tile line", "grid_12", ("tiles", 4, "line"), "", "bad_string", "drawable"),
    ("grid: two-line tile line", "grid_12", ("tiles", 4, "line"), "Cloud\n43%", "bad_string", "drawable"),
    ("grid: numeric more", "grid_12", ("more",), 2, "bad_string", "drawable"),
    ("grid: padded name", "grid_12", ("tiles", 9, "name"), " Zetta", "bad_string", "drawable"),
    ("grid: version True", "grid_12", ("version",), True, "bad_version", "version"),
]


def _mut(name: str, path, value) -> Dict[str, Any]:
    spec = json.loads(json.dumps(SPECS_2B[name]()))
    if value == "first2":
        value = spec["tiles"][:2]
    elif value == "thirteen":
        value = spec["tiles"] + spec["tiles"][:1]
    node = spec
    for step in path[:-1]:
        node = node[step]
    if value is _DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return spec


@pytest.mark.parametrize("why, name, path, value, code, needle", _REFUSALS_2B, ids=[r[0] for r in _REFUSALS_2B])
def test_both_sides_refuse_a_malformed_2b_spec(why, name, path, value, code, needle, shipped_2b):
    assert tos.validate_image_spec(SPECS_2B[name](), KEYS) is None      # the base sample is valid
    bad = _mut(name, path, value)
    problem = tos.validate_image_spec(bad, KEYS)
    assert _code(problem) == code, (why, problem)
    assert tos.image_strings(bad, _entries()) == []                      # fail closed: nothing drawable
    with pytest.raises(ValueError, match=needle):
        nl.walk(bad, _table())


@pytest.mark.parametrize("name", sorted(SPECS_2B))
def test_too_many_strings_is_the_same_count_on_both_sides(name, shipped_2b, monkeypatch):
    spec = SPECS_2B[name]()
    w = tos._walk_image_spec(spec, KEYS, shipped=tos.LAYOUTS)
    count = w.strings + len(w.logos)
    for value, ok in ((count, True), (count - 1, False)):
        monkeypatch.setattr(tos, "ONSCREEN_TEXT_MAX", value)
        monkeypatch.setattr(cards, "ONSCREEN_TEXT_MAX", value)
        if ok:
            assert tos.validate_image_spec(spec, KEYS) is None
            nl.walk(spec, _table())
        else:
            assert _code(tos.validate_image_spec(spec, KEYS)) == "too_many_strings"
            with pytest.raises(ValueError, match="strings"):
                nl.walk(spec, _table())


# ── the worker: strings drawn = strings declared ⊆ the server's allow-list ──


def _words_by_field(layout) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for ln in layout.lines:
        out.setdefault(ln.field, []).extend(ln.text.split())
    return out


def _assert_draws_exactly_its_strings(image: nl.TemplateImage, layout) -> None:
    """Every text item of the walk is drawn whole (its words, in order, from its wrapped lines); the
    only other lines are wordmark plates, each drawing its own name, and every drawn wordmark's name
    is DECLARED (`image.strings`) — so nothing on the image is outside the declaration."""
    items = nl.walk(image.spec, image.logos)
    drawn = _words_by_field(layout)
    expected = {path: value.split() for kind, path, value in items if kind == "text"}
    for path, words in expected.items():
        assert drawn.get(path) == words, (path, drawn.get(path), words)
    wordmark_fields = {f for f in drawn if f.startswith("wordmark:")}
    assert set(drawn) == set(expected) | wordmark_fields
    for field in wordmark_fields:
        key = field.split(":", 1)[1]
        arts = [a for _b, a in layout.tiles if a.key == key]
        assert arts and all(a.wordmark for a in arts), f"a verified logo {key} drew text"
        assert drawn[field] == arts[0].name.split() * len(arts)
        assert arts[0].name in image.strings
    for ln in layout.lines:
        whole = next(s for s in image.strings if ln.text in s)               # every drawn line is declared text
        assert whole


@pytest.mark.parametrize("name", sorted(SPECS_2B))
def test_with_every_logo_a_wordmark_the_drawn_strings_are_the_servers(name, shipped_2b):
    spec = SPECS_2B[name]()
    image = _image(spec, _table())
    server = tos.image_strings(spec, _entries())
    assert server, "the server allowed nothing — the comparison would be vacuous"
    assert list(image.strings) == list(image.allowed) == server
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert layout.kind == f"template:{spec['layout']}"
    _assert_draws_exactly_its_strings(image, layout)
    sch.AssetRegisterRequest(kind="card", ext="jpg", sha256="a" * 64, bytes=1000,
                             metadata={"onscreen_text": list(image.strings), "image_role": sch.IMAGE_ROLE_POST})


@pytest.mark.parametrize("name", sorted(SPECS_2B))
def test_with_logos_drawn_the_strings_are_a_subset_with_the_footer(name, logo_files, shipped_2b):
    spec = SPECS_2B[name]()
    image = _image(spec, _table(logo_files))
    allowed = set(tos.image_strings(spec, _entries())) | {spec["footer"]}   # run_service's template branch
    assert set(image.strings) <= allowed and spec["footer"] in image.strings
    texts = [v for k, _p, v in nl.walk(spec, image.logos) if k == "text"]
    assert list(image.strings) == list(dict.fromkeys(texts))              # no logo name: every logo is drawn
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    _assert_draws_exactly_its_strings(image, layout)
    wordmarks = {f for f in _words_by_field(layout) if f.startswith("wordmark:")}
    assert wordmarks == ({"wordmark:right.name"} if name == "pair_private" else set())


# ── pair specifics ───────────────────────────────────────────────────────────


def test_a_private_investee_is_a_wordmark_plate_of_its_own_name(logo_files, shipped_2b):
    image = _image(_pair_private(), _table(logo_files))
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    (left_box, left), (right_box, right) = layout.tiles
    assert not left.wordmark and left.key == "NVDA"
    assert right.wordmark and right.name == "Nscale" and right.key == "right.name"
    assert layout.field_lines("wordmark:right.name") == ["Nscale"]
    assert left_box[2] < right_box[0] and left_box[1] == right_box[1]       # side by side, same row
    assert "Nscale" in image.strings and image.strings.count("Nscale") == 1  # declared once, as the spec's own


def test_the_pair_arrow_is_a_shape_between_the_plates(logo_files, shipped_2b):
    image = _image(_pair(), _table(logo_files))
    img, layout = nl.draw_template(image, font_path=FONT, layout_engine="basic")
    (poly,) = layout.shapes
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    (lb, _), (rb, _) = layout.tiles
    assert lb[2] < min(xs) < max(xs) < rb[0]                                # strictly between the plates
    assert lb[1] < min(ys) < max(ys) < lb[3]                                # inside their row
    assert max(xs) == poly[3][0] and poly[3][1] == (min(ys) + max(ys)) // 2  # the tip points right, mid-height
    for ln in layout.lines:                                                 # no text in the gap
        assert ln.ink[2] <= min(xs) or ln.ink[0] >= max(xs) or ln.ink[1] >= max(ys) or ln.ink[3] <= min(ys)
    px = np.asarray(img)
    region = px[min(ys):max(ys), min(xs):max(xs)].reshape(-1, 3)
    accent = np.array(cards._rgb(cards.ACCENT))
    assert (np.abs(region.astype(int) - accent).sum(axis=1) <= 6).sum() > 0.3 * len(region)   # drawn, ACCENT
    assert nl._arrow(10, 100, 50, 10, 24, 17) == ((10, 45), (76, 45), (76, 33), (100, 50), (76, 67), (76, 55),
                                                  (10, 55))


def test_a_pair_with_no_room_for_the_arrow_does_not_fit(shipped_2b, monkeypatch):
    """The arrow is part of the layout, never dropped: plates too wide for it step the sizes down,
    and a gap that cannot hold it at the floor is CardOverflow (the day skips), never a missing arrow."""
    monkeypatch.setattr(nl, "PAIR_HALF_GAP", 0)
    monkeypatch.setattr(nl, "ARROW_PAD", 120)
    with pytest.raises(cards.CardOverflow, match="arrow"):
        nl.layout_template(_image(_pair(), _table()), font_path=FONT, layout_engine="basic")


# ── grid specifics ───────────────────────────────────────────────────────────


def test_twelve_long_named_tiles_fit_whole_in_two_columns(shipped_2b):
    image = _image(_grid(), _table())
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert layout.step <= cards.RAMP_STEPS
    drawn = _words_by_field(layout)
    for i in range(12):
        assert drawn[f"tiles[{i}].name"] == GRID_NAMES[i].split()
        assert drawn[f"tiles[{i}].line"] == GRID_LINES[i].split()
    lefts = sorted({ln.x for ln in layout.lines if ln.field.endswith(".name") and ln.field.startswith("tiles")})
    assert len(lefts) == 2                                                  # two aligned text columns
    tops = [min(ln.ink[1] for ln in layout.lines if ln.field == f"tiles[{i}].name") for i in range(0, 12, 2)]
    assert tops == sorted(tops) and len(set(tops)) == 6                     # six rows, top to bottom
    more = [ln for ln in layout.lines if ln.field == "more"]
    assert more and more[0].ink[1] > max(ln.ink[3] for ln in layout.lines if ln.field.startswith("tiles"))


def test_a_grid_wordmark_draws_no_plate_but_its_name_stays_declared(logo_files, shipped_2b):
    files = {k: v for k, v in logo_files.items() if k in GRID_KEYS[:5]}
    image = _image(_grid(), _table(files))
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert [a.key for _b, a in layout.tiles] == GRID_KEYS[:5]               # plates only for verified logos
    assert not any(f.startswith("wordmark:") for f in _words_by_field(layout))
    assert set(GRID_NAMES) <= set(image.strings)                            # names still declared (⊇ drawn)
    assert set(image.strings) <= set(tos.image_strings(_grid(), _entries()))
    # the slot stays: every tile's text starts at the same x in its column, plate or not
    xs = {ln.x for ln in layout.lines if ln.field.startswith("tiles") and ln.field.endswith(".name")}
    assert len(xs) == 2


def test_grid_tiles_with_a_logo_name_other_than_the_tile_name_declare_both(shipped_2b):
    entries = [dict(e, name="Alpha Semiconductor Tech Holdings") if e["key"] == "ALPH" else e for e in _entries()]
    spec = _grid(3, lines=False, more=None)
    got = tos.image_strings(spec, entries)
    assert got[3:5] == ["Alpha Semiconductor Tech Holdings", GRID_NAMES[0]]   # the wordmark name at its logo
    image = nl.template_image_for_script(dict(_script(spec), logos=entries), cards.logo_table({"logos": entries}))
    assert list(image.strings) == got


# ── bytes, pixels, determinism ───────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(SPECS_2B))
@pytest.mark.parametrize("drawn", ["logos", "noise_logos", "wordmarks"])
def test_every_2b_layout_renders_a_baseline_jpeg_under_the_cap(name, drawn, tmp_path, shipped_2b):
    files = None
    if drawn != "wordmarks":
        files = {k: _logo_png(tmp_path / f"{k}.png", _LOGO_COLOURS[i % 2], noise=drawn == "noise_logos",
                              size=(512, 512))
                 for i, k in enumerate(ALL_KEYS)}
    out = nl.render_template_image(_image(SPECS_2B[name](), _table(files)), font_path=FONT, layout_engine="basic")
    assert 0 < len(out.data) <= cards.POST_IMAGE_MAX_BYTES == 950_000
    im = Image.open(io.BytesIO(out.data))
    im.load()
    assert im.format == "JPEG" and im.size == (1080, 1350) and im.mode == "RGB"
    assert not im.info.get("progressive") and not im.info.get("progression")
    assert out.data[:2] == b"\xff\xd8" and b"\xff\xc0" in out.data[:4096]      # SOF0: baseline
    assert out.layout.step <= cards.RAMP_STEPS


def _not_neutral(rgb) -> "np.ndarray":
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    return ((r - np.maximum(g, b)) > 40) | ((g - np.maximum(r, b)) > 40)


@pytest.mark.parametrize("name", sorted(SPECS_2B))
def test_no_green_or_red_outside_the_logo_plates_and_nothing_in_the_margins(name, logo_files, shipped_2b):
    image = _image(SPECS_2B[name](), _table(logo_files))
    img, layout = nl.draw_template(image, font_path=FONT, layout_engine="basic")
    px = np.asarray(img)
    mask = np.ones(px.shape[:2], dtype=bool)
    for box, _art in layout.tiles:
        mask[box[1]:box[3], box[0]:box[2]] = False
    if any(not a.wordmark for _b, a in layout.tiles):
        assert _not_neutral(px)[~mask].any(), "the red/green test logos were not drawn — the check is vacuous"
    bad = _not_neutral(px) & mask
    assert not bad.any(), f"{int(bad.sum())} green/red pixels outside the logo plates"
    m = cards.IMAGE_MARGIN
    page = np.array(cards._rgb(cards.PAGE))
    footer_top = min(ln.ink[1] for ln in layout.lines if ln.field == "footer")
    for region in (px[:m], px[:, :m], px[:, -m:], px[-m:], px[layout.panel[3]:footer_top]):
        assert (region == page).all()
    for ln in layout.lines:
        bounds = (m, m, 1080 - m, 1350 - m) if ln.field == "footer" else layout.panel
        assert cards._within(ln.ink, bounds), (ln.field, ln.ink)
    for box, _art in layout.tiles:
        assert cards._within(box, layout.panel)


@pytest.mark.parametrize("name", ["pair", "grid_12"])
def test_same_inputs_same_bytes_and_a_logo_changes_them(name, logo_files, tmp_path, shipped_2b):
    spec = SPECS_2B[name]()
    a = nl.render_template_image(_image(spec, _table(logo_files)), font_path=FONT, layout_engine="basic").data
    cards._font.cache_clear()
    cards._fit_wordmark.cache_clear()
    b = nl.render_template_image(_image(spec, _table(logo_files)), font_path=FONT, layout_engine="basic").data
    assert a == b
    c = nl.render_template_image(_image(spec, _table()), font_path=FONT, layout_engine="basic").data
    other = dict(logo_files)
    first = spec["left"]["logo"] if name == "pair" else spec["tiles"][0]["logo"]
    other[first] = _logo_png(tmp_path / "other.png", (30, 30, 200, 255))
    d = nl.render_template_image(_image(spec, _table(other)), font_path=FONT, layout_engine="basic").data
    assert len({a, c, d}) == 3


@pytest.mark.parametrize("name, mutate", [
    ("pair", lambda s: s.update(figure=" ".join(["Incomprehensibilities"] * 26))),
    ("pair", lambda s: s["right"].update(name="W" * 60)),
    ("grid_12", lambda s: s["tiles"][3].update(name="W" * 60)),
    ("grid_12", lambda s: s["tiles"][0].update(line=" ".join(["Incomprehensibilities"] * 26))),
])
def test_text_that_cannot_fit_whole_is_card_overflow_and_the_stage_skips_the_day(name, mutate, shipped_2b):
    spec = json.loads(json.dumps(SPECS_2B[name]()))
    mutate(spec)
    assert tos.validate_image_spec(spec, KEYS) is None                   # the server cannot know: the worker does
    image = _image(spec, _table())
    with pytest.raises(cards.CardOverflow, match=spec["layout"]):
        nl.render_template_image(image, font_path=FONT, layout_engine="basic")
    from marketing import render as rd

    class Skip(Exception):
        def __init__(self, reason):
            self.reason = reason

    with pytest.raises(Skip) as ei:
        rd._render_post_image("run-x", image, font=FONT, engine="basic", skip=Skip, template=True)
    assert ei.value.reason == "unrenderable_text"


@pytest.mark.parametrize("name", sorted(SPECS_2B))
def test_render_post_image_dispatches_pair_and_grid_once_shipped(name, shipped_2b):
    script = dict(_script(SPECS_2B[name]()), image_post={"title": "alt", "paragraphs": ["a", "b"]})
    rendered = cards.render_post_image(script, font_path=FONT, layout_engine="basic")
    assert rendered.layout.kind == f"template:{SPECS_2B[name]()['layout']}"
    assert "alt" not in [ln.text for ln in rendered.layout.lines]           # the alt text is never drawn


# ── glyphs ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name, mutate, char", [
    ("grid_12", lambda s: s["tiles"][2].update(name="Delta 株式会社"), "株"),
    ("grid_12", lambda s: s["tiles"][6].update(line="Rockets \U0001F680 · 12%"), "\U0001F680"),
    ("pair_private", lambda s: s["right"].update(name="Nscale ☃"), "☃"),   # the wordmark-plate name
    ("pair", lambda s: s.update(label="invested ก"), "ก"),
])
def test_a_glyph_inter_lacks_is_refused_up_front(name, mutate, char, shipped_2b):
    """The server's string rules do not know the font; the worker's glyph check does, before any
    drawing (render.py turns MissingGlyphs into a skipped day)."""
    spec = json.loads(json.dumps(SPECS_2B[name]()))
    mutate(spec)
    image = _image(spec, _table())
    with pytest.raises(cards.MissingGlyphs) as ei:
        nl.check_glyphs(image, FONT)
    assert char in ei.value.chars


def test_a_logo_name_is_glyph_checked_even_when_the_logo_draws(logo_files, shipped_2b):
    """`allowed` holds every referenced logo's name: a logo that fails at render time still has its
    wordmark drawable, so its name must have every glyph too."""
    entries = [dict(e, name="Alpha ☃ Holdings") if e["key"] == "ALPH" else e for e in _entries()]
    script = dict(_script(_grid()), logos=entries)
    table = cards.logo_table({"logos": [dict(e, sha256="a" * 64) for e in entries]}, logo_files)
    image = nl.template_image_for_script(script, table)
    assert not image.logos["ALPH"].wordmark and "Alpha ☃ Holdings" not in image.strings
    with pytest.raises(cards.MissingGlyphs) as ei:
        nl.check_glyphs(image, FONT)
    assert "☃" in ei.value.chars


@pytest.mark.parametrize("name", sorted(SPECS_2B))
def test_every_sample_string_has_every_glyph(name, shipped_2b):
    nl.check_glyphs(_image(SPECS_2B[name](), _table()), FONT)


# ── the reused layouts: congress_count → spotlight, earnings → rows (shipped today) ────────────


def test_congress_count_is_a_spotlight_the_shipped_validators_accept():
    spec = _congress()
    assert tos.validate_image_spec(spec, KEYS, footer=CONGRESS_FOOTER) is None
    got = tos.image_strings(spec, _entries())
    assert got == ["DISCLOSED IN NOVEMBER", "Apple", "AAPL", "5",
                   "members of Congress disclosed purchases of Apple stock", "November 2026 · as of Dec 8, 2026",
                   CONGRESS_FOOTER]
    image = _image(spec, _table())
    assert list(image.strings) == got
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert layout.field_lines("figure") == ["5"]
    out = nl.render_template_image(image, font_path=FONT, layout_engine="basic")
    assert len(out.data) <= cards.POST_IMAGE_MAX_BYTES
    # a spotlight has no slot a member could ride in on
    assert _code(tos.validate_image_spec(dict(spec, member="x"), KEYS)) == "unknown_key"
    assert _code(tos.validate_image_spec(dict(spec, header=dict(spec["header"], party="x")), KEYS)) == "unknown_key"


@pytest.mark.parametrize("order", ["recommended", "contract"])
def test_earnings_two_rows_are_rows_the_shipped_validators_accept(order):
    spec = _earnings(order)
    assert tos.validate_image_spec(spec, KEYS, footer=EARNINGS_FOOTER) is None
    image = _image(spec, _table())
    assert list(image.strings) == tos.image_strings(spec, _entries())
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    out = nl.render_template_image(image, font_path=FONT, layout_engine="basic")
    assert len(out.data) <= cards.POST_IMAGE_MAX_BYTES
    if order == "recommended":
        # the REPORTED figure is the right-aligned ACCENT cell; the estimate is the muted middle cell
        (fig,) = [ln for ln in layout.lines if ln.field == "sections[0].rows[0].cells[2]"]
        (est,) = [ln for ln in layout.lines if ln.field == "sections[0].rows[0].cells[1]"]
        assert (fig.text, fig.anchor, fig.fill) == ("-$0.05", "ra", cards._rgb(cards.ACCENT))
        assert est.text.startswith("vs ") and est.fill == nl.MUTED


def test_no_green_or_red_on_the_reused_layouts(logo_files):
    for spec in (_congress(), _earnings()):
        img, layout = nl.draw_template(_image(spec, _table(logo_files)), font_path=FONT, layout_engine="basic")
        px = np.asarray(img)
        mask = np.ones(px.shape[:2], dtype=bool)
        for box, _art in layout.tiles:
            mask[box[1]:box[3], box[0]:box[2]] = False
        assert not (_not_neutral(px) & mask).any()


# ── the opening card covers all four 2b series, unchanged ───────────────────

OPENINGS_2B = {
    # stakes: the investor's logo only (two side-by-side plates on the opening card carry no arrow,
    # so the direction would be ambiguous); the headline names both companies
    "company_stakes": {"kicker": "COMPANY STAKES", "logos": ["NVDA"], "figure": "$777.4M",
                       "headline": "NVIDIA invested in Nscale"},
    "congress_count": {"kicker": "DISCLOSED IN NOVEMBER", "logos": ["AAPL"], "chip": "AAPL", "figure": "5",
                       "headline": "members of Congress disclosed purchases of Apple stock"},
    "earnings": {"kicker": "EARNINGS VS ESTIMATES", "logos": ["NWFT"], "chip": "NWFT", "figure": "-$0.05",
                 "headline": "EPS vs a -$0.12 analyst estimate"},
    "theme_explainer": {"kicker": "INSIDE A THEME", "logos": ["ALPH"], "figure": "14",
                        "headline": "companies in AI chips, by what they sell"},
}


@pytest.mark.parametrize("series", sorted(OPENINGS_2B))
def test_the_opening_card_covers_every_2b_series(series):
    card = OPENINGS_2B[series]
    assert tos.validate_opening_card(card, KEYS) is None
    allowed = tos.opening_strings(card, _entries())
    spec = cards.opening_spec(card, _table())                              # every logo a wordmark
    assert cards.onscreen_strings(spec) == allowed
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    assert layout.step <= cards.RAMP_STEPS and len(layout.tiles) == 1
    cards.check_glyphs([spec], FONT)
