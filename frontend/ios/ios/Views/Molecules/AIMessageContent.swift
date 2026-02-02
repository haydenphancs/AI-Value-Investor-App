//
//  AIMessageContent.swift
//  ios
//
//  Molecule: AI message content that renders different rich content types
//

import SwiftUI

struct AIMessageContent: View {
    let content: [RichContentType]
    let timestamp: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            ForEach(Array(content.enumerated()), id: \.offset) { _, item in
                renderContent(item)
            }

            // Timestamp
            MessageTimestamp(time: timestamp, alignment: .leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func renderContent(_ content: RichContentType) -> some View {
        switch content {
        case .text(let text):
            Text(text)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)

        case .sentimentAnalysis(let analysis):
            SentimentAnalysisCard(analysis: analysis)

        case .stockPerformance(let performance):
            StockPerformanceCard(performance: performance)

        case .riskFactors(let data):
            RiskFactorsCard(data: data)

        case .tip(let tipData):
            TipCard(tip: tipData)

        case .bulletPoints(let points):
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                ForEach(points) { point in
                    BulletPointRow(bulletPoint: point)
                }
            }
        }
    }
}

#Preview {
    ScrollView {
        AIMessageContent(
            content: [
                .text("Based on the latest market data and social sentiment analysis, here's what I found about Tesla (TSLA):"),
                .sentimentAnalysis(SentimentAnalysis(
                    overallSentiment: .bullish,
                    percentage: 68,
                    bulletPoints: [
                        ChatBulletPoint(text: "Strong delivery numbers exceeded expectations in Q4", indicatorType: .success),
                        ChatBulletPoint(text: "Competition intensifying in EV market", indicatorType: .warning)
                    ],
                    dataUpdatedText: "Data updated 5 minutes ago"
                ))
            ],
            timestamp: "2:37 PM"
        )
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
