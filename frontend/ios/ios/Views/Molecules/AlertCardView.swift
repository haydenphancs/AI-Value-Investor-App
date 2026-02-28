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

                // Trailing: date badge for earnings/market, chevron for smartMoney
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
            EventDateBadge(
                day: data.formattedDay,
                month: data.formattedMonth
            )
        case .market(let data):
            EventDateBadge(
                day: data.formattedDay,
                month: data.formattedMonth
            )
        case .smartMoney:
            Image(systemName: "chevron.right")
                .font(AppTypography.iconSmall).fontWeight(.medium)
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
