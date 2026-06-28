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
    var onLeaderTap: ((SignalLeader) -> Void)? = nil

    @Environment(\.isActiveTab) private var isActiveTab
    @State private var glow = false

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
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(accent.opacity(glow ? 0.5 : 0.28), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: accent.opacity(glow ? 0.32 : 0.12), radius: glow ? 22 : 14, x: 0, y: 0)
        .padding(.horizontal, AppSpacing.lg)
        // Gate the perpetual glow on tab visibility — tabs are opacity-mounted, so
        // without this the animation would keep re-rendering shadow/stroke on the
        // hidden Home tab. A finite animation when inactive cancels the loop.
        .onChange(of: isActiveTab, initial: true) { _, active in
            if active {
                withAnimation(.easeInOut(duration: 2.2).repeatForever(autoreverses: true)) {
                    glow = true
                }
            } else {
                withAnimation(.easeInOut(duration: 0.3)) { glow = false }
            }
        }
    }
}

#Preview {
    ScrollView {
        ExclusiveSignalsSection(signals: MockHomeRepository.signals)
            .padding(.vertical)
    }
    .background(AppColors.background)
}
