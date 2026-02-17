//
//  PerformanceBadge.swift
//  ios
//
//  Atom: Performance percentage badge with color indication
//

import SwiftUI

struct PerformanceBadge: View {
    let changePercent: Double
    var fontSize: CGFloat = 13
    var showBackground: Bool = false

    private var isPositive: Bool {
        changePercent >= 0
    }

    private var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        if abs(changePercent) >= 1000 {
            let formatter = NumberFormatter()
            formatter.numberStyle = .decimal
            formatter.maximumFractionDigits = 0
            formatter.groupingSeparator = ","
            let formatted = formatter.string(from: NSNumber(value: changePercent)) ?? String(format: "%.0f", changePercent)
            return "\(sign)\(formatted)%"
        }
        return "\(sign)\(String(format: "%.2f", changePercent))%"
    }

    private var color: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    var body: some View {
        Text(formattedChange)
            .font(.system(size: fontSize, weight: .semibold))
            .foregroundColor(color)
            .padding(.horizontal, showBackground ? AppSpacing.sm : 0)
            .padding(.vertical, showBackground ? AppSpacing.xs : 0)
            .background(
                showBackground
                    ? RoundedRectangle(cornerRadius: AppCornerRadius.small)
                        .fill(color.opacity(0.15))
                    : nil
            )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        PerformanceBadge(changePercent: 8.42)
        PerformanceBadge(changePercent: -3.15)
        PerformanceBadge(changePercent: 18.67, showBackground: true)
        PerformanceBadge(changePercent: -5.23, showBackground: true)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
