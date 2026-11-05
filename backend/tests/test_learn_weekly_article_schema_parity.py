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
import logging
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
        "cardSubtitle": "A card line.", "tagLabel": "Case Study",
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
    # The catalog's own byline (owner 2026-11-01: "no "AI-assisted" label").
    assert a["author"] == C.AUTHOR == {"name": "Caydex Research", "title": "Editorial", "isVerified": False}
    assert a["viewCount"] == "" and a["publishedDaysAgo"] == 0
    assert a["isFeatured"] is True and a["hasAudioVersion"] is False and a["audioUrl"] is None
    assert a["tagLabel"] == "CASE STUDY"
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


def _enums(node, path=""):
    """Every (path, enum list) in a response schema."""
    if isinstance(node, dict):
        if isinstance(node.get("enum"), list):
            yield path, node["enum"]
        for k, v in node.items():
            yield from _enums(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _enums(v, f"{path}[{i}]")


def test_no_response_schema_carries_the_icon_list_as_an_enum():
    """Gemini refused both schemas while the 79-icon list was an enum ("too many states for
    serving", 2026-10-23): every Deep Dive died at its first call. The list lives in the prompt."""
    schemas = {"outline": A.outline_response_schema(["S1", "P1"]), "section": A.SECTION_RESPONSE_SCHEMA}
    for name, schema in schemas.items():
        for path, enum in _enums(schema):
            assert not set(enum) & set(rules.ICON_ALLOWLIST), f"{name}{path} is an icon enum again"
    props = schemas["outline"]["properties"]
    assert props["keyHighlights"]["items"]["properties"]["icon"] == {"type": "STRING"}
    assert props["sections"]["items"]["properties"]["icon"] == {"type": "STRING"}
    assert A.SECTION_RESPONSE_SCHEMA["properties"]["blocks"]["items"]["properties"]["icon"] == {"type": "STRING"}
    assert A.DISCLOSURE_ICON in rules.ICON_ALLOWLIST


def test_the_prompt_lists_every_allowed_icon_and_no_other():
    from app.services.learn_weekly import prompts as Pr
    assert tuple(A.ICON_CHOICES) == tuple(sorted(rules.ICON_ALLOWLIST))
    listed = Pr.SYSTEM_BODY.split("copied character for character: ", 1)[1].split(".\n", 1)[0]
    assert [s.strip() for s in listed.split(",")] == list(A.ICON_CHOICES)
    assert Pr.PROMPT_VERSION >= "2026-10-23.1"


def test_the_prompt_says_us_uk_eu_without_dots_like_marketing():
    """Grounding refuses a dotted "U.S." ON PURPOSE (marketing: it hides sentence ends and names
    such as "U.S. Steel"; tests/test_marketing_residuals_compliance.py). Marketing's prompt says so
    up front; the Learn prompt did not, so every Deep Dive spent a repair round on it (2026-10-23)."""
    from app.services.learn_weekly import prompts as Pr
    from app.services.marketing import grounding as g
    assert "Write US, UK and EU without dots" in Pr.SYSTEM_BODY
    assert "without dots" in Pr.hint("ungrounded_acronym")
    assert "U.S" not in g.ACRONYMS and "US" in g.ACRONYMS


def test_the_fact_id_enum_is_capped_for_gemini(caplog):
    """Measured 2026-10-23: 100 ids accepted, 150 refused. Over FACT_ID_ENUM_MAX the enum is
    dropped (with a WARNING) and the writer's unknown_fact check is the guard."""
    at_cap = [f"S{i}" for i in range(A.FACT_ID_ENUM_MAX)]
    over = at_cap + ["P1"]
    fid = lambda s: s["properties"]["statistics"]["items"]["properties"]["fact_id"]
    assert fid(A.outline_response_schema(at_cap))["enum"] == at_cap
    with caplog.at_level(logging.WARNING):
        assert "enum" not in fid(A.outline_response_schema(over))
    assert "exceed FACT_ID_ENUM_MAX" in caplog.text
    for path, enum in _enums(A.outline_response_schema(at_cap)):
        assert len(enum) <= A.FACT_ID_ENUM_MAX, path


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
    # Code-owned on purpose: the validators would refuse it as model text.
    issues = V.validate_block("x", A.disclosure_block(), section_kind="lessons")
    assert {"code_owned", "banned_phrase"} <= {i.code for i in issues}
    assert "not investment advice" in C.DISCLOSURE_TEXT


_AI_WORDING = re.compile(r"(?<![a-z])(?:ai|a\.i\.|artificial intelligence|llms?|models?|automated|machine|"
                         r"generated)(?![a-z])", re.IGNORECASE)


def test_no_ai_label_on_the_byline_or_the_closing():
    # Owner decision 2026-11-01: "no "AI-assisted" label" — the Terms and Support pages carry the
    # general AI line instead (tests/test_legal_pages.py).
    assert not _AI_WORDING.search(C.AUTHOR["title"]) and not _AI_WORDING.search(C.AUTHOR["name"])
    assert not _AI_WORDING.search(C.DISCLOSURE_TEXT)
    assert C.AUTHOR["isVerified"] is False
    # The legacy closing still matches (so the regex is not vacuous) and is not today's closing.
    assert all(_AI_WORDING.search(t) for t in C.LEGACY_DISCLOSURE_TEXTS)
    for worded in ("Written with A.I. help", "A.I.", "Model-written", "AI-assisted · Editor-reviewed"):
        assert _AI_WORDING.search(worded), worded
    assert not _AI_WORDING.search("Editorial") and not _AI_WORDING.search("Caydex Research")
    assert C.DISCLOSURE_TEXT not in C.LEGACY_DISCLOSURE_TEXTS
    # Every bundled article carries this byline: a weekly piece reads like the catalog.
    assert {(a["author"]["name"], a["author"]["title"]) for a in _bundle()} == {
        (C.AUTHOR["name"], C.AUTHOR["title"])}


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
    # The model said "up", but S1 states no change, so the card's arrow is neutral.
    assert A.render_statistics([_KEPT, bad], SHEET) == [
        {"value": "$130.5 billion", "label": "Revenue", "trend": "neutral"}]


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
        {"type": "bulletList", "items": [" a ", "b"], "text": " Two things matter: "},
        {"type": "quote", "text": "Q."},
    ]})
    assert A.section_blocks(draft) == [
        {"type": "paragraph", "text": "P."},
        {"type": "callout", "icon": "bolt.fill", "style": "info", "text": "C."},
        {"type": "paragraph", "text": "Two things matter:"},   # the list's lead-in, before it
        {"type": "bulletList", "items": ["a", "b"]},
        {"type": "quote", "text": "Q."},                       # kept so the validator refuses it
    ]


