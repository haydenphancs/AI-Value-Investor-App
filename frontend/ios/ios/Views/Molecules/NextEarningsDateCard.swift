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
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                HStack(spacing: AppSpacing.xs) {
                    Text(nextEarningsDate.formattedDate)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)

                    Text("(\(nextEarningsDate.statusText))")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textMuted)
                }

                Text(nextEarningsDate.timing.rawValue)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.primaryBlue)
            }

            Spacer()
        }
        .padding(AppSpacing.lg)
        // Nested in a card: light gets `cardEdge` from `.cardSurface`, dark separates by
        // surface (1.11). The `.overlay` stroke that was here added nothing — its token
        // shares the #252B3B dark arm with this fill, so it composited away.
        .cardSurface(AppColors.cardBackgroundNested, cornerRadius: AppCornerRadius.medium)
    }

    // MARK: - Calendar Icon
    private var calendarIcon: some View {
        ZStack {
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.primaryBlue.opacity(0.2))
                .frame(width: 48, height: 48)

            Image(systemName: "calendar")
                .font(AppTypography.iconXL).fontWeight(.medium)
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
