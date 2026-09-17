"""`StockSearchResult` identity guards (2026-09-17).

THE BUG. `GET /stocks/search` deliberately returns TWO rows for one symbol when a coin in the
crypto map is also a real US listing — "BTC" is Bitcoin AND the Grayscale Bitcoin Mini Trust
ETF ("ETH" its Ethereum twin, "SOL" an NYSE equity). `StockSearchResult.id` was the bare
ticker, so the two rows were ONE `ForEach` child. SwiftUI's documented "undefined results"
for duplicate ids showed up as: the ETF row drawn as an empty slot, and the tap on the row
labelled "BTC · Bitcoin · CRYPTO" opening the ETF screen. Reproduced on the simulator in
`TickerLiveSearchSheet` (the detail-screen / Tracking magnifier); `AddAssetSheet` iterates
the same array. The Home `SearchView` never saw it — it re-wraps rows with a UUID.

THE FIX is one line: `id = "\\(ticker)_\\(type ?? "stock")"`, byte-equal to `SearchSelection.id`
(the selection a row builds is `SearchSelection(symbol: ticker, type: type ?? "stock")`, and
`TickerDetailView` / `IndexDetailView` key `.navigationDestination(item:)` on it).

These guards pin (a) that identity formula, (b) that every raw `[StockSearchResult]` list
iterates by it (no `id: \\.ticker` override can reopen the bug), (c) that `SearchSelection.id`
stays the same composite. The backend half — the twins carry DIFFERENT types — is pinned by
tests/test_stock_search_bugs.py::test_a_ticker_collision_is_two_rows_the_ios_identity_can_tell_apart.

Comment-stripped, brace-bound, mutation-tested by hand (`.claude/rules/testing.md` §3).
"""
from __future__ import annotations

import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_REPO_SWIFT = _IOS / "Core" / "Repositories" / "StockRepository.swift"
_SHEET = _IOS / "Views" / "Molecules" / "TickerSearchSheet.swift"
_TRACKING = _IOS / "Views" / "Screens" / "TrackingView.swift"
_TARGET = _IOS / "Views" / "Screens" / "TargetSearchSheet.swift"
_TICKER_DETAIL = _IOS / "Views" / "Screens" / "TickerDetailView.swift"
_INDEX_DETAIL = _IOS / "Views" / "Screens" / "IndexDetailView.swift"

# The exact Swift formula. `\(ticker)` then `_` then `\(type ?? "stock")` — the SAME bytes as
# `SearchSelection.id` renders for the selection the row builds.
_ID_FORMULA = r'var id:\s*String\s*\{\s*"\\\(ticker\)_\\\(type \?\? "stock"\)"\s*\}'


def _strip_comments(src: str) -> str:
    """Block comments, full-line `//` comments AND trailing `//` comments — the explanatory
    comment next to the fix contains every token these guards look for."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _decl_body(src: str, prefix: str) -> str:
    at = src.index(prefix)
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced braces after {prefix!r}")


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_comments(path.read_text(encoding="utf-8"))


# ── (a) the identity is symbol + type ───────────────────────────────────────

def test_search_result_identity_is_symbol_and_type():
    src = _code(_REPO_SWIFT)
    body = _decl_body(src, "struct StockSearchResult")
    decl_line = src[src.index("struct StockSearchResult"): src.index("{", src.index("struct StockSearchResult"))]
    assert "Identifiable" in decl_line, decl_line
    assert re.search(_ID_FORMULA, body), body
    assert not re.search(r"var id:\s*String\s*\{\s*ticker\s*\}", body), (
        "the bare-ticker identity is the bug: (BTC, crypto) and (BTC, etf) become one ForEach child"
    )
    # anti-vacuity: the fields the formula reads are still declared as they are decoded
    assert re.search(r"let ticker:\s*String\b", body) and re.search(r"let type:\s*String\?", body)
    assert 'case ticker = "symbol"' in body


# ── (b) every raw [StockSearchResult] list iterates by that identity ────────

@pytest.mark.parametrize("path,decl,foreach", [
    (_SHEET, "struct TickerLiveSearchSheet", "ForEach(searchResults)"),
    (_TRACKING, "struct AddAssetSheet", "ForEach(searchResults)"),
    (_TARGET, "struct TargetSearchSheet", "ForEach(results)"),
])
def test_raw_search_result_lists_iterate_by_the_identifiable_id(path, decl, foreach):
    body = _decl_body(_code(path), decl)
    assert re.search(r"\[StockSearchResult\]", body), f"{decl} no longer holds the raw DTO list"
    # anti-vacuity: the call is still there (with or without the benign `id: \\.id` spelling)
    assert foreach.rstrip(")") in body, f"{decl}: expected `{foreach}` (anti-vacuity)"
    # An explicit key on the ticker (or the name) would reintroduce the collision even with
    # the composite `id` in place.
    assert not re.search(r"ForEach\((searchResults|results)\s*,\s*id:\s*\\\.(?!id\b)", body), (
        f"{decl}: iterate by the Identifiable id, never by an explicit ticker/name key"
    )


# ── (c) SearchSelection keeps the same composite, and its consumers exist ───

def test_search_selection_identity_matches_the_result_identity():
    src = _code(_SHEET)
    sel = _decl_body(src, "struct SearchSelection")
    assert re.search(r'var id:\s*String\s*\{\s*"\\\(symbol\)_\\\(type\)"\s*\}', sel), sel
    assert re.search(r"let type:\s*String\b(?!\?)", sel), "type is non-Optional on the selection"
    # the row builds the selection with the SAME default the result identity uses
    row = _decl_body(src, "private func resultRow")
    assert re.search(
        r"SearchSelection\(\s*symbol:\s*result\.ticker,\s*type:\s*result\.type \?\? \"stock\"\s*\)", row
    ), row
    # and real consumers key navigation on that identity
    for screen in (_TICKER_DETAIL, _INDEX_DETAIL):
        assert ".navigationDestination(item: $selectedSearchResult)" in _code(screen), screen.name
