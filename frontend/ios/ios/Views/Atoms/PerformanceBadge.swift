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

    /// Signed zero collapsed to +0 — `-0.0 >= 0` is `true` so the "+" branch is taken,
    /// while `String(format:)` keeps the sign bit and yields "-0.00", producing the
    /// literal "+-0.00%". Same normalisation `PriceChangeLabel` already documents.
    private var normalized: Double {
        changePercent == 0 ? 0 : changePercent
    }

    /// Non-finite degrades to an em dash rather than printing "nan%" / "inf%" — these
    /// percentages come from backend ratio math that divides by a prior-period base.
    private var isFinite: Bool { changePercent.isFinite }

    private var isPositive: Bool {
        normalized >= 0
    }

    private var formattedChange: String {
        guard isFinite else { return "—" }
        let sign = isPositive ? "+" : ""
        if abs(normalized) >= 1000 {
            let formatter = NumberFormatter()
            formatter.numberStyle = .decimal
            formatter.maximumFractionDigits = 0
            formatter.groupingSeparator = ","
            let formatted = formatter.string(from: NSNumber(value: normalized)) ?? String(format: "%.0f", normalized)
            return "\(sign)\(formatted)%"
        }
        return "\(sign)\(String(format: "%.2f", normalized))%"
    }

    private var color: Color {
        guard isFinite else { return AppColors.textSecondary }
        return isPositive ? AppColors.gain : AppColors.loss
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
        PerformanceBadge(changePercent: 12500)     // grouped: "+12,500%"
        // Degradation cases — expect "+0.00%" then two em dashes, neutral not red.
        PerformanceBadge(changePercent: -0.0)
        PerformanceBadge(changePercent: .nan, showBackground: true)
        PerformanceBadge(changePercent: .infinity)
    }
    .padding()
    .background(AppColors.background)
}
