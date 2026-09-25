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
    """The scroll callback fires on every scroll frame. An unconditional write to
    `isTabBarPinned` re-renders the whole screen on every scroll frame."""
    decide = _decl_block(_read(_CONTAINER), "private func updatePin()")
    assert "if shouldPin != isTabBarPinned" in decide, (
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


# ── 6. The scroll content is pinned to the viewport width ──────────────
#
# TestFlight, build 1.0 (8), ETH → Overview: *"I don't want it move the whole thing like this."*
# Every child of the container's scroll view — chart, range strip, tab bar, Key Statistics,
# Performance — was translated ~73pt to the right as one unit, with a page-background gutter on
# the left and the right edge cut off; the header above and the AI bar below did not move. That
# is a vertical ScrollView whose content is WIDER than its viewport: the eager VStack takes the
# width of its widest child, centres the normal-width children (+half the overflow) and the
# UIScrollView gets a contentSize.width > bounds.width, so the whole page drags sideways. The
# offender could not be reproduced (iOS 26.6.x, intermittent); the guarantee is structural.
#
# ⚠️ Two look-alikes, both measured with a synthetic 600pt child and both wrong:
#   - `.frame(maxWidth: .infinity)` — a flexible frame reports `clamp(proposal, min ?? child,
#     max ?? child)`, so with no `minWidth` the CHILD's width wins and nothing is clamped (and the
#     scroll view then reports that width upward: the whole screen shifted, header included).
#   - `.containerRelativeFrame(.horizontal)` — pins the content to the container's size, but the
#     container's size follows the content's natural width; the pair never settles. Measured as
#     100% main-thread CPU from the first frame of the screen (`GraphHost.flushTransactions →
#     _FlexFrameLayout.sizeThatFits`), the Home-feed hang's family.
# Only `.frame(minWidth: 0, maxWidth: .infinity)` clamps to the proposal without feedback. The
# container's comments name all three, hence the stripping.

_MOLECULES = _IOS / "Views/Molecules"
_CAROUSEL = _MOLECULES / "KeyStatisticsCarousel.swift"
_KEY_STATS_CARD = _MOLECULES / "KeyStatisticsCard.swift"
_PERFORMANCE = _ORGANISMS / "TickerDetailPerformanceSection.swift"

# Ticker's section is what Index and Commodity render too; ETF and Crypto keep their own.
_KEY_STATS_SECTIONS = [
    "TickerDetailKeyStatsSection.swift",
    "ETFDetailKeyStatsSection.swift",
    "CryptoDetailKeyStatsSection.swift",
]


_CLAMP = ".frame(minWidth: 0, maxWidth: .infinity)"


def test_the_scroll_content_is_container_relative_in_width():
    """Brace-bound on BOTH ends: the frame has to be a modifier on the eager stack, inside the
    ScrollView's closure. A plain `stack < frame < overlay` ordering passed with the frame moved
    onto the ScrollView itself (the ScrollView's closing brace also sits between the two), and
    that placement sizes the scroll VIEW, not its content — the page drags sideways again."""
    body = _decl_block(_read(_CONTAINER), "var body: some View")
    scroll = _decl_block(body, "ScrollView(showsIndicators: false)")   # the trailing closure only
    stack = _decl_block(scroll, "VStack(spacing: 0)")                   # the eager stack's block
    after_stack = scroll[scroll.index(stack) + len(stack):]

    assert _CLAMP not in stack, "the clamping frame is on a CHILD of the stack, not on the stack"
    assert _CLAMP in after_stack, (
        "DetailScrollContainer no longer pins its scroll content to the viewport width: the "
        "clamping frame must be a modifier on the eager stack, inside the ScrollView closure. On "
        "the ScrollView itself it constrains nothing — a child that reports a width above the "
        "viewport centres every sibling and makes the whole page draggable sideways again, on "
        "all five detail screens.")
    assert _CLAMP not in body.replace(scroll, ""), (
        "a second clamping frame outside the ScrollView closure — the one that matters is the "
        "one on the content; a copy on the ScrollView hides a move")
    assert ".frame(maxWidth: .infinity)" not in scroll, (
        "a max-only flexible frame on the scroll content does not clamp an over-wide child; "
        "it reports the child's width")
    assert "containerRelativeFrame" not in body, (
        "`.containerRelativeFrame` is back on the scroll content. It reads the container's size "
        "while the container's size follows the content's natural width — measured as a "
        "100%-CPU layout loop from the first frame of the screen. Use the minWidth: 0 frame.")


def test_the_loading_skeleton_range_strip_cannot_widen_the_content():
    """Seven rigid 34pt capsules are 286pt (+32 padding) of minimum width. The navigation push
    proposes a RAMP of widths (measured 284 → 402pt) to the incoming screen for a few frames,
    so this placeholder was the over-wide child on every push — the clamp above hides it, but
    the DEBUG report would name it on every screen and bury a real offender."""
    skeleton = _read(_IOS / "Views/Molecules/DetailHeaderChartSkeleton.swift")
    stripped = _strip_comments(skeleton)
    row = stripped.find("ForEach(0..<7, id: \\.self)")
    assert row != -1, "the skeleton's seven-capsule range strip is gone — this scan has drifted"
    tail = stripped[row:row + 500]
    clamp = tail.find(".frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)")
    assert clamp != -1, "the skeleton range strip is no longer clamped to its proposal"
    assert ".clipped()" in tail[clamp:], "the clamped strip must clip, or the capsules paint past the frame"


def test_the_over_wide_report_is_debug_only_and_never_lays_out():
    """The natural-width probe exists to NAME an offender while a developer is looking. It must
    stay a diagnostic: a `#if DEBUG` print, off the layout path."""
    raw = _read(_CONTAINER)
    report = _decl_block(raw, "private func reportOverWideContent()")
    assert "#if DEBUG" in report and "#endif" in report, "the over-wide report must be DEBUG-only"
    assert "print(" in report
    body = _decl_block(raw, "var body: some View")
    # Emitter AND consumer, both inside `body` — the PreferenceKey struct alone would satisfy a
    # whole-file scan after the probe was deleted from the scroll content.
    assert "key: ContentNaturalWidthPreferenceKey.self" in body, (
        "the natural-width probe is gone from the scroll content")
    assert ".onPreferenceChange(ContentNaturalWidthPreferenceKey.self)" in body, (
        "nothing reads the natural-width preference any more")
    assert "reportOverWideContent()" in body, "the report is no longer called from the body"
    assert ".frame(width: contentNaturalWidth" not in body and ".frame(width: containerWidth" not in body, (
        "the measured widths must never feed a frame — that would turn a diagnostic into layout")


# ── 7. Key Statistics cards are equal-height and top-aligned ──────────
#
# Same ETH screenshot, second defect: the four-row supply card floated to the vertical middle of
# the five-row price card. Three byte-identical sections (Ticker — also Index and Commodity —,
# ETF, Crypto) each had two nested `HStack(spacing: 0)` with the default `.center` alignment.
# The developer's follow-up widened it: *"i need all cards in here that have the same height.
# check other like tickerdetailview crypto, or etf,... to fix too."* One carousel now serves
# all five screens; the stretch needs BOTH the card's `maxHeight: .infinity` frame and a
# definite row height from `.fixedSize(horizontal: false, vertical: true)` — a stack only
# re-proposes its height to its children when it has one.


@pytest.mark.parametrize("section", _KEY_STATS_SECTIONS)
def test_every_key_stats_section_renders_the_shared_carousel(section):
    body = _decl_block(_read(_ORGANISMS / section), "var body: some View")
    assert "KeyStatisticsCarousel(statisticsGroups:" in body, (
        f"{section}: no longer renders KeyStatisticsCarousel — a private copy of the card row "
        f"is how three screens carried the same misalignment")
    assert "HStack(spacing: 0)" not in body and "ScrollView(.horizontal" not in body, (
        f"{section}: the card row is back inline; keep it in the shared carousel")


def test_the_carousel_rows_are_top_aligned_with_a_definite_height():
    body = _decl_block(_read(_CAROUSEL), "var body: some View")
    assert "ScrollView(.horizontal, showsIndicators: false)" in body, "the carousel no longer scrolls"
    assert "KeyStatisticsCard(statistics:" in body, "the carousel no longer renders the cards"
    assert body.count("HStack(alignment: .top, spacing: 0)") == 2, (
        "both the outer row and the per-group pair must be top-aligned; the default `.center` "
        "floats a shorter card to the middle of its neighbour")
    assert "HStack(spacing: 0)" not in body, "a centre-aligned HStack is back in the carousel"
    assert ".fixedSize(horizontal: false, vertical: true)" in body, (
        "without a definite row height the cards keep their ideal heights and only top-align — "
        "the developer asked for equal heights")


def test_the_card_stretches_to_the_row_with_its_rows_at_the_top():
    body = _decl_block(_read(_KEY_STATS_CARD), "var body: some View")
    width = body.find(".frame(width: 160)")
    stretch = body.find(".frame(maxHeight: .infinity, alignment: .top)")
    surface = body.find(".cardSurface(AppColors.cardBackgroundNested")
    assert width != -1, "the 160pt card width is gone — this scan has drifted"
    assert stretch != -1, (
        "KeyStatisticsCard no longer stretches to the row height with its rows pinned to the "
        "top. `maxHeight: .infinity` alone would centre a four-row body in a five-row card.")
    assert surface != -1, "the nested-card fill is gone (AppColors.cardBackgroundNested is the rule)"
    assert stretch < surface, (
        "the stretch frame must precede .cardSurface — the surface paints exactly the frame it "
        "is attached to, so after it the fill stays content-sized and only the hit area grows")


# ── 8. Four Performance tiles lay out 2×2 ─────────────────────────────


def test_four_performance_tiles_use_two_columns():
    """TestFlight, build 1.0 (9), BNB: *"We have 2 year for bitcoin right? Add 2 year also"* —
    the crypto card gained a fourth tile, which a three-column grid wraps to 3 + 1."""
    src = _strip_comments(_read(_PERFORMANCE))
    columns = _decl_block(_read(_PERFORMANCE), "private var columns: [GridItem]")
    assert "periods.count == 4 ? 2 : 3" in columns, (
        "the Performance grid no longer lays four tiles out 2×2. The rule is by COUNT, not asset "
        "class: a coin with its 2 Years row, but equally a 3-5-year-old equity/ETF/index "
        "(1M/YTD/1Y/3Y) or a commodity with 6-12 months of history (1M/3M/6M/YTD); six tiles "
        "stay 3×2, five 3+2, eight 3+3+2, three in one row")
    assert "GridItem(.flexible(), spacing: AppSpacing.sm)" in columns
    assert "private let columns" not in src, "the column count is a stored constant again"
    body = _decl_block(_read(_PERFORMANCE), "var body: some View")
    assert "LazyVGrid(columns: columns" in body, "the grid no longer reads the derived columns"


def test_the_width_and_carousel_scans_are_not_vacuous():
    raw = _read(_CONTAINER)
    # The container's comments name both rejected alternatives; the scan must not see them.
    assert ".frame(maxWidth: .infinity)" in raw, "the container lost its explanatory comment"
    assert "containerRelativeFrame" in raw, "the container lost the hang explanation"
    body = _decl_block(raw, "var body: some View")
    assert ".frame(maxWidth: .infinity)" not in body and "containerRelativeFrame" not in body
    # The ScrollView closure is a proper sub-block of the body (bounding bites on both ends).
    scroll = _decl_block(body, "ScrollView(showsIndicators: false)")
    assert len(scroll) < len(body) and ".overlay(alignment: .top)" not in scroll
    assert _CLAMP in scroll
    # The carousel's comments name the defect token; the body scan must not see it.
    carousel_raw = _read(_CAROUSEL)
    assert "HStack(spacing: 0)" in carousel_raw, "the carousel lost its explanatory comment"
    assert "HStack(spacing: 0)" not in _decl_block(carousel_raw, "var body: some View")
    for section in _KEY_STATS_SECTIONS:
        assert len(_strip_comments(_read(_ORGANISMS / section))) > 300, f"{section} is a stub"


# ── 9. The pin threshold is the WHOLE above-tabs block, re-judged when it moves ──
#
# 2026-09-25, AAPL, Debug on the iPhone 17 Pro simulator: the tab bar drawn TWICE. The pinned
# overlay copy sat under the nav header and the in-scroll copy directly below it, until the next
# scroll. It was reported as intermittent and tied to a first Analysis open. Instrumenting the
# container showed it was neither:
#
# - Every loaded screen passes `aboveTabs` as a MULTI-view `@ViewBuilder` (price header + chart;
#   crypto adds a third view). A modifier on a multi-view builder is applied to EACH child, so
#   `aboveTabs.background(GeometryReader …)` measured each child alone, and the `max` reducer
#   kept the chart. The threshold read 214pt against 265pt of real content, so the bar pinned
#   ~51pt early (the price header). Resting anywhere in that band showed both copies. "Key
#   Statistics at the top" lands in it, on the Overview tab too; the Analysis tap only kept the
#   offset.
# - The pin was decided only in the scroll callback. The chart grows 20pt after load (the block
#   measures 245 then 265pt with nothing scrolling), and that callback fires BEFORE the new height
#   arrives, so a moved threshold left the pin stale until the next scroll.

_ABOVE_TABS_EMITTER = "key: AboveTabsHeightPreferenceKey.self"
# `aboveTabs` modified DIRECTLY, i.e. the pre-fix shape. It must never come back.
_BARE_ABOVE_TABS_MODIFIER = re.compile(r"\baboveTabs\s*\n?\s*\.(background|overlay|onGeometryChange)\(")


def test_the_pin_threshold_measures_the_above_tabs_block_as_one_view():
    body = _decl_block(_read(_CONTAINER), "var body: some View")
    scroll = _decl_block(body, "ScrollView(showsIndicators: false)")
    stack = _decl_block(scroll, "VStack(spacing: 0)")      # the eager stack (first match)
    wrapper = _decl_block(stack, "VStack(spacing: 0)")     # the first stack INSIDE it

    assert re.sub(r"\s+", "", wrapper) == "{aboveTabs}", (
        "the pin-threshold wrapper no longer holds exactly `aboveTabs`. It exists to turn the "
        "screens' multi-view builder (price header + chart) into ONE view for the height "
        "measurement. Without it the GeometryReader runs once per child, the max reducer keeps "
        "the chart, and the tab bar pins ~51pt early with the in-scroll copy still visible.")

    after_wrapper = stack[stack.index(wrapper) + len(wrapper):]
    modifiers = after_wrapper[: after_wrapper.index("tabBarChrome")]
    assert _ABOVE_TABS_EMITTER in modifiers, (
        "the above-tabs height is no longer measured on the wrapper (between it and the tab "
        "bar). The measurement must sit on the one-view wrapper, not on its children.")

    assert not _BARE_ABOVE_TABS_MODIFIER.search(body), (
        "`aboveTabs` is modified directly again. On a multi-view builder a modifier is applied "
        "to EACH child, so a height measured this way is the tallest child, not the block.")
    assert body.count(_ABOVE_TABS_EMITTER) == 1, (
        "the above-tabs height has more than one emitter. The max reducer would pick the "
        "tallest, and the threshold would no longer be the height of the content above the tabs.")


def test_the_pin_is_rejudged_when_the_threshold_moves():
    raw = _read(_CONTAINER)
    body = _decl_block(raw, "var body: some View")
    decide = _decl_block(raw, "private func updatePin()")

    assert "aboveTabsHeight" in decide and "scrollOffset.value" in decide, (
        "updatePin no longer judges the pin from BOTH inputs (threshold and stored offset)")

    height_cb = _decl_block(body, ".onPreferenceChange(AboveTabsHeightPreferenceKey.self)")
    assert "aboveTabsHeight = height" in height_cb, "the height callback no longer stores the height"
    assert "updatePin()" in height_cb, (
        "the pin is no longer judged when the THRESHOLD moves. The chart grows 20pt after load "
        "with the page at rest (measured 245 then 265pt), and the scroll callback fires before the new "
        "height arrives, so a pin decided only on scroll stays on the old threshold.")

    offset_cb = _decl_block(body, "action: { _, offset in")
    assert "scrollOffset.value = offset" in offset_cb, (
        "the scroll callback no longer records the offset the height callback re-judges against")
    assert "updatePin()" in offset_cb, "the scroll callback no longer decides the pin"

    src = _strip_comments(raw)
    writes = re.findall(r"(?<![\w.])isTabBarPinned\s*=(?!=)", src)
    assert len(writes) == 1 and re.search(r"(?<![\w.])isTabBarPinned\s*=(?!=)", decide), (
        "`isTabBarPinned` is written outside updatePin. A second decision site is how one "
        "input's change goes unjudged.")

    # The offset is written on every scroll frame. A value-type `@State` there is an
    # invalidation per frame, the cost the conditional pin write exists to avoid.
    assert "@State private var scrollOffset = DetailScrollOffset()" in src, (
        "the stored scroll offset is no longer the non-invalidating reference box")
    assert "final class DetailScrollOffset" in src, "the offset box type is gone"


def test_the_pin_threshold_scans_are_not_vacuous():
    raw = _read(_CONTAINER)
    # The type comment names the fixed shape in prose. Stripping must hide it, or the scans
    # above would pass on the comment alone.
    assert "`VStack(spacing: 0) { aboveTabs }`" in raw, "the container lost its explanatory comment"
    assert "{ aboveTabs }" not in _strip_comments(raw)
    # The regex bites on the pre-fix shape, verbatim from the version that shipped the bug...
    pre_fix = (
        "            VStack(spacing: 0) {\n"
        "                aboveTabs\n"
        "                    .background(\n"
        "                        GeometryReader { geometry in\n"
    )
    assert _BARE_ABOVE_TABS_MODIFIER.search(pre_fix)
    # ...and not on the wrapper, whose modifier follows a closing brace.
    assert not _BARE_ABOVE_TABS_MODIFIER.search("VStack(spacing: 0) {\n    aboveTabs\n}\n.background(")
    # The write-site regex skips the init's `self._isTabBarPinned = …` and a comparison.
    pattern = re.compile(r"(?<![\w.])isTabBarPinned\s*=(?!=)")
    assert not pattern.search("self._isTabBarPinned = isTabBarPinned")
    assert not pattern.search("if shouldPin != isTabBarPinned {")
    assert not pattern.search("isTabBarPinned == true")
    assert pattern.search("isTabBarPinned = shouldPin")
