//
//  AnalystActionCard.swift
//  ios
//
//  Card displaying individual analyst upgrade/downgrade action
//

import SwiftUI

struct AnalystActionCard: View {
    let action: AnalystAction

    var body: some View {
        HStack(spacing: 0) {
            // Left border indicator
            Rectangle()
                .fill(action.actionType.borderColor)
                .frame(width: 3)

            // Content
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Header: Firm name, Badge, Date
                HStack(alignment: .top) {
                    Text(action.firmName)
                        .font(AppTypography.headline)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    AnalystActionTypeBadge(actionType: action.actionType)

                    Text(action.formattedDate)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                // Rating change row
                if let previousRating = action.previousRating {
                    HStack(spacing: AppSpacing.sm) {
                        Text(previousRating.rawValue)
                            .font(AppTypography.subheadline)
                            .foregroundColor(AppColors.textSecondary)

                        Image(systemName: "arrow.right")
                            .font(.system(size: 10))
                            .foregroundColor(AppColors.textMuted)

                        Text(action.newRating.rawValue)
                            .font(AppTypography.subheadline)
                            .foregroundColor(action.newRating.color)
                    }
                } else {
                    // For initiated actions, just show new rating
                    Text(action.newRating.rawValue)
                        .font(AppTypography.subheadline)
                        .foregroundColor(action.newRating.color)
                }

                // Price target change row
                if let previousPrice = action.formattedPreviousPrice,
                   let newPrice = action.formattedNewPrice {
                    HStack(spacing: AppSpacing.sm) {
                        Text(previousPrice)
                            .font(AppTypography.subheadline)
                            .foregroundColor(AppColors.textSecondary)

                        Image(systemName: "arrow.right")
                            .font(.system(size: 10))
                            .foregroundColor(AppColors.textMuted)

                        Text(newPrice)
                            .font(AppTypography.subheadline)
                            .foregroundColor(action.priceChangeColor)
                    }
                } else if let newPrice = action.formattedNewPrice {
                    // For initiated/reiterated without previous price
                    Text(newPrice)
                        .font(AppTypography.subheadline)
                        .foregroundColor(AppColors.textSecondary)
                }
            }
            .padding(.leading, AppSpacing.lg)
            .padding(.trailing, AppSpacing.lg)
            .padding(.vertical, AppSpacing.lg)
        }
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            VStack(spacing: AppSpacing.md) {
                ForEach(AnalystAction.sampleData) { action in
                    AnalystActionCard(action: action)
                }
            }
            .padding()
        }
    }
}
