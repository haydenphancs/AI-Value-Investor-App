//
//  SentimentAnalysisCard.swift
//  ios
//
//  Molecule: Sentiment analysis card with overall score and bullet points
//

import SwiftUI

struct SentimentAnalysisCard: View {
    let analysis: SentimentAnalysis

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header row
            HStack {
                Text("OVERALL SENTIMENT")
                    .font(AppTypography.captionBold)
                    .foregroundColor(AppColors.textSecondary)

                Spacer()

                SentimentPercentageBadge(
                    sentiment: analysis.overallSentiment,
                    percentage: analysis.percentage
                )
            }

            // Bullet points
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                ForEach(analysis.bulletPoints) { bulletPoint in
                    BulletPointRow(bulletPoint: bulletPoint)
                }
            }

            // Data updated text
            Text(analysis.dataUpdatedText)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    SentimentAnalysisCard(
        analysis: SentimentAnalysis(
            overallSentiment: .bullish,
            percentage: 68,
            bulletPoints: [
                ChatBulletPoint(text: "Strong delivery numbers exceeded expectations in Q4", indicatorType: .success),
                ChatBulletPoint(text: "Cybertruck production ramping up successfully", indicatorType: .success),
                ChatBulletPoint(text: "Competition intensifying in EV market", indicatorType: .warning),
                ChatBulletPoint(text: "Analyst price targets range from $180-$350", indicatorType: .info)
            ],
            dataUpdatedText: "Data updated 5 minutes ago"
        )
    )
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
