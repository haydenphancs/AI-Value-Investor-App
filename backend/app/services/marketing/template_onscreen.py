"""
The closed on-screen allow-list of a TEMPLATE (news) post — drop 2, contract D9 (2026-10-09).

A news day's image and its video's opening card are drawn by the worker from two code-owned
structures the templates compose (`news_templates.compose`):

* `image_spec` — the 1080×1350 post image: one of the generic LAYOUTS (`rows`, `spotlight`,
  `pair`, `bars`, `grid`, `feature`) plus the common `{layout, version, kicker, footer}`;
* `opening_card` — the video's first card: `{kicker, logos[1..2], chip?, figure?, headline}`; the
  chip (a ticker, a stake's basis and date, or — Batch 1, 2026-10-15 — the period its figure is for)
  is at most OPENING_CHIP_MAX_CHARS long.

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
news-v23 (owner 2026-11-19, "public only"): company_stakes draws a `rows` LIST of one investor's stakes in
public companies (each row's basis word is its `label`, ROW_KEYS); no series writes a `pair` any more — the
layout still validates (a stored output of an older version reads back the same).

Batch 2 fix (CARDS-B2-1, 2026-10-20): which content card a video's DATA CARD (the post image drawn
in the video) replaces — `data_card_slot`, from the per-series table DATA_CARD_SLOTS — so the
picture never covers a hedge, caveat, explainer or source card (DATA_CARD_KEEPS).

The `feature` layout (2026-11-15, owner decisions of the second daily post — the spotlight series'
picture): an ART BAND at the top of the card (the kicker pill and the company's logo plate; workstream C
puts the per-post art into it), then the company name + chip, a big figure, its label, the headline and
1..MAX_FACTS `{label, value}` fact rows (FACT_KEYS). Only a worker that declared the `layout_feature`
capability draws it (`schemas.marketing.WORKER_LAYOUTS_FEATURE`; `script_service` drops a feature series
from the chain of any other holder, and `run_service` refuses a feature picture registered by one). It is
never a video DATA CARD: DATA_CARD_LAYOUTS leaves it out, so `run_service.data_card_strings` allows
nothing for it (no `data_card` is frozen) and the worker keeps the text card — its facts carry the
filing's cost per share, which owner decision 5 keeps off every video surface.

Per-post ART (workstream C, owner decisions 2026-11-15): a picture carries no text, so what the server checks for
art is a DECLARATION — the sha256 of the art a surface drew (the post image's `art_sha256`, the video's
`opening_art_sha256`) — plus one code-owned string, ART_LABEL, burned onto the art. `art_declaration_problem` is the
rule (the declared sha must be the accepted output's art, the label and the declaration come together, art is
never required) and `art_allowed` adds ART_LABEL to a surface's allow-list only when it declared art.
`image_strings`, `opening_strings` and `data_card_strings` are unchanged: the data card never draws art.

Pure: stdlib + `compliance.clean` + the on-screen caps of `schemas.marketing` (and, at call time
only, `news_templates.LEXICON` for the slot table's card titles). No FMP, no I/O.
"""

from __future__ import annotations

import functools
import logging
import math
import re
from typing import Any, Collection, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from app.schemas.marketing import ONSCREEN_TEXT_MAX, ONSCREEN_TEXT_MAX_CHARS
from app.services.marketing.compliance import clean

logger = logging.getLogger(__name__)

#: The generic layouts (contract D9, plus `feature` since 2026-11-15); the worker mirrors this tuple.
LAYOUTS: Tuple[str, ...] = ("rows", "spotlight", "pair", "bars", "grid", "feature", "ranked", "figures", "ledger",
                            "tally")
#: What validates (the worker mirrors it as `cards.SHIPPED_LAYOUTS`, same members, same order). Drop
#: 2a shipped `rows`, `spotlight`, `bars`; drop 2b (2026-10-10) ships `pair` and `grid` with their
#: series (company_stakes, theme_explainer); 2026-11-15 ships `feature` (the spotlight series' art-band
#: picture — a series using it still runs only for a `layout_feature` holder, `script_service`). A layout
#: dropped from here answers `layout_not_shipped`.
SHIPPED_LAYOUTS: Tuple[str, ...] = ("rows", "spotlight", "pair", "bars", "grid", "feature", "ranked", "figures",
                                    "ledger", "tally")
#: The layouts a template video may draw as its DATA CARD (the post image in the card's text zone, Batch 2).
#: `feature` is not one: its art band is sized for the 4:5 page, and its fact rows carry the filing's cost per
#: share, which never reaches a video (owner decision 5, 2026-11-15). The worker mirrors it
#: (`cards.DATA_CARD_LAYOUTS`) and refuses a data card of any other layout (the text card stays).
DATA_CARD_LAYOUTS: Tuple[str, ...] = ("rows", "spotlight", "pair", "bars", "grid")
SPEC_VERSION = 1

