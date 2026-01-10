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

    var body: some View {
        ZStack {
            Circle()
                .fill(isActive ? activeColor : AppColors.cardBackgroundLight)
                .frame(width: 28, height: 28)

            Text("\(level)")
                .font(AppTypography.footnoteBold)
                .foregroundColor(isActive ? AppColors.textPrimary : AppColors.textMuted)
        }
    }
}

// MARK: - Technical Level Indicators Row
struct TechnicalLevelIndicatorsRow: View {
    let activeLevel: Int // 1-5
    let labels: [String]

    private let levelColors: [Color] = [
        Color(hex: "991B1B"), // Strong Sell - dark red
        AppColors.bearish,    // Sell - red
        AppColors.neutral,    // Hold - yellow
        Color(hex: "4ADE80"), // Buy - light green
        AppColors.bullish     // Strong Buy - green
    ]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(1...5, id: \.self) { level in
                VStack(spacing: AppSpacing.sm) {
                    // Level indicator
                    TechnicalLevelIndicator(
                        level: level,
                        isActive: level == activeLevel,
                        activeColor: levelColors[level - 1]
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
