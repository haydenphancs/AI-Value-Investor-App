"""
Tests for `app/services/marketing/template_onscreen.py` — the closed on-screen allow-list of a
template (news) post (drop 2, contract D9): the post image's `image_spec` and the video's
`opening_card`.

Pinned promises:
* the schemas are CLOSED: an unknown key, a missing key, a non-drawable string, a list outside its
  bounds, a ratio that is not a finite number in [0, 1], an unknown style or an unknown logo key is
  refused — the validators name the first problem, None when the structure is drawable;
* `image_strings` / `opening_strings` are deterministic (draw order, de-duplicated, independent of
  JSON key order), hold the referenced logos' company names (wordmark fallback), never a URL or a
  hash, and allow NOTHING for an invalid structure (fail closed, logged at ERROR);
* every layout validates since drop 2b shipped pair / grid with their series (SHIPPED_LAYOUTS ==
  LAYOUTS); a layout dropped from SHIPPED_LAYOUTS is refused as `layout_not_shipped` and allows
  NOTHING (the worker cannot draw it — fail closed);
* every allowed string is declarable as on-screen text (`schemas.marketing.validate_onscreen_text`),
  and the worst case of every layout stays under ONSCREEN_TEXT_MAX;
* Batch 1 (2026-10-15): an opening card's chip is at most OPENING_CHIP_MAX_CHARS (36) — exact at the
  cap, refused one character over (`chip_too_long`, allowing nothing), still optional.
* Batch 2 fix (CARDS-B2-1, 2026-10-20): `data_card_slot` — the ONE content card a video's data card
  replaces. Over every series and variant the templates compose it is a FACT card whose facts the post
  picture draws, never a hedge, caveat, explainer or source card (DATA_CARD_KEEPS, pinned by their words);
  a theme member card only when the grid draws its members; None — no data card — for anything else.
* per-post art (workstream C, 2026-11-15): ART_LABEL ("AI illustration") is drawable and names no model; the
  declaration rule `art_declaration_problem` — the declared sha must be the accepted art's, the label and the
  declaration come together, art is NEVER required, any art on a lesson is refused — over its full matrix; the label
  reaches an allow-list only through `art_allowed`, never through the closed image / opening-card walks.
  Mutation-tested by hand (2026-11-16): no label requirement, an accepted mismatch, art made required and the label
  allowed for a malformed sha each turned the matrix red.

Category 1 (pure): no network, no Supabase.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from app.schemas.marketing import ONSCREEN_TEXT_MAX, ONSCREEN_TEXT_MAX_CHARS, validate_onscreen_text
from app.services.marketing import post_copy
from app.services.marketing import template_onscreen as tos

BACKEND = Path(__file__).resolve().parents[1]
SHA = "0" * 64
URL = "https://example.supabase.co/storage/v1/object/public/marketing-media/logos/" + "a" * 32 + ".png"

FOOTER = post_copy.image_footer(date(2026, 10, 12), "template", source=post_copy.FOOTER_SOURCE_FORM4,   # news-v23
                                when="filed Oct 5–9, 2026")


def _logo(key, name, *, url=URL, sha=SHA):
    return {"key": key, "name": name, "url": url, "sha256": sha, "bytes": 2048, "width": 200, "height": 200}


LOGOS = [
    _logo("GME", "GameStop"),
    _logo("EXB", "Example Bancorp", url=None, sha=None),   # a missing logo: wordmark tile
    _logo("SMR", "Sample Robotics"),
    _logo("COST", "Costco"),
    _logo("NVDA", "NVIDIA"),
    _logo("NWFT", "Northwind Fitness"),
    _logo("BRK-B", "Berkshire Hathaway"),
    _logo("AAPL", "Apple"),
    _logo("MSFT", "Microsoft"),
    _logo("AMZN", "Amazon"),
    _logo("GOOGL", "Alphabet"),
    _logo("META", "Meta Platforms"),
    _logo("AVGO", "Broadcom"),
]
KEYS = tos.logo_keys(LOGOS)

ROWS_CEO = {
    "layout": "rows", "version": 1, "kicker": "FILED LAST WEEK · FORM 4",
    "title": "3 CEOs disclosed buying their own company's stock",
    "sections": [{"rows": [
        {"logo": "GME", "cells": ["GameStop", "CEO", "$74.4M"]},
        {"logo": "EXB", "cells": ["Example Bancorp", "CEO", "$2.3M"]},
        {"logo": "SMR", "cells": ["Sample Robotics", "CEO", "$410K"]},
    ]}],
    "notes": ["Some purchases are reported as held indirectly.",
              "One or more figures come from an amended filing (Form 4/A)."],
    "footer": FOOTER,
}
ROWS_13F = {
    "layout": "rows", "version": 1, "kicker": "13F SEASON",
    "header": {"logo": "BRK-B", "name": "Berkshire Hathaway", "chip": "BRK-B"},
    "title": "Berkshire Hathaway", "subtitle": "Quarter ended Sep 30, 2026 · filed Nov 14, 2026",
    "sections": [
        {"heading": "Newly reported", "rows": [{"logo": "AAPL", "cells": ["Apple"]},
                                               {"logo": "MSFT", "cells": ["Microsoft"]}],
         "more": "+2 more"},
        {"heading": "No longer reported", "rows": [{"logo": "AMZN", "cells": ["Amazon"]}]},
    ],
    "footer": FOOTER,
}
ROWS_EARNINGS = {
    "layout": "rows", "version": 1, "kicker": "EARNINGS VS ESTIMATES",
    "header": {"logo": "NWFT", "name": "Northwind Fitness", "chip": "NWFT"},
    "title": "Quarter ended Sep 30, 2026",
    "sections": [{"rows": [{"cells": ["EPS", "-$0.05", "vs -$0.12 estimate"]},
                           {"cells": ["Revenue", "$551.9M", "vs $543.6M estimate"]}]}],
    "footer": FOOTER,
}
SPOTLIGHT = {
    "layout": "spotlight", "version": 1, "kicker": "FILED LAST WEEK · FORM 4",
    "header": {"logo": "GME", "name": "GameStop", "chip": "GME"},
    "figure": "$74.4M", "headline": "GameStop's CEO disclosed buying GameStop stock",
    "lines": ["2 open-market purchases", "Filed Oct 7–8, 2026"],
    "footer": FOOTER,
}
BARS = {
    "layout": "bars", "version": 1, "kicker": "MONEY MAP",
    "header": {"logo": "COST", "name": "Costco"},
    "title": "How Costco makes money", "subtitle": "Fiscal 2025",
    "segments": [
        {"label": "Merchandise", "value": "$269.9B", "ratio": 1.0, "style": "fill"},
        {"label": "Membership fees", "value": "$5.3B", "ratio": 0.0196, "style": "fill"},
    ],
    "flow": [
        {"label": "Revenue", "value": "$275.2B", "ratio": 1, "style": "fill"},
        {"label": "Gross profit", "value": "$35.4B", "ratio": 0.1286, "style": "fill"},
        {"label": "Operating profit", "value": "$10.4B", "ratio": 0.0378, "style": "fill"},
        {"label": "Net income", "value": "$8.1B", "ratio": 0.0294, "style": "outline"},
    ],
    "callout": "For every $100 of sales, $2.94 was profit",
    "footer": FOOTER,
}
PAIR = {
    "layout": "pair", "version": 1, "kicker": "COMPANY STAKES",
    "left": {"logo": "NVDA", "name": "NVIDIA"}, "right": {"logo": "AAPL", "name": "Apple"},
    "figure": "$777.4M", "label": "invested", "lines": ["As of Mar 27, 2026"],
    "footer": FOOTER,
}
GRID = {
    "layout": "grid", "version": 1, "kicker": "INSIDE A THEME",
    "title": "AI chips", "subtitle": "Largest revenue segment of each",
    "tiles": [{"logo": "NVDA", "name": "NVIDIA", "line": "Data Center"},
              {"logo": "AVGO", "name": "Broadcom", "line": "Semiconductor solutions"},
              {"logo": "AAPL", "name": "Apple"}],
    "more": "+3 more",
    "footer": FOOTER,
}
#: `feature` (2026-11-15): the spotlight series' art-band picture — header (its logo in the band), figure, its label,
#: the headline, 1..MAX_FACTS fact rows.
FEATURE = {
    "layout": "feature", "version": 1, "kicker": "FILED OCT 7–8 · FORM 4",
    "header": {"logo": "GME", "name": "GameStop", "chip": "GME"},
    "figure": "$2M", "figure_label": "of stock, per the Form 4",
    "headline": "GameStop's CEO and at least 2 other insiders disclosed buying stock",
    "facts": [{"label": "Trade dates", "value": "Oct 6–7, 2026"}, {"label": "Shares", "value": "14K"},
              {"label": "Cost per share, as filed", "value": "$142.10–$143.85"},
              {"label": "Other insiders", "value": "At least 2 more"}],
    "footer": FOOTER,
}
#: news-v23 phase 3 — the weekly theme post: angle 1's grid with ticker chips, its decoration and its hand-set sub-line
#: break (theme_ai_chips.jpg), and angle 2's `ranked` rows (theme_biggest_power.jpg).
GRID_V3 = {
    "layout": "grid", "version": 1, "kicker": "INSIDE A THEME", "title": "AI chips",
    "subtitle": "14 companies · each company's largest business and its share of total revenue · fiscal 2025 or 2026",
    "sub_break": 6, "icon": "chip",
    "tiles": [{"logo": "NVDA", "chip": "NVDA", "name": "NVIDIA", "line": "Data Center · 88% of revenue"},
              {"logo": "AVGO", "chip": "AVGO", "name": "Broadcom", "line": "Semiconductor Solutions · 58% of revenue"},
              {"logo": "AAPL", "chip": "AAPL", "name": "Apple"}],
    "more": "+11 more", "footer": FOOTER,
}
RANKED = {
    "layout": "ranked", "version": 1, "kicker": "INSIDE A THEME", "title": "Power the Machine",
    "subtitle": "16 companies · market value as of Nov 24, 2026", "icon": "bolt",
    "rows": [{"rank": "1", "logo": "NVDA", "chip": "NVDA", "name": "NVIDIA", "value": "$4.4T", "ratio": 1.0},
             {"rank": "2", "logo": "AAPL", "chip": "AAPL", "name": "Apple", "value": "$3.9T", "ratio": 0.8864},
             {"rank": "3", "logo": "MSFT", "name": "Microsoft", "value": "$3.6T", "ratio": 0.8182},
             {"rank": "4", "logo": "AVGO", "chip": "AVGO", "name": "Broadcom", "value": "$1.6T", "ratio": 0.3636}],
    "more": "+12 more", "footer": FOOTER,
}
#: news-v23 phase 3, the theme's last three angles (2026-11-20): `figures` (growth + profit), `ledger` (the insider
#: scorecard) and `tally` (Congress) — SAMPLE figures.
FIGURES = {
    "layout": "figures", "version": 1, "kicker": "INSIDE A THEME", "title": "Silicon Rush",
    "subtitle": "14 of its 21 companies · revenue growth and profit · latest fiscal year", "icon": "wafer",
    "heads": [["Revenue growth"], ["Profit per $100", "of revenue"]],
    "rows": [{"logo": "NVDA", "chip": "NVDA", "name": "NVIDIA", "growth": "+114%", "ratio": 1.0, "profit": "$56"},
             {"logo": "AVGO", "chip": "AVGO", "name": "Broadcom", "growth": "+44%", "ratio": 0.386, "profit": "$22"},
             {"logo": "AAPL", "chip": "AAPL", "name": "Apple", "growth": "−2%", "ratio": 0.0175, "profit": "−$17"},
             {"logo": "MSFT", "name": "Microsoft", "growth": "0%", "ratio": 0.0, "profit": "$0.40"}],
    "more": "+10 more", "note": "Profit = net income; growth = revenue vs the year before", "footer": FOOTER,
}
LEDGER = {
    "layout": "ledger", "version": 1, "kicker": "INSIDE A THEME", "title": "Cyber Wars",
    "subtitle": "15 companies · insider filings · last 90 days", "icon": "shield",
    "heads": [["Disclosed", "purchases"], ["Disclosed", "sales"]],
    "rows": [{"logo": "NVDA", "chip": "NVDA", "name": "NVIDIA", "buys": "$2.1M", "sells": "—"},
             {"logo": "AVGO", "chip": "AVGO", "name": "Broadcom", "buys": "$640K", "sells": "$8.6M"},
             {"logo": "AAPL", "chip": "AAPL", "name": "Apple", "buys": "—", "sells": "$186M"},
             {"logo": "MSFT", "name": "Microsoft", "buys": "—", "sells": "$1.2B"}],
    "more": "+1 more with filings", "note": "Purchases and sales only · sales can be pre-planned (10b5-1)",
    "footer": FOOTER,
}
TALLY = {
    "layout": "tally", "version": 1, "kicker": "INSIDE A THEME", "title": "Modern Battlefield",
    "subtitle": "Members of Congress who disclosed purchases", "unit": "members", "icon": "radar",
    "rows": [{"logo": "NVDA", "chip": "NVDA", "name": "NVIDIA", "count": "6"},
             {"logo": "AVGO", "chip": "AVGO", "name": "Broadcom", "count": "4"},
             {"logo": "AAPL", "chip": "AAPL", "name": "Apple", "count": "2"},
             {"logo": "MSFT", "name": "Microsoft", "count": "2"}],
    "note": "October 2026 · each member counted once per company", "footer": FOOTER,
}
OPENING = {"kicker": "FILED LAST WEEK · FORM 4", "logos": ["GME"], "chip": "GME", "figure": "$74.4M",
           "headline": "GameStop's CEO disclosed buying GameStop stock"}

ROWS_EARNINGS_NO_LOGO = {k: v for k, v in ROWS_EARNINGS.items() if k != "header"}

SHIPPED_SAMPLES = {"rows_ceo": ROWS_CEO, "rows_13f": ROWS_13F, "rows_earnings": ROWS_EARNINGS,
                   "spotlight": SPOTLIGHT, "bars": BARS, "pair": PAIR, "grid": GRID, "feature": FEATURE,
                   "grid_v3": GRID_V3, "ranked": RANKED, "figures": FIGURES, "ledger": LEDGER, "tally": TALLY}
#: The two layouts drop 2b shipped (company_stakes, theme_explainer).
SAMPLES_2B = {"pair": PAIR, "grid": GRID}
#: One sample per layout (the `layout_not_shipped` gate is checked for each).
SAMPLE_PER_LAYOUT = {"rows": ROWS_CEO, "spotlight": SPOTLIGHT, "pair": PAIR, "bars": BARS, "grid": GRID,
                     "feature": FEATURE, "ranked": RANKED, "figures": FIGURES, "ledger": LEDGER, "tally": TALLY}

#: image_strings per sample, pinned: kicker first, footer last, each logo's name at its position.
STRINGS_GOLDEN = {
    "rows_ceo": ["FILED LAST WEEK · FORM 4", "3 CEOs disclosed buying their own company's stock",
                 "GameStop", "CEO", "$74.4M", "Example Bancorp", "$2.3M", "Sample Robotics", "$410K",
                 "Some purchases are reported as held indirectly.",
                 "One or more figures come from an amended filing (Form 4/A).", FOOTER],
    "rows_13f": ["13F SEASON", "Berkshire Hathaway", "BRK-B", "Quarter ended Sep 30, 2026 · filed Nov 14, 2026",
                 "Newly reported", "Apple", "Microsoft", "+2 more", "No longer reported", "Amazon", FOOTER],
    "rows_earnings": ["EARNINGS VS ESTIMATES", "Northwind Fitness", "NWFT", "Quarter ended Sep 30, 2026",
                      "EPS", "-$0.05", "vs -$0.12 estimate", "Revenue", "$551.9M", "vs $543.6M estimate",
                      FOOTER],
    "spotlight": ["FILED LAST WEEK · FORM 4", "GameStop", "GME", "$74.4M",
                  "GameStop's CEO disclosed buying GameStop stock", "2 open-market purchases",
                  "Filed Oct 7–8, 2026", FOOTER],
    "bars": ["MONEY MAP", "Costco", "How Costco makes money", "Fiscal 2025", "Merchandise", "$269.9B",
             "Membership fees", "$5.3B", "Revenue", "$275.2B", "Gross profit", "$35.4B", "Operating profit",
             "$10.4B", "Net income", "$8.1B", "For every $100 of sales, $2.94 was profit", FOOTER],
    "pair": ["COMPANY STAKES", "NVIDIA", "Apple", "$777.4M", "invested", "As of Mar 27, 2026", FOOTER],
    "grid": ["INSIDE A THEME", "AI chips", "Largest revenue segment of each", "NVIDIA", "Data Center",
             "Broadcom", "Semiconductor solutions", "Apple", "+3 more", FOOTER],
    # the band's kicker and logo name first, then the text under the band, each fact's label before its value
    # news-v23 phase 3: each tile's logo name, its chip, its name (deduplicated: the logo's name IS the tile's name),
    # its line; a ranked row's rank, logo name, chip, name, value — never the icon, the break or a ratio
    "grid_v3": ["INSIDE A THEME", "AI chips",
                "14 companies · each company's largest business and its share of total revenue · fiscal 2025 or 2026",
                "NVIDIA", "NVDA", "Data Center · 88% of revenue", "Broadcom", "AVGO",
                "Semiconductor Solutions · 58% of revenue", "Apple", "AAPL", "+11 more", FOOTER],
    "ranked": ["INSIDE A THEME", "Power the Machine", "16 companies · market value as of Nov 24, 2026", "1", "NVIDIA",
               "NVDA", "$4.4T", "2", "Apple", "AAPL", "$3.9T", "3", "Microsoft", "$3.6T", "4", "Broadcom", "AVGO",
               "$1.6T", "+12 more", FOOTER],
    # news-v23 phase 3, the last three angles: the title, the sub line, the column heads (or the unit), each row's logo
    # name, chip, name and figures, "+N more", the note — never the icon or a ratio, never a ledger's EMPTY_CELL marker
    # (visual-fidelity round 2026-11-20: a cell with no filing is drawn BLANK)
    "figures": ["INSIDE A THEME", "Silicon Rush", "14 of its 21 companies · revenue growth and profit · latest fiscal year",
                "Revenue growth", "Profit per $100", "of revenue", "NVIDIA", "NVDA", "+114%", "$56", "Broadcom", "AVGO",
                "+44%", "$22", "Apple", "AAPL", "−2%", "−$17", "Microsoft", "0%", "$0.40", "+10 more",
                "Profit = net income; growth = revenue vs the year before", FOOTER],
    "ledger": ["INSIDE A THEME", "Cyber Wars", "15 companies · insider filings · last 90 days", "Disclosed", "purchases",
               "sales", "NVIDIA", "NVDA", "$2.1M", "Broadcom", "AVGO", "$640K", "$8.6M", "Apple", "AAPL", "$186M",
               "Microsoft", "$1.2B", "+1 more with filings", "Purchases and sales only · sales can be pre-planned (10b5-1)",
               FOOTER],
    "tally": ["INSIDE A THEME", "Modern Battlefield", "Members of Congress who disclosed purchases", "members", "NVIDIA",
              "NVDA", "6", "Broadcom", "AVGO", "4", "Apple", "AAPL", "2", "Microsoft",
              "October 2026 · each member counted once per company", FOOTER],
    "feature": ["FILED OCT 7–8 · FORM 4", "GameStop", "GME", "$2M", "of stock, per the Form 4",
                "GameStop's CEO and at least 2 other insiders disclosed buying stock", "Trade dates", "Oct 6–7, 2026",
                "Shares", "14K", "Cost per share, as filed", "$142.10–$143.85", "Other insiders", "At least 2 more",
                FOOTER],
}


def _all_layouts(spec, keys=KEYS, footer=None):
    """The validator with every layout shipped, whatever SHIPPED_LAYOUTS says (the schema alone)."""
    try:
        tos._walk_image_spec(spec, keys, shipped=tos.LAYOUTS, footer=footer)
    except tos._Walk.Invalid as e:
        return str(e)
    return None


def _code(problem):
    return problem.split(":", 1)[0] if problem else None


# ── constants ────────────────────────────────────────────────────────────────


def test_the_contract_constants_are_pinned():
    assert tos.LAYOUTS == ("rows", "spotlight", "pair", "bars", "grid", "feature", "ranked", "figures", "ledger",
                           "tally")
    # drop 2b (2026-10-10): pair and grid shipped with company_stakes / theme_explainer; 2026-11-15: `feature`,
    # the spotlight series' art-band picture (a holder must still declare layout_feature to be handed one); news-v23
    # phase 3: `ranked`, the weekly theme post's angle 2, and (2026-11-20) `figures`, `ledger`, `tally` — angles 3-5
    # (a holder must declare theme_angles)
    assert tos.SHIPPED_LAYOUTS == tos.LAYOUTS
    assert tos.THEME_TABLE_LAYOUTS == ("figures", "ledger", "tally")
    assert (tos.MIN_THEME_TABLE_ROWS, tos.MAX_THEME_TABLE_ROWS) == (4, {"figures": 10, "ledger": 10, "tally": 6})
    assert (tos.THEME_HEADS, tos.MAX_HEAD_LINES, tos.MAX_OPENING_TABLE_ROWS) == (2, 2, 6)
    assert tos.THEME_TABLE_ROW_KEYS == {"figures": (("logo", "name", "growth", "ratio", "profit"), ("chip",)),
                                        "ledger": (("logo", "name", "buys", "sells"), ("chip",)),
                                        "tally": (("logo", "name", "count"), ("chip",))}
    for layout in tos.THEME_TABLE_LAYOUTS:
        assert tos.LAYOUT_KEYS[layout] == (("title", "subtitle", "unit" if layout == "tally" else "heads", "rows"),
                                           ("more", "icon", "note"))
    assert (tos.MINUS, tos.EMPTY_CELL) == ("\u2212", "\u2014")
    assert (tos.MIN_RANKED_ROWS, tos.MAX_RANKED_ROWS) == (4, 10)
    assert tos.RANKED_ROW_KEYS == (("rank", "logo", "name", "value", "ratio"), ("chip",))
    assert tos.LAYOUT_KEYS["ranked"] == (("title", "subtitle", "rows"), ("more", "icon"))
    assert tos.LAYOUT_KEYS["grid"] == (("title", "subtitle", "tiles"), ("more", "icon", "sub_break"))
    assert tos.GRID_TILE_KEYS == (("logo", "name"), ("chip", "line"))
    assert tos.THEME_ICONS == ("chip", "bolt", "wafer", "shield", "radar")
    # every layout but `feature` may be a video's data card (owner decision 5: the cost per share stays off video)
    assert tos.DATA_CARD_LAYOUTS == ("rows", "spotlight", "pair", "bars", "grid")
    assert tos.SPEC_VERSION == 1
    assert (tos.MAX_LOGOS, tos.MAX_ROWS, tos.MAX_SECTIONS, tos.MAX_LINES, tos.MAX_BARS, tos.MAX_FLOW,
            tos.MAX_TILES) == (12, 8, 3, 2, 7, 4, 12)
    # news-v23 (the 13F list): one section of up to six rows
    assert (tos.MIN_BARS, tos.MIN_FLOW, tos.MIN_TILES, tos.MAX_SECTION_ROWS, tos.MAX_CELLS, tos.MAX_NOTES,
            tos.MAX_OPENING_LOGOS) == (2, 2, 3, 6, 3, 2, 2)
    assert (tos.MIN_FACTS, tos.MAX_FACTS) == (1, 4)
    assert tos.LAYOUT_KEYS["feature"] == (("header", "figure", "figure_label", "headline", "facts"), ())
    assert tos.FACT_KEYS == (("label", "value"), ())
    assert tos.BAR_STYLES == ("fill", "outline")
    assert tuple(tos.LAYOUT_KEYS) == tos.LAYOUTS
    assert tos.COMMON_KEYS == ("layout", "version", "kicker", "footer")
    # news-v23: the 13F LIST cover (cover_13f_berkshire.jpg) — the column label, at most four rows, "+N more"
    # 2026-11-20: `figure_label` (the Money Map cover's words under its figure); owner decision 2026-11-20 ("A: costs
    # vs revenue"): a loss year's costs cover — its `subtitle` and its `costs` card
    assert tos.OPENING_KEYS == (("kicker", "logos", "headline"), ("chip", "figure", "list", "figure_label", "subtitle",
                                                                  "costs"))
    # owner decision 2026-11-20: a loss year's COSTS card on the `bars` picture (and its cover) — its closed keys, its
    # parts (none, or cost of sales + operating expenses [+ interest, tax and other items as a cost]), two callout lines
    assert tos.LAYOUT_KEYS["bars"] == (("header", "title", "subtitle", "segments", "flow", "callout"), ("costs",))
    assert tos.COSTS_KEYS == (("revenue", "net", "total", "parts", "callout"), ("note",))
    assert (tos.COSTS_BAR_KEYS, tos.COSTS_NET_KEYS) == ((("label", "value", "ratio"), ()), (("label", "value"), ()))
    assert (tos.COSTS_PARTS, tos.COSTS_CALLOUT_LINES) == ((0, 2, 3), 2)
    # news-v23: the list cover's column label is optional (a Congress Count list has none); an earnings cover names its
    # table's columns — the earnings TABLE's shape (a label and one cell per column, at most two lines a row)
    # news-v23 phase 3 (2026-11-20): a theme TABLE's cover names its layout and carries the picture's heads / unit / note
    assert tos.OPENING_LIST_KEYS == (("rows",), ("heading", "columns", "more", "layout", "heads", "unit", "note"))
    # owner decisions 2026-11-20 ("All rows, compact (Recommended)"): a Form 4 list's cover draws every row its picture
    # draws — up to five (insider_multi5_cover_alt_all5.jpg)
    assert tos.MAX_OPENING_ROWS == 5
    # owner decisions 2026-11-20: a row's grey DETAIL line (the insider list's "<shares> shares · $<low>–$<high> a share,
    # as filed") — picture rows only, never the cover (`detail` is never walked on an opening card's list)
    assert tos.ROW_KEYS == (("cells",), ("logo", "chip", "label", "detail")) and tos.ROW_DETAIL_MAX_CHARS == 64
    assert (tos.TABLE_CELLS, tos.MAX_TABLE_LINES, tos.TABLE_KEYS) == (4, 2, (("cells",), ()))
    # Batch 1 (2026-10-15): the longest chip a template writes is a stake's "Committed up to · as of Nov 18, 2025"
    assert tos.OPENING_CHIP_MAX_CHARS == 36 == len("Committed up to · as of Nov 18, 2025")
    for layout, (required, optional) in tos.LAYOUT_KEYS.items():
        assert not set(required) & set(optional), layout
        assert not (set(required) | set(optional)) & set(tos.COMMON_KEYS), layout


# ── valid specs and their strings ────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(SHIPPED_SAMPLES))
def test_a_shipped_sample_is_valid_and_its_strings_are_pinned(name):
    spec = SHIPPED_SAMPLES[name]
    assert tos.validate_image_spec(spec, KEYS) is None
    assert tos.validate_image_spec(spec, KEYS, footer=FOOTER) is None
    got = tos.image_strings(spec, LOGOS)
    assert got == STRINGS_GOLDEN[name]
    assert got[0] == spec["kicker"] and got[-1] == FOOTER
    assert len(got) == len(set(got))
    validate_onscreen_text(got)                  # the worker can declare every one of them
    assert len(got) <= ONSCREEN_TEXT_MAX


@pytest.mark.parametrize("name", sorted(SHIPPED_SAMPLES))
def test_the_strings_are_deterministic_and_independent_of_key_order(name):
    spec = SHIPPED_SAMPLES[name]

    def shuffled(v):
        if isinstance(v, dict):
            return {k: shuffled(v[k]) for k in reversed(list(v))}
        if isinstance(v, list):
            return [shuffled(x) for x in v]
        return v

    round_trip = json.loads(json.dumps(shuffled(spec), sort_keys=True))   # JSONB reorders keys
    as_tuples = {k: tuple(v) if isinstance(v, list) else v for k, v in spec.items()}
    want = tos.image_strings(spec, LOGOS)
    for variant in (spec, copy.deepcopy(spec), round_trip, shuffled(spec), as_tuples):
        assert tos.validate_image_spec(variant, KEYS) is None
        assert tos.image_strings(variant, LOGOS) == want
        assert tos.image_strings(variant, json.loads(json.dumps(LOGOS))) == want
    assert tos.image_strings(spec, list(reversed(LOGOS))) == want   # the logos' order is not drawn


def test_a_missing_logo_still_allows_its_wordmark_name():
    """EXB has no url/sha (the worker draws a wordmark tile): its company name stays allowed."""
    assert "Example Bancorp" in tos.image_strings(ROWS_CEO, LOGOS)
    spec = copy.deepcopy(SPOTLIGHT)
    spec["header"]["name"] = "GME stock filer"          # the drawn name may differ from the logo's
    got = tos.image_strings(spec, LOGOS)
    assert got[:3] == ["FILED LAST WEEK · FORM 4", "GameStop", "GME stock filer"]   # wordmark at the tile


def test_a_url_or_hash_of_a_logo_is_never_an_allowed_string():
    for spec in list(SHIPPED_SAMPLES.values()):
        for s in tos.image_strings(spec, LOGOS):
            assert "://" not in s and SHA not in s and "a" * 32 not in s
            assert all(s != entry["url"] and s != entry["sha256"] for entry in LOGOS)


@pytest.mark.parametrize("layout", sorted(SAMPLE_PER_LAYOUT))
def test_a_layout_outside_shipped_layouts_is_refused_and_draws_nothing(layout, monkeypatch, caplog):
    """The gate itself, for every layout: drop one from SHIPPED_LAYOUTS (a rollback, or a layout the
    worker cannot draw) and its specs are refused `layout_not_shipped` and allow NOTHING (ERROR) — while
    the others still validate."""
    spec = SAMPLE_PER_LAYOUT[layout]
    assert tos.validate_image_spec(spec, KEYS) is None                       # shipped today
    monkeypatch.setattr(tos, "SHIPPED_LAYOUTS", tuple(x for x in tos.LAYOUTS if x != layout))
    assert _code(tos.validate_image_spec(spec, KEYS)) == "layout_not_shipped"
    with caplog.at_level(logging.ERROR, logger=tos.logger.name):
        assert tos.image_strings(spec, LOGOS) == []
    assert any("layout_not_shipped" in r.getMessage() for r in caplog.records)
    assert _all_layouts(spec) is None                     # the schema itself is valid
    for other, other_spec in SAMPLE_PER_LAYOUT.items():
        if other != layout:
            assert tos.validate_image_spec(other_spec, KEYS) is None, other


def test_the_pair_and_grid_walks_are_in_draw_order():
    w = tos._walk_image_spec(PAIR, KEYS, shipped=tos.LAYOUTS)
    assert tos._resolve(w.out, tos._logo_names(LOGOS)) == [
        "COMPANY STAKES", "NVIDIA", "Apple", "$777.4M", "invested", "As of Mar 27, 2026", FOOTER]
    w = tos._walk_image_spec(GRID, KEYS, shipped=tos.LAYOUTS)
    assert tos._resolve(w.out, tos._logo_names(LOGOS)) == [
        "INSIDE A THEME", "AI chips", "Largest revenue segment of each", "NVIDIA", "Data Center",
        "Broadcom", "Semiconductor solutions", "Apple", "+3 more", FOOTER]


def test_optional_keys_may_be_absent_or_null():
    spec = copy.deepcopy(ROWS_13F)
    for key in ("header", "subtitle", "notes"):
        spec[key] = None
    spec["sections"][0]["heading"] = None
    spec["sections"][0]["more"] = None
    spec["sections"][0]["rows"][0]["logo"] = None
    assert tos.validate_image_spec(spec, KEYS) is None
    assert tos.image_strings(spec, LOGOS) == ["13F SEASON", "Berkshire Hathaway", "Apple", "Microsoft",
                                              "No longer reported", "Amazon", FOOTER]
    spot = dict(SPOTLIGHT, lines=None)
    spot["header"] = dict(SPOTLIGHT["header"], chip=None)
    assert tos.validate_image_spec(spot, KEYS) is None
    assert tos.validate_image_spec({k: v for k, v in SPOTLIGHT.items() if k != "lines"}, KEYS) is None
    assert tos.validate_image_spec(dict(SPOTLIGHT, lines=[]), KEYS) is None


# ── the worst case of every layout fits the declaration cap ─────────────────


def _worst_rows():
    rows = [{"logo": k, "cells": [f"Company {k}", "Director", "$12.3M"]}
            for k in ("AAPL", "MSFT", "AMZN", "GOOGL", "META", "AVGO", "NVDA", "COST")]
    return {"layout": "rows", "version": 1, "kicker": "13F SEASON",
            "header": {"logo": "BRK-B", "name": "Berkshire Hathaway", "chip": "BRK-B"},
            "title": "Berkshire Hathaway", "subtitle": "Quarter ended Sep 30, 2026",
            "sections": [{"heading": "Newly reported", "rows": rows[:3], "more": "+1 more"},
                         {"heading": "No longer reported", "rows": rows[3:6], "more": "+2 more"},
                         {"heading": "Reported more shares", "rows": rows[6:], "more": "+3 more"}],
            "notes": ["Note one.", "Note two."], "footer": FOOTER}


def _worst_bars():
    bar = [{"label": f"Segment {i}", "value": f"${i}.0B", "ratio": i / 10, "style": "fill"} for i in range(7)]
    return dict(BARS, segments=bar, flow=[dict(b, label=f"Flow {i}") for i, b in enumerate(bar[:4])])


def _worst_grid():
    keys = sorted(KEYS)[:12]
    return dict(GRID, tiles=[{"logo": k, "name": f"Co {k}", "line": f"Segment {k}"} for k in keys])


def test_the_worst_case_of_every_layout_is_declarable():
    for spec, validate in ((_worst_rows(), tos.validate_image_spec), (_worst_bars(), tos.validate_image_spec),
                           (dict(SPOTLIGHT, lines=["a line", "another line"]), tos.validate_image_spec),
                           (_worst_grid(), tos.validate_image_spec), (PAIR, tos.validate_image_spec)):
        assert validate(spec, KEYS) is None
        w = tos._walk_image_spec(spec, KEYS, shipped=tos.LAYOUTS)
        assert w.strings + len(w.logos) <= ONSCREEN_TEXT_MAX
    assert len(tos.image_strings(_worst_rows(), LOGOS)) <= ONSCREEN_TEXT_MAX
    assert len(_worst_grid()["tiles"]) == tos.MAX_TILES == tos.MAX_LOGOS


# ── refusals ─────────────────────────────────────────────────────────────────


def _mut(spec, path, value, *, delete=False):
    """A deep copy of `spec` with the value at `path` (a tuple of keys / indexes) replaced or deleted."""
    out = copy.deepcopy(spec)
    node = out
    for step in path[:-1]:
        node = node[step]
    if delete:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return out


_REFUSALS = [
    # closed keys
    ("unknown top-level key", ROWS_CEO, ("strings",), ["x"], "unknown_key"),
    ("unknown key: a url field", SPOTLIGHT, ("url",), URL, "unknown_key"),
    ("unknown section key", ROWS_CEO, ("sections", 0, "title"), "x", "unknown_key"),
    ("unknown row key", ROWS_CEO, ("sections", 0, "rows", 0, "name"), "x", "unknown_key"),
    ("unknown header key", SPOTLIGHT, ("header", "sha256"), SHA, "unknown_key"),
    ("unknown bars header key", BARS, ("header", "colour"), "gold", "unknown_key"),   # news-v23: a chip is allowed
    ("unknown bar key", BARS, ("segments", 0, "colour"), "green", "unknown_key"),
    ("non-string key", ROWS_CEO, (1,), "x", "unknown_key"),
    # missing / null required keys
    ("missing kicker", ROWS_CEO, ("kicker",), None, "missing_key"),
    ("missing footer", BARS, ("footer",), None, "missing_key"),
    ("missing title", ROWS_CEO, ("title",), None, "missing_key"),
    ("missing figure", SPOTLIGHT, ("figure",), None, "missing_key"),
    ("missing callout", BARS, ("callout",), None, "missing_key"),
    ("missing flow", BARS, ("flow",), None, "missing_key"),
    ("missing header name", SPOTLIGHT, ("header", "name"), None, "missing_key"),
    ("missing row cells", ROWS_CEO, ("sections", 0, "rows", 0, "cells"), None, "missing_key"),
    ("missing bar ratio", BARS, ("segments", 1, "ratio"), None, "missing_key"),
    # version / layout
    ("version 2", ROWS_CEO, ("version",), 2, "bad_version"),
    ("version True", ROWS_CEO, ("version",), True, "bad_version"),
    ("version '1'", ROWS_CEO, ("version",), "1", "bad_version"),
    ("version 1.0", ROWS_CEO, ("version",), 1.0, "bad_version"),
    ("unknown layout", ROWS_CEO, ("layout",), "table", "unknown_layout"),
    ("upper-case layout", ROWS_CEO, ("layout",), "ROWS", "unknown_layout"),
    ("layout None", ROWS_CEO, ("layout",), None, "unknown_layout"),
    ("lesson layout", ROWS_CEO, ("layout",), "lesson", "unknown_layout"),
    # strings
    ("over-cap string", ROWS_CEO, ("title",), "a" * (ONSCREEN_TEXT_MAX_CHARS + 1), "bad_string"),
    ("blank string", ROWS_CEO, ("title",), "   ", "bad_string"),
    ("empty string", SPOTLIGHT, ("figure",), "", "bad_string"),
    ("padded string", SPOTLIGHT, ("figure",), " $74.4M", "bad_string"),
    ("two lines", SPOTLIGHT, ("headline",), "GameStop\nCEO", "bad_string"),
    ("a tab", SPOTLIGHT, ("headline",), "GameStop\tCEO", "bad_string"),
    ("line separator", SPOTLIGHT, ("headline",), "GameStop\u2028CEO", "bad_string"),
    ("zero-width space", SPOTLIGHT, ("header", "name"), "Game\u200bStop", "bad_string"),
    ("full-width letters", SPOTLIGHT, ("header", "name"), "\uff27ameStop", "bad_string"),
    ("control character", ROWS_CEO, ("kicker",), "FILED\x07", "bad_string"),
    ("a scheme url", SPOTLIGHT, ("lines", 0), URL, "bad_string"),
    ("a www link", SPOTLIGHT, ("lines", 0), "see www.example.com", "bad_string"),
    ("a content hash", ROWS_CEO, ("notes", 0), "sha " + "ab" * 32, "bad_string"),
    ("a number cell", ROWS_CEO, ("sections", 0, "rows", 0, "cells", 2), 74.4, "bad_string"),
    ("a null cell", ROWS_CEO, ("sections", 0, "rows", 0, "cells", 1), None, "bad_string"),
    ("a dict note", ROWS_CEO, ("notes", 0), {"text": "x"}, "bad_string"),
    ("a numeric bar value", BARS, ("segments", 0, "value"), 269.9, "bad_string"),
    ("footer not a string", ROWS_CEO, ("footer",), ["x"], "bad_string"),
    # lists and bounds
    ("sections not a list", ROWS_CEO, ("sections",), {"rows": []}, "bad_list"),
    ("cells a string", ROWS_CEO, ("sections", 0, "rows", 0, "cells"), "GameStop", "bad_list"),
    ("no section", ROWS_CEO, ("sections",), [], "bad_count"),
    ("four sections", ROWS_13F, ("sections",), [ROWS_13F["sections"][1]] * 4, "bad_count"),
    ("empty section", ROWS_CEO, ("sections", 0, "rows"), [], "bad_count"),
    # news-v23: a section holds up to six rows (the 13F list); seven is refused
    ("seven rows in a section", ROWS_CEO, ("sections", 0, "rows"), [ROWS_CEO["sections"][0]["rows"][0]] * 7,
     "bad_count"),
    ("no cell", ROWS_CEO, ("sections", 0, "rows", 0, "cells"), [], "bad_count"),
    ("four cells", ROWS_CEO, ("sections", 0, "rows", 0, "cells"), ["a", "b", "c", "d"], "bad_count"),
    ("three notes", ROWS_CEO, ("notes",), ["a", "b", "c"], "bad_count"),
    ("three lines", SPOTLIGHT, ("lines",), ["a", "b", "c"], "bad_count"),
    ("one segment", BARS, ("segments",), BARS["segments"][:1], "bad_count"),
    ("eight segments", BARS, ("segments",), BARS["segments"] * 4, "bad_count"),
    ("one flow bar", BARS, ("flow",), BARS["flow"][:1], "bad_count"),
    ("five flow bars", BARS, ("flow",), BARS["flow"] + BARS["flow"][:1], "bad_count"),
    ("nine rows in all", ROWS_13F, ("sections",),
     [{"rows": [{"cells": [str(i)]} for i in range(5)]}, {"rows": [{"cells": [str(i)]} for i in range(4)]}],
     "too_many_rows"),
    # ratios and styles
    ("NaN ratio", BARS, ("segments", 0, "ratio"), math.nan, "bad_ratio"),
    ("inf ratio", BARS, ("flow", 0, "ratio"), math.inf, "bad_ratio"),
    ("-inf ratio", BARS, ("flow", 0, "ratio"), -math.inf, "bad_ratio"),
    ("negative ratio", BARS, ("segments", 1, "ratio"), -0.01, "bad_ratio"),
    ("ratio above 1", BARS, ("segments", 1, "ratio"), 1.0001, "bad_ratio"),
    ("ratio 2", BARS, ("flow", 1, "ratio"), 2, "bad_ratio"),
    ("huge int ratio", BARS, ("flow", 1, "ratio"), 10 ** 400, "bad_ratio"),   # never an OverflowError
    ("negative zero ratio", BARS, ("flow", 1, "ratio"), -1e-300, "bad_ratio"),
    ("bool ratio", BARS, ("segments", 0, "ratio"), True, "bad_ratio"),
    ("string ratio", BARS, ("segments", 0, "ratio"), "0.5", "bad_ratio"),
    ("unknown style", BARS, ("segments", 0, "style"), "dashed", "bad_style"),
    ("a colour as style", BARS, ("flow", 3, "style"), "red", "bad_style"),
    ("null style", BARS, ("flow", 3, "style"), None, "missing_key"),
    ("upper-case style", BARS, ("flow", 3, "style"), "FILL", "bad_style"),
    # logos
    ("unknown logo key", ROWS_CEO, ("sections", 0, "rows", 0, "logo"), "XYZ", "unknown_logo"),
    ("lower-case logo key", SPOTLIGHT, ("header", "logo"), "gme", "unknown_logo"),
    ("logo key a number", BARS, ("header", "logo"), 1, "bad_logo_key"),
    ("logo key a url", SPOTLIGHT, ("header", "logo"), URL, "bad_logo_key"),
    ("logo key empty", SPOTLIGHT, ("header", "logo"), "", "bad_logo_key"),
]


@pytest.mark.parametrize("why, spec, path, value, code", _REFUSALS, ids=[r[0] for r in _REFUSALS])
def test_a_malformed_spec_is_refused(why, spec, path, value, code):
    assert tos.validate_image_spec(spec, KEYS) is None   # the base sample is valid
    bad = _mut(spec, path, value)
    problem = tos.validate_image_spec(bad, KEYS)
    assert _code(problem) == code, (why, problem)
    assert tos.image_strings(bad, LOGOS) == []           # fail closed: nothing is drawable


@pytest.mark.parametrize("key", ["layout", "version", "kicker", "footer"])
def test_a_deleted_common_key_is_refused(key):
    bad = _mut(ROWS_CEO, (key,), None, delete=True)
    assert _code(tos.validate_image_spec(bad, KEYS)) in ("missing_key", "unknown_layout")


@pytest.mark.parametrize("spec", [None, [], "rows", 1, ("rows",), b"{}"])
def test_a_spec_that_is_not_an_object_is_refused(spec):
    assert _code(tos.validate_image_spec(spec, KEYS)) == "not_an_object"
    assert tos.image_strings(spec, LOGOS) == []


_DELETE = object()
#: The `feature` schema's own malformations (2026-11-15), each refused with its code — and drawing nothing.
_FEATURE_REFUSALS = [
    ("no figure_label", ("figure_label",), _DELETE, "missing_key"),
    ("null figure_label", ("figure_label",), None, "missing_key"),
    ("blank figure_label", ("figure_label",), "  ", "bad_string"),
    ("no facts", ("facts",), _DELETE, "missing_key"),
    ("zero facts", ("facts",), [], "bad_count"),
    ("five facts", ("facts",), FEATURE["facts"] + [{"label": "Fifth", "value": "5"}], "bad_count"),
    ("facts an object", ("facts",), {"label": "a", "value": "b"}, "bad_list"),
    ("a fact without a value", ("facts", 0, "value"), _DELETE, "missing_key"),
    ("a fact with a note", ("facts", 0, "note"), "x", "unknown_key"),
    ("a fact not an object", ("facts", 1), "Shares 14K", "not_an_object"),
    ("a fact value with a link", ("facts", 2, "value"), "https://x.test", "bad_string"),
    ("a fact label on two lines", ("facts", 0, "label"), "Trade\ndates", "bad_string"),
    ("a header logo the run lacks", ("header", "logo"), "XYZ", "unknown_logo"),
    ("no header", ("header",), _DELETE, "missing_key"),
    ("a lines key (the spotlight's)", ("lines",), ["a"], "unknown_key"),
    ("a chip too", ("header", "chip"), 7, "bad_string"),
]


@pytest.mark.parametrize("why, path, value, code", _FEATURE_REFUSALS, ids=[r[0] for r in _FEATURE_REFUSALS])
def test_the_feature_schema_refuses_its_own_malformations(why, path, value, code):
    assert tos.validate_image_spec(FEATURE, KEYS) is None
    bad = _mut(FEATURE, path, None, delete=True) if value is _DELETE else _mut(FEATURE, path, value)
    problem = tos.validate_image_spec(bad, KEYS)
    assert _code(problem) == code, (why, problem)
    assert tos.image_strings(bad, LOGOS) == []


def test_the_feature_walk_is_in_draw_order_and_its_worst_case_is_declarable():
    """kicker → the header (its logo name — the band's plate — then its name and chip) → figure → label → headline →
    each fact's label then value → footer; four facts with a chip stay far under ONSCREEN_TEXT_MAX."""
    w = tos._walk_image_spec(FEATURE, KEYS, shipped=tos.LAYOUTS)
    assert w.out[:2] == ["FILED OCT 7–8 · FORM 4", ("logo", "GME")] and w.logos == ["GME"]
    assert w.strings == 1 + 2 + 3 + 2 * tos.MAX_FACTS + 1 and w.strings + len(w.logos) <= ONSCREEN_TEXT_MAX
    one = dict(FEATURE, facts=FEATURE["facts"][:tos.MIN_FACTS], header={"logo": "GME", "name": "GameStop"})
    assert tos.validate_image_spec(one, KEYS) is None
    assert tos.image_strings(one, LOGOS)[-3:] == ["Trade dates", "Oct 6–7, 2026", FOOTER]


@pytest.mark.parametrize("name", sorted(SAMPLES_2B))
def test_the_2b_schemas_refuse_their_own_malformations(name):
    spec = SAMPLES_2B[name]
    cases = {
        "pair": [(("left", "chip"), "NVDA", "unknown_key"), (("right", "logo"), "XYZ", "unknown_logo"),
                 (("label",), None, "missing_key"), (("lines",), ["a", "b", "c"], "bad_count")],
        "grid": [(("tiles",), GRID["tiles"][:2], "bad_count"),
                 (("tiles",), [GRID["tiles"][0]] * 13, "bad_count"),
                 (("tiles", 0, "logo"), "XYZ", "unknown_logo"), (("tiles", 1, "url"), URL, "unknown_key"),
                 (("more",), "", "bad_string")],
    }[name]
    for path, value, code in cases:
        assert _code(tos.validate_image_spec(_mut(spec, path, value), KEYS)) == code, (name, path)


def test_too_many_distinct_logos_is_refused(monkeypatch):
    """Unreachable within today's bounds (rows: 1 + 8, grid: 12) — a guard for a future cap change."""
    monkeypatch.setattr(tos, "MAX_LOGOS", 2)
    assert _code(tos.validate_image_spec(ROWS_CEO, KEYS)) == "too_many_logos"
    assert tos.validate_image_spec(SPOTLIGHT, KEYS) is None   # one logo is fine
    assert tos.image_strings(ROWS_CEO, LOGOS) == []


def test_too_many_strings_is_refused(monkeypatch):
    """Unreachable within today's bounds (worst case ≈ 47 of 64) — a guard for a future cap change.
    The logo names count: each may be drawn as a wordmark."""
    w = tos._walk_image_spec(ROWS_CEO, KEYS, shipped=tos.LAYOUTS)
    assert (w.strings, len(w.logos)) == (14, 3)
    monkeypatch.setattr(tos, "ONSCREEN_TEXT_MAX", 16)
    assert _code(tos.validate_image_spec(ROWS_CEO, KEYS)) == "too_many_strings"
    monkeypatch.setattr(tos, "ONSCREEN_TEXT_MAX", 17)
    assert tos.validate_image_spec(ROWS_CEO, KEYS) is None


def test_a_footer_other_than_the_outputs_is_refused():
    assert tos.validate_image_spec(ROWS_CEO, KEYS, footer=FOOTER) is None
    other = post_copy.image_footer(date(2026, 10, 12), "template", source=post_copy.FOOTER_SOURCE_13F,
                                   when="filed Oct 9, 2026")
    assert _code(tos.validate_image_spec(ROWS_CEO, KEYS, footer=other)) == "footer_mismatch"
    ai_footer = post_copy.image_footer(date(2026, 10, 12))
    assert _code(tos.validate_image_spec(ROWS_CEO, KEYS, footer=ai_footer)) == "footer_mismatch"


@pytest.mark.parametrize("keys", [None, "GME", b"GME", 5, [], frozenset(), [1, 2], {"EXB": 1}])
def test_logo_keys_that_are_not_a_collection_of_strings_allow_no_logo(keys):
    """A bare string is not a key set ("GME" is not {"G", "M", "E"}); junk allows no logo at all."""
    assert _code(tos.validate_image_spec(SPOTLIGHT, keys)) == "unknown_logo"
    assert tos.validate_image_spec(ROWS_EARNINGS_NO_LOGO, keys) is None
    # A one-letter key never matches a character of a bare-string key set.
    one_letter = dict(SPOTLIGHT, header=dict(SPOTLIGHT["header"], logo="G"))
    assert _code(tos.validate_image_spec(one_letter, "GME")) == "unknown_logo"
    assert tos.validate_image_spec(one_letter, ["G"]) is None



def test_problems_name_the_path():
    bad = _mut(ROWS_13F, ("sections", 1, "rows", 0, "cells", 0), "")
    assert tos.validate_image_spec(bad, KEYS) == "bad_string: sections[1].rows[0].cells[0] (blank)"
    bad = _mut(BARS, ("flow", 2, "ratio"), math.nan)
    assert tos.validate_image_spec(bad, KEYS).startswith("bad_ratio: flow[2].ratio")


def test_an_invalid_spec_logs_an_error_and_allows_nothing(caplog):
    caplog.set_level(logging.ERROR, logger=tos.__name__)
    assert tos.image_strings(_mut(BARS, ("segments", 0, "ratio"), math.nan), LOGOS) == []
    assert any("image_spec refused" in r.getMessage() and "bad_ratio" in r.getMessage()
               for r in caplog.records)


# ── logos ────────────────────────────────────────────────────────────────────


def test_logo_keys_reads_only_well_formed_entries():
    logos = [
        _logo("GME", "GameStop"),
        _logo("GME", "Another Name"),            # a duplicate key: the first entry wins
        {"key": "EXB"},                           # no name: no wordmark, so not referencable
        {"key": "SMR", "name": "   "},            # blank name
        {"key": "COST", "name": URL},             # a url is never a drawable name
        {"key": "NVDA", "name": "NVIDIA\n"},      # two lines
        {"key": 7, "name": "Seven"},              # non-string key
        {"key": "", "name": "Empty"},             # empty key
        {"key": "K" * 17, "name": "Too long"},    # key over the bound
        "AAPL", None, ["MSFT", "Microsoft"],      # not objects
        _logo("AMZN", "Amazon", url=None, sha=None),   # a missing image is still a wordmark entry
    ]
    assert tos.logo_keys(logos) == frozenset({"GME", "AMZN"})
    assert tos._logo_names(logos)["GME"] == "GameStop"
    for junk in (None, "GME", {"key": "GME", "name": "GameStop"}, 5):
        assert tos.logo_keys(junk) == frozenset()


def test_a_reference_to_a_malformed_logo_entry_allows_nothing():
    logos = [dict(entry, name="") if entry["key"] == "GME" else entry for entry in LOGOS]
    assert _code(tos.validate_image_spec(SPOTLIGHT, tos.logo_keys(logos))) == "unknown_logo"
    assert tos.image_strings(SPOTLIGHT, logos) == []
    assert tos.opening_strings(OPENING, logos) == []


# ── opening card ─────────────────────────────────────────────────────────────


def test_the_opening_card_is_valid_and_its_strings_are_pinned():
    assert tos.validate_opening_card(OPENING, KEYS) is None
    assert tos.opening_strings(OPENING, LOGOS) == [
        "FILED LAST WEEK · FORM 4", "GameStop", "GME", "$74.4M", "GameStop's CEO disclosed buying GameStop stock"]
    pair = {"kicker": "COMPANY STAKES", "logos": ["NVDA", "EXB"], "headline": "NVIDIA holds a stake"}
    assert tos.validate_opening_card(pair, KEYS) is None
    assert tos.opening_strings(pair, LOGOS) == ["COMPANY STAKES", "NVIDIA", "Example Bancorp",
                                                "NVIDIA holds a stake"]   # both wordmarks, in order
    minimal = {"kicker": "MONEY MAP", "logos": ["COST"], "chip": None, "figure": None,
               "headline": "How Costco makes money"}
    assert tos.validate_opening_card(minimal, KEYS) is None
    assert tos.opening_strings(minimal, LOGOS) == ["MONEY MAP", "Costco", "How Costco makes money"]
    for strings in (tos.opening_strings(OPENING, LOGOS), tos.opening_strings(pair, LOGOS)):
        validate_onscreen_text(strings)


def test_the_opening_strings_are_deterministic():
    want = tos.opening_strings(OPENING, LOGOS)
    round_trip = json.loads(json.dumps({k: OPENING[k] for k in reversed(list(OPENING))}))
    assert tos.opening_strings(round_trip, LOGOS) == want
    assert tos.opening_strings(dict(OPENING, logos=("GME",)), list(reversed(LOGOS))) == want


_OPENING_REFUSALS = [
    ("no logo", ("logos",), [], "bad_count"),
    ("three logos", ("logos",), ["GME", "EXB", "SMR"], "bad_count"),
    ("duplicate logo", ("logos",), ["GME", "GME"], "duplicate_logo"),
    ("unknown logo", ("logos",), ["XYZ"], "unknown_logo"),
    ("logo not a string", ("logos",), [1], "bad_logo_key"),
    ("logos a string", ("logos",), "GME", "bad_list"),
    ("missing kicker", ("kicker",), None, "missing_key"),
    ("missing headline", ("headline",), None, "missing_key"),
    ("unknown key", ("title",), "x", "unknown_key"),
    ("a person slot", ("person",), "Ryan Cohen", "unknown_key"),
    ("a logo url", ("logo_url",), URL, "unknown_key"),
    ("blank chip", ("chip",), " ", "bad_string"),
    # Batch 1 (2026-10-15): a chip one character over OPENING_CHIP_MAX_CHARS (a period chip is at most 26)
    ("over-cap chip", ("chip",), "Committed up to · as of Sept 18, 2025", "chip_too_long"),
    ("numeric chip", ("chip",), 2026, "bad_string"),
    ("numeric figure", ("figure",), 74.4, "bad_string"),
    ("two-line headline", ("headline",), "GameStop\nCEO", "bad_string"),
    ("url headline", ("headline",), URL, "bad_string"),
    ("over-cap headline", ("headline",), "a" * (ONSCREEN_TEXT_MAX_CHARS + 1), "bad_string"),
]


@pytest.mark.parametrize("why, path, value, code", _OPENING_REFUSALS, ids=[r[0] for r in _OPENING_REFUSALS])
def test_a_malformed_opening_card_is_refused(why, path, value, code):
    bad = _mut(OPENING, path, value)
    assert _code(tos.validate_opening_card(bad, KEYS)) == code, why
    assert tos.opening_strings(bad, LOGOS) == []


def test_the_chip_cap_is_exact():
    """Batch 1 (2026-10-15): OPENING_CHIP_MAX_CHARS is the server's bound on the chip — the worker lays out
    whatever it is given, so a longer chip (a wrap that could push the cover to CardOverflow, a skipped day)
    must never reach it. At the cap a chip validates and is drawable; one character over, the card allows
    nothing. Every period chip a template writes (at most 26 characters) is far under it. Mutation-checked by
    hand (2026-10-15): with the check removed from `_walk_opening`, the 37-character row turned red."""
    at_cap = "Committed up to · as of Nov 18, 2025"
    assert len(at_cap) == tos.OPENING_CHIP_MAX_CHARS
    card = dict(OPENING, chip=at_cap)
    assert tos.validate_opening_card(card, KEYS) is None
    assert at_cap in tos.opening_strings(card, LOGOS)
    over = dict(OPENING, chip=at_cap.replace("Nov", "Sept"))
    assert len(over["chip"]) == tos.OPENING_CHIP_MAX_CHARS + 1
    assert _code(tos.validate_opening_card(over, KEYS)) == "chip_too_long"
    assert tos.opening_strings(over, LOGOS) == []
    for chip in ("Quarter ended Sep 30, 2026", "Reported Nov 10, 2026", "Fiscal 2025", "BRK-B"):
        assert tos.validate_opening_card(dict(OPENING, chip=chip), KEYS) is None, chip
    # an absent or null chip is still optional (a theme cover draws none)
    assert tos.validate_opening_card({k: v for k, v in OPENING.items() if k != "chip"}, KEYS) is None
    assert tos.validate_opening_card(dict(OPENING, chip=None), KEYS) is None


@pytest.mark.parametrize("card", [None, [], "card", 3])
def test_an_opening_card_that_is_not_an_object_is_refused(card, caplog):
    caplog.set_level(logging.ERROR, logger=tos.__name__)
    assert _code(tos.validate_opening_card(card, KEYS)) == "not_an_object"
    assert tos.opening_strings(card, LOGOS) == []
    assert any("opening_card refused" in r.getMessage() for r in caplog.records)


# ── drawable strings ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("value, ok", [
    ("GameStop", True), ("$74.4M", True), ("FILED LAST WEEK · FORM 4", True), ("Filed Oct 5–9, 2026", True),
    ("Amazon.com", True),                       # a company name with a dot is not a link
    ("x" * ONSCREEN_TEXT_MAX_CHARS, True), ("x" * (ONSCREEN_TEXT_MAX_CHARS + 1), False),
    ("", False), (" ", False), (" a", False), ("a ", False), ("a\nb", False), ("a\rb", False),
    ("a\x85b", False), ("a\u00adb", False), ("a\u200db", False), ("\ufb01nance", False),   # ligature: not NFKC
    ("https://caydexinvest.com/go/x", False), ("ftp://x", False), ("WWW.EXAMPLE.COM", False),
    ("ab" * 16, False), ("deadbeef" * 3, True),   # 32 hex is a hash; 24 is not
    (None, False), (1, False), (b"bytes", False), (["a"], False),
])
def test_drawable_problem(value, ok):
    assert (tos.drawable_problem(value) is None) is ok, value


