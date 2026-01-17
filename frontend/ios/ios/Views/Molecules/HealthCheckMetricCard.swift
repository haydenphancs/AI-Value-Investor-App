//
//  HealthCheckMetricCard.swift
//  ios
//
//  Molecule: Individual metric card for Health Check display
//  Shows metric name, value, gauge, and insight text
//

import SwiftUI

struct HealthCheckMetricCard: View {
    let metric: HealthCheckMetric

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header: Metric name, subtitle, and value
            headerSection

            // Gauge bar with position indicator
            gaugeSection

            // Insight text with highlighted portion
            insightSection
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackgroundLight)
        )
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(metric.type.rawValue)
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                Text(metric.type.subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            Spacer()

            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(metric.formattedValue)
                    .font(AppTypography.title2)
                    .foregroundColor(metric.valueColor)

                if let comparison = metric.formattedComparison {
                    Text(comparison)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }
        }
    }

    // MARK: - Gauge Section

    private var gaugeSection: some View {
        VStack(spacing: AppSpacing.xs) {
            HealthCheckGaugeBar(
                position: metric.gaugePosition,
                metricType: metric.type
            )

            HStack {
                Text(metric.type.leftLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                Spacer()

                Text(metric.type.rightLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    // MARK: - Insight Section

    private var insightSection: some View {
        insightTextView
            .font(AppTypography.callout)
            .foregroundColor(AppColors.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder
    private var insightTextView: some View {
        if let highlightedValue = metric.highlightedValue {
            if let highlightedLabel = metric.highlightedLabel {
                // Format: "43% lower debt than sector average." or "Trading at a 15% discount to..."
                if metric.type == .peRatio {
                    // Special format for P/E: "Trading at a 15% discount to the Tech sector..."
                    Text("\(Text(highlightedLabel).foregroundColor(AppColors.textSecondary)) \(Text(highlightedValue).foregroundColor(metric.valueColor).bold()) discount \(Text(metric.insightText).foregroundColor(AppColors.textSecondary))")
                } else if metric.type == .returnOnEquity || metric.type == .currentRatio {
                    // Format: "22% below ROE than peers..." or "21% above sector average..."
                    Text("\(Text(highlightedValue).foregroundColor(metric.valueColor).bold()) \(Text(highlightedLabel).foregroundColor(metric.valueColor).bold()) \(Text(metric.insightText).foregroundColor(AppColors.textSecondary))")
                } else {
                    // Format: "43% lower debt than sector average."
                    Text("\(Text(highlightedValue).foregroundColor(metric.valueColor).bold()) \(Text(highlightedLabel).foregroundColor(AppColors.textSecondary)) \(Text(metric.insightText).foregroundColor(AppColors.textSecondary))")
                }
            } else {
                // Format: "Above sector average..." (fallback, not used anymore)
                Text("\(Text(highlightedValue).foregroundColor(metric.valueColor).bold()) \(Text(metric.insightText).foregroundColor(AppColors.textSecondary))")
            }
        } else {
            Text(metric.insightText)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            VStack(spacing: AppSpacing.lg) {
                ForEach(HealthCheckSectionData.sampleData.metrics) { metric in
                    HealthCheckMetricCard(metric: metric)
                }
            }
            .padding()
        }
    }
}
