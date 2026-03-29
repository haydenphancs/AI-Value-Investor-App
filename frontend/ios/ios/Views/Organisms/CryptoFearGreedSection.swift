//
//  CryptoFearGreedSection.swift
//  ios
//
//  Crypto Fear & Greed Index section for the Analysis tab.
//  Replaces Analyst Ratings (which don't exist for crypto).
//

import SwiftUI

struct CryptoFearGreedSection: View {
    let data: CryptoFearGreedData
    @Binding var selectedTimeframe: FearGreedTimeframe

    @State private var showInfoSheet: Bool = false

    private var currentScore: Int {
        data.score(for: selectedTimeframe)
    }

    private var currentLabel: String {
        data.label(for: selectedTimeframe)
    }

    private var currentColor: Color {
        data.color(for: selectedTimeframe)
    }

    private var currentGaugeValue: Double {
        data.gaugeValue(for: selectedTimeframe)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            AnalysisSectionHeader(
                title: "Fear & Greed Index",
                onAction: { showInfoSheet = true },
                iconType: .info
            )

            // Gauge
            HStack {
                Spacer()
                VStack(spacing: AppSpacing.lg) {
                    VStack(spacing: AppSpacing.xs) {
                        Text("Crypto Market Sentiment")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textPrimary)

                        Text("Powered by Alternative.me")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }

                    // Timeframe toggle
                    FearGreedTimeframeToggle(selectedTimeframe: $selectedTimeframe)

                    SemiCircleGauge(
                        value: currentGaugeValue,
                        displayValue: "\(currentScore)",
                        label: currentLabel,
                        labelColor: currentColor,
                        gaugeType: .sentiment,
                        showLabels: true,
                        size: 180
                    )
                    .animation(.easeInOut(duration: 0.5), value: currentGaugeValue)
                }
                Spacer()
            }

            // Disclaimer
            AnalysisDisclaimerText()
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
        .sheet(isPresented: $showInfoSheet) {
            FearGreedInfoSheet()
        }
    }
}

// MARK: - Timeframe Toggle

private struct FearGreedTimeframeToggle: View {
    @Binding var selectedTimeframe: FearGreedTimeframe

    var body: some View {
        AnalysisTimeframeToggle(
            selectedOption: $selectedTimeframe,
            options: FearGreedTimeframe.allCases.map { $0 }
        )
    }
}

// MARK: - Info Sheet

private struct FearGreedInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    Text("About the Fear & Greed Index")
                        .font(AppTypography.titleCompact)
                        .foregroundColor(AppColors.textPrimary)

                    Text("The Crypto Fear & Greed Index measures market sentiment on a scale of 0 to 100.")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)

                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        InfoRow(range: "0 - 25", label: "Extreme Fear", description: "Investors are very worried. Could be a buying opportunity.")
                        InfoRow(range: "25 - 45", label: "Fear", description: "Market participants are fearful.")
                        InfoRow(range: "45 - 55", label: "Neutral", description: "Market sentiment is balanced.")
                        InfoRow(range: "55 - 75", label: "Greed", description: "Market is getting greedy. Caution advised.")
                        InfoRow(range: "75 - 100", label: "Extreme Greed", description: "Market is very greedy. Potential correction ahead.")
                    }

                    Text("Data sources include volatility, market momentum, social media, surveys, Bitcoin dominance, and Google Trends.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

private struct InfoRow: View {
    let range: String
    let label: String
    let description: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(range)
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 60, alignment: .leading)
                Text(label)
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(AppColors.textPrimary)
            }
            Text(description)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.vertical, 4)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        CryptoFearGreedSection(
            data: CryptoFearGreedData(
                value: 40,
                classification: "Fear",
                value7d: 35,
                classification7d: "Fear",
                value30d: 52,
                classification30d: "Neutral",
                history: []
            ),
            selectedTimeframe: .constant(.today)
        )
        .padding()
    }
}
