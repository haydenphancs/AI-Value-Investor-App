"""Guards against the Home-feed layout hang.

WHY THIS FILE EXISTS. Expanding a Daily Scanners card and then scrolling Home pinned the main
thread at 100% inside ONE non-terminating `GraphHost.runTransaction`:

    _UIHostingView.beginTransaction
      → GraphHost.flushTransactions → GraphHost.runTransaction
        → LazySubviewPlacements.placeSubviews
          → LazyHVStack<>.lengthAndSpacing(subviews:predecessors:minorGeometry:)
            → _ViewList_Node.applyNodes ... (recursion depth grew 196 → 590 between samples)

It never recovered — the app had to be force-quit. Measured with `sample <pid>`: 100.0% of
main-thread samples busy before the fix, 2.9% after, with `LazySubviewPlacements.placeSubviews`
going from 770/578 samples to zero.

A lazy stack caches each subview's measured size and derives every offset by walking its
predecessors, so a child that RESIZES IN PLACE invalidates that cache mid-placement and
restarts the walk. Home has exactly such a child: a `ScannerCard` expands inline, animating the
horizontal carousel's height. Users reached the hang through the App-Exclusive Signals lock,
because `onLockedTap` collapses the expanded scanner card with `withAnimation` — the same
in-place resize — so it read as "tapping the lock freezes the app".

Nothing about this is visible in a build or a unit test: both layouts compile, and both render
identically until the resize happens. Per `.claude/rules/testing.md` §3 and the
`project_source_scan_guard_vacuity` memory, every scan below is brace-bounded, comment-stripped,
and was mutation-tested by hand.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"

_HOME = _IOS / "Views/Screens/HomeDashboardView.swift"
_SCANNERS = _IOS / "Views/Organisms/DailyScannersSection.swift"
_SCANNER_CARD = _IOS / "Views/Molecules/ScannerCard.swift"
_THEME_TILE = _IOS / "Views/Molecules/TrendingThemeTile.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails.

    Load-bearing here: the fix's own comment names `LazyVStack`, `LazySubviewPlacements` and
    `LazyHVStack` while explaining why they are gone. An un-stripped scan for "LazyVStack"
    would fail on the explanation, and an un-stripped scan for its ABSENCE would pass on a
    revert whose comment survived.
    """
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of a declaration, comments stripped."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_brace : i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


# ── 1. The fix: Home's feed must not be laid out lazily ────────────────────────


def test_home_scroll_content_is_not_a_lazy_stack():
    block = _decl_block(_read(_HOME), "private var content: some View")

    # Anti-vacuity: prove this really is the Home feed and not some other block. Without
    # these, a renamed `content` would make the assertions below pass on an empty string.
    assert "DailyScannersSection(" in block, "scan drifted — Home feed no longer builds Daily Scanners"
    assert "ExclusiveSignalsSection(" in block, "scan drifted — Home feed no longer builds the signals card"

    for lazy in ("LazyVStack", "LazyHStack", "LazyVGrid", "LazyHGrid"):
        assert lazy not in block, (
            f"Home's scroll content is using {lazy} again. It contains a section that resizes "
            "IN PLACE (an expanded ScannerCard animates the carousel's height), and a lazy "
            "stack re-walks its predecessor chain when a cached subview size is invalidated "
            "mid-placement — measured as a permanent 100%-CPU main-thread hang that required "
            "a force-quit. Home has at most eight fixed sections built from in-memory data, "
            "so laziness buys nothing here. Use a plain VStack."
        )


# ── 2. The precondition that makes rule 1 necessary ────────────────────────────


def test_scanner_card_still_expands_in_place():
    """If this ever stops being true, revisit rule 1 rather than assuming it still applies.

    The hang needs a subview whose height changes while its parent is placing it. That is the
    inline leaderboard: `ScannerCard` reveals it inside its own body, animated, instead of
    pushing a sheet or a detail screen.
    """
    block = _decl_block(_read(_SCANNER_CARD), "var body: some View")
    assert "if isExpanded {" in block, (
        "ScannerCard no longer expands in place. That was the resize driving the Home hang — "
        "re-check whether the VStack rule in this file is still the right fix."
    )
    assert "value: isExpanded" in block, "ScannerCard's expansion is no longer animated"


# ── 3. The sibling deadlock in the same family, already fixed once ─────────────


def test_daily_scanners_does_not_write_layout_state_during_layout():
    """`.scrollPosition(id:)` writes its binding back DURING layout to keep the focused item in
    place when content size changes — which is precisely what an expanding card does every
    frame. That rewrite re-invalidates layout and froze the main thread; the carousel derives
    its active page read-only from a PreferenceKey instead. Same failure family as rule 1.
    """
    src = _strip_comments(_read(_SCANNERS))
    assert ".scrollPosition(" not in src, (
        "DailyScannersSection is using .scrollPosition again. Its layout-time state write "
        "deadlocks SwiftUI when a card resizes inside the carousel — see the header comment "
        "in DailyScannersSection.swift. Derive the active page read-only instead."
    )
    # Anti-vacuity: the read-only derivation it was replaced with must still be there.
    assert "onPreferenceChange" in src, "the read-only active-page derivation is gone"


# ── 4. A fill-mode image must not size the container its overlay anchors to ────


def test_theme_tile_change_chip_anchors_to_the_band_not_the_photo():
    """The Emerging Frontiers percentage capsule was rendering with its top sheared off.

    `themeImage` is `contentMode: .fill`, so it reports an OVERSIZED intrinsic size — a 5:4
    hero in a ~192pt-wide tile wants ~152pt for a 116pt band. A ZStack sizes to its largest
    child, so the stack took the PHOTO's height, `.topTrailing` anchored the chip to the
    photo's top rather than the band's, and `.frame(height:)` then centre-cropped the stack.
    The chip lost half the overflow off the top.

    It varied per tile with each photo's aspect ratio, which is what made it read as a text
    clipping problem rather than a layout one. A background and an overlay are sized by their
    host and never drive layout, so both anchor to the band.
    """
    band = _decl_block(_read(_THEME_TILE), "private var imageBand: some View")

    # Anti-vacuity: prove this is the band that sizes the hero.
    assert "imageHeight" in band, "scan drifted — imageBand no longer sizes itself"

    assert "ZStack" not in band, (
        "TrendingThemeTile's image band is a ZStack again. A `contentMode: .fill` image "
        "reports an oversized intrinsic height and a ZStack sizes to its largest child, so "
        "the `.topTrailing` chip anchors to the photo instead of the band and gets "
        "centre-cropped. Keep the hero as `.background` and the chip as `.overlay`."
    )
    assert ".background(themeImage)" in band, "the hero must be a non-sizing background"
    assert ".overlay(alignment: .topTrailing)" in band, (
        "the change chip must be a non-sizing overlay on the sized band"
    )