def test_the_template_footer_is_drawable():
    """The post_copy template footer (D8; one short line per family since news-v23) is the spec's footer: it must
    pass the same string rules."""
    for source, when in ((post_copy.FOOTER_SOURCE_FORM4, "filed Oct 5–9, 2026"),
                         (post_copy.FOOTER_SOURCE_13F, "amended Nov 14, 2026"),
                         (post_copy.FOOTER_SOURCE_STATEMENTS, "fiscal 2025"),
                         (post_copy.FOOTER_SOURCE_CONGRESS, "November 2026"),
                         (post_copy.FOOTER_SOURCE_EARNINGS, None), (post_copy.FOOTER_SOURCE_FILINGS, None),
                         (post_copy.FOOTER_SOURCE_FILINGS_MARKET, None)):
        footer = post_copy.image_footer(date(2026, 11, 16), "template", source=source, when=when)
        assert tos.drawable_problem(footer) is None
        assert tos.validate_image_spec(dict(ROWS_CEO, footer=footer), KEYS, footer=footer) is None


# ── the data card's slot (Batch 2 fix CARDS-B2-1, 2026-10-20) ─────────────────
#
# A template card stays on screen for its whole narration line, so since news-v12 each one carries its own
# line's hedges. The data card (the day's post image drawn in the video) replaces ONE content card — the
# read-back's `data_card_slot` — and that card must be a FACT card whose facts the picture already shows:
# the shipped worker replaced the LAST card, which for Congress, 13F, earnings, stakes and Form 4 days is
# exactly the hedge the lexicon put on screen on purpose.