MAX_LOGOS = 12          # distinct logo keys one image may reference
MAX_ROWS = 8            # rows across every section of a `rows` image
MAX_SECTIONS = 3
#: news-v23 (the 13F list, owner 2026-11-19 "the range from 2 to 6 are okay"): ONE section of up to six rows.
MAX_SECTION_ROWS = 6
MAX_CELLS = 3
#: news-v23 — the earnings TABLE (earnings_multi4.jpg / earnings_multi2.jpg; owner 2026-11-19: "add Revenue reported and
#: estimate too" + column order "Estimate, Reported, Surprise"): a `rows` row may carry `table` — 1..MAX_TABLE_LINES
#: lines of exactly TABLE_CELLS cells each (a label, then one cell per column) — and a section whose rows carry one
#: names its `columns` (exactly TABLE_CELLS − 1 labels, drawn over the value columns). Every row of a section with
#: `columns` carries a table, and only such a section's rows may (TABLE_KEYS). The worker mirrors the three
#: (marketing/cards.py, pinned equal).
TABLE_CELLS = 4
MAX_TABLE_LINES = 2
TABLE_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("cells",), ())
MAX_NOTES = 2
MAX_LINES = 2
MIN_BARS, MAX_BARS = 2, 7
MIN_FLOW, MAX_FLOW = 2, 4
MIN_TILES, MAX_TILES = 3, 12
#: news-v23 phase 3 (the weekly theme post, angle 2 "who's biggest" — theme_biggest_power.jpg): a `ranked` picture's
#: rows (`{rank, logo, chip?, name, value, ratio}`, RANKED_ROW_KEYS): 4..10, ranked 1..n in order (each row's `rank` is
#: exactly its position, "1" first), its `ratio` the bar's length in [0, 1] (never drawn). Only a worker that declared
#: WORKER_CAPABILITY_THEME_ANGLES draws it (`schemas.marketing.layout_capabilities`).
MIN_RANKED_ROWS, MAX_RANKED_ROWS = 4, 10
RANKED_ROW_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("rank", "logo", "name", "value", "ratio"), ("chip",))
#: news-v23 phase 3: the theme picture's quiet DECORATION (a text-free gold drawing in the top-right glow,
#: theme_family.py), by token — `grid` and `ranked` specs may name one (`icon`, never drawn as text). The worker mirrors
#: the tokens (marketing/cards.THEME_ICONS, pinned equal) and draws each from the mock's own drawing.
THEME_ICONS: Tuple[str, ...] = ("chip", "bolt", "wafer", "shield", "radar")
#: news-v23 phase 3, the theme post's last three angles (2026-11-20) — each a table of company rows in the theme frame,
#: drawn only by a WORKER_CAPABILITY_THEME_ANGLES worker (`schemas.marketing.WORKER_LAYOUTS_THEME`):
#: * `figures` — angle 3 "growth + profit" (theme_growth_profit_silicon.jpg): two column `heads` (each 1..MAX_HEAD_LINES
#:   lines: "Revenue growth" / "Profit per $100" "of revenue"), rows `{logo, chip?, name, growth, ratio, profit}` — the
#:   growth figure ("+114%", "−5%", "0%": GROWTH_FIGURE_RE) beside a bar (`ratio`, never drawn) and the profit per
#:   $100 ("$56", "−$17", "$0.40": PROFIT_FIGURE_RE), each GREEN or RED by its sign (U+2212 = red);
#: * `ledger` — angle 4 "insider scorecard" (theme_insiders_cyber.jpg): two column `heads` ("Disclosed" "purchases",
#:   "Disclosed" "sales" — drawn on solid GREEN / RED pills), rows `{logo, chip?, name, buys, sells}`, each amount
#:   ("$2.1M", LEDGER_AMOUNT_RE) or EMPTY_CELL ("—": no filing — a MARKER the worker leaves blank, never an allowed
#:   string; visual-fidelity round 2026-11-20);
#: * `tally` — angle 5 "Congress" (theme_congress_defense.jpg): the `unit` ("members") under each row's `count` (2..535
#:   distinct members, GREEN), rows `{logo, chip?, name, count}`.
#: Each may carry a `note` (the notes panel) and "+N more"; rows MIN_THEME_TABLE_ROWS..its layout's maximum; the same
#: rows (1..MAX_OPENING_ROWS) are the video cover's list (`opening_card.list.layout`).
THEME_TABLE_LAYOUTS: Tuple[str, ...] = ("figures", "ledger", "tally")
MIN_THEME_TABLE_ROWS = 4
MAX_THEME_TABLE_ROWS: Dict[str, int] = {"figures": 10, "ledger": 10, "tally": 6}
THEME_HEADS, MAX_HEAD_LINES = 2, 2
THEME_TABLE_ROW_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "figures": (("logo", "name", "growth", "ratio", "profit"), ("chip",)),
    "ledger": (("logo", "name", "buys", "sells"), ("chip",)),
    "tally": (("logo", "name", "count"), ("chip",)),
}
#: The figures' shapes (the worker colours each by its sign, so a figure is never free text): growth a whole percent
#: with its sign (U+2212 minus), profit per $100 whole dollars or, under $1, cents; an insider amount the image money
#: form or the empty cell; a member count digits (2..535).
MINUS = "−"
EMPTY_CELL = "—"
GROWTH_FIGURE_RE = re.compile(r"(?:\+|−)[1-9][0-9]{0,2}(?:,[0-9]{3})*%|0%")
PROFIT_FIGURE_RE = re.compile(r"−?\$(?:[1-9][0-9]{0,2}(?:,[0-9]{3})*|0\.(?:0[1-9]|[1-9][0-9]))|\$0")
LEDGER_AMOUNT_RE = re.compile(r"\$[1-9][0-9]{0,2}(?:\.[0-9])?[KMBT]?|—")
TALLY_COUNT_RE = re.compile(r"[1-9][0-9]{0,2}")
#: A `feature` image's fact rows (`facts`: each `{label, value}`, FACT_KEYS), drawn under its headline.
MIN_FACTS, MAX_FACTS = 1, 4
MAX_OPENING_LOGOS = 2
#: The opening card's chip (under the logo, LABEL size) is at most this long (Batch 1, 2026-10-15): a
#: ticker, a stake's "Committed up to · as of Nov 18, 2025" (36, the longest a template writes) or a
#: period ("Quarter ended Sep 30, 2026", "Reported Oct 14, 2026", "Fiscal 2025"). The worker lays out
#: whatever it is given (a longer chip wraps and can push the cover to CardOverflow, a skipped day),
#: so the bound is the SERVER's: the widest chip of each kind is laid out with the worker's own code
#: in tests/test_marketing_news_layouts.py. Server-side only — the worker never receives a longer one.
OPENING_CHIP_MAX_CHARS = 36
#: A logo key is a canonical symbol ("BRK-B"); never drawn, so only bounded here.
LOGO_KEY_MAX_CHARS = 16
#: news-v23 — a `rows` row's optional ticker CHIP (mock_form4.wide_row_card: a navy chip over the row's company
#: name): at most this long — a ticker, never a phrase (the worker's marketing/cards.ROW_CHIP_MAX_CHARS, pinned
#: equal; tests/test_marketing_news_layouts.py lays the widest one out with the worker's own code).
ROW_CHIP_MAX_CHARS = 10
#: news-v23 — a `rows` row's keys, (required, optional): its cells, its logo, its ticker chip and — company stakes
#: (stakes_nvidia.jpg, owner 2026-11-19) — its `label`, the small basis word drawn over the row's figure ("invested",
#: "committed up to", "carrying value", "fair value"), at most ROW_LABEL_MAX_CHARS long. An earnings row's `table` is
#: required under a section with `columns` (TABLE_KEYS). The worker mirrors both (marketing/cards.py, pinned equal).
#: Owner decisions 2026-11-20 (the insider list, insider_multi3.jpg / insider_multi5.jpg): a row's optional `detail`, the
#: ONE small grey line under its name and roles — "28K shares · $71.20–$71.80 a share, as filed" — at most
#: ROW_DETAIL_MAX_CHARS long (a four-digit price range and "about 1.2M shares" fit; the worker lays the widest out).
#: Drawn after the row's cells (and its label); never on a list COVER (the cost per share never reaches the video:
#: `news_templates` never writes it there, and `_validate_cost_placement` stands behind it).
ROW_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("cells",), ("logo", "chip", "label", "detail"))
ROW_LABEL_MAX_CHARS = 16
ROW_DETAIL_MAX_CHARS = 64
BAR_STYLES: Tuple[str, ...] = ("fill", "outline")
#: Owner decision 2026-11-20 — the Money Map LOSS year ("for companies who are loss, it's a bit hard to understand … a
#: cost or loss will have a longer red bar than the revenue", and his choice "A: costs vs revenue (Recommended)";
#: money_map_loss_A_rivian.jpg / money_map_loss_A_mixed.jpg / money_map_loss_A_rivian_cover.jpg): a `bars` spec and a
#: Money Map opening card may carry `costs`, the COSTS CARD drawn in place of the flow's gross / operating / net steps
#: (and, on the cover, in place of the "per $100" figure): (required, optional) — `revenue` (the navy bar: its label,
#: value and ratio), `net` (the net loss over the gap past revenue: its label and value), `total` (the longer red
#: "Costs and expenses" bar: its label, value and the ratio where its SOLID red ends), `parts` (none — the statement
#: has no gross or operating line — or COSTS_PARTS of them, in bar order: cost of sales, operating expenses, and the
#: interest, tax and other items when they were a COST; each label, value, ratio), the optional `note` (interest, tax
#: and other items were INCOME: "+$110M … made the loss smaller", drawn beside the hatched end of the bar — only beside
#: parts) and the two-line `callout` ("For every $100 of revenue, costs were $166.38," / "so the net loss was $66.38").
#: Every ratio is on ONE scale (the longest bar = 1), a finite number in [0, 1], never drawn; the revenue bar is never
#: longer than the costs bar (a LOSS year). The worker mirrors all five (marketing/cards.py, pinned equal); it draws
#: the card only for a WORKER_CAPABILITY_LOOK_V23 holder (`script_service` strips `costs` from any other — that worker
#: draws today's flow card, a subset of these strings).
COSTS_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("revenue", "net", "total", "parts", "callout"), ("note",))
COSTS_BAR_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("label", "value", "ratio"), ())
COSTS_NET_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("label", "value"), ())
COSTS_PARTS: Tuple[int, ...] = (0, 2, 3)
COSTS_CALLOUT_LINES = 2

