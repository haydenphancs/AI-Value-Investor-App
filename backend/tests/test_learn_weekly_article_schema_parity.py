"""The weekly article ↔ iOS contract (`app/services/learn_weekly/article_schema.py`).

A weekly piece is served VERBATIM to `MoneyMoveArticleDTO` — the same decoder the bundled
`money_moves.json` feeds — and old App Store builds decoded with SYNTHESIZED (strict) Codable, where
one missing or wrongly typed required key drops the article (or, before the lenient array, the whole
catalog). So the worst-case rendered piece must carry every non-optional key at every level, with
the JSON type a strict decoder demands. The required-key sets are READ from the Swift source
(comments stripped, each struct brace-bounded), so a new required DTO field fails here first.

Also pinned: the category enum, related tiles that resolve to real (bundle) catalog titles, icons
inside the bundle's allow-list, the category gradients equal to iOS's, the disclosure appended last
and never twice, the code-owned fields, and `draft_sha256` equal to the narration module's.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest
from pydantic import ValidationError

from app.schemas.money_moves import MoneyMovesResponse
from app.services import learn_content_rules as rules
from app.services.learn_weekly import article_schema as A
from app.services.learn_weekly import contracts as C
from app.services.learn_weekly import validators as V

ROOT = Path(__file__).resolve().parents[2]
DTO_SWIFT = ROOT / "frontend/ios/ios/Models/MoneyMovesContentModels.swift"
LEARN_SWIFT = ROOT / "frontend/ios/ios/Models/LearnModels.swift"
BUNDLE = ROOT / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
PUB = date(2026, 10, 23)

SHEET = C.FactSheet(C.KIND_DEEP_DIVE, "AI chips", "ai-chips",
                    (C.Subject("NVIDIA CORP", "0001045810", "NVDA"),), (
    C.Fact("S1", "NVIDIA CORP reported revenue of $130.5 billion for the fiscal year ended "
                 "January 26, 2025 (Form 10-K).", C.ORIGIN_SEC_XBRL, "https://www.sec.gov/b",
           "0001045810-25-000023"),
    C.Fact("S2", "In 2006 the company introduced a programming platform for its chips.",
           C.ORIGIN_LIBRARY, "https://example.org/x", None, "nvidia-cuda"),
))


def _bundle() -> List[Dict[str, Any]]:
    return json.loads(BUNDLE.read_text(encoding="utf-8"))["articles"]


def _outline(n_sections: int = 5, **over: Any) -> A.Outline:
    raw = {
        "title": "How Chips Became the Bottleneck", "subtitle": "A subtitle.",
        "cardSubtitle": "A card line.", "tagLabel": "Feature Deep Dive",
        "keyHighlights": [{"icon": "cpu.fill", "title": f"Highlight {i}", "description": "D."}
                          for i in range(3)],
        "sections": [{"title": f"Section {i}", "icon": "chart.bar.fill",
                      "kind": "peg" if i == 0 else "history", "fact_ids": [],
                      "target_words": 300} for i in range(n_sections)],
        "statistics": [],
    }
    raw.update(over)
    return A.normalize_outline(A.parse_outline(raw), C.KIND_DEEP_DIVE)


def _worst_case() -> Dict[str, Any]:
    """The sparsest piece the renderer can produce: one paragraph per section, no statistics, no
    related tiles, no audio."""
    o = _outline()
    blocks = [[{"type": "paragraph", "text": "Body."}] for _ in o.sections]
    return A.render_ios_content(o, blocks, kind=C.KIND_DEEP_DIVE, category="blueprints",
                                slug=A.make_slug(o.title, PUB), published_date=PUB, sheet=SHEET,
                                related_titles=[])


def _full() -> Dict[str, Any]:
    o = _outline(statistics=[{"fact_id": "S1", "label": "Revenue", "trend": "up"}])
    blocks = [[{"type": "paragraph", "text": "Body."}, {"type": "subheading", "text": "Sub"},
               {"type": "bulletList", "items": ["a", "b"]},
               {"type": "callout", "icon": "lightbulb.fill", "style": "highlight", "text": "C."}]
              for _ in o.sections]
    return A.append_disclosure(A.render_ios_content(
        o, blocks, kind=C.KIND_DEEP_DIVE, category="battles", slug=A.make_slug(o.title, PUB),
        published_date=PUB, sheet=SHEET, related_titles=_bundle()))


# ── the Swift DTOs, read from source ─────────────────────────────────────────────────────────


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _struct_body(src: str, name: str) -> str:
    m = re.search(rf"\bstruct\s+{name}\s*:[^{{]*\{{", src)
    assert m, f"{name} not found in {DTO_SWIFT.name}"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end():i - 1]


def _required(name: str) -> Dict[str, str]:
    """Non-optional stored `let`s of a DTO at its top brace level → their Swift type."""
    body = _struct_body(_strip_comments(DTO_SWIFT.read_text(encoding="utf-8")), name)
    out, depth = {}, 0
    for line in body.splitlines():
        if depth == 0:
            m = re.match(r"\s*let\s+(\w+)\s*:\s*([^=\n]+?)\s*$", line)
            if m and not m.group(2).endswith("?"):
                out[m.group(1)] = m.group(2)
        depth += line.count("{") - line.count("}")
    return out


_JSON_TYPE = {"String": str, "Int": int, "Bool": bool, "[String]": list}

DTO_LEVELS = {
    "MoneyMoveArticleDTO": lambda a: [a],
    "ArticleAuthorDTO": lambda a: [a["author"]],
    "ArticleHighlightDTO": lambda a: a["keyHighlights"],
    "ArticleSectionDTO": lambda a: a["sections"],
    "ArticleSectionContentDTO": lambda a: [b for s in a["sections"] for b in s["content"]],
    "ArticleStatisticDTO": lambda a: a["statistics"],
    "RelatedArticleDTO": lambda a: a["relatedArticles"],
}


def test_the_swift_scan_reads_the_known_required_fields():
    """The scan itself must not go vacuous (it would then require nothing)."""
    art = _required("MoneyMoveArticleDTO")
    assert {"slug", "title", "subtitle", "category", "author", "readTimeMinutes", "viewCount",
            "heroGradientColors", "keyHighlights", "sections"} <= set(art)
    assert "audioUrl" not in art and "statistics" not in art         # optionals stay out
    assert set(_required("RelatedArticleDTO")) >= {"title", "gradientColors", "readTimeMinutes"}
    assert set(_required("ArticleSectionContentDTO")) == {"type"}


@pytest.mark.parametrize("dto", sorted(DTO_LEVELS))
def test_every_required_ios_key_is_present_with_a_strict_json_type(dto):
    required = _required(dto)
    for article in (_worst_case(), _full()):
        # (The worst case has no statistic or related tile; `_full` carries both — pinned below.)
        for node in DTO_LEVELS[dto](article):
            missing = set(required) - set(node)
            assert not missing, f"{dto} missing {missing}"
            for key, swift in required.items():
                want = _JSON_TYPE.get(swift)
                if want is int:
                    assert isinstance(node[key], int) and not isinstance(node[key], bool), (dto, key)
                elif want is not None:
                    assert isinstance(node[key], want), (dto, key, node[key])
                if swift == "[String]":
                    assert all(isinstance(x, str) for x in node[key]), (dto, key)


def test_the_full_article_has_statistics_and_related_tiles_to_check():
    full = _full()
    assert full["statistics"] and full["relatedArticles"]


def test_the_rendered_article_passes_the_existing_money_moves_parity_shape():
    from test_money_moves_schema_parity import _assert_article_shape

    for article in (_worst_case(), _full()):
        resp = MoneyMovesResponse(articles=[article])
        _assert_article_shape(resp.articles[0])
        json.dumps(article, allow_nan=False)                 # JSONB-safe


def test_the_key_set_is_the_bundles_and_nothing_new():
    bundle_keys = set().union(*(a.keys() for a in _bundle()))
    full = _full()
    assert set(full) <= bundle_keys, set(full) - bundle_keys
    # narration adds audioDurationSeconds later; everything else a bundle article carries is here
    assert bundle_keys - set(full) == {"audioDurationSeconds"}
    sec_keys = set().union(*(s.keys() for a in _bundle() for s in a["sections"]))
    assert all(set(s) <= sec_keys for s in full["sections"])
    assert all("kind" not in s for s in full["sections"]), "draft kinds never reach iOS"


def test_code_owned_fields():
    a = _worst_case()
    assert a["author"] == C.AUTHOR and "ai-assisted" in a["author"]["title"].casefold()
    assert a["viewCount"] == "" and a["publishedDaysAgo"] == 0
    assert a["isFeatured"] is True and a["hasAudioVersion"] is False and a["audioUrl"] is None
    assert a["tagLabel"] == "FEATURE DEEP DIVE"
    assert a["heroGradientColors"] == list(A.CATEGORY_GRADIENTS["blueprints"])
    assert a["sortOrder"] == -(2026 * 100 + 43)
    assert a["sections"][0]["hasGlowEffect"] is True
    assert all("hasGlowEffect" not in s for s in a["sections"][1:])
    assert V.SLUG_RE.fullmatch(a["slug"]) and a["slug"].endswith("-2026-10-23")


def test_a_slot_piece_is_not_featured_and_sorts_after_the_bundle():
    o = _outline()
    a = A.render_ios_content(o, [[{"type": "paragraph", "text": "x"}]] * len(o.sections),
                             kind=C.KIND_SLOT, category="valueTraps",
                             slug=A.make_slug(o.title, date(2026, 10, 26)),
                             published_date=date(2026, 10, 26), sheet=SHEET)
    assert a["isFeatured"] is False and a["sortOrder"] == 100000 + 298
    assert a["heroGradientColors"] == ["DC2626", "991B1B", "7F1D1D"]


@pytest.mark.parametrize("category", ["growth", "value_traps", "", None])
def test_the_category_must_be_in_the_enum(category):
    o = _outline()
    with pytest.raises(ValueError):
        A.render_ios_content(o, [[]] * len(o.sections), kind=C.KIND_DEEP_DIVE, category=category,
                             slug="x-2026-10-23", published_date=PUB, sheet=SHEET)


def test_sections_must_be_parallel_to_the_outline():
    o = _outline()
    with pytest.raises(ValueError):
        A.render_ios_content(o, [[]], kind=C.KIND_DEEP_DIVE, category="battles",
                             slug="x-2026-10-23", published_date=PUB, sheet=SHEET)


def test_category_gradients_equal_ios():
    src = _strip_comments(LEARN_SWIFT.read_text(encoding="utf-8"))
    m = re.search(r"var gradientColors: \[String\] \{(.*?)\n    \}", src, re.S)
    assert m, "MoneyMoveCategory.gradientColors not found"
    swift = {case: re.findall(r'"([0-9A-F]{6})"', body)
             for case, body in re.findall(r"case \.(\w+):\s*return (\[[^\]]*\])", m.group(1))}
    assert swift == {k: list(v) for k, v in A.CATEGORY_GRADIENTS.items()}
    assert set(A.CATEGORY_GRADIENTS) == set(C.CATEGORIES)


# ── related tiles ─────────────────────────────────────────────────────────────────────────────


def test_related_tiles_resolve_to_bundle_titles_same_category_first():
    bundle = _bundle()
    titles = {a["title"] for a in bundle}
    weekly = {"slug": "a-weekly-piece-2026-10-16", "title": "A Weekly Piece", "subtitle": "s",
              "category": "battles", "readTimeMinutes": 9, "heroGradientColors": ["7C3AED"] * 3}
    tiles = A.related_articles([weekly] + bundle, "battles", exclude_titles=["Visa vs Mastercard"])
    assert len(tiles) == A.RELATED_COUNT
    assert all(t["title"] in titles for t in tiles), "a tile must open a real catalog article"
    assert "A Weekly Piece" not in {t["title"] for t in tiles}
    assert "Visa vs Mastercard" not in {t["title"] for t in tiles}
    assert all(t["category"] == "battles" for t in tiles)
    for t in tiles:
        assert set(t) == {"title", "subtitle", "category", "readTimeMinutes", "viewCount",
                          "gradientColors"}
        assert t["viewCount"] == "" and len(t["gradientColors"]) == 2


def test_related_tiles_skip_rows_a_tile_cannot_show():
    junk = [None, {"title": "No subtitle", "category": "battles", "readTimeMinutes": 3},
            {"title": "Bad category", "subtitle": "s", "category": "growth", "readTimeMinutes": 3},
            {"title": "Bool minutes", "subtitle": "s", "category": "battles", "readTimeMinutes": True},
            {"title": "Fine", "subtitle": "s", "category": "battles", "readTimeMinutes": 4},
            {"title": "fine", "subtitle": "dup", "category": "battles", "readTimeMinutes": 4}]
    tiles = A.related_articles(junk, "blueprints")
    assert [t["title"] for t in tiles] == ["Fine"]
    assert tiles[0]["gradientColors"] == list(A.CATEGORY_GRADIENTS["battles"][:2])


# ── icons ─────────────────────────────────────────────────────────────────────────────────────


def test_every_schema_icon_enum_is_the_bundle_allow_list():
    schema = A.outline_response_schema(["S1"])
    props = schema["properties"]
    hl = props["keyHighlights"]["items"]["properties"]["icon"]["enum"]
    sec = props["sections"]["items"]["properties"]["icon"]["enum"]
    blk = A.SECTION_RESPONSE_SCHEMA["properties"]["blocks"]["items"]["properties"]["icon"]["enum"]
    assert set(hl) == set(sec) == set(blk) == set(rules.ICON_ALLOWLIST)
    assert A.DISCLOSURE_ICON in rules.ICON_ALLOWLIST


def test_schema_enums_match_the_contracts():
    props = A.outline_response_schema(["S1", "P1", "S1"])["properties"]
    assert props["sections"]["items"]["properties"]["kind"]["enum"] == list(C.SECTION_KINDS)
    assert props["sections"]["items"]["properties"]["fact_ids"]["items"]["enum"] == ["S1", "P1"]
    assert props["statistics"]["items"]["properties"]["trend"]["enum"] == list(A.STAT_TRENDS)
    blk = A.SECTION_RESPONSE_SCHEMA["properties"]["blocks"]["items"]["properties"]
    assert blk["type"]["enum"] == list(C.BLOCK_TYPES) and "quote" not in blk["type"]["enum"]
    assert blk["style"]["enum"] == list(C.CALLOUT_STYLES)
    assert "enum" not in A.outline_response_schema([])["properties"]["statistics"]["items"][
        "properties"]["fact_id"], "an empty enum is invalid for Gemini"


# ── the disclosure ────────────────────────────────────────────────────────────────────────────


def test_the_disclosure_is_appended_last_once_and_never_validated():
    a = _worst_case()
    once = A.append_disclosure(a)
    twice = A.append_disclosure(once)
    assert once == twice
    assert once["sections"][-1]["content"][-1] == A.disclosure_block()
    assert a["sections"][-1]["content"][-1] != A.disclosure_block(), "the input is not mutated"
    # It names the brand on purpose: the validators would refuse it as model text.
    issues = V.validate_block("x", A.disclosure_block(), section_kind="lessons")
    assert any(i.code in ("forbidden_token", "brand_mention") for i in issues)
    assert "ai-assisted" in C.DISCLOSURE_TEXT.casefold()   # the label the public disclosures promise


def test_append_disclosure_refuses_an_article_with_no_section():
    with pytest.raises(ValueError):
        A.append_disclosure({"sections": []})


# ── hashes, slugs, counts ─────────────────────────────────────────────────────────────────────


def test_draft_sha_is_the_narration_modules_and_order_independent():
    from app.services.learn_weekly import narration

    a = _full()
    assert A.content_sha256(a) == narration.draft_sha256(a)
    assert A.content_sha256(dict(reversed(list(a.items())))) == A.content_sha256(a)
    b = json.loads(json.dumps(a))
    b["sections"][0]["content"][0]["readAlong"] = [{"text": "Body.", "start": 0.0, "end": 1.0}]
    assert A.content_sha256(b) != A.content_sha256(a), "merged timings must change the hash"


@pytest.mark.parametrize("title,slug", [
    ("NVIDIA’s AI Toll Booth: How Chips Became the Bottleneck",
     "nvidias-ai-toll-booth-how-chips-became-the-bottleneck-2026-10-23"),
    ("Ünïcode — test!!", "unicode-test-2026-10-23"),
    ("", "money-move-2026-10-23"), ("!!!", "money-move-2026-10-23"), (None, "money-move-2026-10-23"),
    ("Ωμέγα", "money-move-2026-10-23"),
])
def test_slugs_are_kebab_ascii_dated(title, slug):
    assert A.make_slug(title, PUB) == slug


def test_a_long_title_is_cut_at_a_word_and_stays_inside_the_ledger_limit():
    s = A.make_slug("word " * 100, PUB)
    assert len(s) <= A.SLUG_KEBAB_MAX + 11 and not s.startswith("-") and "--" not in s
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", s) and V.SLUG_RE.fullmatch(s)
    assert A.make_slug("x" * 300, PUB) == "x" * A.SLUG_KEBAB_MAX + "-2026-10-23"


@pytest.mark.parametrize("words,minutes", [(0, 1), (99, 1), (300, 2), (500, 3), (700, 4),
                                           (1700, 9), (-5, 1), (True, 1), (None, 1)])
def test_read_time_rounds_half_up_and_never_below_one(words, minutes):
    assert A.read_time_minutes(words) == minutes


def test_sort_orders():
    assert A.sort_order_for(C.KIND_DEEP_DIVE, date(2027, 1, 1)) == -202653      # ISO 2026-W53
    assert A.sort_order_for(C.KIND_DEEP_DIVE, date(2027, 1, 8)) == -202701
    assert A.sort_order_for(C.KIND_SLOT, date(2026, 1, 1)) == 100000
    with pytest.raises(ValueError):
        A.sort_order_for("weekly", PUB)


# ── statistics ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,figure", [
    ("NVIDIA CORP reported revenue of $130.5 billion for the fiscal year.", "$130.5 billion"),
    ("reported cash of $84,000 as of", "$84,000"),
    ("Margins were 12.5% in 2020.", "12.5%"), ("It had 1.2 million subscribers.", "1.2 million"),
    ("It employed 26,000 people.", "26,000"), ("26,000, up from", "26,000"),
    ("In 2006 it introduced CUDA.", None), ("the 2,000th store opened", None),
    ("a net loss of $1.2 billion and revenue of $9 billion", "$1.2 billion"),
    ("", None), (None, None), (42, None),
])
def test_fact_figure_is_the_figure_as_written(text, figure):
    assert A.fact_figure(text) == figure


_KEPT = A.OutlineStatistic(fact_id="S1", label="Revenue", trend="up")


@pytest.mark.parametrize("bad", [
    A.OutlineStatistic(fact_id="S1", label="revenue", trend="up"),          # repeated label
    A.OutlineStatistic(fact_id="S2", label="Platform launch", trend="up"),  # its fact has no figure
    A.OutlineStatistic(fact_id="S9", label="Ghost", trend="up"),            # not on the sheet
    A.OutlineStatistic(fact_id="S1", label="AMD revenue", trend="up"),      # a name the fact lacks
    A.OutlineStatistic(fact_id="S1", label="2024 revenue", trend="up"),     # a year the fact lacks
    A.OutlineStatistic(fact_id="S1", label="   ", trend="up"),              # no label
])
def test_a_statistic_is_dropped_never_guessed(bad):
    # One bad statistic beside one good one (at most MAX_STATISTICS are read, so each case alone).
    assert A.render_statistics([_KEPT, bad], SHEET) == [
        {"value": "$130.5 billion", "label": "Revenue", "trend": "up"}]


def test_only_the_first_max_statistics_are_read_and_a_bad_sheet_reads_none():
    many = [A.OutlineStatistic(fact_id="S1", label=label, trend="neutral")
            for label in ("Revenue", "Annual revenue", "Fiscal 2025 revenue", "Total revenue")]
    assert [s["label"] for s in A.render_statistics(many, SHEET)] == [
        "Revenue", "Annual revenue", "Fiscal 2025 revenue"]
    assert A.render_statistics([], SHEET) == [] and A.render_statistics([_KEPT], None) == []


# ── the model answers ─────────────────────────────────────────────────────────────────────────


def test_outline_parsing_takes_types_not_content():
    raw = {"title": "t", "subtitle": "s", "cardSubtitle": "c", "tagLabel": "x",
           "keyHighlights": [], "sections": [], "statistics": [], "extra": {"ignored": True}}
    o = A.parse_outline(raw)
    assert o.keyHighlights == [] and not hasattr(o, "extra")
    with pytest.raises(ValidationError):
        A.parse_outline({**raw, "sections": [{"title": "t", "icon": "i", "kind": "peg",
                                              "target_words": True}]})
    with pytest.raises(ValidationError):
        A.parse_outline({**raw, "title": None})
    with pytest.raises(TypeError):
        A.parse_outline(["not", "an", "object"])
    with pytest.raises(ValidationError):
        A.parse_section({"blocks": [{"type": "paragraph", "items": "x"}]})


def test_section_blocks_carry_only_their_types_keys():
    draft = A.parse_section({"blocks": [
        {"type": "paragraph", "text": "  P.  ", "items": ["leak"], "icon": "cpu.fill"},
        {"type": "callout", "text": "C.", "icon": "bolt.fill", "style": "info"},
        {"type": "bulletList", "items": [" a ", "b"], "text": "leak"},
        {"type": "quote", "text": "Q."},
    ]})
    assert A.section_blocks(draft) == [
        {"type": "paragraph", "text": "P."},
        {"type": "callout", "icon": "bolt.fill", "style": "info", "text": "C."},
        {"type": "bulletList", "items": ["a", "b"]},
        {"type": "quote", "text": "Q."},                       # kept so the validator refuses it
    ]


@pytest.mark.parametrize("targets", [[300] * 5, [10, 10, 10, 10, 10], [5000] * 6, [1] * 7,
                                     [400, 300, 300, 300, 200], [True, "x", None, 300, 300]])
def test_targets_add_up_to_the_kinds_range_with_a_short_peg(targets):
    kinds = ["peg"] + ["history"] * (len(targets) - 1)
    out = A.normalize_targets(kinds, targets, C.KIND_DEEP_DIVE)
    lo, hi = C.DEEP_DIVE_WORDS
    assert lo <= sum(out) <= hi
    assert out[0] / sum(out) <= A.PEG_TARGET_SHARE + 0.01
    assert all(isinstance(t, int) and t >= 40 for t in out)


def test_targets_without_a_peg_and_for_a_slot():
    out = A.normalize_targets(["history"] * 5, [100, 200, 300, 200, 100], C.KIND_SLOT)
    assert C.SLOT_WORDS[0] <= sum(out) <= C.SLOT_WORDS[1]
    assert A.normalize_targets([], [], C.KIND_SLOT) == []


def test_normalize_outline_is_stable_on_a_second_pass():
    o = _outline()
    again = A.normalize_outline(o, C.KIND_DEEP_DIVE)
    assert [s.target_words for s in again.sections] == pytest.approx(
        [s.target_words for s in o.sections], abs=2)
    assert again.title == o.title and again.tagLabel == o.tagLabel