#: The cards a data card never replaces, by their words (DATA_CARD_KEEPS read through the lexicon).
_INSIDER_KEPT = ["Held indirectly", "Partly held indirectly", "Amended filing", "What a Form 4 is", "Trade date",
                 "Trade dates", "How the amount is counted", "Same week"]
KEPT_TITLES = {
    "ceo_buys": _INSIDER_KEPT, "insider_buys": _INSIDER_KEPT,
    # news-v23: the 13F cover IS the list — never a data card (NO_DATA_CARD_SERIES): nothing to keep
    "thirteen_f": [],
    "money_map": ["What revenue is"],
    # news-v23: their covers ARE their lists — never a data card (NO_DATA_CARD_SERIES): nothing to keep
    "congress_count": [],
    "earnings": [],
    # news-v23: one investor's stakes — the cover IS the list, never a data card (NO_DATA_CARD_SERIES)
    "company_stakes": [],
    # news-v23 phase 3: the cover IS the theme's list — never a data card (NO_DATA_CARD_SERIES)
    "theme_explainer": [],
    # 2026-11-15: never a data card (NO_DATA_CARD_SERIES) — nothing to keep
    "insider_spotlight": [], "thirteen_f_spotlight": [],
}
#: The cards it replaces, by their words, in preference order (a theme member card's title is its names).
SLOT_TITLES = {
    "ceo_buys": [["The purchase", "The purchases"]], "insider_buys": [["The purchase", "The purchases"]],
    "thirteen_f": [],
    "money_map": [["Revenue mix"]],
    "congress_count": [],
    "earnings": [],
    "company_stakes": [],
    "theme_explainer": [],
    # 2026-11-15: never a data card — owner decision 5 keeps the filing's cost per share off every video surface
    "insider_spotlight": [], "thirteen_f_spotlight": [],
}


