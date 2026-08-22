"""Guards for the Tracking tab's instant first paint.

WHY THIS FILE EXISTS. Opening Tracking for the first time in a session showed a full-screen dim +
spinner for ~0.5-1s. Captured on device before the fix, the screen simultaneously read
**"No tickers yet - Add a ticker to start tracking prices"** to a user holding four tickers: the
Assets list branched `filteredAssets.isEmpty ? placeholder : list`, and during the load "empty"
and "not loaded yet" were the same branch.

Two causes, both pinned below:

1. `LoadingOverlay` is `ZStack { Color.black.opacity(0.3).ignoresSafeArea(); ProgressView() }`
   with no `.allowsHitTesting(false)` - it dimmed the tab AND ate every tap for the whole load.
   The same trap was removed from the five asset-detail screens
   (`project_ticker_detail_instant_load`); Tracking kept it.
2. `performLoad()` gated `isLoading` on FOUR parallel calls plus a sequential insights hop, and
   two of those calls are whale data that `AssetsTabContent` never renders. Measured warm against
   production: feed 0.21s, portfolios 0.42s, whales 0.13s + 0.10s, then insights on top.

Deliberately NOT fixed by prefetching at launch: `project_launch_fanout_and_analytics` §4
established "clear eagerly, fetch lazily" and names Tracking as the five-request tab, and
`test_ios_launch_cost_guards.py` pins it. This change adds no launch requests.

Per `.claude/rules/testing.md` §3 and `project_source_scan_guard_vacuity`, every scan here is
comment-stripped, brace-bounded, and was mutation-tested by hand.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"

_TRACKING_VIEW = _IOS / "Views/Screens/TrackingView.swift"
_TRACKING_VM = _IOS / "ViewModels/TrackingViewModel.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails.

    Load-bearing twice over here: the replacement comments in both files spell out
    `LoadingOverlay`, `loadWhaleData` and `isLoading = false` while explaining the fix. Without
    stripping, the absence scans would fail on the explanation and the ORDER scan below would
    index into prose instead of code.
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


# ── 1. No blocking overlay on the LIVE screen ──────────────────────────────────


def test_the_live_tracking_screen_has_no_loading_overlay():
    """⚠️ `TrackingContentViewWithBinding` is the LIVE screen (ContentView.swift instantiates it);
    `TrackingContentView` is used only by the `#Preview`. Asserting against the whole FILE, or
    against the wrong struct, is how a fix to a preview-only duplicate once looked like a fix to
    the shipping one — so this is brace-bounded to the live declaration by name.
    """
    block = _decl_block(_read(_TRACKING_VIEW), "struct TrackingContentViewWithBinding: View")

    # Anti-vacuity: prove this really is the live Tracking screen.
    assert "AssetsTabContent(" in block, "scan drifted — this struct no longer builds the Assets tab"
    assert "isActiveTab" in block, "scan drifted — this struct no longer has the activation task"

    assert "LoadingOverlay" not in block, (
        "the live Tracking screen shows LoadingOverlay again. It has no "
        "`.allowsHitTesting(false)`, so it dims the tab and swallows every tap for the whole "
        "first load. Render the layout immediately and use TrackedAssetsSkeleton instead."
    )


def test_loading_is_not_mistaken_for_an_empty_portfolio():
    """The captured symptom: "No tickers yet" shown to a user with four holdings."""
    block = _decl_block(_read(_TRACKING_VIEW), "struct AssetsTabContent: View")
    assert "TrackedAssetsSkeleton()" in block, (
        "the Assets tab no longer distinguishes loading from empty, so a user WITH holdings is "
        "told 'No tickers yet — Add a ticker' for the whole first load"
    )
    # Anti-vacuity: the genuinely-empty path must still exist.
    assert "AssetsPlaceholderCard(" in block, "the empty/error placeholder is gone entirely"


# ── 2. The gate covers only what the Assets tab draws ──────────────────────────


def test_the_loading_gate_closes_before_the_whale_calls():
    """An ORDER assertion, not a presence check.

    `project_launch_fanout_and_analytics` §4 is explicit that presence alone is not enough — a
    statement placed after the point it was meant to precede still satisfies `in`. Both tokens
    exist either way; what matters is that the gate closes FIRST.
    """
    block = _decl_block(_read(_TRACKING_VM), "private func performLoad()")

    # Drop the `defer { isLoading = false }` safety net before indexing. It is the FIRST
    # occurrence of that assignment and sits above everything, so searching the raw block finds
    # it and the ordering assertion can never fail — this guard was vacuous until the defer was
    # excluded, proven by deleting the real gate-close and watching it still pass.
    body = "\n".join(l for l in block.splitlines() if "defer" not in l)

    close = body.find("isLoading = false")
    whales = body.find("loadWhaleData()")
    insights = body.find("loadPortfolioInsights()")

    assert close != -1, "performLoad no longer closes the loading gate explicitly"
    assert whales != -1, "scan drifted — performLoad no longer loads whale data"
    assert insights != -1, "scan drifted — performLoad no longer loads portfolio insights"

    assert close < whales, (
        "performLoad holds `isLoading` across the whale calls again. AssetsTabContent renders no "
        "whale state, so this makes the visible tab wait on two responses it never draws."
    )
    assert close < insights, (
        "performLoad holds `isLoading` across the portfolio-insights call again — a SEQUENTIAL "
        "round trip on the end of the gate, which is where most of the extra second came from."
    )


def test_whales_have_their_own_loading_flag():
    """Once whale data left the main gate, the Whales sub-tab had no loading state at all — every
    section there is `if !isEmpty`, so it rendered as a blank page indistinguishable from "you
    follow nobody and no whale has traded"."""
    vm = _strip_comments(_read(_TRACKING_VM))
    assert "isLoadingWhales" in vm, "the whales-specific loading flag is gone"

    view = _decl_block(_read(_TRACKING_VIEW), "struct WhalesTabContent: View")
    assert "isLoadingWhales" in view, (
        "the Whales tab no longer reads isLoadingWhales, so an in-flight load renders as an "
        "empty roster"
    )


