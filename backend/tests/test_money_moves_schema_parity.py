"""
Schema parity tests for the Money Moves content pipeline.

These pin the contract between the backend `GET /api/v1/learn/money-moves` response
and the iOS `MoneyMoveArticleDTO` Codable decoder. The backend passes each row's
`content` JSONB through verbatim, so the real guard is that the authored content keeps
the exact camelCase shape the iOS decoder requires — a single bad/renamed field crashes
the whole article screen on decode.

Coverage:
  1. A worst-case `content` dict (minimal required fields, all optionals absent) still
     validates through `MoneyMovesResponse`.
  2. The keys the iOS DTO requires are present at the documented levels, and every
     section-content `type` is one the iOS mapper handles.
  3. The REAL bundled money_moves.json (the single source of truth the seeder also
     reads) decodes and serves cleanly — catches authoring typos before they ship.

No network / Supabase — these exercise the data shape only.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas.money_moves import MoneyMovesResponse

# Keep in sync with the iOS ArticleSectionContentDTO.toContent() switch.
_SUPPORTED_CONTENT_TYPES = {"paragraph", "subheading", "bulletList", "quote", "callout"}

# Required at each level for the iOS decoder (non-optional DTO properties).
_REQUIRED_ARTICLE_KEYS = {
    "slug", "title", "subtitle", "category", "author",
    "readTimeMinutes", "viewCount", "heroGradientColors",
    "keyHighlights", "sections",
}
_REQUIRED_AUTHOR_KEYS = {"name", "title"}
_REQUIRED_HIGHLIGHT_KEYS = {"icon", "title", "description"}

_BUNDLE_JSON = (
    Path(__file__).resolve().parents[2]
    / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
)


def _worst_case_article() -> dict:
    """Minimal article: only the iOS-required (non-optional) fields, every optional absent."""
    return {
        "slug": "test-article",
        "title": "Test Article",
        "subtitle": "",
        "category": "blueprints",
        "author": {"name": "The Alpha", "title": "Investment Research"},
        "readTimeMinutes": 0,
        "viewCount": "",
        "heroGradientColors": [],
        "keyHighlights": [],
        "sections": [
            {
                "title": "Only Section",
                "content": [
                    {"type": "paragraph", "text": "Body."},
                    {"type": "subheading", "text": "Sub"},
                    {"type": "bulletList", "items": ["a", "b"]},
                    {"type": "quote", "text": "Quote.", "attribution": None},
                    {"type": "callout", "text": "Note.", "icon": "info.circle.fill",
                     "style": "highlight"},
                ],
            }
        ],
        # statistics / comments / relatedArticles / tagLabel / isFeatured /
        # hasAudioVersion / audioUrl / commentCount / publishedDaysAgo all absent.
    }


def _assert_spans(spans, where: str) -> None:
    """A read-along span list must be [{text, start, end}] with start <= end, monotonic-ish."""
    assert isinstance(spans, list), f"{where}: readAlong not a list"
    last = -1.0
    for sp in spans:
        assert {"text", "start", "end"} <= sp.keys(), f"{where}: span missing text/start/end ({sp})"
        s, e = sp["start"], sp["end"]
        assert isinstance(s, (int, float)) and isinstance(e, (int, float)), f"{where}: non-numeric span"
        assert s <= e + 1e-6, f"{where}: span start>{s} end {e}"
        assert s >= last - 1e-6, f"{where}: spans not monotonic ({s} < {last})"
        last = s


def _assert_article_shape(article: dict) -> None:
    missing = _REQUIRED_ARTICLE_KEYS - article.keys()
    assert not missing, f"article missing iOS-required keys: {missing}"

    author_missing = _REQUIRED_AUTHOR_KEYS - article["author"].keys()
    assert not author_missing, f"author missing keys: {author_missing}"

    assert isinstance(article["heroGradientColors"], list)
    assert isinstance(article["keyHighlights"], list)
    for hl in article["keyHighlights"]:
        hl_missing = _REQUIRED_HIGHLIGHT_KEYS - hl.keys()
        assert not hl_missing, f"highlight missing keys: {hl_missing}"

    assert isinstance(article["sections"], list) and article["sections"], "sections empty"
    for section in article["sections"]:
        assert "title" in section, "section missing title"
        assert isinstance(section.get("content"), list), "section content not a list"
        for block in section["content"]:
            t = block.get("type")
            assert t in _SUPPORTED_CONTENT_TYPES, f"unsupported content type: {t!r}"
            if t in ("paragraph", "subheading", "quote", "callout"):
                assert block.get("text") is not None, f"{t} block missing text"
                # read-along is optional (additive); validate shape when present.
                if block.get("readAlong") is not None:
                    _assert_spans(block["readAlong"], f"{t}.readAlong")
            if t == "bulletList":
                assert isinstance(block.get("items"), list), "bulletList missing items"
                if block.get("itemsReadAlong") is not None:
                    items_ra = block["itemsReadAlong"]
                    assert isinstance(items_ra, list), "itemsReadAlong not a list"
                    assert len(items_ra) == len(block["items"]), "itemsReadAlong/items length mismatch"
                    for spans in items_ra:
                        _assert_spans(spans, "bulletList.itemsReadAlong[]")


def test_worst_case_article_validates_and_has_ios_keys():
    article = _worst_case_article()
    resp = MoneyMovesResponse(articles=[article])
    assert len(resp.articles) == 1
    _assert_article_shape(resp.articles[0])


def test_readalong_present_validates_and_is_optional():
    """A block carrying read-along timings validates; the worst case (none) also validates."""
    article = _worst_case_article()
    article["sections"][0]["content"] = [
        {"type": "paragraph", "text": "First sentence. Second sentence.",
         "readAlong": [
             {"text": "First sentence.", "start": 0.0, "end": 1.5},
             {"text": "Second sentence.", "start": 1.5, "end": 3.0},
         ]},
        {"type": "bulletList", "items": ["Alpha point.", "Beta point."],
         "itemsReadAlong": [
             [{"text": "Alpha point.", "start": 3.0, "end": 4.0}],
             [{"text": "Beta point.", "start": 4.0, "end": 5.0}],
         ]},
    ]
    resp = MoneyMovesResponse(articles=[article])
    _assert_article_shape(resp.articles[0])


def test_category_values_match_ios_enum():
    # The DB enum / iOS mapper only understand these case names.
    for cat in ("blueprints", "valueTraps", "battles"):
        resp = MoneyMovesResponse(articles=[{**_worst_case_article(), "category": cat}])
        assert resp.articles[0]["category"] == cat


def test_bundled_money_moves_json_decodes_and_serves():
    """The real authored content (single source of truth) must serve cleanly."""
    assert _BUNDLE_JSON.exists(), f"bundle JSON not found at {_BUNDLE_JSON}"
    data = json.loads(_BUNDLE_JSON.read_text())
    assert "articles" in data and data["articles"], "bundle has no articles"

    resp = MoneyMovesResponse(articles=data["articles"])
    assert resp.articles

    slugs = [a["slug"] for a in resp.articles]
    assert len(slugs) == len(set(slugs)), f"duplicate slugs in bundle: {slugs}"
    assert "how-amazon-built-its-moat" in slugs

    for article in resp.articles:
        _assert_article_shape(article)


def test_backend_serves_malformed_article_verbatim_so_ios_must_tolerate():
    """The backend does NO shape validation — it passes each row's `content` dict through as-is. A
    response carrying one article missing iOS-required fields therefore validates fine here. This
    pins the CONTRACT: because the backend can serve a malformed article, the iOS decoder MUST
    tolerate it (MoneyMovesContentModels decodes the article array leniently, dropping the bad one
    and keeping the rest) — otherwise one bad row would blank the entire catalog + all audio."""
    good = _worst_case_article()
    malformed = {"title": "Only a title — missing slug/subtitle/category/author/sections/..."}
    resp = MoneyMovesResponse(
        articles=[good, malformed, {**good, "slug": "second-good", "title": "Second Good"}]
    )
    # Backend passes ALL THREE through unchanged, including the malformed one (List[Dict] passthrough).
    assert len(resp.articles) == 3
    assert resp.articles[1] == malformed
    # The malformed article is missing required keys — the guard the iOS side must survive.
    assert _REQUIRED_ARTICLE_KEYS - resp.articles[1].keys()


def test_bundle_has_at_most_one_featured_article():
    """The hero shows the FIRST isFeatured article and the detail screen hides only that one card
    (by slug) from the category rows. Authoring two featured articles is a mistake — a transient
    two-featured state was the trigger for the 'second featured vanishes' bug. Keep it to one."""
    data = json.loads(_BUNDLE_JSON.read_text())
    featured = [a["slug"] for a in data["articles"] if a.get("isFeatured")]
    assert len(featured) <= 1, f"more than one isFeatured article in bundle: {featured}"


def test_bundle_titles_are_unique():
    """Card taps resolve by slug first, but a duplicate TITLE still muddies title-keyed lookups and
    the dedup in the catalog. Keep authored titles unique."""
    data = json.loads(_BUNDLE_JSON.read_text())
    titles = [a["title"] for a in data["articles"]]
    assert len(titles) == len(set(titles)), f"duplicate titles in bundle: {titles}"


# --- Malformed-shape contract pins ------------------------------------------------------------
# The backend serves `content` VERBATIM (List[Dict] passthrough, no shape validation), so the shapes
# below reach iOS unchanged. iOS was hardened (MoneyMovesContentModels) to DEGRADE on each rather
# than drop the whole article — closing the asymmetry with the always-lenient Journey path. These
# tests pin the backend half of that contract: the payloads must serve without the backend rejecting
# them, so the iOS-tolerance behavior is the load-bearing guarantee. (iOS has no XCTest target; the
# iOS side is verified by build + the guard logic in MoneyMovesContentModels/MoneyMoveArticleSectionContent.)


def _article_with_content(blocks: list) -> dict:
    art = _worst_case_article()
    art["sections"][0]["content"] = blocks
    return art


def test_backend_serves_flat_itemsReadAlong_verbatim():
    """`itemsReadAlong` authored FLAT ([{...}]) instead of nested ([[...]]) is an easy authoring slip
    (text blocks use flat `readAlong`, bulletLists use nested). The backend passes it through; iOS
    must decode it with `try?` and degrade to no-timings rather than dropping the whole article."""
    flat = _article_with_content([
        {"type": "bulletList", "items": ["a", "b"],
         "itemsReadAlong": [{"text": "a", "start": 0.0, "end": 1.0}]},  # FLAT, should be [[...]]
    ])
    resp = MoneyMovesResponse(articles=[flat])
    assert resp.articles[0]["sections"][0]["content"][0]["itemsReadAlong"] == [
        {"text": "a", "start": 0.0, "end": 1.0}
    ]


def test_backend_serves_empty_inner_itemsReadAlong_verbatim():
    """An empty INNER span list (`[[...], [], [...]]`) — an alignment run that produced no spans for
    one bullet — is served verbatim. iOS must render that bullet as plain text, not blank (F1)."""
    art = _article_with_content([
        {"type": "bulletList", "items": ["a", "b", "c"],
         "itemsReadAlong": [
             [{"text": "a", "start": 0.0, "end": 1.0}],
             [],  # <-- the F1 trigger: no spans for bullet #2
             [{"text": "c", "start": 2.0, "end": 3.0}],
         ]},
    ])
    resp = MoneyMovesResponse(articles=[art])
    items_ra = resp.articles[0]["sections"][0]["content"][0]["itemsReadAlong"]
    assert items_ra[1] == [], "empty inner span list must survive passthrough (iOS renders plain text)"


def test_backend_serves_wrong_typed_scalars_verbatim():
    """A numeric `viewCount` / fractional `readTimeMinutes` (Studio edit or programmatic build) is
    served verbatim; iOS must COERCE them rather than drop the article on a type mismatch (F2)."""
    art = _worst_case_article()
    art["viewCount"] = 1_200_000        # number, not the "4.2M" string iOS expects
    art["readTimeMinutes"] = 5.5        # fractional, not Int
    resp = MoneyMovesResponse(articles=[art])
    assert resp.articles[0]["viewCount"] == 1_200_000
    assert resp.articles[0]["readTimeMinutes"] == 5.5


def test_backend_serves_typeless_content_block_verbatim():
    """A content block missing its `type` discriminator is served verbatim; iOS must drop just that
    block (toContent -> nil) rather than throwing and dropping the whole article (F2)."""
    art = _article_with_content([
        {"text": "orphaned block with no type"},   # no `type`
        {"type": "paragraph", "text": "this one is fine"},
    ])
    resp = MoneyMovesResponse(articles=[art])
    blocks = resp.articles[0]["sections"][0]["content"]
    assert "type" not in blocks[0] and blocks[1]["type"] == "paragraph"