@pytest.fixture
def _fresh_slot_rules():
    """`_slot_rules` caches what it reads from the lexicon: a test that edits the tables starts and ends clean."""
    tos._slot_rules.cache_clear()
    yield
    tos._slot_rules.cache_clear()


def test_the_slot_tables_are_pinned_by_their_words_and_never_overlap():
    from app.services.marketing import news_templates as T

    # every series with a template has an entry; the two spotlight series' are EMPTY (news-v22: shipped, no slot)
    assert set(tos.DATA_CARD_SLOTS) == set(tos.DATA_CARD_KEEPS) == set(T.SERIES_SPECS)
    assert set(tos.NO_DATA_CARD_SERIES) <= set(T.SERIES_SPECS)
    assert {s: [T.LEXICON[i] for i in ids] for s, ids in tos.DATA_CARD_KEEPS.items()} == KEPT_TITLES
    assert {s: [[T.LEXICON[i] for i in g] for g in groups]
            for s, groups in tos.DATA_CARD_SLOTS.items()} == SLOT_TITLES
    for series, groups in tos.DATA_CARD_SLOTS.items():
        slot_ids = {i for g in groups for i in g}
        assert not slot_ids & set(tos.DATA_CARD_KEEPS[series]), series
        assert not {T.LEXICON[i] for i in slot_ids} & set(KEPT_TITLES[series]), series
        assert all("{" not in T.LEXICON[i] for i in tos.DATA_CARD_KEEPS[series]), series   # matched exactly


