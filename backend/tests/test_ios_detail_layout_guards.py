"""Source-scan guards for the scroll layout of the five asset-detail screens.

TestFlight, build 1.0 (3), iPhone 17 Pro / iOS 18.7.8, on `^IXIC` → Overview:
*"I cant scroll this screen to the bottom. It's like shaking."*

Two independent defects produced that one sentence.

**1. A lazy scroll container with a pinned section header.** All five screens had::

    ScrollView(showsIndicators: false) {
        LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) { … }

which is the Home-feed hang (`test_ios_home_layout_guards.py`) plus an aggravator. A lazy stack
caches each subview's measured size and derives every offset by walking its predecessors; a child
that resizes mid-placement invalidates that cache and restarts the walk. `pinnedViews` forces that
walk EVERY FRAME, because the pinned header's offset has to be recomputed as you scroll.

What made it fire with no user interaction — the reporter's screenshot shows every card COLLAPSED —
is that the detail view models sink `livePriceManager.$livePrice` into `indexData.price`, which
flows through `headerData` into the container's FIRST child. **Every websocket tick resizes the
subview the pinned offset is measured against.** Scroll + tick = re-measure mid-scroll.

**2. A back-swipe gesture competing with the scroll.** Seven screens had a bare
`.gesture(DragGesture().onEnded { if value.translation.width > 100 { … } })` on the whole screen,
outside the `ScrollView` and alongside `.refreshable`: default `minimumDistance` 10, no axis
filter, no origin filter, and `.gesture` rather than `.simultaneousGesture`, so it arbitrated with
the scroll pan on every flick.

⚠️ **Comments are stripped before every assertion, and here that is not a formality.** The fix's
own comments in `DetailScrollContainer.swift` and in all five screens explain the bug by NAMING
`LazyVStack` and `pinnedViews` verbatim. An un-stripped absence scan would pass on that prose after
someone reverted the code — the exact vacuity this repo has been bitten by before
(`.claude/rules/testing.md` §3, `project_source_scan_guard_vacuity`). Every scan is also
brace-bounded to the declaration it means to check, and the `…_not_vacuous` tests below prove both
helpers still bite.
"""

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_SCREENS = _IOS / "Views/Screens"
_ORGANISMS = _IOS / "Views/Organisms"
_MODIFIERS = _IOS / "Views/Modifiers"

_CONTAINER = _ORGANISMS / "DetailScrollContainer.swift"
_BACKSWIPE = _MODIFIERS / "BackSwipe.swift"

# The five asset-detail screens, and the tab bar each one pins.
_DETAIL_SCREENS = [
    ("IndexDetailView.swift", "IndexDetailTabBar"),
    ("TickerDetailView.swift", "TickerDetailTabBar"),
    ("ETFDetailView.swift", "ETFDetailTabBar"),
    ("CryptoDetailView.swift", "CryptoDetailTabBar"),
    ("CommodityDetailView.swift", "CommodityDetailTabBar"),
]
_IDS = [s.replace("DetailView.swift", "") for s, _ in _DETAIL_SCREENS]

# Every screen that hand-rolled the back-swipe. The last two are not asset-detail screens but
# carried a byte-identical block.
_BACKSWIPE_SCREENS = [s for s, _ in _DETAIL_SCREENS] + ["SearchView.swift", "NewsDetailView.swift"]

_OVERVIEW_CONTENTS = [
    "IndexDetailOverviewContent.swift",
    "TickerDetailOverviewContent.swift",
    "ETFDetailOverviewContent.swift",
    "CryptoDetailOverviewContent.swift",
    "CommodityDetailOverviewContent.swift",
]

