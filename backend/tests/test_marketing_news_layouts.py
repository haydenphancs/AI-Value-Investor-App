"""
Drop 2a (contract D9 / D14) — the worker's TEMPLATE post image (`marketing/news_layouts.py`), its
logo plates (`marketing/cards.py`) and the logo download (`marketing/logos.py`).

What is pinned here, strongest first:

* **The worker draws only what the server allows.** For every shipped layout (`rows`, `spotlight`,
  `bars`), the strings the image declares are exactly the server's own
  `template_onscreen.image_strings` when every logo is a wordmark, and a subset of it (footer
  always included) when logos are drawn — checked against the server module itself, and against
  what the laid-out image REALLY draws (every field's words come back from its wrapped lines).
* **Mirrors are read from the server's source.** `cards.LAYOUTS` and the closed schema's caps are
  compared with template_onscreen.py's own assignments (AST, the file text) and its imports; the
  logo limits with logo_check.py's.
* **Every layout renders a ≤ 950,000-byte baseline JPEG** at 1080×1350 with 12 logos (noise-filled,
  the worst case for the encoder), never truncated; text that cannot fit is CardOverflow, which the
  render stage turns into a skipped day.
* **Neutral pixels**: no green or red anywhere outside the logo plates (the test logos ARE red and
  green, so the check is not vacuous), nothing in the margins.
* **The closed schema refuses** what the server refuses (unknown keys, over-cap counts, NaN /
  out-of-range ratios, unknown logo keys, unshipped layouts…) — each case also refused by the
  server's validator, so the two cannot drift silently.

Hermetic: Pillow, the vendored Inter Bold and generated PNGs; no network. `app.*` is imported by the
TESTS only (the worker package never imports it).
"""

from __future__ import annotations

import ast
import io
import json
import logging
import math
import random
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PIL = pytest.importorskip("PIL")
np = pytest.importorskip("numpy")
from PIL import Image  # noqa: E402

from app.schemas import marketing as sch  # noqa: E402
from app.services.marketing import logo_check, post_copy  # noqa: E402
from app.services.marketing import template_onscreen as tos  # noqa: E402
from marketing import cards  # noqa: E402
from marketing import logos as lg  # noqa: E402
from marketing import news_layouts as nl  # noqa: E402

_BACKEND = Path(__file__).resolve().parents[1]
FONT = str(_BACKEND / "marketing" / "assets" / "fonts" / "Inter-Bold.ttf")
_ONSCREEN = _BACKEND / "app" / "services" / "marketing" / "template_onscreen.py"
_LOGO_CHECK = _BACKEND / "app" / "services" / "marketing" / "logo_check.py"
RUN_DATE = date(2026, 11, 16)
FOOTER = post_copy.image_footer(RUN_DATE, "template", source="SEC Form 4 filings", as_of="Nov 16, 2026")

KEYS = ["GME", "NVDA", "GOOGL", "AMZN", "BRK-B", "COST", "REGN", "MSFT", "AAPL", "META", "TSLA", "INTC"]
NAMES = ["GameStop", "NVIDIA", "Alphabet", "Amazon", "Berkshire Hathaway", "Costco",
         "Regeneron Pharmaceuticals", "Microsoft", "Apple", "Meta Platforms", "Tesla", "Intel"]


def _entries() -> List[Dict[str, Any]]:
    """`output.logos` as the server stores them (url/sha only matter to logos.resolve_logos)."""
    return [{"key": k, "name": n, "url": None, "sha256": None, "bytes": None, "width": None, "height": None}
            for k, n in zip(KEYS, NAMES)]


def _logo_png(path: Path, colour, *, noise: bool = False, size=(256, 256)) -> Path:
    if noise:
        rnd = random.Random(hash(path.name) & 0xFFFF)
        im = Image.frombytes("RGB", size, bytes(rnd.getrandbits(8) for _ in range(size[0] * size[1] * 3)))
    else:
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        from PIL import ImageDraw

        ImageDraw.Draw(im).ellipse((8, 8, size[0] - 8, size[1] - 8), fill=colour)
    im.save(path)
    return path


#: Pure red / green logos: the neutral-palette check must find them INSIDE the plates.
_LOGO_COLOURS = [(220, 20, 20, 255), (20, 200, 40, 255)]


@pytest.fixture
def logo_files(tmp_path) -> Dict[str, Path]:
    return {k: _logo_png(tmp_path / f"{k}.png", _LOGO_COLOURS[i % 2]) for i, k in enumerate(KEYS)}


def _table(files: Optional[Dict[str, Path]] = None) -> Dict[str, cards.LogoArt]:
    resolved = files or {}
    script = {"logos": [dict(e, sha256=("a" * 64 if e["key"] in resolved else None)) for e in _entries()]}
    return cards.logo_table(script, resolved)


# ── specs (each also checked against the SERVER's validator: never a fixture the server refuses) ──


def _rows_spec(n_rows: int = 8, *, header: bool = True) -> Dict[str, Any]:
    first = min(n_rows, 5)
    sections = [{"heading": "Newly reported",
                 "rows": [{"logo": KEYS[1 + i], "cells": [NAMES[1 + i], "1.2 million shares", f"${(9 - i) * 7.4:.1f}M"]}
                          for i in range(first)]}]
    if n_rows > first:
        sections.append({"heading": "No longer reported", "more": "+4 more positions",
                         "rows": [{"logo": KEYS[1 + first + i], "cells": [NAMES[1 + first + i], "$310.0M"]}
                                  for i in range(n_rows - first)]})
    spec = {"layout": "rows", "version": 1, "kicker": "13F SEASON · Q3 2026", "footer": FOOTER,
            "title": "What Berkshire Hathaway reported for Q3 2026",
            "subtitle": "Positions as of Sep 30, filed Nov 14",
            "sections": sections, "notes": ["Values as reported in the filing.", "Share counts as filed."]}
    if header:
        spec["header"] = {"logo": KEYS[0], "name": NAMES[0], "chip": "13F-HR"}
    return spec


def _spotlight_spec() -> Dict[str, Any]:
    return {"layout": "spotlight", "version": 1, "kicker": "FILED LAST WEEK · FORM 4", "footer": FOOTER,
            "header": {"logo": "GME", "name": "GameStop", "chip": "GME"}, "figure": "$74.4M",
            "headline": "GameStop's CEO disclosed buying GameStop stock",
            "lines": ["1,000,000 shares across 3 purchases", "Filed Nov 9 to Nov 13, 2026"]}


def _bars_spec() -> Dict[str, Any]:
    seg = [("Food and sundries", "$105.2B", 0.38), ("Non-foods", "$68.1B", 0.25), ("Fresh foods", "$37.3B", 0.14),
           ("Warehouse ancillary", "$28.9B", 0.10), ("Membership fees", "$5.3B", 0.02),
           ("Other businesses", "$1.4B", 0.005), ("Eliminations", "-$1.0B", 0.004)]
    flow = [("Revenue", "$275.2B", 1.0), ("Gross profit", "$35.9B", 0.13), ("Operating profit", "$10.4B", 0.038),
            ("Net income", "$8.1B", 0.029)]
    return {"layout": "bars", "version": 1, "kicker": "MONEY MAP", "footer": FOOTER,
            "header": {"logo": "COST", "name": "Costco"}, "title": "How Costco makes money",
            "subtitle": "Fiscal 2025 · company financial statements",
            "segments": [{"label": a, "value": b, "ratio": r, "style": "outline" if a == "Eliminations" else "fill"}
                         for a, b, r in seg],
            "flow": [{"label": a, "value": b, "ratio": r, "style": "fill"} for a, b, r in flow],
            "callout": "For every $100 of sales, about $2.94 was profit"}


SPECS = {"rows": _rows_spec, "spotlight": _spotlight_spec, "bars": _bars_spec}


@pytest.mark.parametrize("name", sorted(SPECS))
def test_every_fixture_spec_is_one_the_server_accepts(name):
    spec = SPECS[name]()
    assert tos.validate_image_spec(spec, tos.logo_keys(_entries()), footer=FOOTER) is None


def _image(spec: Dict[str, Any], table: Dict[str, cards.LogoArt]) -> nl.TemplateImage:
    return nl.template_image_for_script({"authorship": "template", "image_spec": spec, "image_footer": FOOTER,
                                         "logos": _entries()}, table)


# ── mirrors (read from the server's source text, and from its module) ────────


