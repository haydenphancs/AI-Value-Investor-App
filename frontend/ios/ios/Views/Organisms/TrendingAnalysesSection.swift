//
//  TrendingAnalysesSection.swift
//  ios
//
//  Organism: Trending analyses list with explore action
//

import SwiftUI

struct TrendingAnalysesSection: View {
    let analyses: [TrendingAnalysis]
    var onExploreTapped: (() -> Void)?
    var onAnalysisTapped: ((TrendingAnalysis) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header
            HStack {
                Text("Trending Analyses")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button(action: {
                    onExploreTapped?()
                }) {
                    Text("Explore")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }

            // Trending list
            VStack(spacing: AppSpacing.sm) {
                ForEach(analyses) { analysis in
                    TrendingAnalysisRow(analysis: analysis) {
                        onAnalysisTapped?(analysis)
                    }
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    ScrollView {
        TrendingAnalysesSection(analyses: TrendingAnalysis.mockTrending)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
