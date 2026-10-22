//
//  ChartCrosshair.swift
//  ios
//
//  The chart's touch layer: hold-and-drag = crosshair scrub, one-finger horizontal drag =
//  pan (with momentum), pinch = zoom around the fingers.
//
//  The pan is a UIKit recognizer (`ChartPanGesture`) because a SwiftUI `DragGesture` inside
//  the page's vertical ScrollView can only be SIMULTANEOUS with the scroll: a vertical flick
//  that started on the chart panned it sideways, and a horizontal pan dragged the page.
//  `ChartPanGesture` begins only when the movement is horizontal and is never simultaneous
//  with the scroll view's pan — whichever axis wins first owns the touch.
//

import Combine
import QuartzCore
import SwiftUI
import UIKit

/// State object shared between the chart's leaf views (scrub header, crosshair, pane
/// readouts). `TickerChartView` holds it WITHOUT observing it, so a scrub step re-renders
/// only those leaves.
final class CrosshairState: ObservableObject {
    /// GLOBAL index into the chart's `ChartSeries` — valid for the series it was set on.
    /// Readers resolve it through `index(in:)`.
    @Published var selectedIndex: Int? = nil
    @Published var isDragging: Bool = false
    /// The selected bar's date. A page of older bars landing mid-scrub shifts every global
    /// index by the page's length; the date is how the selection follows its bar.
    var selectedDate: String? = nil
    /// The selected bar's distance from the newest bar when it was picked. The newest bar is
    /// RE-DATED in place on a refresh (crypto's live point, a weekly bucket): a selection on
    /// it follows it to its new date instead of ending the scrub.
    var selectedFromEnd: Int? = nil
    /// The finger is on the left half of the chart. Published: past the last 1D bar the
    /// snapped bar stays the same while the finger crosses the middle, and the readout chip
    /// (which sits on the side AWAY from the finger) must still move.
    @Published var fingerOnLeft: Bool? = nil
    /// The hold that starts a scrub has been recognized (before any movement). Not
    /// published — the pan recognizer reads it to stay out of a scrub.
    var isArmed = false

    /// The selected bar's index in `series` — the stored index when it still names that
    /// bar, else the bar with the selected date (nil when the bar is gone).
    func index(in series: ChartSeries) -> Int? {
        guard let i = selectedIndex else { return nil }
        guard let date = selectedDate else { return i >= 0 && i < series.count ? i : nil }
        if i >= 0, i < series.count, series.dates[i] == date { return i }
        let j = series.lowerBound(date)
        if j < series.count, series.dates[j] == date { return j }
        // Only the newest bar is ever re-dated: follow it, nothing else.
        if selectedFromEnd == 0, let last = series.dates.last, last > date { return series.count - 1 }
        return nil
    }

    func clearSelection() {
        if isDragging { isDragging = false }
        if selectedIndex != nil { selectedIndex = nil }
        if fingerOnLeft != nil { fingerOnLeft = nil }
        selectedDate = nil
        selectedFromEnd = nil
    }
}

/// Transparent gesture layer over the main chart, plus the crosshair it draws.
struct ChartInteractionLayer: View {
    let series: ChartSeries
    let frame: ChartFrame
    /// The SAME y-range the canvas draws with, so the dot sits on the line.
    let yRange: ChartYRange
    let displayedRange: ChartTimeRange
    /// Pan and pinch: off on 1D (one session laid out by time) and while the bars in hand
    /// belong to a different range than the pill.
    let canNavigate: Bool
    let canScrub: Bool
    @ObservedObject var crosshairState: CrosshairState
    /// Not observed: this layer only drives it.
    let viewport: ChartViewportState
    /// A pan or pinch began — the chart decides whether to page in older history.
    var onNavigationBegan: () -> Void = {}

    /// Not observed: a scrub step stamps it so BackSwipe leaves the scrub's release alone.
    @Environment(\.chartTouchRegion) private var touchRegion
    @GestureState private var scrubActive = false
    @GestureState private var pinchActive = false