def _literal_assignments(path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        for t in targets:
            names = [t] if isinstance(t, ast.Name) else (t.elts if isinstance(t, ast.Tuple) else [])
            try:
                v = ast.literal_eval(value)
            except ValueError:
                continue
            if len(names) == 1 and isinstance(names[0], ast.Name):
                out[names[0].id] = v
            elif isinstance(v, tuple) and len(v) == len(names):
                for n, x in zip(names, v):
                    if isinstance(n, ast.Name):
                        out[n.id] = x
    return out


_MIRRORED = ("LAYOUTS", "SHIPPED_LAYOUTS", "SPEC_VERSION", "MAX_LOGOS", "MAX_ROWS", "MAX_SECTIONS",
             "MAX_SECTION_ROWS", "MAX_CELLS", "MAX_NOTES", "MAX_LINES", "MIN_BARS", "MAX_BARS", "MIN_FLOW",
             "MAX_FLOW", "MIN_TILES", "MAX_TILES", "MAX_OPENING_LOGOS", "LOGO_KEY_MAX_CHARS", "BAR_STYLES",
             "COMMON_KEYS", "LAYOUT_KEYS", "OPENING_KEYS")


def test_the_layouts_mirror_the_server_file_text():
    """`cards.LAYOUTS` and the closed schema mirror template_onscreen.py — read from its TEXT (the
    worker cannot import app.*), and from the imported module for good measure."""
    text = _literal_assignments(_ONSCREEN)
    for name in _MIRRORED:
        assert name in text, f"template_onscreen.py no longer assigns {name} as a literal"
        assert getattr(cards, name) == text[name] == getattr(tos, name), name
    assert cards.LAYOUTS == ("rows", "spotlight", "pair", "bars", "grid")
    # every layout has shipped since drop 2b; a layout the worker cannot draw is never shipped by the server
    assert cards.SHIPPED_LAYOUTS == tos.SHIPPED_LAYOUTS == cards.LAYOUTS
    assert set(cards.SHIPPED_LAYOUTS) == set(nl._WALKERS) == set(nl._LAYOUTS) == set(tos.SHIPPED_LAYOUTS)
    assert set(cards.SHIPPED_LAYOUTS) <= set(cards.LAYOUTS)


def test_the_other_mirrors():
    assert cards.TEMPLATE_AUTHORSHIP == sch.TEMPLATE_AUTHORSHIP == post_copy.AUTHORSHIP_TEMPLATE
    assert cards.VIDEO_LAYOUT_PER_LINE == sch.VIDEO_LAYOUT_PER_LINE
    assert cards.ONSCREEN_TEXT_MAX == sch.ONSCREEN_TEXT_MAX
    assert cards.MAX_STRING_CHARS == sch.ONSCREEN_TEXT_MAX_CHARS
    assert cards.LINE_BREAKS == tos._LINE_BREAKS
    text = _literal_assignments(_LOGO_CHECK)
    for name in ("LOGO_MAX_BYTES", "LOGO_MIN_SIDE_PX", "LOGO_MAX_SIDE_PX", "LOGO_MIN_ASPECT", "LOGO_MAX_ASPECT"):
        assert getattr(lg, name) == text[name] == getattr(logo_check, name), name
    assert lg.MAX_LOGOS == tos.MAX_LOGOS and lg.LOGO_KEY_MAX_CHARS == tos.LOGO_KEY_MAX_CHARS
    assert lg.LOGOS_BUDGET_SECONDS == lg.MAX_LOGOS * lg.LOGO_DOWNLOAD_TIMEOUT_SECONDS == 60
    assert lg.MAX_IMAGE_PIXELS == lg.LOGO_MAX_SIDE_PX ** 2 == 2048 * 2048
    # logo_check.logo_path is the shape logos.py fetches (and nothing else)
    info = logo_check.LogoInfo(ext="png", width=200, height=200, sha256="ab" * 32)
    url = f"https://x.supabase.co/storage/v1/object/public/marketing-media/{logo_check.logo_path(info)}"
    assert lg.logo_url_pattern("x.supabase.co").match(url)


# ── strings: the server's allow-list, and what is really drawn ───────────────


def _words_by_field(layout) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for ln in layout.lines:
        out.setdefault(ln.field, []).extend(ln.text.split())
    return out


def _assert_draws_exactly_its_strings(image: nl.TemplateImage, layout) -> None:
    """Every text item of the walk is drawn whole (its words, in order, from its wrapped lines);
    every wordmark plate draws its company name (or nothing); no other field exists."""
    items = nl.walk(image.spec, image.logos)
    drawn = _words_by_field(layout)
    expected = {}
    for kind, path, value in items:
        if kind == "text":
            expected[path] = value.split()
    for path, words in expected.items():
        assert drawn.get(path) == words, (path, drawn.get(path), words)
    wordmark_fields = {f for f in drawn if f.startswith("wordmark:")}
    assert set(drawn) == set(expected) | wordmark_fields
    plates: Dict[str, int] = {}
    for _box, art in layout.tiles:
        plates[art.key] = plates.get(art.key, 0) + 1
    for field in wordmark_fields:
        key = field.split(":", 1)[1]
        art = image.logos[key]
        assert art.wordmark, f"a verified logo {key} drew text"
        assert drawn[field] == art.name.split() * plates[key]
    referenced = [v for k, _p, v in items if k == "logo"]
    assert sorted(art.key for _b, art in layout.tiles) == sorted(referenced)   # one plate per reference


@pytest.mark.parametrize("name", sorted(SPECS))
def test_with_every_logo_a_wordmark_the_drawn_strings_are_the_servers(name):
    spec = SPECS[name]()
    image = _image(spec, _table())
    server = tos.image_strings(spec, _entries())
    assert server, "the server allowed nothing — the comparison would be vacuous"
    assert list(image.strings) == list(image.allowed) == server
    assert image.strings[0] == spec["kicker"] and image.strings[-1] == FOOTER
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    _assert_draws_exactly_its_strings(image, layout)
    # every wordmark name really is drawn at these sizes (no blank plate on a real fixture) …
    assert not cards.blank_wordmarks(layout) or name == "rows"
    # … and the registration body passes the server's own request schema
    sch.AssetRegisterRequest(kind="card", ext="jpg", sha256="a" * 64, bytes=1000,
                             metadata={"onscreen_text": list(image.strings), "image_role": sch.IMAGE_ROLE_POST})


@pytest.mark.parametrize("name", sorted(SPECS))
def test_with_logos_drawn_the_strings_are_a_subset_with_the_footer(name, logo_files):
    spec = SPECS[name]()
    image = _image(spec, _table(logo_files))
    allowed = set(tos.image_strings(spec, _entries())) | {FOOTER}     # run_service's template branch (D12)
    assert set(image.strings) <= allowed and FOOTER in image.strings
    texts = [v for k, _p, v in nl.walk(spec, image.logos) if k == "text"]
    assert list(image.strings) == list(dict.fromkeys(texts))        # no logo name: every logo is drawn
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    _assert_draws_exactly_its_strings(image, layout)
    assert not any(f.startswith("wordmark:") for f in _words_by_field(layout))


def test_a_drawn_logo_declares_no_name_and_a_wordmark_declares_its_name(logo_files):
    """Rows that name no company in their cells (role + amount only): a VERIFIED logo adds no string
    (the server's allow-list still holds its name — a subset), a wordmark adds exactly its name, at
    its logo's place in the walk."""
    spec = _rows_spec(header=False)
    spec["sections"] = [{"rows": [{"logo": KEYS[i], "cells": ["CEO", f"${i + 1}.0M"]} for i in range(3)]}]
    assert tos.validate_image_spec(spec, tos.logo_keys(_entries()), footer=FOOTER) is None
    server = tos.image_strings(spec, _entries())
    drawn = _image(spec, _table(logo_files))
    assert not set(NAMES[:3]) & set(drawn.strings) and set(drawn.strings) < set(server)
    files = dict(logo_files)
    files.pop(KEYS[1])
    mixed = _image(spec, _table(files))
    assert [s for s in mixed.strings if s in NAMES] == [NAMES[1]]
    assert list(mixed.strings) == [s for s in server if s not in (NAMES[0], NAMES[2])]
    assert list(_image(spec, _table()).strings) == server


def test_the_alt_text_is_never_drawn(logo_files):
    """A template's `image_post` is name-free ALT TEXT: the post image draws the spec, never it."""
    alt = {"title": "A table of five company purchases disclosed on SEC Form 4",
           "paragraphs": ["The largest was 74.4 million dollars.", "Source: SEC Form 4 filings."]}
    script = {"authorship": "template", "image_spec": _spotlight_spec(), "image_footer": FOOTER,
              "image_post": alt, "logos": _entries()}
    image = nl.template_image_for_script(script, _table(logo_files))
    assert not ({alt["title"], *alt["paragraphs"]} & set(image.strings))
    rendered = cards.render_post_image(script, _table(logo_files), font_path=FONT, layout_engine="basic")
    assert rendered.layout.kind == "template:spotlight"


# ── bytes, pixels, determinism ───────────────────────────────────────────────


def _jpeg_info(data: bytes):
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


@pytest.mark.parametrize("name", sorted(SPECS))
@pytest.mark.parametrize("drawn", ["logos", "noise_logos", "wordmarks"])
def test_every_layout_renders_a_baseline_jpeg_under_the_cap_with_twelve_logos(name, drawn, tmp_path):
    files = None
    if drawn != "wordmarks":
        files = {k: _logo_png(tmp_path / f"{k}.png", _LOGO_COLOURS[i % 2], noise=drawn == "noise_logos",
                              size=(512, 512))
                 for i, k in enumerate(KEYS)}
    table = _table(files)
    assert len(table) == 12 and sum(1 for a in table.values() if not a.wordmark) == (0 if files is None else 12)
    out = nl.render_template_image(_image(SPECS[name](), table), font_path=FONT, layout_engine="basic")
    assert 0 < len(out.data) <= cards.POST_IMAGE_MAX_BYTES == 950_000
    im = _jpeg_info(out.data)
    assert im.format == "JPEG" and im.size == (1080, 1350) and im.mode == "RGB"
    assert not im.info.get("progressive") and not im.info.get("progression")
    assert out.data[:2] == b"\xff\xd8" and b"\xff\xc0" in out.data[:4096]      # SOF0: baseline
    assert out.layout.step <= cards.RAMP_STEPS


def _not_neutral(rgb) -> "np.ndarray":
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    return ((r - np.maximum(g, b)) > 40) | ((g - np.maximum(r, b)) > 40)


@pytest.mark.parametrize("name", sorted(SPECS))
def test_no_green_or_red_outside_the_logo_plates_and_nothing_in_the_margins(name, logo_files):
    image = _image(SPECS[name](), _table(logo_files))
    img, layout = nl.draw_template(image, font_path=FONT, layout_engine="basic")
    px = np.asarray(img)
    mask = np.ones(px.shape[:2], dtype=bool)
    for box, _art in layout.tiles:
        mask[box[1]:box[3], box[0]:box[2]] = False
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


def test_the_neutral_check_catches_a_red_pixel():
    """Mutation check of the check itself: one red bar is caught."""
    img = Image.new("RGB", (10, 10), cards._rgb(cards.CARD))
    img.putpixel((3, 3), (230, 30, 30))
    assert _not_neutral(np.asarray(img)).sum() == 1


@pytest.mark.parametrize("name", sorted(SPECS))
def test_same_inputs_same_bytes_and_every_input_changes_them(name, logo_files, tmp_path):
    spec = SPECS[name]()
    a = nl.render_template_image(_image(spec, _table(logo_files)), font_path=FONT, layout_engine="basic").data
    cards._font.cache_clear()
    cards._fit_wordmark.cache_clear()
    b = nl.render_template_image(_image(spec, _table(logo_files)), font_path=FONT, layout_engine="basic").data
    assert a == b
    c = nl.render_template_image(_image(spec, _table()), font_path=FONT, layout_engine="basic").data
    other = dict(logo_files)
    other[spec["header"]["logo"]] = _logo_png(tmp_path / "other.png", (30, 30, 200, 255))
    d = nl.render_template_image(_image(spec, _table(other)), font_path=FONT, layout_engine="basic").data
    assert len({a, c, d}) == 3


# ── wordmarks ────────────────────────────────────────────────────────────────


def test_a_missing_logo_is_a_wordmark_and_its_name_is_declared(logo_files):
    files = dict(logo_files)
    files.pop("GME")
    image = _image(_spotlight_spec(), _table(files))
    assert image.logos["GME"].wordmark and "GameStop" in image.strings
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert layout.field_lines("wordmark:GME") == ["GameStop"]


def test_a_name_too_long_for_a_small_plate_leaves_it_blank_never_clipped(caplog):
    """A row plate is small; a long single word that cannot be drawn legibly leaves the plate
    BLANK (the name stays declared: declared ⊇ drawn), never a broken or clipped word."""
    caplog.set_level(logging.WARNING, logger="marketing.news_layouts")
    entries = _entries()
    entries[6] = dict(entries[6], name="Supercalifragilisticexpialidocious")
    table = cards.logo_table({"logos": entries})
    image = nl.template_image_for_script({"authorship": "template", "image_spec": _rows_spec(), "image_footer": FOOTER,
                                          "logos": entries}, table)
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert "REGN" in cards.blank_wordmarks(layout) and not layout.field_lines("wordmark:REGN")
    assert "Supercalifragilisticexpialidocious" in image.strings          # still declared (safe direction)
    assert any("blank plate" in r.getMessage() for r in caplog.records)
    # the plate is still drawn (white), with no ink on it
    img, _ = nl.draw_template(image, font_path=FONT, layout_engine="basic")
    (box,) = [b for b, art in layout.tiles if art.key == "REGN"]
    _radius, pad = cards.plate_geometry(box)
    inner = (box[0] + pad, box[1] + pad, box[2] - pad, box[3] - pad)
    assert img.crop(inner).getextrema() == ((255, 255),) * 3
    (gme_box,) = [b for b, art in layout.tiles if art.key == "NVDA"]       # anti-vacuity: a drawn wordmark inks
    assert img.crop(gme_box).getextrema() != ((255, 255),) * 3


# ── overflow ─────────────────────────────────────────────────────────────────


def test_text_that_cannot_fit_whole_is_card_overflow_and_the_stage_skips_the_day():
    spec = _spotlight_spec()
    spec["headline"] = " ".join(["Incomprehensibilities"] * 26)      # ≤ 600 chars, far too much text
    image = _image(spec, _table())
    with pytest.raises(cards.CardOverflow, match="spotlight"):
        nl.render_template_image(image, font_path=FONT, layout_engine="basic")
    from marketing import render as rd

    class Skip(Exception):
        def __init__(self, reason):
            self.reason = reason

    with pytest.raises(Skip) as ei:
        rd._render_post_image("run-x", image, font="" + FONT, engine="basic", skip=Skip, template=True)
    assert ei.value.reason == "unrenderable_text"


def test_a_single_word_wider_than_the_column_overflows():
    spec = _bars_spec()
    spec["callout"] = "W" * 120
    with pytest.raises(cards.CardOverflow):
        nl.layout_template(_image(spec, _table()), font_path=FONT, layout_engine="basic")


def test_a_template_image_over_the_cap_at_every_quality_is_image_too_large(logo_files):
    with pytest.raises(cards.ImageTooLarge):
        nl.render_template_image(_image(_spotlight_spec(), _table(logo_files)), font_path=FONT,
                                 layout_engine="basic", max_bytes=1000)


# ── the closed schema (worker mirror) ────────────────────────────────────────


def _mut(name: str, fn) -> Dict[str, Any]:
    spec = json.loads(json.dumps(SPECS[name]()))
    fn(spec)
    return spec


_BAD = [
    ("rows", lambda s: s.update(extra="x"), "unknown key"),
    ("rows", lambda s: s.pop("title"), "missing"),
    ("rows", lambda s: s.update(title=None), "missing"),
    ("rows", lambda s: s.update(version=2), "version"),
    ("rows", lambda s: s.update(version=True), "version"),
    # a rows-shaped spec relabelled as another (shipped) layout is that layout's closed schema's problem
    ("rows", lambda s: s.update(layout="pair"), "unknown key"),
    ("rows", lambda s: s.update(layout="grid"), "unknown key"),
    ("rows", lambda s: s.update(layout="table"), "layout"),
    ("rows", lambda s: s.update(layout=None), "layout"),
    ("rows", lambda s: s["sections"].append({"rows": [{"cells": ["x"]}]}), "more than"),
    ("rows", lambda s: s["sections"].extend([{"rows": [{"cells": ["x"]}]}] * 3), "entries"),
    ("rows", lambda s: s["sections"][0]["rows"][0].update(cells=["a", "b", "c", "d"]), "entries"),
    ("rows", lambda s: s["sections"][0]["rows"][0].update(cells=[]), "entries"),
    ("rows", lambda s: s["sections"][0]["rows"][0].update(logo="ZZZZ"), "names no logo"),
    ("rows", lambda s: s["sections"][0]["rows"][0].update(logo=7), "logo key"),
    ("rows", lambda s: s["sections"][0]["rows"][0].update(cells=[" padded"]), "drawable"),
    ("rows", lambda s: s["sections"][0]["rows"][0].update(cells=["two\nlines"]), "drawable"),
    ("rows", lambda s: s["sections"][0]["rows"][0].update(cells=["x" * 601]), "drawable"),
    ("rows", lambda s: s["sections"][0]["rows"][0].update(cells=[5]), "drawable"),
    ("rows", lambda s: s.update(notes=["a", "b", "c"]), "entries"),
    ("rows", lambda s: s.update(header={"logo": "GME", "name": "GameStop", "colour": "red"}), "unknown key"),
    ("spotlight", lambda s: s.update(lines=["a", "b", "c"]), "entries"),
    ("spotlight", lambda s: s["header"].update(logo="NOPE"), "names no logo"),
    ("spotlight", lambda s: s.update(figure=""), "drawable"),
    ("bars", lambda s: s["segments"][0].update(ratio=float("nan")), "ratio"),
    ("bars", lambda s: s["segments"][0].update(ratio=float("inf")), "ratio"),
    ("bars", lambda s: s["segments"][0].update(ratio=1.0001), "ratio"),
    ("bars", lambda s: s["segments"][0].update(ratio=-0.1), "ratio"),
    ("bars", lambda s: s["segments"][0].update(ratio=True), "ratio"),
    ("bars", lambda s: s["segments"][0].update(ratio="0.5"), "ratio"),
    ("bars", lambda s: s["segments"][0].update(style="dashed"), "style"),
    ("bars", lambda s: s["segments"][0].update(colour="green"), "unknown key"),
    ("bars", lambda s: s.update(segments=s["segments"][:1]), "entries"),
    ("bars", lambda s: s.update(flow=s["flow"] * 2), "entries"),
    ("bars", lambda s: s["header"].update(chip="COST"), "unknown key"),
]


@pytest.mark.parametrize("name, mutate, needle", _BAD)
def test_the_worker_refuses_what_the_server_refuses(name, mutate, needle):
    spec = _mut(name, mutate)
    with pytest.raises(ValueError, match=needle):
        nl.walk(spec, _table())
    # …and so does the server's own validator (the two cannot drift silently)
    assert tos.validate_image_spec(spec, tos.logo_keys(_entries())) is not None


@pytest.mark.parametrize("layout", ["rows", "spotlight", "bars"])
def test_a_layout_outside_shipped_layouts_is_refused_on_both_sides(layout, monkeypatch):
    """The gate both sides keep (every layout is shipped since drop 2b): a layout dropped from
    SHIPPED_LAYOUTS — a rollback — is refused loudly by the worker (never drawn, never the lesson layout)
    and allows nothing on the server, while it is accepted with the real tuples."""
    spec = json.loads(json.dumps(SPECS[layout]()))
    nl.walk(spec, _table())                                                   # shipped: accepted
    assert tos.validate_image_spec(spec, tos.logo_keys(_entries())) is None
    narrowed = tuple(x for x in cards.LAYOUTS if x != layout)
    monkeypatch.setattr(cards, "SHIPPED_LAYOUTS", narrowed)
    monkeypatch.setattr(tos, "SHIPPED_LAYOUTS", narrowed)
    monkeypatch.delitem(nl._WALKERS, layout)
    monkeypatch.delitem(nl._LAYOUTS, layout)
    with pytest.raises(ValueError, match="not shipped"):
        nl.walk(spec, _table())
    assert (tos.validate_image_spec(spec, tos.logo_keys(_entries())) or "").startswith("layout_not_shipped")
    assert tos.image_strings(spec, _entries()) == []


def test_too_many_strings_are_refused_like_the_server(monkeypatch):
    """The legal maximum (strings + logo names ≤ ONSCREEN_TEXT_MAX) walks on both sides; one past a
    (lowered) cap is refused on both — the count is the same count."""
    spec = _rows_spec()
    assert tos.validate_image_spec(spec, tos.logo_keys(_entries())) is None
    items = nl.walk(spec, _table())
    count = sum(1 for k, _p, _v in items if k == "text") + len({v for k, _p, v in items if k == "logo"})
    monkeypatch.setattr(cards, "ONSCREEN_TEXT_MAX", count)
    monkeypatch.setattr(tos, "ONSCREEN_TEXT_MAX", count)
    nl.walk(spec, _table())
    assert tos.validate_image_spec(spec, tos.logo_keys(_entries())) is None
    monkeypatch.setattr(cards, "ONSCREEN_TEXT_MAX", count - 1)
    monkeypatch.setattr(tos, "ONSCREEN_TEXT_MAX", count - 1)
    with pytest.raises(ValueError, match="strings"):
        nl.walk(spec, _table())
    assert "too_many_strings" in (tos.validate_image_spec(spec, tos.logo_keys(_entries())) or "")


@pytest.mark.parametrize("over, needle", [
    ({"image_spec": None}, "no image_spec"),
    ({"image_footer": None}, "image_footer"),
    ({"image_footer": "  "}, "image_footer"),
    ({"image_footer": FOOTER + " x"}, "not the script's image_footer"),
    ({"image_spec": "rows"}, "not an object"),
])
def test_template_image_for_script_fails_loudly(over, needle):
    script = {"authorship": "template", "image_spec": _rows_spec(), "image_footer": FOOTER, "logos": _entries()}
    script.update(over)
    with pytest.raises(ValueError, match=needle):
        nl.template_image_for_script(script, _table())


def test_render_post_image_dispatches_on_authorship_and_never_falls_back(logo_files):
    script = {"authorship": "template", "image_spec": _rows_spec(), "image_footer": FOOTER, "logos": _entries(),
              "image_post": {"title": "t", "paragraphs": ["a", "b"]}}
    assert cards.render_post_image(script, font_path=FONT, layout_engine="basic").layout.kind == "template:rows"
    for bad in ({"image_spec": dict(_rows_spec(), layout="table")}, {"image_spec": None},
                {"image_spec": dict(_rows_spec(), layout=None)}):
        with pytest.raises(ValueError):
            cards.render_post_image({**script, **bad}, font_path=FONT, layout_engine="basic")
    with pytest.raises(ValueError, match="authorship"):
        cards.render_post_image({**script, "authorship": "robot"}, font_path=FONT, layout_engine="basic")
    lesson = {"image_post": {"title": "Start early", "paragraphs": ["Time does the work.", "Fees compound too."]},
              "image_footer": post_copy.image_footer(RUN_DATE)}
    assert cards.render_post_image(lesson, font_path=FONT, layout_engine="basic").layout.kind == "post_image"
    with pytest.raises(ValueError, match="no image_post"):
        cards.render_post_image({"image_footer": "f"}, font_path=FONT, layout_engine="basic")


# ── the logo table ───────────────────────────────────────────────────────────


def test_the_logo_table_keeps_the_servers_well_formed_entries_only(caplog, tmp_path):
    p = _logo_png(tmp_path / "a.png", (10, 10, 200, 255))
    entries = [{"key": "AAA", "name": "Alpha", "sha256": "b" * 64},
               {"key": "AAA", "name": "Second Alpha"},                      # a repeated key: first wins
               {"key": "", "name": "Empty"}, {"key": "X" * 17, "name": "Long key"},
               {"key": "BBB", "name": " padded"}, {"key": "CCC", "name": "two\nlines"},
               {"key": 5, "name": "Number"}, "not an object", {"key": "DDD", "name": "Delta", "sha256": "c" * 64}]
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    table = cards.logo_table({"logos": entries}, {"AAA": p})
    assert list(table) == ["AAA", "DDD"]
    assert table["AAA"] == cards.LogoArt("AAA", "Alpha", str(p), "b" * 64)
    assert table["DDD"].wordmark and table["DDD"].sha256 is None          # no file: no sha in the key
    # the server keeps the same keys (its drawable check is stricter, never looser)
    assert set(table) == set(tos.logo_keys([e for e in entries if isinstance(e, dict)]))
    assert len([r for r in caplog.records if "not a usable" in r.getMessage()]) == 6
    assert cards.logo_table({}) == {} and cards.logo_table({"logos": []}) == {}
    with pytest.raises(ValueError):
        cards.logo_table({"logos": "AAA"})


@pytest.mark.parametrize("kwargs", [
    {"key": "", "name": "n"}, {"key": "K" * 17, "name": "n"}, {"key": "K", "name": ""},
    {"key": "K", "name": " n"}, {"key": "K", "name": "n\nm"}, {"key": "K", "name": "n", "sha256": "a" * 64},
    {"key": "K", "name": "n", "path": 5},
])
def test_logo_art_refuses_what_it_cannot_draw(kwargs):
    with pytest.raises((ValueError, TypeError)):
        cards.LogoArt(**kwargs)


def test_the_server_and_worker_agree_on_logo_keys_for_real_entries():
    assert set(_table()) == set(tos.logo_keys(_entries()))


def test_the_red_green_logo_colours_are_really_off_palette():
    assert all(_not_neutral(np.array([[c[:3]]], dtype=np.uint8)).all() for c in _LOGO_COLOURS)


def test_bars_draw_each_style_as_declared():
    image = _image(_bars_spec(), _table())
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    outlines = [r for r in layout.rects if r.outline]
    fills = [r for r in layout.rects if not r.outline and r.fill == cards._rgb(cards.ACCENT)]
    # the eliminations bar (ratio 0.004 → 3 px) is too thin for a stroke and is drawn filled
    assert len(outlines) == 0 and len(fills) == 11
    spec = _bars_spec()
    spec["segments"][0]["style"] = "outline"
    layout = nl.layout_template(_image(spec, _table()), font_path=FONT, layout_engine="basic")
    (outline,) = [r for r in layout.rects if r.outline]
    assert outline.outline == nl.OUTLINE_W
    width = layout.panel[2] - layout.panel[0] - 2 * cards.IMAGE_PANEL_PAD_X
    assert outline.box[2] - outline.box[0] == round(0.38 * width)
    assert math.isclose((outline.box[2] - outline.box[0]) / width, 0.38, abs_tol=0.002)


@pytest.mark.parametrize("mode, fmt", [("P", "PNG"), ("LA", "PNG"), ("L", "PNG"), ("1", "PNG"),
                                       ("RGB", "JPEG"), ("CMYK", "JPEG"), ("L", "JPEG")])
def test_every_logo_mode_the_web_accepts_decodes_and_draws(mode, fmt, tmp_path):
    """logo_check accepts these PNG colour types / JPEG component counts; the worker must decode
    them (logos.decode_problem) AND draw them (cards.draw_plate) — never a failed day for a logo.
    Each is a DARK (visible) image: mode LA's and CMYK's zero fill is transparent / white, which the
    visibility check (review LOGO-1, below) rightly refuses. A 16-bit GREYSCALE PNG (mode I;16) is not
    here: drawing it clips its greys to white, so the worker refuses it (review R9, at the end of this
    file)."""
    im = Image.new(mode, (160, 120), {"LA": (0, 255), "CMYK": (0, 0, 0, 255)}.get(mode, 0))
    path = tmp_path / f"logo.{fmt.lower()}"
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    path.write_bytes(buf.getvalue())
    assert lg.decode_problem(buf.getvalue(), "png" if fmt == "PNG" else "jpg") is None
    canvas = Image.new("RGB", (300, 300), cards._rgb(cards.CARD))
    cards.draw_plate(canvas, (20, 20, 220, 220), cards.LogoArt("K", "Kay", str(path)))


# ── review LOGO-1: a logo that would draw as a blank white plate is a wordmark ─────────────────


def _logo_bytes(kind: str) -> tuple:
    """(bytes, ext) of a 200×200 logo of `kind`."""
    size = (200, 200)
    if kind == "white_on_transparent":         # a dark-theme mark: white ink, transparent ground
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        im.paste((255, 255, 255, 255), (30, 30, 170, 170))
    elif kind == "transparent":
        im = Image.new("RGBA", size, (0, 0, 0, 0))
    elif kind == "white_jpeg":
        im = Image.new("RGB", size, (255, 255, 255))
    elif kind == "near_white":                 # 240 grey: ~1.1:1 on white
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        im.paste((240, 240, 240, 255), (30, 30, 170, 170))
    elif kind == "faint_alpha":                # black at 5 % opacity composites to ~243
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        im.paste((0, 0, 0, 12), (30, 30, 170, 170))
    elif kind == "speck":                      # 14×14 = 196 px < 0.5 % of 40,000
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        im.paste((0, 0, 0, 255), (0, 0, 14, 14))
    elif kind == "small_mark":                 # 15×15 = 225 px ≥ 0.5 %
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        im.paste((0, 0, 0, 255), (0, 0, 15, 15))
    elif kind == "white_on_black":             # white ink on its own dark tile: visible
        im = Image.new("RGB", size, (0, 0, 0))
        im.paste((255, 255, 255), (30, 30, 170, 170))
    elif kind == "pale_yellow":                # light, but a channel well below white
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        im.paste((255, 240, 120, 255), (30, 30, 170, 170))
    else:
        raise AssertionError(kind)
    buf = io.BytesIO()
    fmt = "JPEG" if im.mode == "RGB" and kind in ("white_jpeg", "white_on_black") else "PNG"
    im.save(buf, format=fmt)
    return buf.getvalue(), "jpg" if fmt == "JPEG" else "png"


def test_the_logo_visibility_check_composites_onto_the_cards_own_plate():
    assert lg.PLATE_RGB == cards._rgb(cards.PLATE) == (255, 255, 255)
    assert 0 < lg.MIN_VISIBLE_FRACTION <= 0.01 and 200 <= lg.VISIBLE_CHANNEL_BELOW <= 240


@pytest.mark.parametrize("kind", ["white_on_transparent", "transparent", "white_jpeg", "near_white", "faint_alpha",
                                  "speck"])
def test_a_logo_invisible_on_the_white_plate_is_refused_and_drawn_as_its_wordmark(kind, tmp_path, caplog):
    """FMP may serve a dark-theme logo (white on transparent), a transparent or a white image: every header
    check passes, and `draw_plate` would composite it onto the WHITE plate as an empty square — with no
    company name either, since a verified logo draws no wordmark. The decode refuses it instead."""
    import hashlib

    data, ext = _logo_bytes(kind)
    problem = lg.decode_problem(data, ext)
    assert problem and "invisible logo on the white plate" in problem, problem
    # what the card would have drawn: the plate alone, not one visible pixel of a logo
    path = tmp_path / f"probe.{ext}"
    path.write_bytes(data)
    canvas = Image.new("RGB", (240, 240), cards._rgb(cards.CARD))
    cards.draw_plate(canvas, (20, 20, 220, 220), cards.LogoArt("K", "Kay", str(path)))
    inner = np.asarray(canvas.crop((60, 60, 180, 180)))
    assert inner.min() >= lg.VISIBLE_CHANNEL_BELOW
    # through resolve_logos: None (the wordmark tile), a WARNING naming the key, no file written
    sha = hashlib.sha256(data).hexdigest()
    url = f"https://sb.example/storage/v1/object/public/marketing-media/logos/{sha[:32]}.{ext}"
    entry = {"key": "AAA", "name": "Alpha", "url": url, "sha256": sha, "bytes": len(data), "width": 200,
             "height": 200}
    dest = tmp_path / "out"
    dest.mkdir()
    caplog.set_level(logging.WARNING, logger="marketing.logos")
    out = lg.resolve_logos({"logos": [entry]}, bucket_origin="sb.example", download=lambda u, **k: data,
                           dest_dir=dest)
    assert out == {"AAA": None} and not list(dest.iterdir())
    assert any(r.levelno == logging.WARNING and "AAA" in r.getMessage() and "invisible" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("kind", ["small_mark", "white_on_black", "pale_yellow"])
def test_a_visible_logo_still_draws(kind):
    data, ext = _logo_bytes(kind)
    assert lg.decode_problem(data, ext) is None


def test_the_visibility_count_is_linear_and_exact():
    """`visible_pixels` counts exactly the pixels whose darkest channel, composited onto white, is below
    VISIBLE_CHANNEL_BELOW — no sampling, no Python pixel loop (a 2048² logo is 4 M pixels)."""
    im = Image.new("RGBA", (2048, 2048), (0, 0, 0, 0))
    im.paste((10, 10, 10, 255), (0, 0, 100, 37))                         # 3,700 dark
    im.paste((255, 255, lg.VISIBLE_CHANNEL_BELOW, 255), (0, 100, 50, 150))  # at the bar: not visible
    im.paste((255, 255, lg.VISIBLE_CHANNEL_BELOW - 1, 255), (0, 200, 10, 210))  # just under: 100 visible
    assert lg.visible_pixels(im) == 3_700 + 100


# ── review round 2: the Money Map's near-break-even figure fits every frame it is drawn on ──────────


def _money_map_near_zero(sign: float, *, segments: int = 2) -> Dict[str, Any]:
    """A composed Money Map whose net income (`sign` × $0.004 per $100 of revenue, a loss year's
    cost bars) rounds to $0.00 per $100 — `news_templates.money_cents` then reads `SUB_CENT`
    ("under $0.01" since review round 3)."""
    from app.services.marketing import company_news_rules as R
    from app.services.marketing import news_templates as T

    names = ["Warehouse and Membership Operations", "Products", "International Operations Segment",
             "Wholesale Distribution Services", "Fresh Foods and Grocery"][:segments]
    segs = tuple(R.Segment(n, 60e9 - i * 7e9) for i, n in enumerate(names))
    rev = sum(s.value_usd for s in segs)
    rec = R.MoneyMap(series="money_map", company=R.CompanyRef(symbol="COST", name="Costco"), fiscal_year="2025",
                     period_end=date(2025, 8, 31), segments=segs, other_usd=None, eliminations_usd=None,
                     revenue_usd=rev, gross_profit_usd=-rev * 0.01, operating_profit_usd=-rev * 0.02,
                     net_income_usd=sign * rev * 0.00004)
    return T.compose(rec, run_date=date(2025, 11, 20), store_state="live", allow_x_url=False)


@pytest.mark.parametrize("sign", [1.0, -1.0])
@pytest.mark.parametrize("segments", [2, 5])
def test_the_less_than_a_cent_figure_fits_the_cover_and_the_image(sign, segments):
    """Review round 2 (low): the sub-cent figure replaces "$0.00" on the video's opening card and in
    the image callout; review round 3 reworded it to `news_templates.SUB_CENT` ("under $0.01").
    Both still lay out — no CardOverflow, which would skip the whole day — and draw the figure whole."""
    from app.services.marketing import news_templates as T

    out = _money_map_near_zero(sign, segments=segments)
    assert out["opening_card"]["figure"] == T.SUB_CENT == "under $0.01"
    table = _table()                                                     # COST as a wordmark (the widest)
    opening = cards.layout_card(cards.opening_spec(out["opening_card"], table), font_path=FONT,
                                layout_engine="basic")
    assert opening.step <= cards.RAMP_STEPS
    assert " ".join(opening.field_lines("figure")) == T.SUB_CENT
    image = nl.template_image_for_script({"authorship": "template", "image_spec": out["image_spec"],
                                          "image_footer": out["image_footer"], "logos": _entries()}, table)
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert layout.step <= cards.RAMP_STEPS
    callout = " ".join(ln.text for ln in layout.lines if ln.field == "callout")
    assert callout == out["image_spec"]["callout"] and T.SUB_CENT in callout
    assert "$0.00" not in callout and "$0.00" not in out["opening_card"]["figure"]


# ── review round 8: the 13F image the templates compose ALWAYS fits the worker's `rows` layout ──────
#
# A CardOverflow at render skips the day after its build was accepted (it never falls back to
# another series), so the server-side bounds in news_templates — F13_FULL_TITLE_CHARS,
# F13_IMAGE_ROWS, F13_LONG_TITLE_SECTIONS, F13_MAX_WORD_CHARS — are proved here against the
# WORKER's own layout code at its floor step: in the widest glyph a name can draw ("W", checked),
# the worst word packing (an exact search over every word-length sequence), 32-character holding
# names, three kinds of moves filling every row the record can carry, 5-digit counts, an amended
# filing's longer footer, with and without a filer symbol. The server composes; the worker lays out.

import string  # noqa: E402
from functools import lru_cache  # noqa: E402

from app.services.marketing import company_news_rules as R  # noqa: E402
from app.services.marketing import news_templates as T  # noqa: E402

_F13_RUN = date(2026, 11, 17)
_F13_ENGINES = ["basic"] + (["raqm"] if cards.raqm_available() else [])
#: news_layouts._place's panel content column (the page minus its margins and the panel's padding).
_F13_COLUMN = cards.IMAGE_WIDTH - 2 * cards.IMAGE_MARGIN - 2 * cards.IMAGE_PANEL_PAD_X
#: Every character `company_news_rules` lets into a filer or company name.
_F13_NAME_ALPHABET = string.ascii_letters + string.digits + " &.,'+-"


def _floor_font(style, engine: str):
    return cards._font(FONT, style.size(cards.RAMP_STEPS), engine)


def _lines(text: str, font, width: float) -> int:
    return len(cards._wrap(text.split(), font, width, 99, "probe"))


def _worst_filer(n: int, *, before: str, after: str, own_glue: bool, font, width: float):
    """The n-character filer — words of "W" of at most F13_MAX_WORD_CHARS joined by single spaces,
    its own last word ending in "'s" when `own_glue` (the ".of" form: the name ends in a
    possessive) — whose title `before + filer + after` wraps (greedily, as the worker's `_wrap`
    does) to the MOST lines at `font` / `width`. `after` starting "'s" glues to the filer's last
    word. Exact over every word-length sequence: a DP over the greedy state (lines, line width).
    Returns (lines, filer)."""
    cap = T.F13_MAX_WORD_CHARS
    space = font.getlength("W W") - 2 * font.getlength("W")
    glue = "'s" if own_glue else ""
    tail = after.split()
    head = tail.pop(0) if tail and tail[0].startswith("'") else ""

    def put(state, w):
        lines, cw = state
        if lines == 0:
            return 1, w
        if cw + space + w <= width:
            return lines, cw + space + w
        return lines + 1, w

    start = (0, 0.0)
    for w in before.split():
        start = put(start, font.getlength(w))
    word_w = {k: font.getlength("W" * k) for k in range(1, cap + 1)}
    last_w = {k: font.getlength("W" * k + glue + head) for k in range(1, cap + 1)}
    tail_w = [font.getlength(w) for w in tail]

    @lru_cache(maxsize=None)
    def best(rem: int, lines: int, cw: float):
        out = (-1, ())
        for k in range(1, cap + 1):
            if k + len(glue) == rem and k + len(glue) <= cap:            # the filer's last word
                st = put((lines, cw), last_w[k])
                for w in tail_w:
                    st = put(st, w)
                if st[0] > out[0]:
                    out = (st[0], (k,))
            elif k + 1 < rem:                                            # a word, a space, more to come
                r = best(rem - k - 1, *put((lines, cw), word_w[k]))
                if r[0] > out[0]:
                    out = (r[0], (k,) + r[1])
        return out

    lines, seq = best(n, *start)
    filer = " ".join("W" * k for k in seq) + glue
    assert len(filer) == n and max(len(w) for w in filer.split()) <= cap
    return lines, filer


def _f13_title(filer: str) -> str:
    """news_templates' image title for this filer (the `f13.subj.*` entries, through `possessive`)."""
    poss = T.possessive(filer)
    return T.LEXICON["f13.subj.of"].format(filer=filer) if poss is None else \
        T.LEXICON["f13.subj.poss"].format(filer_s=poss)


def _worst_names(engine: str) -> List[str]:
    """Eight distinct 32-character holding names of "W" words (≤ F13_MAX_WORD_CHARS) that wrap to the
    most lines in a newly reported row's text column (its tightest: a logo plate on the left, the
    "listed …" cell on the right) at the floor — of those, the one of the FEWEST words (narration
    lists the names, and a line over LINE_WORDS would refuse the record before any image)."""
    font = _floor_font(nl.CELL_STYLE, engine)
    column = (_F13_COLUMN - nl.ROW_TILE.size(cards.RAMP_STEPS) - nl.TILE_GAP - nl.CELL_GAP
              - font.getlength("listed Aug 2026"))
    best = None
    for k in range(1, T.F13_MAX_WORD_CHARS + 1):
        words, n = [], 30
        while n > 0:
            w = min(k, n)
            words.append("W" * w)
            n -= w + 1
        body = " ".join(words)[:30].rstrip()
        body += "W" * (30 - len(body))
        lines = _lines(body + " W", font, column)
        if best is None or lines >= best[0]:
            best = (lines, body)
    assert best[0] >= 2, "the names never wrap: the probe would not reach the tall rows"
    return [f"{best[1]} {c}" for c in "ABCDEFGH"]


def _worst_13f(filer: str, symbol: Optional[str], names: List[str]):
    """Three kinds of moves filling every row a record can carry (3 newly reported — three cells,
    listed in the quarter —, 3 more, 2 fewer: THIRTEEN_F_MAX_MOVES), ranked new > more > fewer so a
    long title still draws 3 + 3 rows, 5-digit counts on all four kinds (a "+k more" under every
    section), an amended filing."""
    assert R.THIRTEEN_F_MAX_MOVES == 8
    moves = []
    for i, (kind, base) in enumerate([("newly_reported", 9.999e8)] * 3 + [("increased", 9e8)] * 3
                                     + [("decreased", 8e8)] * 2):
        co = R.CompanyRef(symbol=f"Z{'ABCDEFGH'[i]}", name=names[i])
        if kind == "newly_reported":
            moves.append(R.ThirteenFMove(company=co, move=kind, shares=1_000.0, prev_shares=None,
                                         value_usd=base - i, listed_on=date(2026, 8, 1)))
        elif kind == "increased":            # 10 → 20 shares of a 2·(base − i) position: a (base − i) move
            moves.append(R.ThirteenFMove(company=co, move=kind, shares=20.0, prev_shares=10.0,
                                         value_usd=2 * (base - i), listed_on=None))
        else:                                # 20 → 10 shares of a (base − i) position: a (base − i) move
            moves.append(R.ThirteenFMove(company=co, move=kind, shares=10.0, prev_shares=20.0,
                                         value_usd=base - i, listed_on=None))
    return R.ThirteenFFiling(
        series="thirteen_f", filer_name=filer, filer_cik="0001067983", filer_symbol=symbol, period="2026-Q3",
        period_end=date(2026, 9, 30), filed_on=date(2026, 11, 13), amended_on=date(2026, 11, 15),
        total_value_usd=5e12, position_count=99_999, moves=tuple(moves),
        counts=tuple((k, 99_999) for k in R.THIRTEEN_F_MOVES))


def _f13_image(out: Dict[str, Any]):
    entries = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None,
                "width": None, "height": None} for r in out["logo_refs"]]
    script = dict(out, logos=entries)
    table = cards.logo_table(script, {})                       # every logo a wordmark: the widest case
    return nl.template_image_for_script(script, table), table


