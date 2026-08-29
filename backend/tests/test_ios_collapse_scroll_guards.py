"""Collapsing a report module must not throw the reader down the page.

TestFlight, ticker report → Deep Dive Modules: *"when i collapse a card, the screen
should stay as where it is, not move to the bottom. if currently users see 'The
Revenue Engine', then when they collapse 'Recent Price Movement', it should collapse
that card up above only, so users can stay to see The Revenue Engine."*

`TickerReportView` is a plain `ScrollView` over an eager `VStack`. Collapsing removes
the module's content in one layout pass while the scroll offset stays put, so
everything below is yanked up by the module's full expanded height.

THE FIX IS DELIBERATELY ASYMMETRIC, and that is the part a future reader will want to
"complete". The scroll fires from the BOTTOM "^" only:

* the **header** chevron never needs it — collapsing removes height BELOW the header,
  so the header does not move, and the user tapping it is looking straight at it.
  Scrolling there would CREATE this bug in mirror image (tap a header at y≈600 while
  reading the summary and the page yanks down 600pt);
* the **bottom "^"** is the opposite case by construction — reaching it means the
  header is far above the fold, so the collapse pulls the content the reader was
  looking at up and out of view.

TWO MECHANISMS ARE BANNED HERE, both because they have already frozen this app's main
thread, and neither ban is enforced anywhere else — they live only in prose comments:

1. `.scrollPosition(id:)` writes its binding back DURING layout, so an in-place card
   resize rewrites state mid-layout → re-invalidates layout → never terminates
   (`DailyScannersSection.swift` header, `ScannerCard.swift`, `HomeDashboardView.swift`).
2. A lazy container on this path (`HomeDashboardView` measured 100% main-thread CPU),
   which would ALSO break the anchor: a culled module cannot be reached by `scrollTo`.

Comments are stripped before every assertion — the comments added by this change name
`.scrollPosition`, `ScrollViewReader` and `if isExpanded` verbatim, so an un-stripped
scan would pass on the explanation after the code was reverted
(`.claude/rules/testing.md` §3).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_REPORT = _IOS / "Views/Screens/TickerReportView.swift"
_SECTION = _IOS / "Views/Organisms/ReportDeepDiveSection.swift"
_VIEWMODEL = _IOS / "ViewModels/TickerReportViewModel.swift"
_VIEWS = _IOS / "Views"

_LAZY = ("LazyVStack", "LazyHStack", "LazyVGrid", "LazyHGrid")


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    # `[ \t]*`, not `\s*`: `\s` eats the preceding newline and collapses two lines
    # into one, which silently shifts every line number this file could report.
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return _strip_comments(path.read_text())


def _decl_block(src: str, prefix: str) -> str:
    """Brace-matched body of the declaration starting at ``prefix``."""
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this scan has drifted"
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail(f"unbalanced braces after {prefix!r}")


# ── 1. the reader encloses the scroll view ────────────────────────────────────

def test_the_reader_encloses_the_scroll_view():
    """Order, not membership. A `ScrollViewReader` nested INSIDE a `ScrollView`
    contains no scroll view for the proxy to scan, so `scrollTo` compiles, runs and
    silently does nothing. Every reader in this app is on the outside."""
    block = _decl_block(_code(_REPORT), "private func reportContent(")

    assert "ScrollView(showsIndicators: false)" in block, "the scan has drifted"
    assert "ScrollViewReader {" in block, "the report screen lost its scroll reader"
    assert block.index("ScrollViewReader {") < block.index("ScrollView(showsIndicators: false)"), (
        "the ScrollViewReader is nested inside the ScrollView — scrollTo is a silent no-op there"
    )
    for lazy in _LAZY:
        assert lazy not in block, (
            f"{lazy} on the report scroll path: it hangs the main thread AND culls the "
            "modules the scroll anchor needs to reach"
        )


# ── 2. every module is addressable, and stably so ─────────────────────────────

def test_every_module_carries_a_scroll_anchor():
    block = _decl_block(_code(_REPORT), "private func deepDiveModulesSection")
    assert "ReportDeepDiveSection(" in block, "the scan has drifted"
    assert ".id(module.id)" in block, "the modules are no longer addressable by scrollTo"
    assert "onCollapse:" in block, "the section's collapse is no longer wired to the screen"


def test_the_module_ids_are_minted_once():
    """The precondition for the anchor. A computed property would hand out fresh UUIDs
    on every render, and a changing `.id()` resets the child's `@State isExpanded` —
    so every module would silently re-collapse, which reads as an unrelated bug."""
    src = _code(_VIEWMODEL)
    assert "let deepDiveModules: [DeepDiveModule] = [" in src, (
        "deepDiveModules is no longer a stored `let`; if it became computed, every "
        ".id(module.id) changes per render and the cards re-collapse on their own"
    )


# ── 3. the scroll itself ──────────────────────────────────────────────────────

def test_the_collapse_scroll_is_unanimated_and_retried():
    block = _decl_block(_code(_REPORT), "private func scrollModuleHeaderToTop")
    assert "transaction.animation = nil" in block
    assert "transaction.disablesAnimations = true" in block, (
        "withTransaction REPLACES the ambient transaction; without this the scroll "
        "inherits any animation a future collapse is wrapped in"
    )
    assert "anchor: .top" in block
    assert "DispatchQueue.main.async" in block, (
        "the retry resolves against settled layout — the content just shrank, so the "
        "first call can be clamped near the end of the report"
    )
    assert "withAnimation" not in block, (
        "an animated scrollTo runs over a contentSize that is still changing — the same "
        "family as the two freezes this app has shipped"
    )


# ── 4. the asymmetry is the design ────────────────────────────────────────────

def test_only_the_bottom_affordance_scrolls():
    """The header chevron must NOT scroll. Collapsing does not move the header, so a
    scroll there introduces the jump this change removes."""
    block = _decl_block(_code(_SECTION), "struct ReportDeepDiveSection")
    assert "var onCollapse: (() -> Void)? = nil" in block
    assert block.count("onCollapse?()") == 1, (
        "onCollapse fires from more than one place. The header path must stay silent: "
        "collapsing removes height BELOW the header, so the header never moves, and "
        "scrolling there yanks the page down by however far the reader had scrolled."
    )
    # It fires from the bottom "^", which lives inside the expanded branch.
    assert block.index("if isExpanded") < block.index("onCollapse?()"), (
        "onCollapse moved out of the expanded branch — it is now on the header path"
    )
    assert "@State private var isExpanded" in block, (
        "the state was lifted. That is a design change, not a refactor: it re-renders "
        "the whole report on every toggle, and it deletes the literal tokens "
        "test_ios_detail_layout_guards asserts on the sibling cards."
    )


def test_the_wiring_did_not_break_the_tap_target_scan():
    """`test_ios_tap_target_guards` slices this file at the FIRST `if isExpanded`.
    Writing the header action as `if isExpanded == false` would move that token above
    the header's modifier chain and fail that guard with a message about a hit-target
    regression that did not happen."""
    block = _decl_block(_code(_SECTION), "struct ReportDeepDiveSection")
    assert block.index(".contentShape(Rectangle())") < block.index("if isExpanded")


# ── 5. the banned API, enforced for the first time ────────────────────────────

def test_scroll_position_is_not_used_anywhere():
    """`.scrollPosition(id:)` is the documented API for exactly this feature and it
    froze the main thread here. Until now the ban existed only in prose."""
    offenders = []
    for path in sorted(_VIEWS.rglob("*.swift")):
        if ".scrollPosition(" in _strip_comments(path.read_text(errors="ignore")):
            offenders.append(str(path.relative_to(_IOS)))
    assert offenders == [], (
        ".scrollPosition( writes its binding back during layout; an in-place card resize "
        "then rewrites state mid-layout and never terminates:\n  " + "\n  ".join(offenders)
    )


# ── anti-vacuity ──────────────────────────────────────────────────────────────

def test_the_collapse_scans_are_not_vacuous():
    raw = _REPORT.read_text()

    # 1. Stripping bites on the exact tokens these tests assert. The comments added by
    #    this change name `.scrollPosition` while explaining why it is absent, so the
    #    ban in test_scroll_position_is_not_used_anywhere would pass on prose alone if
    #    the stripper ever stopped working.
    assert "scrollPosition" in raw, (
        "the comment explaining the .scrollPosition ban is gone — the ban test's "
        "stripping is no longer exercised by real prose"
    )
    assert "scrollPosition" not in _code(_REPORT), (
        "a live .scrollPosition( appeared, or the stripper stopped stripping"
    )
    assert "ScrollViewReader" not in _strip_comments("// ScrollViewReader {\nlet x = 1\n")

    # 2. Comment stripping preserves line count.
    sample = "a\n\n    // note\nb\n"
    assert _strip_comments(sample).count("\n") == sample.count("\n")

    # 3. Brace bounding is bounded at BOTH ends.
    block = _decl_block(_code(_REPORT), "private func deepDiveModulesSection")
    assert len(block) < len(_code(_REPORT))
    assert "private func deepDiveContent" not in block, "the block ran past its closing brace"

    # 4. The ordering check discriminates — a reader inside must fail it.
    good = "ScrollViewReader { proxy in\n ScrollView(showsIndicators: false) { }\n}"
    bad = "ScrollView(showsIndicators: false) {\n ScrollViewReader { proxy in }\n}"
    assert good.index("ScrollViewReader {") < good.index("ScrollView(showsIndicators: false)")
    assert not (bad.index("ScrollViewReader {") < bad.index("ScrollView(showsIndicators: false)"))

    # 5. The files are real.
    for path in (_REPORT, _SECTION, _VIEWMODEL):
        assert len(_code(path)) > 400, f"{path} scan collapsed"