#: Closed keys: (required, optional). An optional key may be absent or null.
COMMON_KEYS: Tuple[str, ...] = ("layout", "version", "kicker", "footer")
LAYOUT_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "rows": (("title", "sections"), ("header", "subtitle", "notes")),
    "spotlight": (("header", "figure", "headline"), ("lines",)),
    "pair": (("left", "right", "figure", "label"), ("lines",)),
    # owner decision 2026-11-20: a loss year's COSTS card (COSTS_KEYS), beside the flow the older worker still draws
    "bars": (("header", "title", "subtitle", "segments", "flow", "callout"), ("costs",)),
    # news-v23 phase 3 (the weekly theme post): a grid may name its `icon` and the word its sub line breaks before
    # (`sub_break`, theme_ai_chips.jpg's hand-set break) — neither drawn as text
    "grid": (("title", "subtitle", "tiles"), ("more", "icon", "sub_break")),
    "feature": (("header", "figure", "figure_label", "headline", "facts"), ()),
    "ranked": (("title", "subtitle", "rows"), ("more", "icon")),
    # news-v23 phase 3 (2026-11-20): the theme's last three angles (THEME_TABLE_LAYOUTS)
    "figures": (("title", "subtitle", "heads", "rows"), ("more", "icon", "note")),
    "ledger": (("title", "subtitle", "heads", "rows"), ("more", "icon", "note")),
    "tally": (("title", "subtitle", "unit", "rows"), ("more", "icon", "note")),
}
OPENING_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("kicker", "logos", "headline"), ("chip", "figure", "list",
                                                                                "figure_label", "subtitle", "costs"))
#: Owner decision 2026-11-20 (money_map_loss_A_rivian_cover.jpg): a Money Map LOSS year's cover is its COSTS card — the
#: opening card's optional `costs` (COSTS_KEYS, the picture's own) and the `subtitle` drawn under its headline (only
#: beside `costs`, never beside a `list`). It keeps its `figure` and `figure_label`: a worker that cannot fit the costs
#: cover draws today's figure card from them (`cards._cover_or_plain`).
#: news-v23 (2026-11-20, the Money Map cover — it read backwards: "OF EVERY $100 OF COSTCO REVENUE WAS NET INCOME" over
#: "$2.94"): the opening card's optional `figure_label`, the words drawn UNDER the figure inside its warm card ("of every
#: $100 of revenue was net income") — only beside a `figure`, at most OPENING_FIGURE_LABEL_MAX_CHARS long. A worker that
#: cannot draw it (no WORKER_CAPABILITY_LOOK_V23) is sent the card without the label AND without the figure the label
#: explains (`script_service._without_figure_label`): the headline, the logo and the chip alone.
OPENING_FIGURE_LABEL_MAX_CHARS = 60
#: news-v23 — the 13F LIST cover (cover_13f_berkshire.jpg; owner 2026-11-19): the opening card's optional `list`,
#: (required, optional) — the column label, 1..MAX_OPENING_ROWS rows exactly like a `rows` row (`{cells, logo?,
#: chip?}`) and the "+N more changes in the caption" line. A worker that draws it shows the card's kicker, headline and
#: (beside a chip) its first logo as the filer's plate above the list; one that cannot is sent the card without it
#: (`script_service.worker_script`: a holder without `look_v23`) and draws today's opening card.
#: news-v23 (earnings and Congress Count, the same cover idea — RESTYLE_SPEC §1 "apply the same idea to every template
#: video"): the column label `heading` is optional (a Congress list has none), and an earnings cover names its table's
#: `columns` (its rows each carry a `table`, TABLE_KEYS — the picture's own rule).
OPENING_LIST_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("rows",), ("heading", "columns", "more", "layout",
                                                                         "heads", "unit", "note"))
#: Owner decisions 2026-11-20 (the Form 4 lists' cover "All rows, compact (Recommended)",
#: insider_multi5_cover_alt_all5.jpg): five rows — every company a Form 4 picture draws. Every other list cover caps
#: itself lower (the 13F's three, stakes' three, the theme grid's four, earnings' four).
MAX_OPENING_ROWS = 5
#: news-v23 phase 3 (2026-11-20): a theme TABLE's cover (`list.layout`, THEME_TABLE_LAYOUTS) shows up to this many of
#: the picture's rows — six fit the 9:16 card, and the companies the cover leaves out are the ones every video caption
#: must list (its "+N more" promise): four left a 16-company theme's TikTok caption twelve rows to list, past its budget.
MAX_OPENING_TABLE_ROWS = 6
#: A `feature` fact row (2026-11-15), (required, optional): a short label ("Shares", "Cost per share, as
#: filed") and its value ("14K", "$142.10–$143.85"), drawn on one row (the value right-aligned) or stacked.
FACT_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("label", "value"), ())
#: A `pair`'s two sides (drop 2b, company_stakes), (required, optional): the investor is a listed
#: company and always references its logo entry; the investee may be private (no symbol, so no logo
#: entry) — then the worker draws its `name` as a wordmark plate.
PAIR_SIDE_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "left": (("logo", "name"), ()),
    "right": (("name",), ("logo",)),
}
#: A `grid` tile (drop 2b, theme_explainer), (required, optional): the member's logo, its company
#: name, and an optional one-line caption (e.g. its largest segment and that segment's share).
GRID_TILE_KEYS: Tuple[Tuple[str, ...], Tuple[str, ...]] = (("logo", "name"), ("chip", "line"))

#: The code-owned label burned onto per-post art, on the `feature` picture's band and on the video's opening card
#: (owner decision 2026-11-15, the art disclosure). Never names a model or a provider (CLAUDE.md #7); no hype word,
#: no emoji. The worker mirrors it (`marketing/cards.py`, pinned equal).
ART_LABEL = "AI illustration"
#: A content hash as an art declaration carries it: exactly 64 lower-case hex characters.
SHA256_RE = re.compile(r"[0-9a-f]{64}")
#: Every answer `art_declaration_problem` can give (None aside), in the order it checks them.
ART_DECLARATION_PROBLEMS: Tuple[str, ...] = (
    "art_declaration_invalid", "art_not_accepted", "art_mismatch", "art_label_missing", "art_label_without_art",
)

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
    """A `{logo, name, chip?}` header (chip=False: no chip allowed): the logo tile, then its name, then its chip."""
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
        sec = w.obj(section, path, ("rows",), ("heading", "columns", "more"))
        w.opt_text(sec, "heading", path)
        tabled = _columns(w, sec, path)
        rows = w.seq(sec["rows"], f"{path}.rows", 1, MAX_SECTION_ROWS)
        total += len(rows)
        if total > MAX_ROWS:
            _fail("too_many_rows", "sections", f"> {MAX_ROWS}")
        for j, raw in enumerate(rows):
            _row(w, raw, f"{path}.rows[{j}]", table=tabled)
        w.opt_text(sec, "more", path)
    _lines(w, spec, "notes", MAX_NOTES)


