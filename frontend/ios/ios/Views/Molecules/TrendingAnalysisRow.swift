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
                        // `iconFillColor`, not `iconBackgroundColor`. The earlier fix here
                        // corrected the INK (from `textPrimary`, which inverted against a fill
                        // that does not) but left the SURFACE as the text-safe token — and the
                        // text-safe values lighten in dark, so white sat at 2.24–2.54:1 across
                        // the three local themes. Both halves have to move together.
                        .fill(analysis.iconFillColor)
                        .frame(width: 44, height: 44)

                    Image(systemName: analysis.systemIconName)
                        .font(AppTypography.iconMedium).fontWeight(.semibold)
                        // Paired with the fill, because this member spans BOTH families:
                        // `gainFill` is adaptive and needs near-black, the frozen fills need
                        // white. A literal token would be wrong for one of them.
                        .foregroundColor(analysis.iconFillInk)
                }

                // Text Content
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(analysis.title)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)

                    Text(analysis.description)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(2)

                    HStack(spacing: AppSpacing.sm) {
                        Text(analysis.formattedCompaniesCount)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        TrendingInterestBadge(interestPercent: analysis.interestPercent)
                    }
                }

                Spacer()

                // Chevron
                Image(systemName: "chevron.right")
                    .font(AppTypography.iconSmall).fontWeight(.medium)
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(AppSpacing.md)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .cardFill()
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
}