def _floor_room(image, engine: str) -> int:
    """The panel room left at the FLOOR step (px): the panel is centred in its area, so the room is
    twice its offset from the top margin (±1 px of floor division)."""
    layout = nl._place(image, cards.RAMP_STEPS, FONT, engine)
    return 2 * (layout.panel[1] - cards.IMAGE_MARGIN)


#: The headroom every worst case must keep at the floor: one more line of the title.
_F13_HEADROOM_PX = int(round(nl.TITLE_STYLE.size(cards.RAMP_STEPS) * nl.TITLE_STYLE.leading))


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_W_is_the_widest_glyph_a_13f_name_can_draw(engine):
    """The probes below fill names with "W": no other character a name may hold is wider, at the
    floor size of the image title, the row cells and the cover headline."""
    for style in (nl.TITLE_STYLE, nl.CELL_STYLE, cards.OPENING_HEADLINE_STYLE):
        font = _floor_font(style, engine)
        widest = max(_F13_NAME_ALPHABET, key=font.getlength)
        assert font.getlength(widest) == font.getlength("W"), (style, widest)
        assert font.getlength("W" * 20) >= 20 * font.getlength("W") - 1      # no kerning shrinks a run


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_a_13f_title_within_the_threshold_is_one_line_at_the_floor(engine):
    """F13_FULL_TITLE_CHARS (31): the widest title of that length in either form — every filer
    character a "W" — is one line at the image title's floor size with a "W" to spare, so any title
    that short is (and the three-kind image is laid out for one title line). Two characters more
    and the widest title wraps: the threshold is where it must be, not a slack bound."""
    font = _floor_font(nl.TITLE_STYLE, engine)
    n = T.F13_FULL_TITLE_CHARS
    poss = "W" * (n - len("'s latest 13F")) + "'s latest 13F"
    of = "The latest 13F of " + "W" * (n - len("The latest 13F of ") - 2) + "'s"
    assert len(poss) == len(of) == n
    assert _f13_title(poss[:-len("'s latest 13F")]) == poss and _f13_title(of[len("The latest 13F of "):]) == of
    for title in (poss, of):
        assert _lines(title, font, _F13_COLUMN) == 1
        assert font.getlength(title) + font.getlength("W") <= _F13_COLUMN
    assert _lines("W" * (n + 2 - len("'s latest 13F")) + "'s latest 13F", font, _F13_COLUMN) == 2


