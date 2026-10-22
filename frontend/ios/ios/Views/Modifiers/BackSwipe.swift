//
//  BackSwipe.swift
//  ios
//
//  Modifier: edge-anchored swipe-to-go-back that does NOT fight the scroll view.
//

import QuartzCore
import SwiftUI

/// Swipe-from-the-left-edge to pop, for screens that hide the navigation bar.
///
/// WHY THIS EXISTS
/// ---------------
/// Seven screens hand-rolled this, all identically, and all in the form that breaks scrolling:
///
/// ```swift
/// .gesture(DragGesture().onEnded { v in
///     if v.translation.width > 100 { handleBackTapped() } })
/// ```
///
/// Three separate faults in that one block:
///
/// 1. **`.gesture` COMPETES with the ScrollView's pan.** It is attached to the whole screen,
///    outside the `ScrollView`, alongside `.refreshable`. A `DragGesture` with the default
///    `minimumDistance` of 10 engages on any drag ≥10pt in ANY direction — including a plain
///    vertical scroll — so the scroll and the swipe arbitrate over every flick. Reported from
///    TestFlight as *"I cant scroll this screen to the bottom. It's like shaking."*
/// 2. **No axis filter.** `translation.width > 100` was checked without comparing it to
///    `.height`, so a lazy diagonal flick down-and-right popped the screen mid-scroll.
/// 3. **No origin filter.** Any rightward drag anywhere on the screen counted, including one
///    that began inside a horizontal carousel.
///
/// The fix is `.simultaneousGesture`, which lets the ScrollView keep its pan instead of
/// arbitrating for it. But that cuts both ways: `.gesture` used to give child gestures
/// priority, which is the only reason a right-swipe on the key-stats carousel did not also
/// pop the screen. Under `.simultaneousGesture` it would — so the origin filter below is
/// **load-bearing**, not tidiness. It is what replaces the priority we gave up.
///
/// Matches the system interactive-pop affordance, which `.navigationBarHidden(true)` disables
/// and which is why these screens rolled their own at all.
struct BackSwipe: ViewModifier {
    let onBack: () -> Void

    /// Where the screen's price chart is. A pan that STARTS on the chart belongs to the chart
    /// — dragging right there scrolls back in time — and must never also pop the screen. The
    /// chart sits 16 pt from the edge, inside the 44 pt origin zone, so the origin filter
    /// alone cannot tell the two apart. nil on screens without a chart.
    @Environment(\.chartTouchRegion) private var chartTouchRegion

    /// How far from the leading edge a back-swipe may begin. The system edge-pan region is
    /// ~20-30pt; 44 is one `HitSlop.minimumTarget` and stays clear of any horizontal carousel,
    /// all of which are inset by at least `AppSpacing.lg`.
    private static let edgeWidth: CGFloat = 44

    /// Travel required to commit. Unchanged from the seven call sites this replaces.
    private static let commitDistance: CGFloat = 100

    /// Horizontal travel must beat vertical by this factor. A scroll that drifts sideways is
    /// still a scroll.
    private static let axisRatio: CGFloat = 1.5

    func body(content: Content) -> some View {
        content.simultaneousGesture(
            // minimumDistance 20 (not the default 10) so a tap-with-a-wobble never arms it.
            DragGesture(minimumDistance: 20, coordinateSpace: .global)
                .onEnded { value in
                    guard value.startLocation.x <= Self.edgeWidth else { return }
                    if let region = chartTouchRegion?.frame, region.contains(value.startLocation) { return }
                    guard value.translation.width > Self.commitDistance else { return }
                    guard abs(value.translation.width)
                            > abs(value.translation.height) * Self.axisRatio else { return }
                    onBack()
                }
        )
    }
}

extension View {
    /// Edge-anchored swipe-to-go-back. Safe to combine with a `ScrollView`; see `BackSwipe`.
    func backSwipe(perform onBack: @escaping () -> Void) -> some View {
        modifier(BackSwipe(onBack: onBack))
    }
}

/// The chart's on-screen frame in GLOBAL coordinates, for `BackSwipe`. A plain reference box,
/// never published: the chart writes it on every scroll frame (its global frame moves as the
/// page scrolls), and a published value would re-render the whole screen at scroll rate —
/// the regression `DetailScrollContainer`'s reference box exists to prevent.
final class ChartTouchRegion {
    /// The chart's global frame, written on every geometry change.
    var chartFrame: CGRect = .null
    /// The chart can pan right now — not on 1D (one session, scrub only) and not while its
    /// bars are loading. Written every chart pass.
    var isPannable = false
    /// A scrub is armed on the chart (from the hold that arms it until the finger lifts). A
    /// scrub that starts at the left edge and drags right is the chart's touch even where the
    /// chart cannot pan; its release must not pop the screen — however long the finger rested
    /// before lifting (a resting finger sends no movement).
    var scrubArmed = false
    /// When the last scrub ended (`CACurrentMediaTime`). BackSwipe's `onEnded` and the
    /// scrub's end arrive together in either order: whichever runs second still sees one.
    var lastScrubAt: CFTimeInterval = 0
    static let scrubGrace: CFTimeInterval = 0.5
    /// What BackSwipe leaves alone: the chart's frame while it can pan or a scrub is (or just
    /// was) armed, else nothing (a quick edge swipe on a 1D chart is the back gesture again).
    var frame: CGRect {
        isPannable || scrubArmed || CACurrentMediaTime() - lastScrubAt < Self.scrubGrace ? chartFrame : .null
    }
}

extension EnvironmentValues {
    /// Injected by a detail screen OUTSIDE `.backSwipe { }`, so both the modifier and the
    /// chart inside the screen read the same box.
    @Entry var chartTouchRegion: ChartTouchRegion? = nil
}
