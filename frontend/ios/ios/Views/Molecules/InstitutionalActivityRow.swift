//
//  InstitutionalActivityRow.swift
//  ios
//
//  Molecule: Row displaying a single institutional trading activity
//  Shows institution name, category, date, change value/percent, and total held
//

import SwiftUI

struct InstitutionalActivityRow: View {
    let activity: InstitutionalActivity

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Left side: Name, Category, Date
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(activity.institutionName)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)

                Text(activity.category)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)

                Text(activity.formattedDate)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            Spacer()

            // Right side: Change value, Change percent, Total held
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(activity.formattedChange)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(activity.changeColor)

                Text(activity.formattedChangePercent)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                Text(activity.formattedTotalHeld)
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
            InstitutionalActivityRow(
                activity: InstitutionalActivity.sampleData[0]
            )

            InstitutionalActivityRow(
                activity: InstitutionalActivity.sampleData[2]
            )
        }
        .padding()
    }
}
