//
//  TickerAnalysisContent.swift
//  ios
//
//  Organism: Analysis tab content combining all analysis sections for Ticker Detail
//

import SwiftUI

struct TickerAnalysisContent: View {
    let analystRatingsData: AnalystRatingsData?
    let sentimentAnalysisData: SentimentAnalysisData?
    let technicalAnalysisData: TechnicalAnalysisData?
    var fearGreedData: CryptoFearGreedData? = nil
    let isAnalystLoaded: Bool
    var isFearGreedLoaded: Bool = true
    let isSentimentLoaded: Bool
    let isTechnicalLoaded: Bool
    @Binding var selectedMomentumPeriod: AnalystMomentumPeriod
    @Binding var selectedSentimentTimeframe: SentimentTimeframe
    var selectedFearGreedTimeframe: Binding<FearGreedTimeframe>? = nil
    var onAnalystRatingsMoreTap: (() -> Void)?
    var onAnalystActionsTap: (() -> Void)?
    var onSentimentMoreTap: (() -> Void)?
    var onTechnicalDetailTap: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Fear & Greed Index (crypto) OR Analyst Ratings (stocks)
            if let fgData = fearGreedData,
               let fgTimeframe = selectedFearGreedTimeframe {
                CryptoFearGreedSection(data: fgData, selectedTimeframe: fgTimeframe)
            } else if !isFearGreedLoaded && analystRatingsData == nil {
                analysisSectionPlaceholder(height: 280)
            } else if let ratingsData = analystRatingsData {
                AnalystRatingsSection(
                    ratingsData: ratingsData,
                    selectedMomentumPeriod: $selectedMomentumPeriod,
                    onMoreTapped: {
                        onAnalystRatingsMoreTap?()
                    },
                    onActionsTapped: {
                        onAnalystActionsTap?()
                    }
                )
            } else if !isAnalystLoaded {
                analysisSectionPlaceholder(height: 280)
            }

            // Sentiment Analysis Section
            if let sentimentData = sentimentAnalysisData {
                SentimentAnalysisSection(
                    sentimentData: sentimentData,
                    selectedTimeframe: $selectedSentimentTimeframe,
                    onMoreTapped: {
                        onSentimentMoreTap?()
                    }
                )
            } else if !isSentimentLoaded {
                analysisSectionPlaceholder(height: 200)
            }

            // Technical Analysis Section
            if let technicalData = technicalAnalysisData {
                TechnicalAnalysisSection(
                    technicalData: technicalData,
                    onDetailTapped: {
                        onTechnicalDetailTap?()
                    }
                )
            } else if !isTechnicalLoaded {
                analysisSectionPlaceholder(height: 180)
            }

            // Bottom spacing for AI bar
            Spacer()
                .frame(height: 120)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
    }

    private func analysisSectionPlaceholder(height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 12)
            .fill(AppColors.cardBackground)
            .frame(height: height)
            .shimmer()
    }
}

#Preview {
    ScrollView {
        TickerAnalysisContent(
            analystRatingsData: AnalystRatingsData.sampleData,
            sentimentAnalysisData: nil,
            technicalAnalysisData: TechnicalAnalysisData.sampleData,
            isAnalystLoaded: true,
            isSentimentLoaded: false,
            isTechnicalLoaded: true,
            selectedMomentumPeriod: .constant(.sixMonths),
            selectedSentimentTimeframe: .constant(.last24h)
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
