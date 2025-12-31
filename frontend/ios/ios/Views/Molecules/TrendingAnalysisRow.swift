//
//  TrendingAnalysisRow.swift
//  ios
//
//  Molecule: Trending analysis item with icon, title, and stats
//

import SwiftUI

struct TrendingAnalysisRow: View {
    let analysis: TrendingAnalysis
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.md) {
                // Category Icon
                ZStack {
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .fill(analysis.iconBackgroundColor)
                        .frame(width: 44, height: 44)

                    Image(systemName: analysis.systemIconName)
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                }

                // Text Content
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(analysis.title)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)

                    Text(analysis.formattedCompaniesCount)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)

                    TrendingInterestBadge(interestPercent: analysis.interestPercent)
                }

                Spacer()

                // Chevron
                Image(systemName: "chevron.right")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(AppSpacing.md)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack(spacing: AppSpacing.sm) {
        ForEach(TrendingAnalysis.mockTrending) { analysis in
            TrendingAnalysisRow(analysis: analysis)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