@pytest.mark.parametrize("engine", ["basic"])
def test_the_tallest_13f_title_a_60_character_filer_can_make_is_five_lines(engine):
    """The exact worst packing (company_news_rules.FILER_NAME_CHARS = 60, words ≤ F13_MAX_WORD_CHARS)
    wraps the title to 5 lines at the floor in either form — the DP agrees with the worker's own
    `_wrap` (pinned on the basic engine, like every layout pin in this file; the fit test below
    re-runs the DP on each engine it lays out with). The image fit below is proved with THIS title
    (a superset: such a filer's many short words do not even fit a hook, so news_templates refuses
    it before any image exists)."""
    font = _floor_font(nl.TITLE_STYLE, engine)
    assert R.FILER_NAME_CHARS[1] == 60
    for before, after, own_glue in (("", "'s latest 13F", False), ("The latest 13F of ", "", True)):
        lines, filer = _worst_filer(60, before=before, after=after, own_glue=own_glue, font=font,
                                    width=_F13_COLUMN)
        assert _lines(before + filer + after, font, _F13_COLUMN) == lines == 5, (filer, lines)
        assert _f13_title(filer) == before + filer + after
        names = _worst_names(engine)
        with pytest.raises(T.NewsTemplateRefused) as e:
            T.compose(_worst_13f(filer, None, names), run_date=_F13_RUN, store_state="live", allow_x_url=False)
        assert e.value.code == "script_shape"


