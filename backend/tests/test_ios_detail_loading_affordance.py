"""Source-scan guards: the asset-detail skeleton must SAY it is loading.

TestFlight, build 1.0 (3), AVGO — *"This screen was taking 5-7 seconds to load. I think
it'd be nicer if there is something on the screen saying 'loading/we're loading the
data/or something' while waiting."* The attached screenshot showed nothing but shimmer
bars, and `DetailHeaderChartSkeleton` genuinely carried **no text and no accessibility
modifier of any kind** — so to VoiceOver the screen said literally nothing.

This is the second report of the same shape (see `test_ios_detail_fast_core_guards.py`,
build 1.0 (6): *"It's very slow at first time open it."*), which is why the affordance is
pinned rather than left to survive on good intentions.

Four things rot silently, so each is asserted:

1. The caption exists at all — a `ProgressView` plus a `Text` driven by `message`.
2. The caption sits OUTSIDE the shimmered group. `ShimmerModifier` ends with `.clipped()`
   and sweeps an opacity gradient, so text inside it pulses and clips. A later tidy-up
   that hoists `.shimmer()` back onto `body` would silently break the caption.
3. The escalation is real and is 2.5s, and a CANCELLED sleep must not flip `isSlow`. A
   bare `try? await Task.sleep(...)` swallows the CancellationError and then CONTINUES to
   the next line — so the naive spelling sets the "taking longer than usual" state on a
   view that already went away.
4. All five screens pass their symbol, so the caption names the asset instead of the
   generic fallback.

⚠️ Comments are stripped before every assertion, and each scan is brace-bounded to the
declaration it means to check. The comments in the file under test spell out `symbol`,
`ProgressView`, `shimmer`, `accessibilityLabel` and `2.5` verbatim, so an un-stripped scan
would pass on prose after the code was reverted — the exact vacuity documented in
`.claude/rules/testing.md` §3. `test_the_scanners_are_not_vacuous` proves the helpers bite.
"""

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_MOLECULES = _IOS / "Views/Molecules"
_SCREENS = _IOS / "Views/Screens"

_HEADER_SKELETON = _MOLECULES / "DetailHeaderChartSkeleton.swift"
_TAB_SKELETON = _MOLECULES / "DetailTabSkeleton.swift"