    var body: some View {
        GeometryReader { geometry in
            let size = geometry.size
            ZStack {
                Color.clear
                    .contentShape(Rectangle())
                    .gesture(scrubGesture(size: size))
                    .simultaneousGesture(pinchGesture)
                    .gesture(panGesture(width: size.width))

                // Only a bar the price scale covers: the dot's y comes from `yRange`.
                if crosshairState.isDragging, let i = crosshairState.index(in: series),
                   frame.visibleRange.contains(i) {
                    crosshair(at: i, size: size)
                }
            }
        }
        .onChange(of: series.revision) {
            // Re-point the stored index at the same bar in the new series (older bars
            // prepended mid-scrub shift every index); the readouts resolve it the same way.
            guard crosshairState.isDragging else { return }
            guard let resolved = crosshairState.index(in: series) else {
                // The bar is gone: drop the selection, keep the scrub armed (the finger is
                // still down — its next move picks a bar again).
                crosshairState.clearSelection()
                return
            }
            // Only re-point here. Clamping into the visible bars happens in `TickerChartView`
            // AFTER the tail append is absorbed: this pass's frame still has the pre-append
            // window, and a clamp against it moved the selection one bar per appended bar.
            if crosshairState.selectedIndex != resolved {
                crosshairState.selectedIndex = resolved
            }
        }
        .onDisappear {
            // A layer torn down mid-scrub (a placeholder replaced the canvas) never sees the
            // scrub end. End it fully: `isArmed` outlives this layer (the state is the chart's),
            // and a stale one blocked the pan and the next scrub's arming.
            endScrub()
        }
        .onChange(of: scrubActive) { _, active in
            if !active { endScrub() }
        }
        .onChange(of: pinchActive) { _, active in
            if !active { viewport.endPinch() }
        }
    }

    // MARK: Gestures

    private func scrubGesture(size: CGSize) -> some Gesture {
        LongPressGesture(minimumDuration: 0.15)
            .sequenced(before: DragGesture(minimumDistance: 0))
            .updating($scrubActive) { value, state, _ in
                if case .second(true, _) = value { state = true }
            }
            .onChanged { value in
                guard canScrub, case .second(true, let drag) = value else { return }
                if !crosshairState.isArmed {
                    crosshairState.isArmed = true
                    viewport.stopFling()
                }
                // Not tied to the `isArmed` transition: whatever state the chart kept, an armed
                // scrub always marks its touch for BackSwipe.
                if touchRegion?.scrubArmed == false { touchRegion?.scrubArmed = true }
                guard let drag else { return }
                updateCrosshair(at: drag.location, in: size)
            }
            .onEnded { _ in endScrub() }
    }

    private var pinchGesture: some Gesture {
        MagnifyGesture()
            .updating($pinchActive) { _, state, _ in state = true }
            .onChanged { value in
                guard canNavigate, !crosshairState.isDragging else { return }
                if !viewport.isPinching {
                    viewport.beginPinch(anchorFraction: Double(value.startAnchor.x))
                    onNavigationBegan()
                }
                viewport.pinch(scale: value.magnification)
            }
            .onEnded { _ in viewport.endPinch() }
    }

    private func panGesture(width: CGFloat) -> ChartPanGesture {
        ChartPanGesture(
            isEnabled: canNavigate,
            canBegin: { [crosshairState, viewport] in
                canNavigate && !crosshairState.isDragging && !crosshairState.isArmed && !viewport.isPinching
            },
            onTouchDown: { [viewport] in
                // Any new touch stops a fling — the direction-agnostic "catch".
                viewport.stopFling()
            },
            onBegan: {
                viewport.beginPan()
                onNavigationBegan()
            },
            onChanged: { translation in
                guard !viewport.isPinching else { return }
                viewport.pan(translation: translation, width: width)
            },
            onEnded: { velocity in
                viewport.endPan(velocity: viewport.isPinching ? 0 : velocity, width: width)
            },
            onCancelled: { viewport.cancelPan() }
        )
    }

    // MARK: Crosshair

    private func updateCrosshair(at location: CGPoint, in size: CGSize) {
        // size.width > 0 is required: a zero-width GeometryReader (transient layout while
        // scrubbing) makes `x / width` NaN, and `Int(NaN)` TRAPS.
        guard size.width > 0, frame.drawRange.count > 0 else { return }
        let x = min(max(location.x, 0), size.width)
        guard let index = frame.index(atX: x, width: size.width), index < series.count else { return }
        if crosshairState.selectedIndex != index {
            crosshairState.selectedDate = series.dates[index]
            crosshairState.selectedFromEnd = series.count - 1 - index
            crosshairState.selectedIndex = index
        }
        let onLeft = x < size.width / 2
        if crosshairState.fingerOnLeft != onLeft {
            crosshairState.fingerOnLeft = onLeft
        }
        if !crosshairState.isDragging {
            crosshairState.isDragging = true
        }
    }

    private func endScrub() {
        crosshairState.isArmed = false
        crosshairState.clearSelection()
        // The grace runs from the END of the scrub, and the armed flag covers BackSwipe's
        // `onEnded` running first.
        touchRegion?.scrubArmed = false
        touchRegion?.lastScrubAt = CACurrentMediaTime()
    }

