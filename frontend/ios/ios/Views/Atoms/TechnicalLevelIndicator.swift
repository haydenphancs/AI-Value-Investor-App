//
//  TechnicalLevelIndicator.swift
//  ios
//
//  Numbered indicator (1-5) for technical analysis gauge levels
//

import SwiftUI

struct TechnicalLevelIndicator: View {
    let level: Int
    let isActive: Bool
    let activeColor: Color
    /// Ink for `activeColor`. Defaults to `textOnAccent` for the frozen fills; the row
    /// below passes `textOnFill` for the levels backed by the ADAPTIVE gain/loss fills.
    /// One ink cannot serve both — see `AppColors.textOnFill`.
    var activeInk: Color = AppColors.textOnAccent

    var body: some View {
        ZStack {
            Circle()
                .fill(isActive ? activeColor : AppColors.cardBackgroundLight)
                .frame(width: 28, height: 28)

            Text("\(level)")
                .font(AppTypography.labelSmallEmphasis)
                // `textOnAccent`, not `textPrimary`: the active circle is a
                // SATURATED FILL, so the digit needs on-accent ink. With
                // `textPrimary` it inverted with the appearance and 4 of the 5
                // active states failed AA in each mode — dark-navy "1" on dark
                // red in light, white "4" on light green in dark.
                .foregroundColor(isActive ? activeInk : AppColors.textMuted)
        }
    }
}

// MARK: - Technical Level Indicators Row
struct TechnicalLevelIndicatorsRow: View {
    let activeLevel: Int // 1-5
    let labels: [String]

    /// `*Fill` tokens: these are saturated circles that carry `textOnAccent`
    /// ink, which is exactly the fill role. The two frozen hexes they replace
    /// (`#991B1B`, `#4ADE80`) did not adapt, so each broke in the opposite mode.
    private let levelColors: [Color] = [
        AppColors.lossFill,      // Strong Sell
        AppColors.lossFill,      // Sell
        AppColors.cautionFill,   // Hold
        AppColors.gainFill,      // Buy
        AppColors.gainFill       // Strong Buy
    ]

    /// Paired ink, index-for-index with `levelColors`. `lossFill`/`gainFill` are ADAPTIVE
    /// and need near-black in dark; `cautionFill` is frozen and needs white.
    private let levelInks: [Color] = [
        AppColors.textOnFill,    // Strong Sell  (lossFill)
        AppColors.textOnFill,    // Sell         (lossFill)
        AppColors.textOnAccent,  // Hold         (cautionFill, frozen)
        AppColors.textOnFill,    // Buy          (gainFill)
        AppColors.textOnFill     // Strong Buy   (gainFill)
    ]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(1...5, id: \.self) { level in
                VStack(spacing: AppSpacing.sm) {
                    // Level indicator
                    TechnicalLevelIndicator(
                        level: level,
                        isActive: level == activeLevel,
                        activeColor: levelColors[level - 1],
                        activeInk: levelInks[level - 1]
                    )
                    
                    // Label with fixed height to keep circles aligned
                    Text(labels[level - 1])
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .multilineTextAlignment(.center)
                        .frame(height: 32) // Fixed height to accommodate 2 lines
                }
                .frame(maxWidth: .infinity)
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            TechnicalLevelIndicatorsRow(
                activeLevel: 4,
                labels: ["Strong\nSell", "Sell", "Hold", "Buy", "Strong\nBuy"]
            )

            HStack(spacing: AppSpacing.md) {
                TechnicalLevelIndicator(level: 1, isActive: false, activeColor: AppColors.bearish)
                TechnicalLevelIndicator(level: 2, isActive: false, activeColor: AppColors.bearish)
                TechnicalLevelIndicator(level: 3, isActive: true, activeColor: AppColors.neutral)
                TechnicalLevelIndicator(level: 4, isActive: false, activeColor: AppColors.bullish)
                TechnicalLevelIndicator(level: 5, isActive: false, activeColor: AppColors.bullish)
            }
        }
        .padding()
    }
}
