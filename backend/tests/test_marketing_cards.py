"""
Phase 4 — the video's still cards (worker side: marketing/cards.py; SYSTEM_DESIGN_GUIDELINES §12.8).

What is pinned here, strongest first:

* **Nothing is drawn where it must not be** (news-v23, the approved look: marketing/look.py) — every
  rendered kind is decoded and the platform chrome above the brand row, the side margins, the gap above
  the caption band, the caption band itself and the safe bottom (the last 420 px) are asserted to be the
  CANVAS (the gradient + glow, `look.canvas`), pixel for pixel. The band itself is checked against what
  libass REALLY inks for a caption (ffmpeg), not only against the arithmetic it is derived from.
* **Never truncated**: every word of every title/body comes back, in order, from the wrapped
  lines; a card that cannot fit raises CardOverflow instead.
* **The strings are the server's**: `onscreen_strings` returns the accepted script's strings
  verbatim and exactly what the card draws, and passes the server's own allow-list logic.
* **Deterministic bytes**, glyph refusal, the look's palette (every pixel is the canvas, a look colour or a
  blend of two — `_off_look`), the brand row on every card but the brand card, the kicker pill and the
  short footer, the badge, the logo, both layout engines.
* **Batch 2 (2026-10-19, "first seconds")**: the HOOK card and the DATA card are drawn only when the run
  FROZE their video feature, each falls back to today's card (never a skipped day), and the end card
  draws the read-back's `end_card_cta` — the legacy CTA against a web that names none.

Hermetic: the vendored Inter Bold and logo only; ffmpeg (for the libass check) runs locally and is
skipped when absent. `app.*` is imported by the TESTS only (to pin the mirrored constants).
"""

from __future__ import annotations

import dataclasses
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
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from app.schemas import marketing as sch  # noqa: E402
from app.services.marketing import post_copy  # noqa: E402
from marketing import captions  # noqa: E402
from marketing import cards  # noqa: E402
from marketing import look  # noqa: E402

_BACKEND = Path(__file__).resolve().parents[1]
FONT = str(_BACKEND / "marketing" / "assets" / "fonts" / "Inter-Bold.ttf")
LOGO = str(_BACKEND / "marketing" / "assets" / "brand" / "caydex-logo.png")
_DATA = _BACKEND / "tests" / "data"

DISCLAIMER = post_copy.disclaimer_card(date(2026, 9, 29))
PAGE_RGB = cards._rgb(cards.PAGE)

#: Batch 2: a real hook, and the writer's longest (writer_prompts.HOOK_MAX_WORDS = 14) in long words.
HOOK = "What does a smart first investor do differently?"
MAX_HOOK = ("Understanding Extraordinary International Diversification Throughout Unpredictable Economic "
            "Environments Requires Institutional Discipline Patience Documentation Everywhere")
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


#: news-v23: every 9:16 card is drawn on the look's canvas (the gradient + the gold glow), every post image on its
#: 4:5 one — "nothing drawn here" means "those pixels ARE the canvas's".
CANVAS = look.canvas(look.VIDEO_FRAME)
IMAGE_CANVAS = look.canvas(look.POST_FRAME)


def _pure_page(im: Image.Image, box: Tuple[int, int, int, int]) -> bool:
    """Is `box` of the 9:16 card (or the 4:5 picture) exactly the look's canvas there — nothing drawn on it?"""
    from PIL import ImageChops

    base = CANVAS if im.size == CANVAS.size else IMAGE_CANVAS
    return ImageChops.difference(im.crop(box), base.crop(box)).getbbox() is None


#: The brand row's top (140 px from the top: cover_13f_berkshire.jpg) and the safe bottom (nothing in the last
#: 420 px of the card).
BRAND_TOP = look.VIDEO_FRAME.brand[1]
SAFE_BOTTOM = look.VIDEO_SAFE_BOTTOM
_OUTSIDE_ZONE = {
    "above the brand row (the platforms' top chrome)": (0, 0, cards.WIDTH, BRAND_TOP),
    "between the content zone and the caption band": (0, cards.TEXT_ZONE_Y[1], cards.WIDTH, cards.CAPTION_BAND_Y[0]),
    "caption band": (0, cards.CAPTION_BAND_Y[0], cards.WIDTH, cards.CAPTION_BAND_Y[1]),
    "the safe bottom": (0, SAFE_BOTTOM, cards.WIDTH, cards.HEIGHT),
    "left margin": (0, BRAND_TOP, cards.SAFE_X[0], SAFE_BOTTOM),
    "right margin": (cards.SAFE_X[1], BRAND_TOP, cards.WIDTH, SAFE_BOTTOM),
}


def _assert_nothing_outside_the_zone(data: bytes, what: str) -> Image.Image:
    im = _png(data)
    assert im.size == (cards.WIDTH, cards.HEIGHT) and im.mode == "RGB", (what, im.size, im.mode)
    for name, box in _OUTSIDE_ZONE.items():
        assert _pure_page(im, box), f"{what}: something was drawn in the {name} {box}"
    return im


#: Every colour the look draws with (marketing/look.py) — a pixel off the canvas is one of these or an
#: anti-aliased blend of two of them, or of one of them with the canvas under it.
LOOK_COLOURS = [look.GOLD, look.WHITE, look.SUB, look.CARD, look.CARD_EDGE, look.INK, look.INK2, look.GREEN,
                look.RED, look.GOLD_INK, look.NAVY_CHIP, look.PILL, look.GREY, look.BAR_TRACK, look.BAR_FILL,
                look.NOTE_PANEL, look.TILE, look.DIVIDER, look.PALE_RED, look.BRAND_OUTLINE, *look.SEG_COLORS,
                (217, 160, 30)]


def _off_look(im: Image.Image, exclude=(), tol: float = 3.0) -> List[Tuple[int, int, int]]:
    """The colours of `im` (outside the `exclude` boxes — logo discs, the brand tile) that are neither its canvas
    nor a look colour nor a blend of two look colours or of a look colour with the canvas pixel under it."""
    np = pytest.importorskip("numpy")
    base = CANVAS if im.size == CANVAS.size else IMAGE_CANVAS
    a = np.asarray(im).astype(np.int16)
    c = np.asarray(base).astype(np.int16)
    keep = np.ones(a.shape[:2], dtype=bool)
    for b in exclude:
        keep[b[1]:b[3], b[0]:b[2]] = False
    changed = keep & (np.abs(a - c).sum(axis=2) > 0)
    pairs = np.unique(np.concatenate([a[changed], c[changed]], axis=1), axis=0).astype(np.float64)
    if not len(pairs):
        return []
    px, under = pairs[:, :3], pairs[:, 3:]
    pal = np.array(LOOK_COLOURS, dtype=np.float64)
    best = np.full(len(px), np.inf)
    ends = [(pal[i], pal[j]) for i in range(len(pal)) for j in range(i, len(pal))]
    for p0, p1 in ends:
        d = p1 - p0
        dd = float((d * d).sum())
        t = np.zeros(len(px)) if dd == 0 else np.clip(((px - p0) @ d) / dd, 0, 1)
        best = np.minimum(best, np.sqrt(((px - (p0 + t[:, None] * d)) ** 2).sum(axis=1)))
    for p0 in pal:
        d = under - p0
        dd = (d * d).sum(axis=1)
        t = np.where(dd == 0, 0.0, np.clip(((px - p0) * d).sum(axis=1) / np.where(dd == 0, 1, dd), 0, 1))
        best = np.minimum(best, np.sqrt(((px - (p0 + t[:, None] * d)) ** 2).sum(axis=1)))
    return [tuple(int(v) for v in px[i]) for i in np.nonzero(best > tol)[0]]


def _red(c) -> bool:
    """A strong RED (the test logos' 220,20,20) — never the look's GOLD (246,196,69) nor its text-safe RED ink's
    anti-aliased edges against the warm card."""
    return c[0] > 150 and c[1] < 90 and c[2] < 90 and c[0] - max(c[1], c[2]) > 100


def _brand_tile(frame=look.VIDEO_FRAME):
    x, y = frame.brand
    return (x - 2, y - 2, x + look.BRAND_TILE_PX + 2, y + look.BRAND_TILE_PX + 2)


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
    """Batch 2: VIDEO_BRAND_TEXT is a SUPERSET — the wordmark, the legacy CTA (older workers, rollbacks) and
    the App Store one (owner decision 6: post_copy.SEARCH_LINE without its full stop)."""
    assert cards.BRAND_TEXT == sch.VIDEO_BRAND_TEXT == (cards.WORDMARK, cards.LEGACY_CTA, cards.CTA)
    assert cards.CTA == sch.VIDEO_END_CARD_CTA == post_copy.SEARCH_LINE.rstrip(".") == "Search Caydex on the App Store"
    assert cards.LEGACY_CTA == sch.VIDEO_LEGACY_CTA == "caydexinvest.com"
    assert cards.END_CARD_CTAS == (cards.LEGACY_CTA, cards.CTA) and set(cards.END_CARD_CTAS) < set(cards.BRAND_TEXT)
    assert cards.MAX_STRING_CHARS == sch.ONSCREEN_TEXT_MAX_CHARS
    assert cards.ONSCREEN_TEXT_MAX == sch.ONSCREEN_TEXT_MAX


def test_the_video_features_mirror_the_server():
    assert cards.VIDEO_FEATURES == sch.VIDEO_FEATURES == ("hook_card", "data_card", "push_in")
    assert cards.LESSON_VIDEO_FEATURES == sch.LESSON_VIDEO_FEATURES == ("hook_card", "push_in")
    assert cards.TEMPLATE_VIDEO_FEATURES == sch.TEMPLATE_VIDEO_FEATURES == ("data_card", "push_in")
    assert (cards.VIDEO_FEATURE_HOOK_CARD, cards.VIDEO_FEATURE_DATA_CARD, cards.VIDEO_FEATURE_PUSH_IN) == (
        sch.VIDEO_FEATURE_HOOK_CARD, sch.VIDEO_FEATURE_DATA_CARD, sch.VIDEO_FEATURE_PUSH_IN)
    # the read-back's own field names (WorkerScript declares them: Pydantic drops undeclared keys)
    assert sch.VIDEO_FEATURES_KEY == "video_features"
    assert {"video_features", "end_card_cta"} <= set(sch.WorkerScript.model_fields)
    assert cards.KINDS == ("brand", "text", "stat", "disclaimer", "opening", "hook", "data")
    assert cards.HOOK_CARD_KINDS == ("opening", "hook") and cards.CARD_RENDER_VERSION == "cards/v6"


def test_the_brand_row_and_lesson_mirrors_are_the_servers():
    """news-v23 (restyle 2026-11-19): the brand row's word, the lesson's kicker pill and its short footer — the
    worker's mirrors (cards, and look which draws them) equal the server's allow-list constants."""
    assert cards.BRAND_ROW_WORD == look.BRAND_ROW_WORD == sch.BRAND_ROW_WORD == "Caydex"
    assert cards.LESSON_KICKER == look.LESSON_KICKER == sch.LESSON_KICKER == "INVESTING LESSON"
    assert cards.LESSON_FOOTER == look.LESSON_FOOTER == post_copy.IMAGE_FOOTER_HEAD == "Not investment advice"
    assert sch.PICTURE_BRAND_TEXT == (cards.BRAND_ROW_WORD,)
    assert sch.LESSON_BRAND_TEXT == (cards.BRAND_ROW_WORD, cards.LESSON_KICKER)
    assert cards.BRAND_ROW_WORD in sch.VIDEO_BRAND_TEXT


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


def test_the_look_palette_is_the_approved_kits():
    """news-v23: the palette is the approved design kit's (out/marketing_v2_preview/FINAL_REFERENCE/scripts/
    style_kit.py), value for value — the text-safe card inks the owner's colour rules name (GREEN a purchase /
    an increase / a profit, RED a decrease / a loss, GOLD_INK neutral emphasis)."""
    assert (look.NAVY_TOP, look.NAVY_BOT, look.GLOW) == ((14, 24, 48), (6, 9, 18), (60, 50, 10))
    assert (look.GOLD, look.WHITE, look.SUB, look.CARD) == ((246, 196, 69), (255, 255, 255), (203, 213, 225),
                                                          (250, 248, 242))
    assert (look.INK, look.INK2, look.GREEN, look.RED, look.GOLD_INK) == (
        (17, 24, 39), (75, 85, 99), (21, 128, 61), (185, 28, 28), (146, 100, 0))
    assert (look.NAVY_CHIP, look.PILL, look.GREY) == ((17, 24, 39), (40, 52, 84), (156, 163, 175))
    assert cards._rgb(cards.PLATE) == look.WHITE
    # the caption file keeps its own colours (the captions are not cards: unchanged by the restyle)
    assert captions.OUTLINE_COLOUR == "&H00261B17" and captions.HIGHLIGHT_C == "&HFAA560&"


def _luminance(rgb) -> float:
    def ch(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_every_ink_is_legible_on_what_it_is_drawn_on():
    """The kit's colour notes, measured: every text ink on the warm card is WCAG 4.5:1 or better; the gold kicker on
    its pill and the white / gold headline on the darkest and the lightest canvas rows too."""
    for ink in (look.INK, look.INK2, look.GREEN, look.RED, look.GOLD_INK):
        assert _contrast(ink, look.CARD) >= 4.5, ink
    assert _contrast(look.GOLD, look.PILL) >= 4.5 and _contrast(look.SUB, look.NOTE_PANEL) >= 4.5
    assert _contrast(look.WHITE, look.NAVY_CHIP) >= 4.5 and _contrast(look.INK, look.GOLD) >= 4.5
    for y in (140, 700, 1300):
        page = CANVAS.getpixel((540, y))
        assert _contrast(look.WHITE, page) >= 4.5 and _contrast(look.GOLD, page) >= 4.5 and _contrast(look.GREY, page) >= 4.5


def test_news_v23_the_table_and_list_tints_carry_their_inks():
    """news-v23 (the earnings table and the Congress list): the estimate's recessed pill under INK / INK2, a positive
    surprise's GREEN on its tint, a negative one's RED on its tint, the Congress count's GREEN figure on its tint, the
    white "purchases" word on its solid GREEN pill and the reported figure's white on its dark pill — each WCAG 4.5:1 or
    better, at the mock kit's values."""
    assert (look.PILL_LIGHT, look.GREEN_TINT, look.RED_TINT) == ((238, 234, 223), (234, 248, 238), (253, 234, 234))
    for ink in (look.INK, look.INK2):
        assert _contrast(ink, look.PILL_LIGHT) >= 4.5, ink
    assert _contrast(look.GREEN, look.GREEN_TINT) >= 4.5 and _contrast(look.RED, look.RED_TINT) >= 4.5
    assert _contrast(look.WHITE, look.GREEN) >= 4.5 and _contrast(look.WHITE, look.NAVY_CHIP) >= 4.5
    # every tint sits on the warm card: each must stay visibly apart from it (a tile, not a hole)
    assert all(t != look.CARD for t in (look.PILL_LIGHT, look.GREEN_TINT, look.RED_TINT))


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
    # Batch 2 made "hook" a KIND (the lesson hook card): a bare one is still refused — it needs its hook.
    ({"kind": "hook"}, ValueError),
    ({"kind": "hook", "title": "  "}, ValueError),
    ({"kind": "hook", "title": "h", "body": "b"}, ValueError),               # a hook card draws only the hook
    ({"kind": "hook", "title": "h", "badge": "Sample data"}, ValueError),
    ({"kind": "hook", "title": "h", "logos": (cards.LogoArt("A", "Alpha"),)}, ValueError),
    ({"kind": "data"}, TypeError),                                            # no picture
    ({"kind": "data", "image": {"layout": "rows"}}, TypeError),               # not a TemplateImage
    ({"kind": "text", "title": "t", "image": "x"}, ValueError),               # only a data card has a picture
    ({"kind": "disclaimer", "body": "d", "cta": "Buy now"}, ValueError),      # a CTA outside END_CARD_CTAS
    ({"kind": "disclaimer", "body": "d", "cta": "Caydex"}, ValueError),       # the wordmark is not a CTA
    ({"kind": "text", "title": "t", "cta": "caydexinvest.com"}, ValueError),  # only the end card has a CTA
    ({"kind": "disclaimer", "body": "d", "cta": None}, TypeError),
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
    for cta in ("", cards.LEGACY_CTA, cards.CTA):
        assert cards.CardSpec("disclaimer", body=DISCLAIMER, cta=cta).end_cta == (cta or cards.LEGACY_CTA)
    assert cards.CardSpec("hook", title="What does a smart first investor do differently?").end_cta == ""
    assert hash(cards.CardSpec("text", title="t")) == hash(cards.CardSpec("text", title="t"))   # still hashable


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
        "look": cards.LOOK,          # news-v23: a read-back from this web (the look's own strings are allowed)
    }
    s.update(over)
    return s


def test_cards_for_script_opens_on_the_first_text_card_never_the_brand_card():
    """Drop 1 (2026-10-09): "videos open on the company, never on our logo" — the first card of
    the video is the script's first text card, then the rest in order, then the disclaimer."""
    specs = cards.cards_for_script(_script())
    assert [s.kind for s in specs] == ["text", "text", "text", "disclaimer"]
    assert [(s.title, s.body) for s in specs[:-1]] == [
        ("Start early", "Time does most of the work."), ("Stay diversified", "Many companies, not one."),
        ("Keep costs low", "Fees compound too.")]
    assert specs[-1].body == DISCLAIMER
    assert all(s.kind != "brand" for s in specs)


def test_the_hook_and_the_wordmark_are_never_drawn_when_there_are_text_cards():
    """…unless the run FROZE `hook_card` (Batch 2: the hook card, below). Without it the hook is the burned
    captions' alone — also against a read-back that carries the feature list empty."""
    assert cards.cards_for_script(_script()) == cards.cards_for_script(_script(video_features=[]))
    specs = cards.cards_for_script(_script())
    drawn = [t for s in specs for t in cards.onscreen_strings(s)]
    assert all("smart first investor" not in t for t in drawn)
    assert all(s.kind != "brand" for s in specs)      # "Caydex" is drawn only as the brand ROW's word (news-v23)
    assert cards.onscreen_strings(specs[0]) == ["Start early", "Time does most of the work.", cards.LESSON_KICKER,
                                                cards.LESSON_FOOTER, cards.BRAND_ROW_WORD]


def test_cards_for_script_skips_empty_cards_and_strips():
    specs = cards.cards_for_script(_script(cards=[
        {"title": "", "body": ""}, {"title": "  ", "body": "\n"}, {}, {"title": None, "body": None},
        {"title": "  Title only  "}, {"body": "\tBody only\n"}, {"title": "T", "body": "B", "extra": "x"},
    ]))
    assert [(s.kind, s.title, s.body) for s in specs[:-1]] == [
        ("text", "Title only", ""), ("text", "", "Body only"), ("text", "T", "B")]


def test_cards_for_script_with_no_drawable_card_falls_back_to_the_brand_card_and_says_so(caplog):
    """The narration still needs a card under it; the wordmark is the one other string the server
    allows. Never silent: the writer always emits three cards, so this is a malformed script."""
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    for cs in ([], None, [{"title": " ", "body": ""}, {}]):
        caplog.clear()
        specs = cards.cards_for_script(_script(cards=cs))
        assert [s.kind for s in specs] == ["brand", "disclaimer"]
        assert any("brand card" in r.getMessage() for r in caplog.records), cs
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
    assert cards.onscreen_strings(specs[-1]) == [odd, cards.LEGACY_CTA, cards.LESSON_KICKER,
                                                 cards.BRAND_ROW_WORD]   # a read-back naming no CTA


# ── onscreen_strings ─────────────────────────────────────────────────────────