def _cap_words(n: int, *, own_glue: bool) -> str:
    """An n-character filer of the longest words F13_MAX_WORD_CHARS allows (the cover headline's and
    the image title's tightest case): two words at the cap, the rest in the third."""
    cap = T.F13_MAX_WORD_CHARS
    rest = n - 2 * cap - 2 - (2 if own_glue else 0)
    assert 1 <= rest <= cap
    return " ".join(["W" * cap, "W" * cap, "W" * rest]) + ("'s" if own_glue else "")


#: (filer, regime): the widest 31-character titles (full image), and 60-character filers of the
#: longest words the cap allows whose titles wrap (long).
_F13_FILERS = {
    "full_poss": ("W" * 18, "full"),
    "full_of": ("W" * 11 + "'s", "full"),
    "long_poss": (_cap_words(60, own_glue=False), "long"),
    "long_of": (_cap_words(60, own_glue=True), "long"),
}


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("case", sorted(_F13_FILERS))
def test_the_13f_image_always_fits_the_workers_rows_layout(case, engine):
    """The composed 13F image lays out whole (no CardOverflow) in the worker's `rows` layout and
    keeps at least one more title line of room at the floor; so does its cover, with and without a
    filer symbol (the image is the same either way: a 13F image has no header). A title that may
    wrap draws only the lead and the next kind; the long cases are ALSO laid out with the tallest
    title any 60-character filer can make (5 lines, above)."""
    filer, regime = _F13_FILERS[case]
    assert regime == "full" or len(filer) == R.FILER_NAME_CHARS[1] == 60
    title = _f13_title(filer)
    assert (len(title) <= T.F13_FULL_TITLE_CHARS) == (regime == "full")
    names = _worst_names(engine)
    outs = {sym: T.compose(_worst_13f(filer, sym, names), run_date=_F13_RUN, store_state="live",
                           allow_x_url=False) for sym in (None, "BRK-B")}
    assert outs[None]["image_spec"] == outs["BRK-B"]["image_spec"]
    spec = outs[None]["image_spec"]
    assert spec["title"] == title
    rows = [len(s["rows"]) for s in spec["sections"]]
    assert rows == ([3, 3, 1] if regime == "full" else [3, 3])
    assert all("more" in s for s in spec["sections"])
    assert all(len(r["cells"][0]) == 32 for s in spec["sections"] for r in s["rows"])
    image, _table_ = _f13_image(outs[None])
    layout = nl.layout_template(image, font_path=FONT, layout_engine=engine)
    _assert_draws_exactly_its_strings(image, layout)
    assert _floor_room(image, engine) >= _F13_HEADROOM_PX
    if regime == "long":
        font = _floor_font(nl.TITLE_STYLE, engine)
        own_glue = title.startswith("The latest 13F of ")
        lines, worst = _worst_filer(60, before="The latest 13F of " if own_glue else "",
                                    after="" if own_glue else "'s latest 13F", own_glue=own_glue, font=font,
                                    width=_F13_COLUMN)
        assert lines >= 5 > _lines(title, font, _F13_COLUMN) if engine == "basic" else \
            lines >= _lines(title, font, _F13_COLUMN)
        tall = json.loads(json.dumps(outs[None]))
        tall["image_spec"]["title"] = _f13_title(worst)
        tall_image, _ = _f13_image(tall)
        nl.layout_template(tall_image, font_path=FONT, layout_engine=engine)
        assert _floor_room(tall_image, engine) >= _F13_HEADROOM_PX
    for sym, out in outs.items():
        _img, table = _f13_image(out)
        opening = cards.layout_card(cards.opening_spec(out["opening_card"], table), font_path=FONT,
                                    layout_engine=engine)
        assert opening.step < cards.RAMP_STEPS, (sym, opening.step)             # room to spare


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_without_the_bounds_the_worst_13f_image_would_overflow(engine, monkeypatch):
    """Anti-vacuity: each bound is load-bearing. Under the tallest 60-character title the image
    with all three kinds (what the image drew before review round 8) does not fit; a "W" word two
    past F13_MAX_WORD_CHARS overflows the cover's headline; and the full image's 8th row
    (template_onscreen.MAX_ROWS) eats the room F13_IMAGE_ROWS keeps."""
    names = _worst_names(engine)
    font = _floor_font(nl.TITLE_STYLE, engine)
    _lines_, worst = _worst_filer(60, before="", after="'s latest 13F", own_glue=False, font=font,
                                  width=_F13_COLUMN)
    monkeypatch.setattr(T, "F13_FULL_TITLE_CHARS", 10 ** 6)                  # the long title, 3 kinds
    out = T.compose(_worst_13f(_F13_FILERS["long_poss"][0], None, names), run_date=_F13_RUN,
                    store_state="live", allow_x_url=False)
    assert [len(s["rows"]) for s in out["image_spec"]["sections"]] == [3, 3, 1]
    out["image_spec"]["title"] = _f13_title(worst)
    with pytest.raises(cards.CardOverflow):
        nl.layout_template(_f13_image(out)[0], font_path=FONT, layout_engine=engine)
    monkeypatch.undo()
    # the word cap: the cover headline with a word two past it does not fit at the floor
    out = T.compose(_worst_13f(_F13_FILERS["long_poss"][0], None, names), run_date=_F13_RUN,
                    store_state="live", allow_x_url=False)
    _img, table = _f13_image(out)
    cap = T.F13_MAX_WORD_CHARS
    wide = dict(out["opening_card"], headline=out["opening_card"]["headline"].replace("W" * cap, "W" * (cap + 2), 1))
    assert "W" * (cap + 2) in wide["headline"]
    with pytest.raises(cards.CardOverflow):
        cards.layout_card(cards.opening_spec(wide, table), font_path=FONT, layout_engine=engine)
    # the row budget: an 8th row under a one-line title leaves less than a title line of room
    monkeypatch.setattr(T, "F13_IMAGE_ROWS", tos.MAX_ROWS)
    out = T.compose(_worst_13f(_F13_FILERS["full_poss"][0], None, names), run_date=_F13_RUN,
                    store_state="live", allow_x_url=False)
    assert sum(len(s["rows"]) for s in out["image_spec"]["sections"]) == tos.MAX_ROWS == 8
    assert _floor_room(_f13_image(out)[0], engine) < _F13_HEADROOM_PX


