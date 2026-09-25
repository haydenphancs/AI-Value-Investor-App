//
//  DetailScrollContainer.swift
//  ios
//
//  Organism: the shared scroll body of the five asset-detail screens.
//

import SwiftUI

/// The scrolling body of an asset-detail screen: content above the tabs, a tab bar that sticks
/// to the top, and the selected tab's content.
///
/// WHY THIS EXISTS
/// ---------------
/// All five detail screens (Ticker, Index, ETF, Crypto, Commodity) had a byte-identical copy of
/// this, built on:
///
/// ```swift
/// LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) { … Section { tabContent } header: { … } }
/// ```
///
/// which is the Home-feed hang (`test_ios_home_layout_guards.py`) with an extra aggravator. A lazy
/// stack caches each subview's measured size and derives every offset by walking its predecessors;
/// a child that resizes mid-placement invalidates that cache and restarts the walk, which on Home
/// produced a non-terminating `LazySubviewPlacements.placeSubviews →
/// LazyHVStack.lengthAndSpacing → _ViewList_Node.applyNodes` recursion at 100% main-thread CPU.
///
/// Two things made it worse here than on Home:
///
/// 1. **`pinnedViews` forces the predecessor walk EVERY FRAME**, because the pinned header's
///    offset has to be recomputed as you scroll. That is exactly the walk a resize restarts.
/// 2. **A live-price tick is a resize.** The detail view models write a refreshed price into
///    `indexData.price`, which flows through `headerData` into this container's FIRST child —
///    the very subview the pinned offset is measured against. So the invalidation fired
///    continuously, with no user interaction beyond scrolling, which is why the TestFlight
///    report (*"I cant scroll this screen to the bottom. It's like shaking."*) arrived with
///    nothing on the screen expanded. (The tick was a `livePriceManager.$livePrice` sink at
///    the time; the FMP WebSocket is gone, but the 30s REST refresh writes the same field, so
///    the hazard is unchanged.)
///
/// The laziness was buying nothing: two children, everything from already-decoded `@Published`
/// state, and no `AsyncImage` reachable from any Overview tab. The News tab keeps its own
/// `LazyVStack` (a `ForEach` over network data WITH `AsyncImage`) nested inside this one, and that
/// one is legitimate — verify that distinction before reaching for `VStack` somewhere else.
///
/// ## Why the sticky tab bar is an overlay and not a sibling
///
/// `pinnedViews` only exists on lazy stacks, so going eager means pinning the tab bar by hand.
/// The obvious shape — insert a pinned copy above the `ScrollView` in the enclosing `VStack` —
/// is **wrong**: it shrinks the scroll view's frame by the tab bar's height at the moment of
/// pinning, so the content under it jumps by that height. `.overlay(alignment: .top)` does not
/// participate in layout at all, so the scroll view's frame never changes and there is nothing
/// to jump. The in-scroll copy keeps its space and simply scrolls up behind the (opaque) pinned
/// copy.
///
/// ## Why the pin threshold is measured on ONE wrapper view
///
/// Seen 2026-09-25, AAPL: the tab bar drawn twice, the pinned copy under the nav header and the
/// in-scroll copy directly below it, until the next scroll. It was not intermittent. Every
/// loaded screen passes `aboveTabs` as a MULTI-view `@ViewBuilder` (price header + chart; crypto
/// adds a third view), and a modifier on a multi-view builder is applied to EACH child. So the
/// height `GeometryReader` ran once per child, the `max` reducer kept the chart's height, and
/// the threshold was short by the price header: 214pt against 265pt on AAPL. The bar pinned
/// while the in-scroll copy was still fully visible. You only saw it when a scroll came to rest
/// inside that ~51pt band, which "Key Statistics at the top" does, on any tab.
/// `VStack(spacing: 0) { aboveTabs }` makes it one view with an identical layout.
///
/// The pin is also judged again when the THRESHOLD moves, not only when the offset moves. The
/// chart grows 20pt after load (the block measures 245 then 265pt with nothing scrolling), and
/// the scroll-geometry callback fires BEFORE the new height arrives. A pin decided only there
/// would stay on the old threshold until the next scroll.
///
/// ## Why the content is pinned to the viewport width
///
/// TestFlight, build 1.0 (8), ETH → Overview: *"I don't want it move the whole thing like this."*
/// The screenshot shows every child of this scroll view — chart, axis labels, range strip, the
/// tab bar and its divider, Key Statistics, Performance — translated ~73pt to the right as one
/// unit, a page-background gutter down the left edge and the right edge cut off, while the
/// header above and the AI bar below sit where they belong. That is the signature of a vertical
/// `ScrollView` whose content is WIDER than its viewport: the eager `VStack` takes the width of
/// its widest child, centres every normal-width child inside that (+half the overflow), and the
/// backing `UIScrollView` gets a `contentSize.width > bounds.width`, so a sideways drag anywhere
/// on the page drags the whole page.
///
/// No child in the tree reports a width above the viewport at default type, and the shift could
/// not be reproduced on the simulator, so the offender is environmental (iOS 26.6.x) and
/// intermittent. The guarantee is structural instead: `.frame(minWidth: 0, maxWidth: .infinity)`
/// on the stack reports exactly the PROPOSED width — a flexible frame's size is
/// `clamp(proposal, min ?? child, max ?? child)`, so with `minWidth: 0` the proposal wins — and
/// an over-wide child then overflows symmetrically and is clipped by the scroll view while its
/// siblings never move, and the content can never be dragged sideways.
///
/// Two shapes that look equivalent and are not, both measured with a synthetic 600pt child:
///
/// - `.frame(maxWidth: .infinity)` alone: `min` defaults to the CHILD's width, so the frame
///   reports 632pt and nothing is clamped. Worse, the scroll view then reports that width
///   upward and the whole screen — header and AI bar included — shifts.
/// - `.containerRelativeFrame(.horizontal)`: pins the content to the container's size, but the
///   container's size is the scroll view's, which (see above) follows the content's natural
///   width — a feedback pair that never settles. Measured: 100% main-thread CPU in
///   `GraphHost.flushTransactions → _FlexFrameLayout.sizeThatFits` from the first frame of the
///   screen, the same family as the Home-feed hang.
///
/// Pinned by `backend/tests/test_ios_detail_layout_guards.py`.
struct DetailScrollContainer<AboveTabs: View, Tabs: View, Content: View>: View {