def test_onscreen_strings_are_verbatim_and_exactly_what_is_drawn():
    title, body = "Two  spaces  here", "Wraps\nacross a newline and   runs"
    spec = cards.CardSpec("text", title=title, body=body)
    assert cards.onscreen_strings(spec) == [title, body, cards.BRAND_ROW_WORD]    # the SAME strings, unmodified
    layout = cards.layout_card(spec, font_path=FONT)
    # the drawing re-wraps them (single spaces) and draws the title upper-cased — its words, nothing else
    assert " ".join(layout.field_lines("title")) == " ".join(title.split()).upper()
    assert " ".join(layout.field_lines("body")) == " ".join(body.split())
    assert layout.field_lines("brand") == [cards.BRAND_ROW_WORD]
    assert {ln.field for ln in layout.lines} == {"title", "body", "brand"}


_B = cards.BRAND_ROW_WORD


@pytest.mark.parametrize("spec, expected, fields", [
    (cards.CardSpec("brand"), [cards.WORDMARK], {"wordmark"}),                 # the brand card: no brand row
    (cards.CardSpec("text", title="T"), ["T", _B], {"title", "brand"}),
    (cards.CardSpec("text", body="B"), ["B", _B], {"body", "brand"}),
    (cards.CardSpec("text", title="T", body="B", badge="Sample data"), ["T", "B", "Sample data", _B],
     {"title", "body", "badge", "brand"}),
    (cards.CardSpec("text", title="T", badge="   "), ["T", _B], {"title", "brand"}),
    (cards.CardSpec("stat", figure="$6.9 billion", label="Deal value", body="As filed.", badge="Sample data"),
     ["$6.9 billion", "Deal value", "As filed.", "Sample data", _B], {"figure", "label", "body", "badge", "brand"}),
    (cards.CardSpec("stat", figure="42"), ["42", _B], {"figure", "brand"}),
    (cards.CardSpec("disclaimer", body="D"), ["D", cards.LEGACY_CTA, _B], {"body", "cta", "brand"}),
    (cards.CardSpec("disclaimer", body="D", cta=cards.CTA), ["D", cards.CTA, _B], {"body", "cta", "brand"}),
    (cards.CardSpec("hook", title="Why do fees matter so much?"), ["Why do fees matter so much?", _B],
     {"title", "brand"}),
    # news-v23: the kicker pill and the short footer, declared and drawn with their own fields
    (cards.CardSpec("text", title="T", body="B", pill=cards.LESSON_KICKER, footer=cards.LESSON_FOOTER),
     ["T", "B", cards.LESSON_KICKER, cards.LESSON_FOOTER, _B], {"title", "body", "pill", "footer", "brand"}),
    (cards.CardSpec("disclaimer", body="D", pill=cards.LESSON_KICKER),
     ["D", cards.LEGACY_CTA, cards.LESSON_KICKER, _B], {"body", "cta", "pill", "brand"}),
])
def test_onscreen_strings_per_kind_match_the_drawn_fields(spec, expected, fields):
    assert cards.onscreen_strings(spec) == expected
    layout = cards.layout_card(spec, font_path=FONT)
    assert {ln.field for ln in layout.lines} == fields
    # every drawn line belongs to a returned string (brand/CTA text included; a title is drawn upper-cased)
    returned = " ".join(expected).casefold()
    for ln in layout.lines:
        assert ln.text.casefold() in returned or all(w.casefold() in returned for w in ln.text.split())


def test_every_declared_string_passes_the_servers_allow_list():
    """Mirrors run_service._check_onscreen_text: brand text + the accepted script's card titles
    and bodies + its disclaimer card, and the disclaimer must be among them."""
    script = _script()
    allowed = set(sch.VIDEO_BRAND_TEXT) | set(sch.LESSON_BRAND_TEXT) | {post_copy.IMAGE_FOOTER_HEAD}   # news-v23
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
    """Every word of each field, in order (a headline is drawn upper-cased: compared case-insensitively)."""
    for name in ("title", "body", "figure", "label"):
        text = getattr(spec, name, "")
        lines = layout.field_lines(name)
        assert [w.casefold() for ln in lines for w in ln.split()] == [w.casefold() for w in _words(text)], (
            name, text, lines)
        assert all(ln == " ".join(ln.split()) and ln for ln in lines)


#: news-v23: the content zone (cards.SAFE_X × [the brand row's foot + 36, TEXT_ZONE_Y[1]] — look.VIDEO_FRAME.area);
#: the brand row (the tile, "Caydex", the kicker pill) above it, the short footer under the caption band, nothing in
#: the safe bottom.
CONTENT = look.VIDEO_FRAME.area
CHROME = (cards.SAFE_X[0], BRAND_TOP - 10, cards.SAFE_X[1], SAFE_BOTTOM)


def _chrome_fields(layout) -> set:
    """The fields a 9:16 layout draws in its brand row or footer (an opening card's kicker is its `badge`; a data
    card's its `kicker`)."""
    out = {"brand", "pill", "footer", "kicker"}
    if layout.kind == "opening":
        out.add("badge")
    return out


def _assert_ink_inside(layout: cards.CardLayout) -> None:
    """Every content line, disc and card inside the content zone; the brand row and the footer inside the card's
    chrome (SAFE_X × [the brand row, the safe bottom]), the footer under the caption band; every box inside it."""
    chrome = _chrome_fields(layout)
    for ln in layout.lines:
        if ln.field in chrome:
            assert cards._within(ln.ink, CHROME), (ln.field, ln.text, ln.ink)
            if ln.field == "footer":
                assert ln.ink[1] >= cards.CAPTION_BAND_Y[1], (ln.text, ln.ink)
            else:
                assert ln.ink[3] <= CONTENT[1], (ln.field, ln.ink)
        elif ln.field != "art_label":
            assert cards._within(ln.ink, CONTENT), (ln.field, ln.text, ln.ink)
    for b in (getattr(layout, "logo", None), *(t[0] for t in layout.tiles)):
        if b:
            assert cards._within(b, CONTENT), b
    for r in getattr(layout, "rects", ()):
        assert cards._within(r.box, (CHROME[0], CHROME[1], CHROME[2], CONTENT[3])), r


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
        assert layout.rule is None and layout.badge is None and layout.panel is None


def test_a_word_too_wide_at_the_start_size_steps_down_instead_of_breaking():
    word = "Counterrevolutionaries"
    column = CONTENT[2] - CONTENT[0] - 16
    assert cards._font(FONT, cards.TITLE_STYLE.start, "basic").getlength(word.upper()) > column
    spec = cards.CardSpec("text", title=f"The {word} Idea", body="Short body.")
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    assert layout.step > 0
    assert word.upper() in layout.field_lines("title")                          # whole, own line (upper-cased)
    _assert_all_words_in_order(layout, spec)


