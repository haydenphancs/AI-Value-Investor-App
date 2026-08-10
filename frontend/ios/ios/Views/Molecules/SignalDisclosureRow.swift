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
    /// Fired when a tap lands on the row BODY (gaps between leader rows, padding).
    /// The row swallows it so it can't bubble up and collapse THIS row — but the
    /// Home screen still collapses the expanded Daily Scanner card with it.
    var onBodyTap: (() -> Void)? = nil
    /// Fired INSTEAD of expanding when the signal is locked (Free/guest). Carries the
    /// kind so the Home screen can attribute the upsell.
    var onLockedTap: ((String) -> Void)? = nil

    /// Lifted to the parent (via `ExclusiveSignalsSection` → the Home screen) so a
    /// tap OUTSIDE the row can collapse it, and only one row expands at a time —
    /// same pattern as `ScannerCard.isExpanded`.
    @Binding var isExpanded: Bool

    // Past this many leaders, the expanded list scrolls INSIDE a bounded box
    // (mirrors the report's Insider "Recent Transactions") so the user scrolls the
    // list, not the whole Home screen. At or below it, the list renders inline.
    private static let scrollThreshold = 6
    private static let expandedListMaxHeight: CGFloat = 260

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
                    withAnimation(.easeInOut(duration: 0.25)) { isExpanded.toggle() }
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
                        .scrollIndicators(.visible)
                        .frame(maxHeight: Self.expandedListMaxHeight)
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
                .transition(.opacity.combined(with: .move(edge: .top)))
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
            .padding(.horizontal, 11)
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