def _composed_variants():
    """(name, composed output) for every sample the templates' own tests compose — every series and the
    variants that change a series' cards — plus the card variants those samples do not reach."""
    import test_marketing_news_templates as NT

    extra = [
        ("ceo_spotlight_plain", lambda: NT.week(NT.buy("NKE", "Nike", "ceo", None, 1_250_000.0, 18_500.0, 1,
                                                       [date(2026, 11, 10)])), NT.MON),
        ("ceo_spotlight_amended", lambda: NT.week(NT.SBUX), NT.MON),
        ("ceo_spotlight_partly_indirect", lambda: NT.week(NT.buy(
            "CTSO", "Contoso", "ceo", None, 640_000.0, 2_600.0, 3, [date(2026, 11, 12), date(2026, 11, 13)],
            holding="mixed")), NT.MON),
        ("insider_late_one_date", lambda: NT.week(NT.late(traded=(date(2026, 10, 14),)), series="insider_buys"),
         NT.MON),
        ("insider_roundup_late_other", lambda: NT.week(
            NT.buy("NKE", "Nike", "cfo", None, 1_250_000.0, 18_500.0, 1, [date(2026, 11, 10)]), NT.late(),
            series="insider_buys"), NT.MON),
        ("thirteen_f_one_kind", lambda: NT.berkshire(
            moves=tuple(m for m in NT.berkshire().moves if m.move == "newly_reported"),
            counts=(("newly_reported", 3),)), NT.TUE),
        ("money_map_no_costs", lambda: NT.costco(gross_profit_usd=None, operating_profit_usd=None), NT.THU),
        ("earnings_reported_only", lambda: NT.earnings(period_end=None), NT.EARNINGS_RUN),
        ("theme_two_groups", lambda: NT.theme(rows=[
            ("NVDA", "NVIDIA", "Data Center", 0.88, "2025"), ("AMD", "AMD", "Data Center", 0.48, "2025"),
            ("QCOM", "Qualcomm", "QCT", 0.84, "2025"), ("ARM", "Arm Holdings", None, None, None),
            ("INTC", "Intel", None, None, None), ("KLAC", "KLA", None, None, None)], theme_size=6), NT.THEME_RUN),
    ]
    return [(name, NT.compose(factory(), day)) for name, factory, day in [*NT.SAMPLES, *extra]]


#: Every composed variant of a series that HAS a data card (the two spotlight series never do: NO_DATA_CARD_SERIES;
#: their composed samples — news-v22 — are checked by test_a_spotlight_series_never_has_a_data_card_slot).
_ALL_VARIANTS = _composed_variants()
_VARIANTS = [(n, out) for n, out in _ALL_VARIANTS if out["series"] not in tos.NO_DATA_CARD_SERIES]


def _picture(out):
    return tos.image_strings(out["image_spec"], out["logo_refs"])


def _shows_purchase(card, out, picture):
    *count, amount = card["body"].split(" · ")          # count · shares · amount
    assert amount in picture                             # the spotlight's figure, or the lead row's cell
    if out["image_spec"]["layout"] == "spotlight":
        assert " · ".join(count) in picture              # the spotlight's count line too


def _shows_moves(card, out, picture):
    (section,) = [s for s in out["image_spec"]["sections"] if s.get("heading") == card["title"]]
    names = [row["cells"][0] for row in section["rows"]]
    assert names and all(n in card["body"] and n in picture for n in names)


def _shows_mix(card, out, picture):
    labels = [piece.split(": ")[0] for piece in card["body"].split(" · ")]
    assert len(labels) == 2 and all(label in picture for label in labels)        # the bars' own labels


def _shows_as_of(card, out, picture):
    assert f"{card['title']} {card['body']}" in picture                                    # "As of {date}"


def _shows_members(card, out, picture):
    from app.services.marketing import news_templates as T

    tiles = {t["name"]: t.get("line") for t in out["image_spec"]["tiles"]}
    pairs = {T.LEXICON["th.c.pair.title"].format(m1=a, m2=b): (a, b) for a in tiles for b in tiles if a != b}
    members = pairs.get(card["title"]) or ((card["title"],) if card["title"] in tiles else ())
    assert members and all(m in picture for m in members)
    for segment in card["body"].split(": ", 1)[1].split(" · "):                       # each member's line
        assert any(line in (segment, None) or line.startswith(f"{segment} · ") for line in
                   (tiles[m] for m in members)), segment


_FACTS_SHOWN = {"ceo_buys": _shows_purchase, "insider_buys": _shows_purchase, "thirteen_f": _shows_moves,
                "money_map": _shows_mix, "theme_explainer": _shows_members}


def test_the_variants_reach_every_series_and_every_kind_of_card():
    assert {out["series"] for _n, out in _VARIANTS} == set(tos.DATA_CARD_SLOTS) - set(tos.NO_DATA_CARD_SERIES)
    seen = {c["title"] for _n, out in _VARIANTS for c in out["cards"]}
    # every kept card a composition can carry is reached, so the next test proves each one stays — except the lone row's
    # "How the amount is counted": owner decisions 2026-11-20 ("3 - 5 companies at once") left no one-company roundup,
    # so card 4 is always the "Same week" lower bound; the keep stays (a guard that costs nothing), and the insider list
    # (never a data card) still composes that card
    lone_only = {"How the amount is counted"}
    assert set().union(*KEPT_TITLES.values()) - lone_only <= seen and not lone_only & seen
    assert lone_only <= {c["title"] for _n, out in _ALL_VARIANTS if out["series"] == "insider_spotlight"
                         for c in out["cards"]}
    # news-v23: earnings, the Congress Count and company stakes have no data card at all (their covers are their lists)
    assert not {out["series"] for _n, out in _VARIANTS} & {"earnings", "congress_count", "company_stakes",
                                                           "theme_explainer"}


