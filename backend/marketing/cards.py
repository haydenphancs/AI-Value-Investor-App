"""
The still cards of the day's 9:16 video (Phase 4, SYSTEM_DESIGN_GUIDELINES §12.8): 1080×1920 PNGs
that `render.py` lays under the burned captions — and the day's 4:5 POST IMAGE (drop 1,
2026-10-09: "an image on every post"), a 1080×1350 baseline JPEG. PURE apart from Pillow and
fontTools (imported inside functions, like captions.py); no app.*, no network.

What a video shows, in order:

* the accepted script's 3-4 TEXT cards (title + body, always together, in order). The FIRST is on
  screen from frame 0, through the hook (drop 1: "videos open on the company, never on our logo"
  — the brand card that used to cover the hook is gone). The hook itself is shown ONLY by the
  burned captions — unless the run froze the video feature `hook_card` (below).
* the closing DISCLAIMER card after the narration: the server-supplied disclaimer text verbatim,
  a small logo and the end card's CTA — "caydexinvest.com" (LEGACY_CTA), or "Search Caydex on the
  App Store" (CTA) when the script read-back names it (`end_card_cta`, Batch 2).
* (the BRAND card — the logo and the wordmark "Caydex" — is drawn only when a script carries no
  text card at all, so the narration still has a card under it; the writer always emits three.)
* ("stat" — a big figure, a label and an optional body — exists for the later reportorial class C
  and is laid out and tested now; class A never builds one.)

A TEMPLATE (news) script's video (drop 2a, `video_layout == "per_line"`) is different: its
OPENING card (`opening_card`: kicker, 1-2 company logos, chip, figure, headline) is shown alone
over the hook, then one text card per narration line, then the disclaimer (`cards_for_script`,
timed by `video.timeline(..., hook_card=True)`). A company logo is drawn UNALTERED — only scaled —
on a disc (`look.draw_disc`: white, dark for a logo made for a dark background, the plate's own colour for
a logo served on a light plate); a logo that is missing or did not verify is a WORDMARK disc carrying the
company name, which is then a drawn (declared) string. Its post image is drawn
by `news_layouts.py` from the closed `image_spec` (`render_post_image` dispatches; a template never
falls back to the lesson layout, which would draw its alt text).

The VIDEO FEATURES (Batch 2, 2026-10-19, "first seconds"; owner decision 7 amended rules §5): the server
freezes them per run and sends them in the script read-back (`video_features`, read by `video_features`
— only the kind's own: LESSON_VIDEO_FEATURES / TEMPLATE_VIDEO_FEATURES). Each only ADDS to today's video
and each falls back to it — never a skipped day:

* `hook_card` (a lesson): the video OPENS on a HOOK card — the accepted hook, verbatim, the two-tone
  headline — shown alone over the narrated hook (`video.timeline(..., hook_card=True)`;
  `opens_on_hook_card`). A hook that is not one drawable string, lacks a glyph or cannot fit at the floor
  size is logged (WARNING) and the video opens on its first content card, as before.
* `data_card` (a template): the ONE text card the read-back names (`data_card_slot`, an index into its
  `cards`: a fact card the picture already shows — the server's per-series choice, never a hedge card,
  which a template keeps on screen for its whole line on purpose; CARDS-B2-1) becomes the DATA card — the
  day's post image (its closed `image_spec`, footer included) laid out in the card's text zone
  (news_layouts.DATA_CARD_FRAME). No slot (WARNING: a web that names none), or one that does not name a
  text card of this video (ERROR), draws no data card. Kept as the text card (WARNING) when the picture
  cannot be drawn there legibly (a line under news_layouts.DATA_CARD_MIN_PX), lacks a glyph, or would take
  the video past ONSCREEN_TEXT_MAX strings.
* `push_in` (either): card 0 zooms in slowly — the video's concern (video.py); it draws no string.

The server ALLOWS each feature card's strings only while the feature is frozen (`run_service.
_check_onscreen_text`); a worker that drew them unfrozen would be refused (422).

PER-POST ART (workstream C, owner decisions 2026-11-15 — "Picture and video cover"): a template's opening card
may draw the run's verified art (`ArtImage`, from `art.resolve_art`): a band across the top of the card, edge to
edge (OPENING_ART_W × OPENING_ART_H, the brand row on a navy scrim over it), its ONE logo disc in a gold ring on
the band's bottom edge, the chip, headline and figure under it in sizes of their own, and the code-owned ART_LABEL ("AI illustration") burned on the band in a PAGE pill — declared
like any string (`onscreen_strings`), beside the video's `opening_art_sha256` (the server allows the label only
then). `cards_for_script(..., art=)` checks it with the render's font and engine and keeps the plain opening card
(WARNING) when it cannot be drawn: two companies, a glyph, no fit at the floor sizes, an unreadable file, too many
strings — never a skipped day. A card that draws no art keeps its bytes.

What the post image shows (`ImageSpec`, `render_image`): the brand row (the Caydex tile + BRAND_ROW_WORD, the
LESSON_KICKER pill), the accepted output's `image_post` title as the two-tone headline and its 2-4 paragraphs each
on a warm card along a gold rail, and the server's code-owned `image_footer` as the short footer line. Same rules
as a card: every string declared verbatim (`image_onscreen_strings` — the server allows only the accepted
`image_post` strings, the footer and LESSON_BRAND_TEXT, and requires the footer), never truncated, a glyph Inter lacks refused up front. Encoded as a BASELINE JPEG
(sRGB by convention: RGB, no ICC profile, no EXIF; 4:4:4 so coloured text stays crisp), stepping
the quality down until it is at most POST_IMAGE_MAX_BYTES — `ImageTooLarge` when even the lowest
step is not, never a bigger file.

Why each piece is shaped the way it is:

* **The strings are checked, the pixels are not.** The worker declares every string a card drew
  (`onscreen_strings`) and the server refuses anything that is not the accepted script's card
  titles/bodies, its disclaimer card or `BRAND_TEXT` — or a frozen feature's strings (the hook, the
  post image's) — (`run_service._check_onscreen_text`). So a
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

THE LOOK (news-v23, owner 2026-11-19: "i need exactly these styles, colors, words"): `look.py` holds the approved
kit — the navy→black gradient with its gold glow, the brand row (the vendored Caydex tile + "Caydex", the kicker in
the gold pill right-aligned), the two-tone UPPERCASE headline, warm-white cards, chips, logo discs, the short footer
line, the colour rules by series (GREEN a purchase / an increase / a profit, RED a decrease / a loss, GOLD_INK a
stake or a share) — and `card_layouts.py` places each video card kind and the lesson picture with it (the brand row
140 px from the top, nothing in the safe bottom VIDEO_SAFE_BOTTOM). Every card draws the brand row; a lesson card
its LESSON_KICKER and LESSON_FOOTER, a template card its opening kicker and its image_footer (`CardSpec.pill` /
`.footer`), all declared. Bump CARD_RENDER_VERSION with ANY change to what a card looks like: it is part of the
video's reuse key (`render.render_key`).
"""

from __future__ import annotations

import dataclasses
import io
import logging
import math
import re
from dataclasses import dataclass, field
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

#: The page colour of a logo tile and the white of the logo disc (news-v23: the full palette — the gradient, the
#: gold, the warm card, the inks, green / red / gold-ink — is marketing/look.py's).
PAGE = "#111827"
TEXT = "#FFFFFF"
WORDMARK = "Caydex"
#: The end card's call to action (owner decision 6, Batch 2: post_copy.SEARCH_LINE without its full
#: stop). Drawn only when the script read-back names it (`end_card_cta`): the web says so only while the
#: run's accepted output has the app on the store, and only a web that allows it can name it.
CTA = "Search Caydex on the App Store"
#: …and the CTA every card drew before Batch 2: still drawn against a read-back that names no CTA (an
#: older web, whose allow-list holds only this one) — and allowed by both webs, so it is the fallback.
LEGACY_CTA = "caydexinvest.com"
END_CARD_CTAS: Tuple[str, ...] = (LEGACY_CTA, CTA)
#: The only strings a card draws besides the script's own and a frozen feature's (mirrors
#: app.schemas.marketing.VIDEO_BRAND_TEXT — a superset since Batch 2, so neither deploy order turns the
#: end card into a 422; tests/test_marketing_cards.py pins them equal).
BRAND_TEXT: Tuple[str, ...] = (WORDMARK, LEGACY_CTA, CTA)
#: news-v23 (2026-11-19) — mirrors of app.schemas.marketing.BRAND_ROW_WORD / LESSON_KICKER and
#: post_copy.IMAGE_FOOTER_HEAD (pinned equal by tests/test_marketing_cards.py): the brand row's word (on every
#: card and picture), the lesson's kicker pill and the lesson's short footer (on every lesson card and picture).
BRAND_ROW_WORD = "Caydex"
LESSON_KICKER = "INVESTING LESSON"
LESSON_FOOTER = "Not investment advice"
#: news-v23 — mirrors app.schemas.marketing.READBACK_LOOK (pinned equal): the value of the script read-back's
#: `look` key that tells this worker its web allow-lists the look's own strings — the brand row's word on a PICTURE,
#: the lesson's kicker pill (picture and cards) and the short footer on every video card. A read-back without it (a
#: web before news-v23, or after a web rollback) is drawn WITHOUT them (`new_look`): that web refuses each one with a
#: 422, which would fail the whole render and lose the day.
LOOK = "news-v23"