def _columns(w: _Walk, sec: Mapping[str, Any], path: str) -> bool:
    """A section's (or a list cover's) `columns` — exactly TABLE_CELLS − 1 drawable labels — walked; True when it has
    them (its every row then carries a `table`, `_row`)."""
    if sec.get("columns") is None:
        return False
    cols = w.seq(sec["columns"], f"{path}.columns", TABLE_CELLS - 1, TABLE_CELLS - 1)
    for k, label in enumerate(cols):
        w.text(label, f"{path}.columns[{k}]")
    return True


def _row(w: _Walk, raw: Any, rpath: str, *, table: bool = False) -> None:
    """One `rows` row (`{cells, logo?, chip?, label?, detail?, table?}`, ROW_KEYS): its logo, its ticker chip, its
    cells, its `label` (news-v23, company stakes: the basis word over the figure), its `detail` (owner decisions
    2026-11-20, the insider list: the grey "… a share, as filed" line), then — news-v23, the earnings table — its
    `table` lines' cells (a label, then one per column), exactly when its section names `columns` (`table=True`);
    also a list cover's row (news-v23, `opening_card.list.rows`)."""
    required, optional = ROW_KEYS
    row = w.obj(raw, rpath, required + (("table",) if table else ()), optional)
    if row.get("logo") is not None:
        w.logo(row["logo"], f"{rpath}.logo")
    if row.get("chip") is not None:
        if not isinstance(row["chip"], str) or len(row["chip"]) > ROW_CHIP_MAX_CHARS:
            _fail("bad_string", f"{rpath}.chip", f"not a chip of at most {ROW_CHIP_MAX_CHARS} characters")
        w.text(row["chip"], f"{rpath}.chip")
    for k, cell in enumerate(w.seq(row["cells"], f"{rpath}.cells", 1, MAX_CELLS)):
        w.text(cell, f"{rpath}.cells[{k}]")
    if row.get("label") is not None:
        if not isinstance(row["label"], str) or len(row["label"]) > ROW_LABEL_MAX_CHARS:
            _fail("bad_string", f"{rpath}.label", f"not a label of at most {ROW_LABEL_MAX_CHARS} characters")
        w.text(row["label"], f"{rpath}.label")
    if row.get("detail") is not None:
        # owner decisions 2026-11-20: the insider list's grey detail line, under the name and the roles
        if not isinstance(row["detail"], str) or len(row["detail"]) > ROW_DETAIL_MAX_CHARS:
            _fail("bad_string", f"{rpath}.detail", f"not a detail line of at most {ROW_DETAIL_MAX_CHARS} characters")
        w.text(row["detail"], f"{rpath}.detail")
    if not table:
        return
    required, optional = TABLE_KEYS
    for t, raw_line in enumerate(w.seq(row["table"], f"{rpath}.table", 1, MAX_TABLE_LINES)):
        tpath = f"{rpath}.table[{t}]"
        line = w.obj(raw_line, tpath, required, optional)
        for k, cell in enumerate(w.seq(line["cells"], f"{tpath}.cells", TABLE_CELLS, TABLE_CELLS)):
            w.text(cell, f"{tpath}.cells[{k}]")


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
    # news-v23: the plate's pill draws the header's ticker CHIP (mock_money_map.header_block); a spec without one
    # (news-v22) draws the company's name there
    _company(w, spec["header"], "header", chip=True)
    w.text(spec["title"], "title")
    w.text(spec["subtitle"], "subtitle")
    _bar_list(w, spec, "segments", MIN_BARS, MAX_BARS)
    _bar_list(w, spec, "flow", MIN_FLOW, MAX_FLOW)
    w.text(spec["callout"], "callout")
    if spec.get("costs") is not None:
        # owner decision 2026-11-20: a loss year's costs card (drawn in place of the flow and the callout above)
        _costs(w, spec["costs"], "costs")


def _costs(w: _Walk, value: Any, path: str) -> None:
    """A Money Map loss year's COSTS card (COSTS_KEYS, owner decision 2026-11-20), in draw order: the revenue bar's label
    and value, the net loss's label and value, the costs bar's label and value, each part's label and value, the note,
    then the callout's two lines. Ratios are checked (finite, [0, 1]; the revenue bar never longer than the costs bar),
    never drawn."""
    required, optional = COSTS_KEYS
    c = w.obj(value, path, required, optional)
    ratios: Dict[str, float] = {}
    for key, (req, opt) in (("revenue", COSTS_BAR_KEYS), ("net", COSTS_NET_KEYS), ("total", COSTS_BAR_KEYS)):
        bar = w.obj(c[key], f"{path}.{key}", req, opt)
        w.text(bar["label"], f"{path}.{key}.label")
        w.text(bar["value"], f"{path}.{key}.value")
        if "ratio" in req:
            problem = _ratio_problem(bar["ratio"])
            if problem:
                _fail("bad_ratio", f"{path}.{key}.ratio", problem)
            ratios[key] = float(bar["ratio"])
    if ratios["revenue"] > ratios["total"]:
        _fail("bad_ratio", f"{path}.revenue.ratio", "the revenue bar is longer than the costs bar (not a loss year)")
    parts = w.seq(c["parts"], f"{path}.parts", 0, max(COSTS_PARTS))
    if len(parts) not in COSTS_PARTS:
        _fail("bad_count", f"{path}.parts", f"{len(parts)} not one of {COSTS_PARTS}")
    req, opt = COSTS_BAR_KEYS
    for i, raw in enumerate(parts):
        ppath = f"{path}.parts[{i}]"
        part = w.obj(raw, ppath, req, opt)
        w.text(part["label"], f"{ppath}.label")
        w.text(part["value"], f"{ppath}.value")
        problem = _ratio_problem(part["ratio"])
        if problem:
            _fail("bad_ratio", f"{ppath}.ratio", problem)
    if c.get("note") is not None:
        if not parts:
            _fail("missing_key", f"{path}.parts", "a note explains the hatched end of the parts: there are none")
        w.text(c["note"], f"{path}.note")
    for k, line in enumerate(w.seq(c["callout"], f"{path}.callout", COSTS_CALLOUT_LINES, COSTS_CALLOUT_LINES)):
        w.text(line, f"{path}.callout[{k}]")


def _icon(spec: Mapping[str, Any]) -> None:
    """A theme picture's decoration token (news-v23 phase 3): absent, or one of THEME_ICONS (never drawn as text)."""
    icon = spec.get("icon")
    if icon is not None and (not isinstance(icon, str) or icon not in THEME_ICONS):
        _fail("bad_icon", "icon", str(icon)[:20])


def _chip_key(w: _Walk, obj: Mapping[str, Any], path: str) -> None:
    """An optional ticker `chip` (a ticker, at most ROW_CHIP_MAX_CHARS), drawn."""
    if obj.get("chip") is not None:
        if not isinstance(obj["chip"], str) or len(obj["chip"]) > ROW_CHIP_MAX_CHARS:
            _fail("bad_string", f"{path}.chip", f"not a chip of at most {ROW_CHIP_MAX_CHARS} characters")
        w.text(obj["chip"], f"{path}.chip")


