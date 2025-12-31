//
//  AlertEventCard.swift
//  ios
//
//  Molecule: Alert/Event card with icon, description and date
//

import SwiftUI

struct AlertEventCard: View {
    let alert: AlertEvent
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: AppSpacing.md) {
                // Icon
                AlertCategoryIcon(type: alert.type)

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

                // Date Badge (if available)
                if alert.hasDate {
                    EventDateBadge(
                        day: alert.formattedDay,
                        month: alert.formattedMonth
                    )
                }
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ForEach(AlertEvent.sampleData) { alert in
            AlertEventCard(alert: alert)
        }
    }
    .padding()
    .background(AppColors.background)
}