def new_look(script: Any) -> bool:
    """Whether the read-back `script` comes from a web that allows the news-v23 look's strings (`LOOK`)."""
    return isinstance(script, dict) and script.get("look") == LOOK

#: Batch 2 (2026-10-19) — mirrors of app.schemas.marketing (pinned equal by tests/test_marketing_cards.py):
#: the video features the server may freeze into a run, and which a script of each kind may carry (a
#: template already opens on its code-owned opening card; only a template has a picture to draw).
VIDEO_FEATURE_HOOK_CARD = "hook_card"
VIDEO_FEATURE_DATA_CARD = "data_card"
VIDEO_FEATURE_PUSH_IN = "push_in"
VIDEO_FEATURES: Tuple[str, ...] = (VIDEO_FEATURE_HOOK_CARD, VIDEO_FEATURE_DATA_CARD, VIDEO_FEATURE_PUSH_IN)
LESSON_VIDEO_FEATURES: Tuple[str, ...] = (VIDEO_FEATURE_HOOK_CARD, VIDEO_FEATURE_PUSH_IN)
TEMPLATE_VIDEO_FEATURES: Tuple[str, ...] = (VIDEO_FEATURE_DATA_CARD, VIDEO_FEATURE_PUSH_IN)

#: Part of the video's AND the post image's reuse keys: bump with any change to layout, sizes,
#: colours, assets, the card set or the JPEG encoding. v2 (drop 1): the video lost its opening
#: brand card; the 4:5 post image was added. v3 (drop 2a): the template opening card, the company
#: logo plates and wordmark tiles, the template post-image layouts (news_layouts.py). v4 (Batch 2):
#: the hook card, the data card and the App Store end card (a lesson card or post image that draws
#: none of them keeps its v3 bytes — tests/test_marketing_cards.py pins the drop-1 goldens). v5
#: (2026-11-15, the spotlight posts): the `feature` template image — the art band (FEATURE_BAND_*:
#: kicker pill + logo plate), the figure, its label, the headline and the fact rows — and, in the same
#: release (workstream C), the per-post art in that band and on the opening card. Bumped ONCE for both:
#: every card and post image that draws neither keeps its v4 bytes (tests/test_marketing_news_layouts.py
#: pins them).
#: v6 (news-v23, 2026-11-19 — owner: "i need exactly these styles, colors, words"): the approved look on every
#: card and post image (marketing/look.py: the gradient + glow, the brand row with its kicker pill, the two-tone
#: headline, warm cards, logo discs — dark for a logo made for a dark background — the short footer, the
#: green / red / gold figures). Every card and picture changes its bytes.
CARD_RENDER_VERSION = "cards/v6"

KINDS: Tuple[str, ...] = ("brand", "text", "stat", "disclaimer", "opening", "hook", "data")
#: The kinds shown ALONE over the narrated hook as card 0 — `video.timeline(..., hook_card=True)`.
HOOK_CARD_KINDS: Tuple[str, ...] = ("opening", "hook")

# ── drop 2a: template (news) scripts — mirrors of app.* (the worker never imports app.*; ──────
# tests/test_marketing_cards.py and tests/test_marketing_news_layouts.py pin every one equal)

#: app.schemas.marketing.TEMPLATE_AUTHORSHIP — a script built by a fixed template, not the writer.
TEMPLATE_AUTHORSHIP = "template"
#: app.schemas.marketing.VIDEO_LAYOUT_PER_LINE — [opening] + one text card per narration line.
VIDEO_LAYOUT_PER_LINE = "per_line"
#: app.services.marketing.template_onscreen: the closed image_spec / opening_card schema.
LAYOUTS: Tuple[str, ...] = ("rows", "spotlight", "pair", "bars", "grid", "feature", "ranked", "figures", "ledger",
                            "tally")
#: What this worker ACCEPTS from the server (mirrors template_onscreen.SHIPPED_LAYOUTS, same members,
#: same order). Drop 2b (2026-10-10) ships `pair` and `grid` with their series — both sides' literals
#: were flipped together; news_layouts derives what it accepts from this tuple. 2026-11-15 ships
#: `feature` (the spotlight series' art-band picture): this image declares `layout_feature` for it. news-v23 phase 3
#: (2026-11-20) ships `ranked` (the weekly theme post's angle 2, a theme's members by market value): this image
#: declares `theme_angles` for it — and, in the same release (2026-11-20), `figures`, `ledger` and `tally` (the theme's
#: last three angles), under the same capability.
SHIPPED_LAYOUTS: Tuple[str, ...] = ("rows", "spotlight", "pair", "bars", "grid", "feature", "ranked", "figures",
                                    "ledger", "tally")
