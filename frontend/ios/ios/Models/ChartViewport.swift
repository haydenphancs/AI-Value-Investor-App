//
//  ChartViewport.swift
//  ios
//
//  The asset-detail chart's visible window: pan, pinch and fling.
//
//  RIGHT-ANCHORED and fractional. `endOffset` is how many bar slots lie between the newest
//  bar and the right edge; `visibleCount` is the window's width in slots. Older history is
//  PREPENDED as the user pans left, and a right-anchored window does not move when bars
//  arrive on the left — the old left-anchored indices had to be shifted on every page, and
//  any frame that drew between the data swap and the shift jumped.
//
//  Fractional so a slow pan or a pinch moves continuously instead of in whole-bar steps
//  (at 10–30 visible bars a whole-bar step is 11–34 pt).
//

import Combine
import QuartzCore
import SwiftUI

/// The visible window, in bar slots, measured from the newest bar.
struct ChartWindow: Equatable {
    var endOffset: Double
    var visibleCount: Double

    static let zero = ChartWindow(endOffset: 0, visibleCount: 0)
}

/// Which data a committed window belongs to. The VM stamps the range/interval its bars were
/// fetched for and bumps `version` on every USER-initiated data change (range, interval,
/// extended hours, an explicit refresh) — never on a live tick or the core→full swap.
struct ChartDataStamp: Equatable {
    let range: ChartTimeRange
    let interval: ChartInterval?
    let version: Int
}

/// The limits of one series. Recomputed by `TickerChartView` from the series in hand.
struct ChartViewportBounds: Equatable {
    /// Bars in the merged series.
    let total: Int
    /// Leftmost bar the window may show. While older history can still load this is
    /// `ChartWarmup.bars` (or the default window's own start, if that is earlier), so every
    /// visible indicator value has its full lookback; once history is exhausted it is 0.
    let floorIndex: Int
    let minVisible: Double
    let maxVisible: Double

    static let none = ChartViewportBounds(total: 0, floorIndex: 0, minVisible: 1, maxVisible: 1)

    static func make(total: Int, floorIndex: Int, defaultCount: Int) -> ChartViewportBounds {
        let total = max(total, 0)
        let floorIndex = min(max(floorIndex, 0), max(total - 1, 0))
        let available = Double(max(total - floorIndex, 1))
        let minVisible = min(10, available)
        // Zoom-out cap: what the default view shows, or 3,000 bars, whichever is more.
        // Past ~3,000 bars a 350 pt chart is ~9 bars per point; the weekly/monthly pills are
        // the honest way to see decades at once.
        let maxVisible = max(min(available, max(Double(defaultCount), 3_000)), minVisible)
        return ChartViewportBounds(total: total, floorIndex: floorIndex, minVisible: minVisible, maxVisible: maxVisible)
    }

    func clamp(_ w: ChartWindow) -> ChartWindow {
        guard total > 0 else { return .zero }
        let visible = min(max(w.visibleCount.isFinite ? w.visibleCount : maxVisible, minVisible), maxVisible)
        // Left slot = total - visible - endOffset must stay >= floorIndex.
        let maxOffset = max(0, Double(total - floorIndex) - visible)
        let offset = min(max(w.endOffset.isFinite ? w.endOffset : 0, 0), maxOffset)
        return ChartWindow(endOffset: offset, visibleCount: visible)
    }

    /// The window a range opens on: from `rangeStart` to the newest bar.
    func defaultWindow(rangeStart: Int) -> ChartWindow {
        clamp(ChartWindow(endOffset: 0, visibleCount: Double(max(total - rangeStart, 1))))
    }

    /// Slot coordinate of the window's left edge.
    func slotLeft(_ w: ChartWindow) -> Double {
        Double(total) - w.visibleCount - w.endOffset
    }

    /// The global bar range a window draws (partially visible edge bars included).
    func drawRange(_ w: ChartWindow) -> Range<Int> {
        guard total > 0, w.visibleCount > 0 else { return 0..<0 }
        let left = slotLeft(w)
        let lo = max(0, Int((left - 0.5).rounded(.down)))
        let hi = min(total, Int((left + w.visibleCount + 0.5).rounded(.up)))
        return lo < hi ? lo..<hi : 0..<0
    }
}