# ── drop 2b: the images and videos news_templates composes for the four 2b series ALWAYS fit ──────
#
# Same proof as the 13F one above, for congress_count (`spotlight`), earnings (`rows`),
# company_stakes (`pair`) and theme_explainer (`grid`): the server-side bounds in news_templates —
# NAME_WORD_MAX_CHARS, PAIR_NAME_WORD_MAX_CHARS, GRID_NAME_WORD_MAX_CHARS, GRID_LINE_WORD_MAX_CHARS,
# GRID_MAX_LINES (through `_char_lines`), STAKE_NAME_MAX_CHARS, STAKE_SOURCE_MAX_CHARS — are proved
# against the WORKER's own layout code at its floor step, in the widest glyph ("W"), the worst word
# packing and the longest figures, for the post image AND every card of the video (the cover, the
# four text cards, the disclaimer). `pair` and `grid` shipped with their series (drop 2b): the
# `shipped_pair_grid` fixture below now restates the real state, kept so the proof holds whatever ships.

_2B_STYLES = (nl.PAIR_NAME_STYLE, nl.GRID_NAME_STYLE, nl.GRID_LINE_STYLE, nl.NAME_STYLE, nl.LINE_STYLE)
#: The pair's half column and the grid tile's text column at the floor (news_layouts._lay_pair /
#: _lay_grid's own arithmetic, on the panel's content column).
_PAIR_HALF = (_F13_COLUMN - nl.PAIR_HALF_GAP) // 2
_GRID_TEXT = ((_F13_COLUMN - (nl.GRID_COLUMNS - 1) * nl.GRID_COL_GAP) // nl.GRID_COLUMNS
              - nl.GRID_TILE.size(cards.RAMP_STEPS) - nl.GRID_TEXT_GAP)


@pytest.fixture
def shipped_pair_grid(monkeypatch):
    monkeypatch.setattr(tos, "SHIPPED_LAYOUTS", tos.LAYOUTS)
    monkeypatch.setattr(cards, "SHIPPED_LAYOUTS", cards.LAYOUTS)
    for layout in ("pair", "grid"):
        monkeypatch.setitem(nl._WALKERS, layout, nl.DRAWERS[layout][0])
        monkeypatch.setitem(nl._LAYOUTS, layout, nl.DRAWERS[layout][1])