@pytest.mark.parametrize("spec", [
    cards.CardSpec("text", title="Pneumonoultramicroscopicsilicovolcanoconiosis", body="b"),
    cards.CardSpec("text", title="t", body="A Pneumonoultramicroscopicsilicovolcanoconiosisesisis case"),
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
    with pytest.raises(cards.CardOverflow, match="line|room"):
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


_TEXT_STYLES = (cards.TITLE_STYLE, cards.BODY_STYLE, cards.FIGURE_STYLE, cards.LABEL_STYLE, cards.STAT_BODY_STYLE,
                cards.DISCLAIMER_STYLE, cards.CTA_STYLE, cards.WORDMARK_STYLE, cards.STORE_CTA_STYLE, cards.HOOK_STYLE,
                cards.CHIP_STYLE, cards.OPENING_HEADLINE_STYLE, cards.OPENING_FIGURE_STYLE, cards.IMAGE_TITLE_STYLE,
                cards.IMAGE_PARAGRAPH_STYLE)


def test_sizes_step_from_start_to_floor_in_bounded_integer_steps():
    for style in (*_TEXT_STYLES, cards.OPENING_DISC, cards.OPENING_ART_DISC, cards.BRAND_TILE_STYLE):
        sizes = [style.size(i) for i in range(cards.RAMP_STEPS + 1)]
        assert sizes[0] == style.start and sizes[-1] == style.floor
        assert sizes == sorted(sizes, reverse=True) and all(isinstance(s, int) for s in sizes)
        assert style.leading >= 1.0
    for style in _TEXT_STYLES:
        if style not in (cards.CHIP_STYLE,):
            assert style.leading >= 1.04, style


def test_every_text_is_at_least_22px_at_every_step():
    """news-v23 (the spec: "every text ≥ 22 px except logo wordmarks"): no text style of a card, a picture or a data
    card steps below 22 px — the data card's legibility floor (news_layouts.DATA_CARD_MIN_PX) is therefore met by
    construction — and the footer and kicker floors are 22 px too."""
    from marketing import news_layouts as nl_

    styles = [*_TEXT_STYLES, *(v for k, v in vars(nl_).items() if k.endswith("_STYLE") and isinstance(v, cards._Style))]
    assert len(styles) > 30
    for style in styles:
        assert style.floor >= 22 == nl_.DATA_CARD_MIN_PX, style
    assert look.FOOTER_FLOOR_PX == look.KICKER_FLOOR_PX == 22


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
    # Batch 2: the App Store end card and the lesson hook card
    "disclaimer (App Store CTA, logo)": (cards.CardSpec("disclaimer", body=DISCLAIMER, cta=cards.CTA), LOGO),
    "disclaimer (App Store CTA, no logo)": (cards.CardSpec("disclaimer", body=DISCLAIMER, cta=cards.CTA), None),
    "hook": (cards.CardSpec("hook", title=HOOK), None),
    "hook (writer max)": (cards.CardSpec("hook", title=MAX_HOOK), None),
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


@pytest.mark.parametrize("name", [n for n in sorted(_KINDS_TO_RENDER) if _KINDS_TO_RENDER[n][1] is None])
def test_only_brand_colours_and_their_alpha_blends(name):
    """news-v23: every pixel off the canvas is a look colour or an anti-aliased blend (the brand tile excepted —
    the vendored logo's own pixels)."""
    spec, _ = _KINDS_TO_RENDER[name]
    im = _png(cards.render_card(spec, font_path=FONT))
    off = _off_look(im, exclude=[_brand_tile()])
    assert off == [], f"{name}: {len(off)} off-look colours, e.g. {off[:5]}"


def test_the_off_look_check_catches_an_off_palette_pixel():
    """Mutation check of the check itself: one magenta pixel on a card is caught."""
    im = _png(cards.render_card(_KINDS_TO_RENDER["text"][0], font_path=FONT)).copy()
    im.putpixel((540, 700), (255, 0, 255))
    assert (255, 0, 255) in _off_look(im, exclude=[_brand_tile()])


def test_the_title_is_white_then_gold_and_the_body_ink_on_its_card():
    spec = _KINDS_TO_RENDER["text"][0]
    layout = cards.layout_card(spec, font_path=FONT)
    fills = [ln.fill for ln in layout.lines if ln.field == "title"]
    assert fills[0] == look.WHITE and fills[-1] == look.GOLD and set(fills) == {look.WHITE, look.GOLD}
    assert {ln.fill for ln in layout.lines if ln.field == "body"} == {look.INK}
    im = _png(cards.render_card(spec, font_path=FONT))
    colours = {c for _, c in im.getcolors(maxcolors=1 << 20)}
    assert {look.WHITE, look.GOLD, look.INK, look.CARD} <= colours     # the warm card under the body


# ── badge ────────────────────────────────────────────────────────────────────


def test_the_badge_is_drawn_only_when_set():
    base = dict(title="Sample", body="Illustrative figures.")
    plain = cards.layout_card(cards.CardSpec("text", **base), font_path=FONT)
    badged = cards.layout_card(cards.CardSpec("text", badge="Sample data", **base), font_path=FONT)
    assert plain.badge is None and badged.badge is not None
    assert badged.field_lines("badge") == ["Sample data"] and plain.field_lines("badge") == []
    # a chip above the title, at the content column's left
    title_top = min(ln.ink[1] for ln in badged.lines if ln.field == "title")
    assert badged.badge[3] < title_top and badged.badge[0] == CONTENT[0] + 8

    def fill_pixels(spec):          # the chip is PILL under GOLD ink; nothing else on these cards is PILL
        return dict((c, n) for n, c in _png(cards.render_card(spec, font_path=FONT)).getcolors(1 << 20)).get(
            look.PILL, 0)

    assert fill_pixels(cards.CardSpec("text", badge="Sample data", **base)) > 3000
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
    side = cards.BRAND_TILE_STYLE.size(layout.step)
    assert (x1 - x0, y1 - y0) == (side, side) and cards._within(layout.logo, CONTENT)
    assert not _pure_page(im, layout.logo)
    tile = im.crop(layout.logo)
    # the tile's corners are rounded (the canvas shows), its mark is light grey on the logo's dark ground
    assert tile.getpixel((0, 0)) == CANVAS.getpixel((x0, y0))
    assert max(tile.getextrema()[0]) > 200


def test_a_transparent_or_non_square_logo_is_composited_on_the_page(tmp_path):
    p = tmp_path / "logo.png"
    im = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
    im.paste((250, 250, 250, 255), (150, 25, 250, 75))
    im.save(p)
    spec = cards.CardSpec("brand")
    data = cards.render_card(spec, font_path=FONT, logo_path=str(p))
    out = _assert_nothing_outside_the_zone(data, "brand, transparent logo")
    box = cards.layout_card(spec, font_path=FONT, with_logo=True).logo
    tile = out.crop(box)
    w = box[2] - box[0]
    assert tile.getpixel((w // 2, 2)) == PAGE_RGB                          # the transparent rows: the tile's ground
    assert min(tile.getpixel((w // 2, w // 2))) > 200                      # the mark, centred


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
from PIL import Image, ImageChops
from marketing import cards, look
assert cards.raqm_available(), "no raqm"
F, L, D = sys.argv[1], sys.argv[2], sys.argv[3]
base = look.canvas(look.VIDEO_FRAME)
specs = [cards.CardSpec("brand"), cards.CardSpec("text", title=sys.argv[4], body=sys.argv[5]),
         cards.CardSpec("stat", figure="$6.9 billion", label="Deal value", body="As filed.", badge="Sample data"),
         cards.CardSpec("disclaimer", body=D), cards.CardSpec("disclaimer", body=D, cta=cards.CTA),
         cards.CardSpec("hook", title=sys.argv[4])]
for s in specs:
    a = cards.render_card(s, font_path=F, logo_path=L, layout_engine="raqm")
    assert a == cards.render_card(s, font_path=F, logo_path=L, layout_engine="raqm")
    assert a != cards.render_card(s, font_path=F, logo_path=L, layout_engine="basic") or s.kind == "brand"
    im = Image.open(io.BytesIO(a)); im.load()
    for box in [(0, 0, 1080, 140), (0, cards.TEXT_ZONE_Y[1], 1080, cards.CAPTION_BAND_Y[1]), (0, 1500, 1080, 1920),
                (0, 140, cards.SAFE_X[0], 1500), (cards.SAFE_X[1], 140, 1080, 1500)]:
        assert ImageChops.difference(im.crop(box), base.crop(box)).getbbox() is None, (s.kind, box)
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


# ── the post image (drop 1, 2026-10-09: contract C5) ─────────────────────────

FOOTER = post_copy.image_footer(date(2026, 10, 9))
IMG_TITLE = "Mr. Market's mood is not the business"
IMG_PARAGRAPHS = (
    "Every day, a moody partner offers to buy your share of the business or sell you his.",
    "Some days he is gloomy and names a low price. Some days he is giddy and names a high one.",
    "You never have to accept his offer. The business keeps doing its work whatever he says.",
)
#: The writer's maxima (contract C8: a title of ≤ 70 characters and ≤ 10 words; 2-4 paragraphs of
#: ≤ 220 characters), in deliberately long words: the worst a legal image can be.
MAX_IMG_TITLE = "Understanding Extraordinary Diversification Throughout Uncertain Times"   # 70
MAX_IMG_PARAGRAPH = ("Institutional investors frequently rebalance diversified portfolios quarterly, maintaining "
                     "predetermined allocations between international equities, government securities, "
                     "corporate obligations, infrastructure, commodities.")[:220]
MAX_IMG_PARAGRAPH_2 = ("Understanding extraordinary international diversification throughout unpredictable "
                       "economic environments requires institutional discipline, comprehensive documentation, "
                       "and considerable patience whenever volatility.")[:220]


def _img(**over) -> "cards.ImageSpec":
    base = dict(title=IMG_TITLE, paragraphs=IMG_PARAGRAPHS, footer=FOOTER)
    base.update(over)
    return cards.ImageSpec(**base)


def _max_img() -> "cards.ImageSpec":
    return cards.ImageSpec(MAX_IMG_TITLE, (MAX_IMG_PARAGRAPH, MAX_IMG_PARAGRAPH_2) * 2, FOOTER)


def _image_script(**over) -> Dict:
    s = _script(image_post={"title": IMG_TITLE, "paragraphs": list(IMG_PARAGRAPHS)}, image_footer=FOOTER)
    s.update(over)
    return s


def _jpeg_markers(data: bytes) -> List[int]:
    """The JPEG's marker codes up to the first scan (SOS), walking the segment lengths."""
    assert data[:2] == b"\xff\xd8", "not a JPEG (no SOI)"
    out, i = [], 2
    while i + 4 <= len(data):
        assert data[i] == 0xFF, f"no marker at {i}"
        code = data[i + 1]
        out.append(code)
        if code == 0xDA:                       # SOS: entropy-coded data follows
            break
        (n,) = struct.unpack(">H", data[i + 2:i + 4])
        i += 2 + n
    return out


def test_the_post_image_constants_mirror_the_server():
    assert cards.POST_IMAGE_MAX_BYTES == sch.POST_IMAGE_MAX_BYTES == 950_000
    assert cards.POST_IMAGE_EXT == sch.POST_IMAGE_EXT == "jpg"
    assert cards.POST_IMAGE_EXT in sch.ASSET_KIND_EXTENSIONS["card"]
    assert cards.IMAGE_ROLE_POST == sch.IMAGE_ROLE_POST and cards.IMAGE_ROLE_POST in sch.IMAGE_ROLES
    assert (cards.IMAGE_PARAGRAPHS_MIN, cards.IMAGE_PARAGRAPHS_MAX) == (
        sch.IMAGE_POST_PARAGRAPHS_MIN, sch.IMAGE_POST_PARAGRAPHS_MAX)
    assert (cards.IMAGE_WIDTH, cards.IMAGE_HEIGHT) == (1080, 1350)
    assert cards.IMAGE_WIDTH * 5 == cards.IMAGE_HEIGHT * 4                      # 4:5
    q = cards.JPEG_QUALITIES
    assert list(q) == sorted(set(q), reverse=True) and all(1 <= v <= 95 for v in q)
    # v4: Batch 2; v5 (2026-11-15): the `feature` image + the art (C); v6 (news-v23, 2026-11-19): the approved look
    # on every card and picture
    assert cards.CARD_RENDER_VERSION == "cards/v6"


def test_image_for_script_reads_the_worker_script_verbatim():
    odd_title, odd_para = "  Two  spaces, kept  ", "A paragraph\nwith a newline and   runs."
    spec = cards.image_for_script(_image_script(
        image_post={"title": odd_title, "paragraphs": [odd_para, "Second."], "extra": "ignored"}))
    assert spec == cards.ImageSpec(odd_title, (odd_para, "Second."), FOOTER)   # never stripped
    assert cards.image_for_script(_script()) is None                          # no image_post key
    assert cards.image_for_script(_image_script(image_post=None)) is None
    assert cards.image_for_script(_image_script(image_post=None, image_footer=None)) is None


@pytest.mark.parametrize("over", [
    {"image_post": "a string"}, {"image_post": ["a", "list"]},
    {"image_post": {"title": "t", "paragraphs": "one string"}},
    {"image_post": {"title": "t", "paragraphs": ("a", "b")}},                 # JSON never sends a tuple
    {"image_post": {"title": "t"}},
    {"image_post": {"title": "t", "paragraphs": ["only one"]}},
    {"image_post": {"title": "t", "paragraphs": ["a", "b", "c", "d", "e"]}},
    {"image_post": {"title": "  ", "paragraphs": ["a", "b"]}},
    {"image_post": {"title": None, "paragraphs": ["a", "b"]}},
    {"image_post": {"title": 7, "paragraphs": ["a", "b"]}},
    {"image_post": {"title": "t", "paragraphs": ["a", " \n"]}},
    {"image_post": {"title": "t", "paragraphs": ["a", 5]}},
    {"image_post": {"title": "t", "paragraphs": ["a", None]}},
    {"image_footer": None}, {"image_footer": ""}, {"image_footer": "   "}, {"image_footer": 3},
])
def test_a_malformed_image_post_or_a_missing_footer_is_a_value_error(over):
    with pytest.raises(ValueError):
        cards.image_for_script(_image_script(**over))


def test_image_for_script_refuses_a_script_that_is_not_a_dict():
    with pytest.raises(ValueError):
        cards.image_for_script(["not", "a", "dict"])  # type: ignore[arg-type]


@pytest.mark.parametrize("kwargs, exc", [
    ({"paragraphs": ["a", "b"]}, TypeError),                                  # must be a tuple
    ({"paragraphs": ("a",)}, ValueError),
    ({"paragraphs": ("a",) * 5}, ValueError),
    ({"paragraphs": ("a", 1)}, TypeError),
    ({"paragraphs": ("a", "")}, ValueError),
    ({"title": ""}, ValueError), ({"title": None}, TypeError),
    ({"footer": "\t"}, ValueError), ({"footer": b"bytes"}, TypeError),
])
def test_imagespec_refuses_what_it_cannot_draw_faithfully(kwargs, exc):
    with pytest.raises(exc):
        _img(**kwargs)


def test_image_onscreen_strings_are_verbatim_in_order_and_once():
    extra = [cards.LESSON_KICKER, cards.BRAND_ROW_WORD]                    # news-v23: the brand row
    assert cards.image_onscreen_strings(_img()) == [IMG_TITLE, *IMG_PARAGRAPHS, FOOTER, *extra]
    twice = _img(paragraphs=(IMG_TITLE, "Different."))
    assert cards.image_onscreen_strings(twice) == [IMG_TITLE, "Different.", FOOTER, *extra]


def test_what_the_worker_registers_passes_the_servers_schema_and_its_allow_list():
    """The post image's registration body, exactly as render.stage_render builds it, validates
    against the server's own request schema; and every declared string is the accepted image
    post's title/paragraph or its footer, the footer among them (run_service._check_post_image_text)."""
    script = _image_script()
    spec = cards.image_for_script(script)
    image = cards.render_image(spec, font_path=FONT, layout_engine="basic")
    drawn = cards.image_onscreen_strings(spec)
    body = sch.AssetRegisterRequest(
        kind="card", ext=cards.POST_IMAGE_EXT, sha256="a" * 64, bytes=len(image.data),
        metadata={"onscreen_text": drawn, "image_role": cards.IMAGE_ROLE_POST, "render_key": "k" * 64,
                  "card_version": cards.CARD_RENDER_VERSION})
    assert body.metadata["onscreen_text"] == drawn
    stored = sch.normalize_image_post(script["image_post"])
    allowed = {stored["title"], *stored["paragraphs"], script["image_footer"], *sch.LESSON_BRAND_TEXT}
    assert [t for t in drawn if t not in allowed] == [] and script["image_footer"] in drawn
    sch.validate_onscreen_text(drawn)
    # …and the schema really is the fence: no footer declared, or too many bytes, is refused.
    with pytest.raises(ValueError):
        sch.AssetRegisterRequest(kind="card", ext="jpg", sha256="a" * 64, bytes=cards.POST_IMAGE_MAX_BYTES + 1,
                                 metadata={"onscreen_text": drawn, "image_role": cards.IMAGE_ROLE_POST})


def _assert_every_word_in_order(layout: "cards.CardLayout", spec: "cards.ImageSpec") -> None:
    fields = [("title", spec.title)] + [(f"paragraph{i}", p) for i, p in enumerate(spec.paragraphs)] + [
        ("footer", spec.footer), ("kicker", cards.LESSON_KICKER), ("brand", cards.BRAND_ROW_WORD)]
    for name, text in fields:
        lines = layout.field_lines(name)
        assert [w.casefold() for ln in lines for w in ln.split()] == text.casefold().split(), (name, text, lines)
    assert {ln.field for ln in layout.lines} == {name for name, _ in fields}


#: news-v23: the lesson picture's content area (look.POST_FRAME: the cards' 56 px side edges, the headline at 140)
IMAGE_AREA = look.POST_FRAME.area


def _assert_image_ink_inside(layout: "cards.CardLayout") -> None:
    """The brand row and the kicker above the content area, every content line and card inside it (the panel:
    its top at the headline, its bottom over the footer), the footer centred on the canvas under it."""
    assert layout.panel and cards._within(layout.panel, IMAGE_AREA)
    for ln in layout.lines:
        if ln.field in ("brand", "kicker"):
            assert ln.ink[3] <= IMAGE_AREA[1] and cards._within(ln.ink, (56, 30, 1024, IMAGE_AREA[1]))
        elif ln.field == "footer":
            assert ln.ink[1] >= layout.panel[3] and cards._within(ln.ink, (60, 0, 1020, cards.IMAGE_HEIGHT - 20))
        else:
            assert cards._within(ln.ink, layout.panel), (ln.field, ln.text, ln.ink, layout.panel)
    for r in layout.rects:
        assert cards._within(r.box, (56, 30, 1024, layout.panel[3])), r


@pytest.mark.parametrize("engine", ["basic"])
def test_the_writers_maximum_image_fits_whole_with_a_step_to_spare(engine):
    spec = _max_img()
    assert len(MAX_IMG_TITLE) <= 70 and len(MAX_IMG_TITLE.split()) <= 10
    assert all(len(p) <= 220 for p in spec.paragraphs) and len(spec.paragraphs) == 4
    layout = cards.layout_image(spec, font_path=FONT, layout_engine=engine)
    _assert_every_word_in_order(layout, spec)
    _assert_image_ink_inside(layout)
    assert layout.step < cards.RAMP_STEPS, "the writer's maximum must not need the floor sizes"


def test_a_real_image_draws_every_word_at_the_start_sizes():
    layout = cards.layout_image(_img(), font_path=FONT, layout_engine="basic")
    _assert_every_word_in_order(layout, _img())
    _assert_image_ink_inside(layout)
    assert layout.step == 0 and layout.kind == "post_image"
    assert all(ln.anchor == "ma" for ln in layout.lines if ln.field == "footer")   # centred


def test_two_paragraphs_and_a_short_title_fit_too():
    spec = _img(title="Start early", paragraphs=("Time does most of the work.", "Fees compound too."))
    layout = cards.layout_image(spec, font_path=FONT, layout_engine="basic")
    _assert_every_word_in_order(layout, spec)
    _assert_image_ink_inside(layout)


def _raw_image(spec: "cards.ImageSpec") -> Tuple[Image.Image, "cards.CardLayout"]:
    return cards.draw_image(spec, font_path=FONT, layout_engine="basic")


@pytest.mark.parametrize("spec", [_img(), _max_img()], ids=["real", "writer_max"])
def test_nothing_is_drawn_in_the_image_margins_and_only_brand_colours(spec):
    """news-v23: the lesson picture is the look's canvas wherever nothing is drawn — the side margins of the cards,
    the gap over the footer — and every pixel off it is a look colour or a blend (the brand tile excepted)."""
    im, layout = _raw_image(spec)
    assert im.size == (cards.IMAGE_WIDTH, cards.IMAGE_HEIGHT) and im.mode == "RGB"
    w, h = cards.IMAGE_WIDTH, cards.IMAGE_HEIGHT
    footer_top = min(ln.ink[1] for ln in layout.lines if ln.field == "footer")
    for name, box in {"left": (0, IMAGE_AREA[1], IMAGE_AREA[0], h), "right": (IMAGE_AREA[2], IMAGE_AREA[1], w, h),
                      "panel→footer": (0, layout.panel[3], w, footer_top - 2),
                      "under the footer": (0, h - 20, w, h)}.items():
        assert _pure_page(im, box), f"something was drawn in the {name} margin {box}"
    assert not _pure_page(im, layout.panel)                                    # anti-vacuity
    off = _off_look(im, exclude=[_brand_tile(look.POST_FRAME)])
    assert off == [], f"{len(off)} off-look colours, e.g. {off[:5]}"
    colours = {c for _, c in im.getcolors(maxcolors=1 << 20)}
    assert {look.WHITE, look.GOLD, look.INK, look.CARD, look.PILL, (217, 160, 30)} <= colours


def test_the_post_image_is_a_baseline_jpeg_under_the_cap_with_no_metadata():
    image = cards.render_image(_img(), font_path=FONT, layout_engine="basic")
    data = image.data
    assert len(data) <= cards.POST_IMAGE_MAX_BYTES and image.quality == cards.JPEG_QUALITIES[0]
    markers = _jpeg_markers(data)
    assert 0xC0 in markers, "not a baseline (SOF0) JPEG"
    assert not {0xC1, 0xC2, 0xC3} & set(markers), "progressive / extended / lossless JPEG"
    assert 0xE1 not in markers and 0xE2 not in markers, "EXIF / ICC metadata make bytes drift"
    decoded = Image.open(io.BytesIO(data))
    decoded.load()
    assert decoded.format == "JPEG" and decoded.size == (1080, 1350) and decoded.mode == "RGB"
    assert not decoded.info.get("progressive") and not decoded.info.get("progression")
    # The JPEG is the drawn image (4:4:4, high quality): every pixel close to the raw one.
    raw, _ = _raw_image(_img())
    diff = [abs(a - b) for a, b in zip(raw.tobytes(), decoded.tobytes())]
    assert sum(diff) / len(diff) < 2.0


def test_the_post_image_bytes_are_deterministic_and_follow_the_text():
    a = cards.render_image(_img(), font_path=FONT, layout_engine="basic").data
    cards._font.cache_clear()
    b = cards.render_image(_img(), font_path=FONT, layout_engine="basic").data
    assert a == b
    c = cards.render_image(_img(title=IMG_TITLE + "!"), font_path=FONT, layout_engine="basic").data
    # news-v23: a lesson footer no longer carries the date, so another FOOTER text is a template family's line
    d = cards.render_image(_img(footer=post_copy.image_footer(date(2026, 10, 10), "template",
                                                              source=post_copy.FOOTER_SOURCE_FILINGS)),
                           font_path=FONT, layout_engine="basic").data
    assert len({a, c, d}) == 3


def test_the_quality_steps_down_until_the_image_fits_and_never_ships_bigger():
    top = cards.render_image(_img(), font_path=FONT, layout_engine="basic")
    stepped = cards.render_image(_img(), font_path=FONT, layout_engine="basic", max_bytes=len(top.data) - 1)
    assert stepped.quality < top.quality and len(stepped.data) <= len(top.data) - 1
    assert stepped.quality in cards.JPEG_QUALITIES
    with pytest.raises(cards.ImageTooLarge) as ei:
        cards.render_image(_img(), font_path=FONT, layout_engine="basic", max_bytes=1000)
    assert all(f"q{q}=" in str(ei.value) for q in cards.JPEG_QUALITIES)       # every step was tried
    for bad in (0, -5, True, 1.5, None):
        with pytest.raises(ValueError):
            cards.render_image(_img(), font_path=FONT, max_bytes=bad)  # type: ignore[arg-type]
    im, _ = _raw_image(_img())
    for bad_q in (0, 96, True, 80.0):
        with pytest.raises(ValueError):
            cards.encode_jpeg(im, bad_q)  # type: ignore[arg-type]


def test_an_image_that_cannot_fit_whole_is_an_overflow_never_a_cut():
    long = " ".join(["Diversification"] * 37)[:600]
    with pytest.raises(cards.CardOverflow):
        cards.layout_image(_img(paragraphs=(long,) * 4), font_path=FONT, layout_engine="basic")
    with pytest.raises(cards.CardOverflow, match="wide"):
        cards.layout_image(_img(title="Pneumonoultramicroscopicsilicovolcanoconiosis" * 2),
                           font_path=FONT, layout_engine="basic")
    t0 = time.monotonic()
    with pytest.raises(cards.CardOverflow, match=str(cards.MAX_STRING_CHARS)):
        cards.layout_image(_img(paragraphs=("a " * 400, "b")), font_path=FONT)
    with pytest.raises(cards.CardOverflow, match=str(cards.MAX_STRING_CHARS)):
        cards.render_image(_img(footer="y" * 10_000_000), font_path=FONT)
    assert time.monotonic() - t0 < 2.0


@pytest.mark.parametrize("over, missing", [
    ({"title": "Growth 🚀"}, "🚀"),
    ({"paragraphs": ("Plain.", "中文 text")}, "中"),
    ({"footer": FOOTER + " ✅"}, "✅"),
])
def test_check_image_glyphs_refuses_what_the_font_cannot_draw(over, missing):
    with pytest.raises(cards.MissingGlyphs) as ei:
        cards.check_image_glyphs(_img(**over), FONT)
    assert missing in ei.value.chars


def test_the_real_footer_and_sample_have_every_glyph():
    # news-v23 (restyle 2026-11-19): the lesson footer is one short line with no separator; a template family's
    # footer carries the "·" and the en dash, so both are checked
    assert "·" not in FOOTER
    dated = post_copy.image_footer(date(2026, 10, 10), "template", source=post_copy.FOOTER_SOURCE_FORM4,
                                   when="filed Dec 31, 2026–Jan 4, 2027")
    assert "·" in dated and "–" in dated
    cards.check_image_glyphs(_img(footer=dated), FONT)
    cards.check_image_glyphs(_img(), FONT)
    cards.check_image_glyphs(_max_img(), FONT)


_RAQM_IMAGE_CHECK = r"""
import sys
from marketing import cards
assert cards.raqm_available(), "no raqm"
F, T, P1, P2, FOOT = sys.argv[1:6]
spec = cards.ImageSpec(T, (P1, P2, P1, P2), FOOT)
layout = cards.layout_image(spec, font_path=F, layout_engine="raqm")
assert layout.engine == "raqm" and layout.step < cards.RAMP_STEPS, layout.step
for name, text in [("title", T), ("paragraph0", P1), ("paragraph1", P2), ("footer", FOOT)]:
    assert [w.casefold() for ln in layout.field_lines(name) for w in ln.split()] == text.casefold().split(), name
a = cards.render_image(spec, font_path=F, layout_engine="raqm").data
assert a == cards.render_image(spec, font_path=F, layout_engine="raqm").data
assert len(a) <= cards.POST_IMAGE_MAX_BYTES
im, lay = cards.draw_image(spec, font_path=F, layout_engine="raqm")
from PIL import ImageChops
from marketing import look
base = look.canvas(look.POST_FRAME)
for box in [(0, 0, 1080, 40), (0, 120, 56, 1250), (1024, 120, 1080, 1250), (0, 1340, 1080, 1350)]:
    assert ImageChops.difference(im.crop(box), base.crop(box)).getbbox() is None, box
print("RAQM-IMAGE-OK")
"""


def test_raqm_lays_out_the_writers_maximum_image_too():
    env = _raqm_env()
    if env is None:
        pytest.skip("libraqm not available to Pillow")
    env.pop("PYTHONPATH", None)
    proc = subprocess.run([sys.executable, "-c", _RAQM_IMAGE_CHECK, FONT, MAX_IMG_TITLE, MAX_IMG_PARAGRAPH,
                           MAX_IMG_PARAGRAPH_2, FOOTER],
                          cwd=_BACKEND, env=env, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 and "no raqm" in proc.stderr:
        pytest.skip("libraqm not loadable in a subprocess either")
    assert proc.returncode == 0 and "RAQM-IMAGE-OK" in proc.stdout, proc.stderr[-2000:]


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


# ── the local preview's post image (marketing/preview.py, review worker:W3) ────
# `preview_image` runs AFTER the narration and the full video render: it must never raise, or the
# report is lost; and it draws the image only when the script carries image_post AND a footer.


def test_the_preview_skips_the_image_of_a_real_switches_off_script(tmp_path):
    from marketing import preview
    # What the server's worker_script sends while MARKETING_IMAGE_POSTS is off: the writer's
    # image_post, no footer. `cards.image_for_script` raises ValueError on it (the old crash).
    script = _image_script(image_footer=None, post_formats={"x": "text", "tiktok": "video"})
    with pytest.raises(ValueError):
        cards.image_for_script(script)
    report = preview.preview_image(script, tmp_path, FONT)
    assert set(report) == {"skipped"} and "image_footer" in report["skipped"]
    stored = {k: v for k, v in _image_script().items() if k != "image_footer"}   # no key at all
    assert set(preview.preview_image(stored, tmp_path, FONT)) == {"skipped"}
    assert preview.preview_image(_image_script(image_footer="   "), tmp_path, FONT).keys() == {"skipped"}
    assert not (tmp_path / "post_image.jpg").exists()


def test_the_preview_has_no_image_entry_without_an_image_post(tmp_path):
    from marketing import preview
    assert preview.preview_image(_script(), tmp_path, FONT) is None
    assert preview.preview_image(_image_script(image_post=None), tmp_path, FONT) is None
    assert not (tmp_path / "post_image.jpg").exists()


def test_the_preview_renders_the_demo_image(tmp_path):
    from marketing import preview
    report = preview.preview_image(preview.DEMO, tmp_path, FONT)
    data = (tmp_path / "post_image.jpg").read_bytes()
    assert report["bytes"] == len(data) <= cards.POST_IMAGE_MAX_BYTES and data[:2] == b"\xff\xd8"
    # news-v23: the demo is today's lesson picture — the short footer and a news-v23 read-back (finding: it drew the
    # long footer, "Caydex" twice and "Educational only" on the image, and this test accepted whatever DEMO said)
    assert preview.DEMO["image_footer"] == cards.LESSON_FOOTER and preview.DEMO["look"] == cards.LOOK
    assert report["onscreen_text"][-3:] == [cards.LESSON_FOOTER, cards.LESSON_KICKER, cards.BRAND_ROW_WORD]
    assert "error" not in report and "skipped" not in report


@pytest.mark.parametrize("over, kind", [
    ({"image_post": {"title": "t", "paragraphs": ["only one"]}}, "ValueError"),
    ({"image_post": "a string"}, "ValueError"),
    ({"image_post": {"title": "Growth 🚀", "paragraphs": ["One.", "Two."]}}, "MissingGlyphs"),
    ({"image_post": {"title": "t", "paragraphs": ["a " * 400, "b"]}}, "CardOverflow"),
])
def test_a_preview_image_that_cannot_render_is_recorded_never_raised(tmp_path, over, kind):
    from marketing import preview
    report = preview.preview_image(_image_script(**over), tmp_path, FONT)
    assert set(report) == {"error"} and report["error"].startswith(f"{kind}: ")
    assert not (tmp_path / "post_image.jpg").exists()


@pytest.mark.parametrize("exc", [cards.ImageTooLarge("over the cap at every step"), cards.CardAssetError("font")])
def test_a_preview_image_over_the_cap_or_without_its_font_is_recorded(tmp_path, monkeypatch, exc):
    from marketing import preview

    def boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(cards, "render_image", boom)
    report = preview.preview_image(_image_script(), tmp_path, FONT)
    assert report == {"error": f"{type(exc).__name__}: {exc}"}
    assert not (tmp_path / "post_image.jpg").exists()


# ── drop 2a: the template opening card, per-line videos, logo plates ─────────
# A template (news) video opens on the COMPANY: its kicker, its logo(s) on light plates (a wordmark
# tile — the company name — when a logo did not verify), chip, figure and headline; then one text
# card per narration line, then the disclaimer. The strings it may draw are the server's
# template_onscreen.opening_strings (checked here against that module itself).

from app.services.marketing import template_onscreen as tos  # noqa: E402

_TPL_ENTRIES = [{"key": "GME", "name": "GameStop", "url": None, "sha256": None},
                {"key": "NVDA", "name": "NVIDIA", "url": None, "sha256": None}]
_OPENING = {"kicker": "FILED LAST WEEK · FORM 4", "logos": ["GME"], "chip": "GME", "figure": "$74.4M",
            "headline": "GameStop's CEO disclosed buying GameStop stock"}
_TPL_LINES = ["GameStop's chief executive disclosed buying about seventy four million dollars of stock.",
              "The filings came in between November ninth and November thirteenth this year.",
              "The purchases covered one million shares across three separate trades.",
              "Form four filings are public, and the figures here are as filed."]
#: A ceo_buys day's cards, in the lexicon's own order and words (news_templates ins.c1 … ins.c4): the fact
#: card "The purchases" (the picture's own line) and two hedges the template keeps on screen on purpose.
_TPL_CARDS = [{"title": "Who filed", "body": "GameStop's CEO, on SEC Form 4."},
              {"title": "The purchases", "body": "3 purchases · 1,000,000 shares · $74.4M"},
              {"title": "What a Form 4 is", "body": "Filed within two business days of an insider's trade"},
              {"title": "How the amount is counted", "body": "Shares times the cost of each share, per the filing"}]
TEMPLATE_DISCLAIMER = post_copy.disclaimer_card(date(2026, 11, 16), authorship="template")


def _template_script(**over):
    s = {"hook": "A chief executive disclosed a large purchase last week.", "video_script": list(_TPL_LINES),
         "cards": [dict(c) for c in _TPL_CARDS], "carousel_slides": [], "disclaimer_card": TEMPLATE_DISCLAIMER,
         "authorship": "template", "content_class": "C", "series": "ceo_buys", "video_layout": "per_line",
         "opening_card": dict(_OPENING), "logos": [dict(e) for e in _TPL_ENTRIES], "look": cards.LOOK}
    s.update(over)
    return s


def _red_logo(path: Path) -> str:
    im = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    im.paste((220, 20, 20, 255), (40, 40, 260, 160))
    im.save(path)
    return str(path)


def test_the_opening_kind_and_the_template_mirrors():
    assert "opening" in cards.KINDS and cards.CARD_RENDER_VERSION == "cards/v6"
    assert cards.LAYOUTS == tos.LAYOUTS and cards.OPENING_KEYS == tos.OPENING_KEYS
    assert cards.MAX_OPENING_LOGOS == tos.MAX_OPENING_LOGOS == 2
    assert cards.TEMPLATE_AUTHORSHIP == sch.TEMPLATE_AUTHORSHIP and cards.VIDEO_LAYOUT_PER_LINE == sch.VIDEO_LAYOUT_PER_LINE
    # the template disclaimer card is the template wording (D8), not the writer's
    assert "fixed template" in TEMPLATE_DISCLAIMER and "Written with AI" not in TEMPLATE_DISCLAIMER


@pytest.mark.parametrize("kwargs", [
    {"kind": "opening", "title": "h"},                                               # no kicker
    {"kind": "opening", "badge": "k"},                                               # no headline
    {"kind": "opening", "badge": "k", "title": "h"},                                 # no logo
    {"kind": "opening", "badge": "k", "title": "h", "body": "b",
     "logos": (cards.LogoArt("A", "Alpha"),)},                                       # draws no body
    {"kind": "opening", "badge": "k", "title": "h",
     "logos": (cards.LogoArt("A", "Alpha"),) * 2},                                   # a repeated logo
    {"kind": "opening", "badge": "k", "title": "h",
     "logos": tuple(cards.LogoArt(k, k) for k in "ABC")},                            # three logos
    {"kind": "text", "title": "t", "logos": (cards.LogoArt("A", "Alpha"),)},         # only an opening has logos
])
def test_an_opening_cardspec_refuses_what_it_cannot_draw(kwargs):
    with pytest.raises(ValueError):
        cards.CardSpec(**kwargs)
    with pytest.raises(TypeError):
        cards.CardSpec("opening", badge="k", title="h", logos=[cards.LogoArt("A", "Alpha")])   # a list


@pytest.mark.parametrize("resolved", [False, True], ids=["wordmark", "logo"])
def test_the_opening_card_draws_exactly_its_strings_and_the_server_allows_them(resolved, tmp_path):
    files = {"GME": tmp_path / "gme.png"} if resolved else {}
    if resolved:
        _red_logo(files["GME"])
    spec = cards.opening_spec(_OPENING, cards.logo_table(_template_script(), files))
    strings = cards.onscreen_strings(spec)
    server = tos.opening_strings(_OPENING, _TPL_ENTRIES)
    assert server and set(strings) <= set(server) | set(sch.VIDEO_BRAND_TEXT)
    if resolved:
        assert "GameStop" not in strings and strings == [s for s in server if s != "GameStop"] + [cards.BRAND_ROW_WORD]
    else:
        assert strings == server + [cards.BRAND_ROW_WORD]   # kicker, wordmark name, chip, figure, headline, brand
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    by_field: Dict[str, List[str]] = {}
    for ln in layout.lines:
        by_field.setdefault(ln.field, []).extend(ln.text.split())
    expected = {"badge": _OPENING["kicker"], "label": _OPENING["chip"], "figure": _OPENING["figure"],
                "title": _OPENING["headline"].upper(), "brand": cards.BRAND_ROW_WORD}
    if not resolved:
        expected["wordmark:GME"] = "GameStop"
    assert {f: " ".join(w) for f, w in by_field.items()} == expected
    assert len(layout.tiles) == 1 and layout.tiles[0][1].key == "GME"
    _assert_ink_inside(layout)
    for box, _art in layout.tiles:
        assert cards._within(box, CONTENT)
    data = cards.render_card(spec, font_path=FONT, layout_engine="basic")
    im = _assert_nothing_outside_the_zone(data, "opening")
    # the logo (pure red) is drawn on its plate, unaltered — and nowhere else is red
    px = im.load()
    box = layout.tiles[0][0]
    reds = [(x, y) for y in range(CONTENT[1], cards.TEXT_ZONE_Y[1], 4)
            for x in range(cards.SAFE_X[0], cards.SAFE_X[1], 4)
            if _red(px[x, y])]
    assert all(box[0] <= x < box[2] and box[1] <= y < box[3] for x, y in reds)
    assert bool(reds) == resolved
    assert (220, 20, 20) in {c for _n, c in im.crop(box).getcolors(1 << 20)} or not resolved


def test_the_opening_card_with_two_companies_and_no_chip_or_figure():
    card = {"kicker": "13F SEASON", "logos": ["GME", "NVDA"], "headline": "A filer reported two changes"}
    assert tos.validate_opening_card(card, tos.logo_keys(_TPL_ENTRIES)) is None
    spec = cards.opening_spec(card, cards.logo_table(_template_script()))
    assert cards.onscreen_strings(spec) == tos.opening_strings(card, _TPL_ENTRIES) + [cards.BRAND_ROW_WORD]
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    (a, _), (b, _) = layout.tiles
    assert a[2] < b[0] and a[1] == b[1] and (a[2] - a[0]) == (b[2] - b[0])          # side by side, equal
    _assert_nothing_outside_the_zone(cards.render_card(spec, font_path=FONT, layout_engine="basic"), "opening 2")


def test_the_opening_card_uses_only_neutral_colours_off_its_plates():
    """With no series the figure is neutral INK: every pixel is the look's (the disc is WHITE, its wordmark INK,
    its gold ring GOLD)."""
    spec = cards.opening_spec(_OPENING, cards.logo_table(_template_script()))
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    im = _png(cards.render_card(spec, font_path=FONT, layout_engine="basic"))
    box = layout.tiles[0][0]
    off = _off_look(im, exclude=[_brand_tile()])
    assert off == [], off[:5]
    assert {ln.fill for ln in layout.lines if ln.field == "figure"} == {look.INK}
    assert cards._rgb(cards.PLATE) == (255, 255, 255) and im.getpixel(((box[0] + box[2]) // 2, box[1] + 10)) == (255, 255, 255)


@pytest.mark.parametrize("series, figure, tone", [
    ("ceo_buys", "$74.4M", look.GREEN), ("insider_spotlight", "$2M", look.GREEN), ("congress_count", "3", look.GREEN),
    ("thirteen_f_spotlight", "+5.6M shares", look.GREEN), ("thirteen_f_spotlight", "\u221218.1M shares", look.RED),
    # news-v23 fix round: a ROUNDED cover figure reads through its "About" (it was drawn neutral INK); the
    # insider_buys roundup's amounts are GREEN only through look.GAIN_SERIES — pinned here too
    ("thirteen_f_spotlight", "About \u221218.1M shares", look.RED), ("thirteen_f_spotlight", "About +4.5M shares", look.GREEN),
    ("insider_buys", "$1.3M", look.GREEN),
    ("thirteen_f", "-4.3M", look.RED), ("thirteen_f", "3", look.INK), ("company_stakes", "$5B", look.GOLD_INK),
    ("theme_explainer", "14", look.GOLD_INK), ("earnings", "-$0.05", look.INK), ("money_map", "$2.94", look.INK),
    (None, "$74.4M", look.INK), ("unknown_series", "+5%", look.INK),
])
def test_the_covers_figure_takes_its_series_tone(series, figure, tone):
    """news-v23 colour rules (owner 2026-11-19): a purchase / a members' count GREEN, a 13F change GREEN up and RED
    down, a stake's amount and a theme's count GOLD_INK, an earnings figure and anything unknown neutral INK."""
    card = dict(_OPENING, figure=figure)
    spec = cards.opening_spec(card, cards.logo_table(_template_script()), series=series)
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    assert {ln.fill for ln in layout.lines if ln.field == "figure"} == {tone}
    script = _template_script(series=series, opening_card=card)
    assert cards.cards_for_script(script)[0] == spec


@pytest.mark.parametrize("name, tone", [("costco_money_map", look.GREEN), ("rivian_money_map", look.RED)])
def test_fidelity_the_money_map_cover_figure_takes_the_net_lines_tone(name, tone):
    """Fidelity review 2026-11-20 (HIGH): the Money Map cover's "per $100" figure was drawn INK while the picture's
    callout draws the same figure GREEN for a profit, RED for a loss (RESTYLE_SPEC §1 / §2.6). It now takes the tone of
    the post image's net line (its flow's last `style`: fill / outline). Mutation-checked by hand: without
    `money_map_net_tone` both covers read INK."""
    import test_marketing_news_templates as NT

    (_n, factory, run_date), = [x for x in NT.SAMPLES if x[0] == name]
    out = _T.compose(factory(), run_date=run_date, store_state="live", allow_x_url=False)
    entries = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in out["logo_refs"]]
    script = dict(out, look=cards.LOOK, logos=entries)
    cover = cards.cards_for_script(script, cards.logo_table(script, {}))[0]
    assert cover.kind == "opening" and cover.figure == out["opening_card"]["figure"]
    # owner decision 2026-11-20: a LOSS year's cover is its costs card — whose figure line ("so the net loss was …") is
    # RED as on the picture; the plain figure card it falls back to keeps the tone below
    assert (cover.image is not None) == (name == "rivian_money_map")
    if cover.image is not None:
        costs_layout = cards.layout_card(cover, font_path=FONT, layout_engine="basic")
        assert {ln.fill for ln in costs_layout.lines if ln.field == "costs.callout[1]"} == {tone}
        cover = dataclasses.replace(cover, image=None)
    layout = cards.layout_card(cover, font_path=FONT, layout_engine="basic")
    assert {ln.fill for ln in layout.lines if ln.field == "figure"} == {tone}


@pytest.mark.parametrize("series, spec, tone", [
    ("money_map", {"flow": [{"style": "fill"}, {"style": "outline"}]}, "loss"),
    ("money_map", {"flow": [{"style": "fill"}]}, "gain"),
    ("money_map", {"flow": []}, None), ("money_map", {"flow": [{"style": "dotted"}]}, None),
    ("money_map", None, None), ("money_map", {"flow": "x"}, None), ("money_map", {"flow": ["x"]}, None),
    ("earnings", {"flow": [{"style": "outline"}]}, None),
])
def test_fidelity_the_money_map_net_tone_reads_only_a_well_formed_flow(series, spec, tone):
    assert cards.money_map_net_tone(series, spec) == tone


@pytest.mark.parametrize("card, needle", [
    ("not an object", "object"),
    ({**_OPENING, "extra": "x"}, "unknown key"),
    ({k: v for k, v in _OPENING.items() if k != "headline"}, "no headline"),
    ({**_OPENING, "logos": []}, "logos"),
    ({**_OPENING, "logos": ["GME", "NVDA", "GME"]}, "logos"),
    ({**_OPENING, "logos": ["GME", "GME"]}, "repeats"),
    ({**_OPENING, "logos": ["ZZZ"]}, "no logo entry"),
    ({**_OPENING, "logos": "GME"}, "logos"),
    ({**_OPENING, "chip": " padded"}, "drawable"),
    ({**_OPENING, "headline": "two\nlines"}, "drawable"),
    ({**_OPENING, "figure": 74.4}, "drawable"),
])
def test_opening_spec_fails_loudly_on_drift(card, needle):
    with pytest.raises(ValueError, match=needle):
        cards.opening_spec(card, cards.logo_table(_template_script()))


def test_a_per_line_script_is_the_opening_one_card_per_line_and_the_disclaimer():
    specs = cards.cards_for_script(_template_script())
    assert [s.kind for s in specs] == ["opening", "text", "text", "text", "text", "disclaimer"]
    assert [(s.title, s.body) for s in specs[1:5]] == [(c["title"], c["body"]) for c in _TPL_CARDS]
    assert specs[0].badge == _OPENING["kicker"] and specs[0].logo_keys == ("GME",)
    assert specs[-1].body == TEMPLATE_DISCLAIMER
    # every string the video may draw is one the server allows (run_service D12: brand text ∪ card
    # titles/bodies ∪ the disclaimer ∪ the opening strings)
    allowed = set(sch.VIDEO_BRAND_TEXT) | {c[k] for c in _TPL_CARDS for k in ("title", "body")} | {
        TEMPLATE_DISCLAIMER} | set(tos.opening_strings(_OPENING, _TPL_ENTRIES))
    drawn = [t for s in specs for t in cards.onscreen_strings(s)]
    assert set(drawn) <= allowed and TEMPLATE_DISCLAIMER in drawn
    cards.check_glyphs(specs, FONT)
    for s in specs:
        _assert_nothing_outside_the_zone(cards.render_card(s, font_path=FONT, layout_engine="basic"), s.kind)


@pytest.mark.parametrize("over, needle", [
    ({"video_layout": None}, "needs video_layout"),                                 # a template, lesson-shaped
    ({"video_layout": "grid"}, "is not 'per_line'"),
    ({"opening_card": None}, "opening_card"),
    ({"cards": _TPL_CARDS[:3]}, "one card per narration line"),
    ({"video_script": _TPL_LINES[:3]}, "one card per narration line"),
    ({"cards": [], "video_script": []}, "one card per narration line"),
    ({"cards": [*_TPL_CARDS[:3], {"title": " ", "body": ""}]}, "draws nothing"),
    ({"authorship": "robot"}, "authorship"),
])
def test_a_template_video_never_falls_back_to_the_lesson_shape(over, needle):
    with pytest.raises(ValueError, match=needle):
        cards.cards_for_script(_template_script(**over))


def test_a_lesson_script_keeps_the_drop_1_card_set_whatever_logos_it_is_given(tmp_path):
    lesson = _script()
    assert cards.cards_for_script(lesson) == cards.cards_for_script(lesson, logos={}) == cards.cards_for_script(
        lesson, logos=cards.logo_table(_template_script()))
    assert [s.kind for s in cards.cards_for_script(lesson)] == ["text", "text", "text", "disclaimer"]
    assert not cards.is_template(lesson) and not cards.is_template({**lesson, "authorship": "ai"})
    with pytest.raises(ValueError):
        cards.cards_for_script({**lesson, "video_layout": "per_line"})              # no opening card


#: A lesson card's, end card's, brand card's and lesson post image's bytes in the approved look (news-v23, cards/v6;
#: Pillow 12.0.0, the basic layout engine) — re-pinned when the look changed every pixel (they were drop 1's).
_DROP1_GOLDEN = {
    # 2026-11-20 (the two-tone split): "Myth: Design and Manufacturing Are One" splits at its clause boundary ("MYTH:"
    # white, the rest gold) and "Why diversification matters" is one block, its last line gold — both re-pinned
    "text": "0331593e6a1e20ef902aa0b0946a2ecfb27c4a5d875422589003868313e0441f",
    "disclaimer": "01cf3612383604c30e6b510fb0d1c3a12c67046bc422ba0aebe8b7ffca00a904",
    "brand": "ec891b4ddcd3f8cd93b31bd1799821b5b0ab7cd000fb87b0e8b4228b814d7755",
    "image": "05a9cb5e9746564681bd45687ba93fff8796c5d742a9a9ab3a044e52ecd93bbb",
}


@pytest.mark.skipif(PIL.__version__ != "12.0.0", reason="the golden bytes were measured on Pillow 12.0.0")
def test_a_lesson_card_and_picture_are_pinned():
    """The lesson's text card, end card, brand card and post picture, byte for byte — the picture as a news-v23
    read-back draws it: the SHORT lesson footer (cards.LESSON_FOOTER) and the look's kicker and brand word (it was
    pinned with the old long footer, which the server now refuses — a golden of a picture that can never register)."""
    import hashlib

    d1 = ("Educational, impersonal information — not investment advice. Investing involves risk. "
          "Written with AI assistance. Caydex · Sep 29, 2026")
    got = {
        "text": cards.render_card(cards.CardSpec("text", title="Myth: Design and Manufacturing Are One",
                                                 body="Traditional automakers built millions of cars for a century."),
                                  font_path=FONT, logo_path=LOGO, layout_engine="basic"),
        "disclaimer": cards.render_card(cards.CardSpec("disclaimer", body=d1), font_path=FONT, logo_path=LOGO,
                                        layout_engine="basic"),
        "brand": cards.render_card(cards.CardSpec("brand"), font_path=FONT, logo_path=LOGO, layout_engine="basic"),
        "image": cards.render_post_image(
            {"image_post": {"title": "Why diversification matters",
                            "paragraphs": ["Spreading money across many companies lowers the damage any one can do.",
                                           "It does not remove risk; it changes its shape."]},
             "image_footer": cards.LESSON_FOOTER, "look": cards.LOOK},
            font_path=FONT, layout_engine="basic").data,
    }
    assert {k: hashlib.sha256(v).hexdigest() for k, v in got.items()} == _DROP1_GOLDEN


# ── Batch 2 (2026-10-19, "first seconds"): the hook card, the data card, the App Store end card ─────────
# Owner decision 7 amended rules §5: a lesson video may OPEN on a hook card. The server freezes the video
# features per run (`output.video_features`, sent in the read-back) and ALLOWS a feature card's strings only
# while it is frozen (run_service._check_onscreen_text). The worker draws a feature card only when the
# read-back carries it, checks it with the render's own font and engine (glyphs, fit, legibility, the
# 64-string cap) and otherwise keeps today's card — never a skipped day. The end card draws the read-back's
# `end_card_cta`; a read-back that names none (an older web) keeps the legacy CTA.

import importlib  # noqa: E402

from marketing import news_layouts as nl  # noqa: E402


def _rs():
    """The server's run_service — imported where a test reads its allow-list (a lazy import: a module in
    another track's hands mid-edit fails only these tests, never the whole file's collection)."""
    return importlib.import_module("app.services.marketing.run_service")


_TPL_FOOTER = post_copy.image_footer(date(2026, 11, 16), "template", source=post_copy.FOOTER_SOURCE_FORM4,   # news-v23
                                     when="filed Nov 16, 2026")
_TPL_IMAGE_SPEC = {"layout": "spotlight", "version": 1, "kicker": "FILED LAST WEEK · FORM 4", "footer": _TPL_FOOTER,
                   "header": {"logo": "GME", "name": "GameStop", "chip": "GME"}, "figure": "$74.4M",
                   "headline": "GameStop's CEO disclosed buying GameStop stock",
                   "lines": ["1,000,000 shares across 3 purchases", "Filed Nov 9 to Nov 13, 2026"]}
_TPL_ALT = {"title": "A purchase disclosed on SEC Form 4",
            "paragraphs": ["The largest was 74.4 million dollars.", "Source: SEC Form 4 filings."]}


def _hook_script(**over):
    """A lesson read-back from a Batch 2 web with `hook_card` (and the push-in) frozen."""
    s = _script(video_features=["hook_card", "push_in"], end_card_cta=cards.CTA)
    s.update(over)
    return s


#: The fixture's FACT card the post picture already shows ("The purchases": "1,000,000 shares across 3
#: purchases" is the picture's own line) — the slot the web names (template_onscreen.data_card_slot);
#: cards 2-3 are the hedges a template keeps on screen for their whole line.
_TPL_DATA_SLOT = 1


def _data_script(**over):
    """A template read-back from a Batch 2 web with `data_card` (and the push-in) frozen, the slot the data
    card replaces (`data_card_slot`), its post image (`image_spec` + `image_footer`) and its frozen formats,
    as script_service.worker_script sends them."""
    s = _template_script(image_spec=json.loads(json.dumps(_TPL_IMAGE_SPEC)), image_footer=_TPL_FOOTER,
                         image_post=dict(_TPL_ALT), post_formats={"tiktok": "video", "bluesky": "image"},
                         video_features=["data_card", "push_in"], end_card_cta=cards.CTA,
                         data_card_slot=_TPL_DATA_SLOT)
    s.update(over)
    return s


#: [opening, text × 4, disclaimer] with the data card at the slot (specs[slot + 1]).
_DATA_KINDS = ["opening", "text", "data", "text", "text", "disclaimer"]
_TEXT_KINDS = ["opening", "text", "text", "text", "text", "disclaimer"]


def _cards(script, **kw):
    return cards.cards_for_script(script, font_path=FONT, layout_engine="basic", **kw)


def _server_allowed(script) -> set:
    """run_service._check_onscreen_text's allow-list for this run, from the server's OWN readers (the
    read-back carries the accepted output's keys): brand text, the cards, the disclaimer, a template's
    opening card — and a frozen feature's strings ONLY while it is frozen (`frozen_video_features`,
    `hook_card_text`, `data_card_strings`)."""
    rs = _rs()
    template = script.get("authorship") == "template"
    allowed = set(sch.VIDEO_BRAND_TEXT) | {c[k] for c in script["cards"] for k in ("title", "body") if c.get(k)}
    allowed.add(script["disclaimer_card"])
    features = rs.frozen_video_features(script, template=template)
    # news-v23: the brand row's kicker pill and the short footer line (a lesson's constants; a template's opening
    # kicker — among its opening strings — and its own image_footer while it is today's short footer)
    if not template:
        allowed |= set(sch.LESSON_BRAND_TEXT) | {post_copy.IMAGE_FOOTER_HEAD}
    elif script.get("image_footer") and rs.picture_footer_problem(script, template=True) is None:
        allowed.add(script["image_footer"])
    if template:
        allowed |= set(tos.opening_strings(script["opening_card"], script["logos"]))
        if sch.VIDEO_FEATURE_DATA_CARD in features:
            allowed |= set(rs.data_card_strings(script) or ())
    elif sch.VIDEO_FEATURE_HOOK_CARD in features and rs.hook_card_text(script):
        allowed.add(rs.hook_card_text(script))
    return allowed


def _declared(specs) -> List[str]:
    out: List[str] = []
    for spec in specs:
        for t in cards.onscreen_strings(spec):
            if t not in out:
                out.append(t)
    return out


# the read-back's two fields: the frozen features and the end card's CTA

@pytest.mark.parametrize("authorship, raw, expected", [
    (None, None, ()), (None, [], ()), ("template", [], ()),
    (None, ["push_in", "hook_card"], ("hook_card", "push_in")), (None, ["push_in"], ("push_in",)),
    (None, ["hook_card"], ("hook_card",)), ("ai", ["hook_card"], ("hook_card",)),
    ("template", ["push_in", "data_card"], ("data_card", "push_in")), ("template", ["data_card"], ("data_card",)),
    # never half-trusted: anything that does not read back is NONE of it (the server's own reading)
    (None, "hook_card", ()), (None, {"hook_card": True}, ()), (None, ["hook_card", "hook_card"], ()),
    (None, ["hook_card", 1], ()), (None, ["zoom"], ()), (None, ["hook_card", "zoom"], ()),
    (None, ["data_card"], ()), (None, ["hook_card", "data_card"], ()),           # a template feature on a lesson
    ("template", ["hook_card"], ()), ("template", ["hook_card", "push_in"], ()),  # a lesson feature on a template
    (None, [None], ()), (None, ["Hook_Card"], ()),
])
def test_video_features_read_back_whole_or_not_at_all_like_the_server(authorship, raw, expected, caplog):
    caplog.set_level(logging.ERROR, logger="marketing.cards")
    script = _script(authorship=authorship) if raw is None else _script(authorship=authorship, video_features=raw)
    if raw is None:
        script.pop("video_features", None)
    assert cards.video_features(script) == expected
    template = authorship == "template"
    # the server's reading of the same value (what it froze, what it allows) is the worker's
    assert _rs().frozen_video_features(script, template=template) == expected
    bad = raw not in (None, []) and expected == ()
    worker = [r for r in caplog.records if r.name == "marketing.cards" and "video_features" in r.getMessage()]
    assert bool(worker) == bad and all(r.levelno == logging.ERROR for r in worker), (raw, caplog.records)


def test_video_features_refuses_only_what_is_not_a_script():
    with pytest.raises(ValueError):
        cards.video_features(["not", "a", "dict"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="authorship"):
        cards.video_features(_script(authorship="robot", video_features=["push_in"]))


@pytest.mark.parametrize("over, expected, logged", [
    ({}, cards.LEGACY_CTA, False),                              # an older web: no key at all
    ({"end_card_cta": None}, cards.LEGACY_CTA, False),
    ({"end_card_cta": cards.LEGACY_CTA}, cards.LEGACY_CTA, False),   # the web says prelaunch
    ({"end_card_cta": cards.CTA}, cards.CTA, False),            # the web says the app is on the store
    ({"end_card_cta": "Caydex"}, cards.LEGACY_CTA, True),       # brand text, but not a CTA
    ({"end_card_cta": "Download Caydex now"}, cards.LEGACY_CTA, True),
    ({"end_card_cta": cards.CTA + "."}, cards.LEGACY_CTA, True),
    ({"end_card_cta": ""}, cards.LEGACY_CTA, True),
    ({"end_card_cta": 5}, cards.LEGACY_CTA, True),
    # the new CTA needs the web's word for it: a feature list alone (no end_card_cta) is not that
    ({"video_features": ["hook_card"]}, cards.LEGACY_CTA, False),
])
def test_the_end_card_draws_the_read_backs_cta_or_the_legacy_one(over, expected, logged, caplog):
    caplog.set_level(logging.ERROR, logger="marketing.cards")
    for script in (_script(**over), _template_script(**over)):
        assert cards.end_card_cta(script) == expected
        specs = cards.cards_for_script(script, font_path=FONT, layout_engine="basic")
        assert specs[-1].kind == "disclaimer" and specs[-1].end_cta == expected
        pill = _OPENING["kicker"] if cards.is_template(script) else cards.LESSON_KICKER      # news-v23
        assert cards.onscreen_strings(specs[-1]) == [script["disclaimer_card"], expected, pill, cards.BRAND_ROW_WORD]
        assert expected in sch.VIDEO_BRAND_TEXT                       # both webs allow it
    assert any(r.name == "marketing.cards" and r.levelno == logging.ERROR and "end_card_cta" in r.getMessage()
               for r in caplog.records) == logged


def test_every_cta_the_web_can_name_is_one_the_worker_draws():
    ss = importlib.import_module("app.services.marketing.script_service")
    named = {ss.end_card_cta({"store_state": state}) for state in ("live", "preorder", "prelaunch", None, "bogus")}
    named.add(ss.end_card_cta(None))
    assert named == set(cards.END_CARD_CTAS)


def test_the_app_store_cta_is_one_line_at_every_step_where_the_legacy_size_would_wrap():
    """STORE_CTA_STYLE keeps "Search Caydex on the App Store" on ONE line in the end card's column at every
    ramp step (CTA_STYLE's 60 px would leave "Store" alone on a second line); the legacy CTA keeps its
    drop-1 style and bytes (`test_a_lesson_renders_byte_identical_to_drop_1`)."""
    column = cards.SAFE_X[1] - cards.SAFE_X[0]
    for engine in ["basic"] + (["raqm"] if cards.raqm_available() else []):
        assert cards._font(FONT, cards.CTA_STYLE.start, engine).getlength(cards.CTA) > column    # anti-vacuity
        for step in range(cards.RAMP_STEPS + 1):
            font = cards._font(FONT, cards.STORE_CTA_STYLE.size(step), engine)
            assert font.getlength(cards.CTA) <= column, (engine, step)
    layout = cards.layout_card(cards.CardSpec("disclaimer", body=DISCLAIMER, cta=cards.CTA), font_path=FONT,
                               layout_engine="basic")
    assert layout.field_lines("cta") == [cards.CTA] and layout.step == 0
    assert all(ln.size == cards.STORE_CTA_STYLE.start and ln.fill == look.GOLD
               for ln in layout.lines if ln.field == "cta")
    legacy = cards.layout_card(cards.CardSpec("disclaimer", body=DISCLAIMER), font_path=FONT, layout_engine="basic")
    assert legacy.field_lines("cta") == [cards.LEGACY_CTA]
    assert {ln.size for ln in legacy.lines if ln.field == "cta"} == {cards.CTA_STYLE.start}


# the hook card (a lesson with `hook_card` frozen)

def test_a_lesson_with_hook_card_frozen_opens_on_the_hook_verbatim():
    script = _hook_script()
    specs = _cards(script)
    assert [s.kind for s in specs] == ["hook", "text", "text", "text", "disclaimer"]
    assert specs[0] == cards.CardSpec("hook", title=script["hook"], pill=cards.LESSON_KICKER,
                                      footer=cards.LESSON_FOOTER) and specs[0].title is script["hook"]
    assert specs[1:] == _cards(_script(end_card_cta=cards.CTA))           # the rest is today's video
    assert cards.opens_on_hook_card(specs) and not cards.opens_on_hook_card(_cards(_script()))
    assert cards.opens_on_hook_card(_cards(_template_script())) and not cards.opens_on_hook_card([])
    assert cards.onscreen_strings(specs[0]) == [script["hook"], cards.LESSON_KICKER, cards.LESSON_FOOTER,
                                                cards.BRAND_ROW_WORD]
    # a lesson ignores any logos it is given, with or without the feature
    assert _cards(script, logos=cards.logo_table(_template_script())) == specs
    # what the video declares is what the server allows for THIS run — the hook only because it is frozen
    drawn = _declared(specs)
    allowed = _server_allowed(script)
    assert set(drawn) <= allowed and script["hook"] in drawn and cards.CTA in drawn
    assert script["hook"] not in _server_allowed(_hook_script(video_features=["push_in"]))
    assert script["hook"] not in _server_allowed(_hook_script(video_features=[]))


def test_the_hook_card_is_the_hook_big_and_centred(monkeypatch):
    """news-v23: the hook card is the hook ALONE, big, two-tone (white, then gold), upper-cased, centred in the
    content zone under the brand row — no card, no panel, no rule."""
    monkeypatch.setattr(cards, "_warned", set())
    spec = cards.CardSpec("hook", title=HOOK, pill=cards.LESSON_KICKER, footer=cards.LESSON_FOOTER)
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    _assert_all_words_in_order(layout, spec)
    _assert_ink_inside(layout)
    assert layout.step == 0 and layout.panel is None and layout.badge is None and layout.logo is None
    assert layout.rule is None
    assert {ln.field for ln in layout.lines} == {"title", "brand", "pill", "footer"}
    title = [ln for ln in layout.lines if ln.field == "title"]
    assert all(ln.size == cards.HOOK_STYLE.start and ln.text == ln.text.upper() for ln in title)
    assert title[0].fill == look.WHITE and title[-1].fill == look.GOLD
    im = _assert_nothing_outside_the_zone(cards.render_card(spec, font_path=FONT, layout_engine="basic"), "hook")
    assert look.GOLD in {c for _n, c in im.getcolors(1 << 20)}
    top, bottom = min(ln.ink[1] for ln in title), max(ln.ink[3] for ln in title)
    zone_mid = (CONTENT[1] + CONTENT[3]) / 2
    assert abs((top + bottom) / 2 - zone_mid) < cards.HOOK_STYLE.start


def test_every_real_hook_fits_its_card_at_the_start_size_and_the_writers_longest_fits_too():
    raw = json.loads((_DATA / "marketing_judge_packages_2026_09_26.json").read_text(encoding="utf-8"))
    hooks = {v for pkg in raw["packages"] for k, v in pkg["fields"] if k == "hook"}
    drafts = json.loads((_DATA / "marketing_real_drafts_2026_09_24.json").read_text(encoding="utf-8"))
    hooks |= {r[2] for r in drafts["honest"] if r[1] == "hook"}
    assert len(hooks) > 50, "the fixtures changed shape — this test would go vacuous"
    for hook in sorted(hooks):
        spec = cards.CardSpec("hook", title=hook)
        layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
        assert layout.step == 0, hook
        _assert_all_words_in_order(layout, spec)
    assert len(MAX_HOOK.split()) == 14                                       # writer_prompts.HOOK_MAX_WORDS
    spec = cards.CardSpec("hook", title=MAX_HOOK)
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    assert layout.step < cards.RAMP_STEPS
    assert min(ln.size for ln in layout.lines if ln.field == "title") >= cards.HOOK_STYLE.floor
    _assert_all_words_in_order(layout, spec)
    _assert_ink_inside(layout)


@pytest.mark.parametrize("hook, why", [
    ("W" * 40, "CardOverflow"),                      # one word wider than the column at the floor
    ("A " + "Pneumonoultramicroscopicsilicovolcanoconiosis" + " case", "CardOverflow"),
    ("Growth 🚀 ahead of the market", "MissingGlyphs"),
    ("  Padded hook", "drawable"), ("Two\nlines", "drawable"), ("", "drawable"), ("   ", "drawable"),
    (None, "drawable"), (7, "drawable"), ("x" * 601, "drawable"),
])
def test_a_hook_card_that_cannot_be_drawn_opens_on_the_first_card_never_skips_the_day(hook, why, caplog):
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    specs = _cards(_hook_script(hook=hook))
    assert specs == _cards(_script(hook=hook, end_card_cta=cards.CTA))     # today's video, nothing raised
    assert [s.kind for s in specs] == ["text", "text", "text", "disclaimer"]
    warned = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "hook card" in warned and (why in warned or why == "drawable"), warned


def test_a_lesson_without_text_cards_still_opens_on_its_hook_card():
    specs = _cards(_hook_script(cards=[]))
    assert [s.kind for s in specs] == ["hook", "brand", "disclaimer"]


def test_a_hook_card_that_would_take_the_video_past_the_string_cap_is_left_out(monkeypatch, caplog):
    """B2V-3: the hook card adds one declared string; a video it would take past ONSCREEN_TEXT_MAX (the
    server refuses more) opens on its first card instead — WARNING, never a 422 at registration."""
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    script = _hook_script()
    with_hook = _declared(_cards(script))
    today = _declared(_cards(_hook_script(video_features=["push_in"])))
    # the hook card adds the hook alone: its pill, footer and brand word are every lesson card's
    assert set(with_hook) == {HOOK, *today} and len(with_hook) == len(today) + 1
    monkeypatch.setattr(cards, "ONSCREEN_TEXT_MAX", len(today))          # one string short of the hook card
    specs = _cards(script)
    assert [s.kind for s in specs] == ["text", "text", "text", "disclaimer"] and _declared(specs) == today
    assert any("no hook card" in r.getMessage() and "on-screen strings" in r.getMessage() for r in caplog.records)
    monkeypatch.setattr(cards, "ONSCREEN_TEXT_MAX", len(with_hook))      # exactly at the cap: drawn
    assert [s.kind for s in _cards(script)] == ["hook", "text", "text", "text", "disclaimer"]


# the data card (a template with `data_card` frozen)

def test_the_fixture_post_image_is_one_the_server_accepts():
    assert tos.validate_image_spec(_TPL_IMAGE_SPEC, tos.logo_keys(_TPL_ENTRIES), footer=_TPL_FOOTER) is None
    assert _rs().data_card_strings(_data_script()) == tos.image_strings(_TPL_IMAGE_SPEC, _TPL_ENTRIES)


@pytest.mark.parametrize("resolved", [False, True], ids=["wordmark", "logo"])
def test_a_template_with_data_card_frozen_draws_its_post_image_in_place_of_the_card_the_web_names(resolved,
                                                                                                tmp_path):
    files = {"GME": tmp_path / "gme.png"} if resolved else {}
    if resolved:
        _red_logo(files["GME"])
    script = _data_script()
    table = cards.logo_table(script, files)
    specs = _cards(script, logos=table)
    at = _TPL_DATA_SLOT + 1                                                   # specs[0] is the opening card
    assert [s.kind for s in specs] == _DATA_KINDS and cards.applied_data_card_slot(specs) == _TPL_DATA_SLOT
    today = _cards(_data_script(video_features=["push_in"]), logos=table)
    assert [s.kind for s in today] == _TEXT_KINDS and cards.applied_data_card_slot(today) is None
    assert specs[:at] == today[:at] and specs[at + 1:] == today[at + 1:]      # only the NAMED card changed
    assert cards.opens_on_hook_card(specs) and specs[-1].end_cta == cards.CTA
    image = nl.template_image_for_script(script, table)
    assert specs[at] == cards.CardSpec("data", image=image)
    assert cards.onscreen_strings(specs[at]) == [*image.strings, cards.BRAND_ROW_WORD]
    texts = [v for kind, _path, v in nl.walk(script["image_spec"], table) if kind == "text"]
    if resolved:
        assert list(image.strings) == list(dict.fromkeys(texts))            # a verified logo adds no name
    # the replaced card is not drawn any more, the closing hedge still is; everything drawn is allowed
    drawn = _declared(specs)
    assert not {_TPL_CARDS[_TPL_DATA_SLOT]["title"], _TPL_CARDS[_TPL_DATA_SLOT]["body"]} & set(drawn)
    assert {_TPL_CARDS[-1]["title"], _TPL_CARDS[-1]["body"]} <= set(drawn)
    assert set(drawn) <= _server_allowed(script) and _TPL_FOOTER in drawn
    unfrozen = _server_allowed(_data_script(video_features=["push_in"]))
    assert not set(_TPL_IMAGE_SPEC["lines"]) & unfrozen                     # allowed only while frozen
    assert _TPL_FOOTER in unfrozen                                           # news-v23: every template card's footer
    cards.check_glyphs(specs, FONT)
    layout = cards.layout_card(specs[at], font_path=FONT, layout_engine="basic")
    assert layout.kind == "data:spotlight" and nl.illegible_lines(layout) == []
    _assert_ink_inside(layout)
    data = cards.render_card(specs[at], font_path=FONT, layout_engine="basic")
    im = _assert_nothing_outside_the_zone(data, "data card")
    assert not _pure_page(im, (cards.SAFE_X[0], cards.TEXT_ZONE_Y[0], cards.SAFE_X[1], cards.TEXT_ZONE_Y[1]))
    cards._font.cache_clear()
    assert cards.render_card(specs[at], font_path=FONT, layout_engine="basic") == data            # deterministic
    reds = [c for _n, c in im.getcolors(1 << 20) if _red(c)]
    assert bool(reds) == resolved                       # the logo, unaltered, on its disc — nothing else is red
    off = _off_look(im, exclude=[_brand_tile(), *(t[0] for t in layout.tiles)])
    assert off == [], off[:5]
    # a ceo_buys picture: its purchase figure GREEN (look.figure_tone)
    assert {ln.fill for ln in layout.lines if ln.field == "figure"} == {look.GREEN}


def _tall_headline_spec() -> Dict:
    """The fixture's picture with a 600-character headline: it does not fit the card's content zone at any step
    (news-v23: every text floor is 22 px, so the data card fails on fit, never on legibility) — no data card."""
    spec = json.loads(json.dumps(_TPL_IMAGE_SPEC))
    spec["headline"] = " ".join(["Disclosed"] * 60)
    assert len(spec["headline"]) <= cards.MAX_STRING_CHARS
    assert tos.validate_image_spec(spec, tos.logo_keys(_TPL_ENTRIES), footer=_TPL_FOOTER) is None
    return spec


@pytest.mark.parametrize("over, level, needle", [
    ({"image_spec": _tall_headline_spec()}, logging.WARNING, "does not fit"),
    ({"image_spec": {**_TPL_IMAGE_SPEC, "lines": ["Growth 🚀 ahead"]}}, logging.WARNING, "MissingGlyphs"),
    ({"image_spec": None}, logging.ERROR, "image_spec"),
    ({"image_footer": None}, logging.ERROR, "image_footer"),
    ({"image_spec": {**_TPL_IMAGE_SPEC, "footer": _TPL_FOOTER + " ·"}}, logging.ERROR, "footer"),
    ({"image_spec": {**_TPL_IMAGE_SPEC, "layout": "pie"}}, logging.ERROR, "layout"),
    ({"image_spec": {**_TPL_IMAGE_SPEC, "header": {"logo": "ZZZ", "name": "Z", "chip": "Z"}}}, logging.ERROR, "logo"),
])
def test_a_data_card_that_cannot_be_drawn_keeps_its_text_card_never_skips_the_day(over, level, needle, caplog):
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    specs = _cards(_data_script(**over))
    assert specs == _cards(_data_script(video_features=["push_in"], **over))   # today's video, nothing raised
    assert [s.kind for s in specs] == _TEXT_KINDS
    said = [r.getMessage() for r in caplog.records if r.levelno == level and "data card" in r.getMessage()]
    assert said and needle in said[0], [r.getMessage() for r in caplog.records]
    assert _TPL_CARDS[_TPL_DATA_SLOT]["title"] in said[0]                      # names the card it kept


def test_a_data_card_that_would_take_the_video_past_the_string_cap_is_left_out(monkeypatch, caplog):
    """The video declares every string it draws, at most ONSCREEN_TEXT_MAX (the server refuses more): the
    data card adds its picture's strings, so a video it would take past the cap keeps its text card."""
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    script = _data_script()
    with_data, today = _declared(_cards(script)), _declared(_cards(_data_script(video_features=["push_in"])))
    assert set(with_data) - set(today)                                     # the picture's own lines
    monkeypatch.setattr(cards, "ONSCREEN_TEXT_MAX", len(with_data) - 1)
    specs = _cards(script)
    assert [s.kind for s in specs] == _TEXT_KINDS and len(_declared(specs)) == len(today)
    assert any("on-screen strings" in r.getMessage() for r in caplog.records)
    monkeypatch.setattr(cards, "ONSCREEN_TEXT_MAX", len(with_data))                   # exactly at the cap: drawn
    assert [s.kind for s in _cards(script)] == _DATA_KINDS


def test_a_feature_card_needs_the_renders_font_but_the_push_in_alone_does_not():
    for script in (_hook_script(), _data_script()):
        with pytest.raises(ValueError, match="font_path"):
            cards.cards_for_script(script)
    for script, base in ((_hook_script(video_features=["push_in"]), _script(end_card_cta=cards.CTA)),
                         (_data_script(video_features=["push_in"]), _data_script(video_features=[]))):
        assert cards.cards_for_script(script) == cards.cards_for_script(base)        # no card, no font needed
    # a feature list that does not read back is none of it: no font needed, today's cards
    assert cards.cards_for_script(_script(video_features=["data_card"])) == cards.cards_for_script(_script())
    assert cards.cards_for_script(_template_script(video_features=["hook_card"])) == cards.cards_for_script(
        _template_script())


def test_a_template_never_draws_a_hook_card_and_a_lesson_never_a_data_card(caplog):
    caplog.set_level(logging.ERROR, logger="marketing.cards")
    lesson = _cards(_script(video_features=["data_card", "push_in"], image_spec=dict(_TPL_IMAGE_SPEC),
                            image_footer=_TPL_FOOTER, logos=[dict(e) for e in _TPL_ENTRIES], data_card_slot=0))
    assert [s.kind for s in lesson] == ["text", "text", "text", "disclaimer"]
    template = _cards(_data_script(video_features=["hook_card", "push_in"]))
    assert [s.kind for s in template] == _TEXT_KINDS
    assert len([r for r in caplog.records if r.name == "marketing.cards" and "video_features" in r.getMessage()]) == 2


# CARDS-B2-1 (2026-10-20): the data card replaces the ONE text card the web names (`data_card_slot`, an
# index into the read-back's `cards`) — a fact card the picture already shows, never the hedge a template
# keeps on screen for its whole line. No usable slot, no data card: the worker never guesses at a card.


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
def test_the_data_card_replaces_exactly_the_card_the_slot_names(slot):
    specs = _cards(_data_script(data_card_slot=slot))
    today = _cards(_data_script(video_features=["push_in"]))
    kinds = ["opening", "text", "text", "text", "text", "disclaimer"]
    kinds[slot + 1] = "data"
    assert [s.kind for s in specs] == kinds and cards.applied_data_card_slot(specs) == slot
    assert [s for i, s in enumerate(specs) if i != slot + 1] == [s for i, s in enumerate(today) if i != slot + 1]
    assert cards.data_card_slot(_data_script(data_card_slot=slot)) == slot
    drawn = set(_declared(specs))
    for i, card in enumerate(_TPL_CARDS):                    # only the named card leaves the screen
        assert ({card["title"], card["body"]} <= drawn) == (i != slot), (i, slot)


def test_the_closing_hedge_card_stays_on_screen_when_the_web_names_the_fact_card():
    """The point of the slot: the fixture's last cards are its hedges ("What a Form 4 is", "How the amount is
    counted"); the picture replaces the fact card it repeats, and each hedge keeps its whole line."""
    specs = _cards(_data_script())
    hedge = _TPL_CARDS[-1]
    assert specs[-2] == cards.CardSpec("text", title=hedge["title"], body=hedge["body"], pill=_OPENING["kicker"],
                                       footer=_TPL_FOOTER)
    assert specs[_TPL_DATA_SLOT + 1].kind == "data"


@pytest.mark.parametrize("raw, level, needle", [
    (None, logging.WARNING, "names no data_card_slot"),          # a web that predates the slot
    (True, logging.ERROR, "not an index"), (False, logging.ERROR, "not an index"),
    ("2", logging.ERROR, "not an index"), (2.0, logging.ERROR, "not an index"),
    (-1, logging.ERROR, "not an index"),                          # never the opening card
    (4, logging.ERROR, "not an index"),                           # never the disclaimer card
    (99, logging.ERROR, "not an index"), ([2], logging.ERROR, "not an index"),
])
def test_without_a_usable_slot_no_data_card_is_drawn_and_it_is_said(raw, level, needle, caplog):
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    script = _data_script(data_card_slot=raw)
    if raw is None:
        script.pop("data_card_slot")
    assert cards.data_card_slot(script) is None
    caplog.clear()
    specs = _cards(script)
    assert specs == _cards(_data_script(video_features=["push_in"]))         # today's text cards, nothing raised
    assert [s.kind for s in specs] == _TEXT_KINDS and cards.applied_data_card_slot(specs) is None
    said = [r for r in caplog.records if r.name == "marketing.cards" and "data_card_slot" in r.getMessage()]
    assert said and all(r.levelno == level for r in said) and needle in said[0].getMessage(), caplog.records
    # nothing of the picture drawn (its footer is every template card's own short footer line: news-v23)
    assert not set(_TPL_IMAGE_SPEC["lines"]) & set(_declared(specs)) and _TPL_FOOTER in _declared(specs)


def test_a_slot_without_a_frozen_data_card_is_ignored(caplog):
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    for features in (["push_in"], []):
        script = _data_script(video_features=features, data_card_slot=_TPL_DATA_SLOT)
        assert cards.data_card_slot(script) is None
        assert [s.kind for s in _cards(script)] == _TEXT_KINDS
    assert any("without a frozen data_card" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)
    lesson = _script(data_card_slot=0)                               # a lesson never has a data card
    assert cards.data_card_slot(lesson) is None and [s.kind for s in _cards(lesson)] == [
        "text", "text", "text", "disclaimer"]


def test_a_slot_that_lands_on_a_card_that_is_not_text_replaces_nothing(caplog):
    """Defence in depth: the read-back's index is checked against the specs it lands on — never the opening
    card, never the disclaimer card, whatever list `_with_data_card` is handed."""
    caplog.set_level(logging.ERROR, logger="marketing.cards")
    script = _data_script()
    table = cards.logo_table(script)
    opening = cards.opening_spec(script["opening_card"], table)
    end = cards.CardSpec("disclaimer", body=TEMPLATE_DISCLAIMER, cta=cards.CTA)
    text = cards.CardSpec("text", title="When", body="Filed Nov 9 to Nov 13, 2026.")
    for specs in ([opening, text, end], [opening, end, end, end, end], [opening, text, opening, text, text, end]):
        out = cards._with_data_card(script, table, list(specs), FONT, "basic")
        assert out == specs
    assert len([r for r in caplog.records if "does not name a text card" in r.getMessage()]) == 3


def test_the_feature_cards_declare_what_the_server_allows_and_render_inside_the_zone():
    """The whole Batch 2 video of each kind, card by card: every card renders inside the text zone, every
    glyph is the face's, and the declared strings are the server's allow-list for the run — at most 64."""
    for script in (_hook_script(), _data_script()):
        specs = _cards(script)
        cards.check_glyphs(specs, FONT)
        drawn = _declared(specs)
        assert set(drawn) <= _server_allowed(script) and len(drawn) <= sch.ONSCREEN_TEXT_MAX
        sch.validate_onscreen_text(drawn)
        for spec in specs:
            _assert_nothing_outside_the_zone(cards.render_card(spec, font_path=FONT, logo_path=LOGO,
                                                               layout_engine="basic"), spec.kind)


_RAQM_FEATURE_CHECK = r"""
import io, json, sys
from PIL import Image, ImageChops
from marketing import cards, look
assert cards.raqm_available(), "no raqm"
F = sys.argv[1]
base = look.canvas(look.VIDEO_FRAME)
for name in ("lesson", "template"):
    script = json.loads(sys.argv[2 if name == "lesson" else 3])
    specs = cards.cards_for_script(script, font_path=F, layout_engine="raqm")
    kinds = [s.kind for s in specs]
    assert (kinds[0] == "hook") if name == "lesson" else (kinds[script["data_card_slot"] + 1] == "data"), kinds
    for s in specs:
        a = cards.render_card(s, font_path=F, layout_engine="raqm")
        assert a == cards.render_card(s, font_path=F, layout_engine="raqm")
        im = Image.open(io.BytesIO(a)); im.load()
        for box in [(0, 0, 1080, 140), (0, cards.TEXT_ZONE_Y[1], 1080, cards.CAPTION_BAND_Y[1]),
                    (0, 1500, 1080, 1920), (0, 140, cards.SAFE_X[0], 1500), (cards.SAFE_X[1], 140, 1080, 1500)]:
            assert ImageChops.difference(im.crop(box), base.crop(box)).getbbox() is None, (s.kind, box)
        assert cards.layout_card(s, font_path=F, layout_engine="raqm").engine == "raqm"
print("RAQM-FEATURES-OK")
"""


def test_raqm_draws_the_feature_cards_too():
    env = _raqm_env()
    if env is None:
        pytest.skip("libraqm not available to Pillow")
    env.pop("PYTHONPATH", None)
    proc = subprocess.run([sys.executable, "-c", _RAQM_FEATURE_CHECK, FONT, json.dumps(_hook_script()),
                           json.dumps(_data_script())], cwd=_BACKEND, env=env, capture_output=True, text=True,
                          timeout=180)
    if proc.returncode != 0 and "no raqm" in proc.stderr:
        pytest.skip("libraqm not loadable in a subprocess either")
    assert proc.returncode == 0 and "RAQM-FEATURES-OK" in proc.stdout, proc.stderr[-2000:]


def test_a_one_line_template_with_data_card_frozen_draws_the_picture_as_its_only_text_card():
    script = _data_script(video_script=_TPL_LINES[:1], cards=[dict(_TPL_CARDS[0])], data_card_slot=0)
    specs = _cards(script)
    assert [s.kind for s in specs] == ["opening", "data", "disclaimer"]
    assert [s.kind for s in _cards(dict(script, data_card_slot=1))] == ["opening", "text", "disclaimer"]
    assert set(_declared(specs)) <= _server_allowed(script) and _TPL_CARDS[0]["title"] not in _declared(specs)


@pytest.mark.parametrize("state, cta", [("live", cards.CTA), ("preorder", cards.CTA), ("prelaunch", cards.LEGACY_CTA)])
def test_the_webs_own_read_back_draws_the_frozen_feature_cards(state, cta):
    """Contract: an accepted output with features frozen, sent through the web's own read-back
    (script_service.worker_script → the WorkerScript response model → JSON), is drawn by the worker with
    its feature card and the end card the web named for the output's store state."""
    ss = importlib.import_module("app.services.marketing.script_service")

    def sent(output):
        return json.loads(sch.WorkerScript(**ss.worker_script(output, run_id="run-b2")).model_dump_json())

    lesson = {"hook": HOOK, "video_script": ["Line one.", "Line two."], "cards": _script()["cards"],
              "carousel_slides": [], "disclaimer_card": DISCLAIMER, "posts": {"tiktok": {}, "x": {}},
              "video_features": ["hook_card", "push_in"], "store_state": state}
    specs = _cards(sent(lesson))
    assert [s.kind for s in specs] == ["hook", "text", "text", "text", "disclaimer"] and specs[0].title == HOOK
    assert specs[-1].end_cta == cta
    template = {k: v for k, v in _data_script().items()
                if k not in ("video_features", "end_card_cta", "data_card_slot")}
    template.update(posts={"tiktok": {}, "bluesky": {}}, video_features=["data_card", "push_in"], store_state=state)
    back = sent(template)
    # CARDS-B2-1: the web names the slot from the cards' own lexicon titles — the fact card, never a hedge
    assert back["data_card_slot"] == _TPL_DATA_SLOT and back["cards"][_TPL_DATA_SLOT]["title"] == "The purchases"
    tspecs = _cards(back)
    assert [s.kind for s in tspecs] == _DATA_KINDS and cards.applied_data_card_slot(tspecs) == _TPL_DATA_SLOT
    assert tspecs[-1].end_cta == cta
    # a day whose cards hold no card the slot table names reads back WITHOUT the data card (the web drops it)
    hedges_only = dict(template, cards=[dict(c) for c in _TPL_CARDS[:1] + _TPL_CARDS[2:]] + [dict(_TPL_CARDS[0])])
    back = sent(hedges_only)
    assert back["data_card_slot"] is None and "data_card" not in back["video_features"]
    assert "data" not in [s.kind for s in _cards(back)]
    # …and a run frozen WITHOUT features reads back as today's video, the end card still the web's
    lesson.pop("video_features")
    assert [s.kind for s in cards.cards_for_script(sent(lesson))] == ["text", "text", "text", "disclaimer"]


# ── the local preview draws what the rendered stage draws (Batch 2 integration) ──────────────────────
# marketing/preview.py called cards_for_script without the render's font and layout engine — a read-back
# with a frozen hook or data card raised ValueError there — and never passed the push-in. It now makes the
# rendered stage's own calls: features read through cards.video_features, the feature cards checked with
# its font and engine, the opening derived by produce_video from the cards, push_in only when frozen.


def _run_preview(monkeypatch, tmp_path, script, *extra: str):
    """`marketing.preview.main` on `script` (plus `extra` arguments), with the narration (Kokoro, the AAC encode),
    the render and the ffprobe faked: (the produce_video calls' keywords, report.json)."""
    from marketing import preview, render, timings, voice

    calls: List[Dict] = []
    monkeypatch.setenv("MARKETING_PREVIEW_REEXEC", "1")              # never re-exec itself under pytest
    monkeypatch.setattr(preview.shutil, "which", lambda name: f"/usr/bin/{name}")
    seconds = 2.0 * len(timings.narrated_lines(script))

    def synthesize(lines, **_k):
        per_line = [(timings.proportional(line.split(), 0.0, 1.0), 1.0) for line in lines]
        words = timings.as_table(timings.assemble(lines, per_line))
        return voice.Narration(tmp_path / "narration.wav", words, seconds, 1.0, len(lines))

    def produce(**kw):
        calls.append(kw)
        return b"mp4", kw["narration_seconds"] + voice.DISCLAIMER_CARD_SECONDS, list(range(len(kw["specs"])))

    monkeypatch.setattr(voice, "synthesize_fitting", synthesize)
    monkeypatch.setattr(voice, "encode_m4a", lambda wav, out: (b"m4a", seconds))
    monkeypatch.setattr(render, "produce_video", produce)
    monkeypatch.setattr(preview, "_ffprobe", lambda path: {})
    path = tmp_path / "script.json"
    path.write_text(json.dumps(script), encoding="utf-8")
    assert preview.main([str(path), "--out", str(tmp_path / "out"), *extra]) == 0
    return calls, json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))


def test_the_preview_draws_a_frozen_hook_card_and_its_push_in_like_the_rendered_stage(monkeypatch, tmp_path):
    calls, report = _run_preview(monkeypatch, tmp_path, _hook_script())
    (kw,) = calls
    assert [s.kind for s in kw["specs"]] == ["hook", "text", "text", "text", "disclaimer"]
    assert kw["specs"][0].title == HOOK and kw["push_in"] is True
    assert "hook_card" not in kw                     # produce_video derives the opening from the cards
    assert kw["layout_engine"] == cards.resolve_layout_engine(None)
    assert report["video_features"] == ["hook_card", "push_in"] and report["push_in"] is True
    assert report["card_kinds"][0] == "hook" and report["end_card_cta"] == cards.CTA
    assert HOOK in report["onscreen_text"] and cards.CTA in report["onscreen_text"]


def test_the_preview_draws_a_frozen_data_card_like_the_rendered_stage(monkeypatch, tmp_path):
    calls, report = _run_preview(monkeypatch, tmp_path, _data_script())
    (kw,) = calls
    assert [s.kind for s in kw["specs"]] == _DATA_KINDS
    assert kw["push_in"] is True and "hook_card" not in kw
    assert report["template"] is True and report["card_kinds"] == _DATA_KINDS
    assert report["data_card_slot"] == _TPL_DATA_SLOT
    assert _TPL_FOOTER in report["onscreen_text"] and report["post_image"]["layout"] == "spotlight"


def test_the_preview_of_a_script_without_features_makes_todays_call(monkeypatch, tmp_path):
    calls, report = _run_preview(monkeypatch, tmp_path, _script())
    (kw,) = calls
    assert [s.kind for s in kw["specs"]] == ["text", "text", "text", "disclaimer"]
    assert "push_in" not in kw and "hook_card" not in kw
    assert report["video_features"] == [] and report["push_in"] is False
    assert report["end_card_cta"] == cards.LEGACY_CTA and HOOK not in report["onscreen_text"]


@pytest.mark.parametrize("features", [["hook_card"], ["data_card"]])
def test_the_preview_never_pushes_in_unless_push_in_itself_is_frozen(monkeypatch, tmp_path, features):
    """B2V-2: the preview's push-in follows push_in ITSELF, like the rendered stage — a day frozen with the
    cards alone (MARKETING_VIDEO_FEATURES="hook_card,data_card") previews with a still card 0."""
    script = _hook_script(video_features=features) if features == ["hook_card"] else _data_script(
        video_features=features)
    calls, report = _run_preview(monkeypatch, tmp_path, script)
    (kw,) = calls
    assert "push_in" not in kw and report["push_in"] is False and report["video_features"] == features
    kinds = [s.kind for s in kw["specs"]]
    assert kinds == (["hook", "text", "text", "text", "disclaimer"] if features == ["hook_card"] else _DATA_KINDS)


def test_the_preview_never_falls_back_from_a_failed_push_in(monkeypatch, tmp_path):
    """The rendered stage renders a failed push-in once more without it (render.py); the preview does NOT —
    it is how a host's ffmpeg is checked before the switch is set, so the failure must show."""
    from marketing import render, video

    _run_preview(monkeypatch, tmp_path, _script())                       # wires the fakes
    calls: List[Dict] = []

    def produce(**kw):
        calls.append(kw)
        raise video.RenderFailed("run preview: No such filter: 'zoompan'")

    monkeypatch.setattr(render, "produce_video", produce)
    path = tmp_path / "push.json"
    path.write_text(json.dumps(_hook_script()), encoding="utf-8")
    from marketing import preview

    with pytest.raises(video.RenderFailed, match="zoompan"):
        preview.main([str(path), "--out", str(tmp_path / "out2")])
    assert len(calls) == 1 and calls[0]["push_in"] is True


# ── workstream C (2026-11-16): the per-post ART on the template OPENING card — the cover ──────────────────────────
#
# Owner decisions 2026-11-15 ("Picture and video cover"; main lane "Video cover now"): a single-company template video
# opens on a 920×518 band of the art at the top of the text zone, its ONE logo plate on the band's bottom edge, the
# kicker, chip, figure and headline under it, and the code-owned "AI illustration" burned on the band. Pinned here:
# everything stays in the zone (nothing in the caption band); the strings end with the label and the server allows
# them; the plain opening card did not move a pixel; the FIT RATE over every real composed opening card of the art
# series; and every way the art cannot be drawn keeps today's opening card with a WARNING — never CardOverflow.

import hashlib  # noqa: E402

np = pytest.importorskip("numpy")
from app.services.marketing import company_news_rules as _R  # noqa: E402
from app.services.marketing import news_templates as _T  # noqa: E402
from app.services.marketing import post_art  # noqa: E402


def _art_bytes(size=(1376, 774), shift: int = 0) -> bytes:
    """A deterministic synthetic art: steel-blue stripes and a RED block in the middle (the palette checks bite)."""
    from PIL import ImageDraw

    w, h = size
    im = Image.new("RGB", size, (40, 70, 110))
    d = ImageDraw.Draw(im)
    for x in range(shift % 40, w, 40):
        d.line([(x, 0), (x, h)], fill=(90, 130, 170), width=3)
    d.rectangle((w // 3, h // 4, 2 * w // 3, 3 * h // 4), fill=(220, 20, 20))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _art(tmp_path: Path, name: str = "art.jpg", **kw) -> "cards.ArtImage":
    data = _art_bytes(**kw)
    (tmp_path / name).write_bytes(data)
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
    return cards.ArtImage(sha256=hashlib.sha256(data).hexdigest(), path=str(tmp_path / name), width=w, height=h)


def _art_opening(art, card=None, files=None) -> "cards.CardSpec":
    plain = cards.opening_spec(card or _OPENING, cards.logo_table(_template_script(), files or {}))
    return cards.CardSpec("opening", badge=plain.badge, label=plain.label, figure=plain.figure, title=plain.title,
                          logos=plain.logos, art=art)


def test_the_opening_art_geometry_stays_in_the_zone_at_every_step():
    """news-v23 (the cover in the approved look, spot_insider.jpg's idea on 9:16): the band is the card's full width
    at its very top — the brand row reads on its navy scrim — native 16:9; the logo disc (64-100 px radius) in its gold
    ring on the band's bottom-left edge; the label a fixed pill at the band's bottom-right, clear of the disc; the
    band ends far above the caption band, the text has the content zone under it."""
    assert (cards.OPENING_ART_W, cards.OPENING_ART_H) == (cards.WIDTH, 608)
    assert abs(cards.OPENING_ART_W / cards.OPENING_ART_H - 16 / 9) < 0.002
    assert cards.OPENING_ART_H + cards.OPENING_ART_DISC.start + 300 < cards.TEXT_ZONE_Y[1] < cards.CAPTION_BAND_Y[0]
    lw, lh = cards.art_label_size(FONT, "basic")
    label_x0 = cards.WIDTH - 40 - lw
    for step in range(cards.RAMP_STEPS + 1):
        r = cards.OPENING_ART_DISC.size(step)
        assert 64 <= r <= 100
        assert CONTENT[0] + 12 + 2 * r + 12 < label_x0                  # the label never reaches the disc
    assert lh + 28 < cards.OPENING_ART_H - BRAND_TOP - look.BRAND_TILE_PX  # nor the brand row
    assert cards.ART_LABEL == tos.ART_LABEL and cards.ART_LABEL_PX >= 24
    assert cards.ART_LABEL_FILL == look.NAVY_CHIP and cards.ART_LABEL_INK == look.WHITE


@pytest.mark.parametrize("resolved", [False, True], ids=["wordmark", "logo"])
def test_an_opening_card_with_art_draws_the_band_the_plate_on_its_edge_and_the_text_under_it(resolved, tmp_path):
    files = {"GME": Path(_red_logo(tmp_path / "gme.png"))} if resolved else {}
    art = _art(tmp_path)
    spec = _art_opening(art, files=files)
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    band, label = layout.art_box, layout.label_box
    assert band == (0, 0, cards.WIDTH, cards.OPENING_ART_H)
    ((plate, logo),) = layout.tiles
    r = (plate[2] - plate[0]) // 2
    assert plate[3] - plate[1] == 2 * r == 2 * cards.OPENING_ART_DISC.size(layout.step) and logo.key == "GME"
    assert abs((plate[1] + plate[3]) / 2 - band[3]) <= 8                       # on the band's bottom edge
    assert plate[0] == CONTENT[0] + 12                                          # bottom-left
    assert (label[2], label[3]) == (band[2] - 40, band[3] - 28) and cards._within(label, band)
    assert not cards._overlap(label, plate)
    by_field: Dict[str, List[str]] = {}
    for ln in layout.lines:
        by_field.setdefault(ln.field, []).extend(ln.text.split())
    expected = {"badge": _OPENING["kicker"], "label": _OPENING["chip"], "figure": _OPENING["figure"],
                "title": _OPENING["headline"].upper(), "art_label": cards.ART_LABEL, "brand": cards.BRAND_ROW_WORD}
    if not resolved:
        expected["wordmark:GME"] = "GameStop"
    assert {f: " ".join(w) for f, w in by_field.items()} == expected
    for ln in layout.lines:
        if ln.field == "art_label":
            assert cards._within(ln.ink, label)
        elif ln.field.startswith("wordmark:"):
            assert cards._within(ln.ink, plate)
        elif ln.field in ("brand", "badge"):                                    # the brand row, on the band's scrim
            assert cards._within(ln.ink, (cards.SAFE_X[0], BRAND_TOP - 10, cards.SAFE_X[1], CONTENT[1]))
        else:                                                                    # under the band, above the captions
            assert ln.ink[1] >= band[3] and ln.ink[3] <= cards.TEXT_ZONE_Y[1], (ln.field, ln.ink)
            assert cards._within(ln.ink, (cards.SAFE_X[0], 0, cards.SAFE_X[1], cards.HEIGHT)), (ln.field, ln.ink)
    data = cards.render_card(spec, font_path=FONT, layout_engine="basic")
    im = _png(data)
    shadow = band[3] + 44                                                        # the band's soft shadow
    for name, box in {**{k: v for k, v in _OUTSIDE_ZONE.items() if k in (
            "between the content zone and the caption band", "caption band", "the safe bottom")},
            "left margin": (0, shadow, cards.SAFE_X[0], SAFE_BOTTOM),
            "right margin": (cards.SAFE_X[1], shadow, cards.WIDTH, SAFE_BOTTOM)}.items():
        assert _pure_page(im, box), f"opening with art: something was drawn in the {name} {box}"
    px = np.asarray(im)
    tile = np.asarray(cards.art_tile(art, band[2] - band[0], band[3] - band[1]))
    mask = np.zeros(tile.shape[:2], dtype=bool)
    mask[340:, :] = True                                                         # under the scrim
    for b in (label, (plate[0] - 14, plate[1] - 14, plate[2] + 14, plate[3] + 14)):
        mask[max(b[1], 0):min(b[3], band[3]), max(b[0], 0):b[2]] = False
    assert (px[band[1]:band[3], band[0]:band[2]][mask] == tile[mask]).all()            # the band IS the art
    assert (px[label[1] + 2, (label[0] + label[2]) // 2] == np.array(cards.ART_LABEL_FILL)).all()
    # the disc is drawn OVER the band's edge: its white on both sides of the edge
    assert tuple(px[band[3] - 4, plate[0] + 14]) == tuple(px[band[3] + 4, plate[0] + 14]) == (255, 255, 255)


def test_the_art_cover_declares_the_label_last_and_the_server_allows_it(tmp_path):
    art = _art(tmp_path)
    spec = _art_opening(art)
    plain = cards.opening_spec(_OPENING, cards.logo_table(_template_script()))
    strings = cards.onscreen_strings(spec)
    assert strings == [*cards.onscreen_strings(plain), cards.ART_LABEL]
    allowed = (set(tos.opening_strings(_OPENING, _TPL_ENTRIES)) | set(tos.art_allowed(art.sha256))
               | set(sch.VIDEO_BRAND_TEXT))
    assert set(strings) <= allowed and cards.ART_LABEL not in tos.opening_strings(_OPENING, _TPL_ENTRIES)
    assert tos.art_declaration_problem(art.sha256, art.sha256, strings) is None
    assert tos.art_declaration_problem(None, art.sha256, cards.onscreen_strings(plain)) is None
    # the glyph check covers the label: a face without one of its glyphs refuses the card
    cards.check_glyphs([spec], FONT)


#: The PLAIN opening card's bytes (Pillow 12.0.0, the basic engine) in the approved look (news-v23, cards/v6): a
#: wordmark, a verified red logo, two companies. A cover without art must not move a pixel.
#: Fidelity review 2026-11-20: re-pinned (the kit's chip box: size + 16 tall, its label 6 px under its top).
#: Content round 2026-11-20: re-pinned for the two-tone split — both sample headlines have no gold tail and no boundary
#: ("GameStop's CEO disclosed buying GameStop stock", "A filer reported two changes"), so each is one block with its
#: last line gold (the half-of-the-words split is gone); the figure card itself did not move.
#: Visual-fidelity round 2 (2026-11-20): re-pinned — the plate is the list covers' (r 86, a 24 px chip; it was r 110 /
#: 30 px) and the content starts at the top under the brand row (it was centred ~170 px under it).
_PLAIN_OPENING_GOLDEN = {
    "opening:wm": "e74b639ef2f215384a6b253cfd1956cf0678d71ca13f2e92baf1eb08f941b693",
    "opening:logo": "a1627a3b99dfe0c0b47a24db1f67f4a9c8273fa43c8b36e03af5e83a3040754a",
    "opening:two": "3fe3284db93d84683538de8e98f123e4fa24a0fddac769fe15eda1c4e8cb3b6f",
}


@pytest.mark.skipif(PIL.__version__ != "12.0.0", reason="the golden bytes were measured on Pillow 12.0.0")
def test_the_plain_opening_card_did_not_move_a_pixel(tmp_path):
    two = {"kicker": "13F SEASON", "logos": ["GME", "NVDA"], "headline": "A filer reported two changes"}
    logo = Path(_red_logo(tmp_path / "gme.png"))
    got = {}
    for name, card, files in (("opening:wm", _OPENING, {}), ("opening:logo", _OPENING, {"GME": logo}),
                              ("opening:two", two, {})):
        spec = cards.opening_spec(card, cards.logo_table(_template_script(), files))
        got[name] = hashlib.sha256(cards.render_card(spec, font_path=FONT, layout_engine="basic")).hexdigest()
    assert got == _PLAIN_OPENING_GOLDEN


def test_fidelity2_a_plain_cover_uses_the_list_covers_plate_and_starts_under_the_brand_row():
    """cover_13f_berkshire.jpg: the plate's disc r 86 in its gold ring, the ticker on a 24 px gold pill, the headline
    from the content's top (~60 px under the brand row). The plain cover drew r 110 / 30 px and centred its block
    (~170 px under the brand row). One plate on every cover: OPENING_DISC / OPENING_CHIP_STYLE are the list covers'
    news_layouts.PLATE_DISC / CHIP_PX (two companies: × 0.75 each, as before)."""
    from marketing import news_layouts as nl

    assert cards.OPENING_DISC == nl.PLATE_DISC and cards.OPENING_CHIP_STYLE == nl.CHIP_PX
    assert (cards.OPENING_DISC.size(0), cards.OPENING_CHIP_STYLE.size(0)) == (86, 24)
    for card, n in ((_OPENING, 1), ({"kicker": "13F SEASON", "logos": ["GME", "NVDA"],
                                    "headline": "A filer reported two changes"}, 2)):
        spec = cards.opening_spec(card, cards.logo_table(_template_script()))
        lay = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
        assert lay.step == 0
        title = [ln for ln in lay.lines if ln.field == "title"]
        assert title[0].y == look.VIDEO_FRAME.area[1]                             # never centred
        assert {d.box[2] - d.box[0] for d in lay.discs} == {2 * (86 if n == 1 else int(round(86 * 0.75)))}
        assert {ln.size for ln in lay.lines if ln.field == "label"} <= {24}


def test_an_art_cover_is_deterministic_and_the_art_changes_it(tmp_path):
    a, b = _art(tmp_path, "a.jpg"), _art(tmp_path, "b.jpg", shift=13)
    one = cards.render_card(_art_opening(a), font_path=FONT, layout_engine="basic")
    cards._font.cache_clear()
    cards._fit_wordmark.cache_clear()
    again = cards.render_card(_art_opening(a), font_path=FONT, layout_engine="basic")
    other = cards.render_card(_art_opening(b), font_path=FONT, layout_engine="basic")
    plain = cards.render_card(cards.opening_spec(_OPENING, cards.logo_table(_template_script())), font_path=FONT,
                              layout_engine="basic")
    assert one == again and len({one, other, plain}) == 3


def test_off_the_band_and_the_plate_the_art_cover_is_the_brand_palette(tmp_path):
    """Everything off the band (and its shadow), the disc with its ring and the label is the look's: the canvas,
    a look colour or a blend — the red art lives only in the band."""
    art = _art(tmp_path)
    spec = _art_opening(art)
    layout = cards.layout_card(spec, font_path=FONT, layout_engine="basic")
    im = _png(cards.render_card(spec, font_path=FONT, layout_engine="basic"))
    band = layout.art_box
    plate = layout.tiles[0][0]
    excluded = [(band[0], band[1], band[2], band[3] + 44), (plate[0] - 14, plate[1] - 14, plate[2] + 14, plate[3] + 14)]
    assert _off_look(im, exclude=excluded) == []
    px = np.asarray(im)
    red = (px[..., 0] > 150) & (px[..., 1] < 90) & (px[..., 2] < 90)
    assert red[:band[3]].any() and not red[band[3] + 44:].any()


# The FIT RATE: every opening card the art series really compose — the templates' samples whose record gets art
# (post_art.art_subject_for: the spotlights, money_map, congress_count, single-company ceo_buys / insider_buys /
# earnings) and the widest spotlight records — laid out with art by the worker's own code.


def _art_series_openings(monkeypatch) -> Dict[str, Dict]:
    """The real composed covers the kept art code would draw on. Owner decision 2026-11-20 ("use it nowhere for now"):
    no shipped series carries art, so the Money Map — the series the art cover was built for — is put back into the
    art set for the test (test_marketing_post_art.keep_art_on_money_map): the worker's art cover keeps its measured
    room for the day the owner turns it on."""
    import test_marketing_news_layouts as NL
    import test_marketing_news_templates as NT
    from test_marketing_post_art import keep_art_on_money_map

    keep_art_on_money_map(monkeypatch)

    out: Dict[str, Dict] = {}
    for name, factory, run_date in NT.SAMPLES:
        rec = factory()
        composed = _T.compose(rec, run_date=run_date, store_state="live", allow_x_url=False)
        if post_art.art_subject_for(rec.series, composed, rec) is not None:
            out[name] = composed
    # the widest one-issuer insider covers as news-v23 composed them (frozen: owner decisions 2026-11-20 compose no
    # insider art) — a stress test of the art cover's room the worker still draws for any art it is handed
    for name, composed in NL._SPOTLIGHT_OUTPUTS.items():
        if "worst" in name:
            out[name] = composed
    return out


def _cover_step(composed: Dict, art, engine: str):
    entries = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in composed["logo_refs"]]
    plain = cards.opening_spec(composed["opening_card"], cards.logo_table({"logos": entries}))
    spec = cards.CardSpec("opening", badge=plain.badge, label=plain.label, figure=plain.figure, body=plain.body,
                          title=plain.title, logos=plain.logos, art=art)
    try:
        return cards.layout_card(spec, font_path=FONT, layout_engine=engine).step
    except cards.CardOverflow:
        return None


#: The measured steps (basic engine; news-v23, the look's cover: the art across the top 608 rows, the text under it):
#: every real composed cover fits by step 9 — the Money Maps at 8-9 (2026-11-20: the picture's headline, the ticker chip
#: and the figure's label under it inside its card; Costco 9 → 8), the rest at 6-7 — and so do the widest Form 4
#: spotlights (12-15). The drop is the art leaving the cover only when it cannot fit at all (`_with_opening_art`:
#: CardOverflow at step RAMP_STEPS). news-v23 (2026-11-19): neither 13F series draws art — the 13F spotlight is the
#: ONE 13F list (`post_art.NEVER_ART_SERIES`), its cover the list itself — so its single-holding covers and their
#: synthetic 60-character "W" filers left this corpus; and (the restyle's earnings table and Congress list, the same
#: day) neither does the Congress Count nor earnings: each is ONE post of 2-4 companies whose cover is the list, so
#: congress_accenture (step 11) and earnings_loss / earnings_profit (9 / 8) left it too. Owner decisions 2026-11-20 ("Drop
#: it from insider posts"; every Form 4 post 3-5 companies): no insider post carries art — the one-company Form 4 covers
#: and the insider spotlight's left the corpus; the Money Map is the one art series (its covers unchanged), and the
#: frozen widest insider covers stay as the stress test.
_COVER_STEPS_BASIC = {
    "costco_money_map": 8, "rivian_money_map": 9, "comcast_money_map": 9, "coca_cola_money_map": 9,
    "mcdonalds_money_map": 9, "amazon_money_map": 9,
    "isp_worst_cluster": 13, "isp_worst_held": 12, "isp_worst_cost": 13, "isp_worst_words": 15,
}
#: Every real cover fits at this step or under (the ceiling the other layouts keep: never the floor sizes).
COVER_ART_CEILING = cards.RAMP_STEPS - 2


@pytest.mark.parametrize("engine", ["basic"] + (["raqm"] if cards.raqm_available() else []))
def test_the_art_cover_fit_rate_over_every_real_composed_opening_card(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(_R, "members_elect_window", lambda day: False)
    art = _art(tmp_path)
    openings = _art_series_openings(monkeypatch)
    steps = {name: _cover_step(composed, art, engine) for name, composed in openings.items()}
    real = {n: s for n, s in steps.items() if "worst" not in n}
    # anti-vacuity: every art series is in the corpus, and only single-company covers are
    series = {openings[n]["series"] for n in real}
    assert series == {"money_map"}, series                               # owner decisions 2026-11-20: the one art series
    assert all(len(openings[n]["opening_card"]["logos"]) == 1 for n in steps)
    assert len(real) == 6 and all(s is not None and s <= COVER_ART_CEILING for s in real.values()), real
    fit = [n for n, s in steps.items() if s is not None]
    assert len(fit) == 10 and len(steps) == 10, steps                  # the pinned fit rate: 10 of 10 (6 of 6 real)
    if engine == "basic":
        assert steps == _COVER_STEPS_BASIC, steps


# news-v23 (2026-11-19): `test_the_longest_real_13f_covers_keep_their_art` pinned the 13F spotlight's single-holding
# covers WITH art; that post is gone (the ONE 13F list, whose cover is the list and never takes art —
# tests/test_marketing_news_layouts.py::test_the_list_cover_declares_only_what_the_server_allows_and_never_draws_art).


def test_without_the_band_at_the_zones_top_no_real_cover_would_fit(tmp_path, monkeypatch):
    """The band's height is a real bound (measured, anti-regression): were the art band 300 px taller, the text under
    it would leave the content zone and NOT ONE real composed cover would fit even at the floor sizes."""
    from marketing import card_layouts

    monkeypatch.setattr(_R, "members_elect_window", lambda day: False)
    monkeypatch.setattr(card_layouts, "OPENING_ART_H", cards.OPENING_ART_H + 300)
    art = _art(tmp_path)
    openings = _art_series_openings(monkeypatch)
    assert all(_cover_step(c, art, "basic") is None for n, c in openings.items() if "worst" not in n)


def _art_template_script(card=None):
    return _template_script(**({"opening_card": card} if card is not None else {}))


def test_a_cover_that_cannot_draw_its_art_is_todays_opening_card_with_a_warning(tmp_path, monkeypatch, caplog):
    """Every way the art cannot go on the cover keeps the PLAIN opening card (byte for byte today's), with a WARNING
    and the reason in the report — never CardOverflow, never a skipped day."""
    import test_marketing_news_layouts as NL

    art = _art(tmp_path)
    plain_specs = cards.cards_for_script(_art_template_script(), font_path=FONT, layout_engine="basic")
    caplog.set_level(logging.WARNING, logger="marketing.cards")

    def run(script, a=art, **kw):
        report: Dict = {}
        specs = cards.cards_for_script(script, font_path=FONT, layout_engine="basic", art=a, report=report, **kw)
        return specs, report.get("opening_art_skipped")

    # no fit: a server-valid cover whose 165-character headline of 16-letter "W" words cannot sit under the band at the
    # floor sizes (news-v23: the 13F spotlight's single-holding covers that played this part are gone; 2026-11-20: a
    # headline with no boundary is ONE balanced block, fewer lines than its two halves took, so the 122-character Form 4
    # worst of before now fits — this one names a second officer)
    name = NL._w_name(32, 16)
    worst = {"kicker": "FORM 4", "logos": ["WWWWA"], "figure": "+987.7M",
             "headline": f"{NL._w_name(60, 16)}'s CEO and {NL._w_name(32, 16)}'s CFO disclosed buying {name} stock"}
    entries = [{"key": "WWWWA", "name": name, "url": None, "sha256": None}]
    assert tos.validate_opening_card(worst, tos.logo_keys(entries)) is None and len(worst["headline"]) == 165
    script = _template_script(opening_card=worst, logos=entries)
    specs, why = run(script)
    assert why == "no_fit" and specs[0].art is None and cards.ART_LABEL not in cards.onscreen_strings(specs[0])
    assert specs == cards.cards_for_script(script, font_path=FONT, layout_engine="basic")
    # two companies
    two = {"kicker": "13F SEASON", "logos": ["GME", "NVDA"], "headline": "A filer reported two changes"}
    specs, why = run(_art_template_script(two))
    assert why == "two_companies" and specs[0].art is None
    # an unreadable art file
    gone = _art(tmp_path, "gone.jpg")
    Path(gone.path).unlink()
    specs, why = run(_art_template_script(), a=gone)
    assert (why, specs) == ("unreadable", plain_specs)
    # a glyph of the label the face lacks
    with monkeypatch.context() as mp:
        mp.setattr(cards, "ART_LABEL", "AI illustration ☃")
        specs, why = run(_art_template_script())
    assert (why, specs) == ("missing_glyphs", plain_specs)
    # the label would take the video past the string cap
    n = len(dict.fromkeys(t for s in plain_specs for t in cards.onscreen_strings(s)))
    with monkeypatch.context() as mp:
        mp.setattr(cards, "ONSCREEN_TEXT_MAX", n)
        specs, why = run(_art_template_script())
    assert (why, specs) == ("too_many_strings", plain_specs)
    # owner decision 2026-11-20: a Money Map LOSS year's cover is its costs card — never art on it (the server makes
    # none: post_art `costs_cover`); the costs cover is kept
    import test_marketing_news_templates as NT

    loss = _T.compose(NT.rivian(), run_date=NT.THU, store_state="live", allow_x_url=False)
    loss_script = dict(loss, look=cards.LOOK, logos=[{"key": r["key"], "name": r["name"], "url": None, "sha256": None}
                                                     for r in loss["logo_refs"]])
    specs, why = run(loss_script)
    assert why == "costs_cover" and specs[0].art is None and specs[0].image is not None
    assert specs == cards.cards_for_script(loss_script, font_path=FONT, layout_engine="basic")
    warnings_ = [r.getMessage() for r in caplog.records if "no art on the opening card" in r.getMessage()]
    assert len(warnings_) == 6
    assert set(cards.OPENING_ART_SKIP_REASONS) == {"two_companies", "missing_glyphs", "no_fit", "unreadable",
                                                   "too_many_strings", "costs_cover"}
    # …and when it CAN be drawn: the opening card carries it, nothing else changes
    specs, why = run(_art_template_script())
    assert why is None and specs[0].art == art and specs[1:] == plain_specs[1:]
    assert cards.cards_for_script(_art_template_script(), font_path=FONT, layout_engine="basic") == plain_specs


def test_the_art_arguments_are_checked_and_a_lesson_never_draws_art(tmp_path, caplog):
    art = _art(tmp_path)
    with pytest.raises(ValueError, match="font_path"):
        cards.cards_for_script(_art_template_script(), art=art)
    with pytest.raises(TypeError):
        cards.cards_for_script(_art_template_script(), font_path=FONT, art="art.jpg")
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    lesson = _script()
    assert cards.cards_for_script(lesson, font_path=FONT, art=art) == cards.cards_for_script(lesson)
    assert any("lesson script" in r.getMessage() and "ignored" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("kwargs, exc", [
    ({"kind": "text", "title": "t"}, ValueError),                                    # only an opening draws art
    ({"kind": "disclaimer", "body": "d"}, ValueError),
    ({"kind": "opening", "badge": "k", "title": "h", "logos": (cards.LogoArt("A", "Alpha"), cards.LogoArt("B", "Beta"))},
     ValueError),                                                                    # one company only
])
def test_cardspec_draws_art_only_on_a_one_company_opening_card(kwargs, exc, tmp_path):
    with pytest.raises(exc):
        cards.CardSpec(**kwargs, art=_art(tmp_path))
    with pytest.raises(TypeError):
        cards.CardSpec("opening", badge="k", title="h", logos=(cards.LogoArt("A", "Alpha"),), art="art.jpg")


@pytest.mark.parametrize("kwargs", [
    {"sha256": "A" * 64}, {"sha256": "a" * 63}, {"sha256": 7}, {"path": ""}, {"path": None},
    {"width": 0}, {"height": -1}, {"width": True}, {"height": 774.0},
])
def test_art_image_refuses_what_it_cannot_be(kwargs):
    base = {"sha256": "a" * 64, "path": "/tmp/x.jpg", "width": 1376, "height": 774}
    with pytest.raises((ValueError, TypeError)):
        cards.ArtImage(**{**base, **kwargs})


def test_the_preview_draws_a_local_art_file_on_a_templates_cover_and_says_where(monkeypatch, tmp_path):
    """`marketing.preview --art-file`: a local JPEG, checked like a downloaded one, drawn on the opening card (and on
    a feature picture's band — this ceo_buys day's picture is `spotlight`, so not there); report.json says so. A bad
    file is recorded, never raised; a lesson ignores it."""
    art = _art(tmp_path)
    calls, report = _run_preview(monkeypatch, tmp_path, _data_script(), "--art-file", art.path)
    (kw,) = calls
    assert kw["specs"][0].kind == "opening" and kw["specs"][0].art.sha256 == art.sha256
    assert report["art"]["cover"] is True and report["art"]["picture"] is False and report["art"]["problem"] is None
    assert cards.ART_LABEL in report["onscreen_text"]
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(_art_bytes(size=(1600, 1600)))
    calls, report = _run_preview(monkeypatch, tmp_path, _data_script(), "--art-file", str(bad))
    assert calls[-1]["specs"][0].art is None and "16:9" in report["art"]["problem"]
    calls, report = _run_preview(monkeypatch, tmp_path, _hook_script(), "--art-file", art.path)
    assert all(s.art is None for s in calls[-1]["specs"]) and "lesson" in report["art"]["problem"]


def test_a_label_that_would_run_into_the_plate_is_no_fit_never_drawn_over_it(tmp_path, monkeypatch, caplog):
    """Defence in depth, made to bite: a label pill as wide as most of the band would cover the disc at its left —
    the cover does not fit (CardOverflow naming why), and cards_for_script keeps today's opening card."""
    art = _art(tmp_path)
    monkeypatch.setattr(cards, "art_label_size", lambda font_path, engine: (860, 44))
    with pytest.raises(cards.CardOverflow, match="runs into"):
        cards.layout_card(_art_opening(art), font_path=FONT, layout_engine="basic")
    report: Dict = {}
    caplog.set_level(logging.WARNING, logger="marketing.cards")
    specs = cards.cards_for_script(_art_template_script(), font_path=FONT, layout_engine="basic", art=art,
                                   report=report)
    assert specs[0].art is None and report == {"opening_art_skipped": "no_fit"}


# ── news-v23 fix round (2026-11-19) ─────────────────────────────────────────────────────────────────────────────────


def _lesson_layout(paragraphs, footer=None, **over):
    script = _image_script(image_post={"title": IMG_TITLE, "paragraphs": list(paragraphs)},
                           image_footer=footer or cards.LESSON_FOOTER, **over)
    spec = cards.image_for_script(script)
    return spec, cards.layout_image(spec, font_path=FONT, layout_engine="basic")


@pytest.mark.parametrize("paragraphs", [IMG_PARAGRAPHS, IMG_PARAGRAPHS[:2],
                                        ("Short one.", "A much longer paragraph that takes more lines to say what it "
                                         "needs to say about the business and its owners.", "Mid-length one here.")])
def test_the_lesson_picture_cards_are_equal_and_fill_the_room(paragraphs):
    """mock_lesson.lesson: ONE warm card per paragraph, all the same height, from the title + 34 to the footer − 34,
    each paragraph centred in its card; a gold dot (r 16) at the card's left + 34, centred on it; the dots joined by
    rail segments that stop 10 px short of each. The worker sized each card to its text and centred the stack
    (floating cards of unequal height, a 13 px dot, one rail through the dots)."""
    from marketing import card_layouts as cl

    _spec, layout = _lesson_layout(paragraphs)
    boxes = sorted((r.box for r in layout.rects if r.fill == look.CARD), key=lambda b: b[1])
    assert len(boxes) == len(paragraphs)
    heights = {b[3] - b[1] for b in boxes}
    assert max(heights) - min(heights) <= 1                                  # equal (rounding)
    title_bottom = max(ln.ink[3] for ln in layout.lines if ln.field == "title")
    # from just under the title (34 px under its last line's descender foot — the mock's card top is y 356 under the
    # same two-line 80 px title) …
    assert cl.LESSON_CARDS_TOP_GAP <= boxes[0][1] - title_bottom <= cl.LESSON_CARDS_TOP_GAP + 0.3 * 80
    if len(paragraphs) == 3 and paragraphs == IMG_PARAGRAPHS:
        assert abs(boxes[0][1] - 356) <= 4
    assert boxes[-1][3] == pytest.approx(layout.footer_top - cl.LESSON_CARDS_BOTTOM_GAP, abs=1)   # … to the footer
    for i, box in enumerate(boxes):
        ink = [ln.ink for ln in layout.lines if ln.field == f"paragraph{i}"]
        top, bot = min(b[1] for b in ink), max(b[3] for b in ink)
        mid = (box[1] + box[3]) / 2
        assert abs((top + bot) / 2 - mid) <= 14                              # centred in its card
    dots = sorted(layout.ellipses, key=lambda e: e.box[1])
    assert len(dots) == len(paragraphs)
    for d, box in zip(dots, boxes):
        assert d.box[2] - d.box[0] == 2 * cl.LESSON_DOT_R and d.box[0] == box[0] + cl.LESSON_DOT_X
        assert abs((d.box[1] + d.box[3]) / 2 - (box[1] + box[3]) / 2) <= 1
    rails = sorted((r.box for r in layout.rects if r.fill == cl.LESSON_RAIL), key=lambda b: b[1])
    assert len(rails) == len(paragraphs) - 1
    for rail, a, b in zip(rails, dots, dots[1:]):
        assert rail[1] - a.box[3] == pytest.approx(cl.LESSON_RAIL_CLEAR, abs=1)
        assert b.box[1] - rail[3] == pytest.approx(cl.LESSON_RAIL_CLEAR, abs=1)


def test_the_lesson_paragraphs_shrink_before_the_title_does():
    """mock_lesson: the paragraphs' size steps down (46 → 30) to fit the equal cards before anything else; the title
    keeps 80 px."""
    long = ["Some days he is gloomy and names a very low price for everything the business owns and earns. " * 2] * 3
    spec, layout = _lesson_layout([p.strip()[:219] for p in long])
    assert layout.step == 0 and {ln.size for ln in layout.lines if ln.field == "title"} == {80}
    sizes = {ln.size for ln in layout.lines if ln.field.startswith("paragraph")}
    assert len(sizes) == 1 and 30 <= sizes.pop() < cards.IMAGE_PARAGRAPH_STYLE.start


# the look's strings only from a news-v23 web — `look` on the read-back (finding: a new worker on an old web 422'd)

def test_the_look_constants_mirror_the_server():
    from app.schemas import marketing as sch

    assert cards.LOOK == sch.READBACK_LOOK == "news-v23"
    assert "look_v23" in sch.WORKER_CAPABILITIES
    assert cards.new_look({"look": cards.LOOK}) and not cards.new_look({}) and not cards.new_look({"look": "news-v22"})
    assert not cards.new_look(None) and not cards.new_look({"look": True})


def _old_web(script):
    out = dict(script)
    out.pop("look", None)
    return out


def test_a_lesson_from_an_older_web_draws_no_kicker_no_footer_and_no_word_on_its_picture():
    """An older web (news-v22 or a rollback) refuses LESSON_KICKER and the lesson footer on every video card, and
    "Caydex" and LESSON_KICKER on the picture — each a 422 that failed the whole render. Without `look`, none is drawn
    or declared: a video draws only its own strings and the brand word (VIDEO_BRAND_TEXT on every web)."""
    old = _old_web(_image_script(image_footer="Educational only · not investment advice · Written with AI assistance"
                                                " · Sep 29, 2026 · Caydex"))
    specs = cards.cards_for_script(old)
    assert specs and all(not s.pill and not s.footer for s in specs)
    for s in specs:
        drawn = cards.onscreen_strings(s)
        assert cards.LESSON_KICKER not in drawn and cards.LESSON_FOOTER not in drawn
    img = cards.image_for_script(old)
    assert img.look is False
    strings = cards.image_onscreen_strings(img)
    assert cards.LESSON_KICKER not in strings and cards.BRAND_ROW_WORD not in strings
    layout = cards.layout_image(img, font_path=FONT, layout_engine="basic")
    assert not {"brand", "kicker"} & {ln.field for ln in layout.lines} and layout.pill is None
    assert layout.brand is not None                                         # the tile (pixels) is still drawn
    new = cards.cards_for_script(_image_script())
    assert all(s.pill == cards.LESSON_KICKER for s in new)
    assert {cards.LESSON_KICKER, cards.BRAND_ROW_WORD} <= set(cards.image_onscreen_strings(
        cards.image_for_script(_image_script())))


def test_a_template_from_an_older_web_draws_no_footer_on_its_cards_and_no_word_on_its_picture():
    import test_marketing_news_layouts as NL
    from marketing import news_layouts as nl

    long_footer = ("Educational only · not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026 · Caydex · "
                   "Not affiliated with anyone named")
    script = _old_web(_template_script(image_footer=long_footer))
    specs = cards.cards_for_script(script)
    assert all(not s.footer for s in specs)
    assert all(long_footer not in cards.onscreen_strings(s) for s in specs)
    spec = dict(NL._rows_spec(), footer=long_footer)
    image = nl.template_image_for_script({"authorship": "template", "image_spec": spec, "image_footer": long_footer,
                                          "logos": NL._entries()}, NL._table())
    assert image.look is False
    layout = nl.layout_template(image, font_path=FONT, layout_engine="basic")   # the long footer still fits
    assert "brand" not in {ln.field for ln in layout.lines}
    assert cards.BRAND_ROW_WORD not in nl.drawn_strings(image, layout)
    assert long_footer in nl.drawn_strings(image, layout)


def test_the_video_brand_row_has_the_covers_sizes():
    """cover_13f_berkshire.jpg: a 56 px tile, a 34 px word, a 28 px kicker pill (46 px tall) — the picture's 52 / 32 /
    26 were used on every video card."""
    layout = cards.layout_card(cards.CardSpec("text", title="Prices swing", body="Gloomy days, low quotes.",
                                              pill=cards.LESSON_KICKER, footer=cards.LESSON_FOOTER),
                               font_path=FONT, layout_engine="basic")
    brand = [ln for ln in layout.lines if ln.field == "brand"]
    pill = [ln for ln in layout.lines if ln.field == "pill"]
    assert brand[0].size == 34 and pill[0].size == 28
    assert layout.brand == (88, 140, 144, 196) and layout.pill[3] - layout.pill[1] == 46 and layout.pill[2] == 992
    title = [ln for ln in layout.lines if ln.field == "title"]
    assert title[0].x == layout.brand[0]                                     # the mark and the headline line up
