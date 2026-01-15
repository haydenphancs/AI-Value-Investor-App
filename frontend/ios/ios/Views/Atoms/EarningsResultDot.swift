//
//  EarningsResultDot.swift
//  ios
//
//  Atom: Colored dot indicator for earnings results (Beat/Miss/Estimate)
//

import SwiftUI

struct EarningsResultDot: View {
    let result: EarningsQuarterResult
    let size: CGFloat

    init(result: EarningsQuarterResult, size: CGFloat = 12) {
        self.result = result
        self.size = size
    }

    var body: some View {
        ZStack {
            // Main dot
            Circle()
                .fill(result.dotColor)
                .frame(width: size, height: size)

            // Dashed border for matched results
            if result.hasDashedBorder {
                Circle()
                    .stroke(
                        AppColors.textPrimary,
                        style: StrokeStyle(lineWidth: 2, dash: [3, 2])
                    )
                    .frame(width: size + 4, height: size + 4)
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.xl) {
            VStack(spacing: AppSpacing.sm) {
                EarningsResultDot(result: .beat)
                Text("Beat")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            VStack(spacing: AppSpacing.sm) {
                EarningsResultDot(result: .missed)
                Text("Missed")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            VStack(spacing: AppSpacing.sm) {
                EarningsResultDot(result: .matched)
                Text("Matched")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            VStack(spacing: AppSpacing.sm) {
                EarningsResultDot(result: .pending)
                Text("Estimate")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
    }
}