def test_every_block_must_carry_text_in_the_schema():
    """With only `type` required, gemini-2.5-flash looped on bare {"type": "callout"} objects:
    2 of 6 live 3M sections came back as 15-16 empty blocks and ~130 words (2026-10-23)."""
    item = A.SECTION_RESPONSE_SCHEMA["properties"]["blocks"]["items"]
    assert item["required"] == ["type", "text"]


@pytest.mark.parametrize("block,expected", [
    ({"type": "bulletList", "items": ["a"], "text": ""}, [{"type": "bulletList", "items": ["a"]}]),
    ({"type": "bulletList", "items": ["a"], "text": "   "}, [{"type": "bulletList", "items": ["a"]}]),
    ({"type": "bulletList", "items": ["a"]}, [{"type": "bulletList", "items": ["a"]}]),
    # No items: no lead-in paragraph; the empty list reaches the validator, which refuses it.
    ({"type": "bulletList", "items": [], "text": "Three reasons:"},
     [{"type": "bulletList", "items": []}]),
])
def test_a_list_lead_in_becomes_a_paragraph_only_when_it_has_text_and_the_list_has_items(
        block, expected):
    assert A.section_blocks(A.parse_section({"blocks": [block]})) == expected


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


# ── section blocks: empty blocks and callout defaults (2026-10-23 live previews) ───────────────