    @ViewBuilder
    private func crosshair(at i: Int, size: CGSize) -> some View {
        let x = frame.x(i, width: size.width)
        let y = yRange.y(series.close[i], height: size.height)

        Path { path in
            path.move(to: CGPoint(x: x, y: 0))
            path.addLine(to: CGPoint(x: x, y: size.height))
        }
        .stroke(AppColors.textMuted.opacity(0.6), style: StrokeStyle(lineWidth: 0.5, dash: [4, 3]))

        Path { path in
            path.move(to: CGPoint(x: 0, y: y))
            path.addLine(to: CGPoint(x: size.width, y: y))
        }
        .stroke(AppColors.textMuted.opacity(0.4), style: StrokeStyle(lineWidth: 0.5, dash: [4, 3]))

        Circle()
            .fill(AppColors.textPrimary)
            .frame(width: 7, height: 7)
            .position(x: x, y: y)

    }
}

// MARK: - Horizontal pan (UIKit)

/// One-finger pan that begins only on HORIZONTAL movement, so the page's vertical scroll
/// keeps vertical drags. Never simultaneous with another recognizer: when the scroll view's
/// pan wins first the chart stays still, and vice versa.
struct ChartPanGesture: UIGestureRecognizerRepresentable {
    /// Off whenever the chart cannot pan (1D, data loading). A recognizer that merely FAILS
    /// each touch still takes part in recognition, and it kept the screen's edge swipe-back
    /// from firing on a 1D chart; a disabled one takes no part at all.
    let isEnabled: Bool
    /// Read LIVE at the moment UIKit asks (the closure captures reference-typed state).
    let canBegin: () -> Bool
    /// Every touch that lands on the chart, before any recognition.
    let onTouchDown: () -> Void
    let onBegan: () -> Void
    /// Translation in points since the pan began (+ = finger moved right).
    let onChanged: (CGFloat) -> Void
    /// Horizontal velocity in points per second.
    let onEnded: (CGFloat) -> Void
    let onCancelled: () -> Void

    func makeCoordinator(converter: CoordinateSpaceConverter) -> Coordinator {
        Coordinator(parent: self)
    }

    func makeUIGestureRecognizer(context: Context) -> UIPanGestureRecognizer {
        let recognizer = UIPanGestureRecognizer()
        recognizer.maximumNumberOfTouches = 1
        recognizer.cancelsTouchesInView = false
        recognizer.delegate = context.coordinator
        recognizer.isEnabled = isEnabled
        return recognizer
    }

    func updateUIGestureRecognizer(_ recognizer: UIPanGestureRecognizer, context: Context) {
        // The representable is rebuilt on every pass; the coordinator must read the newest
        // closures, or `canBegin` would answer from the first frame's state forever.
        context.coordinator.parent = self
        if recognizer.isEnabled != isEnabled { recognizer.isEnabled = isEnabled }
    }

    func handleUIGestureRecognizerAction(_ recognizer: UIPanGestureRecognizer, context: Context) {
        switch recognizer.state {
        case .began:
            onBegan()
        case .changed:
            onChanged(context.converter.localTranslation?.x ?? recognizer.translation(in: recognizer.view).x)
        case .ended:
            onEnded(context.converter.localVelocity?.x ?? recognizer.velocity(in: recognizer.view).x)
        case .cancelled, .failed:
            onCancelled()
        default:
            break
        }
    }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var parent: ChartPanGesture

        init(parent: ChartPanGesture) {
            self.parent = parent
        }

        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            guard parent.canBegin(), let pan = gestureRecognizer as? UIPanGestureRecognizer else { return false }
            let v = pan.velocity(in: pan.view)
            return abs(v.x) > 1.2 * abs(v.y)
        }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
            parent.onTouchDown()
            return true
        }

        /// Never alongside a scroll view's pan — that is the whole point of this recognizer.
        /// Alongside SwiftUI's own gesture recognizer (the scrub's long press, the pinch,
        /// BackSwipe's drag), yes: SwiftUI routes all of a hosting view's gestures through one
        /// recognizer that is already active at touch-down, and refusing it blocks this pan
        /// entirely.
        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            !(otherGestureRecognizer.view is UIScrollView)
        }

        /// A scroll view's pan waits for this one to decide. A horizontal drag on the chart
        /// becomes a chart pan; anything else fails `gestureRecognizerShouldBegin` within the
        /// pan hysteresis, and the page scrolls as before.
        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldBeRequiredToFailBy otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            otherGestureRecognizer.view is UIScrollView && otherGestureRecognizer is UIPanGestureRecognizer
        }
    }
}