    /// Lifted so the screen can swap its nav-bar title for the price once the tabs pin. Written
    /// only when the value actually changes — an unconditional write here re-renders the whole
    /// screen on every scroll frame.
    @Binding var isTabBarPinned: Bool

    let onRefresh: () async -> Void

    private let aboveTabs: AboveTabs
    private let tabs: Tabs
    private let content: Content

    init(
        isTabBarPinned: Binding<Bool>,
        onRefresh: @escaping () async -> Void,
        @ViewBuilder aboveTabs: () -> AboveTabs,
        @ViewBuilder tabs: () -> Tabs,
        @ViewBuilder content: () -> Content
    ) {
        self._isTabBarPinned = isTabBarPinned
        self.onRefresh = onRefresh
        self.aboveTabs = aboveTabs()
        self.tabs = tabs()
        self.content = content()
    }

    /// Height of everything above the tab bar. Measured, not assumed — the price header and
    /// chart differ per asset class, and the skeleton is a different height again.
    @State private var aboveTabsHeight: CGFloat = 0

    /// The latest scroll offset, kept so the pin can be judged again when `aboveTabsHeight`
    /// moves under a page at rest. A reference box and NOT `@State`: it is written on every
    /// scroll frame, and a `@State` write would re-render the container at scroll rate.
    @State private var scrollOffset = DetailScrollOffset()

    /// The scroll content's natural width, i.e. the width of its WIDEST child. Read only to
    /// name an over-wide child in DEBUG; the frame below keeps it from ever affecting layout.
    @State private var contentNaturalWidth: CGFloat = 0
    @State private var containerWidth: CGFloat = 0