def test_empty_blocks_are_dropped_with_a_warning_and_real_text_is_untouched(caplog):
    """gemini-2.5-flash looped on bare {"type": "callout"} blocks (only `type` is required) and
    sank whole sections; an empty block has nothing to judge, so it is dropped — loudly."""
    draft = A.SectionDraft.model_validate({"blocks": [
        {"type": "paragraph", "text": "  Real words stay exactly as written.  "},
        {"type": "callout"}, {"type": "callout", "text": "   "},
        {"type": "bulletList", "items": ["", "  "]}, {"type": "paragraph", "text": None},
        {"type": "bulletList", "items": ["One", " two "]},
    ]})
    with caplog.at_level(logging.WARNING):
        blocks = A.section_blocks(draft)
    assert blocks == [{"type": "paragraph", "text": "Real words stay exactly as written."},
                      {"type": "bulletList", "items": ["One", "two"]}]
    assert "dropped 4 empty block(s) of 6" in caplog.text


@pytest.mark.parametrize("style, icon, want_style, want_icon", [
    ("warning", None, "warning", "exclamationmark.triangle.fill"),
    ("highlight", "not.a.real.symbol", "highlight", "lightbulb.fill"),
    (None, None, "info", "info.circle.fill"),
    ("loud", "chart.bar.fill", "info", "chart.bar.fill"),
])
def test_a_callout_gets_a_default_style_and_icon_never_new_text(style, icon, want_style, want_icon):
    if icon == "chart.bar.fill" and icon not in rules.ICON_ALLOWLIST:
        icon = want_icon = sorted(rules.ICON_ALLOWLIST)[0]          # any allowed icon is kept
    draft = A.SectionDraft.model_validate({"blocks": [
        {"type": "callout", "text": "A lesson.", "style": style, "icon": icon}]})
    assert A.section_blocks(draft) == [
        {"type": "callout", "icon": want_icon, "style": want_style, "text": "A lesson."}]
    assert set(A.CALLOUT_STYLE_ICONS) == set(C.CALLOUT_STYLES)
    assert set(A.CALLOUT_STYLE_ICONS.values()) <= rules.ICON_ALLOWLIST



# ── one figure per card, and only a stated direction (2026-10-23 reader panel) ─────────────────


@pytest.mark.parametrize("text,figures", [
    ("NVIDIA CORP reported revenue of $130.5 billion for the fiscal year.", ["$130.5 billion"]),
    ("a net loss of $1.2 billion and revenue of $9 billion", ["$1.2 billion", "$9 billion"]),
    ("~8,200 projects, representing 1,312 GW of generation", ["8,200", "1,312"]),
    ("cost of revenue increased $17.7 billion, or 44%", ["$17.7 billion", "44%"]),
    ("In 2006 it introduced CUDA.", []), (None, []),
])
def test_fact_figures_lists_every_distinct_figure_once(text, figures):
    assert A.fact_figures(text) == figures


def test_a_card_on_a_fact_with_several_figures_is_dropped(caplog):
    """Oracle's fact held total cloud revenue AND IaaS growth; the card printed the first figure
    under the IaaS label. Two figures → no card (WARNING), never a guess."""
    sheet = C.FactSheet(C.KIND_DEEP_DIVE, "Cloud", "silicon-rush", (), (
        C.Fact("P2", "Total cloud revenue (IaaS plus SaaS) was $11.6 billion, up 40%, and "
                     "Cloud Infrastructure (IaaS) revenue grew 121%.", "sec_peg", "x", ""),
        C.Fact("S3", "Oracle reported revenue of $57.4 billion for the fiscal year ended May 31, "
                     "2026.", "sec_xbrl", "x", "")))
    stats = [A.OutlineStatistic(fact_id="P2", label="Cloud Infrastructure growth", trend="up"),
             A.OutlineStatistic(fact_id="S3", label="Revenue", trend="neutral")]
    with caplog.at_level(logging.WARNING):
        out = A.render_statistics(stats, sheet)
    assert out == [{"value": "$57.4 billion", "label": "Revenue", "trend": "neutral"}]
    assert "states 3 figures" in caplog.text