# ── 3. No launch cost was added ────────────────────────────────────────────────


def test_tracking_is_still_loaded_lazily_on_tab_activation():
    """The rejected alternative was prefetching Tracking at launch. That reverses "clear eagerly,
    fetch lazily" and taxes every launch for users who never open the tab."""
    block = _decl_block(_read(_TRACKING_VIEW), "struct TrackingContentViewWithBinding: View")
    assert ".task(id: isActiveTab)" in block, (
        "Tracking no longer loads on tab activation. If this became a launch-time prefetch, "
        "revisit tests/test_ios_launch_cost_guards.py — it exists to stop exactly that."
    )
    assert "loadIfNeeded()" in block, "the first-visit load is no longer gated by loadIfNeeded"


# ── 4. The roster chip is a WORD, not the server's sentence ────────────────────
#
# `activity_label` is prose: "No trades disclosed since Nov 2025", and for a curated filer a
# 172-character paragraph. Rendered in a `TintedTagBadge` capsule beside the follower count it
# first wrapped the capsule into a CIRCLE with the text clipped inside, and then — once the
# badge was clamped to one line — truncated to "No trades disclose…", dropping the date, which
# is the only informative half. The card only needs to say THAT something is off; the profile
# says what, verbatim, in `WhaleActivityNotice` via `lifecycle_note`.

_TRACKING_MODELS = _IOS / "Models/TrackingModels.swift"
_BADGE = _IOS / "Views/Atoms/TintedTagBadge.swift"


def test_the_roster_chip_uses_the_short_status_word():
    block = _decl_block(_read(_TRACKING_VIEW), "struct WhaleCard: View")

    assert "whale.activityChipLabel" in block, (
        "the roster card renders the server's activity sentence again. It does not fit a "
        "capsule: it truncated to 'No trades disclose…' and, unclamped, drew as a circle."
    )
    assert "text: whale.activityLabel" not in block, (
        "the roster card is passing the full activity sentence to TintedTagBadge again"
    )
    # Anti-vacuity: the chip must still be gated on there being something to disclose.
    assert "hasActivityNotice" in block, "scan drifted — the card no longer gates the chip"


def test_the_short_chip_label_never_returns_the_server_sentence():
    block = _decl_block(_read(_TRACKING_MODELS), "var activityChipLabel: String")
    assert "activityLabel" not in block, (
        "activityChipLabel falls back to the server sentence, which is the thing it exists "
        "to keep off the card"
    )
    # Every status the server can emit must map to a word, including the unknown case —
    # otherwise a status this build has not seen silently drops the disclosure.
    for status in ("dormant", "inactive", "late", "quiet", "none", "default"):
        assert status in block, f"activityChipLabel has no branch for {status!r}"


def test_a_badge_cannot_wrap_into_a_circle():
    """The atom-level invariant, independent of what any server sends.

    `TintedTagBadge` clips to `Capsule()`. With no line limit a long string wraps into a
    near-square block, and a capsule on a square IS a circle — captured on device.
    """
    src = _strip_comments(_read(_BADGE))
    assert "lineLimit" in src, (
        "TintedTagBadge no longer limits its line count, so any long string can wrap it into "
        "a circle with the text clipped inside"
    )
    assert "Capsule()" in src, "scan drifted — TintedTagBadge is no longer capsule-shaped"
