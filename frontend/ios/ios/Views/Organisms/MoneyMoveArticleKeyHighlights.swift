//
//  MoneyMoveArticleKeyHighlights.swift
//  ios
//
//  Organism: Key highlights section with highlight cards
//

import SwiftUI

struct MoneyMoveArticleKeyHighlights: View {
    let highlights: [ArticleHighlight]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section header
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "sparkles")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(AppColors.neutral)

                Text("Key Highlights")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Highlight cards
            VStack(spacing: AppSpacing.md) {
                ForEach(highlights) { highlight in
                    ArticleHighlightCard(highlight: highlight)
                }
            }
        }
    }
}

#Preview {
    ScrollView {
        MoneyMoveArticleKeyHighlights(
            highlights: [
                ArticleHighlight(
                    icon: "building.columns.fill",
                    title: "The Alpha",
                    description: "As technology becomes ubiquitous, decentralized finance (DeFi) is reshaping how we invest."
                ),
                ArticleHighlight(
                    icon: "chart.line.uptrend.xyaxis",
                    title: "Key Trends",
                    description: "The pace of banking innovation has never been faster."
                ),
                ArticleHighlight(
                    icon: "shield.checkered",
                    title: "Risk Factors",
                    description: "Despite strong prospects, regulatory challenges and market volatility remain key concerns."
                )
            ]
        )
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
