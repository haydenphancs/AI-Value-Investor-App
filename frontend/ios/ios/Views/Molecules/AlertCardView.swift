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
        // Renders through the shared `ActivityRow` so the digest, the notifications and
        // the price rules are one visual grammar. This view now supplies only what is
        // SPECIFIC to a digest item: its glyph, its tint and its trailing detail.
        ActivityRow(
            systemName: alert.iconName,
            iconColor: alert.iconColor,
            title: alert.title,
            subtitle: alert.description,
            onTap: onTap,
            trailing: { trailingView }
        )
    }

    @ViewBuilder
    private var trailingView: some View {
        switch alert {
        case .earnings(let data):
            EventDateBadge(day: data.formattedDay, month: data.formattedMonth)
        case .market(let data):
            EventDateBadge(day: data.formattedDay, month: data.formattedMonth)
        case .whaleTrade(let data):
            amountTrailing(amount: data.totalAmount, action: data.action)
        case .analystRating(let data):
            analystRatingTrailing(data)
        case .insiderTransaction(let data):
            amountTrailing(amount: data.totalAmount, action: data.action)
        }
    }

    private func amountTrailing(amount: String, action: WhaleAction) -> some View {
        VStack(alignment: .trailing, spacing: AppSpacing.xs) {
            Text(amount)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(action.color)
            Text(action.rawValue)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }

    private func analystRatingTrailing(_ data: AppAlert.AnalystRatingAlertData) -> some View {
        VStack(alignment: .trailing, spacing: AppSpacing.xs) {
            Text("\(data.items.count)")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
            Text("CHANGES")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
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