#: The layouts a template video may draw as its DATA CARD (mirrors template_onscreen.DATA_CARD_LAYOUTS):
#: never `feature` — `news_layouts.layout_data_card` refuses any other (CardOverflow: the text card stays).
DATA_CARD_LAYOUTS: Tuple[str, ...] = ("rows", "spotlight", "pair", "bars", "grid")
SPEC_VERSION = 1
MAX_LOGOS = 12
MAX_ROWS = 8
MAX_SECTIONS = 3
#: news-v23 (the 13F list): one section of up to six rows (template_onscreen.MAX_SECTION_ROWS, pinned equal).
MAX_SECTION_ROWS = 6
MAX_CELLS = 3
#: news-v23 — the earnings TABLE (template_onscreen.TABLE_CELLS / MAX_TABLE_LINES / TABLE_KEYS, pinned equal): a `rows`
#: row's `table` of 1..MAX_TABLE_LINES lines, each exactly TABLE_CELLS cells (a label, then one per column), under its
#: section's `columns` (TABLE_CELLS − 1 labels); every row of such a section carries one, and only such rows may.
TABLE_CELLS = 4
MAX_TABLE_LINES = 2
TABLE_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("cells",), ())
MAX_NOTES = 2
MAX_LINES = 2
MIN_BARS, MAX_BARS = 2, 7
MIN_FLOW, MAX_FLOW = 2, 4
MIN_TILES, MAX_TILES = 3, 12
#: news-v23 phase 3 — a `ranked` picture's rows (template_onscreen.MIN_RANKED_ROWS / MAX_RANKED_ROWS / RANKED_ROW_KEYS,
#: pinned equal): `{rank, logo, chip?, name, value, ratio}`, ranked 1..n in order, the ratio the bar (never drawn).
MIN_RANKED_ROWS, MAX_RANKED_ROWS = 4, 10
RANKED_ROW_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("rank", "logo", "name", "value", "ratio"), ("chip",))
#: news-v23 phase 3 — the theme picture's decoration tokens (template_onscreen.THEME_ICONS, pinned equal), each drawn
#: by `marketing.theme_art` from the approved mock's own drawing; never text.
THEME_ICONS: Tuple[str, ...] = ("chip", "bolt", "wafer", "shield", "radar")
#: news-v23 phase 3, the theme's last three angles (2026-11-20) — mirrors of template_onscreen (pinned equal): the table
#: layouts, their rows' bounds and keys, the two column heads of 1..MAX_HEAD_LINES lines, the figures' shapes (each
#: drawn GREEN or RED by its sign: MINUS = red), the empty cell, and a table cover's rows (`opening_card.list.layout`).
THEME_TABLE_LAYOUTS: Tuple[str, ...] = ("figures", "ledger", "tally")
MIN_THEME_TABLE_ROWS = 4
MAX_THEME_TABLE_ROWS: Dict[str, int] = {"figures": 10, "ledger": 10, "tally": 6}
THEME_HEADS, MAX_HEAD_LINES = 2, 2
THEME_TABLE_ROW_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "figures": (("logo", "name", "growth", "ratio", "profit"), ("chip",)),
    "ledger": (("logo", "name", "buys", "sells"), ("chip",)),
    "tally": (("logo", "name", "count"), ("chip",)),
}
MINUS = "−"
EMPTY_CELL = "—"
GROWTH_FIGURE_RE = re.compile(r"(?:\+|−)[1-9][0-9]{0,2}(?:,[0-9]{3})*%|0%")
PROFIT_FIGURE_RE = re.compile(r"−?\$(?:[1-9][0-9]{0,2}(?:,[0-9]{3})*|0\.(?:0[1-9]|[1-9][0-9]))|\$0")
LEDGER_AMOUNT_RE = re.compile(r"\$[1-9][0-9]{0,2}(?:\.[0-9])?[KMBT]?|—")
TALLY_COUNT_RE = re.compile(r"[1-9][0-9]{0,2}")
MAX_OPENING_TABLE_ROWS = 6
#: The server's NAME bound of each table (company_news_rules.THEME_TABLE_NAME_W / THEME_TABLE_NAME_LINES by angle,
#: THEME_TABLE_NAME_FLOOR_PX, pinned equal): a company whose name takes at most that many lines of that width
#: at the floor is the only one the server lists, and this worker's rows draw it (one line, else two) on the picture
#: and on the cover with the widest chips and figures — tests/test_marketing_news_layouts.py lays the widest out.
THEME_TABLE_NAME_W: Dict[str, int] = {"figures": 250, "ledger": 275, "tally": 600}
THEME_TABLE_FLOOR_PX = 22
THEME_TABLE_NAME_LINES: Dict[str, int] = {"figures": 2, "ledger": 2, "tally": 1}
#: news-v23 phase 3 — the theme TILE's text column and floor size, and the most lines a tile and its name draw at the
#: floor: the server's bounds (news_templates.THEME_TILE_TEXT_W / THEME_TILE_FLOOR_PX / THEME_TILE_MAX_LINES /
#: THEME_NAME_MAX_LINES, pinned equal), which this worker's grid placer keeps (tests/test_marketing_news_layouts.py
#: lays the widest tiles out). And the ranked row's narrowest name column at the floor (THEME_RANKED_NAME_W /
#: THEME_RANKED_FLOOR_PX).
THEME_TILE_TEXT_W = 347
THEME_TILE_FLOOR_PX = 22
THEME_TILE_MAX_LINES = 3
THEME_NAME_MAX_LINES = 2
THEME_RANKED_NAME_W = 440
THEME_RANKED_FLOOR_PX = 22
MIN_FACTS, MAX_FACTS = 1, 4
MAX_OPENING_LOGOS = 2
LOGO_KEY_MAX_CHARS = 16
#: news-v23 — a `rows` row's ticker CHIP (mock_form4.wide_row_card, drawn over the row's name): at most this long
#: (template_onscreen.ROW_CHIP_MAX_CHARS, pinned equal) — a ticker, never a phrase.
ROW_CHIP_MAX_CHARS = 10
#: news-v23 — a `rows` row's keys, (required, optional), and its `label`'s longest (template_onscreen.ROW_KEYS /
#: ROW_LABEL_MAX_CHARS, pinned equal): company stakes (stakes_nvidia.jpg) draw the basis word ("invested") over the
#: row's figure.
#: Owner decisions 2026-11-20 (the insider list, insider_multi3.jpg): a row's `detail`, its one small grey line under the
#: name and roles ("28K shares · $71.20–$71.80 a share, as filed"), at most ROW_DETAIL_MAX_CHARS long
#: (template_onscreen.ROW_DETAIL_MAX_CHARS, pinned equal).
ROW_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("cells",), ("logo", "chip", "label", "detail"))
ROW_LABEL_MAX_CHARS = 16
ROW_DETAIL_MAX_CHARS = 64
BAR_STYLES: Tuple[str, ...] = ("fill", "outline")
#: Owner decision 2026-11-20 ("A: costs vs revenue (Recommended)", money_map_loss_A_rivian.jpg) — a Money Map LOSS
#: year's COSTS card, on its picture (`bars` spec `costs`) and its video cover (opening card `costs` + `subtitle`):
#: template_onscreen.COSTS_KEYS / COSTS_BAR_KEYS / COSTS_NET_KEYS / COSTS_PARTS / COSTS_CALLOUT_LINES, pinned equal.
#: news_layouts draws it in place of the flow's steps and the one-line callout (`_costs_card`).
COSTS_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("revenue", "net", "total", "parts", "callout"), ("note",))
COSTS_BAR_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("label", "value", "ratio"), ())
COSTS_NET_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("label", "value"), ())
COSTS_PARTS: Tuple[int, ...] = (0, 2, 3)
COSTS_CALLOUT_LINES = 2
COMMON_KEYS: Tuple[str, ...] = ("layout", "version", "kicker", "footer")
LAYOUT_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "rows": (("title", "sections"), ("header", "subtitle", "notes")),
    "spotlight": (("header", "figure", "headline"), ("lines",)),
    "pair": (("left", "right", "figure", "label"), ("lines",)),
    "bars": (("header", "title", "subtitle", "segments", "flow", "callout"), ("costs",)),
    "grid": (("title", "subtitle", "tiles"), ("more", "icon", "sub_break")),
    "feature": (("header", "figure", "figure_label", "headline", "facts"), ()),
    "ranked": (("title", "subtitle", "rows"), ("more", "icon")),
    "figures": (("title", "subtitle", "heads", "rows"), ("more", "icon", "note")),
    "ledger": (("title", "subtitle", "heads", "rows"), ("more", "icon", "note")),
    "tally": (("title", "subtitle", "unit", "rows"), ("more", "icon", "note")),
}
OPENING_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("kicker", "logos", "headline"), ("chip", "figure", "list",
                                                                                "figure_label", "subtitle", "costs"))
#: news-v23 (2026-11-20, the Money Map cover) — the opening card's `figure_label`, drawn under the figure inside its
#: warm card (template_onscreen.OPENING_FIGURE_LABEL_MAX_CHARS, pinned equal): only beside a figure.
OPENING_FIGURE_LABEL_MAX_CHARS = 60
#: news-v23 — the LIST cover's `list` (template_onscreen.OPENING_LIST_KEYS / MAX_OPENING_ROWS, pinned equal): the
#: column label (optional since the Congress Count's list, which has none), an earnings table's `columns`, 1..MAX_OPENING_ROWS
#: `rows` rows and the "+N more changes in the caption" line.
OPENING_LIST_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("rows",), ("heading", "columns", "more", "layout",
                                                                         "heads", "unit", "note"))
#: Owner decisions 2026-11-20: five — the Form 4 lists' compact all-rows cover (insider_multi5_cover_alt_all5.jpg).
MAX_OPENING_ROWS = 5
#: A `feature` fact row (2026-11-15): template_onscreen.FACT_KEYS, pinned equal.
FACT_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("label", "value"), ())
#: Drop 2b: a pair's sides (the investee may have no logo key — a wordmark plate of its name) and a
#: grid tile, (required, optional) — template_onscreen's, pinned equal by tests/test_marketing_layouts_2b.py.
PAIR_SIDE_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "left": (("logo", "name"), ()),
    "right": (("name",), ("logo",)),
}
GRID_TILE_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("logo", "name"), ("chip", "line"))
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