@pytest.mark.parametrize("name, out", _VARIANTS, ids=[n for n, _ in _VARIANTS])
def test_every_template_replaces_a_fact_card_the_picture_shows_never_a_hedge(name, out):
    series, cards, picture = out["series"], out["cards"], _picture(out)
    slot = tos.data_card_slot(series, cards, picture)
    assert type(slot) is int and 0 <= slot < len(cards), slot
    card = cards[slot]
    assert card["title"] not in KEPT_TITLES[series]
    # every hedge, caveat, explainer and source card of the composition stays a text card
    assert all(i != slot for i, c in enumerate(cards) if c["title"] in KEPT_TITLES[series])
    if series != "theme_explainer":
        assert card["title"] in {t for group in SLOT_TITLES[series] for t in group}
    _FACTS_SHOWN[series](card, out, picture)
    # deterministic, and the same after the JSONB round trip the stored output takes
    assert tos.data_card_slot(series, json.loads(json.dumps(cards)), list(reversed(picture))) == slot


def test_the_picks_per_series_are_the_decided_cards():
    """The decision (2026-10-20), by card position — for reading, the titles decide (a variant moves them). news-v23:
    earnings and the Congress Count keep no slot (NO_DATA_CARD_SERIES)."""
    picks = {name: tos.data_card_slot(out["series"], out["cards"], _picture(out)) for name, out in _VARIANTS}
    # news-v23 phase 3: no theme variant keeps a slot any more (its cover is its list)
    assert {n: p for n, p in picks.items() if p != 1} == {}


def test_a_theme_card_is_never_the_slot_since_the_cover_is_the_list():
    """news-v23 phase 3 (the weekly theme post): the video's cover is the theme's list (its picture's first companies,
    the 13F cover's idea), so no card is ever the data card — a member card whose members the grid draws included
    (before phase 3 that card was the slot)."""
    import test_marketing_news_templates as NT

    out = NT.compose(NT.theme(theme_size=14), NT.THEME_RUN)
    picture = _picture(out)
    assert out["opening_card"].get("list") is not None
    assert tos.data_card_slot("theme_explainer", out["cards"], picture) is None
    assert tos.data_card_slot("theme_explainer", [{"title": "NVIDIA", "body": "x"}], ["NVIDIA"]) is None


@pytest.mark.parametrize("cards, slot", [
    ([{"title": "The purchases", "body": "2 purchases · 3M shares · $74.4M"}], 0),
    ([{"title": "Who filed", "body": "x"}, {"title": "The purchase", "body": "y"},
      {"title": "The purchases", "body": "z"}], 1),                                     # the first of its titles
    ([{"title": "The purchases", "body": None}, {"title": "The purchase", "body": "y"}], 1),   # not a text card
    ([{"title": "The purchases"}], None),
    ([{"title": "  ", "body": "y"}], None),
    ([{"title": 7, "body": "y"}], None),
    (["The purchases"], None),
    ([{"title": "The Purchases", "body": "y"}], None),                                 # exact words only
    ([{"title": "The purchases ", "body": "y"}], None),
    ([], None), (None, None), ("The purchases", None), ({"title": "The purchases", "body": "y"}, None),
])
def test_a_slot_is_a_well_formed_text_card_of_the_series_titles(cards, slot):
    assert tos.data_card_slot("ceo_buys", cards, []) == slot


@pytest.mark.parametrize("series", [None, "", "lesson", "checklist", "Ceo_buys", 7, ("ceo_buys",)])
def test_a_series_the_table_does_not_name_has_no_slot(series):
    assert tos.data_card_slot(series, [{"title": "The purchases", "body": "y"}], []) is None


@pytest.mark.parametrize("picture", [None, "Revenue mix", 3, {"x": 1}])
def test_a_static_title_needs_no_picture_but_a_member_card_does(picture):
    assert tos.data_card_slot("money_map", [{"title": "Revenue mix", "body": "Retail: 57% · Services: 14%"}],
                              picture) == 0
    assert tos.data_card_slot("theme_explainer", [{"title": "NVIDIA", "body": "x"}], picture) is None


def test_a_kept_card_is_never_the_slot_even_when_the_table_names_it(monkeypatch, _fresh_slot_rules):
    """Mutation-style guard: a table edited to name a hedge card selects nothing — the kept check runs first."""
    # news-v23: the Congress Count and company stakes keep no slot any more — a Money Map (its "Revenue mix" card the
    # slot, its "What revenue is" card kept)
    out = next(o for n, o in _VARIANTS if n == "costco_money_map")
    assert tos.data_card_slot("money_map", out["cards"], _picture(out)) == 1
    monkeypatch.setitem(tos.DATA_CARD_SLOTS, "money_map", (("mm.c3.none.title",),))
    tos._slot_rules.cache_clear()
    assert tos.data_card_slot("money_map", out["cards"], _picture(out)) is None


def test_a_table_id_the_lexicon_lacks_is_no_slot_and_an_error(monkeypatch, caplog, _fresh_slot_rules):
    monkeypatch.setitem(tos.DATA_CARD_SLOTS, "money_map", (("mm.c9.title",),))
    tos._slot_rules.cache_clear()
    with caplog.at_level(logging.ERROR, logger=tos.logger.name):
        assert tos.data_card_slot("money_map", [{"title": "Revenue mix", "body": "y"}], []) is None
    assert any("mm.c9.title" in r.getMessage() and "money_map" in r.getMessage() for r in caplog.records)


def test_the_slot_reader_never_imports_the_lexicon_at_module_import():
    """news_templates imports this module; the slot table reads its lexicon only when called (no cycle)."""
    src = (BACKEND / "app" / "services" / "marketing" / "template_onscreen.py").read_text(encoding="utf-8")
    top_level = [line for line in src.splitlines() if line.startswith(("import ", "from "))]
    assert not [line for line in top_level if "news_templates" in line]


# ── purity ───────────────────────────────────────────────────────────────────


