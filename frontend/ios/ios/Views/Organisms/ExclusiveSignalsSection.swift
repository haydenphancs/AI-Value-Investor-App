//
//  ExclusiveSignalsSection.swift
//  ios
//
//  Organism: the glowing "App-Exclusive Signals" card with the CAYDEX badge and
//  a stack of expandable signal rows.
//

import SwiftUI

struct ExclusiveSignalsSection: View {
    let signals: [ExclusiveSignal]
    var accent: Color = AppColors.primaryBlue
    var onLeaderTap: ((String, SignalLeader) -> Void)? = nil
    /// Fired when a tap lands on the panel or a row BODY (swallowed here so it
    /// can't bubble to the Home collapse gesture) — the Home screen collapses the
    /// expanded Daily Scanner card with it, since that tap is outside the carousel.
    var onBodyTap: (() -> Void)? = nil
    /// Which signal row is expanded (nil = none). Lifted to the Home screen so a
    /// tap outside the row collapses it; also enforces one-open-at-a-time.
    /// Same pattern as `DailyScannersSection.expandedCardID`.
    @Binding var expandedSignalID: ExclusiveSignal.ID?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text("App-Exclusive Signals")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                TintedTagBadge(text: "CAYDEX", color: accent,
                               systemImage: "sparkles",
                               backgroundOpacity: 0.14,
                               font: AppTypography.captionSmallEmphasis,
                               tracking: 0.4)
            }

            Text("Signals you won't find on free trackers.")
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textSecondary)
                .padding(.top, 3)
                .padding(.bottom, 14)

            VStack(spacing: 9) {
                ForEach(signals) { signal in
                    SignalDisclosureRow(
                        signal: signal,
                        onLeaderTap: onLeaderTap,
                        onBodyTap: onBodyTap,
                        isExpanded: Binding(
                            get: { expandedSignalID == signal.id },
                            set: { expandedSignalID = $0 ? signal.id : nil }
                        )
                    )
                }
            }
        }
        .padding(16)
        .background(
            LinearGradient(
                colors: [Color(hex: "1B2233"), Color(hex: "161B29")],
                startPoint: .top, endPoint: .bottom
            )
        )
        // STATIC glow — deliberately NOT animated. A perpetual .repeatForever glow
        // (previously driving stroke/shadow) entangled with a row's expand
        // `withAnimation` transaction: the two animations compounded on the resizing
        // card and hard-froze the main thread on tap-to-expand. A fixed stroke +
        // fixed-radius shadow gives the same premium glow with ZERO animation to
        // conflict with the expand. (If a breathing glow is wanted back, drive it on
        // a sibling overlay that is NOT an ancestor of the expandable rows.)
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(accent.opacity(0.38), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: accent.opacity(0.22), radius: 18, x: 0, y: 0)
        // Taps on the card body (padding / title / row gaps) count as "outside the
        // expanded row" → collapse it. Still swallows the tap so it doesn't bubble
        // to the Home scroll's collapse .onTapGesture — but forwards it via
        // onBodyTap so an expanded Daily Scanner card collapses too. Taps ON a row
        // are swallowed inside SignalDisclosureRow; its buttons win their hit area.
        .contentShape(Rectangle())
        .onTapGesture {
            if expandedSignalID != nil {
                withAnimation(.easeInOut(duration: 0.25)) { expandedSignalID = nil }
            }
            onBodyTap?()
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    ExclusiveSignalsSectionPreviewHost()
}

/// Stateful host so the preview can actually expand/collapse a row.
private struct ExclusiveSignalsSectionPreviewHost: View {
    @State private var expandedID: ExclusiveSignal.ID?
    var body: some View {
        ScrollView {
            ExclusiveSignalsSection(
                signals: MockHomeRepository.signals,
                expandedSignalID: $expandedID
            )
            .padding(.vertical)
        }
        .background(AppColors.background)
    }
}
