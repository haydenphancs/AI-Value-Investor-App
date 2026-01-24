//
//  InsiderActivityRow.swift
//  ios
//
//  Molecule: Row displaying a single insider trading activity
//  Shows name, title, date, change value, transaction type, and price
//

import SwiftUI

struct InsiderActivityRow: View {
    let activity: InsiderActivity

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Left side: Name, Title, Date
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(activity.name)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)

                Text(activity.title)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)

                Text(activity.formattedDate)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            Spacer()

            // Right side: Change value, Transaction type, Price
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(activity.formattedChange)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(activity.changeColor)

                Text(activity.transactionType.rawValue)
                    .font(AppTypography.caption)
                    .foregroundColor(activity.transactionType.color)

                Text(activity.formattedPrice)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
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
            InsiderActivityRow(
                activity: InsiderActivity.sampleData[0]  // Tim Cook - Informative Buy
            )

            InsiderActivityRow(
                activity: InsiderActivity.sampleData[2]  // Monica Lozano - Uninformative Buy
            )

            InsiderActivityRow(
                activity: InsiderActivity.sampleData[4]  // Oscar Munoz - Informative Sell
            )

            InsiderActivityRow(
                activity: InsiderActivity.sampleData[3]  // Jeff Williams - Uninformative Sell
            )
        }
        .padding()
    }
}