@pytest.mark.parametrize("trend,text,shown", [
    ("up", "Revenue increased to $10 billion.", "up"),
    ("down", "Revenue declined to $10 billion.", "down"),
    ("up", "Revenue declined to $10 billion.", "neutral"),        # the fact says the opposite
    ("up", "Revenue was $10 billion.", "neutral"),                # the fact states no change
    ("up", "It grew in one unit and fell in another: $10 billion.", "neutral"),   # both
    ("sideways", "Revenue increased to $10 billion.", "neutral"),
    ("neutral", "Revenue increased to $10 billion.", "neutral"),
])
def test_an_arrow_needs_its_fact_to_state_that_direction(trend, text, shown):
    assert A.supported_trend(trend, text) == shown


def test_a_subheading_repeating_the_section_title_is_dropped(caplog):
    """iOS draws section.title, then the blocks: five of six preview sections opened with a
    subheading equal to their title, so readers saw every heading twice (2026-10-23)."""
    draft = A.SectionDraft.model_validate({"blocks": [
        {"type": "subheading", "text": "Asset Lives and Depreciation"},
        {"type": "paragraph", "text": "Servers wear out on a schedule the accounts choose."},
        {"type": "subheading", "text": "  asset lives and depreciation. "},
        {"type": "subheading", "text": "Why the schedule matters"},
    ]})
    with caplog.at_level(logging.INFO):
        blocks = A.section_blocks(draft, "Asset Lives and Depreciation")
    assert blocks == [
        {"type": "paragraph", "text": "Servers wear out on a schedule the accounts choose."},
        {"type": "subheading", "text": "Why the schedule matters"}]
    assert "dropped 2 repeated block(s)" in caplog.text
    # without a title nothing is dropped for repeating it
    assert len(A.section_blocks(draft)) == 4


def test_the_prompt_says_not_to_repeat_the_title():
    from app.services.learn_weekly import prompts as Pr
    assert "never open a section with a subheading that repeats that title" in Pr.SYSTEM_BODY
    assert Pr.PROMPT_VERSION >= "2026-10-23.3"


def test_a_lead_in_repeating_a_block_of_its_section_is_dropped():
    """Review round (2026-10-23): the model wrote an intro paragraph and repeated it as the list's
    text, which showed the sentence twice and tripped the article's duplicate rule."""
    draft = A.parse_section({"blocks": [
        {"type": "paragraph", "text": "Several limits shape how fast the business can grow:"},
        {"type": "bulletList", "text": "Several limits shape how fast the business can grow:",
         "items": ["land", "power"]},
        {"type": "bulletList", "text": "Two more matter as well:", "items": ["cooling", "staff"]},
    ]})
    assert A.section_blocks(draft) == [
        {"type": "paragraph", "text": "Several limits shape how fast the business can grow:"},
        {"type": "bulletList", "items": ["land", "power"]},
        {"type": "paragraph", "text": "Two more matter as well:"},
        {"type": "bulletList", "items": ["cooling", "staff"]},
    ]


def test_a_lead_in_differing_only_by_its_closing_mark_is_the_same_sentence():
    """Review round 2: "X." as the intro paragraph and "X:" as the list's text showed twice."""
    draft = A.parse_section({"blocks": [
        {"type": "paragraph", "text": "Several limits shape how fast the business can grow."},
        {"type": "bulletList", "text": "Several limits shape how fast the business can grow:",
         "items": ["land", "power"]},
    ]})
    assert A.section_blocks(draft) == [
        {"type": "paragraph", "text": "Several limits shape how fast the business can grow."},
        {"type": "bulletList", "items": ["land", "power"]},
    ]


def test_a_lead_in_repeated_by_a_later_block_is_dropped():
    """Review round 3: a lead-in repeated by a LATER paragraph of its answer passed the section
    check, then the article's duplicate rule (no repair) rejected the whole generation."""
    draft = A.parse_section({"blocks": [
        {"type": "bulletList", "text": "Several limits shape how fast the business can grow:",
         "items": ["land", "power"]},
        {"type": "paragraph", "text": "Several limits shape how fast the business can grow."},
    ]})
    assert A.section_blocks(draft) == [
        {"type": "bulletList", "items": ["land", "power"]},
        {"type": "paragraph", "text": "Several limits shape how fast the business can grow."},
    ]
