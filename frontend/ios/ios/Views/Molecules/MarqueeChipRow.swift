//
//  MarqueeChipRow.swift
//  ios
//
//  Molecule: the suggestion-chip row above "Ask Cay AI…". Drifts continuously on the
//  global chat; sits still on the five detail bars.
//
//  ── ONE CODE PATH ──────────────────────────────────────────────────────────────────
//  Reduce Motion, VoiceOver, a finger on the row, an off-screen row, a backgrounded app,
//  a detail bar that never drifts, and a row with too few chips to overflow are ALL the
//  same still row. Every one of them is a term in `isPaused`, which feeds TimelineView's
//  `paused:` argument; `body` never branches on any of them. A second, static branch
//  would be a second thing to keep in sync, and the one that gets edited is never the one
//  the reviewer is looking at.
//
//  ── WHY TimelineView AND NOT THE OBVIOUS ALTERNATIVES ──────────────────────────────
//  • `.scrollPosition(id:)` writes its binding during layout and has frozen this app
//    before. It is banned, and `test_ios_collapse_scroll_guards.py` already scans every
//    file under `Views/` for it.
//  • A `ScrollViewReader` + repeated `scrollTo` is a discrete animated jump, not drift,
//    and driving it per frame over a changing content size is the same family of bug.
//  • `.repeatForever` on the offset — the approach the old, unused `MarqueeText` atom
//    took, removed alongside this change — CANNOT BE PAUSED. SwiftUI offers no pause on a running animation, and the presentation
//    layer's current value is unreadable from pure SwiftUI, so stopping means cancelling
//    and resuming means jumping. It also loses the single code path, since Reduce Motion
//    would need its own branch. And this app has already hard-frozen the main thread once
//    with `.repeatForever` wrapped around a resizing subtree.
//  • A `Timer` publisher writing `@State` re-evaluates the WHOLE enclosing body 60×/s —
//    including `CaydexAIChatBar`'s `TextField` and its `@FocusState`. TimelineView keeps
//    the invalidation inside its own closure.
//

import SwiftUI

struct MarqueeChipRow: View {

    let chips: [String]
    /// The global chat passes `true`. The detail bars leave it `false`, which is why they
    /// need no edit at all to stay still.
    var drifts: Bool = false
    /// Points per second. Slow on purpose: a chip has to stay readable and aimable, and
    /// this is a suggestion strip, not a stock ticker.
    var speed: CGFloat = 18
    var spacing: CGFloat = AppSpacing.sm
    var onTap: (String) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOverEnabled
    /// ⚠️ Compared against `.background`, NOT `!= .active`. `.inactive` is transient — the
    /// app switcher, Control Centre, an incoming call — and on the Simulator it is also
    /// every moment the window is not key. Pausing on it made the row look permanently
    /// dead to anyone testing with a browser or terminal in focus, which is exactly how
    /// this was first reported.
    @Environment(\.scenePhase) private var scenePhase

    @State private var tileWidth: CGFloat = 0
    @State private var viewportWidth: CGFloat = 0
    /// The ONLY positional state. Drift and drag both resolve into this one number.
    @State private var base: CGFloat = 0
    @State private var anchor: Date = .now
    @State private var lastTranslation: CGFloat = 0
    @State private var isTouching = false
    @State private var isOnScreen = false
    @State private var resumeTask: Task<Void, Never>?

    /// Apple's minimum comfortable target. The pill itself stays its designed height; this
    /// is the row's, so the extra is hit area rather than a bigger chip.
    static let rowHeight: CGFloat = 44

    private var unit: CGFloat { tileWidth + spacing }

    /// Whether the content is actually wider than the screen. When it is not there is
    /// nothing to scroll to, so the row must not drift into empty space — and it must not
    /// draw a second copy of chips the user can already see.
    private var overflows: Bool { tileWidth > 1 && tileWidth > viewportWidth + 1 }

