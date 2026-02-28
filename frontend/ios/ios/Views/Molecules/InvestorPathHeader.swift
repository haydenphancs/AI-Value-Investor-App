//
//  InvestorJourneyHeader.swift
//  ios
//
//  Molecule: Header for The Investor Journey screen with title, subtitle, and progress
//

import SwiftUI

struct InvestorJourneyHeader: View {
    let completedLessons: Int
    let totalLessons: Int
    var onBackTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Navigation bar
            HStack {
                Button(action: {
                    onBackTapped?()
                }) {
                    Image(systemName: "chevron.left")
                        .font(AppTypography.iconMedium).fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()
            }

            // Title section
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text("The Investor Journey")
                    .font(AppTypography.titleLarge)
                    .foregroundColor(AppColors.textPrimary)

                Text("From Novice to Master")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Overall progress
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                HStack {
                    Text("Overall Progress")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textMuted)

                    Spacer()

                    Text("\(completedLessons)/\(totalLessons) Lessons Completed")
                        .font(AppTypography.labelSmallEmphasis)
                        .foregroundColor(AppColors.textSecondary)
                }

                OverallProgressIndicator(
                    completed: completedLessons,
                    total: totalLessons,
                    segmentCount: totalLessons
                )
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.sm)
        .padding(.bottom, AppSpacing.lg)
    }
}

#Preview {
    VStack {
        InvestorJourneyHeader(completedLessons: 1, totalLessons: 27)
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
