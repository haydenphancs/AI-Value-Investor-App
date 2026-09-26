"""
Backend ↔ iOS contract for the search-screen chips.

`GET /api/v1/search/trending` is decoded by `Models/SearchTrendingModels.swift`, and
`POST /api/v1/search/picks` is encoded from the same file. A field renamed on one side and
not the other is a silent blank (the Swift decoders default every field) or a 422 on every
pick — neither of which any other test would notice.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.schemas.search_trending import (
    PICK_TYPES,
    SECTION_KINDS,
    SearchPickRequest,
    SearchTrendingItemResponse,
    SearchTrendingResponse,
    SearchTrendingSectionResponse,
)

_ROOT = Path(__file__).resolve().parents[2]
_SWIFT = _ROOT / "frontend/ios/ios/Models/SearchTrendingModels.swift"
_CURATED = _ROOT / "backend/data/search_trending_popular.json"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


SRC = _strip_comments(_SWIFT.read_text())


def _block(header: str) -> str:
    start = SRC.index(header)
    open_brace = SRC.index("{", start)
    depth = 0
    for i in range(open_brace, len(SRC)):
        if SRC[i] == "{":
            depth += 1
        elif SRC[i] == "}":
            depth -= 1
            if depth == 0:
                return SRC[open_brace:i + 1]
    raise AssertionError(f"unbalanced braces after {header!r}")


def _wire_keys(struct: str) -> set:
    """The JSON keys a Swift DTO's CodingKeys map to."""
    body = _block(f"struct {struct}")
    ck = body[body.index("enum CodingKeys"):]
    ck = ck[:ck.index("}") + 1]
    out = set()
    for m in re.finditer(r"case\s+(\w+)(?:\s*=\s*\"([^\"]+)\")?", ck):
        out.add(m.group(2) or m.group(1))
    return out


def test_every_response_field_has_a_swift_coding_key():
    pairs = [
        (SearchTrendingResponse, "SearchTrendingDTO"),
        (SearchTrendingSectionResponse, "SearchTrendingSectionDTO"),
        (SearchTrendingItemResponse, "SearchTrendingItemDTO"),
    ]
    for model, struct in pairs:
        backend = set(model.model_fields)
        swift = _wire_keys(struct)
        assert backend == swift, f"{model.__name__} {sorted(backend)} vs {struct} {sorted(swift)}"


def test_the_pick_request_matches_the_swift_encoder():
    body = _block("struct SearchPickRequest")
    swift = set(re.findall(r"let\s+(\w+)\s*:", body))
    assert swift == {"symbol", "type"} == set(SearchPickRequest.model_fields)


def test_section_kinds_equal_the_swift_enum():
    body = _block("enum SearchTrendingKind")
    raw = []
    for m in re.finditer(r"case\s+(\w+)(?:\s*=\s*\"([^\"]+)\")?", body):
        raw.append(m.group(2) or m.group(1))
    assert tuple(raw) == SECTION_KINDS


def test_supported_types_equal_pick_types():
    m = re.search(r"supportedTypes: Set<String> = \[([^\]]*)\]", SRC)
    assert m and {s.strip().strip('"') for s in m.group(1).split(",")} == set(PICK_TYPES)


def test_the_response_never_carries_a_count():
    fields = set(SearchTrendingResponse.model_fields) | set(SearchTrendingSectionResponse.model_fields) \
        | set(SearchTrendingItemResponse.model_fields)
    assert not fields & {"count", "picks", "adders", "rank", "score", "users"}


def test_a_worst_case_body_validates_and_defaults():
    body = {"sections": [{"kind": "trending_searches", "items": [{"symbol": "NVDA"}]},
                         {"kind": "future_kind", "items": []}]}
    parsed = SearchTrendingResponse.model_validate(body)
    assert parsed.window_days == 7 and parsed.stock_sections == []
    assert parsed.sections[0].items[0].type == "stock" and parsed.sections[0].items[0].name == ""


def test_the_bundled_ios_fallback_is_a_subset_of_the_server_list():
    """The app's offline floor must never show a ticker the server's curated list would not."""
    curated = json.loads(_CURATED.read_text())

    def swift_list(name: str) -> set:
        start = SRC.index(f"static let {name}: [SearchTrendingItem] = [")
        chunk = SRC[start:SRC.index("\n    ]", start)]
        return set(re.findall(r'symbol: "([^"]+)", name: "[^"]*", type: "([^"]+)"', chunk))

    server_all = {(i["symbol"], i["type"]) for i in curated["all"]}
    server_stocks = {(i["symbol"], i["type"]) for i in curated["stocks"]}
    bundled_all, bundled_stocks = swift_list("bundledAll"), swift_list("bundledStocks")
    assert bundled_all and bundled_all <= server_all
    assert bundled_stocks and bundled_stocks <= server_stocks
    assert all(t == "stock" for _, t in bundled_stocks)
    assert not any("." in s for s, _ in bundled_all | bundled_stocks), "BRK-B, never BRK.B"
