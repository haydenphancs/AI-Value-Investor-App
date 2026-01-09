//
//  MarketMoodMeter.swift
//  ios
//
//  Market Mood Meter with gauge and timeframe toggle
//

import SwiftUI

struct MarketMoodMeter: View {
    let sentimentData: SentimentAnalysisData
    @Binding var selectedTimeframe: SentimentTimeframe

    private var currentMood: MarketMoodLevel {
        selectedTimeframe == .last24h ? sentimentData.last24hMood : sentimentData.last7dMood
    }

    private var gaugeValue: Double {
        Double(sentimentData.moodScore) / 100.0
    }

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Header
            VStack(spacing: AppSpacing.xs) {
                Text("Market Mood Meter")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Text("Real-time sentiment tracking")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            // Timeframe toggle
            SentimentTimeframeToggleView(selectedTimeframe: $selectedTimeframe)

            // Gauge
            SemiCircleGauge(
                value: gaugeValue,
                displayValue: "\(sentimentData.moodScore)",
                label: currentMood.rawValue,
                labelColor: currentMood.color,
                showLabels: true,
                size: 180
            )
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        MarketMoodMeter(
            sentimentData: SentimentAnalysisData.sampleData,
            selectedTimeframe: .constant(.last24h)
        )
        .padding()
    }
}
