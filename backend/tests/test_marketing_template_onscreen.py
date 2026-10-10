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
  and the worst case of every layout stays under ONSCREEN_TEXT_MAX.

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

FOOTER = post_copy.image_footer(date(2026, 10, 12), "template", source="SEC Form 4 filings",
                                as_of="Filed Oct 5–9, 2026")


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
OPENING = {"kicker": "FILED LAST WEEK · FORM 4", "logos": ["GME"], "chip": "GME", "figure": "$74.4M",
           "headline": "GameStop's CEO disclosed buying GameStop stock"}

ROWS_EARNINGS_NO_LOGO = {k: v for k, v in ROWS_EARNINGS.items() if k != "header"}

SHIPPED_SAMPLES = {"rows_ceo": ROWS_CEO, "rows_13f": ROWS_13F, "rows_earnings": ROWS_EARNINGS,
                   "spotlight": SPOTLIGHT, "bars": BARS, "pair": PAIR, "grid": GRID}
#: The two layouts drop 2b shipped (company_stakes, theme_explainer).
SAMPLES_2B = {"pair": PAIR, "grid": GRID}
#: One sample per layout (the `layout_not_shipped` gate is checked for each).
SAMPLE_PER_LAYOUT = {"rows": ROWS_CEO, "spotlight": SPOTLIGHT, "pair": PAIR, "bars": BARS, "grid": GRID}

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
    assert tos.LAYOUTS == ("rows", "spotlight", "pair", "bars", "grid")
    # drop 2b (2026-10-10): pair and grid shipped with company_stakes / theme_explainer
    assert tos.SHIPPED_LAYOUTS == ("rows", "spotlight", "pair", "bars", "grid") == tos.LAYOUTS
    assert tos.SPEC_VERSION == 1
    assert (tos.MAX_LOGOS, tos.MAX_ROWS, tos.MAX_SECTIONS, tos.MAX_LINES, tos.MAX_BARS, tos.MAX_FLOW,
            tos.MAX_TILES) == (12, 8, 3, 2, 7, 4, 12)
    assert (tos.MIN_BARS, tos.MIN_FLOW, tos.MIN_TILES, tos.MAX_SECTION_ROWS, tos.MAX_CELLS, tos.MAX_NOTES,
            tos.MAX_OPENING_LOGOS) == (2, 2, 3, 5, 3, 2, 2)
    assert tos.BAR_STYLES == ("fill", "outline")
    assert tuple(tos.LAYOUT_KEYS) == tos.LAYOUTS
    assert tos.COMMON_KEYS == ("layout", "version", "kicker", "footer")
    assert tos.OPENING_KEYS == (("kicker", "logos", "headline"), ("chip", "figure"))
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
    ("chip on a bars header", BARS, ("header", "chip"), "COST", "unknown_key"),
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
    ("six rows in a section", ROWS_CEO, ("sections", 0, "rows"), [ROWS_CEO["sections"][0]["rows"][0]] * 6,
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
    other = post_copy.image_footer(date(2026, 10, 12), "template", source="SEC Form 13F", as_of="Fiscal 2025")
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
    """The post_copy template footer (D8) is the spec's footer: it must pass the same string rules."""
    for source, as_of in (("SEC Form 4 filings", "Filed Oct 5–9, 2026"),
                          ("SEC Form 13F-HR/A", "Quarter ended Sep 30, 2026 · filed Nov 14, 2026"),
                          ("company financial statements", "Fiscal 2025")):
        footer = post_copy.image_footer(date(2026, 11, 16), "template", source=source, as_of=as_of)
        assert tos.drawable_problem(footer) is None
        assert tos.validate_image_spec(dict(ROWS_CEO, footer=footer), KEYS, footer=footer) is None


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
