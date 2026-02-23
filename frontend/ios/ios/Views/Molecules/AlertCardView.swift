//
//  AlertCardView.swift
//  ios
//
//  Molecule: Unified alert card that renders different UI based on AppAlert case
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
                iconView

                // Content
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(alert.title)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)

                    Text(alert.description)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }

                Spacer()

                // Trailing accessory (date badge or chevron)
                trailingView
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(.plain)
    }

    // MARK: - Icon

    @ViewBuilder
    private var iconView: some View {
        ZStack {
            Circle()
                .fill(alert.iconBackgroundColor.opacity(0.15))
                .frame(width: 40, height: 40)

            Image(systemName: alert.systemIconName)
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(alert.iconBackgroundColor)
        }
    }

    // MARK: - Trailing Accessory

    @ViewBuilder
    private var trailingView: some View {
        switch alert {
        case .earnings(let data):
            EventDateBadge(
                day: String(data.day),
                month: data.month.uppercased()
            )
        case .market(let data):
            EventDateBadge(
                day: String(data.day),
                month: data.month.uppercased()
            )
        case .smartMoney:
            Image(systemName: "chevron.right")
                .font(.system(size: 14, weight: .medium))
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
