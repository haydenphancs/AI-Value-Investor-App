//
//  SentimentAnalysisSection.swift
//  ios
//
//  Complete Sentiment Analysis section for the Analysis tab
//

import SwiftUI

struct SentimentAnalysisSection: View {
    let sentimentData: SentimentAnalysisData
    @Binding var selectedTimeframe: SentimentTimeframe
    var onMoreTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            AnalysisSectionHeader(
                title: "Sentiment Analysis",
                onAction: { onMoreTapped?() }
            )

            // Market Mood Meter
            HStack {
                Spacer()
                MarketMoodMeter(
                    sentimentData: sentimentData,
                    selectedTimeframe: $selectedTimeframe
                )
                Spacer()
            }

            // Metrics row
            SentimentMetricsRow(sentimentData: sentimentData)

            // Disclaimer
            AnalysisDisclaimerText()
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        SentimentAnalysisSection(
            sentimentData: SentimentAnalysisData.sampleData,
            selectedTimeframe: .constant(.last24h),
            onMoreTapped: {}
        )
        .padding()
    }
}