def _w_cap(style, width: float, engine: str) -> int:
    font = _floor_font(style, engine)
    k = 0
    while font.getlength("W" * (k + 1)) <= width:
        k += 1
    return k


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_the_2b_word_caps_are_the_widest_W_runs_that_fit_at_the_floor(engine):
    """Each per-word cap is EXACTLY the longest run of the widest glyph that fits its column at the
    floor size — not a slack bound (one more "W" does not fit) — and W is the widest glyph a name
    can hold in every 2b style."""
    for style in _2B_STYLES:
        font = _floor_font(style, engine)
        assert font.getlength(max(_F13_NAME_ALPHABET, key=font.getlength)) == font.getlength("W"), style
    assert _w_cap(nl.PAIR_NAME_STYLE, _PAIR_HALF, engine) == T.PAIR_NAME_WORD_MAX_CHARS
    assert _w_cap(nl.GRID_NAME_STYLE, _GRID_TEXT, engine) == T.GRID_NAME_WORD_MAX_CHARS
    assert _w_cap(nl.GRID_LINE_STYLE, _GRID_TEXT, engine) == T.GRID_LINE_WORD_MAX_CHARS
    header_col = _F13_COLUMN - nl.HEADER_TILE.size(cards.RAMP_STEPS) - nl.TILE_GAP
    assert _w_cap(nl.NAME_STYLE, header_col, engine) >= T.NAME_WORD_MAX_CHARS        # header names
    assert _w_cap(nl.TITLE_STYLE, _F13_COLUMN, engine) == T.THEME_TITLE_LINE_CHARS >= T.NAME_WORD_MAX_CHARS
    assert _w_cap(nl.LINE_STYLE, _F13_COLUMN, engine) >= T.NAME_WORD_MAX_CHARS       # a stake's source line


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_char_lines_is_an_upper_bound_on_the_workers_wrap(engine):
    """`news_templates._char_lines` (a greedy wrap by characters) never says fewer lines than the
    worker draws, at the grid's and the pair's floor columns — seeded strings over every character
    a name may hold, "W"-heavy, with every word inside the cap."""
    rng = random.Random(20261010)
    alphabet = _F13_NAME_ALPHABET.replace(" ", "") + "W" * 40
    for style, width, cap in ((nl.GRID_NAME_STYLE, _GRID_TEXT, T.GRID_NAME_WORD_MAX_CHARS),
                              (nl.GRID_LINE_STYLE, _GRID_TEXT, T.GRID_LINE_WORD_MAX_CHARS),
                              (nl.PAIR_NAME_STYLE, _PAIR_HALF, T.PAIR_NAME_WORD_MAX_CHARS)):
        font = _floor_font(style, engine)
        for _ in range(400):
            words = ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, cap)))
                     for _ in range(rng.randint(1, 6))]
            text = " ".join(words)
            assert T._char_lines(text, cap) >= _lines(text, font, width), (style, text)


def _worst_packing(n: int, cap: int, font, width: float):
    """The n-character name of "W" words of at most `cap` characters, single-spaced, whose greedy
    wrap (the worker's `_wrap`) at `font` / `width` takes the MOST lines — exact, a DP over every
    word-length sequence. Returns (lines, name)."""
    space = font.getlength("W W") - 2 * font.getlength("W")
    word_w = {k: font.getlength("W" * k) for k in range(1, cap + 1)}

    def put(state, w):
        lines, cw = state
        if lines and cw + space + w <= width:
            return lines, cw + space + w
        return lines + 1, w

    @lru_cache(maxsize=None)
    def best(rem: int, lines: int, cw: float):
        out = (-1, ())
        for k in range(1, cap + 1):
            if k == rem:
                st = put((lines, cw), word_w[k])
                if st[0] > out[0]:
                    out = (st[0], (k,))
            elif k + 1 < rem:
                r = best(rem - k - 1, *put((lines, cw), word_w[k]))
                if r[0] > out[0]:
                    out = (r[0], (k,) + r[1])
        return out

    lines, seq = best(n, 0, 0.0)
    name = " ".join("W" * k for k in seq)
    assert len(name) == n and _lines(name, font, width) == lines
    return lines, name


def _co(sym: str, name: str):
    return R.CompanyRef(symbol=sym, name=name)


def _assert_2b_draws_its_strings(image: nl.TemplateImage, layout) -> None:
    """Every text item of the walk is drawn whole; the only other lines are wordmark plates, each
    drawing its own (declared) name — a pair's private investee is a plate keyed by its field, and a
    grid wordmark draws no plate at all (test_marketing_layouts_2b's rule)."""
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
        assert arts and all(a.wordmark for a in arts)
        assert drawn[field] == arts[0].name.split() * len(arts) and arts[0].name in image.strings


def _lay_out_everything(out: Dict[str, Any], engine: str) -> int:
    """Lays out the composed post image and EVERY card of its video with every logo a wordmark (the
    widest case); returns the image's room at the floor (px)."""
    image, table = _f13_image(out)
    layout = nl.layout_template(image, font_path=FONT, layout_engine=engine)
    _assert_2b_draws_its_strings(image, layout)
    script = dict(out, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None,
                               "width": None, "height": None} for r in out["logo_refs"]])
    specs = cards.cards_for_script(script, table)
    assert [s.kind for s in specs] == ["opening", "text", "text", "text", "text", "disclaimer"]
    for spec in specs:
        card = cards.layout_card(spec, font_path=FONT, layout_engine=engine)
        assert card.step <= cards.RAMP_STEPS
        if spec.kind == "opening":
            assert card.step < cards.RAMP_STEPS                                 # the cover keeps room to spare
    return _floor_room(image, engine)


def _compose(rec, run_date: date) -> Dict[str, Any]:
    return T.compose(rec, run_date=run_date, store_state="live", allow_x_url=False)


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_the_congress_count_image_and_video_fit(engine):
    font = _floor_font(nl.NAME_STYLE, engine)
    header_col = _F13_COLUMN - nl.HEADER_TILE.size(cards.RAMP_STEPS) - nl.TILE_GAP
    _l, name = _worst_packing(R.COMPANY_NAME_CHARS[1], T.NAME_WORD_MAX_CHARS, font, header_col)
    if len(name.split()) > 5:                       # the hook allows up to 5 company words; use the 2-word worst
        name = "W" * 20 + " " + "W" * 11
    rec = R.CongressCount(series="congress_count", company=_co("MMMMM-A", name), month="2026-09", members=535,
                          fetched_on=date(2026, 10, 13))
    out = _compose(rec, date(2026, 10, 13))
    assert out["opening_card"]["kicker"] == "CONGRESS · DISCLOSED IN SEPTEMBER"    # the longest month
    assert _lay_out_everything(out, engine) >= _F13_HEADROOM_PX


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_the_earnings_image_and_video_fit(engine):
    name = "W" * 20 + " " + "W" * 11
    rec = R.EarningsReport(series="earnings", company=_co("MMMMM-A", name), report_date=date(2026, 11, 5),
                           period_end=date(2026, 9, 30), eps_actual=-12_345.67, eps_estimate=-12_000.12,
                           revenue_actual=999_940_000_000.0, revenue_estimate=998_900_000_000.0)
    out = _compose(rec, date(2026, 11, 12))
    assert out["image_spec"]["sections"][0]["rows"][0]["cells"][-1] == "-$12,345.67"
    assert _lay_out_everything(out, engine) >= _F13_HEADROOM_PX


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("investee_logo", [False, True])
def test_the_company_stake_image_and_video_fit(engine, investee_logo, shipped_pair_grid):
    """The tallest names a pair can hold (the exact worst packing of a 32-character investor and a
    40-character investee at PAIR_NAME_WORD_MAX_CHARS — names the server's name rules accept; their
    many words only fail the narration hook, so the image is proved as a superset, like the 13F
    "tall title"), an 80-character source of 20-character words, the widest figure and label, and
    the longest cover headline."""
    font = _floor_font(nl.PAIR_NAME_STYLE, engine)
    _li, inv = _worst_packing(R.COMPANY_NAME_CHARS[1], T.PAIR_NAME_WORD_MAX_CHARS, font, _PAIR_HALF)
    lines_ee, ee = _worst_packing(T.STAKE_NAME_MAX_CHARS, T.PAIR_NAME_WORD_MAX_CHARS, font, _PAIR_HALF)
    assert lines_ee >= 5
    for name in (inv, ee):
        T._name_slot(name, "probe", T.PAIR_NAME_WORD_MAX_CHARS)          # the server would accept it
    source = " ".join(["W" * 19] * 4) + "W"
    assert len(source) == T.STAKE_SOURCE_MAX_CHARS
    rec = R.CompanyStake(series="company_stakes", stake_id="5f0c8a52-6a5e-4d4f-9a52-0d9c3e1b7a01",
                         investor=_co("MMMMM-A", "Northwind"), investee_name="Fabrikam",
                         investee=_co("MMMMM-B", "Fabrikam") if investee_logo else None,
                         kind="us_listed_off_13f" if investee_logo else "private", value_usd=999_940_000_000.0,
                         value_basis="invested", ownership_pct=None, as_of=date(2026, 9, 30),
                         verified_on=date(2026, 10, 1), source_title=source, background=None,
                         listed_since=None, local_listing=None, is_new=False)
    out = _compose(rec, date(2026, 12, 29))
    assert out["image_spec"]["figure"] == "$999.9B" and out["image_spec"]["lines"][1] == f"Source: {source}"
    tall = json.loads(json.dumps(out))
    tall["image_spec"]["left"]["name"], tall["image_spec"]["right"]["name"] = inv, ee
    tall["image_spec"]["label"] = "committed up to"                         # the widest label
    tall["logo_refs"] = [{"key": r["key"], "name": inv if r["key"] == "MMMMM-A" else ee} for r in out["logo_refs"]]
    tall["opening_card"]["headline"] = T.LEXICON["st.open.committed_up_to"].format(inv=inv, ee=ee)
    assert _lay_out_everything(tall, engine) >= _F13_HEADROOM_PX


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_the_theme_grid_image_and_video_fit(engine, shipped_pair_grid):
    """Twelve tiles, each with the tallest name and line the server lets onto a tile (GRID_MAX_LINES
    each), the tallest title it accepts (THEME_TITLE_MAX_LINES), "+12 more"."""
    title_font = _floor_font(nl.TITLE_STYLE, engine)
    title = " ".join(["W" * 20, "W" * 20, "W" * 18])
    assert len(title) == 60 and T._char_lines(title, T.THEME_TITLE_LINE_CHARS) == T.THEME_TITLE_MAX_LINES
    assert _lines(title, title_font, _F13_COLUMN) == T.THEME_TITLE_MAX_LINES
    tile_name = "W" * 14 + " " + "W" * 14
    segment = "W" * 17 + " " + "W" * 18
    assert T._char_lines(tile_name, T.GRID_NAME_WORD_MAX_CHARS) == T.GRID_MAX_LINES
    assert T._char_lines(segment, T.GRID_LINE_WORD_MAX_CHARS) == T.GRID_MAX_LINES
    syms = [f"{a}{b}" for a in "ABCDEFGHJKLM" for b in "AB"]
    members = tuple(R.ThemeMember(company=_co(s, tile_name), top_segment=segment, top_segment_share=0.995,
                                  fiscal_year="2026") for s in syms)
    rec = R.ThemeExplainer(series="theme_explainer", slug="worst", title=title, members=members,
                           tickers_as_of=date(2026, 11, 1))
    out = _compose(rec, date(2026, 11, 16))
    tiles = out["image_spec"]["tiles"]
    assert len(tiles) == 12 and out["image_spec"]["more"] == "+12 more"
    assert all(t["name"] == tile_name and t["line"] == segment for t in tiles)
    assert _lay_out_everything(out, engine) >= _F13_HEADROOM_PX


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_without_the_2b_bounds_the_worst_images_would_overflow(engine, shipped_pair_grid, monkeypatch):
    """Anti-vacuity: under the tallest accepted title, a grid of twelve THREE-line names (what
    GRID_MAX_LINES refuses) does not fit at the floor; nor does the tallest title a 60-character
    theme can make (what THEME_TITLE_MAX_LINES refuses) over twelve two-line tiles; and a pair name
    word one "W" past PAIR_NAME_WORD_MAX_CHARS cannot be drawn whole."""
    tall = " ".join(["W" * 20, "W" * 20, "W" * 18])
    title_bound = T.THEME_TITLE_MAX_LINES
    for name_lines, title in ((3, tall), (2, None)):
        monkeypatch.setattr(T, "GRID_MAX_LINES", name_lines)
        monkeypatch.setattr(T, "THEME_TITLE_MAX_LINES", 3 if title else 99)
        name = "W" * 14 + " " + "W" * 14 + (" " + "W" * 2 if name_lines == 3 else "")
        if title is None:
            _lt, title = _worst_packing(60, T.NAME_WORD_MAX_CHARS, _floor_font(nl.TITLE_STYLE, engine), _F13_COLUMN)
            assert _lines(title, _floor_font(nl.TITLE_STYLE, engine), _F13_COLUMN) > title_bound
        members = tuple(R.ThemeMember(company=_co(f"Z{a}", name), top_segment="W" * 17 + " " + "W" * 18,
                                      top_segment_share=None, fiscal_year="2026") for a in "ABCDEFGHJKLMN")
        rec = R.ThemeExplainer(series="theme_explainer", slug="worst", title=title, members=members,
                               tickers_as_of=date(2026, 11, 1))
        out = _compose(rec, date(2026, 11, 16))
        assert len(out["image_spec"]["tiles"]) == 12
        with pytest.raises(cards.CardOverflow):
            nl.layout_template(_f13_image(out)[0], font_path=FONT, layout_engine=engine)
    monkeypatch.undo()
    monkeypatch.setattr(tos, "SHIPPED_LAYOUTS", tos.LAYOUTS)
    monkeypatch.setattr(cards, "SHIPPED_LAYOUTS", cards.LAYOUTS)
    for layout in ("pair", "grid"):
        monkeypatch.setitem(nl._WALKERS, layout, nl.DRAWERS[layout][0])
        monkeypatch.setitem(nl._LAYOUTS, layout, nl.DRAWERS[layout][1])
    wide = "W" * (T.PAIR_NAME_WORD_MAX_CHARS + 1)
    monkeypatch.setattr(T, "PAIR_NAME_WORD_MAX_CHARS", len(wide))
    rec = R.CompanyStake(series="company_stakes", stake_id="5f0c8a52-6a5e-4d4f-9a52-0d9c3e1b7a01",
                         investor=_co("NVDA", "NVIDIA"), investee_name=wide, investee=None, kind="private",
                         value_usd=777_399_382.46, value_basis="invested", ownership_pct=None,
                         as_of=date(2026, 3, 27), verified_on=date(2026, 9, 24), source_title="Nscale Form S-1",
                         background=None, listed_since=None, local_listing=None, is_new=False)
    out = _compose(rec, date(2026, 12, 29))
    with pytest.raises(cards.CardOverflow):
        nl.layout_template(_f13_image(out)[0], font_path=FONT, layout_engine=engine)


