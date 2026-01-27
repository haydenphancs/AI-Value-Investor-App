//
//  JourneyItemRow.swift
//  ios
//
//  Molecule: Single row showing a journey step with completion status
//

import SwiftUI

struct JourneyItemRow: View {
    let item: JourneyItem
    let isLast: Bool
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.md) {
                // Step indicator with optional connector line
                VStack(spacing: 0) {
                    JourneyStepIndicator(
                        stepNumber: item.stepNumber,
                        isCompleted: item.isCompleted,
                        isActive: item.isActive
                    )

                    if !isLast {
                        Rectangle()
                            .fill(item.isCompleted ? AppColors.bullish.opacity(0.3) : AppColors.cardBackgroundLight)
                            .frame(width: 2, height: 20)
                    }
                }

                // Title with strikethrough if completed
                Text(item.title)
                    .font(AppTypography.callout)
                    .foregroundColor(item.isCompleted ? AppColors.textMuted : AppColors.textPrimary)
                    .strikethrough(item.isCompleted, color: AppColors.textMuted)

                Spacer()
            }
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack(spacing: 0) {
        JourneyItemRow(
            item: JourneyItem(title: "It's all about mindset", isCompleted: true, isActive: false, stepNumber: 1),
            isLast: false
        )
        JourneyItemRow(
            item: JourneyItem(title: "What is a Stock?", isCompleted: true, isActive: false, stepNumber: 2),
            isLast: false
        )
        JourneyItemRow(
            item: JourneyItem(title: "Value Investing 101", isCompleted: true, isActive: false, stepNumber: 3),
            isLast: false
        )
        JourneyItemRow(
            item: JourneyItem(title: "Understanding the Market", isCompleted: false, isActive: true, stepNumber: 4),
            isLast: true
        )
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
