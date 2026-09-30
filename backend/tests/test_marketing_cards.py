"""
Phase 4 — the video's still cards (worker side: marketing/cards.py; SYSTEM_DESIGN_GUIDELINES §12.8).

What is pinned here, strongest first:

* **Nothing is drawn outside the text zone** — every rendered kind is decoded and the rows of
  the caption band, the platform chrome above the zone and the side margins are asserted to be
  pure PAGE colour, pixel for pixel. The band itself is checked against what libass REALLY inks
  for a caption (ffmpeg), not only against the arithmetic it is derived from.
* **Never truncated**: every word of every title/body comes back, in order, from the wrapped
  lines; a card that cannot fit raises CardOverflow instead.
* **The strings are the server's**: `onscreen_strings` returns the accepted script's strings
  verbatim and exactly what the card draws, and passes the server's own allow-list logic.
* **Deterministic bytes**, glyph refusal, the palette, the badge, the logo, both layout engines.

Hermetic: the vendored Inter Bold and logo only; ffmpeg (for the libass check) runs locally and is
skipped when absent. `app.*` is imported by the TESTS only (to pin the mirrored constants).
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from app.schemas import marketing as sch  # noqa: E402
from app.services.marketing import post_copy  # noqa: E402
from marketing import captions  # noqa: E402
from marketing import cards  # noqa: E402

_BACKEND = Path(__file__).resolve().parents[1]
FONT = str(_BACKEND / "marketing" / "assets" / "fonts" / "Inter-Bold.ttf")
LOGO = str(_BACKEND / "marketing" / "assets" / "brand" / "caydex-logo.png")
_DATA = _BACKEND / "tests" / "data"

DISCLAIMER = post_copy.disclaimer_card(date(2026, 9, 29))
PAGE_RGB = cards._rgb(cards.PAGE)

#: The writer's maxima (writer_prompts.CARD_TITLE_MAX_WORDS / CARD_BODY_MAX_WORDS = 8 / 28), with
#: deliberately long words: the worst a legal card can be.
MAX_TITLE = "Understanding Extraordinary International Diversification Throughout Unpredictable Economic Environments"
MAX_BODY = ("Institutional investors frequently rebalance diversified portfolios quarterly, maintaining "
            "predetermined allocations between international equities, government securities, corporate "
            "obligations, infrastructure investments, commodities, currencies, "
            "reducing concentration vulnerabilities throughout unpredictable macroeconomic downturns.")


def _png(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def _pure_page(im: Image.Image, box: Tuple[int, int, int, int]) -> bool:
    return im.crop(box).getextrema() == tuple((c, c) for c in PAGE_RGB)


_OUTSIDE_ZONE = {
    "above the zone": (0, 0, cards.WIDTH, cards.TEXT_ZONE_Y[0]),
    "below the zone (caption band + bottom chrome)": (0, cards.TEXT_ZONE_Y[1], cards.WIDTH, cards.HEIGHT),
    "left margin": (0, cards.TEXT_ZONE_Y[0], cards.SAFE_X[0], cards.TEXT_ZONE_Y[1]),
    "right margin": (cards.SAFE_X[1], cards.TEXT_ZONE_Y[0], cards.WIDTH, cards.TEXT_ZONE_Y[1]),
    "caption band": (0, cards.CAPTION_BAND_Y[0], cards.WIDTH, cards.CAPTION_BAND_Y[1]),
}


def _assert_nothing_outside_the_zone(data: bytes, what: str) -> Image.Image:
    im = _png(data)
    assert im.size == (cards.WIDTH, cards.HEIGHT) and im.mode == "RGB", (what, im.size, im.mode)
    for name, box in _OUTSIDE_ZONE.items():
        assert _pure_page(im, box), f"{what}: something was drawn in the {name} {box}"
    return im


def _fixture_cards() -> List[Dict[str, str]]:
    """Every card the real writer produced (2026-09-26 judge packages, paired title/body)."""
    raw = json.loads((_DATA / "marketing_judge_packages_2026_09_26.json").read_text(encoding="utf-8"))
    out: Dict[Tuple[str, str], Dict[str, str]] = {}
    for pkg in raw["packages"]:
        for key, value in pkg["fields"]:
            m = re.fullmatch(r"cards\[(\d+)\]\.(title|body)", key)
            if m:
                out.setdefault((pkg["id"], m.group(1)), {})[m.group(2)] = value
    assert len(out) > 50, "the fixture changed shape — this test would go vacuous"
    return list(out.values())


def _real_draft_card_texts() -> List[Tuple[str, str]]:
    raw = json.loads((_DATA / "marketing_real_drafts_2026_09_24.json").read_text(encoding="utf-8"))
    rows = [(r[1], r[2]) for r in raw["honest"] if r[1] in ("cards.title", "cards.body")]
    assert len(rows) > 100
    return rows


def _words(s: str) -> List[str]:
    return s.split()


# ── constants and their derivations ──────────────────────────────────────────


def test_brand_text_mirrors_the_server_allow_list():
    assert cards.BRAND_TEXT == sch.VIDEO_BRAND_TEXT == (cards.WORDMARK, cards.CTA)
    assert cards.MAX_STRING_CHARS == sch.ONSCREEN_TEXT_MAX_CHARS


def test_geometry_constants():
    assert (cards.WIDTH, cards.HEIGHT) == (1080, 1920) == captions.PLAY_RES
    assert cards.TEXT_ZONE_Y == (160, 1120)
    assert cards.SAFE_X == (80, 1000) and cards.SAFE_X[1] - cards.SAFE_X[0] == captions.SAFE_WIDTH
    assert cards.CAPTION_BAND_Y == (1170, 1310)
    # the whole point: card text can never reach the captions
    assert cards.TEXT_ZONE_Y[1] < cards.CAPTION_BAND_Y[0]
    # the band covers the caption line's box as libass sizes it (Fontsize = win ascent+descent),
    # plus its outline
    half = captions.FONT_SIZE / 2 + captions.OUTLINE
    assert cards.CAPTION_BAND_Y[0] < captions.POS[1] - half
    assert cards.CAPTION_BAND_Y[1] > captions.POS[1] + half
    assert isinstance(cards.CARD_RENDER_VERSION, str) and cards.CARD_RENDER_VERSION


def test_brand_colours_are_the_brand_hexes():
    assert (cards.PAGE, cards.CARD, cards.ACCENT, cards.TEXT) == ("#171B26", "#1E2330", "#60A5FA", "#FFFFFF")
    # the caption file's outline is the page colour and its highlight the accent (one palette)
    assert captions.OUTLINE_COLOUR == "&H00261B17" and captions.HIGHLIGHT_C == "&HFAA560&"


def _libass_available() -> bool:
    ff = shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else None)
    if not ff:
        return False
    try:
        out = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(re.search(r"\ssubtitles\s", out))


@pytest.mark.skipif(not _libass_available(), reason="ffmpeg with libass not installed")
def test_the_caption_band_covers_what_libass_really_inks(tmp_path):
    """Burn a caption with tall accents and deep descenders (the real ASS file captions.py
    writes) onto a black frame and find every inked row: all of them sit inside CAPTION_BAND_Y."""
    ff = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    (tmp_path / "fonts").mkdir()
    shutil.copyfile(FONT, tmp_path / "fonts" / "Inter-Bold.ttf")
    words = [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.28, "line": 0}
             for i, w in enumerate("ÅÉÎ gjpqy Quality|".split())]
    (tmp_path / "c.ass").write_text(captions.build_ass(words, captions.font_measurer(FONT)), encoding="utf-8")
    proc = subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "color=c=black:s=1080x1920:d=1", "-vf", "subtitles=c.ass:fontsdir=fonts", "-ss", "0.35",
         "-frames:v", "1", "frame.png"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-1000:]
    frame = Image.open(tmp_path / "frame.png").convert("L")
    ink = frame.point(lambda v: 255 if v > 8 else 0).getbbox()
    assert ink is not None, "no caption was drawn — the check would be vacuous"
    assert cards.CAPTION_BAND_Y[0] <= ink[1] and ink[3] <= cards.CAPTION_BAND_Y[1], (ink, cards.CAPTION_BAND_Y)
    assert ink[1] > cards.TEXT_ZONE_Y[1]


# ── CardSpec ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kwargs, exc", [
    ({"kind": "hook"}, ValueError),
    ({"kind": "Text", "title": "x"}, ValueError),
    ({"kind": ""}, ValueError),
    ({"kind": "brand", "title": "x"}, ValueError),            # a brand card draws no title
    ({"kind": "brand", "badge": "Sample data"}, ValueError),
    ({"kind": "text"}, ValueError),                            # neither title nor body
    ({"kind": "text", "title": "  ", "body": "\n\t"}, ValueError),
    ({"kind": "text", "title": "x", "figure": "$5"}, ValueError),
    ({"kind": "stat"}, ValueError),
    ({"kind": "stat", "figure": "5", "title": "x"}, ValueError),
    ({"kind": "disclaimer"}, ValueError),
    ({"kind": "disclaimer", "body": "   "}, ValueError),
    ({"kind": "disclaimer", "body": "d", "badge": "b"}, ValueError),
    ({"kind": "text", "title": None}, TypeError),
    ({"kind": "text", "title": 5}, TypeError),
    ({"kind": 3}, TypeError),
])
def test_cardspec_refuses_what_it_cannot_draw_faithfully(kwargs, exc):
    with pytest.raises(exc):
        cards.CardSpec(**kwargs)


def test_cardspec_accepts_every_kind():
    cards.CardSpec("brand")
    cards.CardSpec("text", title="t")
    cards.CardSpec("text", body="b")
    cards.CardSpec("text", title="t", body="b", badge="Sample data")
    cards.CardSpec("stat", figure="$6.9 billion", label="l", body="b", badge="Sample data")
    cards.CardSpec("disclaimer", body=DISCLAIMER)


# ── cards_for_script ─────────────────────────────────────────────────────────


def _script(**over):
    s = {
        "hook": "What does a smart first investor do differently?",
        "video_script": ["Line one.", "Line two."],
        "cards": [{"title": "Start early", "body": "Time does most of the work."},
                  {"title": "Stay diversified", "body": "Many companies, not one."},
                  {"title": "Keep costs low", "body": "Fees compound too."}],
        "carousel_slides": [],
        "disclaimer_card": DISCLAIMER,
    }
    s.update(over)
    return s


def test_cards_for_script_brand_then_cards_in_order_then_disclaimer():
    specs = cards.cards_for_script(_script())
    assert [s.kind for s in specs] == ["brand", "text", "text", "text", "disclaimer"]
    assert [(s.title, s.body) for s in specs[1:-1]] == [
        ("Start early", "Time does most of the work."), ("Stay diversified", "Many companies, not one."),
        ("Keep costs low", "Fees compound too.")]
    assert specs[-1].body == DISCLAIMER


def test_the_hook_is_never_drawn_on_a_card():
    specs = cards.cards_for_script(_script())
    drawn = [t for s in specs for t in cards.onscreen_strings(s)]
    assert all("smart first investor" not in t for t in drawn)
    assert cards.onscreen_strings(specs[0]) == [cards.WORDMARK]


def test_cards_for_script_skips_empty_cards_and_strips():
    specs = cards.cards_for_script(_script(cards=[
        {"title": "", "body": ""}, {"title": "  ", "body": "\n"}, {}, {"title": None, "body": None},
        {"title": "  Title only  "}, {"body": "\tBody only\n"}, {"title": "T", "body": "B", "extra": "x"},
    ]))
    assert [(s.kind, s.title, s.body) for s in specs[1:-1]] == [
        ("text", "Title only", ""), ("text", "", "Body only"), ("text", "T", "B")]


def test_cards_for_script_with_no_cards_is_brand_and_disclaimer():
    for cs in ([], None):
        specs = cards.cards_for_script(_script(cards=cs))
        assert [s.kind for s in specs] == ["brand", "disclaimer"]
    specs = cards.cards_for_script({"disclaimer_card": DISCLAIMER})
    assert [s.kind for s in specs] == ["brand", "disclaimer"]


@pytest.mark.parametrize("disclaimer", ["", "   ", "\n", None])
def test_a_script_without_its_disclaimer_must_not_render(disclaimer):
    with pytest.raises(ValueError, match="disclaimer"):
        cards.cards_for_script(_script(disclaimer_card=disclaimer))
    s = _script()
    del s["disclaimer_card"]
    with pytest.raises(ValueError, match="disclaimer"):
        cards.cards_for_script(s)


@pytest.mark.parametrize("bad", [
    {"cards": "not a list"}, {"cards": {"title": "x"}}, {"cards": ["a string entry"]},
    {"cards": [{"title": 5, "body": "b"}]}, {"cards": [{"title": "t", "body": ["b"]}]},
    {"disclaimer_card": 12},
])
def test_a_malformed_script_is_a_value_error(bad):
    with pytest.raises(ValueError):
        cards.cards_for_script(_script(**bad))
    with pytest.raises(ValueError):
        cards.cards_for_script(["not", "a", "dict"])  # type: ignore[arg-type]


def test_the_disclaimer_is_kept_verbatim():
    odd = DISCLAIMER + "  "
    specs = cards.cards_for_script(_script(disclaimer_card=odd))
    assert specs[-1].body == odd
    assert cards.onscreen_strings(specs[-1]) == [odd, cards.CTA]


# ── onscreen_strings ─────────────────────────────────────────────────────────


def test_onscreen_strings_are_verbatim_and_exactly_what_is_drawn():
    title, body = "Two  spaces  here", "Wraps\nacross a newline and   runs"
    spec = cards.CardSpec("text", title=title, body=body)
    assert cards.onscreen_strings(spec) == [title, body]          # the SAME strings, unmodified
    layout = cards.layout_card(spec, font_path=FONT)
    # the drawing re-wraps them (single spaces), but draws nothing else
    assert " ".join(layout.field_lines("title")) == " ".join(title.split())
    assert " ".join(layout.field_lines("body")) == " ".join(body.split())
    assert {ln.field for ln in layout.lines} == {"title", "body"}


@pytest.mark.parametrize("spec, expected, fields", [
    (cards.CardSpec("brand"), [cards.WORDMARK], {"wordmark"}),
    (cards.CardSpec("text", title="T"), ["T"], {"title"}),
    (cards.CardSpec("text", body="B"), ["B"], {"body"}),
    (cards.CardSpec("text", title="T", body="B", badge="Sample data"), ["T", "B", "Sample data"],
     {"title", "body", "badge"}),
    (cards.CardSpec("text", title="T", badge="   "), ["T"], {"title"}),
    (cards.CardSpec("stat", figure="$6.9 billion", label="Deal value", body="As filed.", badge="Sample data"),
     ["$6.9 billion", "Deal value", "As filed.", "Sample data"], {"figure", "label", "body", "badge"}),
    (cards.CardSpec("stat", figure="42"), ["42"], {"figure"}),
    (cards.CardSpec("disclaimer", body="D"), ["D", cards.CTA], {"body", "cta"}),
])
def test_onscreen_strings_per_kind_match_the_drawn_fields(spec, expected, fields):
    assert cards.onscreen_strings(spec) == expected
    layout = cards.layout_card(spec, font_path=FONT)
    assert {ln.field for ln in layout.lines} == fields
    # every drawn line belongs to a returned string (brand/CTA text included)
    returned = " ".join(expected)
    for ln in layout.lines:
        assert ln.text in returned or all(w in returned for w in ln.text.split())


def test_every_declared_string_passes_the_servers_allow_list():
    """Mirrors run_service._check_onscreen_text: brand text + the accepted script's card titles
    and bodies + its disclaimer card, and the disclaimer must be among them."""
    script = _script()
    allowed = set(sch.VIDEO_BRAND_TEXT)
    for card in script["cards"]:
        allowed.update(str(card.get(k)) for k in ("title", "body") if card.get(k))
    allowed.add(script["disclaimer_card"])
    drawn: List[str] = []
    for spec in cards.cards_for_script(script):
        for t in cards.onscreen_strings(spec):
            if t not in drawn:
                drawn.append(t)
    assert [t for t in drawn if t not in allowed] == []
    assert script["disclaimer_card"] in drawn
    assert all(0 < len(t) <= sch.ONSCREEN_TEXT_MAX_CHARS and t.strip() for t in drawn)


# ── wrapping and fitting ─────────────────────────────────────────────────────


def test_wrap_exact_fit_boundary():
    font = cards._font(FONT, 58, "basic")
    line = "Traditional automakers built"
    w = font.getlength(line)
    assert cards._wrap(line.split(), font, w, 10, "t") == [line]               # exactly fits
    assert cards._wrap(line.split(), font, w - 0.01, 10, "t") == ["Traditional automakers", "built"]
    assert cards._wrap([], font, w, 10, "t") == []
    with pytest.raises(cards._NoFit):
        cards._wrap(line.split(), font, w - 0.01, 1, "t")                       # the room is 1 line
    with pytest.raises(cards._NoFit, match="wide"):
        cards._wrap(["Traditional"], font, font.getlength("Traditional") - 1, 10, "t")


def _assert_all_words_in_order(layout: cards.CardLayout, spec: cards.CardSpec) -> None:
    for name in ("title", "body", "figure", "label"):
        text = getattr(spec, name, "")
        lines = layout.field_lines(name)
        assert [w for ln in lines for w in ln.split()] == _words(text), (name, text, lines)
        assert all(ln == " ".join(ln.split()) and ln for ln in lines)


def _assert_ink_inside(layout: cards.CardLayout) -> None:
    zone = (cards.SAFE_X[0], cards.TEXT_ZONE_Y[0], cards.SAFE_X[1], cards.TEXT_ZONE_Y[1])
    bounds = layout.panel or zone
    assert cards._within(bounds, zone)
    for ln in layout.lines:
        assert cards._within(ln.ink, bounds), (ln.field, ln.text, ln.ink, bounds)
    for b in (layout.rule, layout.badge, layout.logo):
        if b:
            assert cards._within(b, bounds)


def test_the_writers_maximum_card_fits_whole():
    assert len(MAX_TITLE.split()) == 8 and len(MAX_BODY.split()) == 28
    spec = cards.CardSpec("text", title=MAX_TITLE, body=MAX_BODY)
    layout = cards.layout_card(spec, font_path=FONT)
    _assert_all_words_in_order(layout, spec)
    _assert_ink_inside(layout)
    assert 0 < layout.step <= cards.RAMP_STEPS


def test_every_real_writer_card_fits_whole_and_inside():
    for c in _fixture_cards():
        spec = cards.CardSpec("text", title=c.get("title", ""), body=c.get("body", ""))
        layout = cards.layout_card(spec, font_path=FONT)
        _assert_all_words_in_order(layout, spec)
        _assert_ink_inside(layout)
    longest_body = max((c.get("body", "") for c in _fixture_cards()), key=len)
    longest_title = max((c.get("title", "") for c in _fixture_cards()), key=len)
    for field_name, text in sorted(set(_real_draft_card_texts())):
        spec = (cards.CardSpec("text", title=text, body=longest_body) if field_name == "cards.title"
                else cards.CardSpec("text", title=longest_title, body=text))
        layout = cards.layout_card(spec, font_path=FONT)
        _assert_all_words_in_order(layout, spec)
        _assert_ink_inside(layout)


def test_title_only_and_body_only_cards():
    for spec in (cards.CardSpec("text", title="Only a title"), cards.CardSpec("text", body="Only a body")):
        layout = cards.layout_card(spec, font_path=FONT)
        _assert_all_words_in_order(layout, spec)
        assert layout.rule is not None and layout.badge is None


def test_a_word_too_wide_at_the_start_size_steps_down_instead_of_breaking():
    word = "Counterrevolutionaries"
    assert cards._font(FONT, cards.TITLE_STYLE.start, "basic").getlength(word) > 808
    spec = cards.CardSpec("text", title=f"The {word} Idea", body="Short body.")
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    assert layout.step > 0
    assert word in layout.field_lines("title")                                  # whole, own line
    _assert_all_words_in_order(layout, spec)


@pytest.mark.parametrize("spec", [
    cards.CardSpec("text", title="Pneumonoultramicroscopicsilicovolcanoconiosis", body="b"),
    cards.CardSpec("text", title="t", body="A Pneumonoultramicroscopicsilicovolcanoconiosis case"),
    cards.CardSpec("text", body="x" * 80),
    cards.CardSpec("stat", figure="$1,000,000,000,000,000,000"),
    cards.CardSpec("disclaimer", body="Supercalifragilisticexpialidocious" * 3),
])
def test_one_word_wider_than_the_column_at_the_floor_is_an_overflow(spec):
    with pytest.raises(cards.CardOverflow, match="wide"):
        cards.layout_card(spec, font_path=FONT)
    with pytest.raises(cards.CardOverflow):
        cards.render_card(spec, font_path=FONT, logo_path=LOGO)


def test_too_many_lines_at_the_floor_is_an_overflow_never_a_cut():
    body = " ".join(["WW"] * 199)
    assert len(body) <= cards.MAX_STRING_CHARS
    with pytest.raises(cards.CardOverflow, match="line"):
        cards.layout_card(cards.CardSpec("text", title=MAX_TITLE, body=body), font_path=FONT)
    figure = " ".join(["88"] * 150)
    with pytest.raises(cards.CardOverflow):
        cards.layout_card(cards.CardSpec("stat", figure=figure), font_path=FONT)


def test_a_string_over_the_servers_cap_is_refused_before_measuring():
    t0 = time.monotonic()
    with pytest.raises(cards.CardOverflow, match=str(cards.MAX_STRING_CHARS)):
        cards.layout_card(cards.CardSpec("text", title="t", body="a " * 400), font_path=FONT)
    with pytest.raises(cards.CardOverflow, match=str(cards.MAX_STRING_CHARS)):
        cards.layout_card(cards.CardSpec("text", title="y" * 10_000_000), font_path=FONT)
    assert time.monotonic() - t0 < 2.0


def test_pathological_inputs_under_the_cap_end_quickly():
    t0 = time.monotonic()
    for spec in (cards.CardSpec("text", body="W" * 600),                   # one 600-char word
                 cards.CardSpec("text", title=MAX_TITLE, body=" ".join("i" * 300)),
                 cards.CardSpec("text", body="́" * 600)):             # 600 combining marks
        try:
            cards.render_card(spec, font_path=FONT, layout_engine="basic")
        except cards.CardOverflow:
            pass
    assert time.monotonic() - t0 < 20.0


def test_sizes_step_from_start_to_floor_in_bounded_integer_steps():
    for style in (cards.TITLE_STYLE, cards.BODY_STYLE, cards.FIGURE_STYLE, cards.LABEL_STYLE,
                  cards.STAT_BODY_STYLE, cards.DISCLAIMER_STYLE, cards.CTA_STYLE, cards.WORDMARK_STYLE):
        sizes = [style.size(i) for i in range(cards.RAMP_STEPS + 1)]
        assert sizes[0] == style.start and sizes[-1] == style.floor
        assert sizes == sorted(sizes, reverse=True) and all(isinstance(s, int) for s in sizes)
        assert style.leading >= 1.05


# ── pixels: nothing outside the zone (the strongest check) ───────────────────

_KINDS_TO_RENDER = {
    "brand (logo)": (cards.CardSpec("brand"), LOGO),
    "brand (no logo)": (cards.CardSpec("brand"), None),
    "text": (cards.CardSpec("text", title="Myth: Design and Manufacturing Are One",
                            body="Traditional automakers built millions of cars for a century."), None),
    "text (writer max)": (cards.CardSpec("text", title=MAX_TITLE, body=MAX_BODY), None),
    "text (badge)": (cards.CardSpec("text", title="Sample", body="Illustrative figures.", badge="Sample data"), None),
    "stat": (cards.CardSpec("stat", figure="$6.9 billion", label="Deal value disclosed in the filing",
                            body="Amounts as reported in the filing.", badge="Sample data"), None),
    "stat (tall)": (cards.CardSpec("stat", figure="Twelve thousand four hundred", label=MAX_TITLE,
                                   body=MAX_BODY), None),
    "disclaimer (logo)": (cards.CardSpec("disclaimer", body=DISCLAIMER), LOGO),
    "disclaimer (no logo)": (cards.CardSpec("disclaimer", body=DISCLAIMER), None),
}


@pytest.mark.parametrize("name", sorted(_KINDS_TO_RENDER))
def test_nothing_is_drawn_outside_the_text_zone(name, monkeypatch):
    monkeypatch.setattr(cards, "_warned", set())
    spec, logo = _KINDS_TO_RENDER[name]
    im = _assert_nothing_outside_the_zone(cards.render_card(spec, font_path=FONT, logo_path=logo), name)
    # anti-vacuity: the card really drew something inside the zone
    zone = (cards.SAFE_X[0], cards.TEXT_ZONE_Y[0], cards.SAFE_X[1], cards.TEXT_ZONE_Y[1])
    assert not _pure_page(im, zone)
    layout = cards.layout_card(spec, font_path=FONT, with_logo=logo is not None)
    _assert_ink_inside(layout)


def _on_palette(colour: Tuple[int, int, int], tol: float = 2.0) -> bool:
    """Is `colour` one of the brand hexes or an alpha blend of two of them?"""
    base = [cards._rgb(h) for h in (cards.PAGE, cards.CARD, cards.ACCENT, cards.TEXT)]
    for a, b in list(combinations(base, 2)) + [(x, x) for x in base]:
        d = [bv - av for av, bv in zip(a, b)]
        dd = sum(v * v for v in d)
        t = 0.0 if dd == 0 else max(0.0, min(1.0, sum((c - av) * dv for c, av, dv in zip(colour, a, d)) / dd))
        p = [av + t * dv for av, dv in zip(a, d)]
        if sum((c - pv) ** 2 for c, pv in zip(colour, p)) ** 0.5 <= tol:
            return True
    return False


@pytest.mark.parametrize("name", [n for n in sorted(_KINDS_TO_RENDER) if _KINDS_TO_RENDER[n][1] is None])
def test_only_brand_colours_and_their_alpha_blends(name):
    spec, _ = _KINDS_TO_RENDER[name]
    im = _png(cards.render_card(spec, font_path=FONT))
    colours = im.getcolors(maxcolors=1 << 20)
    off = [c for _, c in colours if not _on_palette(c)]
    assert off == [], f"{name}: {len(off)} off-palette colours, e.g. {off[:5]}"


def test_the_title_is_accent_and_the_body_white():
    im = _png(cards.render_card(_KINDS_TO_RENDER["text"][0], font_path=FONT))
    colours = {c for _, c in im.getcolors(maxcolors=1 << 20)}
    assert cards._rgb(cards.ACCENT) in colours and cards._rgb(cards.TEXT) in colours
    assert cards._rgb(cards.CARD) in colours   # the panel


# ── badge ────────────────────────────────────────────────────────────────────


def test_the_badge_is_drawn_only_when_set():
    base = dict(title="Sample", body="Illustrative figures.")
    plain = cards.layout_card(cards.CardSpec("text", **base), font_path=FONT)
    badged = cards.layout_card(cards.CardSpec("text", badge="Sample data", **base), font_path=FONT)
    assert plain.badge is None and plain.rule is not None
    assert badged.badge is not None and badged.rule is None
    assert badged.field_lines("badge") == ["Sample data"] and plain.field_lines("badge") == []
    # top-left of the panel
    assert badged.badge[0] == badged.panel[0] + cards.PANEL_PAD_X
    assert badged.badge[1] == badged.panel[1] + cards.PANEL_PAD_Y
    # The pill's fill is ACCENT at 16 % over CARD — a colour text anti-aliasing also produces
    # on a few edge pixels, so count it: a pill is thousands of pixels, stray edges a handful.
    def fill_pixels(spec):
        return dict((c, n) for n, c in _png(cards.render_card(spec, font_path=FONT)).getcolors(1 << 20)).get(
            cards.BADGE_FILL, 0)

    assert fill_pixels(cards.CardSpec("text", badge="Sample data", **base)) > 5000
    assert fill_pixels(cards.CardSpec("text", **base)) < 200


def test_a_badge_wider_than_the_column_is_an_overflow():
    with pytest.raises(cards.CardOverflow, match="badge"):
        cards.layout_card(cards.CardSpec("text", title="t", badge="Sample data " * 8), font_path=FONT)


# ── determinism ──────────────────────────────────────────────────────────────


def _png_chunks(data: bytes) -> List[bytes]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    out, i = [], 8
    while i < len(data):
        (n,) = struct.unpack(">I", data[i:i + 4])
        out.append(data[i + 4:i + 8])
        i += 12 + n
    return out


@pytest.mark.parametrize("name", sorted(_KINDS_TO_RENDER))
def test_same_spec_same_bytes(name):
    spec, logo = _KINDS_TO_RENDER[name]
    a = cards.render_card(spec, font_path=FONT, logo_path=logo, layout_engine="basic")
    b = cards.render_card(spec, font_path=FONT, logo_path=logo, layout_engine="basic")
    cards._font.cache_clear()
    c = cards.render_card(spec, font_path=FONT, logo_path=logo, layout_engine="basic")
    assert a == b == c
    assert set(_png_chunks(a)) <= {b"IHDR", b"IDAT", b"IEND"}, "metadata chunks make bytes drift"


def test_different_text_different_bytes():
    a = cards.render_card(cards.CardSpec("text", title="Start early", body="Time does the work."), font_path=FONT)
    b = cards.render_card(cards.CardSpec("text", title="Start early", body="Time does the work!"), font_path=FONT)
    c = cards.render_card(cards.CardSpec("disclaimer", body=DISCLAIMER), font_path=FONT)
    d = cards.render_card(cards.CardSpec("disclaimer", body=post_copy.disclaimer_card(date(2026, 9, 30))),
                          font_path=FONT)
    assert a != b and c != d


def test_render_cards_is_render_card_per_spec():
    specs = cards.cards_for_script(_script())
    many = cards.render_cards(specs, font_path=FONT, logo_path=LOGO)
    assert many == [cards.render_card(s, font_path=FONT, logo_path=LOGO) for s in specs]
    for spec, data in zip(specs, many):
        _assert_nothing_outside_the_zone(data, spec.kind)


# ── glyphs ───────────────────────────────────────────────────────────────────


def test_the_real_disclaimer_and_brand_text_have_every_glyph():
    assert "—" in DISCLAIMER and "·" in DISCLAIMER
    cards.check_glyphs(cards.cards_for_script(_script()), FONT)
    cards.check_glyphs([cards.CardSpec("text", title="It’s “quoted” – 5% of €1…", body="£ ¥ × ÷ ± ° é ü ñ ©")], FONT)


@pytest.mark.parametrize("text, missing", [
    ("Buy low 🚀", "🚀"),
    ("中文", "中"),
    ("family 👨‍👩‍👧", "‍"),
    ("a‮b", "‮"),                      # bidi override: would reorder under RAQM
    ("control\x07char", "\x07"),
])
def test_check_glyphs_refuses_what_the_font_cannot_draw(text, missing):
    with pytest.raises(cards.MissingGlyphs) as ei:
        cards.check_glyphs([cards.CardSpec("text", title="ok", body=text)], FONT)
    assert missing in ei.value.chars and f"U+{ord(missing):04X}" in str(ei.value)


def test_check_glyphs_covers_extra_strings_and_every_kind():
    with pytest.raises(cards.MissingGlyphs) as ei:
        cards.check_glyphs(cards.cards_for_script(_script()), FONT, extra=["fine", "caption🙂"])
    assert ei.value.chars == ["🙂"]
    for spec in (cards.CardSpec("stat", figure="5", label="l", body="b", badge="✅"),
                 cards.CardSpec("disclaimer", body="😀 disclaimer")):
        with pytest.raises(cards.MissingGlyphs):
            cards.check_glyphs([spec], FONT)
    cards.check_glyphs([], FONT, extra=["  \n\t "])     # whitespace is never drawn


def test_render_card_does_not_crash_on_unchecked_input():
    """render_card may assume checked input, but must never crash or hang on it."""
    for text in ("emoji 😀🇺🇸", "é́́ stacked", "́ leading mark", "中文 字", "​"):
        data = cards.render_card(cards.CardSpec("text", title=text, body=text), font_path=FONT,
                                 layout_engine="basic")
        _assert_nothing_outside_the_zone(data, repr(text))


def test_many_missing_characters_are_summarised():
    text = "".join(chr(0x4E00 + i) for i in range(30))
    with pytest.raises(cards.MissingGlyphs) as ei:
        cards.check_glyphs([cards.CardSpec("text", title=text)], FONT)
    assert len(ei.value.chars) == 30 and "and 10 more" in str(ei.value)


# ── assets ───────────────────────────────────────────────────────────────────


def test_a_missing_logo_renders_without_it_and_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(cards, "_warned", set())
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    for spec in (cards.CardSpec("brand"), cards.CardSpec("disclaimer", body=DISCLAIMER), cards.CardSpec("brand")):
        layout = cards.layout_card(spec, font_path=FONT, with_logo=False)
        assert layout.logo is None
        cards.render_card(spec, font_path=FONT, logo_path=None)
    assert len([r for r in caplog.records if "logo" in r.getMessage()]) == 1
    caplog.clear()
    cards.render_card(cards.CardSpec("text", title="t"), font_path=FONT)   # a text card needs none
    assert caplog.records == []


def test_the_logo_is_drawn_in_its_box():
    spec = cards.CardSpec("brand")
    layout = cards.layout_card(spec, font_path=FONT, with_logo=True)
    im = _png(cards.render_card(spec, font_path=FONT, logo_path=LOGO))
    x0, y0, x1, y1 = layout.logo
    assert (x1 - x0, y1 - y0) == (cards.LOGO_BRAND_PX, cards.LOGO_BRAND_PX)
    tile = im.crop(layout.logo)
    assert not _pure_page(im, layout.logo)
    # the tile's corners are the page colour (the vendored logo blends in), its mark is light grey
    assert tile.getpixel((0, 0)) == PAGE_RGB and tile.getpixel((cards.LOGO_BRAND_PX - 1,) * 2) == PAGE_RGB
    assert max(tile.getextrema()[0]) > 200


def test_a_transparent_or_non_square_logo_is_composited_on_the_page(tmp_path):
    p = tmp_path / "logo.png"
    im = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
    im.paste((250, 250, 250, 255), (150, 25, 250, 75))
    im.save(p)
    spec = cards.CardSpec("disclaimer", body=DISCLAIMER)
    data = cards.render_card(spec, font_path=FONT, logo_path=str(p))
    out = _assert_nothing_outside_the_zone(data, "disclaimer, transparent logo")
    box = cards.layout_card(spec, font_path=FONT, with_logo=True).logo
    tile = out.crop(box)
    assert tile.getpixel((0, 0)) == PAGE_RGB and tile.getpixel((box[2] - box[0] - 1, 0)) == PAGE_RGB
    centre = tile.getpixel(((box[2] - box[0]) // 2, (box[3] - box[1]) // 2))
    assert min(centre) > 200


def test_unreadable_assets_are_typed_errors(tmp_path):
    bad = tmp_path / "not-a-png.png"
    bad.write_bytes(b"this is not an image")
    with pytest.raises(cards.CardAssetError, match="logo"):
        cards.render_card(cards.CardSpec("brand"), font_path=FONT, logo_path=str(bad))
    with pytest.raises(cards.CardAssetError, match="logo"):
        cards.render_card(cards.CardSpec("brand"), font_path=FONT, logo_path=str(tmp_path / "missing.png"))
    missing_font = str(tmp_path / "missing.ttf")
    with pytest.raises(cards.CardAssetError, match="font"):
        cards.render_card(cards.CardSpec("text", title="t"), font_path=missing_font)
    with pytest.raises(cards.CardAssetError, match="font"):
        cards.check_glyphs([cards.CardSpec("text", title="t")], missing_font)


# ── layout engines ───────────────────────────────────────────────────────────


def test_engine_resolution(monkeypatch):
    monkeypatch.setattr(cards, "raqm_available", lambda: False)
    assert cards.resolve_layout_engine(None) == "basic"
    assert cards.resolve_layout_engine("basic") == "basic"
    with pytest.raises(ValueError, match="raqm"):
        cards.resolve_layout_engine("raqm")        # never Pillow's silent fallback
    with pytest.raises(ValueError):
        cards.render_card(cards.CardSpec("brand"), font_path=FONT, layout_engine="raqm")
    monkeypatch.setattr(cards, "raqm_available", lambda: True)
    assert cards.resolve_layout_engine(None) == "raqm"
    for bad in ("RAQM", "harfbuzz", ""):
        with pytest.raises(ValueError):
            cards.resolve_layout_engine(bad)


def test_raqm_available_is_false_on_any_error(monkeypatch):
    from PIL import features

    def boom(_name):
        raise RuntimeError("broken build")

    monkeypatch.setattr(features, "check", boom)
    assert cards.raqm_available() is False


def test_basic_engine_renders_every_kind():
    for name, (spec, logo) in _KINDS_TO_RENDER.items():
        _assert_nothing_outside_the_zone(
            cards.render_card(spec, font_path=FONT, logo_path=logo, layout_engine="basic"), f"basic {name}")


_RAQM_CHECK = r"""
import io, sys, hashlib
from PIL import Image
from marketing import cards
assert cards.raqm_available(), "no raqm"
F, L, D = sys.argv[1], sys.argv[2], sys.argv[3]
page = cards._rgb(cards.PAGE)
specs = [cards.CardSpec("brand"), cards.CardSpec("text", title=sys.argv[4], body=sys.argv[5]),
         cards.CardSpec("stat", figure="$6.9 billion", label="Deal value", body="As filed.", badge="Sample data"),
         cards.CardSpec("disclaimer", body=D)]
