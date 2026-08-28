//
//  SignalDisclosureRow.swift
//  ios
//
//  Molecule: one expandable row inside the "App-Exclusive Signals" card —
//  icon tile, title/subtitle, a top pick, and a disclosure chevron that reveals
//  the top-leaders list.
//

import SwiftUI

struct SignalDisclosureRow: View {
    let signal: ExclusiveSignal
    // (kind, leader) — kind routes the tap (whale/congress → per-ticker detail,
    // earnings → TickerDetailView).
    var onLeaderTap: ((String, SignalLeader) -> Void)? = nil
    /// Fired when a tap lands on the row BODY (not a child control).
    ///
    /// ⚠️ The SWALLOW is the load-bearing part, not this closure. `.onTapGesture` below
    /// consumes the tap so it cannot bubble to the Home screen's collapse gesture and close
    /// the very row you just touched — that holds whether or not a handler is supplied.
    ///
    /// The closure itself is currently UNWIRED. It used to carry the cross-section collapse
    /// ("this tap is outside the OTHER section, so close it"), which was the machinery
    /// enforcing one-open-at-a-time. Every card can now be expanded at once, so Home passes
    /// nothing. Kept because the hook is the natural place for a future caller to react.
    var onBodyTap: (() -> Void)? = nil
    /// Fired INSTEAD of expanding when the signal is locked (Free/guest). Carries the
    /// kind so the Home screen can attribute the upsell.
    var onLockedTap: ((String) -> Void)? = nil

    /// Lifted to the parent (via `ExclusiveSignalsSection` → the Home screen) so a
    /// tap OUTSIDE the row can collapse it, and only one row expands at a time —
    /// same pattern as `ScannerCard.isExpanded`.
    @Binding var isExpanded: Bool

    /// Scroll position 0...1 and the visible share of the list, driving the custom indicator
    /// below. Read from `onScrollGeometryChange` — the same mechanism the article screen uses
    /// for its reading-progress bar.
    @State private var scrollFraction: CGFloat = 0
    @State private var visibleFraction: CGFloat = 1

    // Past this many leaders, the expanded list scrolls INSIDE a bounded box
    // (mirrors the report's Insider "Recent Transactions") so the user scrolls the
    // list, not the whole Home screen. At or below it, the list renders inline.
    /// Above this many leaders the list scrolls inside a bounded box.
    ///
    /// Tied to `expandedListMaxHeight`: a leader row is ~43pt plus 7pt of spacing, so ~4 fit in
    /// 200pt. Keeping the two in step matters — at the old 6/260 pairing a SIX-item list
    /// rendered unbounded at ~300pt while a SEVEN-item list was capped at 260, i.e. more data
    /// produced a SHORTER box. Dropping the cap to 200 would have widened that to 300 vs 200,
    /// so the threshold moves with it and every list is now either fully shown or capped.
    private static let scrollThreshold = 4
    /// Bounds the scrolling list. 200, not 260: the scroll indicator's length is
    /// viewport² / content, so it was ~137pt — reported as "the scroll bar is long". At 200 it
    /// is ~81pt. This is the only honest lever, since the length is what tells a reader how
    /// much more there is; shortening the bar without shortening the viewport would lie.
    private static let expandedListMaxHeight: CGFloat = 200
    /// Thumb bounds for the custom indicator. The upper bound is what makes it read as short.
    private static let thumbMinHeight: CGFloat = 22
    private static let thumbMaxHeight: CGFloat = 56

    /// Enough to make 2–5 glyphs unreadable at `dataMedium` without smearing so far
    /// that the chip stops reading as "a ticker is here".
    private static let lockBlurRadius: CGFloat = 4.5

