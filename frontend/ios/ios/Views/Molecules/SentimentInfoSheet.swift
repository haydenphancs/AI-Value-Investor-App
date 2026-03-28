//
//  SentimentInfoSheet.swift
//  ios
//
//  Molecule: Educational sheet explaining sentiment analysis for value investing
//

import SwiftUI

struct SentimentInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xxl) {
                    headerSection
                    moodMeterSection
                    metricsSection
                    valueInvestingTipsSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Understanding Sentiment")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "face.smiling")
                    .font(AppTypography.iconXL)
                    .foregroundColor(AppColors.primaryBlue)

                Text("Sentiment Analysis")
                    .font(AppTypography.titleCompact)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text("Sentiment analysis measures the overall market mood toward a stock by tracking social media discussions and news coverage. It helps gauge whether investors are feeling optimistic or pessimistic.")
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

    // MARK: - Mood Meter Section

    private var moodMeterSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Market Mood Meter")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                moodExplanationRow(
                    range: "0 - 30",
                    label: "Bearish",
                    color: AppColors.bearish,
                    description: "Negative sentiment dominates. Investors are pessimistic about the stock's prospects."
                )

                moodExplanationRow(
                    range: "31 - 70",
                    label: "Neutral",
                    color: Color(hex: "6B7280"),
                    description: "Mixed sentiment. No strong directional bias from the market."
                )

                moodExplanationRow(
                    range: "71 - 100",
                    label: "Bullish",
                    color: AppColors.bullish,
                    description: "Positive sentiment dominates. Investors are optimistic and enthusiasm is high."
                )
            }
        }
    }

    private func moodExplanationRow(range: String, label: String, color: Color, description: String) -> some View {
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

    // MARK: - Metrics Section

    private var metricsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Key Metrics")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                metricExplanationRow(
                    icon: "bubble.left.and.bubble.right.fill",
                    title: "Social Mentions",
                    description: "Tracks how often the stock is mentioned on social platforms like Reddit. A spike in mentions can indicate growing interest or concern."
                )

                metricExplanationRow(
                    icon: "newspaper.fill",
                    title: "News Articles",
                    description: "Counts recent news coverage. More articles often signal significant events like earnings, acquisitions, or analyst updates."
                )
            }
        }
    }

    private func metricExplanationRow(icon: String, title: String, description: String) -> some View {
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

    // MARK: - Value Investing Tips Section

    private var valueInvestingTipsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "lightbulb.fill")
                    .foregroundColor(AppColors.neutral)

                Text("Value Investing Tips")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
            }

            VStack(spacing: AppSpacing.md) {
                tipCard(
                    icon: "arrow.down.circle",
                    title: "Bearish Can Mean Opportunity",
                    description: "When sentiment is very negative, quality stocks may be undervalued. Fear can create buying opportunities for patient investors."
                )

                tipCard(
                    icon: "exclamationmark.triangle",
                    title: "Beware Extreme Bullishness",
                    description: "When everyone is euphoric, prices often overshoot fair value. Be cautious about buying at peak optimism."
                )

                tipCard(
                    icon: "chart.bar.xaxis",
                    title: "Watch the Trend, Not the Score",
                    description: "A sentiment score shifting from bearish to neutral can be more meaningful than a static high score. Momentum in sentiment often precedes price moves."
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
    SentimentInfoSheet()
}