def _grid(w: _Walk, spec: Mapping[str, Any]) -> None:
    """news-v23 phase 3 adds a tile's ticker `chip` (drawn over its name), the spec's `icon` and its `sub_break` — the
    number of words the sub line's first line holds (1..words − 1; a drawing hint, never drawn)."""
    w.text(spec["title"], "title")
    w.text(spec["subtitle"], "subtitle")
    brk = spec.get("sub_break")
    if brk is not None and (isinstance(brk, bool) or not isinstance(brk, int)
                            or not 1 <= brk < len(str(spec["subtitle"]).split())):
        _fail("bad_sub_break", "sub_break", str(brk)[:20])
    _icon(spec)
    required, optional = GRID_TILE_KEYS
    for i, raw in enumerate(w.seq(spec["tiles"], "tiles", MIN_TILES, MAX_TILES)):
        path = f"tiles[{i}]"
        tile = w.obj(raw, path, required, optional)
        w.logo(tile["logo"], f"{path}.logo")
        _chip_key(w, tile, path)
        w.text(tile["name"], f"{path}.name")
        w.opt_text(tile, "line", path)
    w.opt_text(spec, "more", "")


def _ranked(w: _Walk, spec: Mapping[str, Any]) -> None:
    """The `ranked` layout (news-v23 phase 3, a theme's members by market value — theme_biggest_power.jpg), in draw
    order: the title, the subtitle, then each row's rank, logo, chip, name and value (its `ratio` the bar, in [0, 1],
    never drawn; its `rank` exactly its position), then "+N more"."""
    w.text(spec["title"], "title")
    w.text(spec["subtitle"], "subtitle")
    _icon(spec)
    required, optional = RANKED_ROW_KEYS
    for i, raw in enumerate(w.seq(spec["rows"], "rows", MIN_RANKED_ROWS, MAX_RANKED_ROWS)):
        path = f"rows[{i}]"
        row = w.obj(raw, path, required, optional)
        if row["rank"] != str(i + 1):
            _fail("bad_rank", f"{path}.rank", f"{str(row['rank'])[:8]!r} is not {i + 1}")
        w.text(row["rank"], f"{path}.rank")
        w.logo(row["logo"], f"{path}.logo")
        _chip_key(w, row, path)
        w.text(row["name"], f"{path}.name")
        w.text(row["value"], f"{path}.value")
        problem = _ratio_problem(row["ratio"])
        if problem:
            _fail("bad_ratio", f"{path}.ratio", problem)
    w.opt_text(spec, "more", "")


def _heads(w: _Walk, value: Any, path: str) -> None:
    """A theme table's two column `heads`, each 1..MAX_HEAD_LINES drawable lines (drawn in order)."""
    for i, head in enumerate(w.seq(value, path, THEME_HEADS, THEME_HEADS)):
        for k, line in enumerate(w.seq(head, f"{path}[{i}]", 1, MAX_HEAD_LINES)):
            w.text(line, f"{path}[{i}][{k}]")


def _shaped(w: _Walk, value: Any, path: str, shape: "re.Pattern[str]") -> None:
    """A drawn figure that must also be in its `shape` (the worker colours it by its sign)."""
    w.text(value, path)
    if shape.fullmatch(value) is None:
        _fail("bad_figure", path, f"{str(value)[:20]!r} is not in its figure shape")


def _theme_table_row(w: _Walk, layout: str, raw: Any, path: str) -> None:
    """One row of a theme table (THEME_TABLE_ROW_KEYS[layout]), in draw order: its logo, its chip, its name, then the
    figures — `figures` the growth (its `ratio` the bar, in [0, 1], never drawn) and the profit; `ledger` the purchases
    and the sales; `tally` the count."""
    required, optional = THEME_TABLE_ROW_KEYS[layout]
    row = w.obj(raw, path, required, optional)
    w.logo(row["logo"], f"{path}.logo")
    _chip_key(w, row, path)
    w.text(row["name"], f"{path}.name")
    if layout == "figures":
        _shaped(w, row["growth"], f"{path}.growth", GROWTH_FIGURE_RE)
        problem = _ratio_problem(row["ratio"])
        if problem:
            _fail("bad_ratio", f"{path}.ratio", problem)
        _shaped(w, row["profit"], f"{path}.profit", PROFIT_FIGURE_RE)
    elif layout == "ledger":
        # visual-fidelity round (2026-11-20, theme_insiders_cyber.jpg: a cell with no filing is BLANK): EMPTY_CELL is
        # a code-owned marker — checked, never drawn, so never an allowed string (the captions say "none" in words)
        for key in ("buys", "sells"):
            if row[key] != EMPTY_CELL:
                _shaped(w, row[key], f"{path}.{key}", LEDGER_AMOUNT_RE)
        if row["buys"] == EMPTY_CELL and row["sells"] == EMPTY_CELL:
            _fail("bad_figure", path, "a scorecard row with neither a purchase nor a sale")
    else:
        _shaped(w, row["count"], f"{path}.count", TALLY_COUNT_RE)
        if not 2 <= int(row["count"]) <= 535:
            _fail("bad_figure", f"{path}.count", "not a count of 2..535 members")


def _theme_table_body(w: _Walk, layout: str, obj: Mapping[str, Any], path: str, lo: int, hi: int) -> None:
    """A theme table's column heads (or unit), its rows (lo..hi), then its "+N more" and its note — the picture's
    body after its title and sub line, and the video cover's list after its heading."""
    prefix = f"{path}." if path else ""
    if layout == "tally":
        w.text(obj["unit"], f"{prefix}unit")
    else:
        _heads(w, obj["heads"], f"{prefix}heads")
    for i, raw in enumerate(w.seq(obj["rows"], f"{prefix}rows", lo, hi)):
        _theme_table_row(w, layout, raw, f"{prefix}rows[{i}]")
    w.opt_text(obj, "more", path)
    w.opt_text(obj, "note", path)


def _theme_table(layout: str) -> Any:
    def walk(w: _Walk, spec: Mapping[str, Any]) -> None:
        w.text(spec["title"], "title")
        w.text(spec["subtitle"], "subtitle")
        _icon(spec)
        _theme_table_body(w, layout, spec, "", MIN_THEME_TABLE_ROWS, MAX_THEME_TABLE_ROWS[layout])
    walk.__name__ = f"_{layout}"
    return walk


def _feature(w: _Walk, spec: Mapping[str, Any]) -> None:
    """The `feature` layout (2026-11-15), in draw order: the header (its logo plate sits in the art band
    beside the kicker; then its name and chip), the figure, its label, the headline, then each fact's
    label and value."""
    _company(w, spec["header"], "header", chip=True)
    w.text(spec["figure"], "figure")
    w.text(spec["figure_label"], "figure_label")
    w.text(spec["headline"], "headline")
    required, optional = FACT_KEYS
    for i, raw in enumerate(w.seq(spec["facts"], "facts", MIN_FACTS, MAX_FACTS)):
        path = f"facts[{i}]"
        fact = w.obj(raw, path, required, optional)
        w.text(fact["label"], f"{path}.label")
        w.text(fact["value"], f"{path}.value")


