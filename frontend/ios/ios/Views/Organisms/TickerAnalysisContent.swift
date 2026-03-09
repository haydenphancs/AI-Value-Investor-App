//
//  TickerAnalysisContent.swift
//  ios
//
//  Organism: Analysis tab content combining all analysis sections for Ticker Detail
//

import SwiftUI

struct TickerAnalysisContent: View {
    let analysisData: TickerAnalysisData
    @Binding var selectedMomentumPeriod: AnalystMomentumPeriod
    @Binding var selectedSentimentTimeframe: SentimentTimeframe
    var onAnalystRatingsMoreTap: (() -> Void)?
    var onAnalystActionsTap: (() -> Void)?
    var onSentimentMoreTap: (() -> Void)?
    var onTechnicalDetailTap: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Analyst Ratings Section
            AnalystRatingsSection(
                ratingsData: analysisData.analystRatings,
                selectedMomentumPeriod: $selectedMomentumPeriod,
                onMoreTapped: {
                    onAnalystRatingsMoreTap?()
                },
                onActionsTapped: {
                    onAnalystActionsTap?()
                }
            )

            // Sentiment Analysis Section
            SentimentAnalysisSection(
                sentimentData: analysisData.sentimentAnalysis,
                selectedTimeframe: $selectedSentimentTimeframe,
                onMoreTapped: {
                    onSentimentMoreTap?()
                }
            )

            // Technical Analysis Section
            TechnicalAnalysisSection(
                technicalData: analysisData.technicalAnalysis,
                onDetailTapped: {
                    onTechnicalDetailTap?()
                }
            )

            // Bottom spacing for AI bar
            Spacer()
                .frame(height: 120)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
    }
}

#Preview {
    ScrollView {
        TickerAnalysisContent(
            analysisData: TickerAnalysisData.sampleData,
            selectedMomentumPeriod: .constant(.sixMonths),
            selectedSentimentTimeframe: .constant(.last24h)
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
