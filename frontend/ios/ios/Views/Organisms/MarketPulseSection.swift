//
//  MarketPulseSection.swift
//  ios
//
//  Organism: the "Markets Open" status row + horizontally-scrolling pulse strip
//  of index / crypto / commodity tiles at the top of the Home dashboard.
//

import SwiftUI

struct MarketPulseSection: View {
    let statusText: String
    let isOpen: Bool
    let items: [MarketPulseItem]
    var onTap: ((MarketPulseItem) -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 7) {
                BlinkingDot(color: isOpen ? AppColors.bullish : AppColors.textMuted)
                Text(statusText)
                    .font(AppTypography.labelEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                Spacer()
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.bottom, 10)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(items) { item in
                        MarketPulseCard(item: item) { onTap?(item) }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

/// Small pulsing "live" dot (green when the market is open).
///
/// The repeating animation is gated on `isActiveTab` so it stops when the Home
/// tab is opacity-hidden (tabs are opacity-mounted, so the view never leaves the
/// hierarchy and `onDisappear` never fires) — no CPU/battery drain in the
/// background.
private struct BlinkingDot: View {
    let color: Color
    @Environment(\.isActiveTab) private var isActiveTab
    @State private var dim = false

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 7, height: 7)
            .opacity(dim ? 0.25 : 1)
            .onChange(of: isActiveTab, initial: true) { _, active in
                if active {
                    withAnimation(.easeInOut(duration: 1).repeatForever(autoreverses: true)) {
                        dim = true
                    }
                } else {
                    // Replacing the repeating animation with a finite one cancels the loop.
                    withAnimation(.easeInOut(duration: 0.2)) { dim = false }
                }
            }
    }
}

#Preview {
    MarketPulseSection(
        statusText: "Markets Open",
        isOpen: true,
        items: MockHomeRepository.pulse
    )
    .padding(.vertical)
    .background(AppColors.background)
}