_WALKERS = {"rows": _rows, "spotlight": _spotlight, "pair": _pair, "bars": _bars, "grid": _grid,
            "feature": _feature, "ranked": _ranked, "figures": _theme_table("figures"),
            "ledger": _theme_table("ledger"), "tally": _theme_table("tally")}


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
    among the drawn strings). Only SHIPPED_LAYOUTS validate (every layout since 2026-11-15); any other
    layout answers `layout_not_shipped` (both sides' SHIPPED_LAYOUTS move together). Whether the worker
    holding a run may draw a `feature` spec is the CAPABILITY's question (`run_service`), never this
    one's. Pure."""
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


# ── the picture an OLDER worker draws (no WORKER_CAPABILITY_LOOK_V23) ────────────

#: The most rows an older worker's `rows` walker takes in one section (news-v22's cards.MAX_SECTION_ROWS; news-v23's
#: 13F list draws six). `script_service.PRE_V23_MAX_SECTION_ROWS` is this constant.
PRE_V23_MAX_SECTION_ROWS = 5
#: The news-v23 row keys an older worker's closed `rows` walker refuses that may be DROPPED for it without changing
#: what a row says: a row's ticker `chip` and an insider row's grey `detail` line (its share count and filed cost —
#: the row's total still stands). NEVER a stake row's `label` (the stake's basis: "committed up to", "carrying value",
#: "fair value") or an earnings row's `table` and its section's `columns` (the row's ONLY figures): dropped, an older
#: worker re-claiming a built run drew a committed or fair-value figure as a bare amount — money invested — or the
#: reporters' names with no figure at all (final review of 2026-11-26). Kept, its closed walker refuses the picture
#: and the render fails LOUDLY (RenderInputError → the run's failure is recorded, the run is retried and closes failed
#: at the attempts cap): no picture rather than one without its meaning. Earnings and company stakes never reach such
#: a holder at BUILD time (`script_service._without_look_series`); this is the re-claim of a run a newer worker built.
PRE_V23_ROW_DROPS: FrozenSet[str] = frozenset({"chip", "detail"})
#: The section keys dropped for an older worker: none (an earnings section's `columns` stay with its rows' tables).
PRE_V23_SECTION_DROPS: FrozenSet[str] = frozenset()


def pre_v23_section(sec: Dict[str, Any]) -> Dict[str, Any]:
    """One `rows` section as an older worker's walker takes it: no row chip and no insider detail line, and — the
    news-v23 13F list's six rows — at most PRE_V23_MAX_SECTION_ROWS rows, then without its "+N more" line too (that
    count would no longer be the picture's; every string left is still the stored spec's). A stake's `label` and an
    earnings row's `table` (and the section's `columns`) are KEPT: that walker then refuses the picture (a loud render
    failure) instead of drawing a figure without its basis or a name without its figures (PRE_V23_ROW_DROPS). Pure; a
    copy."""
    rows = [{k: v for k, v in row.items() if k not in PRE_V23_ROW_DROPS} if isinstance(row, dict) else row
            for row in sec["rows"]]
    sec = {k: v for k, v in sec.items() if k not in PRE_V23_SECTION_DROPS}
    if len(rows) <= PRE_V23_MAX_SECTION_ROWS:
        return {**sec, "rows": rows}
    return {k: v for k, v in sec.items() if k != "more"} | {"rows": rows[:PRE_V23_MAX_SECTION_ROWS]}


def pre_v23_image_spec(spec: Any) -> Any:
    """`spec` (an `image_spec`) as a worker WITHOUT WORKER_CAPABILITY_LOOK_V23 is sent it
    (`script_service._without_look_chips`, which logs): every `rows` section cut by `pre_v23_section`; a `bars` picture
    without its header's ticker chip and — owner decision 2026-11-20 — a Money Map loss year's COSTS card (such a worker
    draws the flow steps and their callout, still in the spec). Its strings are a subset of the stored spec's. Pure; a
    copy when anything is cut, else `spec` itself (anything that is not such a spec included)."""
    if not isinstance(spec, dict):
        return spec
    layout = spec.get("layout")
    if layout == "rows" and isinstance(spec.get("sections"), list):
        out = dict(spec)
        out["sections"] = [pre_v23_section(sec) if isinstance(sec, dict) and isinstance(sec.get("rows"), list)
                           else sec for sec in spec["sections"]]
        return out
    if layout == "bars" and ((isinstance(spec.get("header"), dict) and "chip" in spec["header"]) or "costs" in spec):
        out = {k: v for k, v in spec.items() if k != "costs"}
        if isinstance(spec.get("header"), dict):
            out["header"] = {k: v for k, v in spec["header"].items() if k != "chip"}
        return out
    return spec


def v23_only_strings(spec: Any, logos: Any) -> FrozenSet[str]:
    """The strings ONLY the look_v23 picture of `spec` draws — `image_strings(spec)` less those of
    `pre_v23_image_spec(spec)` (a Money Map loss year's costs card, a 13F list's sixth row and its "+N more" line, the
    ticker chips): one of them among a registered picture's declared strings says a look_v23 worker drew it. Empty when
    nothing is cut, or when either spec does not validate (no guess). Pure."""
    cut = pre_v23_image_spec(spec)
    if cut is spec:
        return frozenset()
    full, older = image_strings(spec, logos), image_strings(cut, logos)
    if not full or not older:
        return frozenset()
    return frozenset(full) - frozenset(older)


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
    chip = card.get("chip")
    if chip is not None and len(chip) > OPENING_CHIP_MAX_CHARS:     # a str: opt_text refused anything else
        _fail("chip_too_long", "opening_card.chip", f"{len(chip)} chars (max {OPENING_CHIP_MAX_CHARS})")
    w.opt_text(card, "figure", "opening_card")
    w.opt_text(card, "figure_label", "opening_card")         # under the figure, inside its card
    label = card.get("figure_label")
    if label is not None:
        if card.get("figure") is None:
            _fail("missing_key", "opening_card.figure", "a figure_label explains a figure")
        if len(label) > OPENING_FIGURE_LABEL_MAX_CHARS:                  # a str: opt_text refused anything else
            _fail("label_too_long", "opening_card.figure_label",
                  f"{len(label)} chars (max {OPENING_FIGURE_LABEL_MAX_CHARS})")
    w.text(card["headline"], "opening_card.headline")
    # owner decision 2026-11-20: a Money Map loss year's COSTS cover — the sub line under the headline, then the costs
    # card (the picture's own, COSTS_KEYS); never beside a list, and a subtitle only beside it
    w.opt_text(card, "subtitle", "opening_card")
    if card.get("costs") is not None:
        if card.get("list") is not None:
            _fail("unknown_key", "opening_card.list", "a costs cover carries no list")
        _costs(w, card["costs"], "opening_card.costs")
    elif card.get("subtitle") is not None:
        _fail("missing_key", "opening_card.costs", "a subtitle is drawn only on a costs cover")
    if card.get("list") is not None:
        # news-v23: the list cover — its column label, an earnings table's columns, its rows, its "+N more" line (drawn
        # in that order)
        required, optional = OPENING_LIST_KEYS
        lst = w.obj(card["list"], "opening_card.list", required, optional)
        layout = lst.get("layout")
        if layout is not None:
            # news-v23 phase 3 (2026-11-20): a theme table's cover — the picture's own rows (1..MAX_OPENING_ROWS) in
            # its own layout, under the heading (the picture's sub line): heads or unit, rows, "+N more", note
            if not isinstance(layout, str) or layout not in THEME_TABLE_LAYOUTS:
                _fail("unknown_layout", "opening_card.list.layout", str(layout)[:40])
            need, never = (("unit",), ("heads", "columns")) if layout == "tally" else (("heads",), ("unit", "columns"))
            for k in never:
                if lst.get(k) is not None:
                    _fail("unknown_key", f"opening_card.list.{k}", f"not a key of a {layout} cover")
            for k in ("heading",) + need:
                if lst.get(k) is None:
                    _fail("missing_key", f"opening_card.list.{k}", f"a {layout} cover needs it")
            w.text(lst["heading"], "opening_card.list.heading")
            _theme_table_body(w, layout, lst, "opening_card.list", 1, MAX_OPENING_TABLE_ROWS)
            return w
        for k in ("heads", "unit", "note"):
            if lst.get(k) is not None:
                _fail("unknown_key", f"opening_card.list.{k}", "only a theme table's cover carries it")
        w.opt_text(lst, "heading", "opening_card.list")
        tabled = _columns(w, lst, "opening_card.list")
        for j, raw in enumerate(w.seq(lst["rows"], "opening_card.list.rows", 1, MAX_OPENING_ROWS)):
            if isinstance(raw, Mapping) and raw.get("detail") is not None:
                # owner decisions 2026-11-20 (insider_multi5_cover_alt_all5.jpg, "All rows, compact"): a cover row is
                # the picture's row WITHOUT its detail line — the filed cost per share never reaches a video frame
                _fail("unknown_key", f"opening_card.list.rows[{j}].detail", "a cover row never carries the detail line")
            _row(w, raw, f"opening_card.list.rows[{j}]", table=tabled)
        w.opt_text(lst, "more", "opening_card.list")
    return w


def validate_opening_card(card: Any, logo_keys: Collection[str]) -> Optional[str]:
    """The first problem of a template video's `opening_card` (`{kicker, logos[1..2], chip?,
    figure?, figure_label?, headline, list?}`, closed; a chip at most OPENING_CHIP_MAX_CHARS long; news-v23: a 13F list
    cover's `list`, OPENING_LIST_KEYS; a `figure_label` only beside a figure; owner decision 2026-11-20: a Money Map loss
    year's `costs` card, COSTS_KEYS, with its `subtitle` — never beside a list), or None when it is drawable. Pure."""
    try:
        _walk_opening(card, logo_keys)
    except _Walk.Invalid as e:
        return str(e)
    return None


def opening_strings(card: Any, logos: Any) -> List[str]:
    """Every string a template video's opening card may draw, in draw order (kicker, the logos'
    company names, chip, figure, its figure_label, headline — then a Money Map loss year's sub line and costs card
    (owner decision 2026-11-20) — then, news-v23, a 13F list cover's column label, each row's logo name, chip and cells,
    and its "+N more" line), de-duplicated. An invalid card allows nothing ([] and an ERROR). Deterministic and pure."""
    names = _logo_names(logos)
    try:
        w = _walk_opening(card, names)
    except _Walk.Invalid as e:
        logger.error("template_onscreen: opening_card refused, no string is drawable: %s", e)
        return []
    return _resolve(w.out, names)


# ── per-post art: the declaration and the label (workstream C, 2026-11-15) ─────


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def art_declaration_problem(declared: Any, accepted_sha: Any, drawn: Any) -> Optional[str]:
    """Why one drawn surface's art declaration is refused, or None. `declared`: the sha256 the surface says it drew
    (the post image's `metadata.art_sha256`, the video's `metadata.opening_art_sha256`; None = no art drawn);
    `accepted_sha`: the sha256 of the accepted output's drawable art (`post_art.drawable_art(output)["sha256"]`),
    None when it has none — a lesson, a failed record, art off; `drawn`: the surface's declared on-screen strings.

    In this order: a declaration that is not 64 lower-case hex → `art_declaration_invalid`; a declaration while the
    output has no drawable art → `art_not_accepted` (any art on a lesson lands here); another sha →
    `art_mismatch`; a declaration without ART_LABEL among the drawn strings → `art_label_missing`; ART_LABEL drawn
    with no declaration → `art_label_without_art`. Art is NEVER required: no declaration and no label is None
    even when the output carries art (an older worker, a fallback drawing). An `accepted_sha` that is not a sha
    counts as none (fail closed). Pure, never raises."""
    accepted = accepted_sha if _is_sha(accepted_sha) else None
    labelled = isinstance(drawn, (list, tuple)) and any(s == ART_LABEL for s in drawn if isinstance(s, str))
    if declared is not None:
        if not _is_sha(declared):
            return "art_declaration_invalid"
        if accepted is None:
            return "art_not_accepted"
        if declared != accepted:
            return "art_mismatch"
        if not labelled:
            return "art_label_missing"
        return None
    if labelled:
        return "art_label_without_art"
    return None


def art_allowed(declared: Any) -> List[str]:
    """The strings a surface may draw BECAUSE it drew art: [ART_LABEL] when it declared a well-formed sha256, else
    [] (whether the declaration is the accepted art is `art_declaration_problem`'s question). Pure."""
    return [ART_LABEL] if _is_sha(declared) else []


# ── the data card's slot (Batch 2 fix, CARDS-B2-1) ───────────────────────────

#: A template video is one text card per narration line (`cards`), and a card stays on screen for its whole
#: line — so since news-v12 each card carries its own line's hedges ("Reporting delay", "About 13F filings",
#: "How the amount is counted"). The data card (Batch 2: the day's post image drawn in the video) used to
#: replace the LAST text card: exactly those hedges on several series. It now replaces the card named here —
#: a FACT card whose facts the post picture already shows — and never a card of DATA_CARD_KEEPS.
#:
#: Per series, preference groups of `news_templates.LEXICON` card-title ids: the slot is the first card, in
#: card order, whose title is one of the first group that names any card. A title with fills (a theme
#: member's name) matches only when every name it fills in is one the picture DRAWS. Variants are handled by
#: the titles, never a bare index:
#: * ceo_buys / insider_buys — "The purchase(s)" (card 2: count · shares · amount): the spotlight draws all
#:   three, a roundup's rows the lead row's amount;
#: * thirteen_f — none since news-v23 (NO_DATA_CARD_SERIES): the video's COVER is the picture's list
#:   (cover_13f_berkshire.jpg, the opening card's `list`), so a data card would draw the same list again;
#: * money_map — "Revenue mix" (card 2): the picture's bars name every part;
#: * congress_count and earnings — none since news-v23 (NO_DATA_CARD_SERIES): each is ONE post of several companies
#:   whose video COVER is the picture's list (congress_multi3.jpg's cards, earnings_multi4.jpg's table, on the opening
#:   card's `list`), so a data card would draw the same list again;
#: * company_stakes — "As of" (card 1): the pair draws "As of {date}" beside the figure, and the source line
#:   ("Source", card 4, and the basis card 2 are kept; card 3's listing claim is not on the picture);
#: * theme_explainer — the first MEMBER card ("{m1} and {m2}", "{m1}") whose members are all tiles of the
#:   grid (a name too long for a tile is left off it, counted in "+k more"); a card under a generic title
#:   ("Largest revenue line(s)", its names in the body) is never the slot. No such card: no data card.
#:
#: insider_spotlight (2026-11-15, NO_DATA_CARD_SERIES) has a deliberately EMPTY entry — no slot, so no data card,
#: ever: its picture is the `feature` layout (not in DATA_CARD_LAYOUTS), and its facts carry the filing's cost per
#: share "as filed", which owner decision 5 ("Captions + picture") keeps out of the narration, the burned captions and
#: every video card. Both 13F series (news-v23, the restyle — owner 2026-11-19) draw ONE list, and their video's COVER
#: is that list (cover_13f_berkshire.jpg: the opening card's `list`, `validate_opening_card`): a data card would draw
#: the same list again a few seconds later, so neither keeps a slot. The same holds, since news-v23, for the Congress
#: Count (2-3 companies), earnings (2-4 companies: their table) and company stakes (one investor's stakes in 1-8 public
#: companies) — their covers are their lists. Pinned, with this
#: reason, in tests/test_marketing_template_onscreen.py.
NO_DATA_CARD_SERIES: Tuple[str, ...] = ("insider_spotlight", "thirteen_f_spotlight", "thirteen_f", "congress_count",
                                        "earnings", "company_stakes", "theme_explainer")
DATA_CARD_SLOTS: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "ceo_buys": (("ins.c2.title.one", "ins.c2.title.many"),),
    "insider_buys": (("ins.c2.title.one", "ins.c2.title.many"),),
    # news-v23: the cover is the 13F list — never a data card (NO_DATA_CARD_SERIES)
    "thirteen_f": (),
    "money_map": (("mm.c2.title",),),
    # news-v23: the cover is the list of companies — never a data card (NO_DATA_CARD_SERIES)
    "congress_count": (),
    "earnings": (),
    # news-v23: one investor's stakes — the cover is their list (NO_DATA_CARD_SERIES)
    "company_stakes": (),
    # news-v23 phase 3: the cover is the theme's list (its first companies) — never a data card (NO_DATA_CARD_SERIES)
    "theme_explainer": (),
    # owner decision 5 (2026-11-15): never a data card — see NO_DATA_CARD_SERIES above
    "insider_spotlight": (),
    "thirteen_f_spotlight": (),
}
_INSIDER_KEEPS: Tuple[str, ...] = (
    "ins.c3.indirect.all.title", "ins.c3.indirect.some.title", "ins.c3.amended.title", "ins.c3.explainer.title",
    "ins.c3.late.date.title", "ins.c3.late.window.title", "ins.c4.amounts.title",
    # "Same week": its "At least {m} more …" is the roundup's lower bound (review round 9), a hedge it says itself
    "ins.c4.more.title",
)
#: The cards a data card NEVER replaces, whatever DATA_CARD_SLOTS says: every hedge, caveat, explainer and
#: source card a series can compose (checked before any slot title is matched). Static titles, matched
#: exactly. Pinned, by their words, in tests/test_marketing_template_onscreen.py.
DATA_CARD_KEEPS: Dict[str, Tuple[str, ...]] = {
    "ceo_buys": _INSIDER_KEEPS,
    "insider_buys": _INSIDER_KEEPS,
    "thirteen_f": (),
    "money_map": ("mm.c3.none.title",),
    # no slot to guard since news-v23 (NO_DATA_CARD_SERIES): nothing to keep
    "congress_count": (),
    "earnings": (),
    "company_stakes": (),
    "theme_explainer": (),
    # no slot to guard (NO_DATA_CARD_SERIES): nothing to keep
    "insider_spotlight": (),
    "thirteen_f_spotlight": (),
}

