//
//  NextEarningsDateCard.swift
//  ios
//
//  Molecule: Card displaying the next earnings date with calendar icon
//

import SwiftUI

struct NextEarningsDateCard: View {
    let nextEarningsDate: NextEarningsDate

    var body: some View {
        HStack(spacing: AppSpacing.lg) {
            // Calendar icon
            calendarIcon

            // Date info
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text("Next Earnings Date")
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                HStack(spacing: AppSpacing.xs) {
                    Text(nextEarningsDate.formattedDate)
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.textSecondary)

                    Text("(\(nextEarningsDate.statusText))")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.textMuted)
                }

                Text(nextEarningsDate.timing.rawValue)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.primaryBlue)
            }

            Spacer()
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .stroke(AppColors.cardBackgroundLight.opacity(0.5), lineWidth: 1)
        )
    }

    // MARK: - Calendar Icon
    private var calendarIcon: some View {
        ZStack {
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.primaryBlue.opacity(0.2))
                .frame(width: 48, height: 48)

            Image(systemName: "calendar")
                .font(.system(size: 22, weight: .medium))
                .foregroundColor(AppColors.primaryBlue)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            NextEarningsDateCard(nextEarningsDate: .sample)

            // Confirmed version
            NextEarningsDateCard(
                nextEarningsDate: NextEarningsDate(
                    date: Date(),
                    isConfirmed: true,
                    timing: .beforeMarketOpen
                )
            )
        }
        .padding()
    }
}
