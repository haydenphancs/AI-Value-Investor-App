//
//  InvestorPathHeader.swift
//  ios
//
//  Molecule: Header for The Investor Path screen with title, subtitle, and progress
//

import SwiftUI

struct InvestorPathHeader: View {
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
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()
            }

            // Title section
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text("The Investor Path")
                    .font(AppTypography.largeTitle)
                    .foregroundColor(AppColors.textPrimary)

                Text("From Novice to Master")
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Overall progress
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                HStack {
                    Text("Overall Progress")
                        .font(AppTypography.footnote)
                        .foregroundColor(AppColors.textMuted)

                    Spacer()

                    Text("\(completedLessons)/\(totalLessons) Lessons Completed")
                        .font(AppTypography.footnoteBold)
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
        InvestorPathHeader(completedLessons: 1, totalLessons: 27)
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