_LAZY_CONTAINERS = ("LazyVStack", "LazyHStack", "LazyVGrid", "LazyHGrid")


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails. See the module docstring — load-bearing."""
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


# ── 1. The detail screens scroll eagerly ─────────────────────────────


@pytest.mark.parametrize("screen,tab_bar", _DETAIL_SCREENS, ids=_IDS)
def test_detail_screen_body_has_no_lazy_container(screen: str, tab_bar: str):
    """A lazy stack re-walks its predecessors to place a pinned header; a live-price tick
    resizes its first child. Together that is the reported "shaking"."""
    body = _decl_block(_read(_SCREENS / screen), "var body: some View")

    # Anti-vacuity: prove we captured the real screen body before asserting on absences.
    assert "DetailScrollContainer(" in body, (
        f"{screen}: the scan did not find DetailScrollContainer in `body` — it has drifted, "
        f"and every absence assertion below is now meaningless.")

    for container in _LAZY_CONTAINERS:
        assert container not in body, (
            f"{screen}: `{container}` is back in the screen body. The detail screens scroll "
            f"eagerly on purpose — see DetailScrollContainer's type comment. If a genuinely long "
            f"network-backed list is being added, nest its own lazy stack inside (as the News tab "
            f"does), do not make the outer container lazy again.")

    assert "pinnedViews" not in body, (
        f"{screen}: `pinnedViews` is back. It only exists on lazy stacks, and it is what forced "
        f"the predecessor walk on every frame. The tab bar is pinned by an overlay now.")


@pytest.mark.parametrize("screen,tab_bar", _DETAIL_SCREENS, ids=_IDS)
def test_detail_screen_pins_its_tab_bar_through_the_shared_container(screen: str, tab_bar: str):
    """One container, five screens — so this cannot be fixed on four of them."""
    body = _decl_block(_read(_SCREENS / screen), "var body: some View")
    assert "isTabBarPinned: $isTabBarPinned" in body, (
        f"{screen}: the container is no longer driving `isTabBarPinned`. That state still feeds "
        f"TickerDetailHeader(tickerPrice:), so the nav bar would stop showing the price on scroll.")
    assert f"{tab_bar}(selectedTab: $viewModel.selectedTab)" in body, (
        f"{screen}: expected {tab_bar} to be passed as the container's `tabs`.")


@pytest.mark.parametrize("content", _OVERVIEW_CONTENTS, ids=[c[:-len("DetailOverviewContent.swift")] for c in _OVERVIEW_CONTENTS])
def test_overview_content_is_not_lazy(content: str):
    """Commodity was the only one nesting a lazy stack inside the screen's lazy stack — two
    placement caches to invalidate instead of one, and its sections are all in-memory."""
    body = _decl_block(_read(_ORGANISMS / content), "var body: some View")
    for container in _LAZY_CONTAINERS:
        assert container not in body, (
            f"{content}: `{container}` is back in an Overview tab. None of these sections is "
            f"network-paged and none reaches an AsyncImage, so laziness buys nothing and costs "
            f"a placement cache.")


# ── 2. The container pins by overlay, never by a layout sibling ──────


def test_container_scrolls_eagerly_and_pins_without_changing_layout():
    """`.overlay` contributes no layout. A pinned copy inserted as a VStack sibling above the
    ScrollView would shrink the scroll view's frame by the tab bar's height at the moment of
    pinning, jumping the content by exactly that much."""
    body = _decl_block(_read(_CONTAINER), "var body: some View")

    assert "ScrollView(showsIndicators: false)" in body, \
        "DetailScrollContainer no longer owns the ScrollView — this scan has drifted"
    assert "VStack(spacing: 0)" in body, "the eager stack is gone"
    for container in _LAZY_CONTAINERS:
        assert container not in body, (
            f"`{container}` is back inside DetailScrollContainer. This is the one place the "
            f"whole fix lives; making it lazy again re-breaks all five screens at once.")

    assert ".overlay(alignment: .top)" in body, (
        "the sticky tab bar is no longer an overlay. If it became a layout sibling, the content "
        "jumps by the tab bar's height when it pins.")
    assert "pinnedViews" not in body, "`pinnedViews` is back — it requires a lazy stack"


def test_container_writes_the_pin_state_conditionally():
    """`onPreferenceChange` fires continuously while scrolling. An unconditional write to
    `isTabBarPinned` re-renders the whole screen on every scroll frame."""
    body = _decl_block(_read(_CONTAINER), "var body: some View")
    assert "if shouldPin != isTabBarPinned" in body, (
        "the pin state is being written without comparing it first — that re-renders the screen "
        "on every scroll frame, which is the cost this whole change exists to remove.")


# ── 3. The back-swipe does not compete with the scroll ───────────────


@pytest.mark.parametrize("screen", _BACKSWIPE_SCREENS,
                         ids=[s.replace("View.swift", "") for s in _BACKSWIPE_SCREENS])
def test_no_hand_rolled_back_swipe(screen: str):
    src = _strip_comments(_read(_SCREENS / screen))
    assert "translation.width > 100" not in src, (
        f"{screen}: the hand-rolled back-swipe is back. A bare DragGesture on the screen "
        f"arbitrates with the ScrollView's pan on every flick. Use `.backSwipe {{ … }}`.")
    assert ".backSwipe {" in src, (
        f"{screen}: swipe-to-go-back is gone entirely. These screens set "
        f"`.navigationBarHidden(true)`, which disables the system interactive-pop gesture, so "
        f"removing this leaves no way back except the button.")


def test_back_swipe_is_simultaneous_edge_anchored_and_axis_filtered():
    """Three guards, and each replaces a distinct fault in the seven blocks this modifier
    replaced. The origin filter is the one that is easy to think optional: `.gesture` used to
    give child gestures priority, which is the only reason a right-swipe on the key-stats
    carousel did not also pop the screen. `.simultaneousGesture` gives that up."""
    body = _decl_block(_read(_BACKSWIPE), "func body(content: Content)")

    assert ".simultaneousGesture(" in body, (
        "BackSwipe is using a competing gesture again. `.gesture` and `.highPriorityGesture` "
        "both arbitrate with the ScrollView's pan; only `.simultaneousGesture` lets the scroll "
        "keep it.")
    assert ".gesture(" not in body.replace(".simultaneousGesture(", ""), \
        "a plain `.gesture(` is back alongside the simultaneous one"

    assert "DragGesture(minimumDistance:" in body, \
        "minimumDistance is back to the default 10 — a tap with a wobble arms the swipe"
    assert "startLocation.x" in body, (
        "the origin filter is gone. Under `.simultaneousGesture` a right-swipe on any horizontal "
        "carousel (key stats, chart ranges, AI suggestion chips) now also pops the screen.")
    assert "translation.height" in body, (
        "the axis filter is gone — a diagonal flick down-and-right will pop the screen mid-scroll.")


# ── 3b. Lessons from driving the real screens ────────────────────────


def test_the_pin_is_derived_from_scroll_offset_not_the_tab_bar_geometry():
    """Measured on SPY: reading the tab bar's own `minY` through a GeometryReader silently
    FREEZES once that view leaves the rendered band, so a single fast flick past the threshold
    left the bar unpinned for the whole tab. Only a slow creep past it ever looked right.

    The scroll view's own geometry is never culled, so the pin must come from there.
    """
    body = _decl_block(_read(_CONTAINER), "var body: some View")
    assert ".onScrollGeometryChange(" in body, (
        "the pin is no longer derived from scroll geometry. If it went back to reading the tab "
        "bar's position, it will freeze whenever the user flicks past the threshold in one "
        "gesture — which is the common case, not an edge case.")
    assert "contentOffset" in body, "the scroll offset is no longer being read"
    assert "aboveTabsHeight" in body, (
        "the pin threshold is no longer the measured height of the content above the tabs")


def test_the_header_price_cannot_wrap():
    """An index quote is five digits plus decimals ("$26541.35"). Unconstrained, it wrapped to a
    second line and made the nav header taller than the symbol beside it. Seen on ^IXIC once the
    pin was made reliable — this label was rarely rendered before that."""
    header = _strip_comments(_read(_IOS / "Views/Molecules/TickerDetailHeader.swift"))
    idx = header.find("Text(price)")
    assert idx != -1, "the header price label is gone — this scan has drifted"
    window = header[idx : idx + 400]
    assert "lineLimit(1)" in window, (
        "the header price can wrap again. On an index it becomes two lines and the header grows.")
    assert "minimumScaleFactor(" in window, (
        "the price should shrink to fit, not truncate — a clipped price is a WRONG number.")


# ── 4. The precondition that makes rule 1 load-bearing ───────────────


def test_the_snapshot_cards_still_expand_in_place():
    """Rule 1 exists because these resize inside the scroll container. If they ever stop
    expanding in place, revisit whether the eager container is still required — do not just
    delete this test.

    Note the expands are still ANIMATED, deliberately. Home proved an animated in-place expand
    is fine once the container is eager (`ScannerCard` still animates), and changing both at
    once would have made the improvement unattributable.
    """
    src = _strip_comments(_read(_ORGANISMS / "IndexDetailSnapshotsSection.swift"))
    assert "if isExpanded {" in src, (
        "the snapshot cards no longer expand in place — this scan has drifted, and the "
        "justification for the eager container needs re-checking.")
    assert "isExpanded.toggle()" in src


# ── 5. Anti-vacuity ──────────────────────────────────────────────────


def test_the_layout_scans_are_not_vacuous():
    """Both helpers must still bite, and the comment stripping in particular: the fix's own
    prose names `LazyVStack` and `pinnedViews` in every file this module scans."""
    raw = _read(_CONTAINER)
    stripped = _strip_comments(raw)

    # Stripping demonstrably works, on the exact token the absence scans look for.
    assert "LazyVStack" in raw, (
        "DetailScrollContainer no longer explains what it replaced — if that comment was "
        "deleted, this control is no longer proving anything. Restore it or re-anchor this test.")
    assert "LazyVStack" not in stripped, (
        "comment stripping has stopped working. Every absence assertion in this module would "
        "now pass on prose alone.")

    # The same trap exists in each screen: they all name LazyVStack in a comment.
    for screen, _ in _DETAIL_SCREENS:
        screen_raw = _read(_SCREENS / screen)
        assert "LazyVStack" in screen_raw, f"{screen}: lost the explanatory comment"
        assert "LazyVStack" not in _strip_comments(screen_raw), f"{screen}: stripping failed"

    # Brace bounding is bounded on both ends: the body block is smaller than the file and does
    # not swallow the type's other members.
    body = _decl_block(raw, "var body: some View")
    assert len(body) < len(stripped), "the body block is the whole file — bounding failed"
    assert "private var tabBarChrome" not in body, (
        "the `body` block ran past its closing brace into the next declaration")
    assert "tabBarChrome" in body, "the body no longer renders the tab bar chrome"

    # And the files are real views, not stubs.
    for path in (_CONTAINER, _BACKSWIPE):
        assert len(_strip_comments(_read(path))) > 400, f"{path.name} is too small to be real"
