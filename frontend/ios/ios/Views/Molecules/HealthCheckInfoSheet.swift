//
//  HealthCheckInfoSheet.swift
//  ios
//
//  Molecule: Educational sheet explaining Health Check metrics for value investors
//

import SwiftUI

struct HealthCheckInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xxl) {
                    // Header Section
                    headerSection

                    // What is Health Check Section
                    whatIsHealthCheckSection

                    // Metrics Explained
                    metricsExplainedSection

                    // How Value Investors Use It
                    valueInvestingSection

                    // Rating System Explained
                    ratingSystemSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Health Check Guide")
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
                Image(systemName: "heart.text.square.fill")
                    .font(.system(size: 24))
                    .foregroundColor(AppColors.primaryBlue)

                Text("Financial Health Check")
                    .font(AppTypography.title2)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text("A quick assessment of a company's financial strength across four key dimensions that matter most to value investors.")
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

    // MARK: - What is Health Check Section

    private var whatIsHealthCheckSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("What Does Health Check Tell You?")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                infoCard(
                    icon: "shield.checkered",
                    iconColor: AppColors.bullish,
                    title: "Financial Stability",
                    description: "Evaluates if a company can meet its obligations and weather economic downturns without risking bankruptcy."
                )

                infoCard(
                    icon: "chart.line.uptrend.xyaxis",
                    iconColor: AppColors.primaryBlue,
                    title: "Valuation Context",
                    description: "Shows whether the stock is cheap or expensive relative to its sector peers - crucial for finding undervalued opportunities."
                )

                infoCard(
                    icon: "dollarsign.circle.fill",
                    iconColor: AppColors.neutral,
                    title: "Capital Efficiency",
                    description: "Measures how effectively management uses shareholder capital to generate profits."
                )
            }
        }
    }

    private func infoCard(icon: String, iconColor: Color, title: String, description: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Image(systemName: icon)
                .font(.system(size: 20))
                .foregroundColor(iconColor)
                .frame(width: 28)

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(description)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Metrics Explained Section

    private var metricsExplainedSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("The Four Key Metrics")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                ForEach(HealthCheckMetricType.allCases) { metric in
                    metricExplanationRow(metric: metric)
                }
            }
        }
    }

    private func metricExplanationRow(metric: HealthCheckMetricType) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text(metric.rawValue)
                .font(AppTypography.bodyBold)
                .foregroundColor(AppColors.primaryBlue)

            Text(metric.valueInvestorDescription)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            // Gauge explanation
            HStack(spacing: AppSpacing.xs) {
                Circle()
                    .fill(metric == .debtToEquity || metric == .peRatio ? AppColors.bullish : AppColors.bearish)
                    .frame(width: 8, height: 8)

                Text(metric.leftLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                Spacer()

                Text(metric.rightLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                Circle()
                    .fill(metric == .debtToEquity || metric == .peRatio ? AppColors.bearish : AppColors.bullish)
                    .frame(width: 8, height: 8)
            }
            .padding(.top, AppSpacing.xs)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Value Investing Section

    private var valueInvestingSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "lightbulb.fill")
                    .foregroundColor(AppColors.neutral)

                Text("How Value Investors Use This")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
            }

            VStack(spacing: AppSpacing.md) {
                tipCard(
                    number: "1",
                    title: "Screen for Quality",
                    description: "Use Health Check as a first filter. Companies with 3+ passing metrics often have \"margin of safety\" - a core value investing principle."
                )

                tipCard(
                    number: "2",
                    title: "Compare to Sector",
                    description: "The gauge shows where a metric stands vs sector average. Being better than average in 3+ areas suggests competitive advantage."
                )

                tipCard(
                    number: "3",
                    title: "Identify Red Flags",
                    description: "High debt-to-equity or low current ratio can signal financial distress. Value investors avoid \"value traps\" by checking financial health first."
                )

                tipCard(
                    number: "4",
                    title: "Find Undervalued Gems",
                    description: "A low P/E ratio combined with strong ROE and healthy balance sheet often indicates an overlooked quality company trading at a discount."
                )
            }
        }
    }

    private func tipCard(number: String, title: String, description: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            ZStack {
                Circle()
                    .fill(AppColors.primaryBlue.opacity(0.2))
                    .frame(width: 28, height: 28)

                Text(number)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.primaryBlue)
            }

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(description)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Rating System Section

    private var ratingSystemSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Understanding the Rating")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                ratingRow(rating: .excellent, description: "All 4 metrics pass - exceptional financial health")
                ratingRow(rating: .good, description: "3 of 4 metrics pass - solid fundamentals")
                ratingRow(rating: .mix, description: "2 of 4 metrics pass - mixed signals, investigate further")
                ratingRow(rating: .caution, description: "1 of 4 metrics pass - proceed with caution")
                ratingRow(rating: .poor, description: "0 of 4 metrics pass - significant concerns")
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )

            // Disclaimer
            Text("Note: Health Check is one tool in your analysis toolkit. Always combine with qualitative research, industry analysis, and management assessment before making investment decisions.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, AppSpacing.sm)
        }
    }

    private func ratingRow(rating: HealthCheckRating, description: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: AppSpacing.md) {
            Image(systemName: rating.iconName)
                .font(.system(size: 16))
                .foregroundColor(rating.color)
                .frame(width: 24, alignment: .center)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(rating.rawValue)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(rating.color)

                Text(description)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            
            Spacer()
        }
    }
}

#Preview {
    HealthCheckInfoSheet()
}
