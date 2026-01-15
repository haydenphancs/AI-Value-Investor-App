//
//  FinancialGrowthBadge.swift
//  ios
//
//  Atom: Badge displaying growth percentage with color indication
//

import SwiftUI

struct FinancialGrowthBadge: View {
    let value: Double
    var showSign: Bool = true
    var style: BadgeStyle = .standard

    enum BadgeStyle {
        case standard  // Normal size
        case compact   // Smaller, for inline use
        case large     // Larger, for emphasis
    }

    var body: some View {
        Text(formattedValue)
            .font(fontSize)
            .fontWeight(.semibold)
            .foregroundColor(valueColor)
            .padding(.horizontal, horizontalPadding)
            .padding(.vertical, verticalPadding)
            .background(
                RoundedRectangle(cornerRadius: cornerRadius)
                    .fill(backgroundColor)
            )
    }

    private var formattedValue: String {
        let sign = showSign && value >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", value))%"
    }

    private var valueColor: Color {
        if value > 0 {
            return AppColors.bullish
        } else if value < 0 {
            return AppColors.bearish
        } else {
            return AppColors.textSecondary
        }
    }

    private var backgroundColor: Color {
        if value > 0 {
            return AppColors.bullish.opacity(0.15)
        } else if value < 0 {
            return AppColors.bearish.opacity(0.15)
        } else {
            return AppColors.cardBackgroundLight
        }
    }

    private var fontSize: Font {
        switch style {
        case .standard: return AppTypography.caption
        case .compact: return AppTypography.caption
        case .large: return AppTypography.subheadline
        }
    }

    private var horizontalPadding: CGFloat {
        switch style {
        case .standard: return AppSpacing.sm
        case .compact: return AppSpacing.xs
        case .large: return AppSpacing.md
        }
    }

    private var verticalPadding: CGFloat {
        switch style {
        case .standard: return AppSpacing.xs
        case .compact: return 2
        case .large: return AppSpacing.sm
        }
    }

    private var cornerRadius: CGFloat {
        switch style {
        case .standard: return AppCornerRadius.small
        case .compact: return 4
        case .large: return AppCornerRadius.medium
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: 16) {
            HStack(spacing: 12) {
                FinancialGrowthBadge(value: 19.62, style: .standard)
                FinancialGrowthBadge(value: -8.32, style: .standard)
                FinancialGrowthBadge(value: 0, style: .standard)
            }

            HStack(spacing: 12) {
                FinancialGrowthBadge(value: 19.62, style: .compact)
                FinancialGrowthBadge(value: -8.32, style: .compact)
            }

            HStack(spacing: 12) {
                FinancialGrowthBadge(value: 19.62, style: .large)
                FinancialGrowthBadge(value: -8.32, style: .large)
            }
        }
    }
}