    var body: some View {
        VStack(spacing: 0) {
            Button {
                // A locked row never expands: there is nothing behind it to show (the
                // backend withheld the leaders), so the tap that would have opened it
                // opens the paywall instead.
                if signal.isLocked {
                    onLockedTap?(signal.kind)
                } else {
                    // ⚠️ Deliberately NOT wrapped in `withAnimation`.
                    //
                    // Animating this resizes the whole App-Exclusive Signals section every
                    // frame, and the section was reported as "the title and the whole card is
                    // blinking when I expand". I could NOT reproduce that on the simulator —
                    // an A/B of the shadow change measured 1 direction-reversal vs 0 in the
                    // stationary band, i.e. no flicker either way — but offscreen raster cost
                    // is wildly different on a phone than on a Mac GPU, so a device-only
                    // flicker is entirely plausible and unmeasurable here.
                    //
                    // With no animation there is no per-frame re-render at all, so there is
                    // nothing left that CAN blink. This also removes the last `withAnimation`
                    // transaction on this section — the one a `.repeatForever` glow entangled
                    // with and hard-froze the main thread on, per the header comment in
                    // ExclusiveSignalsSection.swift.
                    isExpanded.toggle()
                }
            } label: {
                HStack(spacing: 12) {
                    IconTile(systemName: signal.iconSystemName, accent: signal.accent,
                             size: 40, iconPointSize: 21)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(signal.title)
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                        Text(signal.subtitle)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                            .lineLimit(1)
                    }

                    Spacer(minLength: 6)

                    VStack(alignment: .trailing, spacing: 2) {
                        topSymbolView
                        // Survives the lock on purpose: "3 members buying" names no
                        // ticker, and it is what makes the upgrade worth taking.
                        Text(signal.topStat)
                            .font(AppTypography.captionSmall)
                            .foregroundColor(AppColors.textMuted)
                    }

                    Image(systemName: "chevron.down")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)
                        .rotationEffect(.degrees(isExpanded ? 180 : 0))
                }
                .padding(12)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            // Additive (value/hint, not label) so the unlocked row keeps the default
            // combined announcement and only the locked one gains the explanation. Both
            // are applied unconditionally — a conditional modifier would change the
            // view's identity and drop the expand animation.
            .accessibilityValue(signal.isLocked ? "Ticker locked" : "")
            .accessibilityHint(signal.isLocked ? "Shows upgrade options" : "")

            // `&& !signal.isLocked` is defensive, not decorative: the row could already
            // be expanded when the lock arrives (a session ending mid-scroll, a tier
            // downgrade on refresh), and without it the previously-served leaders would
            // stay on screen behind a lock that has already been applied everywhere else.
            if isExpanded && !signal.isLocked {
                Group {
                    if signal.leaders.count > Self.scrollThreshold {
                        // Long list → scroll inside a bounded box. A nested ScrollView
                        // captures its own vertical drags, so this list scrolls in
                        // place instead of stretching the Home screen (same pattern as
                        // the report's Insider "Recent Transactions").
                        ScrollView {
                            LazyVStack(spacing: 7) {
                                ForEach(signal.leaders) { leaderRow($0) }
                            }
                        }
                        // ⚠️ The SYSTEM indicator is hidden and replaced, because
                        // `.scrollIndicators(.visible)` does NOT mean "always visible".
                        // It means "do not suppress them"; UIKit still fades them out when
                        // scrolling stops, so a reader who has not touched the list sees no
                        // bar and cannot tell there is more below. That was the TestFlight
                        // report, and there is no API to pin the system one.
                        .scrollIndicators(.hidden)
                        .frame(maxHeight: Self.expandedListMaxHeight)
                        .onScrollGeometryChange(for: SignalListMetrics.self) { geo in
                            SignalListMetrics(offset: geo.contentOffset.y,
                                              content: geo.contentSize.height,
                                              viewport: geo.containerSize.height)
                        } action: { _, m in
                            guard m.content > 0, m.viewport > 0 else { return }
                            visibleFraction = min(1, m.viewport / m.content)
                            // `max(..., 1)` guards the divide: content == viewport means
                            // nothing scrolls, and 0/0 would put NaN into a frame height.
                            let scrollable = max(m.content - m.viewport, 1)
                            scrollFraction = min(max(m.offset / scrollable, 0), 1)
                        }
                        .overlay(alignment: .trailing) { scrollIndicator }
                    } else {
                        VStack(spacing: 7) {
                            ForEach(signal.leaders) { leaderRow($0) }
                        }
                    }
                }
                .padding(.leading, 64)
                .padding(.trailing, 12)
                .padding(.bottom, 12)
                .padding(.top, 2)
                // ⚠️ `.opacity` ALONE — do not put `.move(edge: .top)` back.
                // This content is revealed at the BOTTOM of a clipped container, so a top-edge move
                // starts it offset UPWARD by its own height and slides it down THROUGH everything
                // above it, translucent the whole way. On the Daily Scanners card that meant the
                // leaderboard swept across the card's own header, hero and CTA, and was reported from
                // TestFlight as "words coming from the background ... looks like a bug".
                // A fade moves nothing and cannot overlap anything.
                // (Genuinely top-anchored things — the audio status island, a banner pinned to the top
                // of a screen — are the opposite case and keep their `.move(edge: .top)`.)
                .transition(.opacity)
            }
        }
        .background(AppColors.textPrimary.opacity(0.03))
        .overlay(
            RoundedRectangle(cornerRadius: 13, style: .continuous)
                .stroke(AppColors.textPrimary.opacity(0.05), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 13, style: .continuous))
        // Swallow taps that land ON the row (gaps between leader rows, padding)
        // so ONLY a tap OUTSIDE it collapses it — mirrors ScannerCard. The header
        // Button and leader Buttons are children and still win their hit areas;
        // drags still scroll the nested leader list since this is tap-only. The
        // swallowed tap is forwarded via onBodyTap (→ collapses the scanner card).
        .contentShape(Rectangle())
        .onTapGesture { onBodyTap?() }
    }

    /// The headline ticker, or its locked stand-in.
    ///
    /// What is blurred is a bullet MASK the backend substituted, not the real symbol —
    /// the ticker is withheld server-side (`entitlements.signals_unlocked`), so this blur
    /// is presentation, not the gate. That ordering matters twice: the paid data never
    /// reaches a free client to be read out of the response, and if the blur ever failed
    /// to apply the row would expose "••••" rather than the ticker.
    @ViewBuilder
    private var topSymbolView: some View {
        if signal.isLocked {
            Text(signal.topSymbol)
                .font(AppTypography.dataMedium)
                .foregroundColor(AppColors.textPrimary)
                .blur(radius: Self.lockBlurRadius)
                .overlay(
                    Image(systemName: "lock.fill")
                        // A TEXT-role token (the one LockedTickersChip uses): this glyph
                        // has to clear 4.5:1 in both appearances. A *Graphic token would
                        // fail the launch audit.
                        .font(AppTypography.iconXS)
                        .fontWeight(.semibold)
                        .foregroundColor(AppColors.primaryBlue)
                )
                // The blurred mask must not be readable by VoiceOver either — it would
                // announce the bullets, and the whole subtree needs ONE honest label.
                .accessibilityHidden(true)
        } else {
            Text(signal.topSymbol)
                .font(AppTypography.dataMedium)
                .foregroundColor(AppColors.textPrimary)
        }
    }

    // One drill-down leader row — shared by the inline and the bounded-scroll list
    // so both render identically. Tapping routes to the ticker via `onLeaderTap`.
    @ViewBuilder
    /// Always-on scroll indicator for the expanded leader list.
    ///
    /// Length is proportional to how much of the list is visible, but CLAMPED — the system bar
    /// ran ~137pt on this list and was reported as "long". A clamped thumb still says "there is
    /// more" by existing, and still says "where you are" by moving; it just stops reporting the
    /// exact ratio once the list is short. That trade is deliberate.
    private var scrollIndicator: some View {
        GeometryReader { geo in
            let track = geo.size.height
            let thumb = min(max(track * visibleFraction, Self.thumbMinHeight), Self.thumbMaxHeight)
            Capsule()
                .fill(AppColors.textPrimary.opacity(0.28))
                .frame(width: 3, height: thumb)
                .offset(y: (track - thumb) * scrollFraction)
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
        // Decoration only — it must never eat a tap meant for a leader row beneath it.
        .allowsHitTesting(false)
        .accessibilityHidden(true)
        .padding(.trailing, 3)
    }

    private func leaderRow(_ leader: SignalLeader) -> some View {
        Button { onLeaderTap?(signal.kind, leader) } label: {
            HStack {
                VStack(alignment: .leading, spacing: 1) {
                    Text(leader.symbol)
                        .font(AppTypography.labelEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                    if !leader.companyName.isEmpty {
                        Text(leader.companyName)
                            .font(AppTypography.captionSmall)
                            .foregroundColor(AppColors.textMuted)
                            .lineLimit(1)
                    }
                }
                Spacer()
                Text(leader.stat)
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textSecondary)
            }
            .padding(.leading, 11)
            // ⚠️ Trailing is WIDER than leading (22 vs 11), and that asymmetry is the point.
            //
            // When the list scrolls, the scroll indicator is drawn at the row's trailing edge
            // and is ~7pt wide. At an even 11pt inset the "5 buys" stat ended just ~4pt clear
            // of it, which reads as the bar sitting on the numbers — reported from TestFlight.
            // 22 puts ~15pt of air between them.
            //
            // Applied to EVERY leader row, not just the ones inside a ScrollView: several
            // signals can be expanded at once now, so a scrolling list and a non-scrolling one
            // are often on screen together and their stat columns must line up.
            .padding(.trailing, 22)
            .padding(.vertical, 8)
            .background(AppColors.textPrimary.opacity(0.03))
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    SignalDisclosureRowPreviewHost()
}

/// Stateful host so the preview can actually expand/collapse rows
/// (one-open-at-a-time, like the live Home screen).
private struct SignalDisclosureRowPreviewHost: View {
    @State private var expandedID: ExclusiveSignal.ID?
    var body: some View {
        ScrollView {
            VStack(spacing: 9) {
                // Unlocked (Pro/Max) above, locked (Free/guest) below, so the two states
                // are compared side by side — the blurred chip has to keep the row's
                // height and trailing alignment identical to the unlocked one.
                ForEach(MockHomeRepository.signals + MockHomeRepository.lockedSignals) { signal in
                    SignalDisclosureRow(
                        signal: signal,
                        isExpanded: Binding(
                            get: { expandedID == signal.id },
                            set: { expandedID = $0 ? signal.id : nil }
                        )
                    )
                }
            }
            .padding()
        }
        .background(Color(lightHex: "F5F7FC", darkHex: "1B2233"))
    }
}


/// Scroll geometry for the expanded leader list's custom indicator.
private struct SignalListMetrics: Equatable {
    let offset: CGFloat
    let content: CGFloat
    let viewport: CGFloat
}