    private var tileCount: Int {
        guard overflows, unit > 0 else { return 1 }
        return max(2, Int((viewportWidth / unit).rounded(.up)) + 1)
    }

    private var isPaused: Bool {
        !drifts
            || !overflows
            || reduceMotion
            || voiceOverEnabled          // you cannot chase a moving element with a rotor
            || isTouching
            || !isOnScreen
            || scenePhase == .background
    }

    var body: some View {
        // Built once per body evaluation and captured by the closure below, so a 60 Hz
        // tick re-applies a transform rather than rebuilding ten Buttons.
        let content = tiles

        // ⚠️ The tiled row is `.fixedSize(horizontal: true)`, so it reports its FULL
        // intrinsic width — several screens wide once tiled. That width must not escape
        // upward. It is held inside an `.overlay` on a `Color.clear` that owns the frame,
        // because an overlay's child never sizes its parent; `.frame(maxWidth: .infinity)`
        // alone does NOT contain a fixedSize child, and `.clipped()` only affects drawing,
        // not layout.
        //
        // Getting this wrong is not a cosmetic bug and it is not local: the oversized row
        // widened the whole enclosing VStack, which pushed AIChatScreen's ✕ off the right
        // edge of the screen and centred the empty-state greeting somewhere off-canvas.
        // Both looked like unrelated missing views. Verified on the simulator.
        return Color.clear
            .frame(height: Self.rowHeight)
            .frame(maxWidth: .infinity)
            .overlay(alignment: .leading) {
                TimelineView(.animation(minimumInterval: 1.0 / 60.0, paused: isPaused)) { context in
                    content.offset(x: offset(at: context.date))
                }
                .fixedSize()
            }
            .clipped()
        .background(
            GeometryReader { proxy in
                Color.clear.preference(key: MarqueeViewportWidthKey.self, value: proxy.size.width)
            }
        )
        // Preferences are delivered after layout settles and neither width depends on the
        // offset, so this converges in one pass — it is NOT the write-during-layout cycle
        // that makes `.scrollPosition` unsafe. The epsilon stops float jitter re-entering.
        .onPreferenceChange(MarqueeViewportWidthKey.self) { width in
            if abs(width - viewportWidth) > 0.5 { viewportWidth = width }
        }
        .onPreferenceChange(MarqueeTileWidthKey.self) { width in
            if abs(width - tileWidth) > 0.5 {
                tileWidth = width
                base = 0
                anchor = .now
            }
        }
        .contentShape(Rectangle())
        .simultaneousGesture(pan)
        .onAppear {
            isOnScreen = true
            anchor = .now
        }
        // Freeze and resume WITHOUT jumping. Pausing commits the current position into
        // `base`; resuming restarts the clock from there. Both arms reset `anchor`, so
        // elapsed time never accumulates while the row is standing still — otherwise
        // returning from the background would fast-forward it by however long the app was
        // away. This fires for every pause reason: Reduce Motion, VoiceOver, a finger
        // down, the app deactivating, or the row scrolling out of sight.
        .onChange(of: isPaused) { _, paused in
            if paused { base = rawOffset(at: .now) }
            anchor = .now
        }
        .onDisappear {
            isOnScreen = false
            resumeTask?.cancel()
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Suggested questions")
        // A custom clipped container has no native scroll for VoiceOver to drive, so the
        // rotor would be unable to reach anything off-screen without this.
        .accessibilityScrollAction { edge in
            guard overflows else { return }
            let step = viewportWidth * 0.8
            base = wrapped(offset(at: .now) + (edge == .leading ? step : -step))
            anchor = .now
        }
    }

    private var tiles: some View {
        HStack(spacing: spacing) {
            ForEach(0..<tileCount, id: \.self) { tile in
                singleRow
                    // Only the first copy is real to VoiceOver; the rest are the seamless
                    // loop's machinery and would otherwise read every question 2-3 times.
                    .accessibilityHidden(tile != 0)
                    .background(
                        GeometryReader { proxy in
                            Color.clear.preference(
                                key: MarqueeTileWidthKey.self, value: proxy.size.width
                            )
                        }
                    )
            }
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    private var singleRow: some View {
        HStack(spacing: spacing) {
            // Index-keyed, NEVER `id: \.self`. Tiling repeats every string, and two equal
            // ids inside one ForEach collapse to a single element — which is exactly why
            // the row this replaced could not simply be wrapped.
            ForEach(Array(chips.enumerated()), id: \.offset) { _, chip in
                CaydexAISuggestionChip(text: chip) { onTap(chip) }
            }
        }
    }

    /// Where the row sits right now. A pure function of time — nothing animates; the
    /// offset simply differs each frame.
    private func offset(at date: Date) -> CGFloat {
        // While paused, hold the frozen position. `base` IS that position, because the
        // pause transition below commits it. An earlier version instead zeroed `elapsed`
        // here WITHOUT committing, so every pause snapped the row back to wherever it had
        // last been dragged — which read as "it moves for a second, then jumps back".
        isPaused ? wrapped(base) : rawOffset(at: date)
    }

    /// The drifting position, ignoring the pause state. Split out so the pause transition
    /// can ask "where are we?" without `offset` answering with the frozen value.
    private func rawOffset(at date: Date) -> CGFloat {
        guard overflows else { return 0 }
        return wrapped(base - speed * CGFloat(max(0, date.timeIntervalSince(anchor))))
    }

    /// Folds any real number into `(-unit, 0]`.
    ///
    /// This is what makes the loop seamless AND the drag endless: there is no start or end
    /// to clamp against, in either direction, so nothing can be dragged "off" the row.
    private func wrapped(_ value: CGFloat) -> CGFloat {
        guard unit > 0 else { return 0 }
        let remainder = value.truncatingRemainder(dividingBy: unit)
        return remainder > 0 ? remainder - unit : remainder
    }

    private var pan: some Gesture {
        // `minimumDistance: 0` so `onChanged` fires on TOUCH DOWN — the chip the user is
        // aiming at stops moving BEFORE the tap resolves, which is the whole reason a
        // moving target is tappable here. `.simultaneousGesture` so the chip's own Button
        // still receives that tap; a plain `.gesture` on the parent would lose to the
        // child, and the child would then swallow every pan that begins on a chip, which
        // is nearly all of them.
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                if !isTouching {
                    base = offset(at: .now)
                    anchor = .now
                    lastTranslation = 0
                    isTouching = true
                    resumeTask?.cancel()
                }
                let delta = value.translation.width - lastTranslation
                lastTranslation = value.translation.width
                base = wrapped(base + delta)
            }
            .onEnded { _ in
                anchor = .now
                lastTranslation = 0
                resumeTask?.cancel()
                // A beat before drifting again, so letting go to read something does not
                // immediately pull it away.
                resumeTask = Task { @MainActor in
                    try? await Task.sleep(for: .seconds(1.2))
                    guard !Task.isCancelled else { return }
                    anchor = .now
                    isTouching = false
                }
            }
    }
}

// All tiles are the same width, so `max` is exact rather than a heuristic.
private struct MarqueeTileWidthKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

private struct MarqueeViewportWidthKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

#Preview("Drifting") {
    VStack {
        Spacer()
        MarqueeChipRow(
            chips: [
                "What tickers are hot today?", "Why is NVDA up 6% today?", "What is a moat?",
                "How do I read a balance sheet?", "What topics are hot today?",
            ],
            drifts: true
        ) { _ in }
    }
    .background(AppColors.background)
}

#Preview("Static — too few chips to overflow") {
    VStack {
        Spacer()
        MarqueeChipRow(chips: ["What is a moat?"], drifts: true) { _ in }
    }
    .background(AppColors.background)
}