#: A lexicon fill: `{name}` (`news_templates._t` formats every entry with `str.format`).
_FILL_RE = re.compile(r"\{[A-Za-z0-9_]+\}")


@functools.lru_cache(maxsize=None)
def _slot_rules(series: str) -> Optional[Tuple[Tuple[Tuple[Tuple[str, ...], ...], ...], FrozenSet[str]]]:
    """The series' slot titles (DATA_CARD_SLOTS in preference order, each entry split at its fills into its
    literal parts) and its kept titles (DATA_CARD_KEEPS), read from the lexicon — or None for a series the
    table does not name, or an id the lexicon lacks (ERROR: the data card then has no slot — today's text
    cards)."""
    groups, keeps = DATA_CARD_SLOTS.get(series), DATA_CARD_KEEPS.get(series)
    if groups is None or keeps is None:
        return None
    # news_templates imports this module, so the lexicon is read here, at call time — never at import
    from app.services.marketing.news_templates import LEXICON
    try:
        parts = tuple(tuple(tuple(_FILL_RE.split(LEXICON[i])) for i in group) for group in groups)
        kept = frozenset(LEXICON[i] for i in keeps)
    except KeyError as e:
        logger.error("template_onscreen: the data-card slot table names the lexicon id %s that does not exist "
                     "(series %s) — no data-card slot", str(e)[:80], series)
        return None
    return parts, kept