/// Owns the window and the gestures that move it. Only `window` is published, and only when
/// it changes — a sub-slot finger movement used to republish two values per touch event and
/// re-run the whole chart body each time.
@MainActor
final class ChartViewportState: ObservableObject {
    @Published private(set) var window: ChartWindow = .zero

    /// The stamp `window` was committed for. Until it matches the bars in hand, the chart
    /// renders the series' DEFAULT window (computed in the same pass), never a stale one.
    private(set) var appliedStamp: ChartDataStamp?
    /// Refreshed by `TickerChartView` every pass; never published.
    var bounds: ChartViewportBounds = .none

    /// Gesture anchors, in the same right-anchored terms as `window` (so a prepend
    /// mid-gesture cannot invalidate them). Plain stored properties — not published.
    private var panAnchor: ChartWindow?
    /// The pan's translation when `panAnchor` was taken — 0 at the pan's start, the finger's
    /// translation so far when the anchor is re-taken after a pinch.
    private var panBaseTranslation: CGFloat = 0
    private var pinchAnchor: ChartWindow?
    private var pinchFraction: Double = 1
    private(set) var isPinching = false
    private(set) var isPanning = false
    /// The user moved the window since the last commit — older history is paged in only
    /// for a window the USER moved toward the floor, never for the default window a range
    /// opens on (first paint stays exactly as fast as before).
    private(set) var userMovedSinceCommit = false

    // MARK: Commit

    func commit(_ w: ChartWindow, stamp: ChartDataStamp) {
        appliedStamp = stamp
        userMovedSinceCommit = false
        stopFling()
        panAnchor = nil
        pinchAnchor = nil
        set(w)
    }

    func set(_ w: ChartWindow) {
        let clamped = bounds.clamp(w)
        if clamped != window { window = clamped }
    }

    /// Re-clamp after the bounds changed (bars appended/prepended, the floor relaxed).
    func reclamp() { set(window) }

    /// `appended` new bars arrived at the right (negative: bars left the tail). A window
    /// pinned to the newest bar follows them; one the user panned back into history stays on
    /// the bars it shows.
    func absorbAppend(_ appended: Int) {
        guard appended != 0, window.endOffset > 0.5 else { return }
        panAnchor?.endOffset += Double(appended)
        pinchAnchor?.endOffset += Double(appended)
        set(ChartWindow(endOffset: window.endOffset + Double(appended), visibleCount: window.visibleCount))
    }

    var isPinnedToLatest: Bool { window.endOffset <= 0.5 }

    // MARK: Pan

    func beginPan() {
        stopFling()
        isPanning = true
        userMovedSinceCommit = true
        panAnchor = window
        panBaseTranslation = 0
    }

    /// `translation` in points; positive = finger moved right = reveal OLDER bars.
    func pan(translation: CGFloat, width: CGFloat) {
        guard isPanning, width > 0 else { return }
        guard let anchor = panAnchor else {
            // A pinch took the window mid-drag (it drops the pan's anchor): continue the drag
            // from where the pinch left the window, never from the pre-pinch zoom.
            panAnchor = window
            panBaseTranslation = translation
            return
        }
        guard anchor.visibleCount > 0 else { return }
        let deltaBars = Double((translation - panBaseTranslation) / width) * anchor.visibleCount
        let target = anchor.endOffset + deltaBars
        set(ChartWindow(endOffset: target, visibleCount: anchor.visibleCount))
        // Clamped at the floor or at the newest bar: re-anchor so the finger owns the window
        // again the moment it reverses (no dead zone to drag back through), and so a page of
        // older bars landing mid-drag lets the same drag continue straight on.
        if abs(window.endOffset - target) > 1e-9 {
            panAnchor?.endOffset = window.endOffset - deltaBars
        }
    }