def test_the_module_is_pure_in_a_fresh_interpreter():
    """FMP-free and I/O-free: importing it loads no integration, no Supabase client, no agent."""
    code = ("import sys; import app.services.marketing.template_onscreen; "
            "bad = sorted(m for m in sys.modules if m.startswith(('app.integrations', 'app.services.agents', "
            "'app.database', 'supabase', 'httpx'))); print(','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(BACKEND), capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "", out.stdout


# ── 2026-11-15: the spotlight series never have a data card ──────────────────────────────────────────────────


def test_a_spotlight_series_never_has_a_data_card_slot():
    """Owner decision 5 (2026-11-15, "Captions + picture"): the filing's cost per share is shown in captions and
    on the post picture ONLY — never spoken, never on a burned caption, never on a video card. The insider
    spotlight's picture carries it among its facts, so it can NEVER be the video's data card; the 13F spotlight
    shares its `feature` layout, which is never drawn as a data card either (DATA_CARD_LAYOUTS). Their table
    entries are therefore EMPTY: no card is ever the slot — not even one titled exactly like another series' slot,
    and not any card of a composed spotlight output (news-v22: every spotlight sample of the templates' suite)."""
    # news-v23 (the restyle): both 13F series too — their video's cover IS the picture's list
    # news-v23: the 13F lists, the Congress Count and earnings — their covers are their lists
    # news-v23: and one investor's stakes in public companies — its cover is its list too
    # news-v23 phase 3: and the weekly theme post — its cover is its list (the picture's first companies)
    assert tos.NO_DATA_CARD_SERIES == ("insider_spotlight", "thirteen_f_spotlight", "thirteen_f", "congress_count",
                                       "earnings", "company_stakes", "theme_explainer")
    from app.services.marketing import selection

    assert set(tos.NO_DATA_CARD_SERIES) <= set(selection.SERIES_BY_ID)
    for series in tos.NO_DATA_CARD_SERIES:
        assert tos.DATA_CARD_SLOTS[series] == () and tos.DATA_CARD_KEEPS[series] == ()
        every_title = [{"title": t, "body": "x"} for groups in SLOT_TITLES.values() for g in groups for t in g]
        assert tos.data_card_slot(series, every_title, ["anything", "Revenue"]) is None
    from app.services.marketing import company_news_rules as rules
    from app.services.marketing import news_templates as nt

    composed = [(n, out) for n, out in _ALL_VARIANTS if out["series"] in tos.NO_DATA_CARD_SERIES]
    assert {out["series"] for _n, out in composed} == set(tos.NO_DATA_CARD_SERIES)       # not vacuous
    theme_angles = set()
    for name, out in composed:
        assert tos.data_card_slot(out["series"], out["cards"], _picture(out)) is None, name
        # both 13F series draw the `rows` list — a DATA_CARD_LAYOUTS member — and still have no slot: the series'
        # table entry is empty, so the video keeps every text card (its cover is the list)
        if out["series"] == "theme_explainer":
            # fix round 2026-11-20: a theme post's layout is its ANGLE's (`THEME_LAYOUT_OF_ANGLE`), read from its own
            # ledger key (`R.theme_ref_items`; a pre-angle key counts as "make_money") — not guessed from `rows`
            # (the figures / ledger / tally tables carry rows too, and the guess stopped the loop half way)
            (_slug, angle, _day) = rules.theme_ref_items(out["source_ref"])
            theme_angles.add(angle)
            assert out["image_spec"]["layout"] == nt.THEME_LAYOUT_OF_ANGLE[angle], name
            continue
        # owner decisions 2026-11-20: the insider spotlight is a `rows` list too (the `feature` picture is retired)
        assert out["image_spec"]["layout"] == "rows", name
    assert theme_angles == set(rules.THEME_BUILT_ANGLES)                                 # every angle's sample checked
    assert any(n.startswith("f13s_list") for n, _out in composed)
    # and the picture's layout is never a data card: `feature` is outside DATA_CARD_LAYOUTS
    assert "feature" in tos.SHIPPED_LAYOUTS and "feature" not in tos.DATA_CARD_LAYOUTS
    assert set(tos.DATA_CARD_LAYOUTS) == set(tos.LAYOUTS) - {"feature", "ranked", "figures", "ledger", "tally"}


# ── per-post art: the label and the declaration (workstream C, owner decisions 2026-11-15) ─────────────────────────

ART_SHA = "ab" * 32
OTHER_SHA = "cd" * 32
L = tos.ART_LABEL


def test_the_art_label_is_code_owned_drawable_and_names_no_model():
    assert L == "AI illustration"
    assert tos.drawable_problem(L) is None and validate_onscreen_text([L]) is None
    for word in ("gemini", "google", "claude", "anthropic", "openai", "breaking", "alert"):
        assert word not in L.lower()
    assert all(ord(ch) < 128 for ch in L)                                     # no emoji (a tofu box on a card)
    assert tos.SHA256_RE.fullmatch(ART_SHA) and not tos.SHA256_RE.fullmatch(ART_SHA.upper())


@pytest.mark.parametrize("declared, accepted, drawn, problem", [
    # art is NEVER required: no declaration, no label → fine, whatever the output carries
    (None, None, [], None),
    (None, ART_SHA, ["Contoso"], None),
    # the accepted art, declared and labelled
    (ART_SHA, ART_SHA, ["FORM 4", L, "Contoso"], None),
    (ART_SHA, ART_SHA, (L,), None),
    # the label and the declaration come together
    (ART_SHA, ART_SHA, ["FORM 4"], "art_label_missing"),
    (ART_SHA, ART_SHA, [], "art_label_missing"),
    (ART_SHA, ART_SHA, "AI illustration", "art_label_missing"),               # not a list of strings: no label
    (ART_SHA, ART_SHA, ["ai illustration"], "art_label_missing"),             # exactly the label, never a variant
    (None, ART_SHA, [L], "art_label_without_art"),
    (None, None, [L], "art_label_without_art"),
    # another picture than the accepted one
    (OTHER_SHA, ART_SHA, [L], "art_mismatch"),
    # art where the output has none (a lesson, a failed record, art off) — any art on a lesson lands here
    (ART_SHA, None, [L], "art_not_accepted"),
    (ART_SHA, "not-a-sha", [L], "art_not_accepted"),                          # a broken accepted sha counts as none
    # a malformed declaration, whatever else
    (ART_SHA.upper(), ART_SHA, [L], "art_declaration_invalid"),
    ("ab" * 31, ART_SHA, [L], "art_declaration_invalid"),
    ("", ART_SHA, [L], "art_declaration_invalid"),
    (1, ART_SHA, [L], "art_declaration_invalid"),
    (["ab" * 32], ART_SHA, [L], "art_declaration_invalid"),
    (ART_SHA + "\n", ART_SHA, [L], "art_declaration_invalid"),
])
def test_the_art_declaration_matrix(declared, accepted, drawn, problem):
    got = tos.art_declaration_problem(declared, accepted, drawn)
    assert got == problem
    assert got is None or got in tos.ART_DECLARATION_PROBLEMS


def test_every_declaration_problem_is_reachable():
    reached = {tos.art_declaration_problem(d, a, dr) for d, a, dr in (
        ("x", ART_SHA, [L]), (ART_SHA, None, [L]), (OTHER_SHA, ART_SHA, [L]), (ART_SHA, ART_SHA, []), (None, None, [L]))}
    assert reached == set(tos.ART_DECLARATION_PROBLEMS)


@pytest.mark.parametrize("declared, allowed", [
    (ART_SHA, [L]), (None, []), ("", []), (ART_SHA.upper(), []), ("ab" * 31, []), (1, []),
])
def test_art_allowed_adds_the_label_only_for_a_declared_sha(declared, allowed):
    assert tos.art_allowed(declared) == allowed


def test_the_closed_walks_never_allow_the_art_label():
    """The art carries no text: the label is allowed ONLY through `art_allowed` (a declaration), never by the image or
    opening-card walks — over every composed variant of every series."""
    assert len(_ALL_VARIANTS) >= 30
    for name, out in _ALL_VARIANTS:
        assert L not in tos.image_strings(out["image_spec"], out["logo_refs"]), name
        assert L not in tos.opening_strings(out["opening_card"], out["logo_refs"]), name


# ── news-v23: the 13F LIST cover on the opening card (cover_13f_berkshire.jpg) ─────────────────────────────────────

_LIST_COVER = {"kicker": "13F FILING", "logos": ["BRK-B"], "chip": "BRK-B", "headline": "Berkshire Hathaway's latest 13F",
               "list": {"heading": "Change in shares",
                        "rows": [{"logo": "AAPL", "chip": "AAPL", "cells": ["Apple", "New holding", "+5.6M"]},
                                 {"logo": "MSFT", "chip": "MSFT", "cells": ["Microsoft", "Closed out", "\u221214.6M"]}],
                        "more": "+1 more change in the caption"}}


def test_a_list_cover_validates_and_allows_exactly_its_strings_in_draw_order():
    """The opening card's `list` (news-v23) is walked after the headline: its heading, each row's logo name, chip and
    cells, then its "+N more" line — every one allowed on the video, nothing else."""
    keys = {"BRK-B", "AAPL", "MSFT"}
    assert tos.validate_opening_card(_LIST_COVER, keys) is None
    logos = [{"key": "BRK-B", "name": "Berkshire Hathaway"}, {"key": "AAPL", "name": "Apple"},
             {"key": "MSFT", "name": "Microsoft"}]
    assert tos.opening_strings(_LIST_COVER, logos) == [
        "13F FILING", "Berkshire Hathaway", "BRK-B", "Berkshire Hathaway's latest 13F", "Change in shares", "Apple",
        "AAPL", "New holding", "+5.6M", "Microsoft", "MSFT", "Closed out", "\u221214.6M", "+1 more change in the caption"]
    plain = {k: v for k, v in _LIST_COVER.items() if k != "list"}
    assert tos.validate_opening_card(plain, keys) is None
    assert "Change in shares" not in tos.opening_strings(plain, logos)


@pytest.mark.parametrize("mutate, code", [
    (lambda c: c["list"].__setitem__("rows", c["list"]["rows"] * 3), "bad_count"),          # six rows on a cover
    (lambda c: c["list"].__setitem__("rows", []), "bad_count"),
    (lambda c: c["list"].__setitem__("note", "Quarter ended"), "unknown_key"),
    (lambda c: c["list"].__setitem__("columns", ["Estimate", "Reported", "Surprise"]), "missing_key"),  # no tables
    (lambda c: c["list"]["rows"][0].__setitem__("logo", "ZZZZ"), "unknown_logo"),
    (lambda c: c["list"]["rows"][0].__setitem__("chip", "TOOLONGCHIPX"), "bad_string"),
    (lambda c: c["list"]["rows"][0].__setitem__("cells", ["a", "b", "c", "d"]), "bad_count"),
    (lambda c: c["list"].__setitem__("more", "line\nbreak"), "bad_string"),
    (lambda c: c.__setitem__("list", ["not", "an", "object"]), "not_an_object"),
], ids=["six_rows", "no_rows", "unknown_key", "columns_without_tables", "unknown_logo", "long_chip", "four_cells",
        "two_lines",
        "not_an_object"])
def test_a_malformed_list_cover_is_refused(mutate, code):
    card = copy.deepcopy(_LIST_COVER)
    mutate(card)
    problem = tos.validate_opening_card(card, {"BRK-B", "AAPL", "MSFT"})
    assert problem is not None and problem.startswith(code), problem
    assert tos.opening_strings(card, [{"key": k, "name": k} for k in ("BRK-B", "AAPL", "MSFT")]) == []


# ── news-v23: the earnings TABLE (a section's `columns`, each row's `table`; owner 2026-11-19) ────────────────────────

_TABLE_SPEC = {
    "layout": "rows", "version": 1, "kicker": "EARNINGS VS ESTIMATES", "title": "Reported vs analyst estimates",
    "subtitle": "Reported Nov 5, 2026",
    "sections": [{"columns": ["Estimate", "Reported", "Surprise"],
                  "rows": [{"logo": "AAPL", "chip": "AAPL", "cells": ["Apple"],
                            "table": [{"cells": ["EPS", "$1.05", "$1.12", "+6.7%"]},
                                      {"cells": ["Revenue", "$3.3B", "$3.2B", "−3.0%"]}]},
                           {"logo": "MSFT", "cells": ["Microsoft"],
                            "table": [{"cells": ["EPS", "$4.50", "$4.86", "+8.0%"]}]}]}],
    "notes": ["The estimate is the analyst consensus; its basis can differ from the official accounts."],
    "footer": "Not investment advice · Source: company results and analyst consensus",
}
_TABLE_LOGOS = [{"key": "AAPL", "name": "Apple"}, {"key": "MSFT", "name": "Microsoft"}]


def test_a_table_validates_and_allows_exactly_its_strings_in_draw_order():
    """The earnings table (news-v23): the column labels after the section's heading, then each row's logo name, chip,
    cells and its table lines' cells (a label, then one per column) — every one allowed on the picture (repeats once),
    nothing else."""
    assert tos.validate_image_spec(_TABLE_SPEC, {"AAPL", "MSFT"}) is None
    assert tos.image_strings(_TABLE_SPEC, _TABLE_LOGOS) == [
        "EARNINGS VS ESTIMATES", "Reported vs analyst estimates", "Reported Nov 5, 2026", "Estimate", "Reported",
        "Surprise", "Apple", "AAPL", "EPS", "$1.05", "$1.12", "+6.7%", "Revenue", "$3.3B", "$3.2B", "−3.0%",
        "Microsoft", "$4.50", "$4.86", "+8.0%",
        "The estimate is the analyst consensus; its basis can differ from the official accounts.",
        "Not investment advice · Source: company results and analyst consensus"]


@pytest.mark.parametrize("mutate, code", [
    (lambda s: s["sections"][0].pop("columns"), "unknown_key"),                        # a table without its columns
    (lambda s: s["sections"][0]["rows"][1].pop("table"), "missing_key"),               # columns: every row a table
    (lambda s: s["sections"][0].__setitem__("columns", ["Estimate", "Reported"]), "bad_count"),
    (lambda s: s["sections"][0].__setitem__("columns", ["A", "B", "C", "D"]), "bad_count"),
    (lambda s: s["sections"][0]["rows"][0]["table"][0].__setitem__("cells", ["EPS", "$1", "$2"]), "bad_count"),
    (lambda s: s["sections"][0]["rows"][0]["table"][0]["cells"].append("x"), "bad_count"),
    (lambda s: s["sections"][0]["rows"][0]["table"].append({"cells": ["a", "b", "c", "d"]}), "bad_count"),
    (lambda s: s["sections"][0]["rows"][0].__setitem__("table", []), "bad_count"),
    (lambda s: s["sections"][0]["rows"][0]["table"][0].__setitem__("tone", "green"), "unknown_key"),
    (lambda s: s["sections"][0]["rows"][0]["table"][0]["cells"].__setitem__(3, "+6.7%\nbeat"), "bad_string"),
    (lambda s: s["sections"][0]["columns"].__setitem__(2, "https://x.example"), "bad_string"),
], ids=["no_columns", "row_without_table", "two_columns", "four_columns", "three_cells", "five_cells",
        "three_lines", "empty_table", "unknown_line_key", "two_line_cell", "link_label"])
def test_a_malformed_table_is_refused(mutate, code):
    spec = copy.deepcopy(_TABLE_SPEC)
    mutate(spec)
    problem = tos.validate_image_spec(spec, {"AAPL", "MSFT"})
    assert problem is not None and problem.startswith(code), problem
    assert tos.image_strings(spec, _TABLE_LOGOS) == []


def test_a_list_cover_may_carry_the_table_and_needs_no_heading():
    """The opening card's list (news-v23): a Congress Count list has no column label; an earnings list names its
    columns and each row carries its table — the picture's own rule, allowed on the video in draw order."""
    sec = _TABLE_SPEC["sections"][0]
    card = {"kicker": "EARNINGS VS ESTIMATES", "logos": ["AAPL"], "headline": "Reported vs analyst estimates",
            "list": {"columns": sec["columns"], "rows": sec["rows"]}}
    assert tos.validate_opening_card(card, {"AAPL", "MSFT"}) is None
    got = tos.opening_strings(card, _TABLE_LOGOS)
    assert got[:6] == ["EARNINGS VS ESTIMATES", "Apple", "Reported vs analyst estimates", "Estimate", "Reported",
                       "Surprise"] and "+8.0%" in got and "−3.0%" in got
    congress = {"kicker": "CONGRESS · DISCLOSED IN NOVEMBER", "logos": ["AAPL"],
                "headline": "Members of Congress disclosed purchases",
                "list": {"rows": [{"logo": "AAPL", "chip": "AAPL",
                                   "cells": ["Apple", "members disclosed purchases of Apple stock", "3"]}]}}
    assert tos.validate_opening_card(congress, {"AAPL"}) is None
    bad = copy.deepcopy(card)
    bad["list"]["rows"][0].pop("table")
    assert tos.validate_opening_card(bad, {"AAPL", "MSFT"}).startswith("missing_key")


# ── news-v23 phase 3: the weekly theme post's grid keys and the `ranked` layout ─────────────────────────────────

def _with(spec, path, value):
    out = copy.deepcopy(spec)
    node = out
    for k in path[:-1]:
        node = node[k]
    if value is _DROP:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return out


_DROP = object()


@pytest.mark.parametrize("spec, code", [
    (_with(RANKED, ("rows", 1, "rank"), "3"), "bad_rank"),                  # a rank that is not its position
    (_with(RANKED, ("rows", 0, "rank"), "01"), "bad_rank"),
    (_with(RANKED, ("rows", 0, "rank"), 1), "bad_rank"),
    (_with(RANKED, ("rows", 0, "ratio"), 1.2), "bad_ratio"),
    (_with(RANKED, ("rows", 0, "ratio"), float("nan")), "bad_ratio"),
    (_with(RANKED, ("rows", 0, "ratio"), True), "bad_ratio"),
    (_with(RANKED, ("rows", 0, "ratio"), _DROP), "missing_key"),
    (_with(RANKED, ("rows", 0, "value"), _DROP), "missing_key"),
    (_with(RANKED, ("rows", 0, "price"), "$190"), "unknown_key"),           # a closed row
    (_with(RANKED, ("rows", 0, "chip"), "NVIDIA CORPORATION"), "bad_string"),
    (_with(RANKED, ("rows",), RANKED["rows"][:3]), "bad_count"),            # fewer than four
    (_with(RANKED, ("rows",), [dict(r, rank=str(i + 1)) for i, r in enumerate(RANKED["rows"] * 3)]), "bad_count"),
    (_with(RANKED, ("icon",), "rocket"), "bad_icon"),
    (_with(RANKED, ("icon",), 3), "bad_icon"),
    (_with(GRID_V3, ("icon",), "Chip"), "bad_icon"),
    (_with(GRID_V3, ("sub_break",), 0), "bad_sub_break"),
    (_with(GRID_V3, ("sub_break",), 99), "bad_sub_break"),
    (_with(GRID_V3, ("sub_break",), True), "bad_sub_break"),
    (_with(GRID_V3, ("sub_break",), "6"), "bad_sub_break"),
    (_with(GRID_V3, ("tiles", 0, "chip"), "X" * 11), "bad_string"),
    (_with(GRID_V3, ("tiles", 0, "chip"), ""), "bad_string"),
    (_with(GRID_V3, ("tiles", 0, "share"), "88%"), "unknown_key"),
])
def test_phase3_the_theme_keys_are_closed_and_checked(spec, code):
    """Every new key is closed and checked on the server FIRST (the worker mirrors the walk): a ranked row's rank is
    exactly its position, its ratio a finite number in [0, 1], its chip a ticker; the icon one of THEME_ICONS (never
    drawn); the sub-line break inside the subtitle (never drawn); a tile's chip a ticker."""
    assert _code(tos.validate_image_spec(spec, KEYS)) == code
    assert tos.image_strings(spec, LOGOS) == []


def test_phase3_the_icon_the_break_and_the_ratios_are_never_drawable_strings():
    for spec in (GRID_V3, RANKED):
        got = tos.image_strings(spec, LOGOS)
        assert spec["icon"] not in got and not any(s in ("6", "1.0", "0.8864") for s in got)
    # a ranked row's rank IS drawn (the gold disc's number) — exactly its position
    assert [s for s in tos.image_strings(RANKED, LOGOS) if s.isdigit()] == ["1", "2", "3", "4"]


def test_phase3_the_theme_list_cover_validates_with_a_rank_label_and_a_share_line():
    card = {"kicker": "INSIDE A THEME", "logos": ["NVDA"], "figure": "16", "headline": "Power the Machine",
            "list": {"heading": "16 companies · market value as of Nov 24, 2026",
                     "rows": [{"logo": "NVDA", "chip": "NVDA", "label": "1", "cells": ["NVIDIA", "$4.4T"]},
                              {"logo": "AAPL", "chip": "AAPL", "label": "2", "cells": ["Apple", "$3.9T"]}],
                     "more": "+14 more"}}
    assert tos.validate_opening_card(card, KEYS) is None
    # each row in the walk's order: its logo's name, its chip, its cells, its `label` (the rank, drawn on its disc)
    assert tos.opening_strings(card, LOGOS) == [
        "INSIDE A THEME", "NVIDIA", "16", "Power the Machine", "16 companies · market value as of Nov 24, 2026",
        "NVDA", "$4.4T", "1", "Apple", "AAPL", "$3.9T", "2", "+14 more"]


# ── news-v23 phase 3, the theme's last three angles (2026-11-20): `figures`, `ledger`, `tally` and their covers ──────

@pytest.mark.parametrize("spec, code", [
    # figures: the growth and profit in their shapes (the worker colours them by their sign), a ratio in [0, 1]
    (_with(FIGURES, ("rows", 0, "growth"), "114%"), "bad_figure"),            # unsigned: the colour would be a guess
    (_with(FIGURES, ("rows", 0, "growth"), "-5%"), "bad_figure"),             # an ASCII hyphen, not U+2212
    (_with(FIGURES, ("rows", 0, "growth"), "+0%"), "bad_figure"),
    (_with(FIGURES, ("rows", 0, "growth"), "+114.5%"), "bad_figure"),
    (_with(FIGURES, ("rows", 0, "growth"), "up 114%"), "bad_figure"),
    (_with(FIGURES, ("rows", 0, "profit"), "-$17"), "bad_figure"),
    (_with(FIGURES, ("rows", 0, "profit"), "$0.00"), "bad_figure"),           # a zero that is not one
    (_with(FIGURES, ("rows", 0, "profit"), "56"), "bad_figure"),
    (_with(FIGURES, ("rows", 0, "ratio"), 1.5), "bad_ratio"),
    (_with(FIGURES, ("rows", 0, "ratio"), _DROP), "missing_key"),
    (_with(FIGURES, ("rows", 0, "price"), "$190"), "unknown_key"),             # a closed row
    (_with(FIGURES, ("heads",), [["Revenue growth"]]), "bad_count"),          # exactly two heads
    (_with(FIGURES, ("heads", 1), ["a", "b", "c"]), "bad_count"),             # at most two lines a head
    (_with(FIGURES, ("heads", 0), []), "bad_count"),
    (_with(FIGURES, ("unit",), "members"), "unknown_key"),                    # another table's key
    (_with(FIGURES, ("rows",), FIGURES["rows"][:3]), "bad_count"),            # fewer than four
    (_with(FIGURES, ("rows",), FIGURES["rows"] * 3), "bad_count"),            # more than ten
    (_with(FIGURES, ("icon",), "rocket"), "bad_icon"),
    # ledger: an amount or the empty cell, never a row with neither
    (_with(LEDGER, ("rows", 0, "buys"), "2.1M"), "bad_figure"),
    (_with(LEDGER, ("rows", 0, "buys"), "-"), "bad_figure"),
    (_with(LEDGER, ("rows", 0, "buys"), "$2.15M"), "bad_figure"),
    (_with(LEDGER, ("rows", 0, "buys"), "—"), "bad_figure"),                  # both cells empty
    (_with(LEDGER, ("rows", 0, "sells"), _DROP), "missing_key"),
    (_with(LEDGER, ("heads",), _DROP), "missing_key"),
    # tally: a count of 2..535 distinct members, never 1
    (_with(TALLY, ("rows", 0, "count"), "1"), "bad_figure"),
    (_with(TALLY, ("rows", 0, "count"), "536"), "bad_figure"),
    (_with(TALLY, ("rows", 0, "count"), "02"), "bad_figure"),
    (_with(TALLY, ("rows", 0, "count"), 6), "bad_string"),
    (_with(TALLY, ("rows",), TALLY["rows"] * 2), "bad_count"),                # more than six
    (_with(TALLY, ("unit",), _DROP), "missing_key"),
    (_with(TALLY, ("heads",), [["a"], ["b"]]), "unknown_key"),
])
def test_phase3_the_table_keys_are_closed_and_checked(spec, code):
    """Every key of the three tables is closed and checked on the server FIRST (the worker mirrors the walk): the
    figures in their shapes (each drawn GREEN or RED by its sign, so a free string would be a colour on a guess), two
    column heads of one or two lines, a scorecard row with at least one amount, a count of two or more members."""
    assert _code(tos.validate_image_spec(spec, KEYS)) == code
    assert tos.image_strings(spec, LOGOS) == []


def _table_cover(spec, rows=4, **over):
    lst = {"layout": spec["layout"], "heading": spec["subtitle"], "rows": spec["rows"][:rows]}
    for k in ("heads", "unit", "note"):
        if k in spec:
            lst[k] = spec[k]
    lst.update(over)
    return {"kicker": "INSIDE A THEME", "logos": [spec["rows"][0]["logo"]], "figure": "14", "headline": spec["title"],
            "list": lst}


@pytest.mark.parametrize("spec", [FIGURES, LEDGER, TALLY], ids=["figures", "ledger", "tally"])
def test_phase3_a_table_cover_is_the_pictures_own_rows_under_its_heading(spec):
    card = _table_cover(spec, more="+10 more")
    assert tos.validate_opening_card(card, KEYS) is None
    got = tos.opening_strings(card, LOGOS)
    # the cover may draw exactly what the picture draws for those rows (its heading = the picture's sub line)
    picture = tos.image_strings(spec, LOGOS)
    assert set(got) - {"14", "+10 more"} <= set(picture)
    assert got[:4] == ["INSIDE A THEME", spec["rows"][0]["name"], "14", spec["title"]]
    assert spec["subtitle"] in got and spec["note"] in got
    assert tos.validate_opening_card(_table_cover(spec, rows=1), KEYS) is None


def test_fidelity2_a_ledger_cell_with_no_filing_is_a_marker_never_an_allowed_string():
    """Visual-fidelity round 2 (2026-11-20, theme_insiders_cyber.jpg: a scorecard cell with no filing is BLANK): the
    spec's EMPTY_CELL ("—") is a code-owned marker the server checks but never allows — on the picture and on its
    cover — so a worker that drew "—" again is refused at registration (its captions say "none" in words). Every real
    amount stays allowed."""
    assert any(tos.EMPTY_CELL in (r["buys"], r["sells"]) for r in LEDGER["rows"])
    assert tos.validate_image_spec(LEDGER, KEYS) is None
    picture = tos.image_strings(LEDGER, LOGOS)
    assert tos.EMPTY_CELL not in picture
    assert {r[k] for r in LEDGER["rows"] for k in ("buys", "sells")} - {tos.EMPTY_CELL} <= set(picture)
    card = _table_cover(LEDGER)
    assert tos.validate_opening_card(card, KEYS) is None and tos.EMPTY_CELL not in tos.opening_strings(card, LOGOS)
    # still a marker the walk CHECKS: a row with neither a purchase nor a sale is refused
    assert _code(tos.validate_image_spec(_with(LEDGER, ("rows", 0, "buys"), tos.EMPTY_CELL), KEYS)) == "bad_figure"


@pytest.mark.parametrize("over, code", [
    (dict(layout="ranked"), "unknown_layout"),                       # only a theme TABLE's cover names its layout
    (dict(layout="grid"), "unknown_layout"),
    (dict(heading=None), "missing_key"),
    (dict(columns=["a", "b", "c"]), "unknown_key"),                  # an earnings cover's key
])
def test_phase3_a_table_cover_refuses_what_its_layout_does_not_take(over, code):
    for spec in (FIGURES, LEDGER, TALLY):
        assert _code(tos.validate_opening_card(_table_cover(spec, **over), KEYS)) == code
        assert tos.opening_strings(_table_cover(spec, **over), LOGOS) == []
    assert _code(tos.validate_opening_card(_table_cover(FIGURES, heads=None), KEYS)) == "missing_key"
    assert _code(tos.validate_opening_card(_table_cover(TALLY, unit=None), KEYS)) == "missing_key"
    assert _code(tos.validate_opening_card(_table_cover(TALLY, heads=[["a"], ["b"]]), KEYS)) == "unknown_key"
    assert _code(tos.validate_opening_card(_table_cover(FIGURES, unit="members"), KEYS)) == "unknown_key"
    # up to MAX_OPENING_TABLE_ROWS rows (six) on a table cover; a 13F-style list keeps its four
    many = dict(FIGURES, rows=FIGURES["rows"] * 2)
    assert tos.validate_opening_card(_table_cover(many, rows=6), KEYS) is None
    assert _code(tos.validate_opening_card(_table_cover(many, rows=7), KEYS)) == "bad_count"
    # a 13F / Congress list (no layout) never takes the tables' keys
    plain = {"kicker": "13F", "logos": ["NVDA"], "headline": "X",
             "list": {"rows": [{"cells": ["NVIDIA"], "logo": "NVDA"}], "note": "Purchases and sales only"}}
    assert _code(tos.validate_opening_card(plain, KEYS)) == "unknown_key"


# ── owner decision 2026-11-20: the Money Map LOSS year's costs card ("A: costs vs revenue") ─────────────────────────

COSTS = {
    "revenue": {"label": "Revenue", "value": "$5.8B", "ratio": 0.5943},
    "net": {"label": "Net loss", "value": "$3.9B"},
    "total": {"label": "Costs and expenses", "value": "$9.7B", "ratio": 0.9887},
    "parts": [{"label": "Cost of sales", "value": "$5.9B", "ratio": 0.6025},
              {"label": "Operating expenses", "value": "$3.9B", "ratio": 0.3975}],
    "note": "+$110M from interest, tax and other items made the loss smaller",
    "callout": ["For every $100 of revenue, costs were $166.38,", "so the net loss was $66.38"],
}
COSTS_STRINGS = ["Revenue", "$5.8B", "Net loss", "$3.9B", "Costs and expenses", "$9.7B", "Cost of sales", "$5.9B",
                 "Operating expenses", "+$110M from interest, tax and other items made the loss smaller",
                 "For every $100 of revenue, costs were $166.38,", "so the net loss was $66.38"]
BARS_LOSS = dict(BARS, footer=FOOTER, costs=COSTS)
OPENING_LOSS = {"kicker": "MONEY MAP", "logos": ["COST"], "chip": "COST", "figure": "$66.38",
                "figure_label": "net loss for every $100 of revenue", "headline": "How Costco makes money",
                "subtitle": "Fiscal 2025 · annual results", "costs": COSTS}


def test_costs_the_loss_picture_allows_every_string_of_its_costs_card_after_the_flow():
    assert tos.validate_image_spec(BARS_LOSS, KEYS, footer=FOOTER) is None
    allowed = tos.image_strings(BARS_LOSS, LOGOS)
    plain = tos.image_strings(BARS, LOGOS)
    # a superset of today's (the flow an older worker still draws), the costs card's strings in draw order, footer last
    assert allowed[:len(plain) - 1] == plain[:-1] and allowed[-1] == FOOTER
    assert [s for s in allowed if s not in plain] == [s for s in COSTS_STRINGS if s not in plain]
    assert set(COSTS_STRINGS) <= set(allowed)


def test_costs_the_loss_cover_allows_its_sub_line_and_costs_card_beside_the_fallback_figure():
    assert tos.validate_opening_card(OPENING_LOSS, KEYS) is None
    allowed = tos.opening_strings(OPENING_LOSS, LOGOS)
    assert allowed[:6] == ["MONEY MAP", "Costco", "COST", "$66.38", "net loss for every $100 of revenue",
                           "How Costco makes money"]
    assert allowed[6] == "Fiscal 2025 · annual results" and set(COSTS_STRINGS) <= set(allowed)


def _costs_mut(path, value, *, delete=False):
    return _mut(BARS_LOSS, ("costs",) + path, value, delete=delete)


@pytest.mark.parametrize("spec, code", [
    (_costs_mut(("colour",), "red"), "unknown_key"),
    (_costs_mut(("revenue", "style"), "fill"), "unknown_key"),
    (_costs_mut(("net", "ratio"), 0.4), "unknown_key"),              # the net loss is drawn over the gap: no ratio
    (_costs_mut(("parts", 0, "colour"), "red"), "unknown_key"),
    (_costs_mut(("callout",), None), "missing_key"),
    (_costs_mut(("total",), None, delete=True), "missing_key"),
    (_costs_mut(("revenue", "ratio"), None), "missing_key"),
    (_costs_mut(("parts",), [COSTS["parts"][0]]), "bad_count"),     # one part: never (0, 2 or 3)
    (_costs_mut(("parts",), COSTS["parts"] * 2), "bad_count"),      # four parts
    (_costs_mut(("callout",), ["one line"]), "bad_count"),
    (_costs_mut(("callout",), ["a", "b", "c"]), "bad_count"),
    (_costs_mut(("parts", 1, "ratio"), 1.0001), "bad_ratio"),
    (_costs_mut(("total", "ratio"), float("nan")), "bad_ratio"),
    (_costs_mut(("revenue", "ratio"), True), "bad_ratio"),
    (_costs_mut(("revenue", "ratio"), 0.99), "bad_ratio"),           # revenue longer than costs: not a loss year
    (dict(BARS_LOSS, costs=dict(COSTS, parts=[])), "missing_key"),  # a note with no parts to hatch
    (_costs_mut(("parts", 0, "value"), 5.9), "bad_string"),
    (_costs_mut(("note",), "see www.example.com"), "bad_string"),
    (_costs_mut(("callout", 1), "so the net loss\nwas $66.38"), "bad_string"),
], ids=["unknown_key", "unknown_bar_key", "net_ratio", "unknown_part_key", "callout_null", "total_missing",
        "ratio_null", "one_part", "four_parts", "one_callout_line", "three_callout_lines", "ratio_over_one",
        "ratio_nan", "ratio_bool", "revenue_longer", "note_without_parts", "numeric_value", "link_note",
        "two_line_callout"])
def test_costs_the_closed_schema_refuses_every_malformed_costs_card(spec, code):
    problem = tos.validate_image_spec(spec, KEYS, footer=FOOTER)
    assert problem is not None and problem.startswith(code), problem
    assert tos.image_strings(spec, LOGOS) == []                                   # fail closed: nothing drawable
    # the cover's copy of the card is held to the same walk
    card = dict(OPENING_LOSS, costs=spec["costs"])
    problem = tos.validate_opening_card(card, KEYS)
    assert problem is not None and problem.startswith(code), problem


def test_costs_a_costs_card_without_parts_and_without_a_note_validates():
    spec = dict(BARS_LOSS, costs={k: v for k, v in dict(COSTS, parts=[]).items() if k != "note"})
    assert tos.validate_image_spec(spec, KEYS, footer=FOOTER) is None
    third = dict(COSTS, parts=COSTS["parts"] + [{"label": "Interest, tax and other items", "value": "$100M",
                                                  "ratio": 0.02}])
    assert tos.validate_image_spec(dict(BARS_LOSS, costs=third), KEYS, footer=FOOTER) is None


@pytest.mark.parametrize("card, code", [
    ({k: v for k, v in OPENING_LOSS.items() if k != "costs"}, "missing_key"),     # a sub line only on a costs cover
    (dict(OPENING_LOSS, list={"rows": [{"cells": ["x"]}]}), "unknown_key"),       # a costs cover carries no list
    (dict(OPENING_LOSS, subtitle="Fiscal\n2025"), "bad_string"),
], ids=["subtitle_without_costs", "costs_with_list", "two_line_subtitle"])
def test_costs_the_cover_refuses_a_sub_line_alone_and_a_costs_card_beside_a_list(card, code):
    problem = tos.validate_opening_card(card, KEYS)
    assert problem is not None and problem.startswith(code), problem
    assert tos.opening_strings(card, LOGOS) == []
