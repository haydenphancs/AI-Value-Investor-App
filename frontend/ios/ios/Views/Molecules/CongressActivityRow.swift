//
//  CongressActivityRow.swift
//  ios
//
//  Molecule: Row displaying a single congressional trading activity
//  Shows name, role (Senator/Representative), date, trade value, owner, and price
//

import SwiftUI

struct CongressActivityRow: View {
    let activity: CongressActivity

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Left side: Name, Role, Date
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(activity.name)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)

                Text(activity.role)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)

                Text(activity.formattedDate)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            Spacer()

            // Right side: Change value, Owner tag, Price
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(activity.formattedRange)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(activity.changeColor)

                Text(activity.ownerLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(activity.ownerColor)

                if !activity.formattedPrice.isEmpty {
                    Text(activity.formattedPrice)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.sm) {
            CongressActivityRow(
                activity: CongressActivity.sampleData[0]  // Pelosi - Purchase
            )

            CongressActivityRow(
                activity: CongressActivity.sampleData[1]  // Tuberville - Sale
            )

            CongressActivityRow(
                activity: CongressActivity.sampleData[2]  // Mullin - Purchase
            )
        }
        .padding()
    }
}