    func endPan(velocity: CGFloat, width: CGFloat) {
        isPanning = false
        panAnchor = nil
        guard width > 0, window.visibleCount > 0 else { return }
        let pointsPerBar = Double(width) / window.visibleCount
        startFling(barsPerSecond: Double(velocity) / pointsPerBar, pointsPerBar: pointsPerBar)
    }

    func cancelPan() {
        isPanning = false
        panAnchor = nil
    }

    // MARK: Pinch

    /// `anchorFraction`: where the fingers started, 0 = left edge, 1 = right edge. The bar
    /// under the fingers stays under them.
    func beginPinch(anchorFraction: Double) {
        stopFling()
        isPinching = true
        userMovedSinceCommit = true
        pinchAnchor = window
        pinchFraction = min(max(anchorFraction, 0), 1)
        // A pan in progress re-anchors on its next move (see `pan`): measured from its old
        // anchor it would undo the zoom the moment the pinch ends.
        panAnchor = nil
    }

    func pinch(scale: CGFloat) {
        guard let anchor = pinchAnchor, scale.isFinite, scale > 0 else { return }
        let target = bounds.clamp(ChartWindow(endOffset: anchor.endOffset, visibleCount: anchor.visibleCount / Double(scale)))
        let visible = target.visibleCount
        // Keep slot s = left + f·visible fixed: endOffset' = endOffset + (vc - vc')·(1 - f).
        let offset = anchor.endOffset + (anchor.visibleCount - visible) * (1 - pinchFraction)
        set(ChartWindow(endOffset: offset, visibleCount: visible))
    }

    func endPinch() {
        isPinching = false
        pinchAnchor = nil
    }

    // MARK: Fling

    private var displayLink: CADisplayLink?
    private var flingVelocity: Double = 0      // bars per second, + = toward older bars
    private var flingPointsPerBar: Double = 1
    private var flingLastTime: CFTimeInterval = 0
    /// UIScrollView's normal deceleration (0.998 per ms).
    private static let decelerationPerMs = 0.998
    /// In POINTS per second, so a fling feels the same at every zoom: in bars per second, 8
    /// bars/s was ~290 pt/s at 10 visible bars (a firm flick did nothing) and ~1 pt/s at 3,000.
    private static let flingStartPointsPerSecond = 60.0
    private static let flingStopPointsPerSecond = 15.0

    var isFlinging: Bool { displayLink != nil }

    private func startFling(barsPerSecond v: Double, pointsPerBar: Double) {
        stopFling()
        guard v.isFinite, pointsPerBar.isFinite, pointsPerBar > 0,
              abs(v) * pointsPerBar > Self.flingStartPointsPerSecond else { return }
        flingVelocity = v
        flingPointsPerBar = pointsPerBar
        flingLastTime = CACurrentMediaTime()
        let link = CADisplayLink(target: FlingTarget(self), selector: #selector(FlingTarget.tick(_:)))
        link.preferredFrameRateRange = CAFrameRateRange(minimum: 60, maximum: 120, preferred: 120)
        link.add(to: .main, forMode: .common)
        displayLink = link
    }

    func stopFling() {
        displayLink?.invalidate()
        displayLink = nil
        flingVelocity = 0
    }

    fileprivate func flingTick(_ now: CFTimeInterval) {
        let dt = max(0, min(now - flingLastTime, 0.05))
        flingLastTime = now
        let before = window
        set(ChartWindow(endOffset: window.endOffset + flingVelocity * dt, visibleCount: window.visibleCount))
        flingVelocity *= pow(Self.decelerationPerMs, dt * 1000)
        // Stop when slow, or when a bound absorbed the move (the floor or the newest bar).
        if abs(flingVelocity) * flingPointsPerBar < Self.flingStopPointsPerSecond || window == before {
            stopFling()
        }
    }

    /// Weak proxy: a CADisplayLink retains its target, and the state object must not be
    /// kept alive by its own fling.
    private final class FlingTarget: NSObject {
        weak var owner: ChartViewportState?
        init(_ owner: ChartViewportState) { self.owner = owner }
        @objc func tick(_ link: CADisplayLink) {
            guard let owner else { link.invalidate(); return }
            owner.flingTick(link.targetTimestamp)
        }
    }
}
