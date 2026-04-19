//
//  AlertCardView.swift
//  ios
//
//  Molecule: Unified alert card that renders based on AppAlert case
//

import SwiftUI

struct AlertCardView: View {
    let alert: AppAlert
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: AppSpacing.md) {
                // Icon
                ZStack {
                    Circle()
                        .fill(alert.iconColor.opacity(0.15))
                        .frame(width: 40, height: 40)

                    Image(systemName: alert.iconName)
                        .font(AppTypography.iconMedium).fontWeight(.semibold)
                        .foregroundColor(alert.iconColor)
                }

                // Content
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(alert.title)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Text(alert.description)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }

                Spacer()

                // Trailing view varies by alert type
                trailingView
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private var trailingView: some View {
        switch alert {
        case .earnings(let data):
            EventDateBadge(day: data.formattedDay, month: data.formattedMonth)
        case .market(let data):
            EventDateBadge(day: data.formattedDay, month: data.formattedMonth)
        case .whaleTrade(let data):
            whaleTradeTrailing(data)
        case .analystRating(let data):
            analystRatingTrailing(data)
        case .insiderTransaction(let data):
            EventDateBadge(day: data.formattedDay, month: data.formattedMonth)
        }
    }

    private func whaleTradeTrailing(_ data: AppAlert.WhaleTradeAlertData) -> some View {
        VStack(alignment: .trailing, spacing: AppSpacing.xs) {
            Text(data.totalAmount)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(data.action.color)
            Text(data.action.rawValue)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }

    private func analystRatingTrailing(_ data: AppAlert.AnalystRatingAlertData) -> some View {
        VStack(alignment: .trailing, spacing: AppSpacing.xs) {
            Image(systemName: data.action.iconName)
                .font(AppTypography.iconSmall).fontWeight(.semibold)
                .foregroundColor(data.action.color)
            if let pt = data.priceTarget {
                Text("PT $\(Int(pt))")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ForEach(AppAlert.sampleData) { alert in
            AlertCardView(alert: alert)
        }
    }
    .padding()
    .background(AppColors.background)
}
