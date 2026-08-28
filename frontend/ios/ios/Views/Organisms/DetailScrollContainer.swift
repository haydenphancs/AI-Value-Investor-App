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
/// 2. **A live-price websocket tick is a resize.** The detail view models sink
///    `livePriceManager.$livePrice` into `indexData.price`, which flows through `headerData` into
///    this container's FIRST child — the very subview the pinned offset is measured against. So
///    the invalidation fired continuously, with no user interaction beyond scrolling, which is
///    why the TestFlight report (*"I cant scroll this screen to the bottom. It's like shaking."*)
///    arrived with nothing on the screen expanded.
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

    var body: some View {
        ScrollView(showsIndicators: false) {
            // EAGER. See the type comment — do not reintroduce LazyVStack here.
            VStack(spacing: 0) {
                aboveTabs
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
            let shouldPin = aboveTabsHeight > 0 && offset >= aboveTabsHeight
            if shouldPin != isTabBarPinned {
                isTabBarPinned = shouldPin
            }
        }
        .refreshable {
            await onRefresh()
        }
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
