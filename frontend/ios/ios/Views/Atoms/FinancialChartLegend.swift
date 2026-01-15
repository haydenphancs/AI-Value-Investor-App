//
//  FinancialChartLegend.swift
//  ios
//
//  Atom: Legend items for financial charts
//

import SwiftUI

struct FinancialChartLegendItem: View {
    let color: Color
    let label: String
    var style: LegendStyle = .dot

    enum LegendStyle {
        case dot       // Circle dot
        case line      // Line indicator
        case square    // Square indicator
    }

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            switch style {
            case .dot:
                Circle()
                    .fill(color)
                    .frame(width: 8, height: 8)
            case .line:
                RoundedRectangle(cornerRadius: 1)
                    .fill(color)
                    .frame(width: 12, height: 3)
            case .square:
                RoundedRectangle(cornerRadius: 2)
                    .fill(color)
                    .frame(width: 8, height: 8)
            }

            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
    }
}

struct FinancialChartLegend: View {
    let items: [(color: Color, label: String)]
    var style: FinancialChartLegendItem.LegendStyle = .dot
    var spacing: CGFloat = AppSpacing.lg

    var body: some View {
        HStack(spacing: spacing) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                FinancialChartLegendItem(
                    color: item.color,
                    label: item.label,
                    style: style
                )
            }
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: 24) {
            // Dot style
            FinancialChartLegend(
                items: [
                    (AppColors.bullish, "Beat"),
                    (AppColors.bearish, "Missed"),
                    (AppColors.neutral, "Estimate")
                ],
                style: .dot
            )

            // Line style
            FinancialChartLegend(
                items: [
                    (Color(hex: "3B82F6"), "Gross Margin"),
                    (Color(hex: "22C55E"), "Operating Margin"),
                    (Color(hex: "EF4444"), "Net Margin")
                ],
                style: .line
            )

            // Square style
            FinancialChartLegend(
                items: [
                    (Color(hex: "3B82F6"), "iPhone"),
                    (Color(hex: "22C55E"), "Services"),
                    (Color(hex: "F59E0B"), "Mac")
                ],
                style: .square
            )
        }
    }
}
