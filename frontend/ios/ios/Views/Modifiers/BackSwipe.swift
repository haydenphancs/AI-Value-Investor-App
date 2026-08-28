//
//  BackSwipe.swift
//  ios
//
//  Modifier: edge-anchored swipe-to-go-back that does NOT fight the scroll view.
//

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
            DragGesture(minimumDistance: 20)
                .onEnded { value in
                    guard value.startLocation.x <= Self.edgeWidth else { return }
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
