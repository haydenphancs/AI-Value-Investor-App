//
//  EarningsLegendItem.swift
//  ios
//
//  Atom: Single legend item with colored circle and label
//

import SwiftUI

enum EarningsLegendType {
    case surprised
    case estimate
    case beat
    case missed

    var color: Color {
        switch self {
        case .surprised:
            return AppColors.neutral  // Yellow/Orange
        case .estimate:
            return AppColors.textSecondary  // Gray
        case .beat:
            return AppColors.bullish  // Green
        case .missed:
            return AppColors.bearish  // Red
        }
    }

    var label: String {
        switch self {
        case .surprised:
            return "Surprised"
        case .estimate:
            return "Estimate"
        case .beat:
            return "Beat"
        case .missed:
            return "Missed"
        }
    }
}

struct EarningsLegendItem: View {
    let type: EarningsLegendType

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Circle()
                .fill(type.color)
                .frame(width: 10, height: 10)

            Text(type.label)
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textSecondary)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.xl) {
            EarningsLegendItem(type: .surprised)
            EarningsLegendItem(type: .estimate)
            EarningsLegendItem(type: .beat)
            EarningsLegendItem(type: .missed)
        }
    }
}
