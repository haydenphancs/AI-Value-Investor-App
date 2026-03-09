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

    private var currentScore: Int {
        sentimentData.score(for: selectedTimeframe)
    }

    private var currentMood: MarketMoodLevel {
        sentimentData.mood(for: selectedTimeframe)
    }

    private var gaugeValue: Double {
        Double(currentScore) / 100.0
    }

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Header
            VStack(spacing: AppSpacing.xs) {
                Text("Market Mood Meter")
                    .font(AppTypography.bodySmallEmphasis)
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
                displayValue: "\(currentScore)",
                label: currentMood.rawValue,
                labelColor: currentMood.color,
                gaugeType: .sentiment,
                showLabels: true,
                size: 180
            )
            .animation(.easeInOut(duration: 0.5), value: gaugeValue)
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