#: A video text card's text column (cards._stack_for: the safe zone minus the panel's padding).
_CARD_COLUMN = cards.SAFE_X[1] - cards.SAFE_X[0] - 2 * cards.PANEL_PAD_X


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_the_card_title_word_cap_is_the_widest_W_run_a_card_title_holds(engine):
    """Only the theme's video cards are titled with company names in 2b: CARD_TITLE_WORD_MAX_CHARS is
    exactly the longest "W" run a text card's title draws at the floor; a body holds every name word
    (NAME_WORD_MAX_CHARS) — where the names go when a title cannot hold them."""
    assert _w_cap(cards.TITLE_STYLE, _CARD_COLUMN, engine) == T.CARD_TITLE_WORD_MAX_CHARS
    assert _w_cap(cards.BODY_STYLE, _CARD_COLUMN, engine) >= T.NAME_WORD_MAX_CHARS
    for style in (cards.TITLE_STYLE, cards.BODY_STYLE):
        font = _floor_font(style, engine)
        assert font.getlength(max(_F13_NAME_ALPHABET, key=font.getlength)) == font.getlength("W")


def _theme_with_narrated(name: str):
    """Six narrated members called `name` (the facts go first, theme order), then three short ones
    the grid can draw; segment lines at the tile bound."""
    seg = "W" * 17 + " " + "W" * 18
    members = [R.ThemeMember(company=_co(f"L{a}", name), top_segment=seg, top_segment_share=0.5, fiscal_year="2026")
               for a in "ABCDEF"]
    members += [R.ThemeMember(company=_co(f"S{a}", "Wwwwwwwwww"), top_segment="Cloud", top_segment_share=0.5,
                              fiscal_year="2026") for a in "ABC"]
    return R.ThemeExplainer(series="theme_explainer", slug="cards", title="AI chips", members=tuple(members),
                            tickers_as_of=date(2026, 11, 1))


#: name → whether the card titles name the members: the widest title words the cap allows (2 words,
#: 17 + 14), the tallest title (3 × 10-character words a name: 7 title lines), and a 20-character
#: word (the generic title, the names in the body).
_THEME_CARD_NAMES = {"W" * 17 + " " + "W" * 14: True, " ".join(["W" * 10] * 3): True,
                     "W" * 20 + " " + "W" * 11: False}


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("name", sorted(_THEME_CARD_NAMES))
def test_the_theme_video_cards_fit_whatever_the_member_names(engine, name, shipped_pair_grid):
    out = _compose(_theme_with_narrated(name), date(2026, 11, 16))
    titles = [c["title"] for c in out["cards"][:3]]
    if _THEME_CARD_NAMES[name]:
        assert all(name in t for t in titles), titles
    else:
        assert titles == ["Largest segments"] * 3 and all(name in c["body"] for c in out["cards"][:3])
    _lay_out_everything(out, engine)


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_without_the_card_title_cap_a_long_name_word_overflows_the_card(engine, shipped_pair_grid, monkeypatch):
    monkeypatch.setattr(T, "CARD_TITLE_WORD_MAX_CHARS", T.NAME_WORD_MAX_CHARS)
    out = _compose(_theme_with_narrated("W" * 20 + " " + "W" * 11), date(2026, 11, 16))
    assert ("W" * 20) in out["cards"][0]["title"]
    _image, table = _f13_image(out)
    script = dict(out, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None}
                              for r in out["logo_refs"]])
    with pytest.raises(cards.CardOverflow):
        cards.layout_card(cards.cards_for_script(script, table)[1], font_path=FONT, layout_engine=engine)


# ── review R9 (low): a 16-bit greyscale PNG logo would be drawn with its greys erased ──────────────


def _png16(colour: int, bands: tuple, size: tuple = (120, 120)) -> bytes:
    """A hand-built 16-bit PNG (stdlib only): colour type `colour` (0 grey, 2 RGB, 4 grey+alpha), three
    equal column bands at the 16-bit grey levels `bands` (alpha opaque)."""
    import struct
    import zlib

    w, h = size
    channels = {0: 1, 2: 3, 4: 2}[colour]
    row = bytearray(b"\x00")                    # filter type 0 per scanline
    for x in range(w):
        level = bands[min(x * len(bands) // w, len(bands) - 1)]
        px = [level] * (3 if colour == 2 else 1) + ([0xFFFF] if colour == 4 else [])
        assert len(px) == channels
        row += b"".join(struct.pack(">H", v) for v in px)
    raw = bytes(row) * h

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, colour, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


_GREY_BANDS = (0x0000, 0x8000, 0xFFFF)      # black · 50 % grey · white


def test_a_16_bit_greyscale_png_logo_is_refused_so_its_greys_are_never_drawn_as_white():
    """Pillow opens a 16-bit greyscale PNG as mode I;16, and `convert("RGBA")` (what cards.draw_plate
    runs) CLIPS every sample above 255 to white instead of scaling it: the 50 % grey band would be drawn
    as white while the black band still passes the visibility bar — an ALTERED logo with no wordmark.
    The worker refuses it (the wordmark is drawn), whatever its greys."""
    data = _png16(0, _GREY_BANDS)
    with Image.open(io.BytesIO(data)) as im:
        assert im.mode.startswith("I;16")
        clipped = im.convert("RGBA").getpixel((60, 60))
    assert clipped == (255, 255, 255, 255)        # why: the grey band really is erased by the draw path
    problem = lg.decode_problem(data, "png")
    assert problem is not None and "16/32-bit" in problem, problem
    # an all-black one too (the old test's case: it hid the clipping) — refused by MODE, not by its pixels
    assert lg.decode_problem(_png16(0, (0x0000,)), "png") is not None


@pytest.mark.parametrize("mode", ["I;16", "I;16B", "I;16L", "I;16N", "I", "F"])
def test_every_wide_sample_mode_is_refused_and_no_8_bit_mode_is(mode):
    assert lg.wide_sample_mode(mode)
    for ok in ("1", "L", "LA", "P", "PA", "RGB", "RGBA", "CMYK", "La", "RGBa"):
        assert not lg.wide_sample_mode(ok), ok
    assert not lg.wide_sample_mode(None)


@pytest.mark.parametrize("colour", [2, 4], ids=["rgb16", "grey_alpha16"])
def test_other_16_bit_pngs_still_decode_and_draw_their_greys(colour, tmp_path):
    """No over-block: a 16-bit RGB or grey+alpha PNG opens as RGB / RGBA (8-bit, correctly scaled), so it
    is drawn unaltered — its 50 % grey band stays grey on the plate."""
    data = _png16(colour, _GREY_BANDS)
    assert lg.decode_problem(data, "png") is None
    with Image.open(io.BytesIO(data)) as im:
        assert not lg.wide_sample_mode(im.mode)
        r, g, b, _a = im.convert("RGBA").getpixel((60, 60))
    assert 120 <= r == g == b <= 136, (r, g, b)
    path = tmp_path / "logo.png"
    path.write_bytes(data)
    canvas = Image.new("RGB", (300, 300), cards._rgb(cards.CARD))
    cards.draw_plate(canvas, (20, 20, 220, 220), cards.LogoArt("K", "Kay", str(path)))
    greys = [px for px in canvas.getdata() if 100 <= px[0] <= 160 and px[0] == px[1] == px[2]]
    assert greys, "the grey band must survive the draw"