# (screen file, the symbol property that screen must pass)
_SCREENS_UNDER_GUARD = [
    ("TickerDetailView.swift", "tickerSymbol"),
    ("IndexDetailView.swift", "indexSymbol"),
    ("CryptoDetailView.swift", "cryptoSymbol"),
    ("CommodityDetailView.swift", "commoditySymbol"),
    ("ETFDetailView.swift", "etfSymbol"),
]


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails. See the module docstring."""
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


def _skeleton() -> str:
    return _decl_block(
        _HEADER_SKELETON.read_text(), "struct DetailHeaderChartSkeleton: View"
    )


# ── 1. The caption exists ─────────────────────────────────────────────


def test_the_header_skeleton_renders_a_visible_loading_caption():
    block = _skeleton()
    assert "ProgressView()" in block, (
        "DetailHeaderChartSkeleton lost its spinner — the screen is back to shimmer bars "
        "with no indication anything is happening (TestFlight 1.0(3), AVGO)."
    )
    assert "Text(message)" in block, "the caption text is gone"
    assert "var message: String" in block, "the shared caption/VoiceOver string is gone"


def test_the_caption_names_the_asset_and_degrades_when_it_cannot():
    block = _skeleton()
    assert "var symbol: String?" in block, (
        "the `symbol` parameter is gone, so every screen falls back to a generic caption"
    )
    assert '"Loading \\(symbol)…"' in block, "the caption no longer names the asset"
    assert '"Loading…"' in block, (
        "the nil/blank-symbol fallback is gone — a screen without a resolved symbol would "
        'render "Loading …"'
    )


# ── 2. The caption must not be inside the shimmer ─────────────────────


def test_the_caption_is_not_inside_the_shimmered_group():
    src = _HEADER_SKELETON.read_text()
    body = _decl_block(src, "var body: some View")
    placeholders = _decl_block(src, "private var placeholders: some View")

    assert ".shimmer()" in placeholders, (
        "the placeholder bars are no longer shimmered — they read as broken layout, not "
        "as loading"
    )
    assert ".shimmer()" not in body, (
        "`.shimmer()` was hoisted back onto `body`, which puts the caption inside it. "
        "ShimmerModifier sweeps an opacity gradient and ends with `.clipped()`, so the "
        "text would pulse and clip."
    )


# ── 3. The escalation, and the cancellation trap ──────────────────────


def test_the_caption_escalates_after_two_and_a_half_seconds():
    block = _skeleton()
    assert "slowThresholdSeconds: Double = 2.5" in block, (
        "the 2.5s escalation threshold changed or was removed"
    )
    assert "Task.sleep(for: .seconds(slowThresholdSeconds))" in block, (
        "the escalation timer is gone — a 5-7s load reads as stuck again"
    )
    assert "isSlow = true" in block, "nothing flips the escalated state"


def test_a_cancelled_escalation_timer_cannot_set_the_slow_state():
    """`try? await Task.sleep(...)` swallows the CancellationError and then FALLS THROUGH
    to the next statement, so the naive spelling flips `isSlow` on a view that a fast load
    already replaced. The timer must return on cancellation instead."""
    body = _decl_block(_HEADER_SKELETON.read_text(), "var body: some View")
    assert "try? await Task.sleep" not in body, (
        "a bare `try?` around Task.sleep does NOT stop execution when the task is "
        "cancelled — the next line still runs. Use do/catch + return."
    )
    assert "catch" in body and "return" in body, (
        "the escalation timer no longer bails out on cancellation"
    )


# ── 4. Every screen passes its symbol ─────────────────────────────────


@pytest.mark.parametrize("screen,symbol", _SCREENS_UNDER_GUARD)
def test_every_detail_screen_names_its_asset_in_the_skeleton(screen, symbol):
    src = _strip_comments((_SCREENS / screen).read_text())
    assert f"DetailHeaderChartSkeleton(symbol: {symbol})" in src, (
        f"{screen} does not pass `{symbol}` to DetailHeaderChartSkeleton, so its loading "
        f"caption falls back to the generic 'Loading…'"
    )
    assert "DetailHeaderChartSkeleton()" not in src, (
        f"{screen} still has a no-argument call site"
    )


# ── 5. The tab skeleton's VoiceOver label actually applies ────────────


def test_the_tab_skeleton_label_is_attached_to_a_single_element():
    """`.accessibilityLabel` on a container whose children stay reachable does not stop
    VoiceOver walking the individual shimmer bars. TrackedAssetsSkeleton pairs the two."""
    block = _decl_block(_TAB_SKELETON.read_text(), "struct DetailTabSkeleton: View")
    assert ".accessibilityLabel(" in block, "DetailTabSkeleton lost its VoiceOver label"
    assert ".accessibilityElement(children: .ignore)" in block, (
        "the label is attached to a container whose children are still individually "
        "reachable, so VoiceOver reads the shimmer bars instead of 'Loading'"
    )


def test_the_header_skeleton_is_one_accessibility_element():
    block = _skeleton()
    assert ".accessibilityElement(children: .ignore)" in block
    assert ".accessibilityLabel(message)" in block, (
        "the VoiceOver label must reuse `message` so it can never drift from the visible "
        "caption"
    )


# ── 6. Anti-vacuity ───────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    """The two helpers must (a) strip comments and (b) stay inside the declaration —
    otherwise every assertion above passes on prose or on a neighbouring type."""
    fake = (
        "struct Decoy: View {\n"
        "    var body: some View { Text(\"x\").shimmer() }\n"
        "}\n"
        "\n"
        "struct DetailHeaderChartSkeleton: View {\n"
        "    // ProgressView() var symbol: String? .shimmer() 2.5\n"
        "    var placeholder: some View { EmptyView() }\n"
        "}\n"
    )
    block = _decl_block(fake, "struct DetailHeaderChartSkeleton: View")

    assert "ProgressView()" not in block, "comments are not being stripped"
    assert "var symbol: String?" not in block, "comments are not being stripped"
    assert "2.5" not in block, "comments are not being stripped"
    assert "Text(" not in block, (
        "the scan leaked into the neighbouring `Decoy` declaration — a token in a "
        "different type would satisfy the assertions above"
    )
