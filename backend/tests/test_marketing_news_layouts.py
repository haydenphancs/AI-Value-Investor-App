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
* **Every cover fits with its chip** (news-v19, Batch 1): the period chips the earnings, Money Map
  and symbol-less 13F covers carry now ("Quarter ended …", "Reported …", "Fiscal …"), at their
  widest over every date, on each series' tallest cover — and the Form 4 cover's single headline
  form — lay out with the worker's own opening-card code before the floor.
* **The data card is the post image, legible, or nothing** (Batch 2, 2026-10-19): the same picture laid
  out in the video card's text zone, every drawn line at least DATA_CARD_MIN_PX (a wordmark plate
  exempt), whole, inside the zone — a dense picture is refused (CardOverflow: the text card stays), and
  the search stops at the first illegible step. The post image itself did not move a pixel (goldens).

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
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

PIL = pytest.importorskip("PIL")
np = pytest.importorskip("numpy")
from PIL import Image  # noqa: E402

from app.schemas import marketing as sch  # noqa: E402
from app.services.marketing import logo_check, post_copy  # noqa: E402
from app.services.marketing import template_onscreen as tos  # noqa: E402
from marketing import cards  # noqa: E402
from marketing import logos as lg  # noqa: E402
from marketing import look  # noqa: E402
from marketing import news_layouts as nl  # noqa: E402

_BACKEND = Path(__file__).resolve().parents[1]
FONT = str(_BACKEND / "marketing" / "assets" / "fonts" / "Inter-Bold.ttf")
_ONSCREEN = _BACKEND / "app" / "services" / "marketing" / "template_onscreen.py"
_LOGO_CHECK = _BACKEND / "app" / "services" / "marketing" / "logo_check.py"
RUN_DATE = date(2026, 11, 16)
# news-v23 (restyle 2026-11-19): a family's one short footer line
FOOTER = post_copy.image_footer(RUN_DATE, "template", source=post_copy.FOOTER_SOURCE_FORM4,
                                when="filed Nov 16, 2026")

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


def _feature_spec() -> Dict[str, Any]:
    """The `feature` layout (2026-11-15) — the design's worked insider-spotlight example (a cluster Form 4), drawn
    on the GameStop fixture logo: header, figure, its label, a two-line headline, four fact rows."""
    return {"layout": "feature", "version": 1, "kicker": "FILED NOV 17–18 · FORM 4", "footer": FOOTER,
            "header": {"logo": "GME", "name": "GameStop", "chip": "GME"}, "figure": "$2M",
            "figure_label": "of stock, per the Form 4",
            "headline": "GameStop's CEO and at least 2 other insiders disclosed buying stock",
            "facts": [{"label": "Trade dates", "value": "Nov 16–17, 2026"}, {"label": "Shares", "value": "14K"},
                      {"label": "Cost per share, as filed", "value": "$142.10–$143.85"},
                      {"label": "Other insiders", "value": "At least 2 more"}]}


SPECS = {"rows": _rows_spec, "spotlight": _spotlight_spec, "bars": _bars_spec, "feature": _feature_spec}


@pytest.mark.parametrize("name", sorted(SPECS))
def test_every_fixture_spec_is_one_the_server_accepts(name):
    spec = SPECS[name]()
    assert tos.validate_image_spec(spec, tos.logo_keys(_entries()), footer=FOOTER) is None


def _image(spec: Dict[str, Any], table: Dict[str, cards.LogoArt]) -> nl.TemplateImage:
    return nl.template_image_for_script({"authorship": "template", "look": cards.LOOK, "image_spec": spec, "image_footer": FOOTER,
                                         "logos": _entries(), "look": cards.LOOK}, table)


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
             "COMMON_KEYS", "LAYOUT_KEYS", "OPENING_KEYS",
             # 2026-11-15, the `feature` layout: its fact rows, and which layouts may be a video's data card
             "MIN_FACTS", "MAX_FACTS", "FACT_KEYS", "DATA_CARD_LAYOUTS",
             # news-v23 (2026-11-19): the list cover's keys, and the earnings table (section columns, row lines)
             "OPENING_LIST_KEYS", "MAX_OPENING_ROWS", "TABLE_CELLS", "MAX_TABLE_LINES", "TABLE_KEYS",
             # news-v23 (company stakes, stakes_nvidia.jpg): a row's keys — its basis word `label` — and the label's cap
             "ROW_KEYS", "ROW_LABEL_MAX_CHARS",
             # news-v23 phase 3 (the weekly theme post): the ranked rows' keys, the icon tokens, a tile's chip
             "RANKED_ROW_KEYS", "THEME_ICONS", "GRID_TILE_KEYS", "ROW_CHIP_MAX_CHARS",
             # 2026-11-20, the theme's last three angles: the tables, their rows' bounds and keys, the empty cell, the
             # minus sign and a table cover's rows
             "THEME_TABLE_LAYOUTS", "MIN_THEME_TABLE_ROWS", "MAX_THEME_TABLE_ROWS", "THEME_TABLE_ROW_KEYS", "MINUS",
             "EMPTY_CELL", "MAX_OPENING_TABLE_ROWS",
             # 2026-11-20, the Money Map cover: the opening card's figure label and its cap
             "OPENING_FIGURE_LABEL_MAX_CHARS",
             # owner decision 2026-11-20 ("A: costs vs revenue"): a loss year's costs card — its keys, its parts, its
             # callout's two lines
             "COSTS_KEYS", "COSTS_BAR_KEYS", "COSTS_NET_KEYS", "COSTS_PARTS", "COSTS_CALLOUT_LINES")


def test_the_layouts_mirror_the_server_file_text():
    """`cards.LAYOUTS` and the closed schema mirror template_onscreen.py — read from its TEXT (the
    worker cannot import app.*), and from the imported module for good measure."""
    text = _literal_assignments(_ONSCREEN)
    for name in _MIRRORED:
        assert name in text, f"template_onscreen.py no longer assigns {name} as a literal"
        assert getattr(cards, name) == text[name] == getattr(tos, name), name
    assert cards.LAYOUTS == ("rows", "spotlight", "pair", "bars", "grid", "feature", "ranked", "figures", "ledger",
                             "tally")
    # every layout has shipped since drop 2b (`feature` since 2026-11-15, `ranked` since news-v23 phase 3); a layout
    # the worker cannot draw is never shipped by the server
    assert cards.SHIPPED_LAYOUTS == tos.SHIPPED_LAYOUTS == cards.LAYOUTS
    # `feature` is never a data card, on either side (owner decision 5: the cost per share stays off video), nor the
    # theme's `ranked` list (a theme post draws no data card: its cover IS the list)
    assert cards.DATA_CARD_LAYOUTS == tos.DATA_CARD_LAYOUTS == tuple(
        x for x in cards.LAYOUTS if x not in ("feature", "ranked", "figures", "ledger", "tally"))
    # the tuple-unpacked bounds the text read cannot see
    assert (cards.MIN_RANKED_ROWS, cards.MAX_RANKED_ROWS) == (tos.MIN_RANKED_ROWS, tos.MAX_RANKED_ROWS) == (4, 10)
    assert "MIN_RANKED_ROWS, MAX_RANKED_ROWS = 4, 10" in _ONSCREEN.read_text(encoding="utf-8")
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


#: news-v23: fields the look draws besides the spec's strings — the brand row's word (declared: drawn_strings).
_LOOK_FIELDS = {"brand"}


def _words_by_field(layout) -> Dict[str, List[str]]:
    """Each field's drawn words, in order, case-folded (news-v23: a headline is drawn upper-cased), without the " · "
    separators a stat tile turns into the gap between two tiles; a field drawn again elsewhere ("flow[0].label#total",
    the stacked bar's total) is its own entry."""
    out: Dict[str, List[str]] = {}
    for ln in layout.lines:
        out.setdefault(ln.field, []).extend(w.casefold() for w in ln.text.split() if w != "·")
    return out


def _assert_tickers_stand_in(image, layout, drawn) -> set:
    """The `ticker:<key>` fields a layout drew (final review of 2026-11-26: a small theme-row disc of a MISSING logo
    draws its row's ticker chip — `look.Page.disc(ticker=…)`): each over a WORDMARK disc only, each line exactly that
    logo's ticker, and that ticker a string the picture already draws and declares (the row's chip) — so the server's
    allow-list already holds it. Returns the fields."""
    fields = {f for f in drawn if f.startswith(look.TICKER_FIELD_PREFIX)}
    for field in fields:
        key = field.split(":", 1)[1]
        arts = [a for _b, a in layout.tiles if a.key == key]
        assert arts and all(a.wordmark for a in arts), f"a verified logo {key} drew its ticker"
        assert drawn[field] and set(drawn[field]) == {key.casefold()}, (field, drawn[field])
        assert key in image.strings, f"{key}: a ticker drawn in a disc must be a declared string (the row's chip)"
    return fields


def _spec_words(value: str) -> List[str]:
    return [w.casefold() for w in value.split() if w != "·"]


def _assert_draws_exactly_its_strings(image: nl.TemplateImage, layout, *, undrawn: Sequence[str] = (),
                                      items: Optional[Sequence[Any]] = None) -> None:
    """Every text item of the walk is drawn whole (its words, in order, from its wrapped lines);
    every wordmark plate draws its company name (or nothing); no other field exists. `undrawn`: walk paths the layout
    leaves out by design (the 13F list's and the Money Map's plate name: the pill draws the TICKER, the title already
    names the company) — each must then really be absent. `items`: the walk to hold it to (default `nl.walk` of the
    spec — a costs cover passes `nl.walk_costs_cover`'s)."""
    items = nl.walk(image.spec, image.logos) if items is None else items
    drawn = _words_by_field(layout)
    expected = {}
    for kind, path, value in items:
        if kind == "text" and path not in undrawn:
            expected[path] = _spec_words(value)
    for path in undrawn:
        assert path not in drawn, path
    for path, words in expected.items():
        assert drawn.get(path) == words, (path, drawn.get(path), words)
    for field, words in drawn.items():                       # a field drawn twice: each copy is the whole string
        if "#" in field:
            assert words == expected[field.split("#")[0]], field
    wordmark_fields = {f for f in drawn if f.startswith("wordmark:")}
    ticker_fields = _assert_tickers_stand_in(image, layout, drawn)
    assert drawn.get("brand") == [look.BRAND_ROW_WORD.casefold()]
    assert set(drawn) == (set(expected) | wordmark_fields | ticker_fields | _LOOK_FIELDS
                          | {f for f in drawn if "#" in f})
    plates: Dict[str, int] = {}
    for _box, art in layout.tiles:
        plates[art.key] = plates.get(art.key, 0) + 1
    for field in wordmark_fields:
        key = field.split(":", 1)[1]
        art = image.logos[key]
        assert art.wordmark, f"a verified logo {key} drew text"
        named = plates[key] - len(drawn.get(f"{look.TICKER_FIELD_PREFIX}{key}", []))   # the rest drew a ticker
        assert drawn[field] == _spec_words(art.name) * named
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
    script = {"authorship": "template", "look": cards.LOOK, "image_spec": _spotlight_spec(), "image_footer": FOOTER,
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
    """A clearly GREEN or RED pixel — never the look's gold (GOLD, GOLD_INK, the stacked bar's gold) or blue."""
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    return (((r - np.maximum(g, b)) > 40) & (g < 90)) | ((g - np.maximum(r, b)) > 40)


def _toned_mask(layout, shape) -> "np.ndarray":
    """Where the look may put green or red (news-v23 colour rules): the logo discs, the Money Map's stacked bar, a
    rect / dot / line whose colour IS green or red (a toned figure, a profit or loss bar, its stripe, its dot)."""
    allowed = np.zeros(shape, dtype=bool)

    def allow(b, pad=2):
        allowed[max(b[1] - pad, 0):b[3] + pad, max(b[0] - pad, 0):b[2] + pad] = True

    for box, _art in layout.tiles:
        allow(box, 0)
    for s in getattr(layout, "stacks", ()):
        allow(s.box)
    for r in layout.rects:
        if r.fill in (look.GREEN, look.RED):
            allow(r.box)
    for e in getattr(layout, "ellipses", ()):
        if e.fill in (look.GREEN, look.RED, *look.SEG_COLORS):
            allow(e.box)
    for ln in layout.lines:
        if ln.fill in (look.GREEN, look.RED):
            allow(ln.ink)
    return allowed


@pytest.mark.parametrize("name", sorted(SPECS))
def test_no_green_or_red_outside_the_logo_plates_and_nothing_in_the_margins(name, logo_files):
    """news-v23 colour rules: green and red appear only where the look puts them — the logos on their discs, the
    Money Map's stacked bar and its profit / loss lines (look.SEG_COLORS, GREEN, RED) and a figure whose SERIES is a
    gain or a loss (these specs carry no series: every figure is neutral INK) — and the side margins and the rows
    under the footer are the canvas, every line inside its region."""
    image = _image(SPECS[name](), _table(logo_files))
    img, layout = nl.draw_template(image, font_path=FONT, layout_engine="basic")
    px = np.asarray(img)
    tiles = np.zeros(px.shape[:2], dtype=bool)
    for box, _art in layout.tiles:
        tiles[box[1]:box[3], box[0]:box[2]] = True
    assert _not_neutral(px)[tiles].any(), "the red/green test logos were not drawn — the check is vacuous"
    bad = _not_neutral(px) & ~_toned_mask(layout, px.shape[:2])
    assert not bad.any(), f"{int(bad.sum())} green/red pixels outside the logo discs and the toned marks"
    toned = {ln.field for ln in layout.lines if ln.fill in (look.GREEN, look.RED)}
    assert toned == ({"flow[3].label", "flow[3].value", "callout"} & toned if name == "bars" else set()), toned
    if name == "bars":
        assert {"flow[3].value", "callout"} <= toned                     # the profit, GREEN
    base = np.asarray(look.canvas(look.POST_FRAME))
    x0, _y0, x1, _y1 = look.POST_FRAME.area
    for region in (np.s_[:, :x0], np.s_[:, x1:], np.s_[1350 - 20:]):
        assert (px[region] == base[region]).all()
    for ln in layout.lines:
        if ln.field in ("brand", "kicker"):
            assert cards._within(ln.ink, (x0, 30, x1, layout.panel[1])), (ln.field, ln.ink)
        elif ln.field == "footer":
            assert ln.ink[1] >= layout.panel[3] and cards._within(ln.ink, (60, 0, 1020, 1340)), ln.ink
        else:
            assert cards._within(ln.ink, layout.panel), (ln.field, ln.ink)
    for box, _art in layout.tiles:
        assert cards._within(box, layout.panel)


def test_the_neutral_check_catches_a_red_pixel():
    """Mutation check of the check itself: one red pixel is caught; the look's golds are not."""
    img = Image.new("RGB", (10, 10), look.CARD)
    img.putpixel((3, 3), (230, 30, 30))
    img.putpixel((4, 4), look.GOLD)
    img.putpixel((5, 5), look.GOLD_INK)
    img.putpixel((6, 6), look.SEG_COLORS[2])
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


# review 2026-11-20: the four news-v23 LIST placers (`_lay_f13_list`, `_lay_earnings`, `_lay_congress`, `_lay_stakes`)
# had no same-inputs-same-bytes pin (SPECS above never reaches them: its `rows` spec has a subtitle and two sections).
_LIST_SAMPLES = {
    "f13_list8": ("f13s_list8", "SPOT_SAT"), "berkshire_13f": ("berkshire", "TUE"),
    "earnings4": ("earnings4", "EARNINGS_RUN"), "congress3": ("congress", "CONGRESS_RUN"),
    "stakes4": ("nvidia_stakes_more", "STAKES_RUN"),
}


def _list_sample(name: str) -> Dict[str, Any]:
    import test_marketing_news_templates as NT

    factory, day = _LIST_SAMPLES[name]
    rec = NT.earnings(others=3) if factory == "earnings4" else getattr(NT, factory)()
    return _compose(rec, getattr(NT, day))


def _list_renders(out: Dict[str, Any], files: Dict[str, Path]) -> Tuple[bytes, bytes]:
    """(the picture's JPEG, the list cover's PNG) as the worker draws them, every logo from `files`."""
    entries = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None, "width": None,
                "height": None} for r in out["logo_refs"]]
    script = dict(out, look=cards.LOOK, logos=entries)
    table = cards.logo_table(script, {k: str(v) for k, v in files.items()})
    image = nl.template_image_for_script(script, table)
    picture = nl.render_template_image(image, font_path=FONT, layout_engine="basic").data
    spec = cards.cards_for_script(script, table, font_path=FONT, layout_engine="basic")[0]
    assert spec.image is not None                                                    # the cover IS the list
    return picture, cards.render_card(spec, font_path=FONT, layout_engine="basic")


@pytest.mark.parametrize("name", sorted(_LIST_SAMPLES))
def test_every_list_placer_draws_the_same_bytes_from_the_same_inputs_and_a_logo_changes_them(name, tmp_path):
    """Review 2026-11-20: each LIST placer (the 13F list, the earnings table, the Congress cards, the stakes list) on
    the picture and on its cover: rendered twice with the font and wordmark caches cleared, the same bytes; a changed
    logo, other bytes. (Across processes and hash seeds: the next test.)"""
    out = _list_sample(name)
    files = {r["key"]: _logo_png(tmp_path / f"{i}.png", (40 + 9 * i, 90, 160 - 7 * i, 255))
             for i, r in enumerate(out["logo_refs"])}
    a = _list_renders(out, files)
    cards._font.cache_clear()
    cards._fit_wordmark.cache_clear()
    b = _list_renders(out, files)
    assert a == b
    first_row = out["image_spec"]["sections"][0]["rows"][0]["logo"]           # drawn on the picture AND the cover
    other = dict(files, **{first_row: _logo_png(tmp_path / "other.png", (200, 30, 30, 255))})
    c = _list_renders(out, other)
    assert c[0] != a[0] and c[1] != a[1]


_LIST_DIGEST_SCRIPT = """
import hashlib, json, sys
sys.path.insert(0, "tests"); sys.path.insert(0, ".")
import test_marketing_news_layouts as TL
from pathlib import Path
tmp = Path(sys.argv[1])
digests = {}
for name in sorted(TL._LIST_SAMPLES):
    out = TL._list_sample(name)
    files = {r["key"]: TL._logo_png(tmp / f"{name}-{i}.png", (40 + 9 * i, 90, 160 - 7 * i, 255))
             for i, r in enumerate(out["logo_refs"])}
    pic, cover = TL._list_renders(out, files)
    digests[name] = [hashlib.sha256(pic).hexdigest(), hashlib.sha256(cover).hexdigest()]
print(json.dumps(digests, sort_keys=True))
"""


def test_the_list_placers_draw_the_same_bytes_under_any_hash_seed(tmp_path):
    """Review 2026-11-20: a set-order (string-hash) dependence cannot show inside one process — the seed is fixed per
    process — so two fresh interpreters under PYTHONHASHSEED 1 and 2 render every list sample (picture and cover) and
    must agree byte for byte."""
    import os
    import subprocess

    runs = []
    for seed in ("1", "2"):
        d = tmp_path / seed
        d.mkdir()
        env = dict(os.environ, PYTHONHASHSEED=seed)
        done = subprocess.run([sys.executable, "-c", _LIST_DIGEST_SCRIPT, str(d)], cwd=str(_BACKEND), env=env,
                              capture_output=True, text=True, timeout=300)
        assert done.returncode == 0, done.stderr[-2000:]
        runs.append(json.loads(done.stdout.strip().splitlines()[-1]))
    assert runs[0] == runs[1] and set(runs[0]) == set(_LIST_SAMPLES)


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
    image = nl.template_image_for_script({"authorship": "template", "look": cards.LOOK, "image_spec": _rows_spec(), "image_footer": FOOTER,
                                          "logos": entries}, table)
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert "REGN" in cards.blank_wordmarks(layout) and not layout.field_lines("wordmark:REGN")
    assert "Supercalifragilisticexpialidocious" in image.strings          # still declared (safe direction)
    assert any("blank disc" in r.getMessage() for r in caplog.records)
    # the plate is still drawn (white), with no ink on it
    img, _ = nl.draw_template(image, font_path=FONT, layout_engine="basic")
    (box,) = [b for b, art in layout.tiles if art.key == "REGN"]
    side = box[2] - box[0]
    k = int(side * (1 - 1 / math.sqrt(2)) / 2) + 4                         # the disc's inscribed square, inset
    inner = (box[0] + k, box[1] + k, box[2] - k, box[3] - k)
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
    # news-v23: the bars header takes a ticker chip (the plate's pill) — any OTHER header key is still refused
    ("bars", lambda s: s["header"].update(colour="gold"), "unknown key"),
    ("bars", lambda s: s["header"].update(chip=""), "drawable"),
    # news-v23: a rows row takes a ticker chip — never a phrase, never another key
    ("rows", lambda s: s["sections"][0]["rows"][0].update(chip="ABCDEFGHIJK"), "chip"),
    ("rows", lambda s: s["sections"][0]["rows"][0].update(chip=7), "chip"),
    ("rows", lambda s: s["sections"][0]["rows"][0].update(chip=" GME"), "drawable"),
    ("rows", lambda s: s["sections"][0]["rows"][0].update(colour="green"), "unknown key"),
    # `feature` (2026-11-15): its closed keys, its fact rows' count and keys, its header
    ("feature", lambda s: s.update(lines=["a"]), "unknown key"),
    ("feature", lambda s: s.pop("figure_label"), "missing"),
    ("feature", lambda s: s.update(figure_label=""), "drawable"),
    ("feature", lambda s: s.pop("header"), "missing"),
    ("feature", lambda s: s["header"].pop("logo"), "missing"),
    ("feature", lambda s: s["header"].update(logo="NOPE"), "names no logo"),
    ("feature", lambda s: s.update(facts=[]), "entries"),
    ("feature", lambda s: s["facts"].append({"label": "Fifth", "value": "x"}), "entries"),
    ("feature", lambda s: s.update(facts={"label": "a", "value": "b"}), "not a list"),
    ("feature", lambda s: s["facts"][0].pop("value"), "missing"),
    ("feature", lambda s: s["facts"][0].update(note="x"), "unknown key"),
    ("feature", lambda s: s["facts"][0].update(value=" padded"), "drawable"),
    ("feature", lambda s: s["facts"][0].update(label=7), "drawable"),
    ("feature", lambda s: s.update(layout="spotlight"), "unknown key"),
]


@pytest.mark.parametrize("name, mutate, needle", _BAD)
def test_the_worker_refuses_what_the_server_refuses(name, mutate, needle):
    spec = _mut(name, mutate)
    with pytest.raises(ValueError, match=needle):
        nl.walk(spec, _table())
    # …and so does the server's own validator (the two cannot drift silently)
    assert tos.validate_image_spec(spec, tos.logo_keys(_entries())) is not None


@pytest.mark.parametrize("layout", ["rows", "spotlight", "bars", "feature"])
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
    script = {"authorship": "template", "look": cards.LOOK, "image_spec": _rows_spec(), "image_footer": FOOTER, "logos": _entries()}
    script.update(over)
    with pytest.raises(ValueError, match=needle):
        nl.template_image_for_script(script, _table())


def test_render_post_image_dispatches_on_authorship_and_never_falls_back(logo_files):
    script = {"authorship": "template", "look": cards.LOOK, "image_spec": _rows_spec(), "image_footer": FOOTER, "logos": _entries(),
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
    """news-v23 (owner: "1 bar only, with 3 or more colors"): the segments are ONE stacked bar whose parts are
    proportional to their ratios (normalised), in look.SEG_COLORS order — unless a segment is an `outline` (a
    negative figure: eliminations) or there are more segments than colours, when the legend rows alone are drawn,
    never a wrong bar; the flow's `outline` bar is a LOSS, drawn RED leftwards from the axis."""
    spec = _bars_spec()
    layout = nl.layout_template(_image(spec, _table()), font_path=FONT, layout_engine="basic")
    assert layout.stacks == ()                                  # 7 segments and an outline: legend rows only
    assert not [e for e in layout.ellipses if e.box[2] - e.box[0] == 22]                      # no legend dots
    spec["segments"] = [s for s in spec["segments"] if s["style"] == "fill"][:3]
    layout = nl.layout_template(_image(spec, _table()), font_path=FONT, layout_engine="basic")
    (stack,) = layout.stacks
    assert stack.colours == look.SEG_COLORS[:3] and math.isclose(sum(stack.widths), stack.box[2] - stack.box[0])
    total = sum(s["ratio"] for s in spec["segments"])
    for w, s in zip(stack.widths, spec["segments"]):
        assert math.isclose(w / (stack.box[2] - stack.box[0]), s["ratio"] / total, abs_tol=0.02)
    assert [e.fill for e in layout.ellipses if e.box[2] - e.box[0] == 22] == list(look.SEG_COLORS[:3])
    # a loss: the flow's last bar `outline` → RED value, RED bar and RED callout
    loss = dict(spec, flow=[*spec["flow"][:3], dict(spec["flow"][3], style="outline", value="-$8.1B")])
    layout = nl.layout_template(_image(loss, _table()), font_path=FONT, layout_engine="basic")
    assert {ln.fill for ln in layout.lines if ln.field in ("flow[3].value", "callout")} >= {look.RED}
    assert look.GREEN not in {ln.fill for ln in layout.lines if ln.field.startswith(("flow", "callout"))}
    assert any(r.fill == look.RED for r in layout.rects)


def _money_map_surfaces(image: nl.TemplateImage):
    """(the picture's layout, the video data card's) of one Money Map image — the two places `_stackable` decides."""
    return (("picture", nl.layout_template(image, font_path=FONT, layout_engine="basic")),
            ("data card", nl.layout_data_card(image, font_path=FONT, layout_engine="basic")))


@pytest.mark.parametrize("case", ["eliminations", "negative_fill"])
def test_final_a_money_map_whose_parts_cannot_add_up_to_revenue_draws_no_bar_on_any_surface(case):
    """Spec §2.6 ("a negative segment cannot be stacked … never a wrong bar"): the server's Money Map ends a map of 2-5
    named lines with "Everything else" and an `outline` "Intersegment eliminations" row (a NEGATIVE figure) — six or
    fewer segments, inside look.SEG_COLORS, so the colour count alone would stack it and draw the eliminations as a
    POSITIVE coloured part: the parts adding up to more than revenue. Only `_stackable`'s fill / non-loss clause keeps
    that bar off the picture and the data card; the legend rows still draw every line. The final review (2026-11-20)
    found no test that failed without the clause (the only stack test had 7 segments: the count alone refused it).
    Mutation-checked by hand (2026-11-20): with `_stackable` cut to its count and ratio checks, every case went red."""
    spec = _bars_spec()
    fills = [s for s in spec["segments"] if s["style"] == "fill"]
    if case == "eliminations":
        segs = [*fills[:3], *[s for s in spec["segments"] if s["style"] == "outline"]]
    else:                                                       # a fill whose figure is negative: never a part either
        segs = [*fills[:2], {"label": "Other businesses", "value": "-$1.2B", "ratio": 0.004, "style": "fill"}]
    spec["segments"] = segs
    assert len(segs) <= len(look.SEG_COLORS)                    # the colour count would allow a bar
    assert tos.validate_image_spec(spec, tos.logo_keys(_entries()), footer=FOOTER) is None   # the server accepts it
    for where, layout in _money_map_surfaces(_image(spec, _table())):
        assert layout.stacks == (), where                       # never a wrong bar
        fields = {ln.field for ln in layout.lines}
        assert {f"segments[{i}].{k}" for i in range(len(segs)) for k in ("label", "value")} <= fields, where
    # anti-vacuity: the same fills without the part that cannot stack draw ONE bar on both surfaces
    ok = dict(spec, segments=[s for s in segs if s["style"] == "fill" and not s["value"].startswith("-")])
    for where, layout in _money_map_surfaces(_image(ok, _table())):
        (stack,) = layout.stacks
        assert len(stack.widths) == len(ok["segments"]), where


def test_final_the_real_eliminations_sample_draws_its_legend_and_no_bar():
    """The same on a REAL composed sample (comcast_money_map: two lines, "Everything else" and "Intersegment
    eliminations −$4.3B" — four segments): no bar on the picture or the data card, every line in the legend."""
    out = _sample_output("comcast_money_map")
    segs = out["image_spec"]["segments"]
    assert len(segs) == 4 and segs[-1]["style"] == "outline" and segs[-1]["value"].startswith("\u2212")
    for where, layout in _money_map_surfaces(_composed_image(out)):
        assert layout.stacks == (), where
        assert {f"segments[{i}].label" for i in range(4)} <= {ln.field for ln in layout.lines}, where


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
    canvas = Image.new("RGB", (300, 300), look.CARD)
    _draw_disc(canvas, (20, 20, 220, 220), cards.LogoArt("K", "Kay", str(path)))


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


def _draw_disc(canvas, box, art) -> None:
    """What a picture or card draws for a logo: its disc (look.draw_disc — white, or dark for a logo made for a
    dark background)."""
    look.draw_disc(canvas, look.Disc(box, art), look.POST_FRAME)


def test_the_logo_visibility_check_composites_onto_the_cards_own_plate():
    assert lg.PLATE_RGB == cards._rgb(cards.PLATE) == look.WHITE == (255, 255, 255)
    assert lg.DARK_DISC_RGB == look.NAVY_CHIP                                   # news-v23: the dark disc
    assert 0 < lg.MIN_VISIBLE_FRACTION <= 0.01 and 200 <= lg.VISIBLE_CHANNEL_BELOW <= 240
    assert 0 < lg.DARK_TRANSPARENT_SHARE_MIN < lg.TRANSPARENT_SHARE_MIN and 40 <= lg.DARK_VISIBLE_CHANNEL_FROM <= 90


@pytest.mark.parametrize("kind", ["transparent", "white_jpeg", "faint_alpha", "speck"])
def test_a_logo_invisible_on_the_white_plate_is_refused_and_drawn_as_its_wordmark(kind, tmp_path, caplog):
    """FMP may serve a transparent or a white image, a faint or a speck of a mark: every header check passes, and
    it would draw as an empty disc — white or dark — with no company name either, since a verified logo draws no
    wordmark. The decode refuses it instead (news-v23: a logo invisible on the white disc is tried on the DARK one —
    `disc_problem` — and these show on neither: a white JPEG has no transparency, so it would be a blank square)."""
    import hashlib

    data, ext = _logo_bytes(kind)
    problem = lg.decode_problem(data, ext)
    assert problem and "invisible logo on the white disc" in problem and "dark disc" in problem, problem
    # what the card would have drawn: the disc alone, not one visible pixel of a logo
    path = tmp_path / f"probe.{ext}"
    path.write_bytes(data)
    with Image.open(path) as src:
        assert lg.disc_tone(src.convert("RGBA")) == "dark"
    canvas = Image.new("RGB", (240, 240), look.CARD)
    _draw_disc(canvas, (20, 20, 220, 220), cards.LogoArt("K", "Kay", str(path)))
    inner = np.asarray(canvas.crop((80, 80, 160, 160))).astype(int)
    if kind != "white_jpeg":                                           # the dark disc, nothing that shows on it
        assert np.abs(inner - np.array(look.NAVY_CHIP)).max() <= 45
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


@pytest.mark.parametrize("kind", ["white_on_transparent", "near_white"])
def test_a_light_logo_made_for_a_dark_background_draws_whole_on_the_dark_disc(kind, tmp_path):
    """news-v23 (owner 2026-11-19: "never fall back to the wordmark just because the logo is light"): white or near
    white artwork on transparency — invisible on the white disc — is KEPT and drawn on the DARK disc, whole."""
    data, ext = _logo_bytes(kind)
    assert lg.decode_problem(data, ext) is None
    path = tmp_path / f"probe.{ext}"
    path.write_bytes(data)
    with Image.open(path) as src:
        rgba = src.convert("RGBA")
    assert lg.disc_tone(rgba) == "dark" and lg.disc_problem(rgba) is None
    canvas = Image.new("RGB", (240, 240), look.CARD)
    _draw_disc(canvas, (20, 20, 220, 220), cards.LogoArt("K", "Kay", str(path)))
    px = np.asarray(canvas)
    assert tuple(px[30 + 8, 120]) != look.WHITE                              # the disc is dark at its top …
    assert px[100:140, 100:140].min() >= 230                                 # … and the light mark shows on it


def _plated_logo(plate=(255, 255, 255), mark=(20, 60, 140), size=250, box=(95, 115, 155, 135)) -> "Image.Image":
    """A logo FMP served on an opaque plate: a `size`² square of `plate` with ONE small `mark` rectangle (Lennar's
    small wordmark in its white square, built in code)."""
    from PIL import ImageDraw

    im = Image.new("RGBA", (size, size), plate + (255,))
    ImageDraw.Draw(im).rectangle(box, fill=mark + (255,))
    return im


def test_a_logo_on_a_light_plate_is_sized_by_its_marks_on_a_disc_of_the_plates_colour(tmp_path):
    """news-v23 (mock_f13_lists `real_logo`): a logo served on an opaque LIGHT plate is sized by its MARKS, not by
    its margin — the disc takes the plate's colour, the mark's farthest pixel sits at PLATE_REACH (+ the long-wordmark
    bonus: this mark is 3:1) of the radius, centred, and nothing outside the disc is drawn. Before, the whole square
    was the logo and its mark came out about a third of that size. Same bytes twice."""
    im = _plated_logo(plate=(252, 252, 250))
    assert lg.opaque_plate(im) == (252, 252, 250) and lg.light_plate(im) == (252, 252, 250)
    marks = lg.plate_marks(im, lg.light_plate(im))
    assert marks.getbbox() == (95, 115, 156, 136)                        # the mark only, never the plate
    path = tmp_path / "plated.png"
    im.save(path)
    art = cards.LogoArt("LEN", "Lennar", str(path))

    def draw() -> "Image.Image":
        canvas = Image.new("RGB", (240, 240), look.NAVY_BOT)
        _draw_disc(canvas, (20, 20, 220, 220), art)
        return canvas

    canvas = draw()
    assert canvas.tobytes() == draw().tobytes()
    px = np.asarray(canvas).astype(int)
    assert tuple(px[30, 120]) == (252, 252, 250)                          # the disc is the plate's colour
    assert tuple(px[5, 5]) == look.NAVY_BOT                               # nothing outside the disc
    ys, xs = np.where(np.abs(px - np.array((20, 60, 140))).sum(axis=2) < 40)
    assert xs.size, "the mark is drawn"
    aspect = 61 / 21
    k = look.PLATE_REACH + look.PLATE_REACH_LONG_BONUS * min(1.0, max(0.0, (aspect - 2) / 2))
    far = max(math.hypot(x + 0.5 - 120, y + 0.5 - 120) for x, y in zip(xs, ys))
    assert abs(far - k * 100) <= 3                                         # its farthest pixel at k × r
    assert abs((xs.min() + xs.max()) / 2 - 119.5) <= 1.5 and abs((ys.min() + ys.max()) / 2 - 119.5) <= 1.5


def test_an_opaque_dark_tile_is_drawn_whole_on_the_white_disc_and_a_transparent_logo_is_not_plated(tmp_path):
    """An opaque DARK tile (Berkshire's navy square) is no light plate: the tile is the logo, centred on the white
    disc with its corners at TILE_REACH of the radius. A logo on transparency, or one whose corners differ, is not
    plated at all (the white / dark disc rules apply)."""
    tile = _plated_logo(plate=(40, 50, 140), mark=(250, 250, 250))
    assert lg.opaque_plate(tile) == (40, 50, 140) and lg.light_plate(tile) is None
    path = tmp_path / "tile.png"
    tile.save(path)
    canvas = Image.new("RGB", (240, 240), look.CARD)
    _draw_disc(canvas, (20, 20, 220, 220), cards.LogoArt("BRK", "Berkshire", str(path)))
    px = np.asarray(canvas).astype(int)
    assert tuple(px[22 + 4, 120]) != tuple(np.array((40, 50, 140)))       # the disc's top is white, not the tile
    half = look.TILE_REACH * 100 / math.sqrt(2)                           # the tile's half side
    assert tuple(px[120, int(120 + half - 3)]) == (40, 50, 140) and tuple(px[120, int(120 + half + 3)]) == look.WHITE
    clear = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
    clear.paste((20, 60, 140, 255), (95, 115, 155, 135))
    assert lg.opaque_plate(clear) is None
    odd = _plated_logo()
    odd.putpixel((0, 0), (200, 200, 200, 255))                            # one corner unlike the others
    assert lg.opaque_plate(odd) is None


def test_the_visibility_count_is_linear_and_exact():
    """`visible_pixels` counts exactly the pixels whose darkest channel, composited onto white, is below
    VISIBLE_CHANNEL_BELOW — no sampling, no Python pixel loop (a 2048² logo is 4 M pixels)."""
    im = Image.new("RGBA", (2048, 2048), (0, 0, 0, 0))
    im.paste((10, 10, 10, 255), (0, 0, 100, 37))                         # 3,700 dark
    im.paste((255, 255, lg.VISIBLE_CHANNEL_BELOW, 255), (0, 100, 50, 150))  # at the bar: not visible
    im.paste((255, 255, lg.VISIBLE_CHANNEL_BELOW - 1, 255), (0, 200, 10, 210))  # just under: 100 visible
    assert lg.visible_pixels(im) == 3_700 + 100


# ── LIVE-LOGO-1 / LIVE-LOGO-2 (2026-10-20 live review): a logo that shows only part of itself, or a baked ──
# checkerboard, on the white plate is a wordmark. Real FMP logos are not checked in (no binaries): these are
# their shapes, built in code — GameStop's white "G" + red "S" and Amazon's white "a" + orange smile (made
# for a dark background), Boston Scientific's editor checkerboard behind its name.


def _png_of(im: "Image.Image") -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _dark_theme_logo(shape: str) -> "Image.Image":
    """A 250×250 logo made for a dark background, on real transparency: white artwork + one accent."""
    from PIL import ImageDraw

    im = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if shape == "gme":          # a white "G" (left), a red "S" (right) — as tall as each other
        d.rectangle((0, 30, 120, 220), outline=(255, 255, 255, 255), width=28)
        for y in (30, 111, 192):
            d.rectangle((130, y, 250, y + 28), fill=(235, 0, 0, 255))
        d.rectangle((130, 30, 158, 139), fill=(235, 0, 0, 255))
        d.rectangle((222, 111, 250, 220), fill=(235, 0, 0, 255))
    elif shape == "amzn":       # a big white "a", a thin orange smile under it
        d.ellipse((60, 19, 190, 170), outline=(255, 255, 255, 255), width=34)
        d.arc((0, 120, 250, 231), start=20, end=160, fill=(255, 153, 0, 255), width=14)
    else:
        raise AssertionError(shape)
    return im


@pytest.mark.parametrize("shape, low, high", [("gme", 0.40, 0.55), ("amzn", 0.15, 0.40)])
def test_a_logo_made_for_a_dark_background_is_refused_and_drawn_as_its_wordmark(shape, low, high, tmp_path, caplog):
    """LIVE-LOGO-1 → news-v23: FMP's GameStop and Amazon logos are white artwork + one coloured accent on
    transparency. On the WHITE disc they would draw as a lone red "S" / a lone orange smile (the visible ink spans
    too little of the opaque box) — so they are drawn on the DARK disc, whole (form4_ceo_roundup.jpg's GameStop):
    verified, the logo file kept, no wordmark."""
    import hashlib

    im = _dark_theme_logo(shape)
    assert lg.visible_pixels(im) >= lg.MIN_VISIBLE_FRACTION * 250 * 250       # LOGO-1 alone keeps it
    share = lg.visible_box_share(im)
    assert share is not None and low < share < high < lg.MIN_VISIBLE_BOX_SHARE, share   # the real ones: 0.49 / 0.27
    assert lg.disc_tone(im) == "dark" and lg.disc_problem(im) is None
    data = _png_of(im)
    assert lg.decode_problem(data, "png") is None
    sha = hashlib.sha256(data).hexdigest()
    url = f"https://sb.example/storage/v1/object/public/marketing-media/logos/{sha[:32]}.png"
    entry = {"key": "GME", "name": "GameStop", "url": url, "sha256": sha, "bytes": len(data), "width": 250,
             "height": 250}
    dest = tmp_path / "out"
    dest.mkdir()
    out = lg.resolve_logos({"logos": [entry]}, bucket_origin="sb.example", download=lambda u, **k: data,
                           dest_dir=dest)
    assert out["GME"] is not None and out["GME"].read_bytes() == data
    art = cards.logo_table({"logos": [entry]}, out)["GME"]
    assert not art.wordmark
    canvas = Image.new("RGB", (240, 240), look.CARD)
    _draw_disc(canvas, (20, 20, 220, 220), art)
    px = np.asarray(canvas).astype(int)
    assert np.abs(px[34, 120] - np.array(look.NAVY_CHIP)).max() <= 4            # the dark disc
    white = (px > 230).all(axis=2).sum()
    assert white > 1500                                                          # the white artwork shows


def test_a_logo_on_its_own_opaque_ground_or_whole_on_the_plate_is_kept():
    """The rule needs REAL transparency: a small dark mark on its own opaque white tile (FMP's Eaton, Almirall,
    Cerebras: box shares 0.22-0.32, 0 % transparent) is the design, drawn as it is. A transparent logo whose
    visible ink spans its solid box (every coloured mark; Boston Scientific's text, 0.81) is kept too."""
    from PIL import ImageDraw

    tile = Image.new("RGB", (250, 250), (255, 255, 255))
    ImageDraw.Draw(tile).rectangle((95, 95, 155, 155), fill=(10, 60, 140))
    assert lg.visible_box_share(tile.convert("RGBA")) is None                 # exempt: no transparency
    buf = io.BytesIO()
    tile.save(buf, format="JPEG")
    assert lg.decode_problem(buf.getvalue(), "jpg") is None
    assert lg.decode_problem(_png_of(tile), "png") is None
    mark = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
    ImageDraw.Draw(mark).ellipse((20, 20, 230, 230), fill=(10, 60, 140, 255))
    assert lg.visible_box_share(mark) == 1.0 and lg.decode_problem(_png_of(mark), "png") is None
    # a white band behind a dark name that spans most of it (Boston Scientific's shape): kept
    named = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    named.paste((255, 255, 255, 255), (0, 50, 300, 150))
    named.paste((20, 48, 92, 255), (10, 60, 290, 140))
    assert lg.visible_box_share(named) == pytest.approx(280 * 80 / (300 * 100))
    assert lg.decode_problem(_png_of(named), "png") is None


@pytest.mark.parametrize("ink_w, side, transparent, tone", [
    (60, 200, True, "light"), (59, 200, True, "refused"),   # the box share's bound on white: 60 % kept; 59 % — the
                                                             # dark disc would show only its 41 % white: refused
    (40, 120, True, "dark"),                                 # 30.6 % transparent: whole on the DARK disc (60 % white)
    (40, 119, False, "light"), (40, 115, False, "light"),    # 29.4 % / 24.4 %: exempt on white, whatever the share
])
def test_the_dark_background_rule_s_bounds(ink_w, side, transparent, tone):
    """A 100×100 opaque white square with a dark band `ink_w` px wide on a `side`² canvas that is 75 %, 30.6 %,
    29.4 % or 24.4 % transparent: on the white disc its share is ink_w / 100 — not whole below 0.60, and only with
    real transparency, the bound pinned on both sides of TRANSPARENT_SHARE_MIN (review VAC-1: raised to 0.44 it
    exempted FMP's GameStop logo, 42.9 % transparent, the case the rule was written for). news-v23: such a logo is
    tried on the DARK disc, where its WHITE part shows (100 − ink_w) / 100 of the box — kept at 0.60, refused under."""
    im = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    im.paste((255, 255, 255, 255), (0, 0, 100, 100))
    im.paste((15, 15, 15, 255), (0, 0, ink_w, 100))
    clear = 1 - 100 * 100 / side ** 2
    assert (clear >= lg.TRANSPARENT_SHARE_MIN) == transparent
    share = lg.visible_box_share(im)
    assert (share is None) == (not transparent)
    if transparent:
        assert share == pytest.approx(ink_w / 100)
    problem = lg.decode_problem(_png_of(im), "png")
    assert bool(problem) == (tone == "refused"), problem
    if tone != "refused":
        assert lg.disc_tone(im) == tone


def _blue_disc() -> "Image.Image":
    from PIL import ImageDraw

    im = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((75, 75, 175, 175), fill=(20, 60, 200, 255))
    return im


def test_a_faint_stray_pixel_or_glow_does_not_widen_the_logo_s_box():
    """Review RR-B2F-3: the box the visible ink is measured against is the SOLID pixels' (alpha ≥ 128). Two
    alpha-20 pixels in opposite corners (a resampling speck), or a faint glow around a fully visible mark,
    stretched the old alpha ≥ 16 box to the whole canvas: the disc was refused as "made for a dark background"
    (17 % / 28 %) and drawn as its wordmark although every pixel of it shows."""
    from PIL import ImageDraw

    stray = _blue_disc()
    stray.putpixel((1, 1), (0, 0, 0, 20))
    stray.putpixel((248, 248), (0, 0, 0, 20))
    glow = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((30, 30, 220, 220), fill=(255, 255, 255, 60))
    glow.alpha_composite(_blue_disc())
    for im in (stray, glow):
        assert lg.visible_box_share(im) == pytest.approx(1.0)
        assert lg.decode_problem(_png_of(im), "png") is None


@pytest.mark.parametrize("ring_alpha, refused", [(127, False), (128, True)])
def test_white_artwork_counts_in_the_box_from_half_opacity(ring_alpha, refused):
    """…while white artwork that is half opaque or more IS the logo's shape: a white ring around the disc (made
    for a dark background, invisible on the white plate) is not whole on white from alpha 128 — news-v23: it takes
    the DARK disc, where the ring and the blue disc both show; under it, it is a glow (the white disc)."""
    from PIL import ImageDraw

    im = _blue_disc()
    ring = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((25, 25, 225, 225), outline=(255, 255, 255, ring_alpha), width=8)
    im.alpha_composite(ring)
    share = lg.visible_box_share(im)
    assert share == pytest.approx(100 * 100 / (200 * 200) if refused else 1.0, abs=0.01), share
    assert lg.decode_problem(_png_of(im), "png") is None
    assert lg.disc_tone(im) == ("dark" if refused else "light")


def _checker_logo(levels=(247, 244), square: int = 4, size=(300, 200), band=(50, 150), text: bool = True):
    """FMP's Boston Scientific shape: transparency above and below an opaque band holding a baked editor
    checkerboard (`levels`, `square`-px squares) behind a dark name — letters as 8 px bars, 40 px apart,
    spanning the band (BSX: 61 % transparent, the name over 80 % of the band's box)."""
    from PIL import ImageDraw

    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for y in range(band[0], band[1], square):
        for x in range(0, size[0], square):
            light = levels[((x // square) + ((y - band[0]) // square)) % 2]
            d.rectangle((x, y, x + square - 1, min(y + square, band[1]) - 1), fill=(light, light, light, 255))
    if text:
        for x in range(10, size[0] - 10, 40):
            d.rectangle((x, band[0] + 14, x + 7, band[1] - 15), fill=(19, 48, 92, 255))
        d.rectangle((10, band[0] + 14, size[0] - 11, band[0] + 21), fill=(19, 48, 92, 255))
    return im


@pytest.mark.parametrize("levels, square", [((247, 244), 4), ((254, 237), 6), ((250, 242), 8), ((248, 245), 2),
                                            ((255, 204), 8), ((255, 204), 16), ((255, 221), 10)])
def test_a_baked_checkerboard_is_refused_and_drawn_as_its_wordmark(levels, square):
    """LIVE-LOGO-2: an editor's "transparent" grid saved into the pixels (FMP's Boston Scientific: 254/237 in
    6 px squares, 247/244 in its middle) would be drawn as a faint chequer behind the name on every plate — and
    the editors' standard light grid (255/204, Photoshop's and GIMP's "light checks"; 255/221) as a plainly
    visible one: review RR-B2F-2 found the old light floor (230) never tested its darker squares."""
    im = _checker_logo(levels, square)
    assert lg.visible_box_share(im) >= lg.MIN_VISIBLE_BOX_SHARE                # not the dark-background rule
    found = lg.checker_share(im)
    assert found is not None, levels
    side, across, down = found
    assert side % square == 0 and (side // square) % 2 == 1                  # the square (or an odd multiple)
    assert across >= lg.CHECKER_SHARE_MIN and down >= lg.CHECKER_SHARE_MIN
    problem = lg.decode_problem(_png_of(im), "png")
    assert problem and "checkerboard" in problem, problem


#: The resampled board's blend phases: a square's last row / column is the blend toward its neighbour at a phase
#: that drifts, as when a grid is scaled by a non-integer factor (the real logo: 250/241, 242/250, 247/245 rows).
_RESAMPLED_ROW_PHASES = (0.24, 0.29, 0.44, 0.59, 0.41, 0.35, 0.47, 0.53)
_RESAMPLED_COL_PHASES = (0.12, 0.06, 0.12, 0.29, 0.12, 0.06, 0.47, 0.12, 0.06, 0.12, 0.35, 0.12)


def _resampled_checker_logo() -> "Image.Image":
    """FMP's Boston Scientific logo as it really is, not a perfect board: 6 px squares of 254/237 whose last row
    and column are RESAMPLING blends at drifting phases (so ~13 % of the light pixels do not alternate), behind
    a dark name whose strokes leave about half of the light pixels testable — measured 0.875 across / 0.854 down,
    cover 0.51, light share 0.72 (the real logo: 0.867 / 0.856, 0.50, 0.71)."""
    size, band, sq, levels = (250, 250), (76, 174), 6, (254, 237)
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    px = im.load()

    def level(i: int, j: int, rx: int) -> float:
        v = float(levels[(i + j) % 2])
        if rx == sq - 1:
            v += (levels[(i + j + 1) % 2] - v) * _RESAMPLED_COL_PHASES[j % len(_RESAMPLED_COL_PHASES)]
        return v

    for y in range(*band):
        i, ry = divmod(y - band[0], sq)
        for x in range(size[0]):
            j, rx = divmod(x, sq)
            v = level(i, j, rx)
            if ry == sq - 1:
                v += (level(i + 1, j, rx) - v) * _RESAMPLED_ROW_PHASES[i % len(_RESAMPLED_ROW_PHASES)]
            px[x, y] = (round(v),) * 3 + (255,)
    _draw_band_name(im)
    return im


def _draw_band_name(im: "Image.Image") -> None:
    """The dark name across the 250×250 band (76..174) of `_resampled_checker_logo`: strokes on two lines."""
    from PIL import ImageDraw

    d = ImageDraw.Draw(im)
    for x in (8, 38, 70, 100, 130, 160, 196, 226):
        d.rectangle((x, 86, x + 8, 122), fill=(19, 48, 92, 255))
    for x in (4, 34, 64, 94, 124, 154, 184, 214):
        d.rectangle((x, 128, x + 8, 166), fill=(19, 48, 92, 255))
    for box in ((8, 86, 60, 93), (120, 114, 180, 121), (30, 158, 100, 166), (150, 128, 230, 135)):
        d.rectangle(box, fill=(19, 48, 92, 255))


def test_a_resampled_checkerboard_like_the_real_one_is_refused_at_its_margin(monkeypatch):
    """Review VAC-2: every board above is perfect (it alternates 100 %), so CHECKER_SHARE_MIN could be raised to
    0.90 or CHECKER_COVER_MIN to 0.60 with every test green — and the real Boston Scientific logo (0.87 / 0.86 of
    its testable pixels alternating, cover 0.50) would be drawn with its chequer again. This board measures what
    the real one does, so the rule is pinned at the real logo's margin, not at 1.0."""
    im = _resampled_checker_logo()
    assert lg.visible_box_share(im) >= lg.MIN_VISIBLE_BOX_SHARE                # not the dark-background rule
    found = lg.checker_share(im)
    assert found is not None
    side, across, down = found
    assert side == 6 and 0.80 < across < 0.90 and 0.80 < down < 0.90, found
    problem = lg.decode_problem(_png_of(im), "png")
    assert problem and "checkerboard" in problem, problem
    monkeypatch.setattr(lg, "CHECKER_COVER_MIN", 0.55)                         # …and its cover is the real ~0.5
    assert lg.checker_share(im) is None


def test_a_clean_band_behind_a_thin_name_is_kept():
    """The same name on a PLAIN white band (Boston Scientific's shape without the chequer) draws as it is: its ink
    spans most of the band's box, though it inks under a third of the band's pixels — the dark-background rule
    measures where the ink reaches, never how much of the solid area it covers (review RR-B2F-3 proposed a
    coverage bar of ~0.4: it would refuse this logo, and still pass a white wordmark inside a thin ring)."""
    im = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
    im.paste((255, 255, 255, 255), (0, 76, 250, 174))
    _draw_band_name(im)
    assert lg.visible_box_share(im) >= lg.MIN_VISIBLE_BOX_SHARE
    assert lg.visible_pixels(im) < 0.35 * 250 * 98
    assert lg.checker_share(im) is None and lg.decode_problem(_png_of(im), "png") is None


def _stripes(axis: str) -> "Image.Image":
    """Two light greys in 6 px bands — alternating one way only (a design, not an editor's grid)."""
    im = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    for k in range(0, 300 if axis == "x" else 80, 6):
        light = 247 if (k // 6) % 2 else 240
        box = (k, 60, k + 6, 140) if axis == "x" else (0, 60 + k, 300, 66 + k)
        im.paste((light, light, light, 255), box)
    for x in range(12, 288, 22):
        im.paste((19, 48, 92, 255), (x, 72, x + 7, 128))
    return im


def _two_tone() -> "Image.Image":
    """A light-grey tile with a white inner panel and a dark name: two light levels, no alternation."""
    im = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    im.paste((240, 240, 240, 255), (0, 40, 300, 160))
    im.paste((255, 255, 255, 255), (20, 60, 280, 140))
    im.paste((19, 48, 92, 255), (40, 80, 260, 120))
    return im


def _dither() -> "Image.Image":
    """A near-white ground dithered 1 px by 1 px between 255 and 248 (an old GIF export)."""
    im = _checker_logo((255, 248), square=1, text=False)
    im.paste((19, 48, 92, 255), (20, 80, 280, 120))
    return im


def _black_white_flag() -> "Image.Image":
    """A chequered flag drawn as the logo itself: black and white squares — only one light level."""
    return _checker_logo((255, 0), square=10, text=False)


def _checker_detail_among_light_dots() -> "Image.Image":
    """A dotted design (3×3 white dots on a 6 px grid, a quarter of an opaque dark tile) with a small checker
    patch: the patch alternates perfectly, but it is under CHECKER_COVER_MIN of the light pixels (the dots
    cannot be tested at its side) — a detail of the design, never "most of the logo"."""
    im = Image.new("RGBA", (300, 300), (19, 48, 92, 255))
    for y in range(0, 300, 6):
        for x in range(0, 300, 6):
            im.paste((255, 255, 255, 255), (x, y, x + 3, y + 3))
    patch = _checker_logo((247, 244), 4, size=(80, 80), band=(0, 80), text=False)
    im.paste(patch, (110, 110))
    return im


def _red_mark_with_a_checker_strip() -> "Image.Image":
    """A big red mark with a thin strip of light checker under it: the light pixels are under a quarter of the
    opaque ones — a detail, never "most of the logo"."""
    im = _checker_logo((247, 244), 4, size=(300, 300), band=(250, 262), text=False)
    im.paste((220, 20, 20, 255), (0, 0, 300, 240))
    return im


@pytest.mark.parametrize("build", [lambda: _stripes("x"), lambda: _stripes("y"), _two_tone, _dither,
                                   _black_white_flag, lambda: _checker_logo((247, 246), 4),
                                   _red_mark_with_a_checker_strip, _checker_detail_among_light_dots],
                         ids=["stripes_across", "stripes_down", "two_tone_tile", "dither_1px", "black_white_flag",
                              "levels_1_apart", "a_thin_strip_of_checker", "a_checker_detail_among_dots"])
def test_a_light_logo_that_is_not_a_baked_checkerboard_is_kept(build):
    """Conservative: two light greys alone are not a checkerboard — they must alternate in squares, both
    ways, over most of the light pixels, and be at least CHECKER_STEP_MIN apart. Stripes, a two-tone tile, a
    1 px dither, a black-and-white flag (one light level), greys 1 apart, a checker strip too small to be
    most of the logo's light area and a checker patch among light dots are all drawn as they are."""
    im = build()
    assert lg.checker_share(im) is None
    problem = lg.decode_problem(_png_of(im), "png")
    assert problem is None or "checkerboard" not in problem, problem


def test_a_near_white_logo_with_a_light_ground_is_kept():
    """A flat near-white ground (one level), and a JPEG of one (its noise is many levels, none alternating):
    neither qualifies for the checkerboard test, and both draw."""
    flat = Image.new("RGB", (250, 250), (247, 247, 247))
    flat.paste((200, 30, 30), (60, 60, 190, 190))
    assert lg.checker_share(flat.convert("RGBA")) is None
    buf = io.BytesIO()
    flat.save(buf, format="JPEG", quality=70)
    assert lg.decode_problem(buf.getvalue(), "jpg") is None
    assert lg.decode_problem(_png_of(flat), "png") is None


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
    image = nl.template_image_for_script({"authorship": "template", "look": cards.LOOK, "image_spec": out["image_spec"],
                                          "image_footer": out["image_footer"], "logos": _entries()}, table)
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert layout.step <= cards.RAMP_STEPS
    if "costs" in out["image_spec"]:
        # owner decision 2026-11-20: a LOSS year draws its costs card's two-line callout ("… costs were $100.00," / "so
        # the net loss was under $0.01": the subtraction still exact at cents) in place of the one-line callout
        assert sign < 0 and not layout.field_lines("callout")
        lead, figure = out["image_spec"]["costs"]["callout"]
        assert layout.field_lines("costs.callout[0]") == [lead] and layout.field_lines("costs.callout[1]") == [figure]
        assert lead.endswith("costs were $100.00,") and figure == f"so the net loss was {T.SUB_CENT}"
        callout = f"{lead} {figure}"
    else:
        callout = " ".join(ln.text for ln in layout.lines if ln.field == "callout")
        assert callout == out["image_spec"]["callout"]
    assert T.SUB_CENT in callout
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
#: news-v23 (the approved look): the picture's HEADLINE column — the two-tone title, upper-cased, 8 px inside the
#: cards' edges (look.POST_FRAME) — and its style.
_F13_COLUMN = (look.POST_FRAME.area[2] - 8) - (look.POST_FRAME.area[0] + 8)
_TITLE = nl.HEADLINE_STYLE
#: Every character `company_news_rules` lets into a filer or company name.
_F13_NAME_ALPHABET = string.ascii_letters + string.digits + " &.,'+-"


def _floor_font(style, engine: str):
    return cards._font(FONT, style.size(cards.RAMP_STEPS), engine)


def _lines(text: str, font, width: float, *, upper: bool = False) -> int:
    return len(cards._wrap((text.upper() if upper else text).split(), font, width, 99, "probe"))


def _headline_lines(text: str, font, width: float) -> int:
    """The lines news-v23's two-tone headline draws (look.headline): upper-cased; ONE line when the whole of it fits;
    else its white part's lines, then its gold part's (each part starts a line; the balanced wrap keeps the greedy
    wrap's count)."""
    if font.getlength(text.upper()) <= width:
        return 1
    white, gold = look.two_tone(text)
    return sum(_lines(p, font, width, upper=True) for p in (white, gold) if p)


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
    lists the names, and a line over LINE_WORDS would refuse the record before any image). news-v23: the row card's
    name column at the floor (its disc, the right-aligned figure)."""
    font = _floor_font(nl.ROW_NAME_STYLE, engine)
    x0, _y0, x1, _y1 = look.POST_FRAME.area
    r = nl.ROW_DISC.size(cards.RAMP_STEPS)
    column = ((x1 - 36) - (x0 + 22 + 2 * r + 28) - nl.CELL_GAP
              - _floor_font(nl.ROW_FIGURE_STYLE, engine).getlength("$999.9M"))
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
    script = dict(out, look=cards.LOOK, logos=entries)
    table = cards.logo_table(script, {})                       # every logo a wordmark: the widest case
    return nl.template_image_for_script(script, table), table


def _floor_room(image, engine: str) -> int:
    """The room left in the content area at the FLOOR step (px): the area's height minus what the content takes —
    every content line and card (not the brand row, the kicker, the footer) — wherever the layout centred it."""
    layout = nl._place(image, cards.RAMP_STEPS, FONT, engine)
    if layout.kind.endswith(":spotlight"):
        # news-v23: the spotlight card FILLS the room to the footer (mock_form4.spotlight) and its figure takes what
        # is left — so the room is the figure band's slack: the band between the two hairlines (or the card's foot)
        # less the figure's ink at the floor size and its two margins
        divs = sorted(r.box for r in layout.rects if r.fill == look.DIVIDER)
        card = max((r.box for r in layout.rects if r.fill == look.CARD), key=lambda b: b[3] - b[1])
        band_top = divs[0][3]
        band_bot = divs[1][1] if len(divs) > 1 else card[3] - nl._gap(look.Page(layout_frame(layout),
                                                                                cards.RAMP_STEPS, FONT, engine), 44, 22)
        fig = next(ln for ln in layout.lines if ln.field == "figure")
        return (band_bot - band_top) - (fig.ink[3] - fig.ink[1]) - 2 * 12
    boxes = [ln.ink for ln in layout.lines if ln.field not in ("brand", "kicker", "footer")]
    boxes += [r.box for r in layout.rects if r.box[1] >= layout.panel[1] - 8]
    top, bottom = min(b[1] for b in boxes), max(b[3] for b in boxes)
    return (top - layout.panel[1]) + (layout.panel[3] - bottom)


def layout_frame(layout) -> look.Frame:
    return nl.POST_IMAGE_FRAME if layout.kind.startswith("template:") else nl.DATA_CARD_FRAME


#: The headroom every worst case must keep at the floor: one more line of the title.
_F13_HEADROOM_PX = int(round(_TITLE.size(cards.RAMP_STEPS) * _TITLE.leading))
#: The cover's headline column when it runs full width under the plate (card_layouts._opening's fallback).
_COVER_COLUMN_FULL = (look.VIDEO_FRAME.area[2] - 8) - (look.VIDEO_FRAME.area[0] + 8)


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_W_is_the_widest_glyph_a_13f_name_can_draw(engine):
    """The probes below fill names with "W": no other character a name may hold is wider, at the
    floor size of the image title, the row cells and the cover headline."""
    for style in (_TITLE, nl.ROW_NAME_STYLE, cards.OPENING_HEADLINE_STYLE):
        font = _floor_font(style, engine)
        widest = max(_F13_NAME_ALPHABET, key=font.getlength)
        assert font.getlength(widest) == font.getlength("W"), (style, widest)
        assert font.getlength("W" * 20) >= 20 * font.getlength("W") - 1      # no kerning shrinks a run


@pytest.mark.parametrize("engine", ["basic"])
def test_the_tallest_13f_title_a_60_character_filer_can_make_is_five_lines(engine):
    """The worst packing of a 60-character filer (company_news_rules.FILER_NAME_CHARS = 60, words ≤
    F13_MAX_WORD_CHARS) in the headline's WHITE part ("<filer>'s"; the gold part "latest 13F" is one more line) —
    the DP over every word-length sequence, upper-cased at the floor — and the "of" form, whose two-tone split is
    the half of its words (measured by the worker's own rule, `_headline_lines`). The image fit below is proved
    with these titles (a superset: such a filer's many short words do not even fit a hook, so news_templates
    refuses it before any image exists)."""
    font = _floor_font(_TITLE, engine)
    assert R.FILER_NAME_CHARS[1] == 60
    lines, filer = _worst_filer(60, before="", after="'S", own_glue=False, font=font, width=_F13_COLUMN)
    title = _f13_title(filer)
    assert _headline_lines(title, font, _F13_COLUMN) == lines + 1 == _F13_TALLEST_POSS_LINES, (filer, lines)
    of_lines, of_filer = _worst_filer(60, before="THE LATEST 13F OF ", after="", own_glue=True, font=font,
                                      width=_F13_COLUMN)
    assert _headline_lines(_f13_title(of_filer), font, _F13_COLUMN) <= _F13_TALLEST_OF_LINES
    _worst_names(engine)                      # the probe names (laid out with these titles in the fit test below)


#: Measured (basic engine, news-v23): the tallest 60-character-filer titles, in headline lines at the floor.
_F13_TALLEST_POSS_LINES = 5
_F13_TALLEST_OF_LINES = 5


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

_2B_STYLES = (nl.PAIR_NAME_STYLE, nl.GRID_NAME_STYLE, nl.GRID_LINE_STYLE, nl.SPOT_NAME_STYLE, nl.PAIR_LINE_STYLE,
              nl.HEADLINE_STYLE)
_X0, _Y0, _X1, _Y1 = look.POST_FRAME.area


def _floor_gap(px: int) -> int:
    return int(round(px * 0.6))                                  # news_layouts._gap at the floor step


#: news-v23 — the columns at the floor, by news_layouts' own arithmetic: the pair's investee name (beside the widest
#: stake figure, "$999.9B"), the pair's investor headline (beside its plate), the grid tile's text, the spotlight's
#: company name, the tile strip under a pair (its lines).
_PAIR_FIG = cards._font(FONT, nl.PAIR_FIGURE_STYLE.floor, "basic").getlength("$999.9B")
_PAIR_TX = _X0 + 22 + 2 * nl.ROW_DISC.floor + 28
_PAIR_HALF = int((_X1 - 30) - _PAIR_TX - _PAIR_FIG - nl.CELL_GAP)
_PAIR_HEAD = int((_X1 - 8 - 2 * (nl.PLATE_DISC.floor + look.GOLD_RING_GAP)) - (_X0 + 8) - 28)
_GRID_COL = (_X1 - _X0 - (nl.GRID_COLUMNS - 1) * nl.GRID_COL_GAP) // nl.GRID_COLUMNS
_GRID_TEXT = _GRID_COL - (_floor_gap(16) + 2 * nl.GRID_DISC.floor + _floor_gap(14)) - _floor_gap(8)
_SPOT_NAME = (_X1 - _floor_gap(44)) - (_X0 + _floor_gap(44) + 2 * nl.SPOT_DISC.floor + 34)
_PAIR_LINE = (_X1 - 12) - (_PAIR_TX - 16) - 32


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
    # news-v23: every server word cap still fits its worker column at the floor — the look's columns are as wide or
    # wider (its floors are 22 px or more), so the caps are SAFE bounds now, no longer each one's exact maximum:
    # the grid's line cap is the tightest — 17 "W" in 416 px at 22 px under RAQM, the production worker's engine
    # (HarfBuzz kerning widens "WW": 18 draw 419.1 px; BASIC, this Mac's engine, fits 18) — so it is exact under RAQM
    assert _w_cap(nl.PAIR_NAME_STYLE, _PAIR_HALF, engine) >= T.PAIR_NAME_WORD_MAX_CHARS
    assert _w_cap(nl.HEADLINE_STYLE, _PAIR_HEAD, engine) >= T.PAIR_NAME_WORD_MAX_CHARS   # the investor's headline
    assert _w_cap(nl.GRID_NAME_STYLE, _GRID_TEXT, engine) >= T.GRID_NAME_WORD_MAX_CHARS
    assert _w_cap(nl.GRID_LINE_STYLE, _GRID_TEXT, engine) == T.GRID_LINE_WORD_MAX_CHARS + (engine == "basic")
    assert _w_cap(nl.SPOT_NAME_STYLE, _SPOT_NAME, engine) >= T.NAME_WORD_MAX_CHARS        # header names
    assert _w_cap(nl.HEADLINE_STYLE, _F13_COLUMN, engine) >= T.NAME_WORD_MAX_CHARS
    assert _w_cap(nl.PAIR_LINE_STYLE, _PAIR_LINE, engine) >= T.NAME_WORD_MAX_CHARS       # a stake's source line


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
    expected = {path: _spec_words(value) for kind, path, value in items if kind == "text"}
    for path, words in expected.items():
        assert drawn.get(path) == words, (path, drawn.get(path), words)
    wordmark_fields = {f for f in drawn if f.startswith("wordmark:")}
    ticker_fields = _assert_tickers_stand_in(image, layout, drawn)
    assert set(drawn) == (set(expected) | wordmark_fields | ticker_fields | _LOOK_FIELDS
                          | {f for f in drawn if "#" in f})
    for field in wordmark_fields:
        key = field.split(":", 1)[1]
        arts = [a for _b, a in layout.tiles if a.key == key]
        assert arts and all(a.wordmark for a in arts)
        named = len(arts) - len(drawn.get(f"{look.TICKER_FIELD_PREFIX}{key}", []))   # each other plate drew a ticker
        assert drawn[field] == _spec_words(arts[0].name) * named and arts[0].name in image.strings


def _lay_out_everything(out: Dict[str, Any], engine: str) -> int:
    """Lays out the composed post image and EVERY card of its video with every logo a wordmark (the
    widest case); returns the image's room at the floor (px)."""
    image, table = _f13_image(out)
    layout = nl.layout_template(image, font_path=FONT, layout_engine=engine)
    _assert_2b_draws_its_strings(image, layout)
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None,
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


def _lay_out_list_post(out: Dict[str, Any], engine: str) -> int:
    """news-v23: a several-company LIST post (the Congress Count's cards, the earnings table) — its cards FLEX to the
    room (mock_congress_multi / mock_earnings_multi), so the floor-room measure of a content-sized picture reads ~0.
    The bar instead: the picture and its cover (the list on the 9:16 frame) fit BEFORE the floor step — a step of room
    to spare — every string drawn whole, and every video card lays out. Returns the picture's step."""
    image, table = _f13_image(out)
    layout = nl.layout_template(image, font_path=FONT, layout_engine=engine)
    _assert_2b_draws_its_strings(image, layout)
    assert layout.step < cards.RAMP_STEPS, layout.step
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    specs = cards.cards_for_script(script, table)
    assert [s.kind for s in specs] == ["opening", "text", "text", "text", "text", "disclaimer"]
    assert specs[0].image is not None                                             # the cover is the list
    for spec in specs:
        card = cards.layout_card(spec, font_path=FONT, layout_engine=engine)
        assert card.step < cards.RAMP_STEPS if spec.kind == "opening" else card.step <= cards.RAMP_STEPS
    return layout.step


#: news-v23 — the worst companies a several-company post may hold: distinct symbols of distinct issuers, the server's
#: name bounds (a 20-character word, 32 characters) as two "W" words.
_WORST_NAMES = [("MMMMM-A", "W" * 20 + " " + "W" * 11), ("NNNNN-A", "W" * 20 + " " + "M" * 11),
                ("PPPPP-A", "M" * 20 + " " + "W" * 11), ("QQQQQ-A", "W" * 19 + " " + "W" * 12)]


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("n", [2, 3])
def test_the_congress_count_image_and_video_fit(engine, n):
    """news-v23 (owner 2026-11-19: 2-3 companies a post, congress_multi3.jpg): the widest list — n companies of the
    server's worst names (two words wrapped on the card), a three-digit count, the longest month — fits the picture
    and every video card (the cover is the list), with a title line of room to spare at the floor."""
    counts = tuple(R.CongressCount(series="congress_count", company=_co(sym, name), month="2026-09", members=535,
                                   fetched_on=date(2026, 10, 13)) for sym, name in _WORST_NAMES[:n])
    rec = R.CongressMonth(series="congress_count", month="2026-09", fetched_on=date(2026, 10, 13), counts=counts)
    out = _compose(rec, date(2026, 10, 13))
    assert out["opening_card"]["kicker"] == "CONGRESS · DISCLOSED IN SEPTEMBER"    # the longest month
    assert [r["cells"][2] for r in out["opening_card"]["list"]["rows"]] == ["535"] * n
    _lay_out_list_post(out, engine)


@pytest.mark.parametrize("factory, run_date, rests", [("f13s_list2", "SPOT_SAT", False), ("pershing", "TUE", True),
                                                       ("f13s_list5", "SPOT_SAT", True)])
def test_a_two_row_13f_picture_shares_its_slack_and_three_or_more_rest_on_the_footer(factory, run_date, rests):
    """Review 2026-11-20 (confirmed): the 13F picture put ALL its slack above the column label (mock_f13_lists' PSH_AIR =
    1.0, the list resting on the footer) — right for Pershing's three rows, but a two-holding list (the owner's "2 to 6"
    allows it; no approved mock draws it) left ~470 px of empty navy under the headline. Two rows now take the mock
    script's default share (`F13_TWO_ROW_AIR` = 0.4 above the label, the rest under the list); three or more still rest
    on the footer (their cards end where Berkshire's do). Mutation-checked by hand: `F13_TWO_ROW_AIR` = 1.0 turns the
    two-row case red."""
    import test_marketing_news_templates as NT

    out = _compose(getattr(NT, factory)(), getattr(NT, run_date))
    image, _table = _f13_image(out)
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    title_bottom = max(ln.ink[3] for ln in layout.lines if ln.field.startswith("title"))
    label_top = next(ln.ink[1] for ln in layout.lines if ln.field == "sections[0].heading")
    list_bottom = max(r.box[3] for r in layout.rects if r.fill == look.CARD and r.radius == look.CARD_RADIUS)
    above, below = label_top - title_bottom, layout.footer_top - list_bottom
    if rests:
        assert below < 60, (above, below)                                  # the list rests on the footer
    else:
        assert nl.F13_TWO_ROW_AIR == 0.4
        assert above < 260 and below > 200 and 0.6 < above / below < 1.0, (above, below)


def _cg_text_air(layout) -> List[Tuple[int, int]]:
    """(air above, air below) the text block of every Congress card of a laid-out list: from the card's top to its
    chip (or name) ink, and from its last line's ink or its GREEN pill — whichever is lower — to the card's bottom."""
    out = []
    for card in (r for r in layout.rects if r.fill == look.CARD and r.radius == look.CARD_RADIUS):
        x0, y0, x1, y1 = card.box
        inside = lambda b: x0 <= b[0] and b[2] <= x1 and y0 <= b[1] and b[3] <= y1          # noqa: E731
        texts = [ln.ink for ln in layout.lines if inside(ln.ink)
                 and ln.field.startswith("sections[0].rows[") and ln.field.endswith((".chip", ".cells[0]",
                                                                                   ".cells[1]"))]
        pills = [r.box for r in layout.rects if r.fill == look.GREEN and inside(r.box)]
        chips = [r.box for r in layout.rects if inside(r.box) and r.radius == 9 and r.fill != look.GREEN]
        top = min([b[1] for b in texts + chips])
        bottom = max([b[3] for b in texts + pills])
        out.append((top - y0, y1 - bottom))
    return out


@pytest.mark.parametrize("worst", [False, True], ids=["sample", "worst_names"])
def test_every_congress_card_keeps_the_mocks_air_around_its_text(worst):
    """Review 2026-11-20 (confirmed): a 3-company Congress COVER (the list on the 9:16 frame) stepped until its text block
    met a 10 px guard — the PLTR chip 10 px under the card's top, the pill 9 px over its bottom — while the mock's own
    check (mock_congress_multi.company_card) asserts 14 px both ways. Every card of the picture and of the cover now
    keeps `nl.CG_TEXT_AIR` (14) above its chip and below its last line / pill; the hairline gap gives way first.
    Mutation-checked by hand: the guard back at 10 (and no gap give-way) turns the cover's row red."""
    import test_marketing_news_templates as NT

    assert nl.CG_TEXT_AIR == 14
    if worst:
        counts = tuple(R.CongressCount(series="congress_count", company=_co(sym, name), month="2026-09", members=535,
                                       fetched_on=date(2026, 10, 13)) for sym, name in _WORST_NAMES[:3])
        out = _compose(R.CongressMonth(series="congress_count", month="2026-09", fetched_on=date(2026, 10, 13),
                                       counts=counts), date(2026, 10, 13))
    else:
        out = _compose(NT.congress(others=2), NT.CONGRESS_RUN)
    image, table = _f13_image(out)
    picture = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    cover = cards.layout_card(cards.cards_for_script(script, table)[0], font_path=FONT, layout_engine="basic")
    for what, layout in (("picture", picture), ("cover", cover)):
        air = _cg_text_air(layout)
        assert len(air) == 3, (what, air)
        assert all(a >= nl.CG_TEXT_AIR and b >= nl.CG_TEXT_AIR for a, b in air), (what, air)


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("n", [2, 3, 4])
def test_the_earnings_image_and_video_fit(engine, n):
    """news-v23 (owner 2026-11-19: 2-4 companies a post, earnings_multi*.jpg): the widest table — n companies of the
    server's worst names (the compact column cannot hold a 20-character word: the STACKED cards), the widest figures
    the gates let through (a −$12,345.67 EPS; the widest surprise, "−1,000.0%", at the ten-times gap bound; a revenue
    pair written at three places, "$999.940B") — fits the picture and every video card (the cover is the table)."""
    eps = [(-12_345.67, -12_000.12), (-9_000.0, 1_000.0)]
    reports = tuple(R.EarningsReport(series="earnings", company=_co(sym, name), report_date=date(2026, 11, 5),
                                     period_end=date(2026, 9, 30), eps_actual=eps[i % 2][0],
                                     eps_estimate=eps[i % 2][1], revenue_actual=999_940_400_000.0,
                                     revenue_estimate=999_939_600_000.0)
                    for i, (sym, name) in enumerate(_WORST_NAMES[:n]))
    rec = R.EarningsDay(series="earnings", reports=reports)
    out = _compose(rec, date(2026, 11, 12))
    rows = out["image_spec"]["sections"][0]["rows"]
    assert rows[0]["table"][0]["cells"] == ["EPS", "\u2212$12,000.12", "\u2212$12,345.67", "\u22122.9%"]
    assert rows[1]["table"][0]["cells"] == ["EPS", "$1,000.00", "\u2212$9,000.00", "\u22121,000.0%"]
    assert rows[0]["table"][1]["cells"] == ["Revenue", "$999.940B", "$999.940B", "0.0%"]
    _lay_out_list_post(out, engine)


@pytest.mark.parametrize("n", [3, 4])
def test_a_short_name_earnings_table_draws_compact_cards_and_a_long_one_stacks(n):
    """The compact card (the name beside the table, earnings_multi4.jpg) whenever the names fit its column; a name that
    cannot (a 20-character word) stacks EVERY card (the header row over the table) — never a clipped name."""
    from datetime import date as _d
    names = [("COST", "Costco"), ("NKE", "Nike"), ("SBUX", "Starbucks"), ("LOW", "Lowe's")][:n]
    short = R.EarningsDay(series="earnings", reports=tuple(
        R.EarningsReport(series="earnings", company=_co(s, nm), report_date=_d(2026, 11, 5),
                         period_end=_d(2026, 9, 30), eps_actual=1.12, eps_estimate=1.05, revenue_actual=3.2e9,
                         revenue_estimate=3.3e9) for s, nm in names))
    long_ = R.EarningsDay(series="earnings", reports=(*short.reports[:-1], R.EarningsReport(
        series="earnings", company=_co("MMMMM-A", "W" * 20 + " Co"), report_date=_d(2026, 11, 5),
        period_end=_d(2026, 9, 30), eps_actual=1.12, eps_estimate=1.05, revenue_actual=3.2e9,
        revenue_estimate=3.3e9)))
    for rec, stacked in ((short, False), (long_, True)):
        image, _table = _f13_image(_compose(rec, _d(2026, 11, 12)))
        layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
        names_y = [ln.y for ln in layout.lines if ln.field.endswith(".cells[0]") and ".table" not in ln.field]
        eps_y = [ln.y for ln in layout.lines if ln.field.endswith(".table[0].cells[0]")]
        # compact: each name sits level with its table (between its EPS and Revenue lines); stacked: above it
        assert all((ny < ey) == stacked for ny, ey in zip(names_y, eps_y)), (stacked, names_y, eps_y)


def _worst_stakes(n: int, *, source_len: int = 60, name_len: int = 32):
    """news-v23 — the worst post of `n` stakes the server lets through: an investor name of 31 "W" (two 15-letter words:
    `STAKE_PAIR_NAME_WORD_MAX_CHARS`), investee names of `name_len` "W" in 15-letter words (the read's 32-character
    cap, `T.STAKE_NAME_MAX_CHARS`), the widest basis word ("committed up to"), the widest figure ("$999.9B") and a
    source of `source_len` characters of "W" words of 20 (`T.NAME_WORD_MAX_CHARS`) — at most
    `T.STAKE_ROW_SOURCE_MAX_CHARS` is drawn on the row's line."""
    inv = _co("MMMMM-A", "W" * 15 + " " + "W" * 15)
    src_words, left = [], source_len - 9
    while left > 0:
        k = min(20, left)
        src_words.append("W" * k)
        left -= k + 1
    source = ("Wa 10-Q " + " ".join(src_words)[:source_len - 9].strip() + "a")
    rows = []
    for i in range(n):
        words, left = [], name_len
        while left > 0:
            k = min(T.PAIR_NAME_WORD_MAX_CHARS, left)
            words.append("W" * k)
            left -= k + 1
        name = " ".join(words)[:name_len].strip()
        name = name[:-1] + "ABCDEFGH"[i]
        rows.append(R.CompanyStake(
            series="company_stakes", stake_id=f"0000000{i}-0000-4000-8000-000000000000", investor=inv,
            investee_name=name, investee=_co("NPRSTUVX"[i] * 5 + "-A", "Fabrikam " + "ABCDEFGH"[i]), kind="commitment",
            value_usd=999_940_000_000.0 - i, value_basis="committed_up_to", ownership_pct=None, as_of=date(2026, 9, 30),
            verified_on=date(2026, 10, 1), source_title=source, background=None, listed_since=None,
            local_listing=None, is_new=False))
    rows.sort(key=R.stake_rank_key)
    return R.CompanyStakes(series="company_stakes", investor=inv, stakes=tuple(rows))


def _lay_out_stakes_post(out: Dict[str, Any], engine: str) -> None:
    """news-v23: a company stakes LIST post — the picture and its cover (the list on the 9:16 frame) fit BEFORE the floor
    step, drawing exactly their strings (the investor plate's NAME is declared, never drawn: its pill draws the ticker,
    the title names the company — the 13F list's rule), the cover's content above the burned-caption band; every other
    video card lays out."""
    image, table = _f13_image(out)
    assert nl._is_stakes_list(image)
    layout = nl.layout_template(image, font_path=FONT, layout_engine=engine)
    assert layout.step < cards.RAMP_STEPS, layout.step
    _assert_draws_exactly_its_strings(image, layout, undrawn=("header.name",))
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    specs = cards.cards_for_script(script, table)
    assert [s.kind for s in specs] == ["opening", "text", "text", "text", "text", "disclaimer"]
    assert specs[0].image is not None                                             # the cover is the list
    cover = cards.layout_card(specs[0], font_path=FONT, layout_engine=engine)
    assert cover.step < cards.RAMP_STEPS, cover.step
    content = [ln for ln in cover.lines if ln.field not in ("brand", "kicker", "footer")
               and not look.is_logo_standin(ln.field)]
    assert max(ln.ink[3] for ln in content) <= cards.CAPTION_BAND_Y[0]
    for spec in specs[1:]:
        assert cards.layout_card(spec, font_path=FONT, layout_engine=engine).step <= cards.RAMP_STEPS


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_the_company_stakes_list_image_and_video_fit(engine, n):
    """news-v23 (owner 2026-11-19: one investor's stakes in 1-4 public companies, every one on the picture — review
    2026-11-20; stakes_nvidia.jpg): the worst post the server lets through (`_worst_stakes`: the widest names, basis
    word, figure and drawn source) fits the picture and every video card — the cover is the list, its first three rows
    and "+1 more stake in the caption" for a fourth — before the floor step, every string drawn whole."""
    rec = _worst_stakes(n)
    out = _compose(rec, date(2026, 12, 29))
    rows = out["image_spec"]["sections"][0]["rows"]
    assert len(rows) == n and rows[0]["label"] == "committed up to" and rows[0]["cells"][2] == "$999.9B"
    assert len(rows[0]["cells"][1]) == len("As of Sep 30, 2026 · ") + T.STAKE_ROW_SOURCE_MAX_CHARS
    assert out["image_spec"]["sections"][0].get("more") is None
    assert (out["opening_card"]["list"].get("more") is not None) is (n > T.STAKES_COVER_ROWS)
    _lay_out_stakes_post(out, engine)


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_a_stake_source_too_wide_for_its_row_is_left_to_the_captions_and_why(engine, monkeypatch):
    """`T.STAKE_ROW_SOURCE_MAX_CHARS` (60): a longer source is left off the row's line ("As of {date}" alone — the captions
    keep it) and the worst post still fits. Anti-vacuity: with the bound lifted, four rows of an 80-character source of
    the widest glyph cannot share their tiles with their dates in two lines — the picture overflows. Mutation-checked by
    hand (2026-11-20): the bound's comparison removed from `_stakes_parts`, the first half turned red."""
    out = _compose(_worst_stakes(4, source_len=80), date(2026, 12, 29))
    assert {r["cells"][1] for r in out["image_spec"]["sections"][0]["rows"]} == {"As of Sep 30, 2026"}
    assert "Wa 10-Q" in out["captions"]["facebook"]
    _lay_out_stakes_post(out, engine)
    monkeypatch.setattr(T, "STAKE_ROW_SOURCE_MAX_CHARS", 80)
    wide = _compose(_worst_stakes(4, source_len=80), date(2026, 12, 29))
    with pytest.raises(cards.CardOverflow):
        nl.layout_template(_f13_image(wide)[0], font_path=FONT, layout_engine=engine)


def test_the_stakes_list_draws_the_mocks_parts_in_their_colours():
    """stakes_nvidia.jpg, drawn by the worker: the investor's plate (its ticker on the GOLD pill), the two-tone title
    with "IN PUBLIC COMPANIES" gold, per row a NAVY chip, the name INK, the basis word INK2 right-aligned over the amount
    GOLD_INK (a stake's figure carries no verdict: never green or red), the as-of line INK2 on its TILE at the card's
    foot — the amount's baseline the name's, the basis word above the amount; review 2026-11-20: the picture draws
    every stake of a post (at most four, as the mock — which has no "+N more" line), the COVER three and the grey "+1
    more stake in the caption" line."""
    import test_marketing_news_templates as NT
    out = _compose(NT.nvidia_stakes_more(), date(2026, 12, 29))
    image, _table = _f13_image(out)
    assert nl._is_stakes_list(image)
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert layout.step == 0
    by = {}
    for ln in layout.lines:
        by.setdefault(ln.field, []).append(ln)
    assert by["header.chip"][0].text == "NVDA"
    title = by["title"]
    assert [ln.fill for ln in title if ln.text in ("IN PUBLIC", "COMPANIES", "IN PUBLIC COMPANIES")] and all(
        ln.fill == look.GOLD for ln in title if "PUBLIC" in ln.text or "COMPANIES" in ln.text)
    assert all(ln.fill == look.WHITE for ln in title if "STAKES" in ln.text)
    for j in range(4):
        rp = f"sections[0].rows[{j}]"
        (name,), (fig,), (label,), (chip,) = by[f"{rp}.cells[0]"], by[f"{rp}.cells[2]"], by[f"{rp}.label"], \
            by[f"{rp}.chip"]
        assert name.fill == look.INK and fig.fill == look.GOLD_INK and label.fill == look.INK2
        assert chip.fill == look.WHITE                                            # white ink on the navy chip
        assert fig.y == name.y and label.y < fig.y and fig.anchor == label.anchor == "rs"
        meta = by[f"{rp}.cells[1]"]
        assert all(m.fill == look.INK2 for m in meta) and min(m.y for m in meta) > fig.y
    assert "sections[0].more" not in by                                         # the picture draws all four
    assert any(r.fill == look.TILE for r in layout.rects) and any(r.fill == look.NAVY_CHIP for r in layout.rects)
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    cover = cards.layout_card(cards.cards_for_script(script, _table)[0], font_path=FONT,
                              layout_engine="basic")
    more = [ln for ln in cover.lines if ln.field == "sections[0].more"]
    assert [ln.text for ln in more] == ["+1 more stake in the caption"] and more[0].fill == look.GREY
    # not a stakes list: another series, or a row without its basis word, falls to its own placer
    import dataclasses
    other = dataclasses.replace(image, series="thirteen_f")
    assert not nl._is_stakes_list(other)
    spec = json.loads(json.dumps(image.spec))
    del spec["sections"][0]["rows"][0]["label"]
    assert not nl._is_stakes_list(dataclasses.replace(image, spec=spec))


# ── news-v23 phase 3 (2026-11-20): the weekly theme post's pictures, at the server's own worst ──────────────────────
#
# The theme tiles and ranked rows are bounded by the worker's FONT measured on the server (`text_metrics`, an upper
# bound on Pillow's widths) at the FLOOR size: a tile name ≤ THEME_NAME_MAX_LINES lines in THEME_TILE_TEXT_W, a tile's
# text ≤ THEME_TILE_MAX_LINES lines; a ranked name ONE line in THEME_RANKED_NAME_W. Each test composes the widest
# record the server accepts and lays it out with the worker's own code, every logo a wordmark; its twin breaks the
# bound and must overflow — never a clipped or shrunk-past-the-floor string.

#: Widest one-line tile name / segment label (14 "W" at 22 px: 327.6 px of text_metrics' 347).
_TH_W14 = "W" * 14
#: The widest tile name the server lets onto a tile: two 14-"W" words, two lines at the floor.
_TH_NAME2 = _TH_W14 + " " + _TH_W14
#: The tallest title THEME_TITLE_MAX_LINES lets through (60 characters, 3 lines by its character count).
_TH_TITLE = " ".join(["W" * 20, "W" * 20, "W" * 18])


def _th_share_seg() -> str:
    """The widest "W" segment whose one-line tile line ("<seg> · 99.5% of revenue") fits THEME_TILE_TEXT_W."""
    seg = "W"
    while T.text_metrics.line_count(T._t("th.img.line.share", seg=seg + "W", share="99.5%"), T.THEME_TILE_FLOOR_PX,
                                     T.THEME_TILE_TEXT_W) == 1:
        seg += "W"
    return seg


def _theme_worst(slug: str, title: str = _TH_TITLE):
    """24 members (12 tiles, "+12 more"): even tiles a TWO-line name over the widest one-line share line, odd tiles a
    one-line name over a one-line label and its share line — every tile THREE lines at the floor, the server's cap."""
    seg2 = _th_share_seg()
    syms = [f"{a}{b}" for a in "ABCDEFGHJKLM" for b in "AB"]
    members = tuple(R.ThemeMember(company=_co(s, _TH_NAME2 if i % 2 == 0 else _TH_W14),
                                  top_segment=seg2 if i % 2 == 0 else _TH_W14, top_segment_share=0.995,
                                  fiscal_year="2026") for i, s in enumerate(syms))
    return R.ThemeExplainer(series="theme_explainer", slug=slug, title=title, members=members,
                            tickers_as_of=date(2026, 11, 1))


def test_phase3_the_server_measures_the_worker_font_as_an_upper_bound(engine="basic"):
    """`text_metrics.INTER_BOLD_ADVANCES` is the font's own hmtx table (fontTools), and `text_width` is never under
    what Pillow draws at the tile and row floors (or at the line's start sizes) — for every character the table holds,
    alone and in runs, and for the alphabet a company name may hold. Fails on any font change before a bound drifts."""
    from fontTools.ttLib import TTFont
    from PIL import ImageFont

    from app.services.marketing import text_metrics as tm

    tt = TTFont(FONT)
    cmap, hmtx = tt.getBestCmap(), tt["hmtx"]
    assert tt["head"].unitsPerEm == tm.UNITS_PER_EM
    for ch, adv in tm.INTER_BOLD_ADVANCES.items():
        assert hmtx[cmap[ord(ch)]][0] == adv, ch
    # a character outside the table counts as the widest glyph of the WHOLE face — never narrower than it draws
    assert tm.WIDEST_ADVANCE == max(hmtx[g][0] for g in cmap.values()) > tm.INTER_BOLD_ADVANCES["W"]
    assert tm.text_width("ü", 22) > tm.text_width("W", 22) and tm.text_width("Ǳ", 22) >= 2839 * 22 / 2048
    samples = list(tm.INTER_BOLD_ADVANCES) + [c * 12 for c in tm.INTER_BOLD_ADVANCES] + [
        _F13_NAME_ALPHABET, _TH_NAME2, "Data Center · 88% of revenue", "Client Computing, FY2022 · 55% of revenue",
        "Semiconductor Solutions · 58% of revenue", "$40.8T", "WWWWW-A", "Nano Nuclear Energy",
        # characters outside the table (a filing's segment label): the face's widest glyphs among them
        "Nestlé Health Ǳ‰ üñö ™", "‱⟷Ⅷ"]
    for px in sorted({T.THEME_TILE_FLOOR_PX, T.THEME_RANKED_FLOOR_PX, nl.TH_NAME.size(0), nl.TH_LINE.size(0),
                      nl.TH_RANK_NAME.size(0)}):
        for layout_engine in _F13_ENGINES:
            font = ImageFont.truetype(FONT, px, layout_engine=(ImageFont.Layout.RAQM if layout_engine == "raqm"
                                                               else ImageFont.Layout.BASIC))
            for text in samples:
                assert font.getlength(text) <= tm.text_width(text, px), (text, px, layout_engine)


def test_phase3_the_theme_fit_constants_mirror_the_server():
    """The worker's copies of the server's tile / row bounds (cards.py cannot import app.*), equal."""
    for name in ("THEME_TILE_TEXT_W", "THEME_TILE_FLOOR_PX", "THEME_TILE_MAX_LINES", "THEME_NAME_MAX_LINES",
                 "THEME_RANKED_NAME_W", "THEME_RANKED_FLOOR_PX"):
        assert getattr(cards, name) == getattr(T, name), name
    # the worker's floors are the server's floors (a tile name and line, a ranked name, never drawn smaller)
    assert nl.TH_NAME.floor == nl.TH_LINE.floor == T.THEME_TILE_FLOOR_PX
    assert nl.TH_RANK_NAME.floor == T.THEME_RANKED_FLOOR_PX


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("slug", ["worst", "ai-chips", "cyber-wars"])
def test_phase3_the_theme_grid_image_and_cover_fit_the_servers_worst(engine, slug, shipped_pair_grid):
    """Twelve THREE-line tiles (the server's cap: a two-line name over a one-line share line, or a one-line name over
    its label and share line) under the tallest title the server accepts, "+12 more", with no icon / the chip / the
    widest icon (the shield): the picture and its list cover lay out before the floor step, every string whole."""
    rec = _theme_worst(slug)
    out = _compose(rec, date(2026, 11, 16))
    tiles = out["image_spec"]["tiles"]
    assert len(tiles) == 12 and out["image_spec"]["more"] == "+12 more"
    for t in tiles:
        lines = T._tile_name_lines(t["name"]) + T.text_metrics.line_count(t["line"], T.THEME_TILE_FLOOR_PX,
                                                                          T.THEME_TILE_TEXT_W)
        assert lines == T.THEME_TILE_MAX_LINES or (t["line"].endswith("of revenue") and lines == 2), t
    assert any(T._tile_name_lines(t["name"]) == 2 for t in tiles)
    assert out["image_spec"].get("icon") == T.THEME_ICON_OF_SLUG.get(slug)
    _lay_out_list_post(out, engine)


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_phase3_a_tile_past_the_servers_bounds_overflows_never_clips(engine, shipped_pair_grid):
    """Anti-vacuity: the same worst grid with ONE tile's name three lines (THEME_NAME_MAX_LINES refuses it), or every
    tile's name six lines, does not fit at any step — the worker draws a name in at most two lines, as the server
    counts."""
    out = _compose(_theme_worst("worst"), date(2026, 11, 16))
    nl.layout_template(_f13_image(out)[0], font_path=FONT, layout_engine=engine)
    for change in ("one", "all"):
        bad = json.loads(json.dumps(out))
        for i, tile in enumerate(bad["image_spec"]["tiles"]):
            if change == "all" or i == 0:
                tile["name"] = " ".join([_TH_W14] * (3 if change == "one" else 6))
        with pytest.raises(cards.CardOverflow):
            nl.layout_template(_f13_image(bad)[0], font_path=FONT, layout_engine=engine)


def _ranked_worst(names=None, n: int = 14, slug: str = "cyber-wars"):
    """A market-value post at the widest the server lets onto a ranked row: a name of the widest one line in
    THEME_RANKED_NAME_W at the floor, the widest chip a symbol can be ("WWWWW-A"), the widest value ("$40.8T")."""
    w = "W"
    while T.text_metrics.line_count(w + "W", T.THEME_RANKED_FLOOR_PX, T.THEME_RANKED_NAME_W) == 1:
        w += "W"
    syms = ["WWWWW-A", "WWWWW-B", "WWWWW-C", "MWWWW-A", "MWWWW-B", "MWWWW-C", "WMWWW-A", "WMWWW-B", "WMWWW-C",
            "WWMWW-A", "WWMWW-B", "WWMWW-C", "WWWMW-A", "WWWMW-B"][:n]
    values = [40.8e12 - i * 1e12 for i in range(n)]
    members = tuple(R.ThemeMember(company=_co(s, (names or {}).get(i, w)), top_segment=None, top_segment_share=None,
                                  fiscal_year=None, market_value_usd=v) for i, (s, v) in enumerate(zip(syms, values)))
    return R.ThemeExplainer(series="theme_explainer", slug=slug, title=_TH_TITLE, members=members,
                            tickers_as_of=date(2026, 11, 18), theme_size=16, angle="biggest",
                            posted_on=date(2026, 11, 25), values_as_of=date(2026, 11, 24)), w


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_phase3_the_ranked_image_and_cover_fit_the_servers_worst(engine, shipped_pair_grid):
    rec, widest = _ranked_worst()
    out = _compose(rec, date(2026, 11, 25))
    spec = out["image_spec"]
    assert spec["layout"] == "ranked" and len(spec["rows"]) == T.THEME_RANKED_ROWS and spec["more"] == "+4 more"
    assert {r["name"] for r in spec["rows"]} == {widest} and spec["rows"][0]["value"] == "$40.8T"
    assert spec["rows"][0]["chip"] == "WWWWW-A" and spec["icon"] == "shield"
    _lay_out_list_post(out, engine)


#: How many "W" past the server's widest ranked name the worker's floor column still lays out, per engine (measured
#: 2026-11-26): BASIC (this Mac) 3; RAQM — the production worker image, run in a Debian bookworm container — 2
#: (HarfBuzz kerning widens "WW"), and 3 overflow. Either way the server's bound is the conservative side.
_RANKED_NAME_SLACK = {"basic": 3, "raqm": 2}


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_phase3_a_ranked_name_past_the_servers_bound_overflows_never_clips(engine, shipped_pair_grid):
    """Anti-vacuity: the server refuses one "W" past the widest one-line name (`slot_rejected`); the worker's column
    at the floor is a little wider (recorded per engine, `_RANKED_NAME_SLACK`: that many "W" more still lay out — the
    server's bound is the conservative side, text_metrics' upper bound and the narrowest chip/value column), and one
    more overflows at every step."""
    rec, widest = _ranked_worst()
    out = _compose(rec, date(2026, 11, 25))
    nl.layout_template(_f13_image(out)[0], font_path=FONT, layout_engine=engine)
    with pytest.raises(T.NewsTemplateRefused, match="slot_rejected"):
        _compose(_ranked_worst({3: widest + "W"})[0], date(2026, 11, 25))
    slack = _RANKED_NAME_SLACK[engine]
    for extra, fits in ((slack, True), (slack + 1, False)):
        bad = json.loads(json.dumps(out))
        bad["image_spec"]["rows"][3]["name"] = widest + "W" * extra
        if fits:
            nl.layout_template(_f13_image(bad)[0], font_path=FONT, layout_engine=engine)
        else:
            with pytest.raises(cards.CardOverflow):
                nl.layout_template(_f13_image(bad)[0], font_path=FONT, layout_engine=engine)


@pytest.mark.parametrize("which", ["grid", "ranked"])
def test_phase3_the_theme_pictures_are_deterministic(which, shipped_pair_grid):
    """The same spec draws the same bytes twice (the reuse key's promise), icon included."""
    if which == "grid":
        out = _compose(_theme_worst("ai-chips", title="AI chips"), date(2026, 11, 16))
    else:
        out = _compose(_ranked_worst()[0], date(2026, 11, 25))
    image = _f13_image(out)[0]
    a = nl.render_template_image(image, font_path=FONT, layout_engine="basic").data
    b = nl.render_template_image(image, font_path=FONT, layout_engine="basic").data
    assert a == b and len(a) > 10_000


#: news-v23: a video text card's title column (the two-tone headline, 8 px inside the content zone) and its body's
#: (on the warm card, after the gold dot: card_layouts._body_card).
_CARD_COLUMN = (look.VIDEO_FRAME.area[2] - 8) - (look.VIDEO_FRAME.area[0] + 8)
_BODY_COLUMN = (look.VIDEO_FRAME.area[2] - 40) - (look.VIDEO_FRAME.area[0] + 96)


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_the_card_title_word_cap_is_the_widest_W_run_a_card_title_holds(engine):
    """Only the theme's video cards are titled with company names in 2b: CARD_TITLE_WORD_MAX_CHARS fits a text
    card's title at the floor (news-v23: the title column is wider than the old panel's, so the cap is a SAFE bound
    now — any name word, NAME_WORD_MAX_CHARS, fits too); a body holds every name word."""
    assert _w_cap(cards.TITLE_STYLE, _CARD_COLUMN, engine) >= T.NAME_WORD_MAX_CHARS >= T.CARD_TITLE_WORD_MAX_CHARS
    assert _w_cap(cards.BODY_STYLE, _BODY_COLUMN, engine) >= T.NAME_WORD_MAX_CHARS
    for style in (cards.TITLE_STYLE, cards.BODY_STYLE):
        font = _floor_font(style, engine)
        assert font.getlength(max(_F13_NAME_ALPHABET, key=font.getlength)) == font.getlength("W")


def _theme_with_narrated(name: str):
    """Six narrated members called `name` (the facts go first, theme order), then three short ones; short segments.
    news-v23 phase 3: the narration follows the PICTURE's order, so a narrated name is one a tile draws (at most
    THEME_NAME_MAX_LINES at the floor) — the names below are the widest and the longest-worded such names."""
    members = [R.ThemeMember(company=_co(f"L{a}", name), top_segment="Cloud", top_segment_share=0.5, fiscal_year="2026")
               for a in "ABCDEF"]
    members += [R.ThemeMember(company=_co(f"S{a}", "Wwwwwwwwww"), top_segment="Cloud", top_segment_share=0.5,
                              fiscal_year="2026") for a in "ABC"]
    return R.ThemeExplainer(series="theme_explainer", slug="cards", title="AI chips", members=tuple(members),
                            tickers_as_of=date(2026, 11, 1))


#: A tile-drawable name with a 20-character word (narrow letters: one line of a tile) — past CARD_TITLE_WORD_MAX_CHARS.
_TH_LONG_WORD = "W" + "i" * 19 + " Wwwwwwwwww"
#: name → whether the card titles name the members: the widest name a tile draws (two 14-"W" words, its two lines),
#: a five-word name (two tile lines), and a 20-character word (the generic title, the names in the body).
_THEME_CARD_NAMES = {_TH_NAME2: True, "Wwww Wwww Wwww Wwww Wwwww": True, _TH_LONG_WORD: False}


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("name", sorted(_THEME_CARD_NAMES))
def test_the_theme_video_cards_fit_whatever_the_member_names(engine, name, shipped_pair_grid):
    assert T._tile_name_lines(name) <= T.THEME_NAME_MAX_LINES               # a name the picture (so the video) draws
    out = _compose(_theme_with_narrated(name), date(2026, 11, 16))
    titles = [c["title"] for c in out["cards"][:3]]
    if _THEME_CARD_NAMES[name]:
        assert all(name in t for t in titles), titles
    else:
        assert titles == ["Largest revenue lines"] * 3 and all(name in c["body"] for c in out["cards"][:3])
    _lay_out_everything(out, engine)


def test_phase3_a_member_no_tile_can_draw_is_never_narrated():
    """news-v23 phase 3 (video = picture): a member whose name takes more than THEME_NAME_MAX_LINES tile lines is left
    off the picture, so off the narration and the cards too — the video names only the picture's first members."""
    long_name = " ".join(["W" * 10] * 3)                     # three tile lines at the floor
    assert T._tile_name_lines(long_name) == 3
    out = _compose(_theme_with_narrated(long_name), date(2026, 11, 16))
    assert [t["name"] for t in out["image_spec"]["tiles"]] == ["Wwwwwwwwww"] * 3
    assert long_name not in json.dumps(out["cards"]) and long_name not in " ".join(out["video_script"])
    assert long_name not in json.dumps(out["opening_card"])


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_without_the_card_title_cap_a_long_name_word_overflows_the_card(engine, shipped_pair_grid, monkeypatch):
    """news-v23: with the cap lifted to any name word the card still fits (the look's title column holds 20 "W");
    a word wider than the column at the floor — two "W" past what it holds — is what overflows, never clipped."""
    monkeypatch.setattr(T, "CARD_TITLE_WORD_MAX_CHARS", T.NAME_WORD_MAX_CHARS)
    out = _compose(_theme_with_narrated(_TH_LONG_WORD), date(2026, 11, 16))
    assert _TH_LONG_WORD.split()[0] in out["cards"][0]["title"]
    _image, table = _f13_image(out)
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None}
                              for r in out["logo_refs"]])
    cards.layout_card(cards.cards_for_script(script, table)[1], font_path=FONT, layout_engine=engine)
    wide = "W" * (_w_cap(cards.TITLE_STYLE, _CARD_COLUMN, engine) + 2)
    with pytest.raises(cards.CardOverflow):
        cards.layout_card(cards.CardSpec("text", title=f"{wide} chips", body="b"), font_path=FONT,
                          layout_engine=engine)


# ── news-v19 (Batch 1, plan item 5): the covers' period chips, laid out by the worker ─────────────────
#
# The earnings, Money Map and symbol-less 13F covers carry a chip now — one more LABEL line under the logo:
# "Quarter ended Sep 30, 2026" / "Reported Nov 10, 2026" (earnings: a verified quarter / the report day),
# "Fiscal 2025" (Money Map), "Quarter ended Sep 30, 2026" (a 13F filer with no symbol). A Form 4, Congress
# or listed-filer 13F cover keeps its ticker, a stake its basis and date. Each new chip is proved with the
# worker's OWN opening-card code (`cards.opening_spec` + `cards.layout_card`: no worker change) at its
# WIDEST — the rendering widest in pixels over every day of 2025-2035 (every fiscal year 1990-2099), at the
# label's start and floor sizes — on the tallest cover of its series, every logo a wordmark: the chip is
# drawn whole, as the server allows it, and the cover keeps the room to spare every cover keeps.

from dataclasses import replace  # noqa: E402
from datetime import timedelta  # noqa: E402

#: news-v23: the cover's column (the content zone, 8 px in); the chip is ONE gold pill in it (cards.CHIP_STYLE).
_COVER_COLUMN = (look.VIDEO_FRAME.area[2] - 8) - (look.VIDEO_FRAME.area[0] + 8) - 2 * look.CHIP_PAD_X
#: news-v23 (the ONE 13F list): the 13F cover carries no period chip — its only chip is a listed filer's ticker.
#: news-v23: the earnings period chips left with the one-company cover (`test_an_earnings_cover_has_no_chip`).
#: 2026-11-20: the Money Map's "Fiscal {fy}" chip left too — its cover carries the TICKER, as the picture's plate does,
#: and the fiscal year rides the cover's short footer. No cover carries a period chip any more.
_CHIP_KINDS: Tuple[str, ...] = ()


def _lay_cover(card: Dict[str, Any], names: Dict[str, str], engine: str):
    """The cover laid out by the worker, every logo a wordmark of its company name (the widest case)."""
    logos = [{"key": k, "name": n, "url": None, "sha256": None} for k, n in names.items()]
    table = cards.logo_table({"logos": logos}, {})
    assert tos.validate_opening_card(card, tos.logo_keys(logos)) is None          # the server would accept it
    return cards.layout_card(cards.opening_spec(card, table), font_path=FONT, layout_engine=engine)


def _assert_the_chip_fits(card: Dict[str, Any], names: Dict[str, str], engine: str) -> int:
    """The cover lays out with room to spare and draws its chip whole — exactly what the server allows;
    without the chip it is never taller (the chip is really drawn: its line costs room). Returns the step. "Room to
    spare" is before the floor step under BASIC (this Mac); under RAQM, the production worker's engine, the widest Money
    Map cover needs the floor step itself (measured 2026-11-26 in a Debian bookworm container) — it still fits whole."""
    layout = _lay_cover(card, names, engine)
    assert layout.step < cards.RAMP_STEPS + (engine == "raqm"), (card["chip"], card["headline"], layout.step)
    assert " ".join(layout.field_lines("label")) == card["chip"]
    logos = [{"key": k, "name": n, "url": None, "sha256": None} for k, n in names.items()]
    assert card["chip"] in tos.opening_strings(card, logos)
    bare = {k: v for k, v in card.items() if k != "chip"}
    assert _lay_cover(bare, names, engine).step <= layout.step
    return layout.step


def test_every_period_chip_is_one_label_line_within_the_cap():
    """No period chip is left to bound (news-v23: the earnings cover is its table; 2026-11-20: the Money Map cover's chip
    is its ticker) — the lexicon holds none, and the server's cap stays OPENING_CHIP_MAX_CHARS (36: a stake's
    "Committed up to · as of Nov 18, 2025"); a ticker chip is laid out at its widest by the Money Map cover test."""
    assert T.OPENING_CHIP_MAX_CHARS == tos.OPENING_CHIP_MAX_CHARS == 36
    assert _CHIP_KINDS == () and not [k for k in T.LEXICON if ".open.chip" in k]


def test_an_earnings_cover_has_no_chip():
    """news-v23: an earnings cover is its TABLE (the opening card's `list`) — no plate, no chip (news-v19's period chips
    "Quarter ended …" / "Reported …" left with the one-company cover); the picture's subtitle dates the reports."""
    out = _compose(R.EarningsDay(series="earnings", reports=tuple(
        R.EarningsReport(series="earnings", company=_co(sym, name), report_date=date(2026, 11, 5),
                         period_end=date(2026, 9, 30), eps_actual=1.12, eps_estimate=1.05, revenue_actual=3.2e9,
                         revenue_estimate=3.3e9) for sym, name in (("COST", "Costco"), ("NKE", "Nike")))),
        date(2026, 11, 12))
    assert "chip" not in out["opening_card"] and out["opening_card"]["list"]["columns"] == [
        "Estimate", "Reported", "Surprise"]
    assert "er.open.chip.period" not in T.LEXICON and "er.open.chip.report" not in T.LEXICON


# (owner decisions 2026-11-20, "All rows, compact": a Form 4 cover is the LIST of every company its picture draws —
# never one company's "{subj} disclosed buying stock" — so the one-headline-form fit test of news-v19 is retired with
# that headline; the list covers' fit at their widest is test_r5_the_form4_roundup_cover_is_its_list_and_fits_at_its_
# widest and test_il_every_insider_list_picture_and_cover_fits_the_worker.)


def test_the_cover_room_is_finite_so_the_chip_fit_is_no_vacuous_pass():
    """Anti-vacuity: the room-to-spare assertion (`_assert_the_chip_fits`) can fail — a chip several times the cap
    (twelve of news-v19's Money Map period chips joined) takes a plain cover under the tallest 60-character filer
    headline to the floor (news-v23: the 13F cover itself is the list and carries no period chip, so the plain card is
    built by hand)."""
    font = cards._font(FONT, cards.OPENING_HEADLINE_STYLE.size(cards.RAMP_STEPS - 1), "basic")
    before = T.LEXICON["f13.subj.of"].split("{filer}")[0]
    _lines_, filer = _worst_filer(R.FILER_NAME_CHARS[1], before=before, after="", own_glue=True, font=font,
                                  width=_COVER_COLUMN)
    chip = " · ".join(["Fiscal 2025"] * 12)                  # news-v19's period chip, twelve times
    card = {"kicker": "13F FILING", "logos": ["ZA"], "chip": chip, "figure": "99999", "headline": before + filer}
    table = cards.logo_table({"logos": [{"key": "ZA", "name": "Wwwwwwwwww", "url": None, "sha256": None}]}, {})
    try:
        step = cards.layout_card(cards.opening_spec(card, table), font_path=FONT, layout_engine="basic").step
    except cards.CardOverflow:
        step = cards.RAMP_STEPS + 1
    assert len(chip) > 4 * T.OPENING_CHIP_MAX_CHARS and step >= cards.RAMP_STEPS


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
    canvas = Image.new("RGB", (300, 300), look.CARD)
    _draw_disc(canvas, (20, 20, 220, 220), cards.LogoArt("K", "Kay", str(path)))
    greys = [px for px in canvas.getdata() if 100 <= px[0] <= 160 and px[0] == px[1] == px[2]]
    assert greys, "the grey band must survive the draw"


# ── Batch 2 (2026-10-19, "first seconds"): the DATA CARD — the post image in the video card's text zone ──
#
# A template video's text card may be replaced by the day's post image (the frozen feature `data_card`;
# cards.cards_for_script decides, see tests/test_marketing_cards.py). It is the SAME picture — the same
# TemplateImage, placers and strings — laid out on the 9:16 card (news-v23: look.VIDEO_FRAME — the brand row 140 px
# from the top, the content in cards.SAFE_X above the caption band, the footer under it, nothing in the safe bottom)
# instead of the 4:5 page, and only when every line it draws is at least DATA_CARD_MIN_PX. It is never a bitmap
# scale of the JPEG.

#: The post image's bytes in the approved look (news-v23, cards/v6), measured on Pillow 12.0.0 (basic engine).
#: Fidelity review 2026-11-20: re-pinned (the kit's chip box, the roundup's 76 px headline and row block, the
#: spotlight's 26 px chip and 36 px footer gap, the Money Map's card levels; cards/v6 is unshipped — no bump).
_POST_IMAGE_GOLDEN = {
    "bars": "c21a3a4379d37813a1d75383db27935beaa6165be754113ab9054052e9da2abd",
    # owner decisions 2026-11-20 (form4_roundup5.jpg): a section of four rows or more — three headline lines, 14 px apart
    "rows": "346cc9f6842a7ce9c41f5f6d65527226bb0237a33fa483a088d09390cbe39261",
    # 2026-11-20 (the two-tone split at a sentence or clause boundary): the spotlight sample's headline ("GameStop's CEO
    # disclosed buying GameStop stock" — no gold tail, no boundary) is one block, its last line gold — re-pinned
    "spotlight": "ad6d0c440597b5fed1a190b9f5c2b91ce968ebb0c24527711c6a3abba0aed55a",
}


@pytest.mark.skipif(PIL.__version__ != "12.0.0", reason="the golden bytes were measured on Pillow 12.0.0")
def test_the_post_image_is_pinned():
    import hashlib

    got = {name: hashlib.sha256(nl.render_template_image(_image(SPECS[name](), _table()), font_path=FONT,
                                                         layout_engine="basic").data).hexdigest()
           for name in sorted(_POST_IMAGE_GOLDEN)}
    assert got == _POST_IMAGE_GOLDEN


def test_the_two_frames():
    """news-v23: the post image's frame is look.POST_FRAME (the brand row at y 48, the headline at 140, the cards'
    56 px side edges, the footer 34 px off the bottom); the data card's is look.VIDEO_FRAME — the brand row 140 px
    from the top (cover_13f_berkshire.jpg), the content in cards.SAFE_X down to TEXT_ZONE_Y[1] (never under the
    burned captions), the footer under the caption band and above the safe bottom (nothing in the last 420 px)."""
    assert nl.POST_IMAGE_FRAME is look.POST_FRAME and nl.DATA_CARD_FRAME is look.VIDEO_FRAME
    p, v = nl.POST_IMAGE_FRAME, nl.DATA_CARD_FRAME
    assert p.canvas == (1080, 1350) and p.brand == (64, 48) and p.area[:3] == (56, 140, 1024)
    assert v.canvas == (cards.WIDTH, cards.HEIGHT)
    assert (v.area[0], v.area[2]) == cards.SAFE_X and v.area[3] == cards.TEXT_ZONE_Y[1] < cards.CAPTION_BAND_Y[0]
    # the brand row as cover_13f_berkshire.jpg draws it (mock_f13_lists.render_cover: brand(size=56) — a 34 px word —
    # and kicker_right(size=28), the glow lowered with the row), on the TEXT column: the area's side + 8, so the mark,
    # the kicker's right edge and the headline (x0 + 8) line up, as the picture's 64 = 56 + 8 and the mock's 98 = 90 + 8
    assert v.brand == (v.area[0] + 8, 140) and v.right == v.area[2] - 8
    assert (v.brand_px, v.brand_word_px, v.kicker_px, v.glow_y) == (56, 34, 28, (140 - 260, 140 + 420))
    assert (p.brand_px, p.brand_word_px, p.kicker_px, p.glow_y) == (52, 32, 26, (-260, 420))     # style_kit's
    assert v.area[1] > v.brand[1] + v.brand_px
    assert cards.CAPTION_BAND_Y[1] < v.footer_bottom - 30 and v.footer_bottom <= look.VIDEO_SAFE_BOTTOM == 1500
    assert nl._place.__defaults__ == (nl.POST_IMAGE_FRAME, 0, None, None)      # the post image keeps its frame
    assert nl.DATA_CARD_MIN_PX == 22


def _pair_spec() -> Dict[str, Any]:
    return {"layout": "pair", "version": 1, "kicker": "COMPANY STAKES", "footer": FOOTER,
            "left": {"logo": "NVDA", "name": "NVIDIA"}, "right": {"logo": "AMZN", "name": "Amazon"},
            "figure": "$777.4M", "label": "invested", "lines": ["As of Mar 27, 2026", "Source: Form S-1"]}


def _grid_spec(n: int = 4, *, lines: bool = True) -> Dict[str, Any]:
    tiles = [{"logo": KEYS[i], "name": NAMES[i], **({"line": f"Cloud services · {10 + i}%"} if lines else {})}
             for i in range(n)]
    return {"layout": "grid", "version": 1, "kicker": "INSIDE A THEME", "footer": FOOTER, "title": "AI chips",
            "subtitle": f"What {n} of its companies sell, by largest revenue segment", "tiles": tiles}


#: Sparse pictures — a legible data card: (spec, the step it lays out at on the basic engine).
_LEGIBLE = {
    # news-v23 fix round: the spotlight card fills the room and its figure takes what is left (mock_form4.spotlight),
    # and row cards flex before the page steps (mock_form4.roundup) — so these fit two steps earlier
    # 2026-11-20: a headline with no gold tail and no boundary is ONE balanced block (its last line gold), which takes
    # no more lines than the two separately balanced halves did — these two fit one step earlier
    "spotlight": (_spotlight_spec, 4),
    "rows_1": (lambda: _rows_spec(1), 1),
    # fidelity review 2026-11-20: the row block advances by font size (mock_form4.wide_row_card) — one step earlier
    "rows_3": (lambda: _rows_spec(3), 7),
    "rows_4_no_header": (lambda: _rows_spec(4, header=False), 6),
    "pair": (_pair_spec, 0),
    "grid_4": (_grid_spec, 0),
    "grid_8": (lambda: _grid_spec(8), 0),
    "grid_6_no_lines": (lambda: _grid_spec(6, lines=False), 0),
    # news-v23: the look's row cards and tiles are denser at the floor — these were refused before, now legible
    # owner decisions 2026-11-20 (form4_roundup5.jpg): five rows sit 14 px apart — one step earlier
    "rows_5": (lambda: _rows_spec(5), 10),
    "bars": (_bars_spec, 12),
    # news-v23 phase 3 (theme_ai_chips.jpg): the grid's tiles share one name size and one line size and fill the room,
    # so twelve tiles lay out at the first step (2 before). A theme post draws no data card any more
    # (template_onscreen.NO_DATA_CARD_SERIES); the grid stays drawable as one for a web that still names its slot.
    "grid_12": (lambda: _grid_spec(12), 0),
}
#: Dense ones — no data card (the text card stays): the 13F season's 8 rows under a header.
_DENSE = {"rows_8": lambda: _rows_spec(8)}


def _assert_data_card_inside(layout) -> None:
    """The content inside the frame's content area (its panel), the brand row and the kicker above it, the footer
    under the caption band and above the safe bottom, every disc and card inside the panel."""
    zone = nl.DATA_CARD_FRAME.area
    panel = layout.panel
    assert cards._within(panel, zone) and (panel[0], panel[2]) == (cards.SAFE_X[0], cards.SAFE_X[1])
    for ln in layout.lines:
        if ln.field == "footer":
            assert cards.CAPTION_BAND_Y[1] <= ln.ink[1] and ln.ink[3] <= look.VIDEO_SAFE_BOTTOM, ln
            assert cards._within(ln.ink, (cards.SAFE_X[0], 0, cards.SAFE_X[1], cards.HEIGHT)), ln
        elif ln.field in ("brand", "kicker"):
            assert cards._within(ln.ink, (cards.SAFE_X[0], 130, cards.SAFE_X[1], panel[1])), ln
        else:
            assert cards._within(ln.ink, panel), (ln.field, ln.ink, panel)
    for box in [t[0] for t in layout.tiles] + [r.box for r in layout.rects if r.box[1] >= panel[1]]:
        assert cards._within(box, panel)


@pytest.mark.parametrize("name", sorted(_LEGIBLE))
@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_a_sparse_picture_is_a_legible_data_card_drawing_exactly_its_strings(name, engine, shipped_pair_grid):
    make, basic_step = _LEGIBLE[name]
    spec = make()
    assert tos.validate_image_spec(spec, tos.logo_keys(_entries()), footer=FOOTER) is None
    image = _image(spec, _table())
    layout = nl.layout_data_card(image, font_path=FONT, layout_engine=engine)
    assert layout.kind == f"data:{spec['layout']}" and layout.engine == engine
    if engine == "basic":
        assert layout.step == basic_step
    assert nl.illegible_lines(layout) == [] and min(
        ln.size for ln in layout.lines if not look.is_logo_standin(ln.field)) >= nl.DATA_CARD_MIN_PX
    (_assert_2b_draws_its_strings if spec["layout"] in ("pair", "grid") else _assert_draws_exactly_its_strings)(
        image, layout)
    _assert_data_card_inside(layout)
    # the same strings as the post image, never more: what the video declares for the card
    assert list(image.strings) == list(tos.image_strings(spec, _entries()))


def test_a_wordmark_plate_is_exempt_from_the_floor():
    """A row's wordmark disc draws the company name small (sized by its disc, as on the post image) — it stands in
    for the logo, so it does not refuse the card."""
    layout = nl.layout_data_card(_image(_rows_spec(2), _table()), font_path=FONT, layout_engine="basic")
    small = [ln for ln in layout.lines if ln.size < nl.DATA_CARD_MIN_PX]
    assert small and all(ln.field.startswith("wordmark:") for ln in small)
    assert nl.illegible_lines(layout) == [] and nl.illegible_lines(layout, 999) != []


@pytest.mark.parametrize("name", sorted(_DENSE))
def test_a_dense_picture_is_no_data_card(name, shipped_pair_grid):
    image = _image(_DENSE[name](), _table())
    with pytest.raises(cards.CardOverflow, match="data card"):
        nl.layout_data_card(image, font_path=FONT, layout_engine="basic")
    with pytest.raises(cards.CardOverflow):
        nl.draw_data_card(image, font_path=FONT, layout_engine="basic")
    with pytest.raises(cards.CardOverflow):
        cards.render_card(cards.CardSpec("data", image=image), font_path=FONT, layout_engine="basic")
    nl.layout_template(image, font_path=FONT, layout_engine="basic")      # …while the post image still fits


def test_a_dense_picture_is_refused_for_its_fit_and_a_raised_floor_still_bites(monkeypatch):
    """news-v23: every text style's floor is DATA_CARD_MIN_PX (22) or more (the look: "every text ≥ 22 px except
    logo wordmarks"), so the floor never refuses a picture by itself — a dense one is refused because it does not
    FIT. The guard still bites (anti-vacuity): raised to 30 px, a picture whose notes start at 26 px is refused at
    the first step, as illegible."""
    image = _image(_rows_spec(8), _table())
    with pytest.raises(cards.CardOverflow, match="does not fit"):
        nl.layout_data_card(image, font_path=FONT, layout_engine="basic")
    monkeypatch.setattr(nl, "DATA_CARD_MIN_PX", 30)
    with pytest.raises(cards.CardOverflow, match="legib"):
        nl.layout_data_card(_image(_rows_spec(1), _table()), font_path=FONT, layout_engine="basic")


def test_the_search_stops_at_the_first_illegible_step(monkeypatch):
    """Every size steps down together: once a step draws a line under the floor, no later step can be legible, so
    the data card's search ends there — it never lays out every remaining step (with the floor raised to 26 px:
    the notes start at 26, the subtitle reaches 25 at step 13)."""
    calls: List[int] = []
    real = nl._place

    def spy(image, step, *a, **k):
        calls.append(step)
        return real(image, step, *a, **k)

    monkeypatch.setattr(nl, "_place", spy)
    monkeypatch.setattr(nl, "DATA_CARD_MIN_PX", 26)
    with pytest.raises(cards.CardOverflow, match="legib"):
        nl.layout_data_card(_image(_rows_spec(8), _table()), font_path=FONT, layout_engine="basic")
    first_illegible = min(s for s in range(cards.RAMP_STEPS + 1) if min(
        st.size(s) for st in (nl.SUBTITLE_STYLE, nl.NOTE_STYLE, nl.ROW_SECOND_STYLE, nl.HEADING_STYLE,
                              nl.MORE_STYLE, nl.CHIP_PX, nl.ROW_NAME_STYLE)) < 26)
    assert calls == list(range(first_illegible + 1)) and first_illegible < cards.RAMP_STEPS


@pytest.mark.parametrize("name", ["spotlight", "pair", "grid_4"])
def test_the_data_card_draws_only_inside_the_zone_neutrally_and_deterministically(name, logo_files, shipped_pair_grid):
    image = _image(_LEGIBLE[name][0](), _table(logo_files))
    img, layout = nl.draw_data_card(image, font_path=FONT, layout_engine="basic")
    assert img.size == (cards.WIDTH, cards.HEIGHT) and img.mode == "RGB"
    px = np.asarray(img)
    base = np.asarray(look.canvas(look.VIDEO_FRAME))
    x0, y0, x1, y1 = nl.DATA_CARD_FRAME.area
    for region in (np.s_[:130], np.s_[y1:cards.CAPTION_BAND_Y[1]], np.s_[look.VIDEO_SAFE_BOTTOM:], np.s_[:, :x0],
                   np.s_[:, x1:]):                     # chrome, the gap and the caption band, the safe bottom, margins
        assert (px[region] == base[region]).all()
    mask = np.ones(px.shape[:2], dtype=bool)
    for box, _art in layout.tiles:
        mask[box[1]:box[3], box[0]:box[2]] = False
    assert _not_neutral(px)[~mask].any(), "the red/green test logos were not drawn — the check is vacuous"
    assert not (_not_neutral(px) & ~_toned_mask(layout, px.shape[:2])).any()
    data = cards.render_card(cards.CardSpec("data", image=image), font_path=FONT, layout_engine="basic")
    cards._font.cache_clear()
    cards._fit_wordmark.cache_clear()
    assert cards.render_card(cards.CardSpec("data", image=image), font_path=FONT, layout_engine="basic") == data
    decoded = Image.open(io.BytesIO(data))
    decoded.load()
    assert decoded.tobytes() == img.tobytes()                            # the PNG is exactly the drawn card


def test_the_post_check_still_refuses_what_the_canvas_would_let_through(monkeypatch):
    """Defence in depth: the canvas stops the search as it measures, but the footer is not the canvas's,
    and a later placer could draw text without measuring it — the laid-out lines are checked again. With
    the page's own check disabled, a picture drawn under a raised floor is still no data card."""
    monkeypatch.setattr(look.Page, "legible", lambda self, size, what: None)
    monkeypatch.setattr(nl, "DATA_CARD_MIN_PX", 30)
    with pytest.raises(cards.CardOverflow, match="legibility floor"):
        nl.layout_data_card(_image(_rows_spec(1), _table()), font_path=FONT, layout_engine="basic")


# ── 2026-11-15: the `feature` layout — the spotlight series' art-band picture ──────────────────────────────
#
# One card: an ART BAND across its top (cards.FEATURE_BAND_*: the kicker pill top-left, the logo plate centred on a
# flat FEATURE_BAND_FILL panel — workstream C draws the per-post art into it), then the name + chip, the figure, its
# label, the headline and 1-4 fact rows. The band is a FIXED box; only the text under it steps down the ramp. What
# is pinned here: the band height was chosen by MEASUREMENT against worst-case specs; the geometry (band, pill,
# plate, text regions, margins, palette); the strings; the bytes; and that it is never a video data card.

_FEATURE_LOGOS = [{"key": k, "name": n} for k, n in (("NWHI", "Northwind Holdings International"), ("CTSO", "Contoso"),
                                                     ("NWT", "Northwind"))]
_F4_WORST_FOOTER = post_copy.image_footer(date(2027, 1, 2), "template", source=post_copy.FOOTER_SOURCE_FORM4,
                                          when="filed Dec 30, 2026–Jan 2, 2027")
_F13_WORST_FOOTER = post_copy.image_footer(date(2026, 12, 21), "template", source=post_copy.FOOTER_SOURCE_13F,
                                           when="amended Dec 20, 2026")


def _feature(**parts: Any) -> Dict[str, Any]:
    return {"layout": "feature", "version": 1, **parts}


#: WORST-CASE spotlight pictures (synthetic, longer than anything a template writes today): the widest kicker
#: (a window across a year end), a 32-character company name (CompanyRef's maximum) with a five-letter chip, a
#: two- and three-line headline, a figure that wraps, four long fact rows (a per-share cost RANGE "as filed", a
#: year-end trade window), and the two longest footers (a year-end Form 4 window; an amended 13F).
_FEATURE_WORST = {
    "insider_cluster": _feature(
        kicker="FILED DEC 30–JAN 2 · FORM 4", footer=_F4_WORST_FOOTER,
        header={"logo": "NWHI", "name": "Northwind Holdings International", "chip": "GOOGL"},
        figure="$1,234.5M", figure_label="of stock, per the Form 4 filings",
        headline="Northwind Holdings International's CEO and at least 4 other insiders disclosed buying stock",
        facts=[{"label": "Trade dates", "value": "Dec 29, 2026–Jan 2, 2027"},
               {"label": "Shares", "value": "1,234,567"},
               {"label": "Cost per share, as filed", "value": "$1,142.10–$1,143.85"},
               {"label": "Other insiders", "value": "At least 4 more"}]),
    "insider_held": _feature(
        kicker="FILED DEC 30–JAN 2 · FORM 4", footer=_F4_WORST_FOOTER,
        header={"logo": "NWHI", "name": "Northwind Holdings International", "chip": "BRK-B"},
        figure="$987.6K", figure_label="of stock, per the Form 4",
        headline="Northwind Holdings International's chief financial officer disclosed buying stock",
        facts=[{"label": "Trade dates", "value": "Dec 29, 2026–Jan 2, 2027"},
               {"label": "Shares", "value": "12,345"},
               {"label": "Cost per share, as filed", "value": "$79.98–$80.12"},
               {"label": "Held", "value": "Partly indirectly"}]),
    # the 13F spotlight's wording since the owner's feedback of 2026-11-19 (the direction first), its headlines in the
    # FULL form at 105 and 102 characters (54.6 and 52.8 ems — on either side of the picture's width bound,
    # F13S_PICTURE_HEAD_MAX_EM = 54, and wider than the cover's, 50)
    "thirteen_f_new": _feature(
        kicker="13F FILING", footer=_F13_WORST_FOOTER,
        header={"logo": "NWHI", "name": "Northwind Holdings International", "chip": "GOOGL"},
        figure="1,234.5M shares", figure_label="newly reported at the quarter's end, rounded",
        headline="Wellington Management Company Limited Partnership reported a new Northwind Holdings International "
                 "holding",
        facts=[{"label": "Quarter ended", "value": "Sep 30, 2026"},
               {"label": "Filed", "value": "Nov 13, 2026"},
               {"label": "Amended", "value": "Dec 20, 2026"},
               {"label": "Value at quarter's end", "value": "$1,234.5M"}]),
    "thirteen_f_fewer": _feature(
        kicker="13F FILING", footer=_F13_WORST_FOOTER,
        header={"logo": "NWHI", "name": "Northwind Holdings International", "chip": "GOOGL"},
        figure="−753.1M shares", figure_label="fewer than the quarter before, rounded",
        headline="Wellington Management Company Limited Partnership reduced its Northwind Holdings International "
                 "holding",
        facts=[{"label": "Latest 13F", "value": "1,234.5M shares"},
               {"label": "Quarter before", "value": "1,987.6M shares"},
               {"label": "Filed", "value": "Nov 13, 2026"},
               {"label": "Amended", "value": "Dec 20, 2026"}]),
}
#: The design's two worked examples (design_content step 10): the Contoso cluster Form 4 and the Fabrikam 13F
#: decrease — what a real post looks like. They must keep the big-number look (FEATURE_TYPICAL_MAX_STEP; the 13F one
#: FEATURE_TYPICAL_13F_MAX_STEP since the owner's feedback of 2026-11-19 — its direction-first wording and four facts).
_FEATURE_TYPICAL = {
    "contoso_cluster": _feature(
        kicker="FILED NOV 17–18 · FORM 4",
        footer=post_copy.image_footer(date(2026, 11, 19), "template", source=post_copy.FOOTER_SOURCE_FORM4,
                                      when="filed Nov 17–18, 2026"),
        header={"logo": "CTSO", "name": "Contoso", "chip": "CTSO"}, figure="$2M", figure_label="of stock, per the Form 4",
        headline="Contoso's CEO and at least 2 other insiders disclosed buying stock",
        facts=[{"label": "Trade dates", "value": "Nov 16–17, 2026"}, {"label": "Shares", "value": "14K"},
               {"label": "Cost per share, as filed", "value": "$142.10–$143.85"},
               {"label": "Other insiders", "value": "At least 2 more"}]),
    "fabrikam_13f": _feature(
        kicker="13F FILING",
        footer=post_copy.image_footer(date(2026, 11, 21), "template", source=post_copy.FOOTER_SOURCE_13F,
                                      when="filed Nov 13, 2026"),
        header={"logo": "NWT", "name": "Northwind", "chip": "NWT"}, figure="−18.1M shares",
        figure_label="fewer than the quarter before", headline="Fabrikam Capital reduced its Northwind holding",
        facts=[{"label": "Latest 13F", "value": "41.9M shares"}, {"label": "Quarter before", "value": "60M shares"},
               {"label": "Quarter ended", "value": "Sep 30, 2026"}, {"label": "Filed", "value": "Nov 13, 2026"}]),
}
#: The ceiling the other layouts' worst cases are pinned to: they fit BEFORE the floor sizes (step < RAMP_STEPS —
#: "the cover keeps room to spare", "the writer's maximum must not need the floor sizes").
FEATURE_CEILING = cards.RAMP_STEPS - 1
#: The worked examples fit at this step or below (news-v23, the look: the measured steps are 0-2 on the basic engine —
#: figure ≥ 126 px, headline ≥ 61 px, facts ≥ 28 px).
FEATURE_TYPICAL_MAX_STEP = 2
#: The 13F worked example since the owner's feedback of 2026-11-19 ("does this institute reduce or add more share?"):
#: the direction-first headline ("Fabrikam Capital reduced its Northwind holding", two lines), the signed change and
#: its label, and FOUR facts ("Latest 13F", "Quarter before", "Quarter ended", "Filed" — one row more than before;
#: the first was "Now" until the review of 2026-11-19 — a 13F shows the quarter's end, never today) take it
#: from step 6 to step 8 at FEATURE_BAND_H = 440 (figure 106 px, headline 36 px, facts 26 px; measured on the basic
#: engine). The band was NOT moved for it (it is the insider spotlight's too, and the art's crop): under the old
#: promise (step 6 for the 13F example as well) the sweep below would pick 400 px — recorded there, reported to the
#: owner.
FEATURE_TYPICAL_13F_MAX_STEP = 2
#: The step of the widest composed insider spotlight (news-v23: measured on the basic engine, 2026-11-19).
_REAL_FEATURE_MAX_STEP = 14
#: The worked examples each held to its own promise.
_TYPICAL_MAX_STEP = {"contoso_cluster": FEATURE_TYPICAL_MAX_STEP, "fabrikam_13f": FEATURE_TYPICAL_13F_MAX_STEP,
                     "isp_cluster": FEATURE_TYPICAL_MAX_STEP}


def _feature_image(spec: Dict[str, Any], files: Optional[Dict[str, Path]] = None) -> nl.TemplateImage:
    table = cards.logo_table({"logos": _FEATURE_LOGOS}, files)
    return nl.template_image_for_script({"authorship": "template", "look": cards.LOOK, "image_spec": spec, "image_footer": spec["footer"],
                                         "logos": _FEATURE_LOGOS}, table)


def _feature_steps(engine: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for name, spec in {**_FEATURE_WORST, **_FEATURE_TYPICAL}.items():
        try:
            out[name] = nl.layout_template(_feature_image(spec), font_path=FONT, layout_engine=engine).step
        except cards.CardOverflow:
            out[name] = cards.RAMP_STEPS + 1                      # does not fit at all
    return out


def test_every_feature_fixture_is_one_the_server_accepts():
    for name, spec in {**_FEATURE_WORST, **_FEATURE_TYPICAL}.items():
        assert tos.validate_image_spec(spec, tos.logo_keys(_FEATURE_LOGOS), footer=spec["footer"]) is None, name
        assert tos.validate_image_spec(_feature_spec(), tos.logo_keys(_entries()), footer=FOOTER) is None


#: The REAL composed worked example (the templates' own `isp_cluster` sample, laid out from `_SPOTLIGHT_OUTPUTS`
#: below): held to FEATURE_TYPICAL_MAX_STEP like its synthetic twin above. news-v23: the 13F spotlight is the ONE 13F
#: list (`rows`), never the `feature` picture — its single-holding samples are gone.
_REAL_TYPICAL = ("isp_cluster",)


def _real_feature_steps(engine: str) -> Dict[str, int]:
    """The step of every composed spotlight picture (`_SPOTLIGHT_OUTPUTS`: the templates' samples and the widest
    records they compose) — RAMP_STEPS + 1 when it does not fit at all."""
    out: Dict[str, int] = {}
    for name, composed in _SPOTLIGHT_OUTPUTS.items():
        try:
            out[name] = nl.layout_template(_composed_image(composed), font_path=FONT, layout_engine=engine).step
        except cards.CardOverflow:
            out[name] = cards.RAMP_STEPS + 1
    return out


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_every_worst_case_feature_fits_before_the_ceiling_and_the_worked_examples_keep_their_size(engine):
    """THE FIT-RATE TEST the band height was chosen by: every worst-case spotlight picture — the synthetic fixtures
    above AND the widest records the templates really compose (`_SPOTLIGHT_OUTPUTS`, the binding constraint) — fits
    at a ramp step at or under the ceiling the other layouts use (before the floor — never CardOverflow, never the
    floor sizes), and the worked examples, synthetic and composed, at FEATURE_TYPICAL_MAX_STEP or under. Measured
    on the basic engine at FEATURE_BAND_H = 440 (2026-11-15, re-measured 2026-11-16 with the composed records): the
    synthetic worst cases at steps 8-11, but the composed worst cases (60-character filer names in 13F headlines) at
    up to 14 — ONE step under the ceiling of 15, not four; the worked examples at 6. Re-measured 2026-11-19 (the 13F
    spotlight's direction-first wording, owner feedback): the 10- and 20-letter 13F worst cases still at 14 (their
    headlines in the short form: wider than F13S_PICTURE_HEAD_MAX_EM), the 13F worked examples at 8
    (FEATURE_TYPICAL_13F_MAX_STEP: four facts), the insider's unchanged at 6. The review of 2026-11-19 added the
    60-character filer in 16- and 18-letter words (every kind): its short-form picture sits AT the ceiling (15) —
    it still fits, before the floor sizes, but with NO step of margin. The short form cannot be shortened (the filer
    name is the subject), and no curated filer comes near it (the longest, 34 characters, at 11)."""
    steps = _feature_steps(engine)
    real = _real_feature_steps(engine)
    for name in _FEATURE_WORST:
        assert steps[name] <= FEATURE_CEILING, (name, steps[name])
    for name, step in real.items():
        assert step <= FEATURE_CEILING, (name, step)
    for name in _FEATURE_TYPICAL:
        assert steps[name] <= _TYPICAL_MAX_STEP[name], (name, steps[name])
    for name in _REAL_TYPICAL:
        assert real[name] <= _TYPICAL_MAX_STEP[name], (name, real[name])
    if engine == "basic":
        # news-v23 (the look, re-measured 2026-11-19): the worked examples at their start sizes or close (the look's
        # floors are lower and its card roomier than the old band's), the worst cases one step under the ceiling
        assert steps == {"insider_cluster": 0, "insider_held": 0, "thirteen_f_new": 6, "thirteen_f_fewer": 5,
                         "contoso_cluster": 0, "fabrikam_13f": 2}, steps
        # news-v23: the insider spotlight's composed worst cases alone (the 13F spotlight left the `feature` picture)
        assert max(real.values()) == _REAL_FEATURE_MAX_STEP == FEATURE_CEILING - 1, real
        assert {n: real[n] for n in _REAL_TYPICAL} == {"isp_cluster": 0}, real


def test_the_feature_band_height_is_a_real_bound(monkeypatch, tmp_path):
    """news-v23 (spot_insider.jpg, mock_spotlight's "overlay" mode): the art band is drawn only WITH art, edge to edge
    from the top (FEATURE_BAND_H = 432), the brand row on its navy scrim. Every worked example — synthetic and
    composed — draws its art at that height; a band of 900 px leaves the worst cases no room (they fall back to
    the plain picture, never an overflow) — the height is a real bound (anti-vacuity)."""
    art = _art_file(tmp_path)

    def art_step(spec=None, out=None):
        if out is not None:
            entries = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in out["logo_refs"]]
            script = dict(out, look=cards.LOOK, logos=entries)
            image = nl.template_image_for_script(script, cards.logo_table(script, {}), art=art)
        else:
            script = {"authorship": "template", "look": cards.LOOK, "image_spec": spec, "image_footer": spec["footer"],
                      "logos": _FEATURE_LOGOS}
            image = nl.template_image_for_script(script, cards.logo_table({"logos": _FEATURE_LOGOS}, {}), art=art)
        try:
            return nl.layout_template(image, font_path=FONT, layout_engine="basic").step
        except cards.CardOverflow:
            return None

    assert nl.FEATURE_BAND_H == 432
    assert all(art_step(spec=_FEATURE_TYPICAL[n]) is not None for n in _FEATURE_TYPICAL)
    assert all(art_step(out=_SPOTLIGHT_OUTPUTS[n]) is not None for n in _REAL_TYPICAL)
    monkeypatch.setattr(nl, "FEATURE_BAND_H", 900)
    assert any(art_step(out=_SPOTLIGHT_OUTPUTS[n]) is None for n in _SPOTLIGHT_OUTPUTS if "worst" in n)


def test_the_feature_band_geometry():
    """news-v23: the art band is the picture's full width from its top edge (spot_insider.jpg), FEATURE_BAND_H tall,
    the scrim over its top FEATURE_SCRIM_H rows reaching under the brand row; the disc (FEATURE_DISC radius, 56-92 px)
    in its gold ring sits on the band's bottom-left edge; the "AI illustration" pill at its bottom-right never reaches
    the disc or the brand row."""
    assert nl.FEATURE_BAND_H == 432 and nl.FEATURE_SCRIM_H == 250
    assert look.POST_FRAME.brand[1] + look.BRAND_TILE_PX < nl.FEATURE_SCRIM_H < nl.FEATURE_BAND_H
    lw, lh = cards.art_label_size(FONT, "basic")
    for step in range(cards.RAMP_STEPS + 1):
        r = nl.FEATURE_DISC.size(step)
        assert 48 <= r <= 92
        assert look.POST_FRAME.area[0] + 12 + 2 * r + 12 < 1080 - 40 - lw          # the label clear of the disc
    assert nl.FEATURE_BAND_H - 28 - lh > look.POST_FRAME.brand[1] + look.BRAND_TILE_PX + 20


@pytest.mark.parametrize("name", sorted(_FEATURE_WORST) + sorted(_FEATURE_TYPICAL))
def test_the_feature_layout_keeps_every_element_in_its_region(name, tmp_path):
    """No art: no band; the brand row and its kicker pill on top, the logo disc in its gold ring under them at the
    left, the name (WHITE) and its gold chip beside the disc, the headline under them, then ONE warm card — the
    figure, its label, the fact rows — over the footer; every line inside its region, the chip and the disc never
    meeting the text."""
    spec = {**_FEATURE_WORST, **_FEATURE_TYPICAL}[name]
    files = {"NWHI": _logo_png(tmp_path / "n.png", (30, 30, 200, 255)), "CTSO": _logo_png(tmp_path / "c.png", (30, 30, 200, 255))}
    for table_files in (None, files):
        layout = nl.layout_template(_feature_image(spec, table_files), font_path=FONT, layout_engine="basic")
        assert layout.kind == "template:feature" and layout.band is None and layout.art is None
        panel = layout.panel
        ((disc, art),) = layout.tiles
        assert art.key == spec["header"]["logo"] and cards._within(disc, panel) and disc[0] == panel[0] + 12
        card_rect = next(r for r in layout.rects if r.fill == look.CARD)
        assert cards._within(card_rect.box, panel) and card_rect.box[1] > disc[3]
        for ln in layout.lines:
            if ln.field in ("brand", "kicker"):
                assert ln.ink[3] <= panel[1], ln
            elif ln.field == "footer":
                assert ln.ink[1] >= panel[3] and cards._within(ln.ink, (60, 0, 1020, 1340)), ln
            elif ln.field.startswith("wordmark:"):
                assert cards._within(ln.ink, disc), ln
            elif ln.field in ("header.name", "header.chip"):
                assert ln.ink[0] > disc[2] and ln.ink[3] < card_rect.box[1], ln
            elif ln.field == "headline":
                assert ln.ink[1] > disc[1] and ln.ink[3] < card_rect.box[1], ln
            else:
                assert cards._within(ln.ink, card_rect.box), ln
        assert not layout.shapes


def test_with_no_art_the_feature_picture_draws_no_band(logo_files):
    """No art (today, and workstream C's fallback): no band is drawn — the canvas (the gradient + glow) shows above
    the card everywhere but the brand row, the disc with its gold ring, the name, the chip and the headline; the side
    margins stay the canvas; no green or red outside the disc and the toned figure (the fixture logo IS red)."""
    image = _image(_feature_spec(), _table(logo_files))
    img, layout = nl.draw_template(image, font_path=FONT, layout_engine="basic")
    px = np.asarray(img)
    base = np.asarray(look.canvas(look.POST_FRAME))
    x0, _y0, x1, _y1 = look.POST_FRAME.area
    for region in (np.s_[:, :x0], np.s_[:, x1:], np.s_[1350 - 20:]):
        assert (px[region] == base[region]).all()
    ((disc, _art),) = layout.tiles
    assert _not_neutral(px)[disc[1]:disc[3], disc[0]:disc[2]].any()
    assert not (_not_neutral(px) & ~_toned_mask(layout, px.shape[:2])).any()
    assert layout.band is None and layout.label is None


def test_the_feature_name_row_puts_the_chip_beside_a_short_name_and_under_a_long_one():
    short = nl.layout_template(_feature_image(_FEATURE_TYPICAL["contoso_cluster"]), font_path=FONT, layout_engine="basic")
    name = [ln for ln in short.lines if ln.field == "header.name"]
    chip = [ln for ln in short.lines if ln.field == "header.chip"]
    assert len(name) == 1 and len(chip) == 1 and chip[0].ink[0] > name[0].ink[2] and chip[0].ink[1] < name[0].ink[3]
    # news-v23 (spot_insider.jpg): the chip is BESIDE the name whatever its length — a long name wraps in the room
    # left of the chip, the chip beside its first line, never stacked
    long = _feature(**{**_FEATURE_WORST["insider_cluster"], "header": {"logo": "NWHI", "chip": "GOOGL",
                                                                        "name": "Northwind Holdings International"},
                       "facts": [{"label": "Shares", "value": "1"}], "headline": "A headline"})
    lay = nl.layout_template(_feature_image(long), font_path=FONT, layout_engine="basic")
    names = [ln for ln in lay.lines if ln.field == "header.name"]
    chip = [ln for ln in lay.lines if ln.field == "header.chip"][0]
    assert chip.ink[0] > max(ln.ink[2] for ln in names), (chip.ink, [ln.ink for ln in names])
    assert chip.ink[1] < names[0].ink[3] and chip.ink[3] > names[0].ink[1]
    assert {r.fill for r in lay.rects if r.box[0] <= chip.ink[0] <= r.box[2]
            and r.box[1] <= chip.ink[1] <= r.box[3]} == {look.GOLD}
    no_chip = _feature(**{**_FEATURE_TYPICAL["contoso_cluster"], "header": {"logo": "CTSO", "name": "Contoso"}})
    assert tos.validate_image_spec(no_chip, tos.logo_keys(_FEATURE_LOGOS), footer=no_chip["footer"]) is None
    lay = nl.layout_template(_feature_image(no_chip), font_path=FONT, layout_engine="basic")
    assert not [ln for ln in lay.lines if ln.field == "header.chip"]
    assert not [r for r in lay.rects if r.fill == look.GOLD]


def test_a_wide_fact_value_stacks_under_its_label_and_one_fact_is_enough():
    """A value wider than RIGHT_SHARE of the column is stacked under its label (never squeezed); a picture of ONE
    fact row (MIN_FACTS) lays out; the rows are drawn in order, each under a faint DIVIDER rule."""
    spec = _feature(**{**_FEATURE_TYPICAL["contoso_cluster"],
                       "facts": [{"label": "Cost per share, as filed",
                                  "value": "$1,142.10 to $1,143.85 per share across two filings"}]})
    assert tos.validate_image_spec(spec, tos.logo_keys(_FEATURE_LOGOS), footer=spec["footer"]) is None
    lay = nl.layout_template(_feature_image(spec), font_path=FONT, layout_engine="basic")
    label = [ln for ln in lay.lines if ln.field == "facts[0].label"]
    value = [ln for ln in lay.lines if ln.field == "facts[0].value"]
    assert value and min(v.ink[1] for v in value) > max(l.ink[3] for l in label)          # stacked
    rules = [r for r in lay.rects if r.fill == look.FACT_DIVIDER]          # mock_spotlight.DIVIDER
    assert len(rules) == 1 and rules[0].box[3] - rules[0].box[1] == 2
    assert rules[0].box[3] <= min(l.ink[1] for l in label)


def test_text_that_cannot_fit_a_feature_is_card_overflow_never_clipped():
    spec = _feature_spec()
    spec["headline"] = " ".join(["Incomprehensibilities"] * 26)
    with pytest.raises(cards.CardOverflow, match="feature"):
        nl.render_template_image(_image(spec, _table()), font_path=FONT, layout_engine="basic")
    spec = _feature_spec()
    spec["kicker"] = "W" * 60                                           # a pill wider than the band at every step
    with pytest.raises(cards.CardOverflow, match="feature"):
        nl.layout_template(_image(spec, _table()), font_path=FONT, layout_engine="basic")


def test_the_feature_picture_is_never_a_video_data_card(logo_files, caplog):
    """Owner decision 5 (2026-11-15): the filing's cost per share — on this picture — never reaches a video. The
    worker refuses to lay a `feature` picture out as a data card (CardOverflow before anything is measured), so a
    data card frozen against one (a web that sent a slot anyway) keeps its text card with a WARNING; the server
    sends none (template_onscreen.DATA_CARD_LAYOUTS, tests/test_marketing_template_onscreen.py)."""
    image = _image(_feature_spec(), _table(logo_files))
    with pytest.raises(cards.CardOverflow, match="never drawn as a data card"):
        nl.layout_data_card(image, font_path=FONT, layout_engine="basic")
    with pytest.raises(cards.CardOverflow):
        cards.render_card(cards.CardSpec("data", image=image), font_path=FONT, layout_engine="basic")
    script = {"authorship": "template", "video_layout": "per_line", "image_spec": _feature_spec(),
              "image_footer": FOOTER, "logos": _entries(), "disclaimer_card": "Educational only.",
              "opening_card": {"kicker": "FORM 4", "logos": ["GME"], "headline": "GameStop's CEO disclosed buying stock"},
              "video_script": ["One.", "Two."], "cards": [{"title": "The purchase", "body": "One purchase."},
                                                          {"title": "About Form 4", "body": "A filing."}],
              "video_features": ["data_card"], "data_card_slot": 0}
    with caplog.at_level(logging.WARNING, logger="marketing.cards"):
        specs = cards.cards_for_script(script, _table(logo_files), font_path=FONT, layout_engine="basic")
    assert [s.kind for s in specs] == ["opening", "text", "text", "disclaimer"]
    assert any("no data card" in r.getMessage() and "never drawn as a data card" in r.getMessage()
               for r in caplog.records)
    # anti-vacuity: the same script with a `spotlight` picture DOES draw its data card
    script["image_spec"] = _spotlight_spec()
    specs = cards.cards_for_script(script, _table(logo_files), font_path=FONT, layout_engine="basic")
    assert [s.kind for s in specs] == ["opening", "data", "text", "disclaimer"]


#: news-v23 (cards/v6, the approved look): every template picture of this file and its data card, wordmarks and drawn
#: logos (Pillow 12.0.0, basic engine) — re-pinned when the look changed every pixel; determinism and "nothing moves
#: unless a version is bumped" are what they keep.
#: Fidelity review 2026-11-20: the feature re-pinned (its figure block, label baseline, solid ring, head ladder).
#: Visual-fidelity round 2 (2026-11-20): re-pinned — without art the block is balanced on the page (it rested on the
#: footer); nothing else in it moved.
_FEATURE_GOLDEN = {
    "feature": "a2f9f4c0f0e1b4c3107ff44cee349fc2e74e2c436010608017e09fffde332fed",
}
#: Fidelity review 2026-11-20: the rows / spotlight / bars pictures and data cards re-pinned (chips, rows block,
#: Money Map levels, wordmark sizes); the grid and pair data cards did not move.
#: Content round 2026-11-20 (the two-tone split at a sentence or clause boundary, else one block with its last line
#: gold): the samples whose headline has no gold tail moved — the spotlight picture (both modes) and the rows_1 /
#: rows_3 / rows_4_no_header / spotlight data cards; nothing else did.
#: Owner decisions 2026-11-20 (form4_roundup5.jpg: "3 - 5 companies at once"): a generic `rows` picture with a section
#: of ROWS_THREE_LINE_ROWS rows or more (now the Form 4 roundup's) sets its headline in three lines and its rows 14 px
#: apart — the rows picture (both modes) and the rows_4_no_header and rows_5 data cards re-pinned; nothing else moved
#: (rows_1 and rows_3 did not).
_PRE_FEATURE_GOLDEN = {
    "data:bars": "635267a0808af4b8b50b5d6c191cb5105a6c816cbd8668c489b4e7f3d532efc4",
    # news-v23 phase 3 (2026-11-20): the grid data cards re-pinned with the theme grid's new placer (the other
    # pictures did not move); cards/v6 is not shipped yet, so no version bump
    "data:grid_12": "cf3d32ff76e3a441ff25ea45b6d88554f162dcebe6b775b87ef61f6151a25f90",
    "data:grid_4": "2f2f6ac5f78ae7a32c2c3b1ef13224123d5f496d76c4154f3311c3a99dde120e",
    "data:grid_6_no_lines": "0b7b05e73b03c8a30249c6f1c0d7ea38639f810ff72da51b748cbf6239c347be",
    "data:grid_8": "90b2208c71f02223eeea92f90536a10885849f785ffac2a596d5e4b8c309515b",
    "data:pair": "0788138721a45ed4f15557b526465d1df995d3cb708f1de2db891cb754f13744",
    "data:rows_1": "450e5a1f4aa0e13a16113285da45399f043ab456ecb983bdeff0c4bd25422b32",
    "data:rows_3": "97dd287a09eff3c47227d5457cf2f3e0983de1052e3f0d5cf79db81f339a3c89",
    "data:rows_4_no_header": "d32c3aef67548a71868bce75cd04832e6c4e039a1511a31e474e9083ab50d67e",
    "data:rows_5": "caa7a2ad98bfed5635a156cd46c71dd4bf267fd46b8d642fcc87f14830c97556",
    "data:spotlight": "0d9c5ba3d75d1c3be0f53867e865cdc687b28b6e31b2e784c433af16c98efcb1",
    "post:bars:logos": "cb5da843b9c31c85756293fa348216d4a22a88b021b137c1746fb01d327e86ef",
    "post:bars:wm": "c21a3a4379d37813a1d75383db27935beaa6165be754113ab9054052e9da2abd",
    "post:rows:logos": "f9fa441be8f4b757b58ccbbbc3f0da7f21fbb3bf9afea0f42a5368c2086838f3",
    "post:rows:wm": "346cc9f6842a7ce9c41f5f6d65527226bb0237a33fa483a088d09390cbe39261",
    "post:spotlight:logos": "0f4174c6221481bae354d8d0554bf329f6471f0f3b6b4e3e6e754ef020cd2b69",
    "post:spotlight:wm": "ad6d0c440597b5fed1a190b9f5c2b91ce968ebb0c24527711c6a3abba0aed55a",
}


@pytest.mark.skipif(PIL.__version__ != "12.0.0", reason="the golden bytes were measured on Pillow 12.0.0")
def test_the_no_art_feature_picture_is_pinned():
    import hashlib

    got = {name: hashlib.sha256(nl.render_template_image(_image(SPECS[name](), _table()), font_path=FONT,
                                                         layout_engine="basic").data).hexdigest()
           for name in sorted(_FEATURE_GOLDEN)}
    assert got == _FEATURE_GOLDEN


@pytest.mark.skipif(PIL.__version__ != "12.0.0", reason="the golden bytes were measured on Pillow 12.0.0")
def test_no_existing_template_picture_or_data_card_moved_when_the_feature_layout_arrived(logo_files):
    """The other pictures and every legible data card, pinned in the look (wordmarks and drawn logos)."""
    import hashlib

    def h(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    got: Dict[str, str] = {}
    for name in ("bars", "rows", "spotlight"):
        for mode, table in (("wm", _table()), ("logos", _table(logo_files))):
            got[f"post:{name}:{mode}"] = h(nl.render_template_image(_image(SPECS[name](), table), font_path=FONT,
                                                                    layout_engine="basic").data)
    for name, (make, _step) in sorted(_LEGIBLE.items()):
        got[f"data:{name}"] = h(cards.render_card(cards.CardSpec("data", image=_image(make(), _table(logo_files))),
                                                  font_path=FONT, layout_engine="basic"))
    assert got == _PRE_FEATURE_GOLDEN


# ── news-v22 (B4, 2026-11-15): the REAL spotlight pictures and covers the templates compose ─────────────────────
#
# The fixtures above were synthetic (written before the templates). Here every spotlight sample of the templates'
# own suite, and the widest records the templates can compose (32-character company and 60-character filer names
# — CompanyRef's and the filer name's maxima — in the widest glyphs, with ten-letter words and with the longest
# drawable 20-letter words; a cluster of four; a cost range in thousands and one of six-digit costs filed to four
# places, printed as filed; a window across the year end; an amended 13F with a new holding's filed value and a
# rounded "About …" count), are laid out by the worker's own code: the picture at a step under the ceiling the
# other layouts keep (never the floor sizes, never CardOverflow), the cover (the opening card) at any step of its
# ramp.

_W32 = "Wwwwwwwwww Wwwwwwwwww Wwwwwwwwww"                   # 32 characters (CompanyRef's maximum), the widest glyph
_W60_FILER = "Wwwwwwwww Wwwwwwwww Wwwwwwwww Wwwwwwwww Wwwwwwwww Wwwwwwwwww"    # 60 (the filer name's maximum)
#: The same maxima in the LONGEST words a name may hold (F13_MAX_WORD_CHARS / the company-name word bound, 20).
_W32_WORDS = "Wwwwwwwwwwwwwwwwwwww Wwwwwwwwwww"                   # 20 + 1 + 11 = 32
_W60_FILER_WORDS = "Wwwwwwwwwwwwwwwwwwww Wwwwwwwwwwwwwwwwwwww Wwwwwwwwwwwwwwwwww"   # 20 + 1 + 20 + 1 + 18 = 60
#: The same 60 characters in 16- and 18-letter words (review 2026-11-19): the widths at which a filer name too wide
#: for the full headline wraps its SHORT form worst on the picture — AT the ceiling (`_w_name(60, 16 / 18)`).
_W60_FILER_16 = "Wwwwwwwwwwwwwwww Wwwwwwwwwwwwwwww Wwwwwwwwwwwwwwww Wwwwwwwww"           # 16 + 1 + 16 + 1 + 16 + 1 + 9
_W60_FILER_18 = "Wwwwwwwwwwwwwwwwww Wwwwwwwwwwwwwwwwww Wwwwwwwwwwwwwwwwww Www"           # 18 + 1 + 18 + 1 + 18 + 1 + 3


#: Owner decisions 2026-11-20 ("Drop it from insider posts", "3 - 5 companies at once"): no series composes a
#: `feature` picture or a one-company Form 4 `spotlight` any more. The worker keeps both layouts (the feature layout with
#: post_art, for a future art series; the spotlight for an older web during a worker-first deploy), so their composed
#: cases are FROZEN as the last composer that wrote them (news-v23) composed them —
#: tests/data/frozen_layout_outputs_news_v23.json: "feature" (the insider spotlight's four samples and four
#: widest records: 32-character names in the widest glyphs and in 20-letter words, a cluster of four, a cost range in
#: thousands and six-digit costs filed to four places, a window across the year end) and "spotlight" (a one-company
#: CEO week and a late insider's). Deliberately NOT named `marketing_*.json`: that glob is the judge's calibration corpus
#: (tests/test_marketing_judge.py::test_no_rubric_exemplar_appears_in_a_calibration_list), and these are layout inputs.
_FROZEN = json.loads((_BACKEND / "tests" / "data" / "frozen_layout_outputs_news_v23.json").read_text())


def _frozen_output(kind: str, name: str) -> Dict[str, Any]:
    return json.loads(json.dumps(_FROZEN[kind][name]))


def _frozen_layout(kind: str, name: str, engine: str = "basic"):
    image = _composed_image(_frozen_output(kind, name))
    return image, nl.layout_template(image, font_path=FONT, layout_engine=engine)


def _spotlight_outputs() -> Dict[str, Dict[str, Any]]:
    """The frozen `feature` outputs (`_FROZEN`), by name."""
    return {n: _frozen_output("feature", n) for n in _FROZEN["feature"]}


_SPOTLIGHT_OUTPUTS = _spotlight_outputs()


def _composed_image(out: Dict[str, Any]) -> nl.TemplateImage:
    """A composed output's picture as the worker builds it (every logo a wordmark: no logo file)."""
    entries = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None, "width": None,
                "height": None} for r in out["logo_refs"]]
    script = dict(out, look=cards.LOOK, logos=entries)
    return nl.template_image_for_script(script, cards.logo_table(script, {}))


def test_the_spotlight_samples_and_worst_cases_compose():
    """Anti-vacuity: the four frozen insider spotlight samples and the four widest records (`_FROZEN`), and the widest
    records really are the maxima they claim (review 2026-11-16: the names were 30 and 59 characters). news-v23: the 13F
    spotlight is the ONE 13F list — its worst cases are laid out in the 13F list section. Owner decisions 2026-11-20:
    no composed sample is a `feature` picture any more (the insider spotlight is a `rows` list)."""
    import test_marketing_news_templates as NT

    assert len(_SPOTLIGHT_OUTPUTS) == 8 and _FROZEN["template_version"] == "news-v23"
    assert all(o["image_spec"]["layout"] == "feature" for o in _SPOTLIGHT_OUTPUTS.values())
    assert all(o["image_spec"]["layout"] == "spotlight" for o in _FROZEN["spotlight"].values())
    assert not [n for n, f, d in NT.SAMPLES if _compose(f(), d)["image_spec"]["layout"] in ("feature", "spotlight")]
    assert all(o["series"] == "insider_spotlight" for o in _SPOTLIGHT_OUTPUTS.values())
    assert len(_W32) == len(_W32_WORDS) == 32 and len(_W60_FILER) == len(_W60_FILER_WORDS) == 60
    assert max(len(w) for w in (_W32_WORDS + " " + _W60_FILER_WORDS).split()) == 20
    assert (_W60_FILER_16, _W60_FILER_18) == (_w_name(60, 16), _w_name(60, 18))
    worst = _SPOTLIGHT_OUTPUTS["isp_worst_cluster"]["image_spec"]
    assert worst["headline"] == f"A director of {_W32} and at least 4 other insiders disclosed buying stock"
    assert {"label": "Cost a share, as filed", "value": "$1,142.10–$1,143.85"} in worst["facts"]
    assert {"label": "Cost a share, as filed", "value": "$712,345.1234–$719,876.5432"} in (
        _SPOTLIGHT_OUTPUTS["isp_worst_cost"]["image_spec"]["facts"])


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("name", sorted(_SPOTLIGHT_OUTPUTS))
def test_every_composed_spotlight_picture_and_cover_fits_the_worker(engine, name):
    out = _SPOTLIGHT_OUTPUTS[name]
    entries = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None, "width": None,
                "height": None} for r in out["logo_refs"]]
    table = cards.logo_table(dict(out, look=cards.LOOK, logos=entries), {})
    layout = nl.layout_template(_composed_image(out), font_path=FONT, layout_engine=engine)
    assert layout.kind == "template:feature" and layout.step <= FEATURE_CEILING, (name, layout.step)
    opening = cards.layout_card(cards.opening_spec(out["opening_card"], table), font_path=FONT, layout_engine=engine)
    assert opening.step <= cards.RAMP_STEPS, (name, opening.step)
    # the cost per share is on the picture (its facts) — never on the cover
    if out["series"] == "insider_spotlight":
        assert any(ln.field.startswith("facts[") and "$" in ln.text for ln in layout.lines)
        assert "as filed" not in json.dumps(out["opening_card"])


def _w_name(n: int, word: int) -> str:
    """An n-character name of title-case "W" words of `word` letters (the widest glyphs a name draws), the last word
    shorter; never a trailing space, never a word over `word` letters."""
    s = " ".join(["W" + "w" * (word - 1)] * 40)[:n]
    return s[:-2] + " W" if s.endswith(" ") else s


# ── news-v23: the ONE 13F list — f13_list_berkshire.jpg / f13_list_pershing.jpg / cover_13f_berkshire.jpg ────────────
#
# Both 13F series draw one ranked list (owner 2026-11-19: "i need more companies, similar with Berkshire hathaway latest
# 13F" / "the range from 2 to 6 are okay"): the two-tone title, the filer's plate (its logo, its gold ticker chip) only
# for a listed filer, the grey column label "Change in shares" over the figure column, one warm card per row — the logo
# disc, the ticker chip GREEN for an increase / a new holding and RED for a decrease / a holding closed out, the name,
# the grey status line, the signed change right-aligned in the same tone — and "+N more changes in the caption". The
# cover (the opening card's `list`) is its first four rows on the 9:16 frame, above the burned-caption band. Laid out
# by the worker's own code at the widest records the templates compose — a 60-character filer in 10- to 20-letter words
# (and its ".of" form, and a listed filer's plate), 32-character holdings, the widest rounded nine-digit figures and
# status lines, eight holdings (six rows and "+2 more"), an amended footer — at a step under the ceiling the other
# layouts keep, never the floor sizes, never CardOverflow.

_F13L_RUN = date(2026, 12, 21)
#: The 13F list's ceiling: the floor step itself (every size at its legible floor) — measured, `_F13L_MAX_STEP`.
_F13L_CEILING = cards.RAMP_STEPS


def _f13l_record(filer: str, names, *, n: int = 8, symbol: Optional[str] = None):
    """A 13F spotlight list of `n` holdings named `names` (the kinds cycling — fewer, more, new, closed out — each at its
    widest figure: rounded nine-digit counts), for `filer` (listed as `symbol`), amended; 31 changes in the filing."""
    from app.services.marketing import company_news_rules as R

    big, small = 987_654_321.0, 654_321_987.0
    kinds = [("decreased", dict(shares=small, prev_shares=big)), ("increased", dict(shares=big, prev_shares=small)),
             ("newly_reported", dict(shares=big, prev_shares=0.0)),
             ("no_longer_reported", dict(shares=0.0, prev_shares=big))]
    moves = []
    for i in range(n):
        kind, kw = kinds[i % 4]
        value = ({"value_usd": None, "prev_value_usd": 9e11 - i * 1e10} if kind == "no_longer_reported"
                 else {"value_usd": 9e11 - i * 1e10})
        moves.append(R.ThirteenFMove(company=R.CompanyRef(f"{'WMQKVXYZ'[i]}WWA", names[i]), move=kind,
                                     listed_on=None, **{**kw, **value}))
    moves.sort(key=R.thirteen_f_spotlight_order)
    return R.ThirteenFSpotlight(series="thirteen_f_spotlight", filer_name=filer, filer_cik="0009999909",
                                filer_symbol=symbol, period="2026-Q3", period_end=date(2026, 9, 30),
                                filed_on=date(2026, 11, 13), amended_on=date(2026, 12, 20), moves=tuple(moves),
                                eligible_moves=len(moves), filing_moves=31)


def _w_names(word: int, n: int = 8):
    """`n` distinct 32-character holding names of `word`-letter words (the last letter tells them apart)."""
    return [_w_name(32, word)[:-1] + "WMNHKXYZ"[i] for i in range(n)]


def _f13l_outputs() -> Dict[str, Dict[str, Any]]:
    from app.services.marketing import news_templates as T
    import test_marketing_news_templates as NT

    cases = {n: (f(), d) for n, f, d in NT.SAMPLES if n.startswith("f13s_") or n.endswith("_13f")}
    cases["f13s_list8"] = (NT.f13s_list8(), NT.SPOT_SAT)
    for fw in (10, 16, 18, 20):
        for cw in (10, 20):
            for n in (8, 6, 2):
                cases[f"list_worst_f{fw}_c{cw}_n{n}"] = (_f13l_record(_w_name(60, fw), _w_names(cw), n=n), _F13L_RUN)
    cases["list_worst_of"] = (_f13l_record(_w_name(58, 10) + "'s", _w_names(20)), _F13L_RUN)
    cases["list_worst_listed"] = (_f13l_record(_w_name(60, 16), _w_names(20), symbol="WWWWB"), _F13L_RUN)
    # the main lane's roundup at its widest (`_worst_13f`: 60-character filer, eight holdings, amended), both forms
    names = _worst_names("basic")
    for sym in (None, "BRK-B"):
        cases[f"roundup_worst_{sym or 'unlisted'}"] = (_worst_13f(_w_name(60, 16), sym, names), _F13_RUN)
    return {n: T.compose(rec, run_date=d, store_state="live", allow_x_url=False) for n, (rec, d) in cases.items()}


_F13L_OUTPUTS = _f13l_outputs()


def _f13l_script(out: Dict[str, Any]) -> Dict[str, Any]:
    entries = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None, "width": None,
                "height": None} for r in out["logo_refs"]]
    return dict(out, look=cards.LOOK, logos=entries)


def _f13l_cover(out: Dict[str, Any], engine: str = "basic"):
    """(the cover CardSpec `cards_for_script` builds, its layout)."""
    specs = cards.cards_for_script(_f13l_script(out), font_path=FONT, layout_engine=engine)
    return specs[0], cards.layout_card(specs[0], font_path=FONT, layout_engine=engine)


def test_the_13f_list_worst_cases_compose_at_their_maxima():
    """Anti-vacuity: the widest records really are the maxima (60-character filers, 32-character holdings, eight
    holdings → six rows and "+2 more", the cover three and "+5 more" — review 2026-11-20), every 13F output is the ONE
    list."""
    outs = _F13L_OUTPUTS
    worst = outs["list_worst_f20_c20_n8"]
    sec = worst["image_spec"]["sections"][0]
    assert len(sec["rows"]) == 6 and sec["more"] == "+2 more changes in the caption"
    assert worst["opening_card"]["list"]["more"] == "+5 more changes in the caption"
    assert all(len(r["cells"][0]) == 32 for r in sec["rows"]) and len(_w_name(60, 20)) == 60
    assert outs["list_worst_listed"]["image_spec"]["header"]["chip"] == "WWWWB"
    assert outs["list_worst_of"]["image_spec"]["title"].startswith("The latest 13F of ")
    assert {len(o["image_spec"]["sections"][0]["rows"]) for o in outs.values()} >= {2, 3, 5, 6}
    for name, o in outs.items():
        spec = o["image_spec"]
        assert spec["layout"] == "rows" and spec["kicker"] == "13F FILING" and "subtitle" not in spec, name
        assert len(spec["sections"]) == 1 and spec["sections"][0]["heading"] == "Change in shares", name


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("name", sorted(_F13L_OUTPUTS))
def test_every_composed_13f_list_picture_and_cover_fits_the_worker(engine, name):
    """The picture lays out whole at a step under the ceiling, drawing exactly its strings — every row's figure right
    of its name — and the cover is the LIST (never today's plain card) at a step under the ceiling, above the
    burned-caption band, its plate drawn only for a listed filer."""
    out = _F13L_OUTPUTS[name]
    image = _composed_image(out)
    assert nl._is_f13_list(image)
    layout = nl.layout_template(image, font_path=FONT, layout_engine=engine)
    assert layout.kind == "template:rows" and layout.step <= _F13L_CEILING, (name, layout.step)
    # the plate's pill draws the filer's TICKER; its name is the title's (f13_list_berkshire.jpg, the Money Map's rule)
    _assert_draws_exactly_its_strings(image, layout, undrawn=("header.name",) if "header" in image.spec else ())
    figures = [ln.text for ln in layout.lines if ln.field.endswith(".cells[2]")]
    assert figures == [r["cells"][2] for r in out["image_spec"]["sections"][0]["rows"]], name
    spec, cover = _f13l_cover(out, engine)
    assert spec.image is not None, name                                       # the list cover, not the fallback
    assert cover.step <= _F13L_CEILING, (name, cover.step)
    content = [ln for ln in cover.lines if ln.field not in ("brand", "kicker", "footer")
               and not look.is_logo_standin(ln.field)]
    assert max(ln.ink[3] for ln in content) <= cards.CAPTION_BAND_Y[0], name   # the hook's captions burn below
    rows = [ln for ln in cover.lines if ln.field.endswith(".cells[2]")]
    assert len(rows) == len(out["opening_card"]["list"]["rows"]) <= 4
    plates = len(cover.discs) - len(rows)
    assert plates == (1 if out["opening_card"].get("chip") else 0), name


#: The measured steps (basic engine, 2026-11-19): the widest pictures and covers, and the real samples.
#: fidelity review 2026-11-20: the picture's own 72 px head ladder and the five-row metrics (mock_f13_lists) put every
#: real sample's picture at step 0 — Pershing too (it stepped to 3 at 80 px).
_F13L_MAX_STEP = {"picture": 16, "cover": 16, "samples_picture": 0, "samples_cover": 4}


def test_the_13f_list_steps_are_the_measured_ones():
    """The measured margin (basic engine). The widest records — a 60-character filer in W-only words, the widest glyph,
    upper-cased in the three-line title — fit only at the FLOOR step (16: every size at its legible floor; the
    title steps the whole page, as on every `rows` picture), never an overflow; the real samples stay near their
    start sizes (Berkshire's five rows at 0, the longest real filer, Pershing Square Capital Management, at 0 / 4)."""
    pic = {n: nl.layout_template(_composed_image(o), font_path=FONT, layout_engine="basic").step
           for n, o in _F13L_OUTPUTS.items()}
    cov = {n: _f13l_cover(o)[1].step for n, o in _F13L_OUTPUTS.items()}
    assert max(pic.values()) == _F13L_MAX_STEP["picture"] <= _F13L_CEILING, pic
    assert max(cov.values()) == _F13L_MAX_STEP["cover"] <= _F13L_CEILING, cov
    samples = [n for n in pic if not n.startswith(("list_worst", "roundup_worst"))]
    assert max(pic[n] for n in samples) == _F13L_MAX_STEP["samples_picture"], {n: pic[n] for n in samples}
    assert max(cov[n] for n in samples) == _F13L_MAX_STEP["samples_cover"], {n: cov[n] for n in samples}


def _f13l_layout(name: str):
    out = _F13L_OUTPUTS[name]
    image = _composed_image(out)
    return out, image, nl.layout_template(image, font_path=FONT, layout_engine="basic")


def test_the_13f_list_draws_the_mocks_parts_in_their_colours():
    """f13_list_berkshire.jpg: the chip of each row in its direction's colour (GREEN new / increased, RED reduced /
    closed out) with WHITE ink; the signed figure in the same colour, right-aligned; the name INK, the status INK2; the
    column label GREY, right-aligned on the figure column; the filer's plate (a disc in a gold ring, the GOLD chip)
    beside the title. Mutation-checked by hand (2026-11-19): with the chip filled NAVY (the Form 4 roundup's) this
    test went red."""
    out, image, layout = _f13l_layout("berkshire_13f")
    rows = out["image_spec"]["sections"][0]["rows"]
    for j, row in enumerate(rows):
        tone = look.GREEN if row["cells"][2].startswith("+") else look.RED
        fig = [ln for ln in layout.lines if ln.field == f"sections[0].rows[{j}].cells[2]"]
        chip_ln = [ln for ln in layout.lines if ln.field == f"sections[0].rows[{j}].chip"]
        name = [ln for ln in layout.lines if ln.field == f"sections[0].rows[{j}].cells[0]"]
        status = [ln for ln in layout.lines if ln.field == f"sections[0].rows[{j}].cells[1]"]
        assert fig[0].fill == tone and chip_ln[0].fill == look.WHITE and chip_ln[0].text == row["chip"]
        assert name[0].fill == look.INK and status[0].fill == look.INK2 and status[0].text == row["cells"][1]
        pill = [r for r in layout.rects if r.fill == tone and _within(chip_ln[0].ink, r.box)]
        assert pill, (j, row["chip"])                                              # the chip's own pill, toned
        assert name[0].ink[1] > chip_ln[0].ink[3] - 2                              # the chip sits above the name
    label = [ln for ln in layout.lines if ln.field == "sections[0].heading"]
    figs = [ln for ln in layout.lines if ln.field.endswith(".cells[2]")]
    assert label[0].text == "Change in shares" and label[0].fill == look.GREY
    assert abs(label[0].ink[2] - max(f.ink[2] for f in figs)) <= 6                 # over the figure column
    header_chip = [ln for ln in layout.lines if ln.field == "header.chip"]
    assert header_chip[0].text == "BRK-B" and header_chip[0].fill == look.INK
    assert any(d.ring for d in layout.discs)                                       # the plate's gold ring
    assert "more" not in out["image_spec"]["sections"][0]
    # an unlisted filer draws no plate: every disc is a row's
    out2, _image2, layout2 = _f13l_layout("pershing_13f")
    assert not any(d.ring for d in layout2.discs) and len(layout2.discs) == 3
    assert not [ln for ln in layout2.lines if ln.field.startswith("header")]


def test_final_a_13f_filer_word_past_the_bound_overflows_the_picture_and_the_cover(monkeypatch):
    """Anti-vacuity for the template's F13_MAX_WORD_CHARS (final review 2026-11-20 —
    `test_marketing_news_templates.test_final_a_13f_name_word_too_long_to_draw_whole_is_refused`): the bound MATTERS.
    With it lifted, a filer whose name holds a 30-letter word of the widest glyph composes — and its picture and its
    list cover cannot be drawn (CardOverflow: at render, the day skipped after the build was accepted). The bound (20)
    is conservative: a 20-letter word of the same glyph still lays out on both."""
    import test_marketing_news_templates as NT
    from app.services.marketing import news_templates as T

    monkeypatch.setattr(T, "F13_MAX_WORD_CHARS", 60)                       # the template's bound lifted

    def surfaces(n: int):
        out = NT.compose(NT.berkshire(filer_name=f"{'W' * n} Capital"), NT.TUE)
        image, table = _f13_image(out)
        script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                    "bytes": None, "width": None, "height": None}
                                                   for r in out["logo_refs"]])
        return (lambda: nl.layout_template(image, font_path=FONT, layout_engine="basic"),
                lambda: cards.layout_card(cards.cards_for_script(script, table)[0], font_path=FONT,
                                          layout_engine="basic"))

    for draw in surfaces(30):
        with pytest.raises(cards.CardOverflow):
            draw()
    for draw in surfaces(20):
        draw()                                                             # inside the real bound: both lay out


def test_the_13f_list_with_more_than_six_draws_the_more_line_under_the_rows():
    out, _image, layout = _f13l_layout("f13s_list8")
    more = [ln for ln in layout.lines if ln.field == "sections[0].more"]
    last_card = max(r.box[3] for r in layout.rects if r.fill == look.CARD)
    assert more[0].text == "+2 more changes in the caption" and more[0].fill == look.GREY
    assert more[0].ink[1] > last_card and more[0].ink[3] < layout.footer_top
    assert abs((more[0].ink[0] + more[0].ink[2]) / 2 - 540) <= 2                   # centred


def test_a_list_cover_that_cannot_be_drawn_is_todays_opening_card_with_a_warning(monkeypatch, caplog):
    """`cards._cover_or_plain`: a 13F list cover the worker cannot lay out (forced here) is replaced by today's plain
    opening card — the kicker, the logo, the chip, the headline: a subset of the server's allow-list — with a WARNING;
    never CardOverflow, never a skipped day."""
    out = _F13L_OUTPUTS["berkshire_13f"]

    def no_fit(*_a, **_k):
        raise nl._NoFit("forced")

    monkeypatch.setattr(nl, "_lay_f13_list", no_fit)
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    specs = cards.cards_for_script(_f13l_script(out), font_path=FONT, layout_engine="basic")
    assert specs[0].kind == "opening" and specs[0].image is None
    assert "the 13F list cover cannot be drawn" in caplog.text
    allowed = set(tos.opening_strings(out["opening_card"], out["logo_refs"])) | {out["image_footer"],
                                                                                 cards.BRAND_ROW_WORD}
    assert set(cards.onscreen_strings(specs[0])) <= allowed
    rows = [c for r in out["opening_card"]["list"]["rows"] for c in r["cells"]]
    assert rows and not set(rows) & set(cards.onscreen_strings(specs[0]))         # no row of the list is drawn
    cards.layout_card(specs[0], font_path=FONT, layout_engine="basic")


def test_the_list_cover_declares_only_what_the_server_allows_and_never_draws_art(tmp_path):
    """The list cover's strings (`onscreen_strings`: its picture's — kicker, the plate's name and chip, the title, the
    label, the rows, the "+N more" line, the short footer — and the brand row's word) are all on the server's allow-list
    for the video (`opening_strings` + the run's footer + VIDEO_BRAND_TEXT); a list cover never draws per-post art."""
    for name in ("berkshire_13f", "f13s_list8", "list_worst_listed"):
        out = _F13L_OUTPUTS[name]
        spec, _layout = _f13l_cover(out)
        allowed = set(tos.opening_strings(out["opening_card"], out["logo_refs"])) | {out["image_footer"],
                                                                                     cards.BRAND_ROW_WORD}
        assert set(cards.onscreen_strings(spec)) <= allowed, name
        assert out["opening_card"]["list"]["heading"] in cards.onscreen_strings(spec)
    script = _f13l_script(_F13L_OUTPUTS["berkshire_13f"])
    report: Dict[str, Any] = {}
    specs = cards.cards_for_script(script, font_path=FONT, layout_engine="basic", art=_art_file(tmp_path),
                                   report=report)
    assert specs[0].art is None and report.get("opening_art_skipped") == "two_companies"


def test_a_news_v22_13f_roundup_spec_keeps_the_generic_rows_layout():
    """An older web (worker-first deploy) sends the news-v22 roundup — several kind sections under a counts subtitle:
    it is not the list (`_is_f13_list`), so the generic rows layout draws it, its headings toned (HEADING_TONES)."""
    spec = {"layout": "rows", "version": 1, "kicker": "13F SEASON", "footer": FOOTER, "title": "A filer's latest 13F",
            "subtitle": "1 newly reported · 1 no longer reported",
            "sections": [{"heading": "Newly reported", "rows": [{"logo": "GME", "cells": ["GameStop", "$1.2B"]}]},
                         {"heading": "No longer reported", "rows": [{"logo": "NVDA", "cells": ["NVIDIA"]}]}]}
    image = nl.template_image_for_script({"authorship": "template", "look": cards.LOOK, "image_spec": spec,
                                          "image_footer": FOOTER, "logos": _entries(), "series": "thirteen_f"},
                                         _table())
    assert not nl._is_f13_list(image)
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert [ln.text for ln in layout.lines if ln.field == "subtitle"]
    fig = [ln for ln in layout.lines if ln.field == "sections[0].rows[0].cells[1]"]
    assert fig[0].fill == look.GREEN


# ── workstream C (2026-11-16): the per-post ART — the worker's download (marketing/art.py) and the `feature` band ──
#
# The web stores only a picture that passed its check, content-addressed under `art/<sha[:32]>.jpg`, and the read-back
# carries {url, sha256, bytes, width, height} while MARKETING_ART_ENABLED is on. Pinned here: the worker fetches ONLY
# that shape on the run's own bucket origin, checks the sha, the size and the decode (its own 2752×1548 bomb guard),
# never raises (None = today's drawing); its bounds mirror post_art's; the `feature` picture draws the art in its band
# (plate bottom-left, the code-owned label bottom-right, nothing else moves), falls back to TODAY'S bytes when the art
# cannot be drawn, and no other layout ever draws art.

import hashlib  # noqa: E402

from app.services.marketing import post_art  # noqa: E402
from marketing import art as art_mod  # noqa: E402

_POST_ART = _BACKEND / "app" / "services" / "marketing" / "post_art.py"
_ART_HOST = "sb.example"
_ART_BASE = f"https://{_ART_HOST}/storage/v1/object/public/marketing-media/art/"


def _art_jpeg(size=(1376, 774), *, noise: bool = False, fmt: str = "JPEG", mode: str = "RGB",
              quality: int = 90, shift: int = 0) -> bytes:
    """A synthetic, deterministic art picture: steel-blue stripes with a RED block on the left and a GREEN block on
    the right (so the neutral-palette checks bite on the art) — or random noise (the encoder's worst case)."""
    w, h = size
    if noise:
        rnd = random.Random(1234 + shift)
        im = Image.frombytes("RGB", size, bytes(rnd.getrandbits(8) for _ in range(w * h * 3)))
    else:
        from PIL import ImageDraw

        im = Image.new("RGB", size, (40, 70, 110))
        d = ImageDraw.Draw(im)
        for x in range(shift % 40, w, 40):
            d.line([(x, 0), (x, h)], fill=(90, 130, 170), width=3)
        d.rectangle((w // 34, h // 3, w // 7, 2 * h // 3), fill=(220, 20, 20))              # near the left edge
        d.rectangle((w - w // 7, h // 3, w - w // 34, 2 * h // 3), fill=(20, 200, 40))      # near the right edge
    if mode != "RGB":
        im = im.convert(mode)
    buf = io.BytesIO()
    im.save(buf, fmt, **({"quality": quality} if fmt == "JPEG" else {}))
    return buf.getvalue()


def _art_file(tmp_path: Path, name: str = "art.jpg", **kw) -> cards.ArtImage:
    data = _art_jpeg(**kw)
    path = tmp_path / name
    path.write_bytes(data)
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
    return cards.ArtImage(sha256=hashlib.sha256(data).hexdigest(), path=str(path), width=w, height=h)


def _art_entry(data: bytes, *, size=(1376, 774), **over: Any) -> Dict[str, Any]:
    sha = hashlib.sha256(data).hexdigest()
    entry = {"url": f"{_ART_BASE}{sha[:32]}.jpg", "sha256": sha, "bytes": len(data), "width": size[0],
             "height": size[1]}
    entry.update(over)
    return entry


class _ArtDownloads:
    """`render.download` for the art: the URL answers `body` (counted) or raises `error`."""

    def __init__(self, body: Any = b"", error: Optional[BaseException] = None):
        self.body, self.error = body, error
        self.calls: List[tuple] = []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if self.error is not None:
            raise self.error
        return self.body


def _resolve(entry: Any, dl: _ArtDownloads, tmp_path: Path, *, origin: Optional[str] = _ART_HOST):
    return art_mod.resolve_art({"art": entry}, bucket_origin=origin, download=dl, dest_dir=tmp_path)


def test_the_art_bounds_mirror_the_web_and_the_label_is_the_servers():
    """Read from post_art.py's TEXT (the worker cannot import app.*) and from the imported module: what the web
    stores and lets the read-back carry is exactly what the worker accepts; the label is the server's."""
    text = _literal_assignments(_POST_ART)
    for name in ("ART_PREFIX", "ART_EXT", "ART_CONTENT_TYPE", "ART_MAX_BYTES", "ART_MIN_WIDTH", "ART_MAX_WIDTH",
                 "ART_ASPECT_TOLERANCE"):
        assert getattr(art_mod, name) == text[name] == getattr(post_art, name), name
    assert art_mod.ART_ASPECT == post_art.ART_ASPECT == 16 / 9
    assert art_mod.MEDIA_BUCKET == lg.MEDIA_BUCKET == "marketing-media"
    # its own bomb guard, sized for the widest art at 16:9 — never the logos' 2048²; the stored size fits it
    assert art_mod.MAX_IMAGE_PIXELS == 2752 * 1548 > lg.MAX_IMAGE_PIXELS
    assert post_art.ART_WIDTH * post_art.ART_HEIGHT <= art_mod.MAX_IMAGE_PIXELS
    assert cards.ART_LABEL == tos.ART_LABEL == "AI illustration"
    assert cards.ART_LABEL in tos.art_allowed("a" * 64) and tos.art_allowed(None) == []
    # the worst case render.py counts: the one download (its share + one read) + a second for sha/decode/write
    assert art_mod.ART_WORST_CASE_SECONDS == lg.worst_case_download_seconds(4.0) + 1.0 == 8.0


def test_the_url_the_web_builds_is_the_only_shape_the_worker_fetches(monkeypatch):
    sha = hashlib.sha256(b"x").hexdigest()
    monkeypatch.setattr(post_art.settings, "SUPABASE_URL", f"https://{_ART_HOST}")
    monkeypatch.setattr(post_art.settings, "MARKETING_MEDIA_BUCKET", "marketing-media")
    url = post_art.expected_art_url(sha)
    m = art_mod.art_url_pattern(_ART_HOST).match(url)
    assert m and m.group(1) == sha[:32] and post_art.art_path(sha) == f"art/{sha[:32]}.jpg"
    assert not art_mod.art_url_pattern(_ART_HOST).match(url.replace("/art/", "/logos/"))
    assert not lg.logo_url_pattern(_ART_HOST).match(url)                     # never a logo, either way


def test_an_entry_the_web_sends_passes_and_its_bounds_are_the_webs(monkeypatch):
    """The worker's entry check accepts exactly what post_art.art_problem accepts, at every edge (the web's own
    record check, run on the same numbers), and post_art.worker_art's answer always passes it."""
    monkeypatch.setattr(post_art.settings, "SUPABASE_URL", f"https://{_ART_HOST}")
    monkeypatch.setattr(post_art.settings, "MARKETING_MEDIA_BUCKET", "marketing-media")
    data = b"\xff\xd8x"
    sha = hashlib.sha256(data).hexdigest()
    sizes = [(1376, 774), (1024, 576), (2752, 1548), (1023, 575), (2753, 1549), (1376, 900),
             (1376, 769), (1376, 770), (1376, 778), (1376, 779), (1024, 573), (1024, 574), (2752, 1556), (2752, 1557)]
    verdicts = {}
    for w, h in sizes:
        entry = _art_entry(data, size=(w, h))
        record = dict(entry, status="ok", version=post_art.ART_VERSION)
        web_ok = post_art.art_problem(record) is None
        assert (art_mod.entry_problem(entry) is None) is web_ok, (w, h)
        verdicts[(w, h)] = web_ok
    assert verdicts[(1376, 774)] and verdicts[(1376, 770)] and not verdicts[(1376, 769)]   # both edges really bite
    assert verdicts[(1376, 778)] and not verdicts[(1376, 779)] and not verdicts[(1023, 575)]
    # the series the kept art code was built for (owner decision 2026-11-20, "use it nowhere for now": shipped, none
    # carries art — the Money Map is put back into the art set for this test only)
    from test_marketing_post_art import keep_art_on_money_map
    keep_art_on_money_map(monkeypatch)
    output = {"authorship": "template", "series": "money_map",
              "art": dict(_art_entry(data), status="ok", version=post_art.ART_VERSION)}
    monkeypatch.setattr(post_art.settings, "MARKETING_ART_ENABLED", True)
    sent = post_art.worker_art(output)
    assert sent is not None and set(sent) == set(post_art.WORKER_ART_KEYS) and art_mod.entry_problem(sent) is None
    assert sha == sent["sha256"]
    for bad in ({"bytes": 0}, {"bytes": art_mod.ART_MAX_BYTES + 1}, {"bytes": True}, {"bytes": "12"},
                {"width": 1376.0}, {"height": None}, {"sha256": "A" * 64}, {"sha256": "a" * 63}, {"url": None}):
        assert art_mod.entry_problem(_art_entry(data, **bad)) is not None, bad
    assert art_mod.entry_problem("junk") and art_mod.entry_problem(None)


def test_a_verified_art_resolves_to_a_local_file_with_the_logos_timeouts(tmp_path, caplog):
    data = _art_jpeg()
    dl = _ArtDownloads(data)
    caplog.set_level(logging.INFO, logger="marketing.art")
    got = _resolve(_art_entry(data), dl, tmp_path)
    assert isinstance(got, cards.ArtImage) and (got.width, got.height) == (1376, 774)
    assert got.sha256 == hashlib.sha256(data).hexdigest() and Path(got.path).read_bytes() == data
    assert Path(got.path).name == f"art-{got.sha256[:32]}.jpg" and Path(got.path).parent == tmp_path
    ((url, kw),) = dl.calls
    assert url == _art_entry(data)["url"]
    share = float(art_mod.ART_DOWNLOAD_TIMEOUT_SECONDS)
    assert kw == {"max_bytes": art_mod.ART_MAX_BYTES, "timeout": lg.connect_seconds(share),
                  "read_timeout": lg.read_seconds(share), "max_seconds": share}
    assert all(_ART_BASE not in r.getMessage() for r in caplog.records)        # no URL in a log line


def test_no_art_in_the_read_back_fetches_nothing_and_says_nothing(tmp_path, caplog):
    dl = _ArtDownloads(b"")
    caplog.set_level(logging.DEBUG, logger="marketing.art")
    for script in ({}, {"art": None}, None, "junk", {"logos": []}):
        assert art_mod.resolve_art(script, bucket_origin=_ART_HOST, download=dl, dest_dir=tmp_path) is None
    assert not dl.calls and not caplog.records


@pytest.mark.parametrize("why, url", [
    ("another host", f"https://evil.example/storage/v1/object/public/marketing-media/art/{{hex}}.jpg"),
    ("plain http", f"http://{_ART_HOST}/storage/v1/object/public/marketing-media/art/{{hex}}.jpg"),
    ("another bucket", f"https://{_ART_HOST}/storage/v1/object/public/other-bucket/art/{{hex}}.jpg"),
    ("the logos prefix", f"https://{_ART_HOST}/storage/v1/object/public/marketing-media/logos/{{hex}}.jpg"),
    ("a run folder", f"https://{_ART_HOST}/storage/v1/object/public/marketing-media/2026-11-17/spotlight/{{hex}}.jpg"),
    ("a png", f"{_ART_BASE}{{hex}}.png"),
    ("a query", f"{_ART_BASE}{{hex}}.jpg?download=1"),
    ("upper-case hex", f"{_ART_BASE}{{HEX}}.jpg"),
    ("another object", f"{_ART_BASE}{'0' * 32}.jpg"),
    ("a signed url", f"https://{_ART_HOST}/storage/v1/object/sign/marketing-media/art/{{hex}}.jpg"),
])
def test_only_the_art_url_shape_of_the_runs_bucket_is_ever_fetched(why, url, tmp_path, caplog):
    data = _art_jpeg()
    hex32 = hashlib.sha256(data).hexdigest()[:32]
    dl = _ArtDownloads(data)
    caplog.set_level(logging.WARNING, logger="marketing.art")
    entry = _art_entry(data, url=url.format(hex=hex32, HEX=hex32.upper()))
    assert _resolve(entry, dl, tmp_path) is None, why
    assert not dl.calls, why                                            # never requested
    assert any("not fetched" in r.getMessage() for r in caplog.records), why


def test_an_unknown_bucket_origin_or_a_bad_entry_fetches_nothing(tmp_path, caplog):
    data = _art_jpeg()
    dl = _ArtDownloads(data)
    caplog.set_level(logging.WARNING, logger="marketing.art")
    assert _resolve(_art_entry(data), dl, tmp_path, origin=None) is None
    assert _resolve(_art_entry(data, bytes=art_mod.ART_MAX_BYTES + 1), dl, tmp_path) is None
    assert _resolve(_art_entry(data, size=(1376, 1000)), dl, tmp_path) is None
    assert _resolve("not an object", dl, tmp_path) is None
    assert not dl.calls and len(caplog.records) == 4


def test_a_sha_mismatch_is_an_error_and_no_art(tmp_path, caplog):
    data, other = _art_jpeg(), _art_jpeg(shift=7)
    caplog.set_level(logging.WARNING, logger="marketing.art")
    assert _resolve(_art_entry(data), _ArtDownloads(other), tmp_path) is None
    (rec,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "sha256" in rec.getMessage() and not list(tmp_path.iterdir())     # nothing written


@pytest.mark.parametrize("why, body", [
    ("empty", b""), ("not bytes", "a string"), ("over the cap", b"\xff" * (art_mod.ART_MAX_BYTES + 1)),
])
def test_a_download_that_is_not_the_capped_bytes_is_no_art(why, body, tmp_path):
    data = _art_jpeg()
    assert _resolve(_art_entry(data), _ArtDownloads(body), tmp_path) is None, why


def test_a_download_failure_never_raises(tmp_path, caplog):
    data = _art_jpeg()
    caplog.set_level(logging.WARNING, logger="marketing.art")
    for exc in (RuntimeError("HTTP 404"), TimeoutError("read"), OSError("reset")):
        assert _resolve(_art_entry(data), _ArtDownloads(error=exc), tmp_path) is None
    assert sum("download failed" in r.getMessage() for r in caplog.records) == 3


def test_a_size_the_read_back_does_not_say_is_no_art(tmp_path):
    data = _art_jpeg()
    # the sha is right, but the read-back's bytes count is not the object's
    assert _resolve(_art_entry(data, bytes=len(data) + 1), _ArtDownloads(data), tmp_path) is None
    # the sha is right, but the picture is not the size the read-back says
    assert _resolve(_art_entry(data, size=(1024, 576)), _ArtDownloads(data), tmp_path) is None


@pytest.mark.parametrize("why, make, needle", [
    ("a PNG behind .jpg", lambda: _art_jpeg(fmt="PNG"), "PNG"),
    ("a CMYK JPEG", lambda: _art_jpeg(mode="CMYK"), "CMYK"),
    ("a truncated JPEG", lambda: _art_jpeg()[:5000], ""),
    ("not an image", lambda: b"\xff\xd8 this is not a jpeg at all" * 10, ""),
    ("several frames (MPO)", lambda: _mpo(), "MPO"),
])
def test_wrong_format_mode_frames_or_a_broken_file_is_no_art(why, make, needle, tmp_path):
    data = make()
    assert art_mod.decode_problem(data, width=1376, height=774) is not None, why
    if needle:
        assert needle in art_mod.decode_problem(data, width=1376, height=774), why
    assert _resolve(_art_entry(data), _ArtDownloads(data), tmp_path) is None, why


def _mpo() -> bytes:
    """A two-frame MPO (a JPEG container Pillow opens as 'MPO' with n_frames 2)."""
    a, b = Image.new("RGB", (1376, 774), (40, 70, 110)), Image.new("RGB", (1376, 774), (90, 130, 170))
    buf = io.BytesIO()
    a.save(buf, "MPO", save_all=True, append_images=[b])
    return buf.getvalue()


def test_a_greyscale_jpeg_is_drawable(tmp_path):
    data = _art_jpeg(mode="L")
    got = _resolve(_art_entry(data), _ArtDownloads(data), tmp_path)
    assert got is not None
    assert cards.art_tile(got, 952, 440).mode == "RGB"


def test_a_decompression_bomb_is_refused_from_its_header_never_inflated(monkeypatch):
    """Over MAX_IMAGE_PIXELS is refused from the HEADER: no pixel is decoded (Image.load never runs)."""
    big = _art_jpeg(size=(2800, 1575))
    loads: List[Any] = []
    real_load = Image.Image.load

    def spy(self, *a, **k):
        loads.append(self.size)
        return real_load(self, *a, **k)

    monkeypatch.setattr(Image.Image, "load", spy)
    problem = art_mod.decode_problem(big, width=2800, height=1575)
    assert problem and "over" in problem and str(art_mod.MAX_IMAGE_PIXELS) in problem
    assert not [s for s in loads if s == (2800, 1575)]
    # and Pillow's own bomb warning is an error here, never a decode: a guard lowered below the picture
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1376 * 774 // 3)
    assert "DecompressionBomb" in (art_mod.decode_problem(_art_jpeg(), width=1376, height=774) or "")


def test_resolve_art_never_raises(tmp_path, monkeypatch, caplog):
    data = _art_jpeg()
    caplog.set_level(logging.WARNING, logger="marketing.art")
    # an unwritable destination
    assert art_mod.resolve_art({"art": _art_entry(data)}, bucket_origin=_ART_HOST, download=_ArtDownloads(data),
                               dest_dir=tmp_path / "missing" / "dir") is None
    assert any("could not write" in r.getMessage() for r in caplog.records)
    # a bug anywhere inside: logged with its stack, no art
    monkeypatch.setattr(art_mod, "entry_problem", lambda entry: 1 / 0)
    assert _resolve(_art_entry(data), _ArtDownloads(data), tmp_path) is None
    assert any(r.exc_info and "unexpectedly" in r.getMessage() for r in caplog.records)


# ── the `feature` picture WITH art ───────────────────────────────────────────


def _feature_art_image(art: Optional[cards.ArtImage], files: Optional[Dict[str, Path]] = None,
                       spec: Optional[Dict[str, Any]] = None) -> nl.TemplateImage:
    spec = spec or _feature_spec()
    script = {"authorship": "template", "look": cards.LOOK, "image_spec": spec, "image_footer": spec["footer"], "logos": _entries()}
    return nl.template_image_for_script(script, _table(files), art=art)


def test_the_art_fills_the_band_with_the_plate_bottom_left_and_the_label_bottom_right(tmp_path, logo_files):
    """news-v23 (spot_insider.jpg): with art the band is the picture's top FEATURE_BAND_H rows edge to edge — the art,
    centre-cropped, under a navy scrim at its top (the brand row reads on it) — the disc in its gold ring cut into the
    band's bottom-left edge, the "AI illustration" pill (NAVY_CHIP under white ink) at its bottom-right; the name, the
    headline and the card under the band."""
    art = _art_file(tmp_path)
    image = _feature_art_image(art, logo_files)
    img, layout = nl.draw_template(image, font_path=FONT, layout_engine="basic")
    band = layout.band
    assert layout.art == art and band == (0, 0, 1080, nl.FEATURE_BAND_H)
    ((disc, logo),) = layout.tiles
    r = (disc[2] - disc[0]) // 2
    assert logo.key == "GME" and not logo.wordmark and r == nl.FEATURE_DISC.size(layout.step)
    assert abs((disc[1] + disc[3]) / 2 - (band[3] - 6)) <= 1 and disc[0] == look.POST_FRAME.area[0] + 12
    label = layout.label
    lw, lh = cards.art_label_size(FONT, "basic")
    assert label == (1080 - 40 - lw, band[3] - 28 - lh, 1080 - 40, band[3] - 28)
    assert cards._within(label, band) and not nl._overlap(label, disc) and label[1] > layout.pill[3]
    (lab,) = [ln for ln in layout.lines if ln.field == "art_label"]
    assert lab.text == cards.ART_LABEL and lab.size == cards.ART_LABEL_PX and cards._within(lab.ink, label)
    assert lab.fill == cards.ART_LABEL_INK
    for ln in layout.lines:
        if ln.field not in ("brand", "kicker", "art_label") and not look.is_logo_standin(ln.field):
            assert ln.ink[1] >= band[3], (ln.field, ln.ink)                     # everything else under the band
    # the band's pixels ARE the art (centre-cropped) under the scrim, off the label and the disc with its ring
    px = np.asarray(img)
    tile = np.asarray(cards.art_tile(art, band[2] - band[0], band[3] - band[1]))
    mask = np.zeros(tile.shape[:2], dtype=bool)
    mask[nl.FEATURE_SCRIM_H:, :] = True
    for b in (label, (disc[0] - 14, disc[1] - 14, disc[2] + 14, disc[3] + 14)):
        mask[max(b[1], 0):min(b[3], band[3]), max(b[0], 0):b[2]] = False
    assert (px[band[1]:band[3], band[0]:band[2]][mask] == tile[mask]).all()
    assert (px[label[1] + 2, (label[0] + label[2]) // 2] == np.array(cards.ART_LABEL_FILL)).all()


def test_with_art_green_and_red_live_only_in_the_band_and_the_plate_and_the_margins_stay_page(tmp_path, logo_files):
    """The colour check of the picture, exempting ONLY the art band (and its shadow), the logo disc and the toned
    figure (the art and the logo ARE red and green, so the check bites); every line's ink is in its region."""
    art = _art_file(tmp_path)
    img, layout = nl.draw_template(_feature_art_image(art, logo_files), font_path=FONT, layout_engine="basic")
    px = np.asarray(img)
    exempt = _toned_mask(layout, px.shape[:2])
    band = layout.band
    exempt[band[1]:band[3] + 44, band[0]:band[2]] = True
    for box, _art in layout.tiles:
        exempt[box[1] - 14:box[3] + 14, box[0] - 14:box[2] + 14] = True
    assert (_not_neutral(px) & exempt).any(), "the red/green art was not drawn — the check is vacuous"
    assert not (_not_neutral(px) & ~exempt).any()
    for ln in layout.lines:
        if ln.field == "art_label":
            assert cards._within(ln.ink, layout.label) and cards._within(layout.label, band)
        elif ln.field == "footer":
            assert ln.ink[1] >= layout.panel[3]
        elif ln.field not in ("brand", "kicker") and not look.is_logo_standin(ln.field):
            assert cards._within(ln.ink, (layout.panel[0], band[3], layout.panel[2], layout.panel[3])), ln


def test_the_picture_declares_the_label_only_when_it_drew_the_art_and_the_server_accepts_both(tmp_path):
    spec = _feature_spec()
    art = _art_file(tmp_path)
    image = _feature_art_image(art)
    out = nl.render_template_image(image, font_path=FONT, layout_engine="basic")
    drawn = nl.drawn_strings(image, out.layout)
    assert out.art_skipped is None and out.layout.art == art
    assert drawn == [*image.strings, cards.ART_LABEL, look.BRAND_ROW_WORD]
    allowed = (set(tos.image_strings(spec, _entries())) | {FOOTER} | set(tos.art_allowed(art.sha256))
               | set(sch.PICTURE_BRAND_TEXT))
    assert set(drawn) <= allowed
    assert tos.art_declaration_problem(art.sha256, art.sha256, drawn) is None
    sch.AssetRegisterRequest(kind="card", ext="jpg", sha256="a" * 64, bytes=1000,
                             metadata={"onscreen_text": drawn, "image_role": sch.IMAGE_ROLE_POST,
                                       "art_sha256": art.sha256})
    plain = nl.render_template_image(_feature_art_image(None), font_path=FONT, layout_engine="basic")
    assert nl.drawn_strings(image, plain.layout) == [*image.strings, look.BRAND_ROW_WORD]
    assert tos.art_declaration_problem(None, art.sha256, list(image.strings)) is None      # art is never required
    # the label without the declaration, or the declaration without the label, is what the server refuses
    assert tos.art_declaration_problem(None, art.sha256, drawn) == "art_label_without_art"
    assert tos.art_declaration_problem(art.sha256, art.sha256, list(image.strings)) == "art_label_missing"


def test_the_no_art_picture_and_every_fallback_are_todays_bytes(tmp_path, monkeypatch, caplog):
    """The feature picture WITHOUT art is B's bytes (`_FEATURE_GOLDEN` below is unchanged); a picture whose art
    cannot be drawn falls back to exactly those bytes, says why (art_skipped) and logs a WARNING — never an error
    out of the render, never a skipped day."""
    plain = nl.render_template_image(_feature_art_image(None), font_path=FONT, layout_engine="basic")
    assert plain.art_skipped is None and plain.layout.art is None
    caplog.set_level(logging.WARNING, logger="marketing.news_layouts")
    # unreadable: the verified file is gone by draw time
    gone = _art_file(tmp_path, "gone.jpg")
    Path(gone.path).unlink()
    out = nl.render_template_image(_feature_art_image(gone), font_path=FONT, layout_engine="basic")
    assert (out.art_skipped, out.data, out.layout.art) == ("unreadable", plain.data, None)
    # too large: a byte cap the art picture cannot meet but today's can
    art = _art_file(tmp_path, noise=True)
    cap = len(plain.data) + 1000
    small = nl.render_template_image(_feature_art_image(None), font_path=FONT, layout_engine="basic", max_bytes=cap)
    out = nl.render_template_image(_feature_art_image(art), font_path=FONT, layout_engine="basic", max_bytes=cap)
    assert (out.art_skipped, out.data) == ("too_large", small.data)
    # no fit: a label that cannot fit the band
    ok = _art_file(tmp_path, "ok.jpg")
    with monkeypatch.context() as mp:
        mp.setattr(cards, "art_label_size", lambda font_path, engine: (2000, 40))
        out = nl.render_template_image(_feature_art_image(ok), font_path=FONT, layout_engine="basic")
    assert (out.art_skipped, out.data) == ("no_fit", plain.data)
    # a glyph of the label the face lacks
    with monkeypatch.context() as mp:
        mp.setattr(cards, "ART_LABEL", "AI illustration ☃")
        out = nl.render_template_image(_feature_art_image(ok), font_path=FONT, layout_engine="basic")
    assert (out.art_skipped, out.data) == ("missing_glyphs", plain.data)
    # the label would take the picture past the string cap
    image = _feature_art_image(ok)
    with monkeypatch.context() as mp:
        mp.setattr(cards, "ONSCREEN_TEXT_MAX", len(image.strings))
        out = nl.render_template_image(image, font_path=FONT, layout_engine="basic")
    assert (out.art_skipped, out.data) == ("too_many_strings", plain.data)
    reasons = [r.getMessage() for r in caplog.records if "no art on the picture" in r.getMessage()]
    assert len(reasons) == 5 and set(nl.ART_SKIP_REASONS) == {"missing_glyphs", "too_many_strings", "no_fit",
                                                               "too_large", "unreadable"}


def test_text_that_cannot_fit_is_still_the_pictures_own_overflow_with_or_without_art(tmp_path):
    """A picture that does not fit WITHOUT art overflows with it too: the art falls back (no_fit) to the plain
    picture, whose own overflow is the result (the stage skips it `unrenderable_text`, as today)."""
    spec = _feature_spec()
    spec["headline"] = " ".join(["Incomprehensibilities"] * 26)
    with pytest.raises(cards.CardOverflow):
        nl.render_template_image(_feature_art_image(_art_file(tmp_path), spec=spec), font_path=FONT,
                                 layout_engine="basic")


def test_noise_art_stays_under_the_cap_or_falls_back(tmp_path):
    """The encoder's worst case (random noise in the band) at the real cap: either the art picture is ≤ 950,000
    bytes, or it falls back to today's picture — never a bigger file, never an error."""
    art = _art_file(tmp_path, noise=True)
    out = nl.render_template_image(_feature_art_image(art), font_path=FONT, layout_engine="basic")
    assert len(out.data) <= cards.POST_IMAGE_MAX_BYTES
    if out.art_skipped is None:
        assert out.layout.art == art
    else:
        assert out.art_skipped == "too_large" and out.layout.art is None


def test_same_inputs_same_bytes_and_the_art_changes_them(tmp_path, logo_files):
    a = _art_file(tmp_path, "a.jpg")
    b = _art_file(tmp_path, "b.jpg", shift=11)
    one = nl.render_template_image(_feature_art_image(a, logo_files), font_path=FONT, layout_engine="basic").data
    cards._font.cache_clear()
    cards._fit_wordmark.cache_clear()
    again = nl.render_template_image(_feature_art_image(a, logo_files), font_path=FONT, layout_engine="basic").data
    other = nl.render_template_image(_feature_art_image(b, logo_files), font_path=FONT, layout_engine="basic").data
    plain = nl.render_template_image(_feature_art_image(None, logo_files), font_path=FONT, layout_engine="basic").data
    assert one == again and len({one, other, plain}) == 3


def test_the_art_tile_is_a_deterministic_centre_crop_never_a_stretch(tmp_path):
    """A 16:9 art in the 952×440 band loses its top and bottom, keeps its full width (the red and green blocks
    at the sides stay); in a taller box it loses its sides. Same file, same pixels."""
    art = _art_file(tmp_path)
    wide = cards.art_tile(art, 952, 440)
    assert wide.size == (952, 440) and wide.mode == "RGB"
    assert wide.tobytes() == cards.art_tile(art, 952, 440).tobytes()
    a = np.asarray(wide)
    assert _not_neutral(a[:, :952 // 3]).any() and _not_neutral(a[:, -952 // 3:]).any()
    tall = np.asarray(cards.art_tile(art, 400, 400))
    assert not _not_neutral(tall).any()                           # the side blocks are cropped away
    with pytest.raises(TypeError):
        cards.art_tile("not art", 10, 10)
    bad = cards.ArtImage(sha256=art.sha256, path=art.path, width=art.width + 1, height=art.height)
    with pytest.raises(cards.CardAssetError, match="verified at"):
        cards.art_tile(bad, 952, 440)


@pytest.mark.parametrize("name", ["rows", "spotlight", "bars"])
def test_no_main_lane_picture_ever_draws_art(name, tmp_path, logo_files, caplog):
    """"Video cover now, pictures later": art offered to a rows / spotlight / bars picture is dropped (WARNING) —
    the bytes are today's, nothing declares the label."""
    art = _art_file(tmp_path)
    caplog.set_level(logging.WARNING, logger="marketing.news_layouts")
    spec = SPECS[name]()
    script = {"authorship": "template", "look": cards.LOOK, "image_spec": spec, "image_footer": FOOTER, "logos": _entries()}
    image = nl.template_image_for_script(script, _table(logo_files), art=art)
    assert image.art is None
    assert any("drawn only on a feature picture" in r.getMessage() for r in caplog.records)
    out = nl.render_template_image(image, font_path=FONT, layout_engine="basic")
    plain = nl.render_template_image(_image(spec, _table(logo_files)), font_path=FONT, layout_engine="basic")
    assert out.data == plain.data and nl.drawn_strings(image, out.layout) == [*image.strings, look.BRAND_ROW_WORD]
    with pytest.raises(TypeError):
        nl.template_image_for_script(script, _table(logo_files), art="not art")


def test_a_data_card_never_draws_art(tmp_path, logo_files):
    """The data card is built from the script without art (cards._with_data_card), and a feature picture is never a
    data card at all."""
    art = _art_file(tmp_path)
    with pytest.raises(cards.CardOverflow, match="never drawn as a data card"):
        nl.layout_data_card(_feature_art_image(art, logo_files), font_path=FONT, layout_engine="basic")
    script = {"authorship": "template", "video_layout": "per_line", "image_spec": _spotlight_spec(),
              "image_footer": FOOTER, "logos": _entries(), "disclaimer_card": "Educational only.",
              "opening_card": {"kicker": "FORM 4", "logos": ["GME"], "headline": "GameStop's CEO disclosed buying stock"},
              "video_script": ["One.", "Two."], "cards": [{"title": "The purchase", "body": "One purchase."},
                                                          {"title": "About Form 4", "body": "A filing."}],
              "video_features": ["data_card"], "data_card_slot": 0}
    specs = cards.cards_for_script(script, _table(logo_files), font_path=FONT, layout_engine="basic", art=art)
    assert [s.kind for s in specs] == ["opening", "data", "text", "disclaimer"]
    assert specs[0].art == art and specs[1].image.art is None
    assert cards.ART_LABEL not in cards.onscreen_strings(specs[1])


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_every_composed_spotlight_picture_draws_the_art_at_its_own_step(engine, tmp_path):
    """The picture's fit rate WITH art (news-v23: the band takes the picture's top 432 rows, so the text under it has
    less room than without art): every composed spotlight picture either draws its art or — the widest records —
    falls back to its plain picture (art_skipped "no_fit"), never an overflow; every REAL sample (not a synthetic
    worst case) draws its art. The measured rate is pinned (basic engine)."""
    art = _art_file(tmp_path)
    drawn, fell_back = [], []
    for name, out in _SPOTLIGHT_OUTPUTS.items():
        entries = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None, "width": None,
                    "height": None} for r in out["logo_refs"]]
        script = dict(out, look=cards.LOOK, logos=entries)
        table = cards.logo_table(script, {})
        rendered = nl.render_template_image(nl.template_image_for_script(script, table, art=art), font_path=FONT,
                                            layout_engine=engine)
        if rendered.layout.art == art:
            drawn.append(name)
        else:
            assert rendered.art_skipped == "no_fit", (name, rendered.art_skipped)
            fell_back.append(name)
    assert len(drawn) + len(fell_back) == len(_SPOTLIGHT_OUTPUTS) == 8      # news-v23: the insider spotlight's
    assert all("worst" in n for n in fell_back), fell_back
    if engine == "basic":
        assert len(drawn) == _ART_PICTURE_FIT, (drawn, fell_back)


#: news-v23, measured (basic engine): of the 8 composed insider spotlight pictures (the 13F spotlight is the ONE 13F
#: list since 2026-11-19 and never draws art), how many draw their art (the rest — the synthetic worst cases only —
#: fall back to the plain picture).
_ART_PICTURE_FIT = 8


#: The `feature` picture WITH the synthetic art of `_art_jpeg()` (Pillow 12.0.0, basic engine, every logo a
#: wordmark), measured 2026-11-16 (workstream C). The no-art bytes stay `_FEATURE_GOLDEN`.
#: Fidelity review 2026-11-20: re-pinned (the figure block, the chip on the name's baseline, the glow under the
#: band, the mock's AI pill).
_FEATURE_ART_GOLDEN = {
    "feature+art": "fcf6eb029fdcb84b3e280be8882a7e50d1dd158da62fab74624ba736c409df6b",
}


@pytest.mark.skipif(PIL.__version__ != "12.0.0", reason="the golden bytes were measured on Pillow 12.0.0")
def test_the_feature_picture_with_art_is_pinned(tmp_path):
    art = _art_file(tmp_path)
    assert art.sha256 == hashlib.sha256(_art_jpeg()).hexdigest()
    got = {"feature+art": hashlib.sha256(nl.render_template_image(_feature_art_image(art), font_path=FONT,
                                                                   layout_engine="basic").data).hexdigest()}
    assert got == _FEATURE_ART_GOLDEN


def test_a_label_that_would_cover_the_plate_or_the_kicker_falls_back_never_drawn_over_them(tmp_path, monkeypatch):
    """Defence in depth, made to bite: a label pill wide enough to reach the bottom-left plate, or tall enough to
    reach the top-left kicker pill, while still inside the band — the art does not fit; today's picture is drawn."""
    plain = nl.render_template_image(_feature_art_image(None), font_path=FONT, layout_engine="basic")
    art = _art_file(tmp_path)
    for size in ((900, 44), (584, 400)):            # reaches the disc at every step; reaches the kicker pill only
        with monkeypatch.context() as mp:
            mp.setattr(cards, "art_label_size", lambda font_path, engine, size=size: size)
            out = nl.render_template_image(_feature_art_image(art), font_path=FONT, layout_engine="basic")
        assert (out.art_skipped, out.data) == ("no_fit", plain.data), size


# ── news-v23 fix round (2026-11-19): the drawing matched to the approved mocks ──────────────────────────────────────
# Each test below fails on the code before its fix (checked by hand: the fix reverted, the test red, restored).


def _sample_output(name: str) -> Dict[str, Any]:
    from app.services.marketing import news_templates as T
    import test_marketing_news_templates as NT

    (_n, factory, run_date), = [s for s in NT.SAMPLES if s[0] == name]
    return T.compose(factory(), run_date=run_date, store_state="live", allow_x_url=False)


def _sample_layout(name: str, engine: str = "basic"):
    image = _composed_image(_sample_output(name))
    return image, nl.layout_template(image, font_path=FONT, layout_engine=engine)


def _sizes(layout, field: str) -> List[int]:
    return sorted({ln.size for ln in layout.lines if ln.field == field})


@pytest.mark.parametrize("name", ["costco_money_map", "rivian_money_map", "amazon_money_map", "mcdonalds_money_map",
                                  "coca_cola_money_map", "comcast_money_map"])
def test_a_money_map_keeps_its_80px_headline_plate_and_callout_while_only_its_cards_step(name):
    """mock_money_map.render tries the headline at 80 (then 72, 66) OUTSIDE its card levels, the plate (LOGO_R 86),
    the chip (24), the sub line (32) and the callout (30 / 50) fixed: only the cards step. The worker stepped the whole
    page together, so five of the six samples drew a 65-68 px headline, a smaller plate and callout."""
    image, layout = _sample_layout(name)
    assert layout.head_px == nl.BARS_HEAD_PX[0] == 80
    assert _sizes(layout, "title") == [80] and _sizes(layout, "subtitle") == [32]
    plate = [d for d in layout.discs if d.ring]
    assert len(plate) == 1 and plate[0].box[2] - plate[0].box[0] == 2 * nl.PLATE_DISC.start == 172
    # fidelity review 2026-11-20: the cards took a card LEVEL of mock_money_map.LEVELS (never the ramp)
    assert layout.step == 0 and layout.bars_level is not None
    ls, vs, _bh, _rg, _pad = nl.BARS_LEVELS[layout.bars_level[0]]
    if "costs" in image.spec:
        # owner decision 2026-11-20 (a LOSS year, money_map_loss_A_rivian.jpg): the costs card and its callout at the
        # mock's own sizes in place of the flow and the one-line callout; the revenue card's total drawn once
        assert name == "rivian_money_map" and not _sizes(layout, "callout")
        assert _sizes(layout, "costs.callout[0]") == [28] and _sizes(layout, "costs.callout[1]") == [44]
        assert _sizes(layout, "costs.total.value") == [nl.COSTS_PICTURE.val]
        assert {ln.size for ln in layout.lines if ln.field == "flow[0].value"} == {vs + 8}
        return
    assert _sizes(layout, "callout") == [30, 50] or _sizes(layout, "callout") == [50]
    values = [ln.size for ln in layout.lines if ln.field.startswith("flow[") and ln.field.endswith("value")
              and not ln.field.endswith("#total")]
    assert values and set(values) == {vs}
    assert {ln.size for ln in layout.lines if ln.field == "flow[0].value#total"} == {vs + 8}


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("company_name", ["Wwwwwwwwww Wwwwwwwwww Wwwwwwwwww", "W" * 20 + " " + "W" * 11,
                                          "Wwwwwwwwwwwwww Wwwwwwwwwwwwwww"])
@pytest.mark.parametrize("sample", ["costco", "amazon", "rivian"])
def test_a_money_map_title_too_wide_beside_the_plate_goes_under_it_and_fits(sample, company_name, engine):
    """A company name the server's bounds let through (32 characters, a word of up to 20) must still draw: the title
    is set FULL WIDTH under the plate when its words do not fit beside it (card_layouts._opening's rule). Before, the
    column beside the plate was the only one tried and these names were CardOverflow — a skipped day — while HEAD
    (news-v21) drew them."""
    from app.services.marketing import company_news_rules as R
    from app.services.marketing import news_templates as T
    import test_marketing_news_templates as NT

    rec = getattr(NT, sample)(company=R.CompanyRef(symbol="COST", name=company_name))
    image = _composed_image(T.compose(rec, run_date=date(2026, 11, 19), store_state="live", allow_x_url=False))
    layout = nl.layout_template(image, font_path=FONT, layout_engine=engine)       # never CardOverflow
    plate = [d for d in layout.discs if d.ring][0]
    ring = (plate.box[0] - look.GOLD_RING_GAP, plate.box[1] - look.GOLD_RING_GAP,
            plate.box[2] + look.GOLD_RING_GAP, plate.box[3] + look.GOLD_RING_GAP)
    title = [ln for ln in layout.lines if ln.field == "title"]
    assert title and not any(_overlap(ln.ink, ring) for ln in title)
    if company_name != "Wwwwwwwwwwwwww Wwwwwwwwwwwwwww":                  # these two only fit under the plate
        assert min(ln.ink[1] for ln in title) > plate.box[3] and max(ln.ink[2] for ln in title) > ring[0]


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def test_the_money_map_plate_pill_draws_the_ticker_chip_and_a_news_v22_spec_its_name():
    """mock_money_map.header_block draws the TICKER in the plate's gold pill ("COST", "RIVN"); the worker drew the
    company's name there ("Rivian Automotive", wider than the plate's ring, narrowing the headline column)."""
    out = _sample_output("rivian_money_map")
    assert out["image_spec"]["header"] == {"logo": "RIVN", "name": "Rivian Automotive", "chip": "RIVN"}
    assert "RIVN" in tos.image_strings(out["image_spec"], tos.logo_keys(out["logos"]) if "logos" in out else
                                       [{"key": r["key"], "name": r["name"]} for r in out["logo_refs"]])
    _img, layout = _sample_layout("rivian_money_map")
    assert layout.field_lines("header.chip") == ["RIVN"] and not layout.field_lines("header.name")
    # a spec written before news-v23 (no chip) still draws: the name in the pill
    legacy = _image(_bars_spec(), _table())
    assert "chip" not in legacy.spec["header"]
    old = nl.layout_template(legacy, font_path=FONT, layout_engine="basic")
    assert old.field_lines("header.name") == [legacy.spec["header"]["name"]] and not old.field_lines("header.chip")


@pytest.mark.parametrize("name", ["ceo_roundup", "insider_mixed"])
def test_a_form4_roundup_row_draws_its_ticker_chip_over_the_name(name):
    """mock_form4.wide_row_card: a navy chip with the row's ticker over the company name. The server sends it
    (`rows[].chip`, news_templates._row_chip) and allows it; the worker draws it above cells[0] inside the card."""
    out = _sample_output(name)
    rows = out["image_spec"]["sections"][0]["rows"]
    assert all(r["chip"] == r["logo"] for r in rows)
    _img, layout = _sample_layout(name)
    for j, row in enumerate(rows):
        chip = [ln for ln in layout.lines if ln.field == f"sections[0].rows[{j}].chip"]
        name_ln = [ln for ln in layout.lines if ln.field == f"sections[0].rows[{j}].cells[0]"]
        assert [ln.text for ln in chip] == [row["chip"]] and chip[0].fill == look.WHITE
        assert chip[0].ink[3] < name_ln[0].ink[1]                             # above the name
        box = [r for r in layout.rects if r.fill == look.NAVY_CHIP and _within(chip[0].ink, r.box)]
        assert len(box) == 1


def _within(inner, outer) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def test_a_row_label_is_closed_bounded_and_walked_in_the_servers_order():
    """news-v23 (company stakes): a `rows` row's `label` — the basis word over its figure — is an OPTIONAL row key on both
    sides (ROW_KEYS, pinned equal by the file text above), at most ROW_LABEL_MAX_CHARS long (the longest basis word,
    "committed up to", fits), walked after the row's cells; one character more, or a non-string, is refused by both
    walkers. Mutation-checked by hand (2026-11-20): `label` dropped from the worker's ROW_KEYS, this turned red."""
    from app.services.marketing import news_templates as T
    assert cards.ROW_KEYS == tos.ROW_KEYS == (("cells",), ("logo", "chip", "label", "detail"))
    assert cards.ROW_LABEL_MAX_CHARS == tos.ROW_LABEL_MAX_CHARS == 16
    assert max(len(T.LEXICON[f"st.label.{b}"]) for b in T.STAKE_BASES) <= tos.ROW_LABEL_MAX_CHARS
    spec = _rows_spec(2)
    row = next(r for sec in spec["sections"] for r in sec["rows"] if r.get("logo") is not None)
    row["label"] = "committed up to"
    keys = tos.logo_keys(_entries())
    assert tos.validate_image_spec(spec, keys) is None
    image = _image(spec, _table())                       # the worker's walker takes it too
    assert "committed up to" in image.strings
    server = tos.image_strings(spec, _entries())
    assert server.index("committed up to") == server.index(row["cells"][-1]) + 1      # after the row's cells
    for bad in ("x" * (tos.ROW_LABEL_MAX_CHARS + 1), 7):
        row["label"] = bad
        assert tos.validate_image_spec(spec, keys) is not None
        with pytest.raises(ValueError):
            _image(spec, _table())


def test_the_row_chip_bound_is_one_number_and_its_widest_chip_fits():
    assert cards.ROW_CHIP_MAX_CHARS == tos.ROW_CHIP_MAX_CHARS == 10
    spec = _rows_spec(5)
    for sec in spec["sections"]:
        for row in sec["rows"]:
            if row.get("logo") is not None:
                row["chip"] = "W" * cards.ROW_CHIP_MAX_CHARS
    assert tos.validate_image_spec(spec, tos.logo_keys(_entries())) is None
    for engine in _F13_ENGINES:
        image = _image(spec, _table())
        assert nl.layout_template(image, font_path=FONT, layout_engine=engine).step <= cards.RAMP_STEPS
    from app.services.marketing import news_templates as T
    assert T._row_chip("GME") == {"chip": "GME"} and T._row_chip("A" * 11) == {} and T._row_chip("") == {}


@pytest.mark.parametrize("name", ["ceo_roundup", "insider_mixed"])
def test_a_roundup_flexes_its_row_cards_before_the_page_steps(name):
    """mock_form4.roundup: `h = min(190, room per row)`, the disc `min(74, (h − 22) // 2)` — the headline, the names and
    the amounts keep their size. The worker stepped the whole page (ceo_roundup at step 2: disc 68, amount 67, name 40)."""
    _img, layout = _sample_layout(name)
    assert layout.step == 0
    rows = len(_sample_output(name)["image_spec"]["sections"][0]["rows"])
    figs = [ln for ln in layout.lines if re.fullmatch(r"sections\[0\]\.rows\[\d\]\.cells\[2\]", ln.field)]
    names = [ln for ln in layout.lines if re.fullmatch(r"sections\[0\]\.rows\[\d\]\.cells\[0\]", ln.field)]
    assert len(figs) == rows and {ln.size for ln in figs} == {nl.ROW_FIGURE_STYLE.start}
    assert {ln.size for ln in names} == {nl.ROW_NAME_STYLE.start}
    discs = [d.box[2] - d.box[0] for d in layout.discs if not d.ring]
    assert len(discs) == rows and all(2 * 60 <= dia <= 2 * nl.ROW_DISC.start for dia in discs)


@pytest.mark.parametrize("name, head", [("ceo_spotlight", 80), ("insider_late", None)])
def test_the_spotlight_card_fills_to_the_footer_and_the_figure_takes_what_is_left(name, head):
    """mock_form4.spotlight: the card from the headline + 30 down to the footer − 36, the tiles laid from its foot up,
    only the FIGURE sized to the band left; the headline its own ladder (80 → 60, ≤ 3 lines). The worker sized the card
    to its content and centred it (floating), and stepped the headline, disc and name down with it. (The frozen
    one-company weeks: owner decisions 2026-11-20 compose none.)"""
    _img, layout = _frozen_layout("spotlight", name)
    assert layout.step == 0
    card = max((r for r in layout.rects if r.fill == look.CARD), key=lambda r: r.box[3] - r.box[1])
    # it sits on the footer's gap — the mock's `foot_top - 36` (fidelity review 2026-11-20; the rows' panel ends at 34)
    assert card.box[3] == layout.footer_top - nl.SPOT_FOOTER_GAP == layout.panel[3] - 2
    sizes = _sizes(layout, "headline")
    assert len(sizes) == 1 and 60 <= sizes[0] <= 80 and (head is None or sizes[0] == head)
    assert len(layout.field_lines("headline")) <= 3
    disc = [d for d in layout.discs][0]
    assert disc.box[2] - disc.box[0] == 2 * nl.SPOT_DISC.start
    fig = [ln for ln in layout.lines if ln.field == "figure"][0]
    assert fig.size >= 200
    tiles = [r for r in layout.rects if r.fill == look.TILE]
    assert tiles and max(r.box[3] for r in tiles) == card.box[3] - 44          # the tiles end one pad off its foot


@pytest.mark.parametrize("words, lines", [
    ("At least 3 CEOs disclosed", ["AT LEAST 3 CEOs", "DISCLOSED"]),                       # mock_form4's own break
    ("At least 3 company insiders disclosed", ["AT LEAST 3 COMPANY", "INSIDERS DISCLOSED"]),
    ("Contoso's CEO and at least 2 other insiders", ["CONTOSO'S CEO AND", "AT LEAST 2 OTHER INSIDERS"]),
    ("A director of Northwind and at least one other insider",
     ["A DIRECTOR OF NORTHWIND", "AND AT LEAST ONE", "OTHER INSIDER"]),
])
def test_a_balanced_headline_never_splits_at_least_n_nor_leaves_an_orphan(words, lines):
    """mock_spotlight sets "Contoso's CEO and" / "at least 2 other insiders": the phrase stays whole. The worker drew
    "… AND AT" / "LEAST 2 …"; keeping the phrase whole by greedy narrowing alone left "INSIDER" orphaned."""
    font = cards._font(FONT, 66, "basic")
    got = look.balanced(look.caps(words, FONT).split(), font, 952, 6, "x")
    assert got == lines
    assert " ".join(got) == look.caps(words, FONT)                          # every word, in order


@pytest.mark.parametrize("name", ["isp_cluster", "isp_indirect", "isp_named_outside_window", "isp_single"])
def test_no_insider_spotlight_headline_splits_at_least(name):
    _img, layout = _frozen_layout("feature", name)
    lines = layout.field_lines("headline")
    assert lines and not any(ln.endswith(" AT") or ln.startswith("LEAST ") or ln.endswith(" LEAST")
                             for ln in lines)


def test_the_feature_card_sets_the_mocks_fact_rows_and_two_line_label():
    """mock_spotlight's INSIDER_LAYOUTS["overlay"]: 56 px fact rows; the label beside the figure on two lines ("of stock
    by the CEO," / "per Form 4"). The worker drew 52 px rows and the label on one line."""
    assert nl.FACT_ROW.start == 56
    _image, layout = _frozen_layout("feature", "isp_cluster")
    assert layout.field_lines("figure_label") == ["of stock by the CEO,", "per Form 4"]
    labels = sorted(ln.y for ln in layout.lines if re.fullmatch(r"facts\[\d\]\.label", ln.field))
    assert len(labels) >= 2 and {b - a for a, b in zip(labels, labels[1:])} == {56}


# the colour rules — pinned (they were said to be; nothing read them)

def test_the_13f_heading_tones_are_a_news_v22_webs_headings():
    """news-v23: the 13F list's figures are signed ("+5.6M", "−14.6M") and read their tone from the sign; the
    unsigned per-kind headings of a news-v22 roundup — what an older web still sends during a worker-first deploy —
    keep their tones (the lexicon no longer holds them: pinned here by their words)."""
    from app.services.marketing import news_templates as T

    assert look.HEADING_TONES == {"Newly reported": "gain", "No longer reported": "loss",
                                  "Reported more shares": "gain", "Reported fewer shares": "loss"}
    assert not set(look.HEADING_TONES) & set(T.LEXICON.values())


def test_every_series_has_exactly_one_colour_rule():
    from app.services.marketing import news_templates as T

    groups = (look.GAIN_SERIES, look.SIGNED_SERIES, look.GOLD_SERIES, frozenset({"earnings", "money_map"}))
    assert sum(len(g) for g in groups) == len(set().union(*groups)) == len(T.SERIES_SPECS)
    assert set().union(*groups) == set(T.SERIES_SPECS)


def _rows_with_figures(heads: Dict[str, List[str]]) -> Dict[str, Any]:
    # a news-v22 roundup's shape (its counts subtitle): the generic rows layout, whose figures read the heading's
    # tone — a news-v23 13F LIST (one section, no subtitle) reads only the sign (`_lay_f13_list`)
    spec = {"layout": "rows", "version": 1, "kicker": "13F SEASON", "footer": FOOTER, "title": "A filer's changes",
            "subtitle": "1 change",
            "sections": [{"heading": h, "rows": [{"logo": "GME", "cells": ["GameStop", "status", v]} for v in vals]}
                         for h, vals in heads.items()]}
    return spec


@pytest.mark.parametrize("series, heading, value, colour", [
    ("thirteen_f", "Newly reported", "1.2M", look.GREEN), ("thirteen_f", "Reported more shares", "40K", look.GREEN),
    ("thirteen_f", "No longer reported", "1.2M", look.RED), ("thirteen_f", "Reported fewer shares", "40K", look.RED),
    ("thirteen_f", "Newly reported", "−3M", look.RED),                   # a sign wins over the heading
    ("insider_buys", None, "$1.3M", look.GREEN), ("ceo_buys", None, "$74.4M", look.GREEN),
    ("earnings", None, "$1.10", look.INK), (None, None, "$1.3M", look.INK),
])
def test_a_rows_figure_takes_its_series_and_heading_tone(series, heading, value, colour):
    spec = _rows_with_figures({heading: [value]} if heading else {"": [value]})
    if heading is None:
        spec["sections"][0].pop("heading")
    image = nl.template_image_for_script({"authorship": "template", "look": cards.LOOK, "image_spec": spec,
                                          "image_footer": FOOTER, "logos": _entries(), "series": series}, _table())
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    fig = [ln for ln in layout.lines if ln.field == "sections[0].rows[0].cells[2]"]
    assert fig and fig[0].fill == colour


@pytest.mark.parametrize("value, tone", [("About −18.1M shares", "loss"), ("about +4.5M shares", "gain"),
                                         ("About 48K", None), ("−18.1M shares", "loss"), ("+5.6M", "gain"),
                                         ("Aboutish −3", None)])
def test_signed_tone_reads_through_one_about(value, tone):
    assert look.signed_tone(value) == tone


# logos: a thin outer stroke is never lost; a plated logo's allocation is bounded by its disc

def _thin_badge(path: Path, size=(1002, 252), stroke=4) -> Path:
    from PIL import ImageDraw

    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((1, 1, size[0] - 2, size[1] - 2), radius=size[1] // 2 - 2, outline=(200, 20, 20, 255),
                        width=stroke)
    d.text((size[0] // 2, size[1] // 2), "ACME", fill=(200, 20, 20, 255), anchor="mm",
           font=cards._font(FONT, size[1] // 3, "basic"))
    im.save(path)
    return path


def _thin_wordmark(path: Path) -> Path:
    from PIL import ImageDraw, ImageFont

    im = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for k in range(3):                      # three thin rules across the full width: the logo's outermost marks
        d.line((2, 60 + 60 * k, 247, 60 + 60 * k), fill=(10, 10, 10, 255), width=1)
    d.text((125, 125), "acme", fill=(10, 10, 10, 255), anchor="mm", font=ImageFont.truetype(FONT, 20))
    im.save(path)
    return path


@pytest.mark.parametrize("make", [_thin_badge, _thin_wordmark, lambda p: _thin_badge(p, (960, 320), 3)])
def test_a_logo_whose_outer_strokes_are_thin_never_paints_past_its_disc(tmp_path, make):
    """`_reach` box-reduced the alpha BEFORE thresholding, so a thin outline vanished and the logo was scaled ~2.8×
    too big and pasted unclipped over the card, its chip and its name."""
    path = make(tmp_path / "thin.png")
    # the measure itself: never nearer than the true farthest solid pixel (the clip below is only a second guard)
    rgba = Image.open(path).convert("RGBA")
    rgba = rgba.crop(rgba.getchannel("A").getbbox())
    a = np.asarray(rgba.getchannel("A")) >= 128
    ys, xs = np.nonzero(a)
    true_far = float(np.max(np.hypot(np.maximum(np.abs(xs - rgba.width / 2), np.abs(xs + 1 - rgba.width / 2)),
                                     np.maximum(np.abs(ys - rgba.height / 2), np.abs(ys + 1 - rgba.height / 2)))))
    assert look._reach(rgba) >= true_far - 0.5
    canvas = Image.new("RGB", (400, 400), look.CARD)
    box = (126, 126, 274, 274)                                  # a ROW_DISC (r 74) disc
    look.draw_disc(canvas, look.Disc(box, cards.LogoArt("ACME", "Acme", str(path))), look.POST_FRAME)
    px = np.asarray(canvas).astype(int)
    yy, xx = np.mgrid[0:400, 0:400]
    outside = np.hypot(xx + 0.5 - 200, yy + 0.5 - 200) > 74 + 1.5
    assert (np.abs(px[outside] - np.array(look.CARD)).sum(axis=1) == 0).all()


def test_a_tiny_mark_on_a_big_light_plate_is_resampled_inside_its_disc(tmp_path, monkeypatch):
    """A light plate whose only MARK is a 2×2 dot (its other visible ink is plate-coloured enough not to count) asked
    `_draw_plated` to upscale the WHOLE logo ×50: 21k–32k px squares, 1.8–4.1 GB. Only the window that lands on the
    disc is resampled now."""
    im = Image.new("RGBA", (512, 512), (232, 232, 232, 255))
    im.paste((221, 221, 221, 255), (156, 156, 356, 356))
    im.paste((20, 20, 20, 255), (255, 255, 257, 257))
    path = tmp_path / "dot.png"
    im.save(path)
    assert lg.decode_problem(path.read_bytes(), "png") is None and lg.light_plate(im) is not None
    sizes: List[Any] = []
    real = Image.Image.resize

    def spy(self, size, *a, **k):
        sizes.append(tuple(size))
        assert size[0] * size[1] <= 4_000_000, f"resample to {size} — unbounded by the disc"
        return real(self, size, *a, **k)

    monkeypatch.setattr(Image.Image, "resize", spy)
    canvas = Image.new("RGB", (400, 400), look.CARD)
    look.draw_disc(canvas, look.Disc((90, 90, 310, 310), cards.LogoArt("DOT", "Dot", str(path))), look.POST_FRAME)
    assert sizes and max(w * h for w, h in sizes) <= 4_000_000


# ── news-v23 phase 3, the theme's last three angles (2026-11-20): `figures`, `ledger`, `tally` at the server's worst ──
#
# A table's names are bounded by the SERVER (`company_news_rules.theme_table_name_fits`: at most
# THEME_TABLE_NAME_LINES[angle] lines of THEME_TABLE_NAME_W[angle] at the floor, `text_metrics` an upper bound): each test
# builds the widest record the server lists — every name at the bound, the widest chip a symbol can be ("WWWWW-C"),
# the widest figures — and lays the picture AND its six-row cover out with the worker's own code, every logo a
# wordmark. The twin breaks the bound and must overflow — never a clipped or squeezed name.

_ANGLE_OF_LAYOUT = {"figures": "growth_profit", "ledger": "insiders", "tally": "congress"}
_TABLE_SYMS = ["WWWWW-A", "WWWWW-B", "WWWWW-C", "MWWWW-A", "MWWWW-B", "MWWWW-C", "WMWWW-A", "WMWWW-B", "WMWWW-C",
               "WWMWW-A", "WWMWW-B", "WWMWW-C", "WWWMW-A", "WWWMW-B"]


def _table_word(angle: str) -> str:
    """The widest "Wm" run one line of the angle's name column holds at the floor (by `text_metrics`, the server's)."""
    width = R.THEME_TABLE_NAME_W[angle]
    word = ""
    for ch in "Wm" * 60:
        if T.text_metrics.text_width(word + ch, R.THEME_TABLE_NAME_FLOOR_PX) > width:
            break
        word += ch
    return word[:20]                                                    # a display-name word is at most 20 characters


def _table_name(angle: str) -> str:
    """The widest name the server lists for `angle` within the 32-character display-name cap."""
    word = _table_word(angle)
    name = word if R.THEME_TABLE_NAME_LINES[angle] == 1 else f"{word} {word}"
    return name[:32].rstrip()


def _table_worst(layout: str, n: int):
    angle = _ANGLE_OF_LAYOUT[layout]
    name = _table_name(angle)
    assert R.theme_table_name_fits(angle, name)
    run = date(2026, 11, 25)
    cos = [_co(s, name) for s in _TABLE_SYMS[:n]]
    if layout == "figures":
        members = tuple(R.ThemeMember(company=c, top_segment=None, top_segment_share=None, fiscal_year="2025",
                                      revenue_usd=(600.0 if i % 2 else 1.0), prior_revenue_usd=(100.0 if i % 2 else 2.0),
                                      net_income_usd=(-6000.0 if i % 2 else -10.0)) for i, c in enumerate(cos))
        extra = {}
    elif layout == "ledger":
        members = tuple(R.ThemeMember(company=c, top_segment=None, top_segment_share=None, fiscal_year=None,
                                      purchases_usd=999.9e6 - i, sales_usd=999.9e6) for i, c in enumerate(cos))
        extra = dict(window_start=run - timedelta(days=90), window_end=run - timedelta(days=1), companies_checked=n)
    else:
        members = tuple(R.ThemeMember(company=c, top_segment=None, top_segment_share=None, fiscal_year=None,
                                      congress_members=535 - i) for i, c in enumerate(cos))
        extra = dict(month="2026-10")
    return R.ThemeExplainer(series="theme_explainer", slug="cyber-wars", title=_TH_TITLE, members=members,
                            tickers_as_of=date(2026, 11, 18), theme_size=24, angle=angle, posted_on=run, **extra)


def test_phase3_the_table_constants_mirror_the_server():
    """The worker's copies of the tables' contract (cards.py cannot import app.*): template_onscreen's layouts, rows,
    heads, shapes, the empty cell and the cover's rows; company_news_rules' name bound, keyed by layout."""
    for name in ("THEME_TABLE_LAYOUTS", "MIN_THEME_TABLE_ROWS", "MAX_THEME_TABLE_ROWS", "THEME_TABLE_ROW_KEYS",
                 "MINUS", "EMPTY_CELL", "MAX_OPENING_TABLE_ROWS"):
        assert getattr(cards, name) == getattr(tos, name), name
    assert (cards.THEME_HEADS, cards.MAX_HEAD_LINES) == (tos.THEME_HEADS, tos.MAX_HEAD_LINES) == (2, 2)
    for name in ("GROWTH_FIGURE_RE", "PROFIT_FIGURE_RE", "LEDGER_AMOUNT_RE", "TALLY_COUNT_RE"):
        assert getattr(cards, name).pattern == getattr(tos, name).pattern, name
    assert {layout: cards.THEME_TABLE_NAME_W[layout] for layout in _ANGLE_OF_LAYOUT} == {
        layout: R.THEME_TABLE_NAME_W[angle] for layout, angle in _ANGLE_OF_LAYOUT.items()}
    assert {layout: cards.THEME_TABLE_NAME_LINES[layout] for layout in _ANGLE_OF_LAYOUT} == {
        layout: R.THEME_TABLE_NAME_LINES[angle] for layout, angle in _ANGLE_OF_LAYOUT.items()}
    assert cards.THEME_TABLE_FLOOR_PX == R.THEME_TABLE_NAME_FLOOR_PX
    assert nl.TH_FIG_NAME.floor == nl.TH_LEDGER_NAME.floor == nl.TH_TALLY_NAME.floor == R.THEME_TABLE_NAME_FLOOR_PX
    assert set(cards.THEME_TABLE_LAYOUTS) <= set(nl.DRAWERS) and nl.TALLY_PILL_WORD == "purchases"
    assert T.LEXICON["th.cg.sub"].split()[-1] == nl.TALLY_PILL_WORD           # the pill's word is the sub line's last


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("layout, n", [("figures", 14), ("figures", 4), ("ledger", 14), ("ledger", 4), ("tally", 14),
                                       ("tally", 4)])
def test_phase3_the_table_image_and_cover_fit_the_servers_worst(engine, layout, n):
    """The widest names the server lists, the widest chip and figures ("+500%" / "−100%", "−$1,000", "$999.9M", "535"),
    the tallest title, "+N more": the picture and its six-row list cover lay out before the floor step, every string
    whole, every video card too."""
    out = _compose(_table_worst(layout, n), date(2026, 11, 25))
    spec = out["image_spec"]
    assert spec["layout"] == layout and spec["rows"][0]["chip"] == "WWWWW-A"
    assert len(out["opening_card"]["list"]["rows"]) == min(n, 6)
    if layout == "figures":
        assert {r["growth"] for r in spec["rows"]} == {"+500%", "−50%"} and "−$1,000" in {r["profit"] for r in spec["rows"]}
    _lay_out_list_post(out, engine)


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("layout", ["figures", "ledger", "tally"])
def test_phase3_a_table_name_past_the_servers_bound_overflows_never_clips(engine, layout):
    """Anti-vacuity: the server leaves out (adapter) and refuses (template) a name past its bound; the worker, handed
    one anyway — a name three times the bound's width — finds no step it fits at (picture or cover): it never clips
    a name or draws one under the floor."""
    out = _compose(_table_worst(layout, 6), date(2026, 11, 25))
    word = _table_word(_ANGLE_OF_LAYOUT[layout])
    bad = json.loads(json.dumps(out))
    for row in bad["image_spec"]["rows"]:
        row["name"] = " ".join([word] * 4)
    with pytest.raises(cards.CardOverflow):
        nl.layout_template(_f13_image(bad)[0], font_path=FONT, layout_engine=engine)
    import dataclasses

    with pytest.raises(T.NewsTemplateRefused, match="slot_rejected"):
        _compose(dataclasses.replace(
            _table_worst(layout, 6),
            members=tuple(dataclasses.replace(m, company=_co(m.company.symbol, (word + " " + word + " W")[:32]))
                          for m in _table_worst(layout, 6).members)), date(2026, 11, 25))


@pytest.mark.parametrize("layout", ["figures", "ledger", "tally"])
def test_phase3_the_table_pictures_are_deterministic_and_coloured_by_sign(layout):
    """The same spec draws the same bytes twice (the reuse key's promise); every figure is GREEN or RED by its sign —
    a growth or profit with U+2212 RED, else GREEN; purchases GREEN and sales RED; the counts GREEN; the column-head
    pills solid GREEN / RED with WHITE ink; "purchases" on the Congress sub line on a GREEN pill."""
    out = _compose(_table_worst(layout, 6), date(2026, 11, 25))
    image = _f13_image(out)[0]
    a = nl.render_template_image(image, font_path=FONT, layout_engine="basic").data
    b = nl.render_template_image(image, font_path=FONT, layout_engine="basic").data
    assert a == b and len(a) > 10_000
    lay = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    by_field: Dict[str, Any] = {}
    for ln in lay.lines:
        by_field.setdefault(ln.field, []).append(ln)
    if layout == "figures":
        for i, row in enumerate(out["image_spec"]["rows"]):
            for key in ("growth", "profit"):
                want = look.RED if row[key].startswith("−") else look.GREEN
                assert {ln.fill for ln in by_field[f"rows[{i}].{key}"]} == {want}, (i, key)
    elif layout == "ledger":
        assert {ln.fill for ln in by_field["rows[0].buys"]} == {look.GREEN}
        assert {ln.fill for ln in by_field["rows[0].sells"]} == {look.RED}
        assert {ln.fill for ln in by_field["heads[0][0]"] + by_field["heads[1][1]"]} == {look.WHITE}
        pills = [r for r in lay.rects if r.fill in (look.GREEN, look.RED) and r.radius == 12]
        assert {r.fill for r in pills} == {look.GREEN, look.RED}
    else:
        assert {ln.fill for i in range(6) for ln in by_field[f"rows[{i}].count"]} == {look.GREEN}
        sub = by_field["subtitle"]
        assert [ln.text for ln in sub][-1] == "purchases" and sub[-1].fill == look.WHITE
        assert any(r.fill == look.GREEN and r.radius == 9 for r in lay.rects)


def test_phase3_a_table_cover_is_only_a_cover_never_a_data_card():
    out = _compose(_table_worst("figures", 6), date(2026, 11, 25))
    image = _f13_image(out)[0]
    with pytest.raises(cards.CardOverflow, match="never drawn as a data card"):
        nl.layout_data_card(image, font_path=FONT, layout_engine="basic")
    nl.layout_data_card(image, font_path=FONT, layout_engine="basic", cover=True)
    assert set(cards.THEME_TABLE_LAYOUTS).isdisjoint(cards.DATA_CARD_LAYOUTS)


# ── fidelity review 2026-11-20 (owner: "i need exactly these styles, colors, words") ─────────────────────────────────
#
# Each test pins one measured difference against the approved mock that drew it (FINAL_REFERENCE/scripts), and was
# mutation-checked by hand once: the fix reverted turns it red.


def test_fidelity_a_chip_is_the_kits_box_its_size_plus_16_its_text_6px_under_its_top():
    """style_kit.chip: `[x, y, x + w + 24, y + size + 16]`, the label "la" at (x + 12, y + 6). `look.chip_size` used
    the font's ascent + descent + 16 — every chip of every family 6 px taller than approved (44 px for a 22 px label)."""
    page = look.Page(look.POST_FRAME, 0, FONT, "basic")
    for size in (22, 24, 26, 28, 30):
        assert look.chip_size(page, "GME", size)[1] == size + 16
    box = look.chip(page, "c", "BRK-B", 22, 100, 200)
    (ln,) = [x for x in page.lines if x.field == "c"]
    assert box[3] - box[1] == 38 and (ln.anchor, ln.x, ln.y) == ("la", box[0] + 12, box[1] + 6)
    assert _within_box(ln.ink, box)


def _within_box(ink, box) -> bool:
    return box[0] <= ink[0] and box[1] <= ink[1] and ink[2] <= box[2] and ink[3] <= box[3]


def test_fidelity_the_form4_roundup_draws_the_mocks_76px_headline_and_its_row_block():
    """mock_form4.roundup: `headline2(size=76, max_lines=4, min_size=56)` and wide_row_card's block `38 + 10 + name_size
    + 8 + 28` (the chip, the name 48 px under the chip's top, the role 50 px under the name, centred on the card). The
    worker drew the roundup headline at 80 and advanced the block by the fonts' ascent + descent (21 px taller)."""
    _image, layout = _sample_layout("ceo_roundup")
    assert _sizes(layout, "title") == [76] and nl.ROWS_HEAD_PX[0] == 76
    rows = [r for r in layout.rects if r.fill == look.CARD]
    assert len(rows) == 3
    for j, card in enumerate(rows):
        h = card.box[3] - card.box[1]
        chip = next(r for r in layout.rects if r.fill == look.NAVY_CHIP and _within_box(r.box, card.box))
        name = next(ln for ln in layout.lines if ln.field == f"sections[0].rows[{j}].cells[0]")
        role = next(ln for ln in layout.lines if ln.field == f"sections[0].rows[{j}].cells[1]")
        assert chip.box[3] - chip.box[1] == 38
        assert chip.box[1] - card.box[1] == round((h - 126) / 2)          # the mock's block, centred
        assert (name.y - chip.box[1], role.y - name.y) == (48, 50)


def test_fidelity_the_form4_spotlight_chip_is_the_mocks_26px_and_the_name_58px_under_it():
    """mock_form4.spotlight: `chip(..., size=26)` and `block = 44 + 14 + name_size` (the name 58 px under the chip's
    top, the block centred on the disc). The worker drew a 24 px chip 7 px higher."""
    _image, layout = _frozen_layout("spotlight", "ceo_spotlight")
    (chip,) = [ln for ln in layout.lines if ln.field == "header.chip"]
    (name,) = [ln for ln in layout.lines if ln.field == "header.name"]
    disc = layout.discs[0]
    assert chip.size == 26 and name.y - (chip.y - look.CHIP_TEXT_DY) == 58
    block = 42 + 16 + name.size
    assert chip.y - look.CHIP_TEXT_DY == round((disc.box[1] + disc.box[3]) / 2 - block / 2)
    card = next(r for r in layout.rects if r.fill == look.CARD)
    assert card.box[3] == layout.footer_top - 36                  # `bottom = foot_top - 36`


@pytest.mark.parametrize("r", [74, 92])
def test_fidelity_a_wordmark_disc_starts_at_the_kits_26px(r):
    """style_kit.logo_circle: 26 px, shrinking 1 px at a time while wider than 1.55·r. The worker started at
    0.36·r (30 px on the r 92 feature disc) and stepped 27 → 24, never trying 26 or 25."""
    lines = look.disc_wordmark_lines(cards.LogoArt("CTSO", "Contoso", None), (0, 0, 2 * r, 2 * r), FONT, "basic")
    assert lines and {ln.size for ln in lines} == {26}


def _seal_with_mark(path: Path) -> Path:
    """A round seal (a filled green disc) with a small detached BLUE "®" off its lower-right edge — Starbucks' shape."""
    from PIL import ImageDraw

    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((5, 5, 195, 195), fill=(0, 112, 74, 255))
    d.ellipse((172, 172, 184, 184), fill=(30, 60, 220, 255))
    im.save(path)
    return path


def _drawn(canvas: "Image.Image", colour, tol: int = 40):
    a = np.asarray(canvas).astype(int)
    m = (np.abs(a - np.array(colour)).max(axis=2) <= tol)
    ys, xs = np.nonzero(m)
    return (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1) if len(xs) else None, (xs, ys)


def test_fidelity_a_detached_mark_does_not_shrink_a_seal_and_is_drawn_whole_inside_its_disc(tmp_path):
    """mock_form4 drew Starbucks' seal at 0.88 of its disc; the worker's farthest-pixel rule measured the detached ®
    and drew it at 0.73. The reach is the MAIN mass's now (`look._main_reach`), the ® still drawn whole inside the
    disc."""
    path = _seal_with_mark(tmp_path / "seal.png")
    canvas = Image.new("RGB", (400, 400), look.CARD)
    box = (126, 126, 274, 274)                                   # a ROW_DISC (r 74)
    look.draw_disc(canvas, look.Disc(box, cards.LogoArt("SEAL", "Seal", str(path))), look.POST_FRAME)
    (sx0, _sy0, sx1, _sy1), _ = _drawn(canvas, (0, 112, 74))
    assert (sx1 - sx0) / 148 >= 0.78, (sx1 - sx0)
    _bb, (mx, my) = _drawn(canvas, (30, 60, 220), tol=60)
    assert len(mx) > 4                                           # the ® is drawn …
    assert float(np.max(np.hypot(mx + 0.5 - 200, my + 0.5 - 200))) <= 74      # … whole inside the disc


def _diamond(path: Path, *, hole: bool = True) -> Path:
    from PIL import ImageDraw

    im = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.polygon([(125, 0), (249, 125), (125, 249), (0, 125)], fill=(255, 176, 0, 255))
    if hole:
        d.polygon([(125, 62), (187, 125), (125, 187), (62, 125)], fill=(0, 0, 0, 0))
    im.save(path)
    return path


def _ring_logo(path: Path) -> Path:
    from PIL import ImageDraw

    im = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((0, 0, 249, 249), outline=(255, 176, 0, 255), width=40)
    im.save(path)
    return path


@pytest.mark.parametrize("make, diamond", [(_diamond, True), (lambda p: _logo_png(p, (255, 176, 0, 255)), False),
                                           (_ring_logo, False)], ids=["diamond", "filled_disc", "ring"])
def test_fidelity_a_diamond_mark_is_capped_at_the_mocks_fill_and_a_round_mark_is_not(tmp_path, make, diamond):
    """mock_money_map LOGO_FILL["RIVN"] = 0.70 ("0.84 / 0.76 put … the Rivian diamond's side points within ~5-10 px of
    the disc edge"); the reach rule drew it at 0.84. A diamond's long side is capped at DIAMOND_FILL now; a filled
    round mark and a ring (the same reach, solid along the diagonals) keep the reach rule."""
    path = make(tmp_path / "mark.png")
    rgba = Image.open(path).convert("RGBA")
    rgba = rgba.crop(rgba.getchannel("A").getbbox())
    assert look._diamond_like(rgba) is diamond
    canvas = Image.new("RGB", (400, 400), look.CARD)
    look.draw_disc(canvas, look.Disc((126, 126, 274, 274), cards.LogoArt("M", "M", str(path))), look.POST_FRAME)
    (x0, y0, x1, y1), _ = _drawn(canvas, (255, 176, 0))
    fill = max(x1 - x0, y1 - y0) / 148
    if diamond:
        assert fill <= look.DIAMOND_FILL + 0.01 and look.DIAMOND_FILL == 0.70
    else:
        assert fill >= 0.80                                       # the reach rule: ~0.84 (LOGO_REACH 0.86 of the radius)


def test_fidelity_the_feature_card_sets_its_label_on_the_figures_baseline_at_the_mocks_height():
    """mock_spotlight.render_insider_spotlight: the figure in a cap-height box (`int(136 × 0.76)`), the label's LAST line
    on the figure's baseline, the hairline 30 px under it — a 421 px card for four facts, 69 px under the headline. The
    worker centred the label on the '$' ink (20 px high) and drew a 441 px card."""
    image = _composed_image(_frozen_output("feature", "isp_cluster"))
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    (fig,) = [ln for ln in layout.lines if ln.field == "figure"]
    label = [ln for ln in layout.lines if ln.field == "figure_label"]
    assert fig.anchor == "ls" and label[-1].anchor == "ls" and label[-1].y == fig.y
    card = next(r for r in layout.rects if r.fill == look.CARD)
    assert layout.step == 0 and card.box[3] - card.box[1] == 40 + 103 + 30 + 2 + 8 + 4 * 56 + 14 == 421
    divider = min(r.box[1] for r in layout.rects if r.fill == look.FACT_DIVIDER)
    assert divider == fig.y + 30


def test_fidelity_a_feature_without_art_wears_the_solid_gold_ring():
    """mock_spotlight.logo_badge: the disc wears a solid 5 px gold ring. Without art the worker drew the plate's thin
    ring. (Where the block sits without art — balanced since the visual-fidelity round 2 — is
    test_fidelity2_the_insider_spotlight_without_art_is_balanced_on_the_page.)"""
    image = _composed_image(_frozen_output("feature", "isp_cluster"))
    assert image.art is None
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    (disc,) = layout.discs
    assert disc.ring and disc.solid_ring and disc.cutout is None


def test_fidelity_a_feature_headline_shrinks_before_it_breaks_its_clause():
    """mock_spotlight.headline_lines shrinks the headline (66 → 50) so "AT LEAST N OTHER INSIDERS" stays one line; the
    worker drew 66 px in four lines ("AND AT LEAST ONE" / "OTHER INSIDER")."""
    image = _composed_image(_frozen_output("feature", "isp_named_outside_window"))
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    head = [ln.text for ln in layout.lines if ln.field == "headline"]
    assert len(head) <= nl.FEATURE_HEAD_MAX_LINES
    assert any(t.endswith("AT LEAST ONE OTHER INSIDER") for t in head), head


def test_fidelity_the_13f_picture_draws_one_72px_headline_and_both_filers_alike():
    """mock_f13_lists (art director round 3): ONE headline size for every 13F post — 72 px, the largest both approved
    filers fit — and Pershing's three rows drawn with Berkshire's figure / name / status / disc sizes ("the two 13F posts
    differ in style" is the mock's own assert). The worker drew 80 px, and Pershing's 4-line title stepped the whole page
    to ramp step 3."""
    lays = {name: _sample_layout(name)[1] for name in ("berkshire_13f", "pershing_13f")}
    style = {}
    for name, lay in lays.items():
        assert lay.step == 0 and _sizes(lay, "title") == [72]
        style[name] = (_sizes(lay, "sections[0].rows[0].cells[2]"), _sizes(lay, "sections[0].rows[0].cells[0]"),
                       _sizes(lay, "sections[0].rows[0].cells[1]"),
                       sorted({d.box[2] - d.box[0] for d in lay.discs if not d.ring}))
        # the column label's ink top on its row's top: the first card 40 px under it (the mock's label_h)
        (lab,) = [ln for ln in lay.lines if ln.field == "sections[0].heading"]
        first = min(r.box[1] for r in lay.rects if r.fill == look.CARD)
        assert first - lab.ink[1] == 24 + 16
    assert style["berkshire_13f"] == style["pershing_13f"]


def test_fidelity_the_money_map_picture_takes_the_mocks_card_level_and_positions():
    """mock_money_map.render: the cards sized by its LEVELS table (Costco: (26, 28, 16, 14, 24)), 18 + min(22, spare // 3)
    apart and centred — cards 387-686 / 717-1051 / 1082-1248 — the subtitle at the headline's pitch + 10. The worker
    stepped the ramp (text ~3 px bigger, a 51 px bar, 19 px gaps)."""
    _image, layout = _sample_layout("costco_money_map")
    assert layout.head_px == 80 and nl.BARS_LEVELS[layout.bars_level[0]] == (26, 28, 16, 14, 24)
    cards_ = sorted(r.box for r in layout.rects if r.fill == look.CARD)
    assert len(cards_) == 3
    for (y0, y1), (m0, m1) in zip([(b[1], b[3]) for b in cards_], [(387, 686), (717, 1051), (1082, 1248)]):
        assert abs(y0 - m0) <= 3 and abs(y1 - m1) <= 3, cards_
    (stack,) = layout.stacks
    assert stack.box[3] - stack.box[1] == 56
    (sub,) = [ln for ln in layout.lines if ln.field == "subtitle"]
    assert sub.y == 140 + 2 * round(80 * 1.08) + 10


@pytest.mark.parametrize("rows", [(("ACN", "Accenture"), ("CTSO", "Contoso")),
                                  (("LMT", "Lockheed Martin"), ("RTX", "RTX"), ("KTOS", "Kratos")),
                                  (("PLTR", "Palantir"), ("ACN", "Accenture"), ("GD", "General Dynamics"))],
                         ids=["acn_ctso", "lmt_rtx_ktos", "pltr_acn_gd"])
def test_fidelity_every_congress_card_opens_its_second_line_on_the_green_pill(rows):
    """congress_multi3.jpg wraps every card alike: "members disclosed" / "purchases of <Co> stock". The worker balanced
    each card's line on its own, so the solid GREEN pill jumped between the end of line 1 and the start of line 2."""
    counts = tuple(R.CongressCount(series="congress_count", company=_co(sym, name), month="2026-11", members=3 - (i > 0),
                                   fetched_on=date(2026, 12, 8)) for i, (sym, name) in enumerate(rows))
    out = _compose(R.CongressMonth(series="congress_count", month="2026-11", fetched_on=date(2026, 12, 8),
                                   counts=counts), date(2026, 12, 8))
    image, table = _f13_image(out)
    picture = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    cover = cards.layout_card(cards.cards_for_script(script, table)[0], font_path=FONT, layout_engine="basic")
    for what, lay in (("picture", picture), ("cover", cover)):
        for j in range(len(rows)):
            runs = [ln for ln in lay.lines if ln.field == f"sections[0].rows[{j}].cells[1]"]
            first_base = min(ln.y for ln in runs)
            pill = next(ln for ln in runs if ln.text == nl.CG_PILL_WORD)
            line_index = sorted({ln.y for ln in runs}).index(pill.y)
            assert line_index == 1 and pill.y > first_base, (what, j, [(ln.text, ln.y) for ln in runs])
            # the pill's word opens its line
            assert pill.x - nl.CG_PILL_PAD_X == min(ln.x for ln in runs if ln.y == pill.y) - nl.CG_PILL_PAD_X


def _list_surfaces(out: Dict[str, Any]):
    """(the picture's layout, the list cover's) of a composed LIST post, every logo a wordmark."""
    image, table = _f13_image(out)
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    cover_spec = cards.cards_for_script(script, table)[0]
    assert cover_spec.image is not None                                   # the cover IS the list
    return (("picture", nl.layout_template(image, font_path=FONT, layout_engine="basic")),
            ("cover", cards.layout_card(cover_spec, font_path=FONT, layout_engine="basic")))


def _fill_under(layout, line) -> Any:
    """The fill of the LAST-drawn rect the line's ink sits inside (its pill or tile; the card is drawn before it)."""
    under = [r for r in layout.rects if _within(line.ink, r.box)]
    assert under, (line.field, line.text)
    return under[-1].fill


def _neutral_surprise_earnings() -> Dict[str, Any]:
    """Two fictional reports, one with a revenue pair that rounds to a 0.0% surprise (the neutral cell)."""
    reports = (R.EarningsReport(series="earnings", company=_co("NWND", "Northwind"), report_date=date(2026, 11, 5),
                                period_end=date(2026, 9, 30), eps_actual=0.15, eps_estimate=0.10,
                                revenue_actual=2_000_000_000.0, revenue_estimate=2_000_000_000.0),
               R.EarningsReport(series="earnings", company=_co("CTSO", "Contoso"), report_date=date(2026, 11, 5),
                                period_end=date(2026, 9, 30), eps_actual=1.20, eps_estimate=1.00,
                                revenue_actual=900_000_000.0, revenue_estimate=1_000_000_000.0))
    return _compose(R.EarningsDay(series="earnings", reports=reports), date(2026, 11, 12))


def test_final_the_earnings_table_colours_only_the_surprise_and_by_its_sign():
    """Spec §2.3, owner 2026-11-19: "+X% surprised (in green) or -X% surprised (in red)", the Estimate and Reported
    cells NEUTRAL — on the picture AND on its list cover: the estimate INK on PILL_LIGHT, the reported figure WHITE on
    NAVY_CHIP, a "+" surprise GREEN on GREEN_TINT, a "−" one RED on RED_TINT, "0.0%" INK2 on PILL_LIGHT. The final
    review (2026-11-20) found these owner-mandated colours unpinned: drawing every surprise grey left the suites
    green. Mutation-checked by hand (2026-11-20): with `_ErTable.draw`'s tone forced to None, this test went red."""
    seen = set()
    for out in (_list_sample("earnings4"), _neutral_surprise_earnings()):
        rows = out["image_spec"]["sections"][0]["rows"]
        for where, layout in _list_surfaces(out):
            for j, row in enumerate(rows):
                for i, line in enumerate(row["table"]):
                    lp = f"sections[0].rows[{j}].table[{i}]"
                    est, rep, sur = (next(ln for ln in layout.lines if ln.field == f"{lp}.cells[{k}]")
                                     for k in (1, 2, 3))
                    assert (est.text, est.fill, _fill_under(layout, est)) == (line["cells"][1], look.INK,
                                                                              look.PILL_LIGHT), (where, lp)
                    assert (rep.text, rep.fill, _fill_under(layout, rep)) == (line["cells"][2], look.WHITE,
                                                                              look.NAVY_CHIP), (where, lp)
                    cell = line["cells"][3]
                    want = {"+": (look.GREEN, look.GREEN_TINT), "−": (look.RED, look.RED_TINT)}.get(
                        cell[:1], (look.INK2, look.PILL_LIGHT))
                    assert (sur.text, sur.fill, _fill_under(layout, sur)) == (cell, *want), (where, lp)
                    seen.add(want)
    # anti-vacuity: all three tones were drawn
    assert seen == {(look.GREEN, look.GREEN_TINT), (look.RED, look.RED_TINT), (look.INK2, look.PILL_LIGHT)}


def test_final_the_congress_counts_and_the_purchases_pill_are_the_same_green():
    """Spec §2.4, owner 2026-11-19: "use green color for 'purchases'. also, numbers like 6,4,3,2,2, use green color,
    same green as the purchases" — on the picture AND its list cover: each card's member count GREEN on its GREEN_TINT
    tile, the word "purchases" WHITE on a solid GREEN pill, the rest of the line INK2. Unpinned until the final review
    (2026-11-20): the counts drawn INK left the suites green. Mutation-checked by hand (2026-11-20): with the count's
    colour INK, this test went red."""
    out = _list_sample("congress3")
    rows = out["image_spec"]["sections"][0]["rows"]
    assert len(rows) == 3
    for where, layout in _list_surfaces(out):
        for j, row in enumerate(rows):
            rp = f"sections[0].rows[{j}]"
            (count,) = [ln for ln in layout.lines if ln.field == f"{rp}.cells[2]"]
            assert (count.text, count.fill, _fill_under(layout, count)) == (row["cells"][2], look.GREEN,
                                                                            look.GREEN_TINT), (where, j)
            runs = [ln for ln in layout.lines if ln.field == f"{rp}.cells[1]"]
            (pill,) = [ln for ln in runs if ln.text == nl.CG_PILL_WORD]
            assert (pill.fill, _fill_under(layout, pill)) == (look.WHITE, look.GREEN), (where, j)
            rest = [ln for ln in runs if ln is not pill]
            assert rest and all(ln.fill == look.INK2 for ln in rest), (where, j)


# ── 2026-11-20 content round: the server's width bound, the two-tone split, the Money Map and Form 4 roundup covers ──


def _positive_kerns_from_the_font(chars) -> Dict[str, int]:
    """Every pair of `chars` the face's GPOS `kern` feature can WIDEN, read from the font file (fontTools): the sum over
    the feature's lookups of each lookup's largest positive x-advance for the pair in any of its subtables."""
    from fontTools.ttLib import TTFont

    tt = TTFont(FONT)
    cmap, gpos = tt.getBestCmap(), tt["GPOS"].table
    lookups: List[int] = []
    for fr in gpos.FeatureList.FeatureRecord:
        if fr.FeatureTag == "kern":
            lookups += [li for li in fr.Feature.LookupListIndex if li not in lookups]
    subtables = []
    for li in sorted(lookups):
        lk = gpos.LookupList.Lookup[li]
        subs = [st.ExtSubTable if lk.LookupType == 9 else st for st in lk.SubTable]
        subtables.append([(st, {g: i for i, g in enumerate(st.Coverage.glyphs)}) for st in subs])
    out: Dict[str, int] = {}
    for a in chars:
        for b in chars:
            ga, gb, total = cmap[ord(a)], cmap[ord(b)], 0
            for subs in subtables:
                best = 0
                for st, cov in subs:
                    if ga not in cov:
                        continue
                    if st.Format == 1:
                        vals = [p.Value1 for p in st.PairSet[cov[ga]].PairValueRecord if p.SecondGlyph == gb]
                    else:
                        c1, c2 = st.ClassDef1.classDefs.get(ga, 0), st.ClassDef2.classDefs.get(gb, 0)
                        vals = [st.Class1Record[c1].Class2Record[c2].Value1]
                    best = max([best] + [getattr(v, "XAdvance", 0) or 0 for v in vals if v is not None])
                total += best
            if total > 0:
                out[a + b] = total
    return out


def test_r5_the_width_bound_is_pinned_to_the_font_and_to_pillow():
    """`text_metrics.HINTED_ADVANCES[22]` is Pillow's own hinted advance of every table character (BASIC layout: a string
    is exactly their sum — no `kern` table), `POSITIVE_KERNS` is every pair the font's GPOS `kern` feature can WIDEN
    (raqm applies it: "WW", "ty", "_j" … — Inter's kerning does not only narrow), and `MAX_PAIR_KERN` the widest any
    pair of the face's glyphs can be widened. A font or Pillow change fails here before the bound drifts."""
    from fontTools.ttLib import TTFont
    from PIL import ImageFont

    from app.services.marketing import text_metrics as tm

    chars = list(tm.INTER_BOLD_ADVANCES)
    assert set(tm.HINTED_ADVANCES) == {22} and set(tm.HINTED_ADVANCES[22]) == set(chars)
    basic = ImageFont.truetype(FONT, 22, layout_engine=ImageFont.Layout.BASIC)
    for ch in chars:
        assert basic.getlength(ch) == tm.HINTED_ADVANCES[22][ch], ch
    assert basic.getlength("Semiconductor Process Control") == sum(
        tm.HINTED_ADVANCES[22][c] for c in "Semiconductor Process Control")
    assert tm.POSITIVE_KERNS == _positive_kerns_from_the_font(chars)
    assert tm.POSITIVE_KERNS["WW"] == 45 and "Wo" not in tm.POSITIVE_KERNS      # widens / only narrows
    gpos = TTFont(FONT)["GPOS"].table
    worst = 0
    for fr in gpos.FeatureList.FeatureRecord:
        if fr.FeatureTag != "kern":
            continue
    lookups = sorted({li for fr in gpos.FeatureList.FeatureRecord if fr.FeatureTag == "kern"
                      for li in fr.Feature.LookupListIndex})
    for li in lookups:
        lk, best = gpos.LookupList.Lookup[li], 0
        for st in lk.SubTable:
            st = st.ExtSubTable if lk.LookupType == 9 else st
            if st.Format == 1:
                vals = [p.Value1 for ps in st.PairSet for p in ps.PairValueRecord]
            else:
                vals = [c2.Value1 for c1 in st.Class1Record for c2 in c1.Class2Record]
            best = max([best] + [getattr(v, "XAdvance", 0) or 0 for v in vals if v is not None])
        worst += best
    assert tm.MAX_PAIR_KERN == worst == 521


def _raqm_model_width(text: str, px: int) -> float:
    """What raqm (HarfBuzz) can draw `text` in at most, MODELLED from the font (this Mac's Pillow has no raqm): every
    glyph at the larger of its hinted and exact advance, every pair at its largest GPOS widening, each rounded UP to the
    1/64 px HarfBuzz positions on."""
    import math

    from PIL import ImageFont

    from app.services.marketing import text_metrics as tm

    basic = ImageFont.truetype(FONT, px, layout_engine=ImageFont.Layout.BASIC)
    q = lambda v: math.ceil(v * 64) / 64       # noqa: E731
    adv = sum(max(basic.getlength(c), q(tm.INTER_BOLD_ADVANCES[c] * px / 2048)) for c in text)
    return adv + sum(q(tm.POSITIVE_KERNS.get(a + b, 0) * px / 2048) for a, b in zip(text, text[1:]))


@pytest.mark.parametrize("text", [
    "Semiconductor Process Control", "Semiconductor Process Control · 91% of revenue", "WWWWWWWWWWWWWW",
    "Wwwwwwwwww Wwwwwwwwww Wwwwwwwwww", "Data Center · 88% of revenue", "Client Computing, FY2022 · 55% of revenue",
    "Twitty typewriter tty__jj rtrfrt", "Cadence Design Systems", "AVAVAVAV To Ty", "77 ff fw ty", "_j_j_j_j",
    string.ascii_letters + string.digits + " &.,'+-",
])
def test_r5_the_width_bound_holds_both_engines_and_is_tight(text):
    """`text_width` at the floor (22 px) is never under Pillow's BASIC width nor the RAQM model (above), and never more
    than a hair over the larger of them (1/64 px a glyph and a pair) — the old flat half-pixel-a-glyph allowance was 14
    px over KLA's segment and dropped its share. Mutation-checked by hand (2026-11-20): the old formula restored, the
    tightness assertion turned red; the kerning term removed, the "WW…" / "_j_j" / "tty" cases turned red on the raqm
    model."""
    from PIL import ImageFont

    from app.services.marketing import text_metrics as tm

    basic = ImageFont.truetype(FONT, 22, layout_engine=ImageFont.Layout.BASIC).getlength(text)
    raqm = _raqm_model_width(text, 22)
    bound = tm.text_width(text, 22)
    assert basic <= bound and raqm <= bound + 1e-9, (text, basic, raqm, bound)
    assert bound - max(basic, raqm) <= (2 * len(text)) / 64 + 0.01, (text, basic, raqm, bound)


def test_r5_a_klas_tile_keeps_its_share_with_its_filed_name_and_with_its_short_label():
    """KLA's tile at the floor, laid out by the WORKER: the filed name one line ("Semiconductor Process Control", 344 px
    of the 347 px column) and its short label's share line both draw, as the server now counts them. Final review
    (2026-11-20): the line-count check was `A and B or C` — "line 0 starts with the label" passed it alone, so a plan
    that split the share over more lines than the server budgeted stayed green; it is now exact, and the expected
    count is the SERVER's (`_tile_line`'s rule: 1 when the whole line fits, else the label's lines + 1)."""
    from app.services.marketing import news_templates as T

    px, w = T.THEME_TILE_FLOOR_PX, T.THEME_TILE_TEXT_W
    for label in ("Semiconductor Process Control", "Process Control"):
        line = T._tile_line(1, label, "91%")
        assert line == f"{label} · 91% of revenue"
        page = look.Page(look.POST_FRAME, cards.RAMP_STEPS, FONT, "basic")
        plan = nl._th_fact_plan(page, line, px, w, "tile")
        server_lines = 1 if T.text_metrics.line_count(line, px, w) == 1 else T.text_metrics.line_count(label, px, w) + 1
        assert len(plan) == server_lines == 2, (label, plan)
        assert [t for t, _c in plan[0]] == [label], (label, plan)
        assert [t for t, _c in plan[1]] == ["91%", " of revenue"], (label, plan)


@pytest.mark.parametrize("text, white, gold", [
    # the lesson's hook (the final build split it "A MOODY PARTNER OFFERS YOU A PRICE" / "EVERY DAY…")
    ("A moody partner offers you a price every day. You never have to take it.",
     "A moody partner offers you a price every day.", "You never have to take it."),
    # never after an honorific or an abbreviation ("MR." / "MARKET'S MOOD")
    ("Mr. Market's mood", "", "Mr. Market's mood"),
    ("Mr. Market's mood is not the business", "", "Mr. Market's mood is not the business"),
    ("Dr. Smith and St. Louis grew", "", "Dr. Smith and St. Louis grew"),
    ("The U.S. market grew. Prices moved.", "The U.S. market grew.", "Prices moved."),
    ("Mrs. Lee waited. Prices fell.", "Mrs. Lee waited.", "Prices fell."),
    # a clause boundary when there is no sentence one (the one nearest the middle)
    ("Every day, a moody partner names a price", "Every day,", "a moody partner names a price"),
    ("Buy low — sell high", "Buy low —", "sell high"),
    ("Prices swing; value stays", "Prices swing;", "value stays"),
    ("Why? Value matters", "Why?", "Value matters"),
    # the templates' own gold tails are unchanged
    ("How Costco makes money", "How Costco", "makes money"),
    ("At least 3 CEOs disclosed buying their own company's stock", "At least 3 CEOs disclosed",
     "buying their own company's stock"),
    ("Revenue", "", "Revenue"), ("Prices swing", "", "Prices swing"),
])
def test_r5_the_two_tone_split_falls_at_a_sentence_or_clause_boundary(text, white, gold):
    """2026-11-20: the gold part starts after a SENTENCE boundary, else a CLAUSE boundary, never after an honorific or
    an abbreviation; with neither, `two_tone` answers ("", text) and the headline draws ONE block whose last line is
    gold. Mutation-checked by hand (2026-11-20): the old "last half of the words" restored, the hook and "Mr." cases
    turned red; "mr." removed from NOT_SENTENCE_ENDS, the "Mr. Market's mood is …" case turned red."""
    assert look.two_tone(text) == (white, gold)


def _headline_colours(text: str, style=cards.TITLE_STYLE, width: float = 900.0, max_lines: int = 16):
    page = look.Page(look.VIDEO_FRAME, 0, FONT, "basic")
    look.headline(page, "title", text, style, 100, 400, width, max_lines=max_lines)
    return [(ln.text, "gold" if ln.fill == look.GOLD else "white") for ln in page.lines]


def test_r5_a_headline_without_a_boundary_is_one_block_with_its_last_line_gold():
    """The lesson picture's title (lesson.jpg: "MR. MARKET'S MOOD" white / "IS NOT THE BUSINESS" gold) is ONE balanced
    block, its last line gold; a one-line title is all gold (as a one-word one always was); a title with a sentence
    boundary keeps its two parts, the gold one starting at the boundary."""
    lines = _headline_colours("Mr. Market's mood is not the business", cards.IMAGE_TITLE_STYLE)
    assert lines == [("MR. MARKET'S MOOD", "white"), ("IS NOT THE BUSINESS", "gold")]
    assert _headline_colours("Mr. Market's mood") == [("MR. MARKET'S MOOD", "gold")]
    hook = _headline_colours("A moody partner offers you a price every day. You never have to take it.",
                             cards.HOOK_STYLE)
    whites = " ".join(t for t, c in hook if c == "white")
    golds = " ".join(t for t, c in hook if c == "gold")
    assert whites == "A MOODY PARTNER OFFERS YOU A PRICE EVERY DAY." and golds == "YOU NEVER HAVE TO TAKE IT."
    assert [c for _t, c in hook] == sorted([c for _t, c in hook], key=lambda c: c == "gold")   # white lines first
    # a title that IS a gold tail keeps every line gold, as before (the theme insiders' card "Disclosed purchases" wraps
    # to two lines at the card title's size): the last-line rule is for text with NO boundary only
    tail = _headline_colours("Disclosed purchases", cards.TITLE_STYLE, width=600)
    assert len(tail) == 2 and all(c == "gold" for _t, c in tail)


def _money_map_cover(name: str, figure: str, label: str, chip: str, engine: str):
    from app.services.marketing import news_templates as T

    card = {"kicker": "MONEY MAP", "logos": [chip], "chip": chip, "figure": figure, "figure_label": label,
            "headline": T.LEXICON["mm.img.title"].format(co=name)}
    return card, _lay_cover(card, {chip: name}, engine)


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_r5_the_money_map_cover_draws_its_label_under_the_figure_inside_its_card(engine):
    """The cover reads like the picture: the two-tone headline beside the plate (the TICKER in its gold pill), then the
    figure (GREEN a profit / RED a loss — `money_map_net_tone`) with the label under it, INSIDE the same warm card,
    centred; the worker declares exactly the server's strings in its order. Mutation-checked by hand (2026-11-20): with
    `_figure_card` drawing no label, the "body" assertions turned red."""
    from app.services.marketing import news_templates as T

    out = _sample_output("costco_money_map")
    logos = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in out["logo_refs"]]
    table = cards.logo_table({"logos": logos}, {})
    spec = cards.opening_spec(out["opening_card"], table, series="money_map", image_spec=out["image_spec"])
    assert (spec.figure, spec.body, spec.label, spec.tone) == (
        "$2.94", "of every $100 of revenue was net income", "COST", "gain")
    layout = cards.layout_card(spec, font_path=FONT, layout_engine=engine)
    fig = [ln for ln in layout.lines if ln.field == "figure"]
    body = [ln for ln in layout.lines if ln.field == "body"]
    assert fig and body and all(ln.fill == look.GREEN for ln in fig) and all(ln.fill == look.INK for ln in body)
    assert " ".join(ln.text for ln in body) == spec.body
    assert min(ln.ink[1] for ln in body) > max(ln.ink[3] for ln in fig)                       # under the figure
    card = next(r.box for r in layout.rects if r.fill == look.CARD and _within(fig[0].ink, r.box))
    assert all(_within(ln.ink, card) for ln in body)                                           # inside its card
    names = {a.name for a in spec.logos}
    assert [x for x in cards.onscreen_strings(spec) if x not in names][:5] == [
        "MONEY MAP", "COST", "$2.94", spec.body, "How Costco makes money"]
    allowed = tos.opening_strings(out["opening_card"], logos)
    assert set(cards.onscreen_strings(spec)) - {cards.BRAND_ROW_WORD} <= set(allowed)
    loss = _sample_output("rivian_money_map")
    lspec = cards.opening_spec(loss["opening_card"], cards.logo_table({"logos": [
        {"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in loss["logo_refs"]]}, {}),
        series="money_map", image_spec=loss["image_spec"])
    llay = cards.layout_card(lspec, font_path=FONT, layout_engine=engine)
    assert lspec.tone == "loss" and all(ln.fill == look.RED for ln in llay.lines if ln.field == "figure")
    assert T.LEXICON["mm.open.loss"] == lspec.body


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_r5_the_money_map_cover_fits_at_its_widest(engine):
    """The tallest Money Map cover the server can write: a 32-character company name packed into the most headline
    lines at the step before the floor (the exact DP), and the widest word a name may hold, under the widest figures
    ("under $0.01", "$100.00") with the longer label, the widest ticker chip — before the floor step, every string
    whole. Anti-vacuity: the same cover with a label five times as long does not fit before the floor."""
    from app.services.marketing import company_news_rules as R
    from app.services.marketing import news_templates as T

    step = cards.RAMP_STEPS - 1
    font = cards._font(FONT, cards.OPENING_HEADLINE_STYLE.size(step), engine)
    before, after = T.LEXICON["mm.img.title"].split("{co}")
    _n, packed = _worst_filer(R.COMPANY_NAME_CHARS[1], before=before, after=after, own_glue=False, font=font,
                              width=_COVER_COLUMN)
    for name in (packed, "W" * T.NAME_WORD_MAX_CHARS + " " + "W" * 11):
        for figure in (T.SUB_CENT, "$100.00"):
            for label_id in ("mm.open.profit", "mm.open.loss"):
                card, layout = _money_map_cover(name, figure, T.LEXICON[label_id], "WWWWW-W", engine)
                # before the floor under BASIC (this Mac); RAQM, the production worker's engine, needs the floor step
                # itself for the worst of them (measured 2026-11-26 in a Debian bookworm container) — it still fits
                assert layout.step < cards.RAMP_STEPS + (engine == "raqm"), (name, figure, layout.step)
                _assert_the_chip_fits(card, {"WWWWW-W": name}, engine)
                assert " ".join(layout.field_lines("body")) == T.LEXICON[label_id]
                assert " ".join(layout.field_lines("label")) == "WWWWW-W"
    # (built past the server's cap on purpose: the worker's own layout, no server check)
    big = {"kicker": "MONEY MAP", "logos": ["WWWWW-W"], "chip": "WWWWW-W", "figure": "$100.00",
           "figure_label": " ".join([T.LEXICON["mm.open.profit"]] * 5),
           "headline": T.LEXICON["mm.img.title"].format(co=packed)}
    table = cards.logo_table({"logos": [{"key": "WWWWW-W", "name": packed, "url": None, "sha256": None}]}, {})
    with pytest.raises(ValueError, match="figure_label is over 60"):
        cards.opening_spec(big, table)                         # the worker refuses it too (contract drift)
    spec = replace(cards.opening_spec(dict(big, figure_label=T.LEXICON["mm.open.profit"]), table),
                   body=big["figure_label"])
    try:
        step = cards.layout_card(spec, font_path=FONT, layout_engine=engine).step
    except cards.CardOverflow:
        step = cards.RAMP_STEPS + 1
    # Under RAQM the worst cover itself takes the floor step, so there the five-times label must not fit at all.
    assert step >= cards.RAMP_STEPS + (engine == "raqm"), (engine, step)


def _ins_worst_roundup(n_rows: int, role: str):
    from app.services.marketing import company_news_rules as R

    names = [_W32_WORDS, _W32, "W" * 20 + " " + "M" * 11, "M" * 20 + " " + "W" * 11, "Wwwwwwwwww Wwwwwwwwww Wwwwwwwwwm",
             "Mwwwwwwwww Wwwwwwwwww Wwwwwwwwww", "Wwwwwwwwww Mwwwwwwwww Wwwwwwwwww", "Wwwwwwwwww Wwwwwwwwww Mwwwwwwwww"]
    syms = ["WWWWA", "WWWWB", "WWWWC", "WWWWD", "WWWWE", "WWWWF", "WWWWG", "WWWWH"]
    rows = tuple(R.InsiderPurchase(company=R.CompanyRef(syms[i], names[i]), role=role, person_name=None,
                                   amount_usd=999_000_000.0 - i * 1_000_000.0, shares=900_000.0, purchases=3,
                                   earliest_trade_date=date(2026, 11, 10), latest_trade_date=date(2026, 11, 12),
                                   filing_dates=(date(2026, 11, 12),), holding="mixed", amended=True)
                 for i in range(n_rows))
    series = "ceo_buys" if role in ("ceo",) else "insider_buys"
    return R.InsiderBuysWeek(series=series, window_start=date(2026, 11, 9), window_end=date(2026, 11, 15), rows=rows)


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("n_rows, role", [(3, "ceo"), (3, "director"), (5, "director"), (5, "ceo")])
def test_r5_the_form4_roundup_cover_is_its_list_and_fits_at_its_widest(n_rows, role, engine):
    """The roundup's cover (RESTYLE_SPEC §1, the 13F cover's idea; owner decisions 2026-11-20 "All rows, compact") at the
    server's widest — 32-character names of the widest letters, the widest amounts, the three-line headline, every row
    (3-5: the owner's "at most 5", every one drawn since the final review of 2026-11-26, so no "+N more") — lays out
    before the floor step as the LIST (never today's plain card as a fallback), drawing exactly the server's strings:
    the headline, each row's chip, name, role and GREEN amount, and the short footer."""
    from app.services.marketing import news_templates as T

    out = _compose(_ins_worst_roundup(n_rows, role), date(2026, 11, 16))
    card = out["opening_card"]
    assert len(card["list"]["rows"]) == min(n_rows, T.INS_COVER_ROWS)
    assert ("more" in card["list"]) == (n_rows > T.INS_COVER_ROWS)
    _lay_out_list_post(out, engine)
    image, table = _f13_image(out)
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    cover = cards.cards_for_script(script, table, font_path=FONT, layout_engine=engine)[0]
    assert cover.image is not None and cover.image.series == out["series"]
    layout = cards.layout_card(cover, font_path=FONT, layout_engine=engine)
    amounts = [ln for ln in layout.lines if ln.field.endswith(".cells[2]")]
    assert len(amounts) == len(card["list"]["rows"]) and all(ln.fill == look.GREEN for ln in amounts)
    allowed = set(tos.opening_strings(card, script["logos"])) | {out["image_footer"], cards.BRAND_ROW_WORD}
    assert set(cards.onscreen_strings(cover)) <= allowed
    assert card["headline"] in cards.onscreen_strings(cover)


# ── visual-fidelity round 2 (2026-11-20, the items the phase-4 fixer never saw) ──────────────────────────────────────
#
# Each test pins one measured difference against its approved mock (FINAL_REFERENCE), mutation-checked by hand once:
# the fix reverted turns it red.


def _dark_tile(size: int = 200, *, flat: bool = False, rgb_top=(57, 79, 92), rgb_bottom=(34, 160, 148),
               mark: str = "band") -> "Image.Image":
    """An opaque square logo tile — Rapid7's shape: a vertical gradient (or, `flat`, one colour: Berkshire's navy
    plate) under a white wordmark band across the middle (`mark` "band"), or a white mark in a CORNER ("corner")."""
    from PIL import ImageDraw

    im = Image.new("RGBA", (size, size), rgb_top + (255,))
    d = ImageDraw.Draw(im)
    if not flat:
        for y in range(size):
            t = y / (size - 1)
            d.line([(0, y), (size - 1, y)], fill=tuple(int(round(a + (b - a) * t)) for a, b in zip(rgb_top, rgb_bottom))
                   + (255,))
    if mark == "band":
        d.rectangle((int(size * 0.10), int(size * 0.44), int(size * 0.90), int(size * 0.56)), fill=(255, 255, 255, 255))
    else:
        d.rectangle((2, 2, int(size * 0.14), int(size * 0.14)), fill=(255, 255, 255, 255))
    return im


def test_fidelity2_a_dark_image_tile_is_classified_by_its_shape_and_tone_never_by_company():
    """mock_theme_insiders `circle_tile` (Rapid7: "a full-bleed square tile: it becomes the disc"): an opaque, square,
    DARK picture that is not one flat plate. A flat dark plate (Berkshire, f13_list_berkshire.jpg) and a mid-tone tile
    (Vertiv's orange, theme_biggest_power.jpg) keep their approved treatment: whole on the white disc."""
    assert lg.dark_image_tile(_dark_tile()) is not None
    assert lg.dark_image_tile(_dark_tile(flat=True, rgb_top=(43, 49, 145))) is None          # a flat plate
    assert lg.dark_image_tile(_dark_tile(rgb_top=(251, 106, 6), rgb_bottom=(248, 82, 12))) is None   # mid-tone
    assert lg.dark_image_tile(_dark_tile().resize((200, 120))) is None                       # not square
    clear = _dark_tile()
    clear.putpixel((0, 0), (0, 0, 0, 0))
    assert lg.dark_image_tile(clear) is None                                                 # not opaque


def test_fidelity2_a_dark_image_tile_fills_its_disc_and_keeps_every_mark_inside_the_rim(tmp_path):
    """theme_insiders_cyber.jpg: Rapid7's tile IS the disc (it was a small square on the white disc). The tile covers
    the disc to its rim (a point just inside the rim is the tile's ground, never the white disc) and its white mark is
    drawn whole, inside the rim's inner edge — at the scorecard's small disc (r 28) and the cover's (r 56)."""
    path = tmp_path / "tile.png"
    _dark_tile().save(path)
    art = cards.LogoArt("RPD", "Rapid7", str(path))
    for r in (nl.TH_LEDGER_DISC, 56):
        canvas = Image.new("RGB", (4 * r, 4 * r), look.CARD)
        box = (r, r, 3 * r, 3 * r)
        _draw_disc(canvas, box, art)
        cx = cy = 2 * r
        # just inside the rim, off the wordmark band: the top, the bottom and the upper-left edge
        for x, y in ((cx, box[1] + look.DISC_RIM + 2), (cx, box[3] - look.DISC_RIM - 3),
                     (int(cx - 0.866 * r) + look.DISC_RIM + 2, cy - r // 2)):
            px = canvas.getpixel((x, y))
            assert max(px) < 200, (r, (x, y), px)                      # the tile's dark ground, not the white disc
        a = np.asarray(canvas).astype(int)
        yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
        dist = np.hypot(xx + 0.5 - cx, yy + 0.5 - cy)
        white = (a.min(axis=2) >= 245) & (dist <= r)                # the mark's white, on the disc (rim included)
        assert int(white.sum()) > 20
        assert float(dist[white].max()) <= r - look.DISC_RIM + 0.5, r


def test_fidelity2_a_tile_whose_mark_reaches_its_corner_keeps_the_white_disc(tmp_path):
    """Never a mark under the rim: a tile whose white mark sits in a corner cannot cover the disc with the mark inside
    it (`image_tile_scale` is None) and is drawn as before, whole on the white disc."""
    rgba = _dark_tile(mark="corner")
    assert lg.dark_image_tile(rgba) is not None and look.image_tile_scale(rgba, 56) is None
    path = tmp_path / "corner.png"
    rgba.save(path)
    canvas = Image.new("RGB", (112, 112), look.CARD)
    _draw_disc(canvas, (28, 28, 84, 84), cards.LogoArt("X", "X", str(path)))
    assert canvas.getpixel((28 + look.DISC_RIM + 2, 56)) == look.WHITE


def test_fidelity2_one_logo_rule_for_every_family_the_form4_mocks_fill():
    """The approved mocks disagree on Starbucks' seal — mock_form4 LOGO_FILL 0.88, mock_earnings_multi 0.72. ONE rule
    stays (LOGO_REACH, the Form 4 mock's fill): the same logo fills the same share of its disc in every family, the
    earnings table's small disc (ER_DISC_S, r 36) included — never a per-family fill."""
    rgba = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    from PIL import ImageDraw

    ImageDraw.Draw(rgba).ellipse((5, 5, 195, 195), fill=(0, 112, 74, 255))
    rgba = rgba.crop(rgba.getchannel("A").getbbox())
    fills = {r: look.logo_scale(rgba, 2 * r) * rgba.width / (2 * r)
             for r in (nl.ER_DISC_S.size(0), nl.ER_DISC_C.size(0), nl.ROW_DISC.size(0), nl.F13_DISC.size(0))}
    assert min(fills.values()) >= 0.83 and max(fills.values()) - min(fills.values()) <= 0.02, fills


def _ledger_with_empty_cells():
    out = json.loads(json.dumps(_compose(_table_worst("ledger", 6), date(2026, 11, 25))))
    out["image_spec"]["rows"][0]["sells"] = cards.EMPTY_CELL
    out["opening_card"]["list"]["rows"][0]["sells"] = cards.EMPTY_CELL
    return out


def test_fidelity2_a_ledger_cell_with_no_filing_is_blank_on_the_picture_and_the_cover():
    """theme_insiders_cyber.jpg leaves a cell with no filing BLANK; the worker drew an INK2 "—". The spec's
    EMPTY_CELL is a marker: never drawn, never declared — on the picture and on its cover (the server allows it
    nowhere: test_marketing_template_onscreen)."""
    out = _ledger_with_empty_cells()
    image, table = _f13_image(out)
    assert cards.EMPTY_CELL not in image.strings
    lay = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert not [ln for ln in lay.lines if ln.text == cards.EMPTY_CELL or ln.field == "rows[0].sells"]
    assert len([r for r in lay.rects if r.fill == look.RED_TINT]) == 5          # the other five rows' sales tags only
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    cover = cards.cards_for_script(script, table, font_path=FONT, layout_engine="basic")[0]
    assert cover.image is not None and cards.EMPTY_CELL not in cards.onscreen_strings(cover)
    cl = cards.layout_card(cover, font_path=FONT, layout_engine="basic")
    assert not [ln for ln in cl.lines if ln.text == cards.EMPTY_CELL]
    assert set(cards.onscreen_strings(cover)) <= (set(tos.opening_strings(out["opening_card"], script["logos"]))
                                                  | {out["image_footer"]} | set(sch.VIDEO_BRAND_TEXT))


def _tally_record():
    cos = [_co("CTSO", "Contoso"), _co("FBKM", "Fabrikam Defense"), _co("NWND", "Northwind"), _co("LTW", "Litware"),
           _co("ADVW", "Adventure Works")]
    members = tuple(R.ThemeMember(company=c, top_segment=None, top_segment_share=None, fiscal_year=None,
                                  congress_members=6 - i) for i, c in enumerate(cos))
    return R.ThemeExplainer(series="theme_explainer", slug="modern-battlefield", title="Modern Battlefield",
                            members=members, tickers_as_of=date(2026, 11, 18), theme_size=16, angle="congress",
                            posted_on=date(2026, 11, 25), month="2026-10")


def test_fidelity2_a_tally_count_and_its_unit_are_12px_apart_ink_to_ink():
    """mock_theme_congress.row_card: the count's ink box, `gap = 12`, then the INK box of "members" (it rises to its
    "b"). The worker spaced the unit's x-height: 8 px between the "6" and the "b" (theme_congress_defense.jpg: 12)."""
    out = _compose(_tally_record(), date(2026, 11, 25))
    image, _table = _f13_image(out)
    lay = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert lay.step == 0
    counts = {ln.field: ln for ln in lay.lines if ln.field.endswith(".count")}
    units = [ln for ln in lay.lines if ln.field == "unit" or ln.field.startswith("unit#")]
    assert len(counts) == len(units) == 5
    for i, unit in enumerate(units):
        count = counts[f"rows[{i}].count"]
        assert unit.ink[1] - count.ink[3] == nl.TH_TALLY_INK_GAP == 12, (i, count.ink, unit.ink)


def _grid_record():
    syms = [f"{a}{b}" for a in "ABCDEF" for b in "AB"]
    members = tuple(R.ThemeMember(company=_co(s, f"Contoso {s}"), top_segment="Data Center", top_segment_share=0.88,
                                  fiscal_year="2026") for s in syms)
    return R.ThemeExplainer(series="theme_explainer", slug="ai-chips", title="AI Chips", members=members,
                            tickers_as_of=date(2026, 11, 1))


def test_fidelity2_a_grid_tiles_name_hangs_8px_under_its_chip():
    """mock_theme.tile_layout: the name drawn "la" 2 px under the chip — its cap top 8 px under the chip's foot
    (theme_ai_chips.jpg). The worker put the name's cap top 4-5 px under it."""
    out = _compose(_grid_record(), date(2026, 11, 25))
    image, _table = _f13_image(out)
    lay = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    assert lay.step == 0
    from PIL import ImageFont

    chips = [r for r in lay.rects if r.fill == look.NAVY_CHIP]
    seen = 0
    for i in range(len(out["image_spec"]["tiles"])):
        (chip_ln,) = [ln for ln in lay.lines if ln.field == f"tiles[{i}].chip"]
        name = [ln for ln in lay.lines if ln.field == f"tiles[{i}].name"][0]
        (box,) = [r.box for r in chips if r.box[0] <= chip_ln.ink[0] and chip_ln.ink[2] <= r.box[2]
                  and r.box[1] <= chip_ln.ink[1] and chip_ln.ink[3] <= r.box[3]]
        cap_top = name.y + ImageFont.truetype(FONT, name.size).getbbox("H", anchor="ls")[1]
        assert cap_top - box[3] == 8, (i, box, cap_top)
        seen += 1
    assert seen >= 4


def test_fidelity2_a_theme_cover_breaks_its_sub_line_where_the_picture_does():
    """theme_ai_chips.jpg breaks its sub line by hand ("21 companies · each company's largest business" / "and its
    share of total revenue · fiscal …", the spec's `sub_break`); the cover's heading — the same string — broke "… and
    its" / "share of total revenue …". It breaks at the picture's break now."""
    out = _compose(_grid_record(), date(2026, 11, 25))
    spec = out["image_spec"]
    brk = spec["sub_break"]
    words = spec["subtitle"].split()
    want = [" ".join(words[:brk]), " ".join(words[brk:])]
    image, table = _f13_image(out)
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    cover = cards.cards_for_script(script, table, font_path=FONT, layout_engine="basic")[0]
    assert out["opening_card"]["list"]["heading"] == spec["subtitle"] and cover.image.sub_break == brk
    cl = cards.layout_card(cover, font_path=FONT, layout_engine="basic")
    assert [ln.text for ln in cl.lines if ln.field == "sections[0].heading"] == want
    # a cover whose heading is not the picture's subtitle takes no break from it
    assert nl._picture_sub_break(spec, spec["subtitle"] + " x") is None and nl._picture_sub_break(None, "a b") is None


def test_fidelity2_a_theme_sub_line_breaks_only_after_a_separator_when_its_phrases_fit():
    """A sub line too wide for one line ("12 of its 21 companies · revenue growth and profit · latest fiscal year" on
    the 9:16 cover) broke mid-phrase ("… · revenue" / "growth and profit …"). It breaks only after a " · " now — the
    separator ends its line, every word drawn in order — and word-wraps only when no such split fits."""
    page = look.Page(look.VIDEO_FRAME, 0, FONT, "basic")
    text = "12 of its 21 companies · revenue growth and profit · latest fiscal year"
    room = page.length(text, 30) - 40
    lines = nl._th_sub_lines(page, text, None, 30, room)
    assert lines is not None and len(lines) == 2 and " ".join(lines) == text
    assert all(ln.endswith(nl.SUB_SEPARATOR) for ln in lines[:-1]), lines
    assert nl._th_sub_lines(page, text, None, 30, page.length(text, 30) + 1) == [text]
    # a phrase wider than the room: the word wrap, never a refusal
    long = "21 companies · each company's largest business and its share of total revenue · fiscal 2025"
    got = nl._th_sub_lines(page, long, None, 30, 520)
    assert got is not None and " ".join(got) == long


def _congress_out(others: int):
    import test_marketing_news_templates as NT

    return _compose(NT.congress(others=others), NT.CONGRESS_RUN)


def test_fidelity2_two_congress_cards_take_the_pictures_room_as_three_do():
    """mock_congress_multi: `h = (bottom − top − gaps) / n` — the cards take the room between the sub line and the note.
    Two companies' cards were capped at 300 px and left ~63 px of empty navy above and below them; now they take it,
    the count tile spans the card's height and is never wider than a CG_CARD_CAP card's square (the name keeps its
    column; it narrows further when a line needs it — the `test_final_*` Congress tests below)."""
    gaps = {}
    for others in (1, 2):
        image, _table = _f13_image(_congress_out(others))
        lay = nl.layout_template(image, font_path=FONT, layout_engine="basic")
        cards_ = sorted((r.box for r in lay.rects if r.fill == look.CARD), key=lambda b: b[1])
        notes = min(r.box[1] for r in lay.rects if r.fill == look.NOTE_PANEL)
        sub_bottom = max(ln.ink[3] for ln in lay.lines if ln.field == "subtitle")
        tiles = [r.box for r in lay.rects if r.fill == look.GREEN_TINT]
        assert len(cards_) == len(tiles) == others + 1
        gaps[others + 1] = (cards_[0][1] - sub_bottom, notes - cards_[-1][3])
        for (x0, y0, x1, y1), card in zip(tiles, cards_):
            assert y1 - y0 == (card[3] - card[1]) - 2 * nl.CG_PAD                 # the tile spans the card
            assert x1 - x0 <= nl.CG_CARD_CAP - 2 * nl.CG_PAD
    assert all(abs(a - b) <= 1 for a, b in zip(gaps[2], gaps[3])), gaps         # no band above or below


def _congress_line_plans(lines: Sequence[Any], n: int) -> List[Tuple[str, ...]]:
    """Each Congress card's line as DRAWN: its runs (`_cg_line` draws the words before the pill, the pill's word and the
    rest as separate lines on one baseline) joined per baseline, top to bottom."""
    out = []
    for j in range(n):
        by_base: Dict[int, List[Any]] = {}
        for ln in lines:
            if ln.field == f"sections[0].rows[{j}].cells[1]":
                by_base.setdefault(ln.y, []).append(ln)
        out.append(tuple(" ".join(r.text for r in sorted(runs, key=lambda r: r.x))
                         for _y, runs in sorted(by_base.items())))
    return out


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("names", [
    (("GD", "General Dynamics"), ("NOC", "Northrop Grumman")),
    (("ACN", "Accenture"), ("BAH", "Booz Allen Hamilton")),
    (("ACN", "Accenture"), ("LMT", "Lockheed Martin")),                 # the shipped sample's pair: its cover broke
    (("GD", "General Dynamics"), ("NOC", "Northrop Grumman"), ("BAH", "Booz Allen Hamilton")),
], ids=["gd_noc", "acn_bah", "acn_lmt", "three"])
def test_final_every_congress_card_keeps_its_line_on_two_lines_on_the_picture_and_the_cover(engine, names):
    """Final review (2026-11-20): a two-company card's count tile is a CG_CARD_CAP square (248 px), so its text column
    was ~432 px on the picture and ~384 on the cover, and a defence name fell to three lines — "purchases of General" /
    "Dynamics stock", "purchases of Accenture" / "stock" (the cover of the shipped Accenture + Lockheed Martin pair).
    The tile now narrows, down to CG_TILE_NARROW_FLOOR, before three lines are tried: every card reads "members
    disclosed" / "purchases of <name> stock" on the picture AND the cover; a three-company card's tile is untouched."""
    import test_marketing_news_templates as NT

    counts = tuple(R.CongressCount(series="congress_count", company=_co(sym, name), month="2026-11", members=m,
                                   fetched_on=NT.CONGRESS_RUN) for (sym, name), m in zip(names, (5, 3, 2)))
    rec = R.CongressMonth(series="congress_count", month="2026-11", fetched_on=NT.CONGRESS_RUN, counts=counts)
    out = _compose(rec, NT.CONGRESS_RUN)
    image, table = _f13_image(out)
    picture = nl.layout_template(image, font_path=FONT, layout_engine=engine)
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    cover = cards.layout_card(cards.cards_for_script(script, table)[0], font_path=FONT, layout_engine=engine)
    want = [("members disclosed", f"purchases of {name} stock") for _sym, name in names]
    for what, lay in (("picture", picture), ("cover", cover)):
        assert _congress_line_plans(lay.lines, len(names)) == want, (what, names)
        tiles = [r.box[2] - r.box[0] for r in lay.rects if r.fill == look.GREEN_TINT]
        assert len(tiles) == len(names)
        assert all(nl.CG_TILE_NARROW_FLOOR <= t <= nl.CG_CARD_CAP - 2 * nl.CG_PAD for t in tiles), (what, tiles)
    if len(names) == 3:                                                     # a three-company card never narrows
        assert [r.box[2] - r.box[0] for r in picture.rects if r.fill == look.GREEN_TINT] == [183] * 3


@pytest.mark.parametrize("engine", _F13_ENGINES)
def test_final_a_three_line_congress_card_keeps_of_its_company_stock_whole(engine):
    """Final review (2026-11-20): when one card's name is too long for two lines even at CG_TILE_NARROW_FLOOR, every card
    takes three — and the greedy wrap broke the OTHER cards' names or left "stock" alone ("purchases of General
    Dynamics" / "stock"). The pill's word now stands alone and "of <Co> stock" stays whole when it fits one line; only a
    name too long for that wraps, never leaving one word on its last line. Every word drawn, in order."""
    import test_marketing_news_templates as NT

    names = (("AAA", "Booz Allen Hamilton Holding Corp"), ("GD", "General Dynamics"))  # 32 characters: the bound
    counts = tuple(R.CongressCount(series="congress_count", company=_co(sym, name), month="2026-11", members=m,
                                   fetched_on=NT.CONGRESS_RUN) for (sym, name), m in zip(names, (5, 2)))
    rec = R.CongressMonth(series="congress_count", month="2026-11", fetched_on=NT.CONGRESS_RUN, counts=counts)
    out = _compose(rec, NT.CONGRESS_RUN)
    image, table = _f13_image(out)
    picture = nl.layout_template(image, font_path=FONT, layout_engine=engine)
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    cover = cards.layout_card(cards.cards_for_script(script, table)[0], font_path=FONT, layout_engine=engine)
    for what, lay in (("picture", picture), ("cover", cover)):
        plans = _congress_line_plans(lay.lines, 2)
        assert plans[1] == ("members disclosed", "purchases", "of General Dynamics stock"), (what, plans)
        for (_sym, name), plan in zip(names, plans):
            assert len(plan) == 3 and plan[0] == "members disclosed" and plan[1].split()[0] == "purchases"
            assert " ".join(plan) == f"members disclosed purchases of {name} stock", (what, plan)
            assert len(plan[-1].split()) >= 2, (what, plan)                # never one word alone
        tiles = [r.box[2] - r.box[0] for r in lay.rects if r.fill == look.GREEN_TINT]
        assert tiles == [nl.CG_TILE_NARROW_FLOOR] * 2, (what, tiles)      # narrowed as far as it goes first


def test_fidelity2_the_insider_spotlight_without_art_is_balanced_on_the_page():
    """spot_insider.jpg draws the art band above the disc; without art the block rested on the footer and left ~260
    px of plain page under the brand row. It is balanced now: as much air over the disc's ring as under the card."""
    image, lay = _frozen_layout("feature", "isp_cluster")
    assert image.art is None and lay.art is None
    row_bottom = max(lay.brand[3], lay.pill[3])
    ring_top = min(d.box[1] for d in lay.discs) - look.SOLID_RING_W
    card_bottom = max(r.box[3] for r in lay.rects if r.fill == look.CARD)
    above, below = ring_top - row_bottom, lay.footer_top - card_bottom
    assert abs(above - below) <= 2 and below >= nl.FEATURE_FOOT_GAP, (above, below)


def test_fidelity2_a_card_never_breaks_inside_a_date():
    """A stake's text card read "Invested $5B · as of Dec 26," / "2025": a DATE is one unit for every wrap (greedy and
    balanced) — "Dec 26, 2025", "Nov 16–17, 2026", "Aug 27–Nov 24, 2026", "November 2026", "(Dec 29, 2025)" — never
    "may 10" (the verb); a date wider than the column falls back to its words, never a refusal."""
    assert look.date_units("Invested $5B · as of Dec 26, 2025".split())[-1] == "Dec 26, 2025"
    assert look.date_units("filed Aug 27–Nov 24, 2026 ok".split()) == ["filed", "Aug 27–Nov 24, 2026", "ok"]
    assert look.date_units("November 2026 · as of (Dec 29, 2025)".split()) == ["November 2026", "·", "as", "of",
                                                                                 "(Dec 29, 2025)"]
    assert look.date_units("you may 10 x".split()) == ["you", "may", "10", "x"]
    # the balanced headline's units (its exact narrowest split works on them) keep a date whole beside "at least N"
    assert look.no_break_units("at least 2 filed on Dec 26, 2025".split()) == ["at least 2", "filed", "on",
                                                                                "Dec 26, 2025"]
    font = look._font(FONT, 46, "basic")
    text = "Invested $5B · as of Dec 26, 2025"
    width = font.getlength("Invested $5B · as of Dec 26,") + 2
    lines = look.wrap(text.split(), font, width, 4, "body")
    assert lines == ["Invested $5B · as of", "Dec 26, 2025"]
    assert look.balanced(text.split(), font, width, 4, "body")[-1].endswith("Dec 26, 2025")
    narrow = font.getlength("Dec 26,") + 2                                       # the date alone does not fit
    assert look.wrap("Dec 26, 2025".split(), font, narrow, 4, "body") == ["Dec 26,", "2025"]


# ── owner decisions 2026-11-20: the insider LIST and the Form 4 covers, laid out by the worker ─────────────────────────
# "for insider trade, i think we discussed that there is no single company already, each post or each image/video will
# have more than 2,3, ideally are 3 - 5 companies at once." / video cover "All rows, compact (Recommended)". The insider
# list (insider_multi3.jpg / insider_multi5.jpg / insider_multi5_alt_with_note.jpg) and every Form 4 cover
# (insider_multi5_cover_alt_all5.jpg) at the server's widest: 32-character names of the widest glyphs (and 20-letter
# words), ten-character chips' worth of symbol, "CEO + at least 4 other insiders", the longest detail line (a four-digit
# cost range in thousands), five rows with the held-indirectly note.


def _il_widest(n: int, *, held: bool = False) -> Any:
    """The widest insider list of `n` companies the server composes (each row a CEO and four other insiders)."""
    from app.services.marketing import company_news_rules as R

    names = ["Wwwwwwwwww Wwwwwwwwww Wwwwwwwww" + c for c in "WMQOGDHN"]
    names[1] = _W32_WORDS
    day = date(2026, 11, 18)

    def person(sym, name, role, amount, cost, holding="direct"):
        return R.InsiderPurchase(company=R.CompanyRef(sym, name), role=role, person_name=None, amount_usd=amount,
                                 shares=round(amount / cost[1], 4), purchases=3, earliest_trade_date=day,
                                 latest_trade_date=day, filing_dates=(day,), holding=holding, amended=False,
                                 cost_per_share_min_usd=cost[0], cost_per_share_max_usd=cost[1])

    rows = []
    for i in range(n):
        sym, name = f"WWW{'ABCDEFGH'[i]}", names[i]       # four letters: a fifth would fold to one issuer root
        lead = person(sym, name, "ceo", 987_654_321.0 - i * 1_000_000.0, (1_142.10, 1_143.85),
                      holding="indirect" if held and i == 0 else "direct")
        others = tuple(person(sym, name, role, 900_000_000.0 - k * 1_000_000.0 - i * 1_000.0, (1_000.0, 1_000.0))
                       for k, role in enumerate(("cfo", "director", "director", "director")))
        rows.append(R.InsiderSpotlightCompany(lead=lead, others=others))
    rows.sort(key=R.spotlight_company_order)
    return R.InsiderSpotlight(series="insider_spotlight", window_start=date(2026, 11, 17), window_end=day,
                              companies=tuple(rows))


def _il_cases() -> Dict[str, Tuple[Any, date]]:
    import test_marketing_news_templates as NT

    cases = {n: (f(), d) for n, f, d in NT.SAMPLES if n.startswith("isp_")}
    # owner decisions 2026-11-20, held to "at most 5" by the final review of 2026-11-26: a list is 3-5 companies, every
    # one drawn — no insider list carries a "+N more" line any more (the 13F list of eight still does: _F13 cases)
    cases.update({f"isp_n{k}": (NT.isp_n(k), NT.SPOT_THU) for k in range(R.INSIDER_MIN_ROWS, R.INSIDER_MAX_ROWS + 1)})
    cases.update({f"widest{k}": (_il_widest(k), NT.SPOT_THU) for k in (3, 5)})
    cases["widest5_held"] = (_il_widest(5, held=True), NT.SPOT_THU)
    return cases


_IL_OUTPUTS = {name: _compose(rec, d) for name, (rec, d) in _il_cases().items()}


def test_il_the_widest_cases_are_what_they_claim():
    """Anti-vacuity: the widest rows really carry the longest strings the server writes — 32-character names, the four-
    other-insiders roles line, a detail line with a four-digit cost range — and the five-row held case keeps its NOTE on
    the picture (insider_multi5_alt_with_note.jpg)."""
    w = _IL_OUTPUTS["widest5"]["image_spec"]
    rows = w["sections"][0]["rows"]
    assert len(rows) == 5 and {len(r["cells"][0]) for r in rows} == {32}
    assert {r["cells"][1] for r in rows} == {"CEO + at least 4 other insiders"}
    assert all(r["detail"].endswith("$1,000.00–$1,143.85 a share, as filed") for r in rows), rows[0]["detail"]
    assert max(len(r["detail"]) for r in rows) <= tos.ROW_DETAIL_MAX_CHARS
    held = _IL_OUTPUTS["widest5_held"]["image_spec"]
    assert held.get("notes") and len(held["sections"][0]["rows"]) == 5
    assert not any("more" in o["image_spec"]["sections"][0] for o in _IL_OUTPUTS.values())   # 3-5: every row drawn


@pytest.mark.parametrize("engine", _F13_ENGINES)
@pytest.mark.parametrize("name", sorted(_IL_OUTPUTS))
def test_il_every_insider_list_picture_and_cover_fits_the_worker(engine, name):
    """The picture lays out before the floor step with every string drawn whole — the detail line on each PICTURE row,
    the note panel at five rows — and the compact COVER lays out too, drawing the same rows WITHOUT a detail line (no
    cost per share anywhere on the video: owner decision 5) and only the server's opening strings."""
    out = _IL_OUTPUTS[name]
    _lay_out_list_post(out, engine)
    image, table = _f13_image(out)
    picture = nl.layout_template(image, font_path=FONT, layout_engine=engine)
    rows = out["image_spec"]["sections"][0]["rows"]
    for j, row in enumerate(rows):
        assert picture.field_lines(f"sections[0].rows[{j}].detail") == [row["detail"]], (name, j)
    if out["image_spec"].get("notes"):
        assert picture.field_lines("notes[0]"), name
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    cover = cards.cards_for_script(script, table, font_path=FONT, layout_engine=engine)[0]
    assert cover.image is not None and nl._is_insider_list(cover.image)
    layout = cards.layout_card(cover, font_path=FONT, layout_engine=engine)
    assert layout.step < cards.RAMP_STEPS, (name, layout.step)
    drawn = cards.onscreen_strings(cover)
    assert not [s for s in drawn if "a share" in s or "as filed" in s], (name, drawn)
    assert not any(ln.field.endswith(".detail") for ln in layout.lines), name
    allowed = set(tos.opening_strings(out["opening_card"], script["logos"])) | {out["image_footer"],
                                                                                  cards.BRAND_ROW_WORD}
    assert set(drawn) <= allowed, set(drawn) - allowed
    amounts = [ln for ln in layout.lines if ln.field.endswith(".cells[2]")]
    assert len(amounts) == len(out["opening_card"]["list"]["rows"]) == min(5, len(rows))
    assert all(ln.fill == look.GREEN for ln in amounts)


def test_il_the_worker_mirrors_the_servers_row_bounds():
    """The detail line's bound and the cover's five rows are ONE number on both sides (pinned equal), and the worker's
    walker refuses a detail line one character longer — as the server's does. Mutation-checked by hand (2026-11-20):
    the worker's ROW_DETAIL_MAX_CHARS at 63 turned this red."""
    assert cards.ROW_KEYS == tos.ROW_KEYS == (("cells",), ("logo", "chip", "label", "detail"))
    assert cards.ROW_DETAIL_MAX_CHARS == tos.ROW_DETAIL_MAX_CHARS == 64
    assert cards.MAX_OPENING_ROWS == tos.MAX_OPENING_ROWS == 5
    out = json.loads(json.dumps(_IL_OUTPUTS["isp_n3"]))
    row = out["image_spec"]["sections"][0]["rows"][0]
    row["detail"] = "x" * tos.ROW_DETAIL_MAX_CHARS
    keys = tos.logo_keys([{"key": r["key"], "name": r["name"]} for r in out["logo_refs"]])
    assert tos.validate_image_spec(out["image_spec"], keys, footer=out["image_footer"]) is None
    _f13_image(out)
    row["detail"] = "x" * (tos.ROW_DETAIL_MAX_CHARS + 1)
    assert tos.validate_image_spec(out["image_spec"], keys, footer=out["image_footer"]) is not None
    with pytest.raises(ValueError):
        _f13_image(out)


def test_il_a_cover_row_never_carries_a_detail_line():
    """The cover's rows are the picture's WITHOUT `detail` (insider_multi5_cover_alt_all5.jpg): the server's opening-card
    validator refuses a detail on a cover row (so `opening_strings` allows nothing), and the worker's `cover_image`
    refuses it too — the cost per share can never reach a video frame through the cover. Mutation-checked by hand
    (2026-11-20): either check removed turned this red."""
    out = json.loads(json.dumps(_IL_OUTPUTS["isp_n5"]))
    card = out["opening_card"]
    assert all("detail" not in r for r in card["list"]["rows"])
    card["list"]["rows"][0]["detail"] = out["image_spec"]["sections"][0]["rows"][0]["detail"]
    problem = tos.validate_opening_card(card, [r["key"] for r in out["logo_refs"]])
    assert problem is not None and "detail" in problem, problem
    assert tos.opening_strings(card, [{"key": r["key"], "name": r["name"]} for r in out["logo_refs"]]) == []
    # the worker mirrors it: a cover row with a detail line is contract drift, never drawn
    table = cards.logo_table(dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None,
                                                                "sha256": None} for r in out["logo_refs"]]), {})
    with pytest.raises(ValueError, match="detail"):
        nl.cover_image(card, table, footer=out["image_footer"], series=out["series"])


# ── owner decision 2026-11-20: the Money Map LOSS year's costs card ("A: costs vs revenue") ─────────────────────────
#
# money_map_loss_A_rivian.jpg / money_map_loss_A_mixed.jpg / money_map_loss_A_rivian_cover.jpg: the navy revenue bar over
# the longer red costs bar on ONE scale, the gap past revenue the net loss; a profit year keeps today's card.


def _loss_records() -> Dict[str, Any]:
    import test_marketing_news_templates as NT
    from app.services.marketing import company_news_rules as R

    # five long, real-world segment names (Comcast's, Amazon's) — the revenue card's two-column legend at its widest a
    # level can hold (W-names that wide push even a profit year's legend to the ramp: not the costs card's to fix)
    worst_segments = tuple(R.Segment(n, v) for n, v in zip(
        ("Connectivity and Platforms", "Content and Experiences", "Third-Party Seller Services",
         "Advertising Services", "Subscription Services"), (2.1e9, 1.3e9, 0.9e9, 0.7e9, 0.5e9)))
    return {
        "rivian": NT.rivian(),                                              # other INCOME: the hatched end + note
        "coherent": NT.coherent(),                                          # other a COST: the maroon third part
        "no_gross": NT.rivian(gross_profit_usd=None),                       # one undivided costs bar
        "two_decimals": NT.rivian(revenue_usd=5_840_000_000.0, gross_profit_usd=-40_000_000.0,
                                  operating_profit_usd=-3_900_000_000.0, net_income_usd=-3_820_000_000.0,
                                  segments=(R.Segment("Automotive", 4_660_000_000.0),
                                            R.Segment("Software and services", 1_180_000_000.0))),
        # an operating PROFIT and a net loss (interest-heavy): the revenue marker falls inside the third part
        "interest_heavy": NT.coherent(revenue_usd=10_000_000_000.0, gross_profit_usd=4_000_000_000.0,
                                      operating_profit_usd=1_000_000_000.0, net_income_usd=-2_000_000_000.0,
                                      segments=(R.Segment("Networking", 5_000_000_000.0),
                                                R.Segment("Lasers", 3_250_000_000.0),
                                                R.Segment("Materials", 1_750_000_000.0))),
        # the 2× bound: a net loss equal to revenue — the revenue bar at half the scale
        "loss_equals_revenue": NT.rivian(net_income_usd=-5_800_000_000.0, operating_profit_usd=-5_800_000_000.0),
        # a sliver of a loss beside a long bar: "Net loss" right-aligned on the card, never into "Revenue"
        "tiny_loss": NT.rivian(gross_profit_usd=1_000_000_000.0, operating_profit_usd=-900_000_000.0,
                               net_income_usd=-10_000_000.0),
        # the widest costs card under the tallest header the head ladder takes: a three-line name, five long segments
        # and "Everything else" (the two-column legend), three parts at two decimals
        "worst": NT.coherent(company=R.CompanyRef(symbol="COHR", name="Rivian Automotive"),
                             segments=worst_segments, other_usd=340_000_000.0, revenue_usd=5_840_000_000.0,
                             gross_profit_usd=1_234_000_000.0, operating_profit_usd=-1_166_000_000.0,
                             net_income_usd=-1_320_000_000.0),
        # a name too wide for the head ladder (the server's bounds let it through): the ramp, never CardOverflow
        "worst_name": NT.coherent(company=R.CompanyRef(symbol="COHR", name="Wwwwwwwwww Wwwwwwwwww Wwwwwwwwww"),
                                  segments=worst_segments, other_usd=340_000_000.0, revenue_usd=5_840_000_000.0,
                                  gross_profit_usd=1_234_000_000.0, operating_profit_usd=-1_166_000_000.0,
                                  net_income_usd=-1_320_000_000.0),
    }


def _loss_out(name: str) -> Dict[str, Any]:
    import test_marketing_news_templates as NT

    rec = _loss_records()[name]
    out = _compose(rec, NT.THU)
    assert "costs" in out["image_spec"] and "costs" in out["opening_card"]          # anti-vacuity
    return out


def _loss_script(out: Dict[str, Any]) -> Dict[str, Any]:
    entries = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None, "width": None,
                "height": None} for r in out["logo_refs"]]
    return dict(out, look=cards.LOOK, logos=entries)


def test_costs_the_worst_sample_really_is_the_widest():
    import test_marketing_news_templates as NT

    out = _loss_out("worst")
    c = out["image_spec"]["costs"]
    plan = T._money_map_costs(*(Decimal(repr(v)) for v in (5_840_000_000.0, 1_234_000_000.0, -1_166_000_000.0,
                                                             -1_320_000_000.0)))
    assert plan.places == 2 and len(c["parts"]) == 3 and len(out["image_spec"]["segments"]) == 6
    assert c["parts"][2]["value"] == "$154M" and c["revenue"]["value"] == "$5.84B" and NT.THU
    assert out["image_spec"]["title"] == "How Rivian Automotive makes money"                 # three lines at 80 px


@pytest.mark.parametrize("name", sorted(_loss_records()))
def test_costs_every_loss_picture_fits_and_draws_exactly_what_it_declares(name):
    """Each loss year lays out at the picture's own sizes (step 0, a card LEVEL, never CardOverflow), draws every string
    it declares whole — and declares only what it draws: the flow's later steps ("Gross loss", "Operating loss" …) and
    its one-line callout are checked, never drawn or declared — every declared string the server's allow-list holds."""
    out = _loss_out(name)
    image = _composed_image(out)
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    if name == "worst_name":
        # a title over three lines at 66 px: the whole page steps down the ramp (as a profit year's does)
        assert layout.step > 0 and layout.bars_level is None
    else:
        assert layout.step == 0 and layout.bars_level is not None and layout.head_px is not None
    _assert_draws_exactly_its_strings(image, layout, undrawn=("header.name",))
    fields = set(layout.fields())
    assert {"costs.callout[0]", "costs.callout[1]", "costs.total.value", "flow[0].value"} <= fields
    assert not {f for f in fields if f.startswith("flow[") and not f.startswith("flow[0]")} and "callout" not in fields
    server = tos.image_strings(out["image_spec"], tos.logo_keys(_loss_script(out)["logos"]))
    server = tos.image_strings(out["image_spec"], _loss_script(out)["logos"])
    assert set(image.strings) <= set(server)
    for f in out["image_spec"]["flow"][1:]:
        assert f["label"] not in image.strings or f["label"] in ("Net loss",)
    # every line drawn at least 22 px (the costs card's own sizes: 24 px at the smallest; the ramp's floor)
    assert min(ln.size for ln in layout.lines if not look.is_logo_standin(ln.field)) >= 22


@pytest.mark.parametrize("name", ["rivian", "coherent", "worst"])
def test_costs_the_loss_picture_and_cover_draw_the_same_bytes_from_the_same_inputs(name, tmp_path):
    out = _loss_out(name)
    files = {r["key"]: _logo_png(tmp_path / f"{i}.png", (40 + 9 * i, 90, 160, 255))
             for i, r in enumerate(out["logo_refs"])}

    def renders():
        script = _loss_script(out)
        table = cards.logo_table(script, {k: str(v) for k, v in files.items()})
        image = nl.template_image_for_script(script, table)
        pic = nl.render_template_image(image, font_path=FONT, layout_engine="basic").data
        spec = cards.cards_for_script(script, table, font_path=FONT, layout_engine="basic")[0]
        assert spec.image is not None and spec.image.cover                     # the cover IS the costs card
        return pic, cards.render_card(spec, font_path=FONT, layout_engine="basic")

    a = renders()
    cards._font.cache_clear()
    cards._fit_wordmark.cache_clear()
    assert renders() == a


@pytest.mark.parametrize("name", sorted(_loss_records()))
def test_costs_the_cover_is_the_costs_card_above_the_caption_band(name):
    """money_map_loss_A_rivian_cover.jpg: the headline beside the plate, its sub line, the costs card and its callout —
    every line above cards.TEXT_ZONE_Y[1] (the burned captions never cross it; the mock's callout did), every string it
    draws one the server allows on the cover (the opening card's) or the short footer, and drawn whole."""
    out = _loss_out(name)
    script = _loss_script(out)
    table = cards.logo_table(script, {})
    spec = cards.cards_for_script(script, table, font_path=FONT, layout_engine="basic")[0]
    assert spec.kind == "opening" and spec.image is not None and spec.image.spec.get("costs") is not None
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    for ln in layout.lines:
        if ln.field not in ("brand", "kicker", "footer"):
            assert ln.ink[3] <= cards.TEXT_ZONE_Y[1], (ln.field, ln.ink)
    for r in layout.rects:
        if r.box[1] > 300:                                                     # every card under the header
            assert r.box[3] <= cards.TEXT_ZONE_Y[1], r
    # the costs card starts under the header — at least COSTS_COVER_MIN_TOP below the sub line's ink and the plate
    header_foot = max(ln.ink[3] for ln in layout.lines if ln.field in ("title", "subtitle", "header.chip"))
    card_top = min(r.box[1] for r in layout.rects if r.fill == look.CARD and r.box[1] > 300)
    assert card_top >= header_foot + nl.COSTS_COVER_MIN_TOP - 2, (card_top, header_foot)
    _assert_draws_exactly_its_strings(spec.image, layout, undrawn=("header.name",),
                                      items=nl.walk_costs_cover(spec.image.spec, spec.image.logos))
    allowed = set(tos.opening_strings(out["opening_card"], script["logos"])) | {out["image_footer"]}
    assert set(cards.onscreen_strings(spec)) - {cards.BRAND_ROW_WORD} <= allowed
    assert {"costs.callout[1]", "subtitle", "title"} <= set(layout.fields())
    assert min(ln.size for ln in layout.lines if not look.is_logo_standin(ln.field)) >= nl.DATA_CARD_MIN_PX


def test_costs_rivians_cover_and_picture_take_the_mocks_shape():
    """The approved shapes: the picture's costs card at the mock's own sizes (28 / 30 px labels and values, 42 px bars,
    the callout 28 / 44); the cover's headline at 80 px with the biggest costs level that keeps it above the caption
    band — the 3-line Rivian headline leaves room for COSTS_COVER_LEVELS[2]."""
    out = _loss_out("rivian")
    layout = nl.layout_template(_composed_image(out), font_path=FONT, layout_engine="basic")
    assert _sizes(layout, "costs.revenue.label") == [28] and _sizes(layout, "costs.revenue.value") == [30]
    assert _sizes(layout, "costs.callout[0]") == [28] and _sizes(layout, "costs.callout[1]") == [44]
    assert _sizes(layout, "title") == [80]
    (bar,) = [s for s in layout.stacks if s.hatch is not None and s.outline is None]
    assert bar.box[3] - bar.box[1] == nl.COSTS_PICTURE.bar and bar.radius == nl.COSTS_PICTURE.rad
    script = _loss_script(out)
    spec = cards.cards_for_script(script, cards.logo_table(script, {}), font_path=FONT, layout_engine="basic")[0]
    cover = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    assert _sizes(cover, "title") == [80] and _sizes(cover, "costs.revenue.label") == [nl.COSTS_COVER_LEVELS[2].lab]
    # a two-line headline (a short name) takes a bigger cover level
    short = _compose(_loss_records()["rivian"].__class__(**{**_loss_records()["rivian"].__dict__}) if False else
                     __import__("test_marketing_news_templates").rivian(
                         company=__import__("app.services.marketing.company_news_rules",
                                            fromlist=["CompanyRef"]).CompanyRef(symbol="RIVN", name="Rivian")),
                     __import__("test_marketing_news_templates").THU)
    sscript = _loss_script(short)
    sspec = cards.cards_for_script(sscript, cards.logo_table(sscript, {}), font_path=FONT, layout_engine="basic")[0]
    scover = cards.layout_card(sspec, font_path=FONT, layout_engine="basic")
    assert _sizes(scover, "costs.revenue.label")[0] > nl.COSTS_COVER_LEVELS[2].lab


def test_costs_the_bars_marker_box_and_hatch_are_drawn_where_the_figures_put_them():
    """Rivian: the marker where revenue ends crosses both bars (INK), cost of sales runs PAST it (a gross loss), the pale
    net-loss box with its dashed RED edge spans revenue → where the solid red ends, and the bar's last stretch is
    HATCHED (stripes of operating expenses' red on the card); Coherent: cost of sales ends BEFORE the marker (a gross
    profit), a maroon third part, no hatch."""
    from PIL import Image

    out = _loss_out("rivian")
    image = _composed_image(out)
    img, layout = nl.draw_template(image, font_path=FONT, layout_engine="basic")
    bar = next(s for s in layout.stacks if s.hatch is not None and s.outline is None)
    marker = next(s for s in layout.stacks if s.colours == (look.INK,))
    mx = (marker.box[0] + marker.box[2]) / 2
    cos_end = bar.box[0] + bar.widths[0]
    assert cos_end > mx                                                     # the gross loss: past the marker
    hx0, hx1 = bar.hatch
    assert hx1 - hx0 >= nl.COSTS_THIN_END and abs(bar.box[0] + hx1 - bar.box[2]) <= 1
    by = (bar.box[1] + bar.box[3]) // 2
    px = img.load()
    assert all(abs(c - k) <= 40 for c, k in zip(px[int(mx), by], look.INK))   # the marker over the costs bar
    stripe = {px[x, by] for x in range(int(bar.box[0] + hx0) + 3, int(bar.box[0] + hx1) - 2)}
    assert any(sum(abs(c - k) for c, k in zip(p, look.COST_MID_RED)) < 60 for p in stripe)
    assert any(sum(abs(c - k) for c, k in zip(p, look.CARD)) < 30 for p in stripe)
    pale = [r for r in layout.rects if r.fill == look.PALE_RED]
    assert len(pale) == 1 and abs(pale[0].box[0] - (mx + nl.COSTS_BOX_GAP)) <= 2
    assert abs(pale[0].box[2] - (bar.box[0] + hx0)) <= 1                   # the box ends where the solid red ends
    assert sum(1 for r in layout.rects if r.fill == look.RED and r.box[3] - r.box[1] <= nl.COSTS_DASH_W) > 10
    # Coherent: a gross profit (cost of sales ends before the marker), the maroon third part, no hatch
    lay2 = nl.layout_template(_composed_image(_loss_out("coherent")), font_path=FONT, layout_engine="basic")
    bar2 = next(s for s in lay2.stacks if s.colours == (look.RED, look.COST_MID_RED, look.COST_MAROON))
    marker2 = next(s for s in lay2.stacks if s.colours == (look.INK,))
    assert bar2.hatch is None and len(bar2.widths) == 3
    assert bar2.box[0] + bar2.widths[0] < marker2.box[0]
    del Image


def _cb(rev, total, parts, note=False):
    c = {"revenue": {"ratio": rev}, "total": {"ratio": total}, "parts": [{"ratio": r} for r in parts]}
    if note:
        c["note"] = "+$1M from interest, tax and other items made the loss smaller"
    return c


def T_costs(rev, gp, op, ni):
    """The server's own costs card for these statement lines (`news_templates._money_map_costs`)."""
    from app.services.marketing import news_templates as T

    return T._money_map_costs(rev, gp, op, ni).block


def test_costs_a_thin_piece_is_widened_from_operating_expenses_never_from_cost_of_sales():
    span = 900.0
    # a thin THIRD part (maroon): 16 px, taken from operating expenses; cost of sales and the bar's length unchanged
    ws, solid, rev = nl.costs_bar_widths(_cb(0.85, 1.0, [0.55, 0.445, 0.005]), span)
    assert ws[2] == nl.COSTS_THIN_END and abs(ws[0] - 0.55 * span) < 1e-9
    assert abs(sum(ws) - span) < 1e-9 and abs(ws[1] - (0.445 * span - (nl.COSTS_THIN_END - 0.005 * span))) < 1e-9
    assert solid == sum(ws) and abs(rev - 0.85 * span) < 1e-9
    # a thin HATCH (other income): 16 px at the end, from operating expenses' solid red; the bar's length unchanged
    ws, solid, _rev = nl.costs_bar_widths(_cb(0.6, 0.999, [0.6, 0.4], note=True), span)
    assert sum(ws) - solid == nl.COSTS_THIN_END and abs(sum(ws) - span) < 1e-9
    # a wide hatch keeps its own width (where costs end)
    ws, solid, _rev = nl.costs_bar_widths(_cb(0.6, 0.9, [0.6, 0.4], note=True), span)
    assert abs(solid - 0.9 * span) < 1e-9
    # re-check round 2026-11-26: a SMALL net loss beside other income (revenue $10B, operating −$150M, net −$50M: the
    # costs end 4.4 px past revenue, the hatch 9 px) — the hatch's floor never pulls the solid red back to the revenue
    # marker (it drew the costs ending 2.7 px BEFORE revenue): the solid keeps COSTS_BOX_GAP past it, the hatch narrows
    c = T_costs(Decimal("10e9"), Decimal("2e9"), Decimal("-0.15e9"), Decimal("-0.05e9"))
    ws, solid, rev = nl.costs_bar_widths(c, span)
    assert "note" in c and sum(ws) - c["total"]["ratio"] * span < nl.COSTS_THIN_END
    assert solid > rev and abs(solid - (rev + nl.COSTS_BOX_GAP)) < 1e-9 and solid <= c["total"]["ratio"] * span
    assert 0 < sum(ws) - solid < nl.COSTS_THIN_END and abs(sum(ws) - span) < 1e-9
    # a net loss thinner than the box gap keeps its own end (never pushed past where the costs end)
    ws, solid, rev = nl.costs_bar_widths(_cb(0.995, 0.997, [0.8, 0.2], note=True), span)
    assert abs(solid - 0.997 * span) < 1e-9 and solid > rev
    # a thin COST OF SALES: its floor, from operating expenses
    ws, solid, _rev = nl.costs_bar_widths(_cb(0.6, 1.0, [0.002, 0.998]), span)
    assert ws[0] == nl.COSTS_THIN_END and abs(sum(ws) - span) < 1e-9
    # a thin OPERATING EXPENSES beside a third part: its floor from the third part, never from cost of sales
    ws, solid, _rev = nl.costs_bar_widths(_cb(0.6, 1.0, [0.6, 0.004, 0.396]), span)
    assert ws[1] == nl.COSTS_THIN_MID and abs(ws[0] - 0.6 * span) < 1e-9 and abs(sum(ws) - span) < 1e-9
    # no parts: one undivided piece, the whole bar solid
    ws, solid, _rev = nl.costs_bar_widths(_cb(0.6, 1.0, []), span)
    assert ws == [span] and solid == span
    # ratios that sum past the span (each rounded to four places) are scaled back to it
    ws, solid, _rev = nl.costs_bar_widths(_cb(0.6, 1.0, [0.6001, 0.4001]), span)
    assert abs(sum(ws) - span) < 1e-9


@pytest.mark.parametrize("mutate, needle", [
    (lambda c: c.update(colour="red"), "unknown key"),
    (lambda c: c["parts"].pop(), "entries, not one of"),
    (lambda c: c.update(callout=c["callout"][:1]), "entries"),
    (lambda c: c["revenue"].update(ratio=0.999), "longer than the costs bar"),
    (lambda c: c["parts"][0].update(ratio=float("nan")), "finite"),
    (lambda c: c.update(parts=[]), "a note explains"),
    (lambda c: c["net"].update(ratio=0.3), "unknown key"),
], ids=["unknown", "one_part", "one_line", "revenue_longer", "nan", "note_without_parts", "net_ratio"])
def test_costs_the_workers_walk_refuses_what_the_server_refuses(mutate, needle):
    out = _loss_out("rivian")
    spec = copy.deepcopy(out["image_spec"])
    mutate(spec["costs"])
    assert tos.validate_image_spec(spec, tos.logo_keys(_loss_script(out)["logos"]), footer=out["image_footer"])
    with pytest.raises(ValueError, match=needle):
        nl.walk(spec, cards.logo_table(_loss_script(out), {}))


def test_costs_a_cover_that_cannot_fit_falls_back_to_todays_figure_card(monkeypatch, caplog):
    out = _loss_out("rivian")
    script = _loss_script(out)

    def no_room(*_a, **_k):
        raise nl._NoFit("forced")

    monkeypatch.setattr(nl, "_lay_costs_cover", no_room)
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    spec = cards.cards_for_script(script, cards.logo_table(script, {}), font_path=FONT, layout_engine="basic")[0]
    assert spec.image is None and spec.figure == "$66.38" and spec.body == "net loss for every $100 of revenue"
    assert any("the costs cover cannot be drawn" in r.getMessage() for r in caplog.records)
    allowed = set(tos.opening_strings(out["opening_card"], script["logos"])) | {out["image_footer"]}
    assert set(cards.onscreen_strings(spec)) - {cards.BRAND_ROW_WORD} <= allowed


def test_costs_a_loss_data_card_is_legible_and_draws_exactly_its_strings():
    out = _loss_out("rivian")
    image = _composed_image(out)
    layout = nl.layout_data_card(image, font_path=FONT, layout_engine="basic")
    assert nl.illegible_lines(layout) == []
    _assert_draws_exactly_its_strings(image, layout, undrawn=("header.name",))
    _assert_data_card_inside(layout)


def test_costs_the_cover_and_opening_card_reject_a_subtitle_alone_and_a_costs_card_beside_a_list():
    out = _loss_out("rivian")
    table = cards.logo_table(_loss_script(out), {})
    card = dict(out["opening_card"])
    with pytest.raises(ValueError, match="subtitle"):
        cards.opening_spec({k: v for k, v in card.items() if k != "costs"}, table, footer=out["image_footer"])
    with pytest.raises(ValueError, match="both a costs card and a list"):
        cards.opening_spec(dict(card, list={"rows": [{"cells": ["x"]}]}), table, footer=out["image_footer"])
    # an older web's read-back (no short footer to draw): today's opening card
    assert cards.opening_spec(card, table, footer="").image is None


# ── final review of 2026-11-26: a theme row's disc never stays blank for a missing logo ─────────────────────────────
#
# FINAL_BUILD's theme pictures (logos uncached) drew EMPTY white discs for ordinary names at the theme rows' 26-30 px
# radii (Constellation Energy, CrowdStrike, Qualcomm, Micron …) and left the "how they make money" grid an empty
# ~100 px gutter (a wordmark drew no disc there); the names that did fit printed at 9-10 px. Now a small disc of a
# missing logo draws its row's TICKER — the row's own chip, already drawn, declared and allowed by the server
# (`template_onscreen.image_strings` holds every chip) — whenever its name fits only under
# `look.WORDMARK_PREFER_TICKER_BELOW_PX` or not at all.

_THEME_ANGLE_SAMPLES = ("ai_chips_weekly", "power_machine", "silicon_growth", "cyber_insiders", "battlefield_congress")


@pytest.mark.parametrize("factory", _THEME_ANGLE_SAMPLES)
def test_final_review_a_theme_row_disc_of_a_missing_logo_draws_its_ticker_never_a_blank(factory, shipped_pair_grid):
    """Every logo a WORDMARK (no logo file — the FINAL_BUILD case): the picture and its list cover leave NO blank disc
    (`cards.blank_wordmarks`), draw no company name in a disc under the legible size, and every ticker a disc draws is
    the chip of a row of that logo — a string the server already allows for the post (picture: `image_strings`;
    cover: `opening_strings`). The grid draws a disc on every tile (no empty gutter). Mutation-checked by hand: with
    `ticker=` dropped at the theme rows' `page.disc` calls, the blank discs came back and this turned red."""
    import test_marketing_news_templates as NT

    out = _compose(getattr(NT, factory)(), NT.THEME_WED)
    image, table = _f13_image(out)
    picture = nl.layout_template(image, font_path=FONT, layout_engine="basic")
    script = dict(out, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None,
                                                "bytes": None, "width": None, "height": None}
                                               for r in out["logo_refs"]])
    cover = cards.layout_card(cards.cards_for_script(script, table)[0], font_path=FONT, layout_engine="basic")
    server_picture = set(tos.image_strings(out["image_spec"], script["logos"]))
    server_cover = set(tos.opening_strings(out["opening_card"], script["logos"]))
    for layout, allowed in ((picture, server_picture), (cover, server_cover)):
        assert cards.blank_wordmarks(layout) == [], (factory, cards.blank_wordmarks(layout))
        assert not [ln for ln in layout.lines if ln.field.startswith("wordmark:")
                    and ln.size < look.WORDMARK_PREFER_TICKER_BELOW_PX], factory
        tickers = [ln for ln in layout.lines if ln.field.startswith(look.TICKER_FIELD_PREFIX)]
        for ln in tickers:
            key = ln.field.split(":", 1)[1]
            assert ln.text == key and ln.text in allowed, (factory, ln.field, ln.text)
    # not vacuous: the small discs of this post really needed the stand-in (the tally's 46 px discs fit their names)
    needs = factory != "battlefield_congress"
    assert any(ln.field.startswith(look.TICKER_FIELD_PREFIX) for ln in picture.lines) is needs, factory
    if out["image_spec"]["layout"] == "grid":
        keys = [t["logo"] for t in out["image_spec"]["tiles"]]
        assert sorted(a.key for _b, a in picture.tiles) == sorted(keys)                 # a disc on every tile


def test_final_review_a_ticker_fits_a_theme_rows_smallest_disc():
    """The ticker stand-in at the theme rows' radii (figures 26, ledger 28, ranked / grid floor 30): four- and
    five-letter symbols and a class symbol fit inside the circle where the name ("CrowdStrike Holdings") does not fit at
    all. A symbol too wide even at the floor size ("WWWWW" at 26) draws nothing — the old blank, never a clipped one."""
    art = cards.LogoArt("CRWD", "CrowdStrike Holdings", None)
    for r in (nl.TH_FIG_DISC, nl.TH_LEDGER_DISC, 30):
        box = (0, 0, 2 * r, 2 * r)
        assert look.disc_wordmark_lines(art, box, FONT, "basic") == []                 # the blank of FINAL_BUILD
        for ticker in ("CRWD", "GOOGL", "BRK-B"):
            lines = look.disc_ticker_lines(art, ticker, box, FONT, "basic")
            assert [ln.text for ln in lines] == [ticker] and lines[0].field == "ticker:CRWD", (r, ticker)
            assert lines[0].size >= cards.WORDMARK_MIN_PX
            assert all(math.hypot(x - r, y - r) <= r - 4 for x in (lines[0].ink[0], lines[0].ink[2])
                       for y in (lines[0].ink[1], lines[0].ink[3])), (r, ticker)
    assert look.disc_ticker_lines(art, "TWO WORDS", (0, 0, 60, 60), FONT, "basic") == []
    assert look.disc_ticker_lines(art, "WWWWW", (0, 0, 52, 52), FONT, "basic") == []
