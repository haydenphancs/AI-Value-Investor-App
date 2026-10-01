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

            // Gauge bar with position indicator. A not-meaningful row (ROE on negative
            // equity) has no place on the scale, so it draws no gauge at all rather than
            // a marker parked at a made-up midpoint.
            if !metric.isNotMeaningful {
                gaugeSection
            }

            // Insight text with highlighted portion
            insightSection
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill(AppColors.cardBackgroundNested)   // nested in a card: step the surface up in dark
        )
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(metric.type.rawValue)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                Text(metric.type.subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            Spacer()

            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(metric.formattedValue)
                    .font(AppTypography.titleCompact)
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
                metricType: metric.type,
                hasBenchmark: metric.comparisonValue != nil,
                // The zone gauge places the TRUE Z; inverting gauge_position capped it
                // at 4.41.
                zValue: metric.type == .altmanZScore ? metric.value : nil
            )

            if metric.type == .altmanZScore {
                // Zone labels aligned to segment widths: Distress 30%, Grey 20%, Safe 50%.
                // Boundaries follow the backend status: 1.8 itself is Distress, 3.0
                // itself is Grey.
                GeometryReader { geo in
                    let w = geo.size.width
                    HStack(spacing: 0) {
                        Text("≤ 1.8")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.bearish)
                            .frame(width: w * 0.30, alignment: .center)

                        Text("1.8 – 3.0")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.neutral)
                            .frame(width: w * 0.20, alignment: .center)

                        Text("> 3.0")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.bullish)
                            .frame(width: w * 0.50, alignment: .center)
                    }
                }
                .frame(height: 16)
            } else {
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
    }

    // MARK: - Insight Section

    private var insightSection: some View {
        insightTextView
            .font(AppTypography.bodySmall)
            .foregroundColor(AppColors.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder
    private var insightTextView: some View {
        if let highlightedValue = metric.highlightedValue,
           let highlightedLabel = metric.highlightedLabel {
            // Unified format for all metrics:
            // "21% above sector average. Descriptive text."
            Text("\(Text(highlightedValue).foregroundColor(metric.valueColor).bold()) \(Text(highlightedLabel).foregroundColor(metric.valueColor).bold()) \(Text(metric.insightText).foregroundColor(AppColors.textSecondary))")
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
                // Edge rows: negative D/E, ROE "N/M" (no gauge), Z exactly 3.0 (Grey).
                ForEach(HealthCheckSectionData.sampleNegativeEquity.metrics) { metric in
                    HealthCheckMetricCard(metric: metric)
                }
            }
            .padding()
        }
    }
}