# The video cards' sizes (news-v23: the approved look, laid out by marketing/card_layouts.py).
TITLE_STYLE = _Style(76, 40, 1.08)              # a text card's title: the two-tone headline (WHITE, then GOLD)
BODY_STYLE = _Style(52, 30, 1.28)               # its body on the warm card (INK)
FIGURE_STYLE = _Style(168, 84, 1.04)            # a stat card's figure (its tone)
LABEL_STYLE = _Style(52, 32, 1.2)               # its label (INK)
STAT_BODY_STYLE = _Style(44, 30, 1.3)           # its body (INK2)
DISCLAIMER_STYLE = _Style(46, 28, 1.3)          # the end card's disclaimer text (SUB)
CTA_STYLE = _Style(60, 40, 1.2)                 # "caydexinvest.com" (GOLD)
#: "Search Caydex on the App Store" (GOLD): 885 px at 56 px — ONE line in the 920 px column at every step.
STORE_CTA_STYLE = _Style(56, 40, 1.2)
WORDMARK_STYLE = _Style(132, 96, 1.2)           # "Caydex" on the brand card (WHITE)
BRAND_TILE_STYLE = _Style(360, 240, 1.0)        # the brand card's logo tile (side)
CHIP_STYLE = _Style(30, 22, 1.0)                # a cover's chip in its gold pill; a text card's badge
#: The template opening card — the cover (drop 2a; news-v23 look): its headline beside the company plate (the logo
#: disc, OPENING_DISC radius — × 0.75 each for two companies — in a gold ring, the chip in a gold pill under it,
#: OPENING_CHIP_STYLE), the figure big on a warm card in the series' tone. Visual-fidelity round (2026-11-20): the
#: plate is the LIST covers' (cover_13f_berkshire.jpg: news_layouts.PLATE_DISC r 86, CHIP_PX 24 — pinned equal) and the
#: headline starts at the content's top, under the brand row, as theirs does — the card's room is never centred (it was
#: r 110 / 30 px, the block centred ~170 px under the brand row; the mock's headline sits ~60 px under it).
OPENING_HEADLINE_STYLE = _Style(80, 40, 1.08)
OPENING_DISC = _Style(86, 46, 1.0)
OPENING_CHIP_STYLE = _Style(24, 22, 1.0)
OPENING_FIGURE_STYLE = _Style(200, 84, 1.04)
#: news-v23 (2026-11-20): the figure's label under it, inside its card (INK; the Money Map cover's "of every $100 of
#: revenue was net income" — the picture callout's words, money_map_costco.jpg).
OPENING_FIGURE_LABEL_STYLE = _Style(44, 30, 1.2)
#: The opening card WITH per-post art (workstream C, owner decisions 2026-11-15: "Picture and video cover"; news-v23
#: look): the art across the TOP of the card, edge to edge, OPENING_ART_W × OPENING_ART_H (16:9 of the full width),
#: under a navy scrim the brand row reads on; the logo disc (OPENING_ART_DISC radius) in its gold ring cut into the
#: band's bottom-left edge; ART_LABEL burned at the band's bottom-right; the chip beside the disc, then the headline
#: and the figure card under the band — every line above the caption band (cards.TEXT_ZONE_Y[1]).
OPENING_ART_W = WIDTH
OPENING_ART_H = 608
OPENING_ART_DISC = _Style(100, 64, 1.0)
#: The code-owned label burned on every per-post art (mirrors app.services.marketing.template_onscreen.ART_LABEL,
#: pinned equal by tests/test_marketing_news_layouts.py): the disclosure on the picture and the cover. Never a
#: model or provider name. Drawn at ART_LABEL_PX (fixed, never wrapped, never stepped down) in a PAGE pill with
#: TEXT ink (ART_LABEL_FILL / ART_LABEL_INK, set with the colours below) — on palette and legible over any art.
ART_LABEL = "AI illustration"
ART_LABEL_PX = 24
#: The pill (mock_spotlight.ai_pill): the label's width + 2 × ART_LABEL_PAD_X wide, ART_LABEL_PX + 2 × ART_LABEL_PAD_Y
#: tall (size + 20), the label's baseline ART_LABEL_BASE_UP above the pill's bottom ("ls").
ART_LABEL_PAD_X, ART_LABEL_PAD_Y = 16, 10
ART_LABEL_BASE_UP = 10
#: The lesson hook card (Batch 2; news-v23 look): the accepted hook alone, big, two-tone, centred in the content
#: zone. 14 words of 8-15 letters (writer_prompts.HOOK_MAX_WORDS) fit before the floor.
HOOK_STYLE = _Style(92, 56, 1.08)
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
class ArtImage:
    """The per-post ART a template card or picture may draw (workstream C, owner decisions 2026-11-15): `path` is
    a VERIFIED local JPEG (`art.resolve_art`: the read-back's content-addressed object, sha256-checked and decoded
    under its own pixel bound) of exactly `width` × `height`; `sha256` is the stored object's — what the surface
    that draws it DECLARES (`metadata.art_sha256` / `metadata.opening_art_sha256`) and what the render keys carry.
    Drawn only with the code-owned ART_LABEL burned on it."""

    sha256: str
    path: str
    width: int
    height: int

    def __post_init__(self) -> None:
        if not isinstance(self.sha256, str) or len(self.sha256) != 64 or any(
                c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("ArtImage.sha256 must be 64 lower-case hex characters")
        if not isinstance(self.path, str) or not self.path:
            raise TypeError(f"ArtImage.path must be a non-empty str, not {self.path!r}")
        for name in ("width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"ArtImage.{name} must be a positive int, not {value!r}")


@dataclass(frozen=True)
class CardSpec:
    """One card. Which fields a kind draws (and requires) is fixed, and a field the kind does not
    draw must be empty — so `onscreen_strings` is always exactly what `render_card` draws:

    * brand      — nothing (draws the logo and WORDMARK)
    * text       — title and/or body; optional badge
    * stat       — figure (required), label, body; optional badge
    * disclaimer — body (required; the server's disclaimer card text; draws it + logo + its CTA:
      `cta`, one of END_CARD_CTAS — "" draws LEGACY_CTA, what every disclaimer card drew before
      Batch 2)
    * opening    — a template video's first card (drop 2a, `opening_card`): badge = kicker
      (required), `logos` = 1-2 company logos (a wordmark tile — the company name — for one that
      is not verified), label = chip, figure, body = the figure's label (news-v23, `figure_label`: only beside a
      figure), title = headline (required). Drawn under the brand row (the kicker in its pill), the headline
      two-tone beside the logo disc, the figure on a warm card with its label under it, over the hook.
      news-v23: a 13F LIST cover (the card's `list`) carries `image` — the list as a news_layouts.TemplateImage
      (`news_layouts.cover_image`), drawn by the 13F list placer on the 9:16 frame (cover_13f_berkshire.jpg) and
      declaring exactly its strings.
    * hook       — a LESSON video's first card (Batch 2, the frozen feature `hook_card`): title = the
      accepted hook, VERBATIM (required). Drawn big as the two-tone headline under the brand row,
      shown alone over the narrated hook.
    * data       — a TEMPLATE video's data card (Batch 2, `data_card`): `image` = the day's post
      image, a news_layouts.TemplateImage (required) — drawn in the card's text zone in place of the
      text card the read-back names (`data_card_slot`), declaring exactly its strings
      (`TemplateImage.strings`).

    `art` (workstream C, owner decision 2026-11-15 "Picture and video cover"): an OPENING card with ONE company
    may carry the run's per-post art (an ArtImage) — drawn as a band across the top, edge to edge (OPENING_ART_W ×
    OPENING_ART_H, under a navy scrim the brand row reads on) with the logo disc on its bottom edge, the chip,
    headline and figure under it, and
    ART_LABEL burned on the band (`onscreen_strings` then ends with ART_LABEL). Every other kind draws no art.
    """

    kind: str
    title: str = ""
    body: str = ""
    figure: str = ""
    label: str = ""
    badge: str = ""
    logos: Tuple[LogoArt, ...] = ()
    cta: str = ""
    #: A data card's picture (news_layouts.TemplateImage). Compared, never hashed (it holds dicts).
    image: Any = field(default=None, hash=False)
    #: An opening card's per-post art (an ArtImage), or None — today's opening card.
    art: Any = field(default=None, hash=False)
    #: news-v23: the KICKER in the brand row's pill (a template's opening-card kicker, a lesson's LESSON_KICKER; ""
    #: — no pill) — every kind but the opening card (its `badge` IS its kicker), the brand and the data card.
    pill: str = ""
    #: news-v23: the card's short FOOTER line (a template's image_footer, a lesson's LESSON_FOOTER; "" — none),
    #: under the caption band — every kind but the brand, disclaimer and data card.
    footer: str = ""
    #: news-v23: the figure's colour ("gain" GREEN, "loss" RED, "gold" GOLD_INK, "" neutral) — an opening or stat
    #: card's, from the script's series (look.figure_tone). Draws no string.
    tone: str = ""

    def __post_init__(self) -> None:
        for name in ("kind", "title", "body", "figure", "label", "badge", "cta", "pill", "footer", "tone"):
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
            "opening": ("title", "figure", "label", "badge", "body"),
            "hook": ("title",),
            "data": (),
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
        if self.cta and (self.kind != "disclaimer" or self.cta not in END_CARD_CTAS):
            raise ValueError(f"only a disclaimer card draws a CTA, and only one of {END_CARD_CTAS} "
                             f"(a {self.kind} card with cta {self.cta[:40]!r})")
        if self.kind == "hook" and not _present(self.title):
            raise ValueError("a hook card needs its hook (title)")
        if self.kind == "data" or (self.kind == "opening" and self.image is not None):
            from marketing import news_layouts

            if not isinstance(self.image, news_layouts.TemplateImage):
                raise TypeError(f"a {self.kind} card's picture must be a news_layouts.TemplateImage, "
                                f"not {type(self.image).__name__}")
        elif self.image is not None:
            raise ValueError(f"a {self.kind} card draws no picture")
        if self.kind == "opening":
            if not (_present(self.badge) and _present(self.title)):
                raise ValueError("an opening card needs its kicker (badge) and its headline (title)")
            if _present(self.body) and not _present(self.figure):
                raise ValueError("an opening card's figure label (body) explains a figure: it has none")
            keys = [a.key for a in self.logos]
            if not 1 <= len(keys) <= MAX_OPENING_LOGOS or len(set(keys)) != len(keys):
                raise ValueError(f"an opening card draws 1-{MAX_OPENING_LOGOS} distinct company logos, "
                                 f"not {keys}")
        for name in ("pill", "footer"):
            value = getattr(self, name)
            if value and not drawable_text(value):
                raise ValueError(f"CardSpec.{name} must be one drawable string or empty")
        if self.pill and self.kind in ("opening", "brand", "data"):
            raise ValueError(f"a {self.kind} card draws no kicker pill of its own")
        if self.footer and self.kind in ("brand", "disclaimer", "data"):
            raise ValueError(f"a {self.kind} card draws no footer line")
        if self.tone not in ("", "gain", "loss", "gold"):
            raise ValueError(f"CardSpec.tone {self.tone!r} is not one of '', 'gain', 'loss', 'gold'")
        if self.art is not None:
            if self.kind != "opening":
                raise ValueError(f"a {self.kind} card draws no art (only a template's opening card does)")
            if self.image is not None:
                raise ValueError("a 13F list cover draws no art (several companies: decision 4)")
            if not isinstance(self.art, ArtImage):
                raise TypeError(f"an opening card's art must be an ArtImage, not {type(self.art).__name__}")
            if len(self.logos) != 1:
                raise ValueError("an opening card draws art only for ONE company (decision 4: single-company "
                                 f"records), not {len(self.logos)}")

    @property
    def logo_keys(self) -> Tuple[str, ...]:
        return tuple(a.key for a in self.logos)

    @property
    def end_cta(self) -> str:
        """The CTA a disclaimer card draws (`cta`, else LEGACY_CTA); "" for every other kind."""
        if self.kind != "disclaimer":
            return ""
        return self.cta or LEGACY_CTA


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


def money_map_net_tone(series: Any, image_spec: Any) -> Optional[str]:
    """A Money Map's net-income tone from its post image's spec (the flow's LAST line: "fill" a profit → "gain",
    "outline" a loss → "loss" — the flag news_layouts._lay_bars colours the callout by), so the video cover's "per
    $100" figure is GREEN / RED as on the picture (RESTYLE_SPEC §2.6). None for another series or a spec it cannot
    read (the figure then keeps `look.figure_tone`'s neutral INK)."""
    if series != "money_map" or not isinstance(image_spec, dict):
        return None
    flow = image_spec.get("flow")
    if not isinstance(flow, list) or not flow or not isinstance(flow[-1], dict):
        return None
    return {"fill": "gain", "outline": "loss"}.get(flow[-1].get("style"))


def opening_spec(card: Any, logos: Dict[str, LogoArt], *, footer: str = "", series: Any = None,
                 look: bool = True, image_spec: Any = None) -> CardSpec:
    """The template video's opening CardSpec from the script's `opening_card` (`{kicker, logos[1-2],
    chip?, figure?, headline, list?}`, closed — the server validated it with
    template_onscreen.validate_opening_card): kicker → badge, chip → label, figure → figure,
    headline → title, each VERBATIM. ValueError on anything else (an unknown key, a logo key the
    script carries no entry for, a string that is not drawable) — contract drift, never guessed.

    news-v23: a 13F LIST cover (`list`, OPENING_LIST_KEYS) also carries `image` — the cover as a
    news_layouts.TemplateImage (`news_layouts.cover_image`: the card's kicker and headline, the filer's plate beside a
    chip, the list and the short `footer`), drawn by the 13F list placer (cover_13f_berkshire.jpg). A list without a
    footer to draw (an older web) is ignored with a WARNING: today's opening card."""
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
    for k in ("kicker", "chip", "figure", "figure_label", "headline"):
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
    if texts["figure_label"] and not texts["figure"]:
        raise ValueError("script.opening_card.figure_label explains a figure: the card has none")
    if len(texts["figure_label"]) > OPENING_FIGURE_LABEL_MAX_CHARS:
        raise ValueError(f"script.opening_card.figure_label is over {OPENING_FIGURE_LABEL_MAX_CHARS} characters")
    tone = ""
    if texts["figure"]:
        from marketing import look as look_mod

        tone = (money_map_net_tone(series, image_spec)
                or look_mod.figure_tone(series if isinstance(series, str) else None, texts["figure"]) or "")
    image = None
    if card.get("subtitle") is not None and card.get("costs") is None:
        raise ValueError("script.opening_card.subtitle is drawn only on a costs cover (it has no costs)")
    if card.get("costs") is not None and card.get("list") is not None:
        raise ValueError("script.opening_card carries both a costs card and a list")
    if card.get("list") is not None:
        if drawable_text(footer):
            from marketing import news_layouts

            image = news_layouts.cover_image(card, logos, footer=footer, series=series, look=look,
                                             picture=image_spec)
        else:
            logger.warning("cards: the opening card carries a 13F list but no short footer to draw under it (an older "
                           "web) — today's opening card")
    elif card.get("costs") is not None:
        # owner decision 2026-11-20 (money_map_loss_A_rivian_cover.jpg): a Money Map LOSS year's cover is its costs
        # card — when it cannot be drawn (`_cover_or_plain`), today's figure card from the same opening card
        if drawable_text(footer):
            from marketing import news_layouts

            image = news_layouts.costs_cover_image(card, logos, footer=footer, series=series, look=look)
        else:
            logger.warning("cards: the opening card carries a costs card but no short footer to draw under it (an "
                           "older web) — today's opening card")
    return CardSpec("opening", badge=texts["kicker"], label=texts["chip"], figure=texts["figure"],
                    body=texts["figure_label"], title=texts["headline"], logos=tuple(arts), footer=footer, tone=tone,
                    image=image)


def video_features(script: Any) -> Tuple[str, ...]:
    """The VIDEO_FEATURES the server froze for this run (Batch 2), as the script read-back carries them
    (`video_features`), sorted — the kind's own only: LESSON_VIDEO_FEATURES for a lesson,
    TEMPLATE_VIDEO_FEATURES for a template. () when the read-back carries none (an older web, or a run
    frozen without features: today's video). A value that does not read back — not a list, a non-string,
    a duplicate, an unknown feature or one of the other kind — is logged at ERROR and read as () WHOLE,
    never half-trusted (run_service.frozen_video_features' own rule: a feature only ever adds to the
    video, so the safe reading of a value nobody can explain is today's video). ValueError only for a
    script that is not a dict, or whose authorship is unknown (`is_template`). The worker's ONE reader of
    the field: the render's reuse keys and the push-in read it here too, so what is drawn, what moves and
    what is keyed never disagree."""
    if not isinstance(script, dict):
        raise ValueError(f"script must be a dict, not {type(script).__name__}")
    raw = script.get("video_features")
    if raw is None:
        return ()
    template = is_template(script)
    applicable = TEMPLATE_VIDEO_FEATURES if template else LESSON_VIDEO_FEATURES
    if (not isinstance(raw, list) or not all(isinstance(f, str) for f in raw) or len(set(raw)) != len(raw)
            or not set(raw) <= set(applicable)):
        logger.error("cards: the script's video_features %s do not read back for a %s (allowed %s) — read as "
                     "none: today's video", str(raw)[:80], "template" if template else "lesson", applicable)
        return ()
    return tuple(sorted(raw))


def end_card_cta(script: Any) -> str:
    """The CTA the end (disclaimer) card draws: the script read-back's `end_card_cta` (Batch 2: the web's
    choice — CTA only while the run's accepted output has the app on the store, owner decision 6) when it
    is one of END_CARD_CTAS; LEGACY_CTA when the read-back names none (an older web: it allows only that
    one). Any other value is logged at ERROR and drawn as LEGACY_CTA — both webs allow it, so a bad value
    costs the new CTA, never the day."""
    raw = script.get("end_card_cta") if isinstance(script, dict) else None
    if raw is None:
        return LEGACY_CTA
    if isinstance(raw, str) and raw in END_CARD_CTAS:
        return raw
    logger.error("cards: the script's end_card_cta %r is not one of %s — the end card draws %r",
                 str(raw)[:40], END_CARD_CTAS, LEGACY_CTA)
    return LEGACY_CTA


def data_card_slot(script: Any) -> Optional[int]:
    """The index into the script read-back's `cards` (its own content cards — never the opening or the
    disclaimer card) of the ONE text card the frozen `data_card` replaces: `data_card_slot`, the server's
    per-series choice of a fact card the picture already shows (CARDS-B2-1: the last card is often the
    hedge a template keeps on screen on purpose). None — no data card, today's text cards — when:

    * the script did not freeze `data_card` (`video_features`) — a slot it names anyway is ignored
      (WARNING: the web sends one only with the feature);
    * the read-back names no slot (WARNING: a web that predates the slot or names none — never a guess at
      the last card);
    * the slot is not an index of `cards` — a bool, a non-int, negative, out of range (ERROR).

    ValueError only for a script that is not a dict (or whose authorship is unknown, `video_features`). The
    worker's ONE reader of the field; `cards_for_script` also checks that the card it names is a text card."""
    if not isinstance(script, dict):
        raise ValueError(f"script must be a dict, not {type(script).__name__}")
    raw = script.get("data_card_slot")
    if VIDEO_FEATURE_DATA_CARD not in video_features(script):
        if raw is not None:
            logger.warning("cards: the script names data_card_slot %s without a frozen data_card — ignored",
                           str(raw)[:40])
        return None
    if raw is None:
        logger.warning("cards: data_card is frozen but the read-back names no data_card_slot — no data card: "
                       "today's text cards")
        return None
    entries = script.get("cards")
    count = len(entries) if isinstance(entries, list) else 0
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw < count:
        logger.error("cards: the script's data_card_slot %s is not an index of its %d card(s) — no data card: "
                     "today's text cards", str(raw)[:40], count)
        return None
    return raw


def applied_data_card_slot(specs: Sequence[CardSpec]) -> Optional[int]:
    """The `data_card_slot` the cards APPLIED — the index into the script's `cards` of the text card a data
    card replaced (`specs` start with the template's opening card) — or None when they draw no data card.
    What the render's reuse key and the video's metadata record."""
    index = next((i for i, spec in enumerate(specs) if spec.kind == "data"), None)
    return None if index is None else index - 1


def opens_on_hook_card(specs: Sequence[CardSpec]) -> bool:
    """`video.timeline`'s `hook_card`: is card 0 shown ALONE over the narrated hook — a template's opening
    card, or a lesson's hook card (Batch 2)? (Only a HOOK card shows the hook's own words: that is when
    the hook's burned captions are suppressed — `specs[0].kind == "hook"`.)"""
    return bool(specs) and specs[0].kind in HOOK_CARD_KINDS


def _too_many_strings(specs: Sequence[CardSpec]) -> Optional[str]:
    """Why `specs` would declare more than ONSCREEN_TEXT_MAX distinct on-screen strings (the most one
    video asset may declare — the server refuses more), or None."""
    n = len(dict.fromkeys(t for spec in specs for t in onscreen_strings(spec)))
    return None if n <= ONSCREEN_TEXT_MAX else f"the video would declare {n} on-screen strings (max {ONSCREEN_TEXT_MAX})"


def _feature_card_problem(card: CardSpec, font_path: str, layout_engine: Optional[str]) -> Optional[str]:
    """Why a feature card cannot be drawn — a glyph the face lacks, or it does not fit (legibly, for a data
    card) at the floor — or None. Exactly the checks the render makes, so a card kept here never ends the
    day there. CardAssetError (an unreadable font) is not content: it propagates."""
    try:
        check_glyphs([card], font_path)
        layout_card(card, font_path=font_path, layout_engine=layout_engine)
    except (MissingGlyphs, CardOverflow) as e:
        return f"{type(e).__name__}: {str(e)[:200]}"
    return None


def _with_hook_card(script: Dict[str, Any], specs: List[CardSpec], font_path: str,
                    layout_engine: Optional[str]) -> List[CardSpec]:
    """`specs` opened by the HOOK card (the frozen `hook_card`): the accepted hook, verbatim — or `specs`
    unchanged, with a WARNING, when the hook is not one drawable string, lacks a glyph, cannot fit at the
    floor size or takes the video past ONSCREEN_TEXT_MAX strings (the video opens on its first card)."""
    hook = script.get("hook")
    if not drawable_text(hook):
        logger.warning("cards: hook_card is frozen but the script's hook is not one drawable string — no hook "
                       "card: the video opens on its first card")
        return specs
    card = (CardSpec("hook", title=hook, pill=LESSON_KICKER, footer=LESSON_FOOTER) if new_look(script)
            else CardSpec("hook", title=hook))
    problem = _feature_card_problem(card, font_path, layout_engine) or _too_many_strings([card, *specs])
    if problem is not None:
        logger.warning("cards: no hook card (%s) — the video opens on its first card", problem)
        return specs
    logger.info("cards: hook_card frozen — the video opens on the hook card")
    return [card, *specs]


def _with_data_card(script: Dict[str, Any], table: Dict[str, LogoArt], specs: List[CardSpec], font_path: str,
                    layout_engine: Optional[str]) -> List[CardSpec]:
    """`specs` ([opening, text…, disclaimer]) with the text card the read-back names (`data_card_slot` —
    `cards[slot]`, which is specs[slot + 1]) replaced by the DATA card (the frozen `data_card`): the day's
    post image, drawn in the card's text zone — or `specs` unchanged when there is no usable slot
    (`data_card_slot` logs why), the slot does not name a text card between the opening and the disclaimer
    (ERROR: never the opening card, never the disclaimer), the picture cannot be read from the script (ERROR:
    the server sends data_card only with a drawable picture) or cannot be drawn there (WARNING: a glyph, no
    legible fit, too many strings)."""
    from marketing import news_layouts

    slot = data_card_slot(script)
    if slot is None:
        return specs
    index = slot + 1                         # specs[0] is the opening card
    if not 1 <= index < len(specs) - 1 or specs[index].kind != "text":
        logger.error("cards: data_card_slot %d does not name a text card of this video (%s) — no data card: "
                     "today's text cards", slot, [s.kind for s in specs])
        return specs
    replaced = specs[index].title or specs[index].body
    try:
        image = news_layouts.template_image_for_script(script, table)
    except ValueError as e:
        logger.error("cards: data_card is frozen but the script's post image cannot be read (%s) — no data "
                     "card: the video keeps its text card %d (%r)", str(e)[:200], slot, replaced[:40])
        return specs
    card = CardSpec("data", image=image)
    candidate = [*specs[:index], card, *specs[index + 1:]]
    problem = _feature_card_problem(card, font_path, layout_engine) or _too_many_strings(candidate)
    if problem is not None:
        logger.warning("cards: no data card (%s) — the video keeps its text card %d (%r)", problem, slot,
                       replaced[:40])
        return specs
    logger.info("cards: data_card frozen — the data card (%s picture) replaces text card %d (%r)", image.layout,
                slot, replaced[:40])
    return candidate


def _cover_or_plain(opening: CardSpec, font_path: str, layout_engine: Optional[str]) -> CardSpec:
    """A 13F LIST cover (news-v23, `opening.image`) when it can be drawn — its glyphs, and it fits legibly at the
    floor (the render's own checks) — else today's plain opening card (the kicker, the logo, the chip, the headline:
    a subset of the server's allow-list), with a WARNING: a cover never ends the day. CardAssetError (an unreadable
    font) is not the cover's: it propagates."""
    try:
        check_glyphs([opening], font_path)
        layout_card(opening, font_path=font_path, layout_engine=layout_engine)
    except (MissingGlyphs, CardOverflow) as e:
        logger.warning("cards: the %s cover cannot be drawn (%s: %s) — today's opening card instead",
                       "costs" if opening.image is not None and opening.image.spec.get("costs") is not None
                       else "13F list", type(e).__name__, str(e)[:200])
        return dataclasses.replace(opening, image=None)
    return opening


#: Why per-post art offered to a template's opening card was not drawn (`cards_for_script(..., report=)`): the
#: opening card names two companies (decision 4: single-company records only); a glyph of the label the face
#: lacks; the text does not fit under the band at the floor sizes; the art file cannot be read now; the video
#: would declare more than ONSCREEN_TEXT_MAX strings with the label; the cover is a Money Map loss year's COSTS card
#: (owner decision 2026-11-20 — the server makes no art for one: post_art's `costs_cover`).
OPENING_ART_SKIP_REASONS: Tuple[str, ...] = ("two_companies", "missing_glyphs", "no_fit", "unreadable",
                                             "too_many_strings", "costs_cover")


def _with_opening_art(specs: List[CardSpec], art: "ArtImage", font_path: str, layout_engine: Optional[str],
                      report: Optional[Dict[str, Any]]) -> List[CardSpec]:
    """`specs` ([opening, …]) with the opening card drawing the run's per-post art (workstream C, decision 6: the
    cover) — or `specs` unchanged, with a WARNING naming why (and `report["opening_art_skipped"]`, one of
    OPENING_ART_SKIP_REASONS), when it cannot be: two companies on the card, a label glyph the face lacks, no fit
    under the band at the floor sizes, an unreadable art file, too many strings. Exactly the checks the render
    makes, so an art card kept here never ends the day there; the plain opening card is today's cover. CardAssetError
    for the FONT is not art: it propagates (the render would fail on it either way)."""
    opening = specs[0]
    reason: Optional[str] = None
    detail = ""
    card = opening
    if opening.image is not None and opening.image.spec.get("costs") is not None:
        reason, detail = "costs_cover", "a Money Map loss year's cover is its costs card"
    elif opening.image is not None:
        reason, detail = "two_companies", "a 13F list cover (several companies)"
    elif len(opening.logos) != 1:
        reason, detail = "two_companies", f"{len(opening.logos)} logos on the card"
    else:
        card = CardSpec("opening", badge=opening.badge, label=opening.label, figure=opening.figure,
                        body=opening.body, title=opening.title, logos=opening.logos, art=art, footer=opening.footer,
                        tone=opening.tone)
        try:
            check_glyphs([card], font_path)
        except MissingGlyphs as e:
            reason, detail = "missing_glyphs", str(e)[:200]
        if reason is None:
            try:
                layout_card(card, font_path=font_path, layout_engine=layout_engine)
            except CardOverflow as e:
                reason, detail = "no_fit", str(e)[:200]
        if reason is None:
            try:
                art_tile(art, OPENING_ART_W, OPENING_ART_H)
            except CardAssetError as e:
                reason, detail = "unreadable", str(e)[:200]
        if reason is None:
            too_many = _too_many_strings([card, *specs[1:]])
            if too_many is not None:
                reason, detail = "too_many_strings", too_many
    if reason is not None:
        logger.warning("cards: no art on the opening card (%s: %s) — the plain opening card, today's cover (art "
                       "sha=%s…)", reason, detail, art.sha256[:12])
        if report is not None:
            report["opening_art_skipped"] = reason
        return specs
    logger.info("cards: the opening card draws the per-post art (sha=%s…) — the cover", art.sha256[:12])
    return [card, *specs[1:]]


def cards_for_script(script: Dict[str, Any], logos: Optional[Dict[str, LogoArt]] = None, *,
                     font_path: Optional[str] = None, layout_engine: Optional[str] = None,
                     art: Optional["ArtImage"] = None, report: Optional[Dict[str, Any]] = None) -> List[CardSpec]:
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
    a ValueError: a template video never falls back to the lesson shape.

    Batch 2: the disclaimer card draws the read-back's end card CTA (`end_card_cta`), and the script's
    frozen video features (`video_features`) add their cards — a LESSON with `hook_card` OPENS on the
    hook card (time it with `hook_card=True`: `opens_on_hook_card`); a TEMPLATE with `data_card` draws
    its post image as the DATA card in place of the text card the read-back names (`data_card_slot`; no
    slot, no data card — never the last card by default). Each feature card is checked here with
    the render's own `font_path` / `layout_engine` — its glyphs, that it fits (legibly, for a data card),
    that the video stays within ONSCREEN_TEXT_MAX strings — and one that fails is left out with a
    WARNING: today's card, never a skipped day. `font_path` is REQUIRED when the script froze `hook_card`
    or `data_card` (ValueError: the check is never skipped).

    Workstream C: `art` (an ArtImage — `art.resolve_art`'s verified read-back art, or None) makes a TEMPLATE's
    opening card draw the per-post art (`_with_opening_art`: one company only, checked here with the render's own
    font and engine, the plain opening card with a WARNING — and `report["opening_art_skipped"]` when a dict is
    passed — otherwise; `font_path` is then REQUIRED too). A lesson never draws art: art passed for one is ignored
    with a WARNING."""
    if not isinstance(script, dict):
        raise ValueError(f"script must be a dict, not {type(script).__name__}")
    if art is not None and not isinstance(art, ArtImage):
        raise TypeError(f"art must be an ArtImage or None, not {type(art).__name__}")
    if art is not None and not font_path:
        raise ValueError("cards_for_script was given art: it needs the render's font_path to check that the opening "
                         "card can draw it")
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
    features = video_features(script)
    drawn = [f for f in features if f in (VIDEO_FEATURE_HOOK_CARD, VIDEO_FEATURE_DATA_CARD)]
    if drawn and not font_path:
        raise ValueError(f"the script froze {drawn}: cards_for_script needs the render's font_path to check "
                         "that each feature card can be drawn")
    if layout == VIDEO_LAYOUT_PER_LINE:
        table = logo_table(script) if logos is None else logos
        raw_footer = script.get("image_footer")
        # the short footer on every card only from a news-v23 web (`new_look`): an older one stored the long footer
        # and allows it on no video card (a 422 that loses the day)
        footer = raw_footer if drawable_text(raw_footer) and new_look(script) else ""
        opening = opening_spec(script.get("opening_card"), table, footer=footer, series=script.get("series"),
                               look=new_look(script), image_spec=script.get("image_spec"))
        if opening.image is not None and font_path:
            opening = _cover_or_plain(opening, font_path, layout_engine)
        pill = opening.badge
        end = CardSpec("disclaimer", body=disclaimer, cta=end_card_cta(script), pill=pill)
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
            per_line.append(CardSpec("text", title=title, body=body, pill=pill, footer=footer))
        per_line.append(end)
        if art is not None:
            per_line = _with_opening_art(per_line, art, font_path, layout_engine, report)  # type: ignore[arg-type]
        if VIDEO_FEATURE_DATA_CARD in features:
            per_line = _with_data_card(script, table, per_line, font_path, layout_engine)  # type: ignore[arg-type]
        return per_line
    if art is not None:
        logger.warning("cards: art was given for a lesson script — ignored (only a template's opening card draws art)")
    # the lesson's kicker pill and short footer only from a news-v23 web (`new_look`): an older one refuses both
    pill, footer = (LESSON_KICKER, LESSON_FOOTER) if new_look(script) else ("", "")
    end = CardSpec("disclaimer", body=disclaimer, cta=end_card_cta(script), pill=pill)
    specs: List[CardSpec] = []
    for i, entry in enumerate(raw_cards):
        if not isinstance(entry, dict):
            raise ValueError(f"script.cards[{i}] must be an object, not {type(entry).__name__}")
        title = _script_str(entry.get("title"), f"script.cards[{i}].title").strip()
        body = _script_str(entry.get("body"), f"script.cards[{i}].body").strip()
        if not title and not body:
            logger.warning("script.cards[%d] has neither title nor body — no card drawn for it", i)
            continue
        specs.append(CardSpec("text", title=title, body=body, pill=pill, footer=footer))
    if not specs:
        # Never silent: the writer always emits three cards, so this is a malformed script that
        # still deserves a video (its narration and disclaimer are fine).
        logger.warning("script has no drawable text card — the video opens on the brand card instead")
        specs.append(CardSpec("brand"))
    specs.append(end)
    if VIDEO_FEATURE_HOOK_CARD in features:
        specs = _with_hook_card(script, specs, font_path, layout_engine)  # type: ignore[arg-type]
    return specs


def onscreen_strings(spec: CardSpec) -> List[str]:
    """EXACTLY the strings `render_card` draws for `spec`, verbatim (the server compares them with
    the accepted script). A whitespace-only field is neither drawn nor returned.

    An opening card returns them in template_onscreen.opening_strings' order — kicker, the company
    name of each logo drawn as a WORDMARK (a verified logo draws no string), chip, figure, the figure's
    label (news-v23), headline — so it is always a subset of the server's allow-list, and equal to it when no logo
    verified. A hook card returns the hook; a data card its picture's `TemplateImage.strings` (kicker …
    footer, each wordmark's company name at its logo's place — a subset of the server's
    template_onscreen.image_strings); a disclaimer card its text and its CTA (`end_cta`). An opening card WITH
    art (workstream C) also returns ART_LABEL, last — the server allows it only beside the video's
    `opening_art_sha256` declaration (template_onscreen.art_allowed)."""
    if spec.kind == "brand":
        fields = [WORDMARK]
    elif spec.kind == "text":
        fields = [spec.title, spec.body, spec.badge]
    elif spec.kind == "stat":
        fields = [spec.figure, spec.label, spec.body, spec.badge]
    elif spec.kind == "opening" and spec.image is not None:
        # news-v23: a 13F list cover declares its picture's strings (kicker … footer; each wordmark's name)
        fields = list(spec.image.strings)
    elif spec.kind == "opening":
        fields = [spec.badge, *[a.name for a in spec.logos if a.wordmark], spec.label, spec.figure, spec.body,
                  spec.title]
    elif spec.kind == "hook":
        fields = [spec.title]
    elif spec.kind == "data":
        fields = list(spec.image.strings)
    else:  # disclaimer
        fields = [spec.body, spec.end_cta]
    # news-v23: the brand row's word (every card but the brand card, which draws WORDMARK — the same string), its
    # kicker pill and the short footer line
    fields += [spec.pill, spec.footer, BRAND_ROW_WORD]
    if spec.kind == "opening" and spec.art is not None:
        fields.append(ART_LABEL)
    return list(dict.fromkeys(s for s in fields if _present(s)))


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
    """`fg` at `alpha` composited over `bg`."""
    f, b = _rgb(fg), _rgb(bg)
    return tuple(int(round(bv + (fv - bv) * alpha)) for fv, bv in zip(f, b))  # type: ignore[return-value]


#: Drop 2a: the WHITE disc a company logo sits on (news-v23: or the DARK one, look.NAVY_CHIP, for a logo made for
#: a dark background — logos.disc_tone; logos.PLATE_RGB mirrors this), and a wordmark's ink on it (look.INK).
PLATE = TEXT
LOGO_INK = "#111827"
#: The ART_LABEL pill (workstream C; news-v23 look): NAVY_CHIP, opaque, under white ink — legible over any art.
ART_LABEL_FILL = _rgb("#111827")
ART_LABEL_INK = _rgb(TEXT)

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
    #: Per-post art (workstream C, an opening card only): the art band's box and the ART_LABEL pill's box (its
    #: text is in `lines`, field "art_label"). None on every card that draws no art.
    art_box: Optional[Box] = None
    label_box: Optional[Box] = None
    #: news-v23 (marketing/card_layouts.py): what the look draws besides text — cards, pills, rules (look.Rect),
    #: dots and rings (look.Ellipse), the logo discs (look.Disc), the brand row's tile and kicker pill, the footer's
    #: top.
    rects: Tuple[Any, ...] = ()
    ellipses: Tuple[Any, ...] = ()
    discs: Tuple[Any, ...] = ()
    stacks: Tuple[Any, ...] = ()
    brand: Optional[Box] = None
    pill: Optional[Box] = None
    footer_top: int = 0

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


def _check_length(spec: CardSpec) -> None:
    for s in onscreen_strings(spec):
        if len(s) > MAX_STRING_CHARS:
            raise CardOverflow(f"{spec.kind} card: a string of {len(s)} characters is over "
                               f"{MAX_STRING_CHARS} (the server refuses it): {s[:60]!r}…")


def _within(inner: Box, outer: Box) -> bool:
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _place(spec: CardSpec, step: int, font_path: str, engine: str, with_logo: bool) -> CardLayout:
    """`spec` at ramp `step`, in the approved look (news-v23: marketing/card_layouts.place_card)."""
    from marketing import card_layouts

    return card_layouts.place_card(spec, step, font_path, engine, with_logo)


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def art_label_size(font_path: str, engine: str) -> Tuple[int, int]:
    """(width, height) of the ART_LABEL pill: the label at ART_LABEL_PX — fixed, never wrapped, never stepped
    down — plus ART_LABEL_PAD_X a side; ART_LABEL_PX + 2 × ART_LABEL_PAD_Y tall (mock_spotlight.ai_pill)."""
    font = _font(font_path, ART_LABEL_PX, engine)
    return int(math.ceil(font.getlength(ART_LABEL))) + 2 * ART_LABEL_PAD_X, ART_LABEL_PX + 2 * ART_LABEL_PAD_Y


def art_label_line(box: Box, font_path: str, engine: str) -> DrawnLine:
    """ART_LABEL laid out in its pill `box` (left-aligned on the pad, its baseline ART_LABEL_BASE_UP above the pill's
    bottom — mock_spotlight.ai_pill — ART_LABEL_INK). _NoFit when its ink would leave the pill (defence in depth:
    the pill is sized from the same font)."""
    font = _font(font_path, ART_LABEL_PX, engine)
    x, y = box[0] + ART_LABEL_PAD_X, box[3] - ART_LABEL_BASE_UP
    l, t, r, b = font.getbbox(ART_LABEL, anchor="ls")
    ink = (x + l, y + t, x + r, y + b)
    if not _within(ink, box):
        raise _NoFit("art label: its ink leaves its pill")
    return DrawnLine("art_label", ART_LABEL, ART_LABEL_PX, x, y, "ls", ART_LABEL_INK, ink)


def art_tile(art: ArtImage, w: int, h: int):
    """The art as a w×h RGB PIL image: the verified file opened (its size must be the one it was verified at),
    centre-CROPPED to the w:h aspect (never stretched, never letter-boxed) and resized with LANCZOS — deterministic
    for the same file. CardAssetError when the file is unreadable now."""
    from PIL import Image

    if not isinstance(art, ArtImage):
        raise TypeError(f"art_tile needs an ArtImage, not {type(art).__name__}")
    try:
        with Image.open(art.path) as src:
            if src.size != (art.width, art.height):
                raise CardAssetError(f"art {art.sha256[:12]}: the file is {src.size[0]}x{src.size[1]}, verified at "
                                     f"{art.width}x{art.height}")
            src.load()
            rgb = src.convert("RGB")
    except CardAssetError:
        raise
    except Exception as e:  # noqa: BLE001 — re-raised typed, with the sha (never the path's bytes)
        raise CardAssetError(f"art {art.sha256[:12]} unreadable: {type(e).__name__}: {e}") from e
    sw, sh = rgb.size
    if sw * h > w * sh:                              # wider than the box: crop the sides
        cw = max(1, int(round(sh * w / h)))
        x0 = (sw - cw) // 2
        crop = (x0, 0, x0 + cw, sh)
    else:                                            # taller (or equal): crop the top and the bottom
        ch = max(1, int(round(sw * h / w)))
        y0 = (sh - ch) // 2
        crop = (0, y0, sw, y0 + ch)
    return rgb.resize((w, h), Image.LANCZOS, box=crop)


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
    did not fit legibly, and no ticker stood in — `look.Page.disc(ticker=…)`, field "ticker:<key>")
    — logged by the callers once the layout is final."""
    drawn = {ln.field for ln in getattr(layout, "lines", ())}
    return [art.key for _box, art in getattr(layout, "tiles", ()) if art.wordmark
            and f"wordmark:{art.key}" not in drawn and f"ticker:{art.key}" not in drawn]


def layout_card(spec: CardSpec, *, font_path: str, with_logo: bool = True,
                layout_engine: Optional[str] = None) -> CardLayout:
    """Where everything on the card goes: the largest ramp step at which every element fits.
    CardOverflow (with the floor step's reason) when none does."""
    engine = resolve_layout_engine(layout_engine)
    _check_length(spec)
    if spec.kind == "data" or (spec.kind == "opening" and spec.image is not None):
        # The day's post image in the card's text zone (a data card), or a 13F list cover (news-v23) — its own
        # placers, with a legibility floor (news_layouts.layout_data_card; a news_layouts.TemplateLayout, CardOverflow
        # when not legible).
        from marketing import news_layouts

        return news_layouts.layout_data_card(spec.image, font_path=font_path, layout_engine=engine,  # type: ignore[return-value]
                                             cover=spec.kind == "opening")
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
    uses_logo = spec.kind == "brand"
    if uses_logo and not logo_path:
        _warn_once("no_logo", "cards: no logo path — the brand card renders without its big logo tile")
    if spec.kind == "data" or (spec.kind == "opening" and spec.image is not None):
        # The day's post image in the card's text zone, or a 13F list cover (news_layouts draws it: its own panel,
        # rows, plates).
        from marketing import news_layouts

        _check_length(spec)
        img, data_layout = news_layouts.draw_data_card(spec.image, font_path=font_path,
                                                       layout_engine=resolve_layout_engine(layout_engine),
                                                       cover=spec.kind == "opening")
        _warn_blank(spec, data_layout)
        return _png_bytes(img, spec.kind, data_layout)
    layout = layout_card(spec, font_path=font_path, with_logo=bool(uses_logo and logo_path),
                         layout_engine=layout_engine)
    _warn_blank(spec, layout)
    from marketing import card_layouts

    img = card_layouts.draw_card(layout, spec, font_path, logo_path)
    return _png_bytes(img, spec.kind, layout)


def _warn_blank(spec: CardSpec, layout: Any) -> None:
    blank = blank_wordmarks(layout)
    if blank:
        logger.warning("cards: the %s card draws a blank plate for %s (the name does not fit legibly)",
                       spec.kind, blank)


def _png_bytes(img, kind: str, layout: Any) -> bytes:
    """A card's deterministic PNG: RGB, no metadata chunks, a fixed zlib level."""
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=_PNG_COMPRESS_LEVEL, optimize=False)
    logger.debug("card rendered kind=%s step=%d engine=%s lines=%d bytes=%d",
                 kind, layout.step, layout.engine, len(layout.lines), buf.tell())
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

#: The lesson picture (news-v23 look, marketing/card_layouts.place_lesson_image): the title two-tone, each
#: paragraph on its own warm card, every card the same height (mock_lesson: paragraphs from 46 px, leading 1.28).
#: Sized so the writer's maximum (a 70-character title, four 220-character paragraphs of long words) fits before
#: the floor (tests/test_marketing_cards.py pins that it fits).
IMAGE_TITLE_STYLE = _Style(80, 44, 1.08)
IMAGE_PARAGRAPH_STYLE = _Style(46, 24, 1.28)


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
    #: news-v23 — the read-back's web allows the look's own strings (`new_look`): the brand row's word and the
    #: LESSON_KICKER pill are drawn and declared. False (an older web refuses both on a picture): the tile alone.
    look: bool = True

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
        return ImageSpec(title=raw.get("title"), paragraphs=tuple(paragraphs), footer=footer,  # type: ignore[arg-type]
                         look=new_look(script))
    except TypeError as e:
        raise ValueError(f"script.image_post is malformed: {e}") from e


def image_onscreen_strings(spec: ImageSpec) -> List[str]:
    """EXACTLY the strings the post image draws, verbatim and in order — title, paragraphs, footer, then (news-v23,
    `ImageSpec.look`) the kicker pill's LESSON_KICKER and the brand row's BRAND_ROW_WORD (a repeated string once):
    the image's `metadata.onscreen_text` (the server allows the last two: app.schemas.marketing.LESSON_BRAND_TEXT)."""
    out: List[str] = []
    for s in (spec.title, *spec.paragraphs, spec.footer, *((LESSON_KICKER, BRAND_ROW_WORD) if spec.look else ())):
        if s not in out:
            out.append(s)
    return out


def check_image_glyphs(spec: ImageSpec, font_path: str) -> None:
    """MissingGlyphs naming every character of the image's strings the face lacks."""
    check_glyphs([], font_path, extra=image_onscreen_strings(spec))


def _place_image(spec: ImageSpec, step: int, font_path: str, engine: str) -> CardLayout:
    """The lesson picture at ramp `step`, in the approved look (news-v23: card_layouts.place_lesson_image)."""
    from marketing import card_layouts

    return card_layouts.place_lesson_image(spec, step, font_path, engine)


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
    from marketing import card_layouts

    layout = layout_image(spec, font_path=font_path, layout_engine=layout_engine)
    return card_layouts.draw_lesson_image(layout, font_path), layout


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
    #: Workstream C: why the per-post art OFFERED to this picture was not drawn (news_layouts.ART_SKIP_REASONS:
    #: the picture fell back to today's drawing), or None — no art offered, or the art drawn.
    art_skipped: Optional[str] = None


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
