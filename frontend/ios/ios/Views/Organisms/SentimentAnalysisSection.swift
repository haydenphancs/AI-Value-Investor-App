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

    @State private var showInfoSheet: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            AnalysisSectionHeader(
                title: "Sentiment Analysis",
                onAction: { showInfoSheet = true },
                iconType: .info
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
            SentimentMetricsRow(sentimentData: sentimentData, selectedTimeframe: selectedTimeframe)
                .padding(.top, AppSpacing.md)

            // Disclaimer
            AnalysisDisclaimerText()
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
        .sheet(isPresented: $showInfoSheet) {
            SentimentInfoSheet()
        }
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
