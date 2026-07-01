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
                    SignalDisclosureRow(signal: signal, onLeaderTap: onLeaderTap)
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
        // Swallow stray taps on the card body (padding / title / row gaps) so they
        // don't bubble to the Home scroll's scanner-collapse .onTapGesture — mirrors
        // ScannerCard. Inner SignalDisclosureRow Buttons still win their own hit area.
        .contentShape(Rectangle())
        .onTapGesture { }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    ScrollView {
        ExclusiveSignalsSection(signals: MockHomeRepository.signals)
            .padding(.vertical)
    }
    .background(AppColors.background)
}