def _renders(parts: Tuple[str, ...], title: str, drawn: FrozenSet[str]) -> bool:
    """Is `title` the lexicon entry split into `parts` (its literal text around each fill), with every fill one
    of the picture's own strings (`drawn`)? An entry with no fill is exact equality. Bounded: a card title has
    at most two fills, each tried against at most ONSCREEN_TEXT_MAX strings."""
    def match(i: int, pos: int) -> bool:
        if not title.startswith(parts[i], pos):
            return False
        pos += len(parts[i])
        if i == len(parts) - 1:
            return pos == len(title)
        return any(d and title.startswith(d, pos) and match(i + 1, pos + len(d)) for d in drawn)

    return match(0, 0)


def _text_card_title(card: Any) -> Optional[str]:
    """A content card's title when the card is one the worker draws as a TEXT card (`{title, body}`, both
    strings, the title not blank), else None — never a slot."""
    if not isinstance(card, dict):
        return None
    title, body = card.get("title"), card.get("body")
    if not isinstance(title, str) or not isinstance(body, str) or not title.strip():
        return None
    return title


def data_card_slot(series: Any, cards: Any, picture: Any) -> Optional[int]:
    """The 0-based index into a template script's `cards` (its content cards — never the code-owned opening
    card or the disclaimer card) of the ONE card its data card replaces (DATA_CARD_SLOTS), or None: a series
    the table does not name (a lesson, an unknown id), cards that are not a list, or no card of the series'
    slot titles. `picture`: the strings the data card draws (`image_strings` of the run's post image —
    `run_service.data_card_strings`); a title with fills matches only names drawn there. A card of
    DATA_CARD_KEEPS, or one that is not a well-formed text card, is never the slot. Deterministic, pure (the
    lexicon is read at call time); never raises."""
    rules = _slot_rules(series) if isinstance(series, str) else None
    if rules is None or not isinstance(cards, (list, tuple)):
        return None
    groups, kept = rules
    drawn = (frozenset(s for s in picture if isinstance(s, str))
             if isinstance(picture, (list, tuple, set, frozenset)) else frozenset())
    titles = [_text_card_title(c) for c in cards]
    for group in groups:
        for i, title in enumerate(titles):
            if title is None or title in kept:
                continue
            if any(_renders(parts, title, drawn) for parts in group):
                return i
    return None
