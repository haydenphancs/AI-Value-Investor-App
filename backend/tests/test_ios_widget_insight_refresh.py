"""A new Insights card reaches the Home Screen tile right after its run (owner, 2026-10-30).

"currently, the widget doesn't show the title of the Insight card in the Updates screen. i
need the widget to show it out. right after the Insight has new content or a new run."

The backend half is behaviour-tested elsewhere: the card's headline outranks the dated causes
(`test_daily_move_attribution.py`), and a stored card drops the widget payloads built from the
old one (`test_widget_inflight_behaviour.py`, `test_news_insight_conclusion.py`). The extension
never fetches Holdings, so the APP has to ask for the new tile — that half is pinned here,
because there is no XCTest target:

* `UpdatesViewModel` hands every card it receives FROM THE NETWORK — the feed load and the
  re-poll that waits out a fallback card — to `noteInsightForWidget`, with the DTO's own
  `generated_at` (the summary's `updatedAt` falls back to now when it does not parse, which
  would beat every snapshot). Never the disk snapshot or `feedCache`: those are not new.
* `WidgetRefreshService.insightCardArrived` refreshes, as a content change (debounced, past
  the 60 s throttle), only for an AI card the tile shows — the Holdings head mover's or the
  market card — written after the stored snapshot was BUILT (`asOf`).
"""

from __future__ import annotations

import re
from pathlib import Path

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_UPDATES_VM = _IOS / "ViewModels/UpdatesViewModel.swift"
_REFRESH = _IOS / "Core/Services/WidgetRefreshService.swift"


def _code(path: Path) -> str:
    """The file with `//` and `/* */` comments removed (string literals kept)."""
    assert path.exists(), f"guard is stale — {path.name} moved; update this guard, do not delete it"
    src, out, i, in_str = path.read_text(encoding="utf-8"), [], 0, False
    while i < len(src):
        c = src[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < len(src):
                out.append(src[i + 1])
                i += 2
                continue
            in_str = c != '"'
        elif c == '"':
            in_str = True
            out.append(c)
        elif src.startswith("//", i):
            i = src.find("\n", i) if "\n" in src[i:] else len(src)
            continue
        elif src.startswith("/*", i):
            i = src.find("*/", i + 2) + 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _body(src: str, decl_regex: str) -> str:
    """The brace-balanced body of the first declaration matching `decl_regex`."""
    m = re.search(decl_regex, src)
    assert m, f"declaration not found: {decl_regex}"
    start = src.index("{", m.end() - 1)
    depth, in_str = 0, False
    for j in range(start, len(src)):
        c = src[j]
        if in_str:
            in_str = not (c == '"' and src[j - 1] != "\\")
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced body after {decl_regex}")


def test_both_network_card_paths_reach_the_widget():
    src = _code(_UPDATES_VM)
    load = _body(src, r"private func loadFeed\(for tab: NewsFilterTab, force: Bool\) async\s*\{")
    assigned = "insightSummary = response.insight.flatMap { NewsInsightSummary(dto: $0) }"
    noted = "noteInsightForWidget(insightSummary, stamp: response.insight?.generatedAt, scope: scope)"
    assert assigned in load and noted in load
    assert load.index(assigned) < load.index(noted)
    repoll = _body(src, r"private func repollInsight\(scope: String, token: UUID\) async -> Bool\s*\{")
    assert "noteInsightForWidget(card, stamp: dto.generatedAt, scope: scope)" in repoll
    # Exactly those two: the disk snapshot and the in-memory cache are not new cards.
    assert src.count("noteInsightForWidget(") == 3, "two calls and the declaration"
    for decl in (r"private func seedFromSnapshot\(\)\s*\{",
                 r"private static func snapshotInsight\(_ dto: AIInsightCardDTO\?\) -> NewsInsightSummary\?\s*\{"):
        assert "noteInsightForWidget" not in _body(src, decl)


def test_the_helper_passes_the_dtos_own_stamp():
    helper = _body(_code(_UPDATES_VM), r"private func noteInsightForWidget\([^)]*\)\s*\{")
    assert "WidgetRefreshService.shared.insightCardArrived(" in helper
    assert "generatedAt: UpdatesDateParser.parse(stamp)" in helper
    assert "isAIGenerated: card.isAIGenerated" in helper
    assert "updatedAt" not in helper, "updatedAt falls back to now on an unparseable stamp"


def test_only_a_newer_ai_card_the_tile_shows_refreshes_it():
    body = _body(_code(_REFRESH), r"func insightCardArrived\([^)]*\)\s*\{")
    assert re.search(
        r"guard isAIGenerated, let generatedAt, let stored = WidgetSnapshotStore\.read\(\)", body
    )
    assert re.search(r"scope == UpdatesScope\.market\s*\{\s*snapshot = stored\.market", body)
    assert "stored.portfolio?.headlineMover?.ticker" in body
    assert "caseInsensitiveCompare(scope) == .orderedSame" in body
    assert "guard let snapshot, generatedAt > snapshot.asOf" in body
    # A content change, not `refresh()`: that one is throttled for 60 s after the foreground
    # refresh that usually ran moments before the card arrived.
    assert body.rstrip("}").rstrip().endswith("contentChanged()")
    assert "refresh(" not in body
