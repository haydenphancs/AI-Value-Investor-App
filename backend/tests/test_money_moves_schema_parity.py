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
            if t == "bulletList":
                assert isinstance(block.get("items"), list), "bulletList missing items"


def test_worst_case_article_validates_and_has_ios_keys():
    article = _worst_case_article()
    resp = MoneyMovesResponse(articles=[article])
    assert len(resp.articles) == 1
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