    var body: some View {
        ScrollView(showsIndicators: false) {
            // EAGER. See the type comment — do not reintroduce LazyVStack here.
            VStack(spacing: 0) {
                // Wrapped so the measurement sees ONE view. `aboveTabs` is a multi-view
                // builder on every loaded screen, and a bare `.background` would measure each
                // child on its own. See the type comment.
                VStack(spacing: 0) {
                    aboveTabs
                }
                .background(
                    GeometryReader { geometry in
                        Color.clear.preference(
                            key: AboveTabsHeightPreferenceKey.self,
                            value: geometry.size.height
                        )
                    }
                )

                tabBarChrome

                content
            }
            // Measures the stack's NATURAL width (a `VStack` is as wide as its widest child),
            // which the container-relative frame below deliberately does not constrain. A
            // width is a property of the content and stays valid after culling, like the
            // height preference above.
            .background(
                GeometryReader { geometry in
                    Color.clear.preference(
                        key: ContentNaturalWidthPreferenceKey.self,
                        value: geometry.size.width
                    )
                }
            )
            // Exactly the proposed (viewport) width, whatever the children report. See the
            // type comment: this is what stops the whole page from being dragged sideways.
            // `minWidth: 0` is load-bearing — see the type comment for why the max-only form
            // does nothing here and why `containerRelativeFrame` must not replace it.
            .frame(minWidth: 0, maxWidth: .infinity)
        }
        // Draws over the scrolled content; contributes NO layout. This is what replaces
        // `pinnedViews` without moving the scroll view's frame.
        .overlay(alignment: .top) {
            if isTabBarPinned {
                tabBarChrome
            }
        }
        .onPreferenceChange(AboveTabsHeightPreferenceKey.self) { height in
            if height > 0, height != aboveTabsHeight {
                aboveTabsHeight = height
                // The threshold moved under an offset that did not, so no scroll callback
                // will come to fix the pin. Judge it here too.
                updatePin()
            }
        }
        .onPreferenceChange(ContentNaturalWidthPreferenceKey.self) { width in
            if width > 0, width != contentNaturalWidth {
                contentNaturalWidth = width
                reportOverWideContent()
            }
        }
        // The pin is derived from the SCROLL OFFSET, never from the tab bar's own geometry.
        //
        // It used to read the tab bar's `minY` through a GeometryReader in its background, and
        // that is unreliable by construction: SwiftUI stops updating a `GeometryReader` once its
        // view leaves the rendered band, so the reading FREEZES at whatever it was when the tab
        // bar scrolled away. Flick past it in one gesture and the last value was still positive,
        // so the bar never pinned at all — measured on SPY, where it stayed unpinned through the
        // whole tab. It only ever looked correct when you crept past the threshold slowly.
        //
        // `onScrollGeometryChange` reads the scroll view itself, which is never culled.
        .onScrollGeometryChange(for: CGFloat.self) { geometry in
            geometry.contentOffset.y + geometry.contentInsets.top
        } action: { _, offset in
            scrollOffset.value = offset
            updatePin()
        }
        // The viewport width, for the DEBUG over-wide report only. Separate from the pin
        // read above so the pin keeps firing on every frame with the cheapest possible value.
        .onScrollGeometryChange(for: CGFloat.self) { geometry in
            geometry.containerSize.width
        } action: { _, width in
            if width > 0, width != containerWidth {
                containerWidth = width
                reportOverWideContent()
            }
        }
        .refreshable {
            await onRefresh()
        }
    }

    /// The ONE place the pin is decided. It has two inputs, and each has its own trigger: the
    /// offset (scroll callback) and the threshold (height preference). Deciding it in only one
    /// of those callbacks leaves it stale whenever the other input moves alone.
    private func updatePin() {
        let shouldPin = aboveTabsHeight > 0 && scrollOffset.value >= aboveTabsHeight
        if shouldPin != isTabBarPinned {
            isTabBarPinned = shouldPin
        }
    }

    /// Names an over-wide child while a developer is looking. Nothing on the page depends on
    /// it: the container-relative frame has already kept the overflow from moving anything.
    /// Prints only when the pair changes, so a steady state costs one line, not one per frame.
    private func reportOverWideContent() {
        #if DEBUG
        guard containerWidth > 0, contentNaturalWidth > containerWidth + 0.5 else { return }
        print(
            "⚠️ [DetailScrollContainer] scroll content is \(Int(contentNaturalWidth.rounded()))pt wide in a "
            + "\(Int(containerWidth.rounded()))pt viewport — a child reports "
            + "\(Int((contentNaturalWidth - containerWidth).rounded()))pt more than it was offered. "
            + "The page stays pinned; find that child."
        )
        #endif
    }

    /// Rendered twice — once in the scroll content (so it scrolls away) and once in the overlay
    /// (so it sticks). Both bind to the same `selectedTab`, so either responds to a tap. The
    /// opaque `AppColors.background` is what hides the in-scroll copy behind the pinned one.
    private var tabBarChrome: some View {
        VStack(spacing: 0) {
            tabs
                .padding(.top, AppSpacing.lg)

            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
        .background(AppColors.background)
    }
}

/// Height of the content above the tab bar, used to decide when the tab bar pins.
///
/// Deliberately a HEIGHT and not a position: a height is a property of the content and stays
/// valid after SwiftUI stops updating the reader, whereas a scroll-relative position silently
/// freezes the moment its view leaves the rendered band.
struct AboveTabsHeightPreferenceKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

/// The latest scroll offset of a `DetailScrollContainer`. A class so writing it is not a view
/// invalidation: the scroll callback writes it on every frame.
final class DetailScrollOffset {
    var value: CGFloat = 0
}

/// The scroll content's natural width — what its widest child reports. Compared against the
/// viewport in DEBUG to name a child that overflows; never used for layout.
struct ContentNaturalWidthPreferenceKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}