for s in specs:
    a = cards.render_card(s, font_path=F, logo_path=L, layout_engine="raqm")
    assert a == cards.render_card(s, font_path=F, logo_path=L, layout_engine="raqm")
    assert a != cards.render_card(s, font_path=F, logo_path=L, layout_engine="basic") or s.kind == "brand"
    im = Image.open(io.BytesIO(a)); im.load()
    for box in [(0, 0, 1080, cards.TEXT_ZONE_Y[0]), (0, cards.TEXT_ZONE_Y[1], 1080, 1920),
                (0, 0, cards.SAFE_X[0], 1920), (cards.SAFE_X[1], 0, 1080, 1920)]:
        assert im.crop(box).getextrema() == tuple((c, c) for c in page), (s.kind, box)
    layout = cards.layout_card(s, font_path=F, layout_engine="raqm")
    assert layout.engine == "raqm"
print("RAQM-OK")
"""


def _raqm_env():
    """An environment in which Pillow finds libraqm: this one, or (macOS) Homebrew's libs."""
    if cards.raqm_available():
        return dict(os.environ)
    # Pillow's macOS wheels carry raqm but dlopen FriBiDi (and HarfBuzz) at run time.
    if sys.platform == "darwin" and any(Path("/opt/homebrew/lib").glob("libfribidi*")):
        return {**os.environ, "DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib"}
    return None


def test_raqm_engine_renders_every_kind_inside_the_zone():
    env = _raqm_env()
    if env is None:
        pytest.skip("libraqm not available to Pillow")
    env.pop("PYTHONPATH", None)
    proc = subprocess.run([sys.executable, "-c", _RAQM_CHECK, FONT, LOGO, DISCLAIMER, MAX_TITLE, MAX_BODY],
                          cwd=_BACKEND, env=env, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 and "no raqm" in proc.stderr:
        pytest.skip("libraqm not loadable in a subprocess either")
    assert proc.returncode == 0 and "RAQM-OK" in proc.stdout, proc.stderr[-2000:]


# ── the worker boundary ──────────────────────────────────────────────────────


def test_importing_cards_loads_no_pillow_and_no_app(tmp_path):
    """Pillow is imported inside the functions (like captions.py): a bare import of the module —
    which the worker's fresh-interpreter test does for every module — stays light."""
    code = ("import sys, json\nimport marketing.cards\n"
            "print(json.dumps(sorted(m for m in sys.modules if m == 'PIL' or m.startswith(('PIL.', 'app.', 'fontTools')) or m == 'app')))")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run([sys.executable, "-c", code], cwd=_BACKEND, env=env, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-1000:]
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []
