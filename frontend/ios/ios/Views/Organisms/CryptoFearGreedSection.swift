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
                        size: 220
                    )
                    .animation(.easeInOut(duration: 0.5), value: currentGaugeValue)
                }
                Spacer()
            }

            // Disclaimer
            AnalysisDisclaimerText()
                .padding(.top, AppSpacing.lg)
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
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xxl) {
                    headerSection
                    scaleSection
                    dataSourcesSection
                    valueInvestingTipsSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Understanding Fear & Greed")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }

    // MARK: - Header

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "gauge.with.dots.needle.33percent")
                    .font(AppTypography.iconXL)
                    .foregroundColor(AppColors.primaryBlue)

                Text("Fear & Greed Index")
                    .font(AppTypography.titleCompact)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text("The Crypto Fear & Greed Index measures overall market sentiment on a scale of 0 to 100. It aggregates multiple data sources to determine whether the market is driven by fear or greed.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Scale Breakdown

    private var scaleSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Sentiment Scale")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                scaleRow(
                    range: "0 - 25",
                    label: "Extreme Fear",
                    color: AppColors.bearish,
                    description: "Investors are very worried. Panic selling may push prices below fair value, creating potential buying opportunities."
                )
                scaleRow(
                    range: "25 - 45",
                    label: "Fear",
                    color: Color(hex: "E57373"),
                    description: "Market participants are cautious. Selling pressure is elevated but not extreme."
                )
                scaleRow(
                    range: "45 - 55",
                    label: "Neutral",
                    color: Color(hex: "6B7280"),
                    description: "Balanced sentiment. No strong directional bias from the market."
                )
                scaleRow(
                    range: "55 - 75",
                    label: "Greed",
                    color: Color(hex: "81C784"),
                    description: "Market optimism is rising. Prices may be getting stretched above fundamental value."
                )
                scaleRow(
                    range: "75 - 100",
                    label: "Extreme Greed",
                    color: AppColors.bullish,
                    description: "Euphoria dominates. Historically, extreme greed often precedes corrections."
                )
            }
        }
    }

    private func scaleRow(range: String, label: String, color: Color, description: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Circle()
                    .fill(color)
                    .frame(width: 12, height: 12)

                Text("\(range) — \(label)")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(color)
            }

            Text(description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Data Sources

    private var dataSourcesSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Data Sources")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                dataSourceRow(
                    icon: "chart.line.uptrend.xyaxis",
                    title: "Volatility & Momentum",
                    description: "Measures unusual price swings and market momentum compared to recent averages."
                )
                dataSourceRow(
                    icon: "bubble.left.and.bubble.right.fill",
                    title: "Social Media & Surveys",
                    description: "Tracks crypto discussion volume and sentiment across social platforms and polls."
                )
                dataSourceRow(
                    icon: "bitcoinsign.circle",
                    title: "Bitcoin Dominance & Trends",
                    description: "Monitors Bitcoin's market share and Google Trends search volume for crypto terms."
                )
            }

            Text("Powered by Alternative.me")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }

    private func dataSourceRow(icon: String, title: String, description: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: icon)
                    .font(AppTypography.iconDefault)
                    .foregroundColor(AppColors.primaryBlue)
                    .frame(width: 24)

                Text(title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
            }

            Text(description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Value Investing Tips

    private var valueInvestingTipsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "lightbulb.fill")
                    .foregroundColor(AppColors.neutral)

                Text("Investing Tips")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
            }

            VStack(spacing: AppSpacing.md) {
                tipCard(
                    icon: "arrow.down.circle",
                    title: "Be Greedy When Others Are Fearful",
                    description: "Extreme fear often signals capitulation. Quality crypto assets may be available at a discount when the market is panicking."
                )
                tipCard(
                    icon: "exclamationmark.triangle",
                    title: "Be Fearful When Others Are Greedy",
                    description: "When euphoria peaks, prices tend to overshoot. Consider taking profits or reducing exposure during extreme greed."
                )
                tipCard(
                    icon: "chart.bar.xaxis",
                    title: "Track the Trend Over Time",
                    description: "A single reading matters less than the direction. A shift from extreme fear toward neutral can signal a recovery beginning."
                )
            }
        }
    }

    private func tipCard(icon: String, title: String, description: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: icon)
                    .font(AppTypography.iconDefault)
                    .foregroundColor(AppColors.bullish)
                    .frame(width: 24)

                Text(title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
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
