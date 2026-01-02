//
//  JourneyStepIndicator.swift
//  ios
//
//  Atom: Step indicator for journey progress (checkmark, number, or active state)
//

import SwiftUI

struct JourneyStepIndicator: View {
    let stepNumber: Int
    let isCompleted: Bool
    let isActive: Bool

    private var backgroundColor: Color {
        if isCompleted {
            return AppColors.bullish
        } else if isActive {
            return AppColors.primaryBlue
        }
        return AppColors.cardBackgroundLight
    }

    private var foregroundColor: Color {
        if isCompleted || isActive {
            return .white
        }
        return AppColors.textMuted
    }

    var body: some View {
        ZStack {
            Circle()
                .fill(backgroundColor)
                .frame(width: 24, height: 24)

            if isCompleted {
                Image(systemName: "checkmark")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundColor(foregroundColor)
            } else {
                Text("\(stepNumber)")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(foregroundColor)
            }
        }
    }
}

#Preview {
    HStack(spacing: AppSpacing.lg) {
        JourneyStepIndicator(stepNumber: 1, isCompleted: true, isActive: false)
        JourneyStepIndicator(stepNumber: 2, isCompleted: true, isActive: false)
        JourneyStepIndicator(stepNumber: 3, isCompleted: true, isActive: false)
        JourneyStepIndicator(stepNumber: 4, isCompleted: false, isActive: true)
        JourneyStepIndicator(stepNumber: 5, isCompleted: false, isActive: false)
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
