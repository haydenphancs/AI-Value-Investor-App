//
//  ReportDeepDiveMetricCard.swift
//  ios
//
//  Molecule: Individual metric card for deep dive (Profitability, Valuation, Growth, Health)
//

import SwiftUI

struct ReportDeepDiveMetricCard: View {
    let data: DeepDiveMetricCard

    private var ratingColor: Color {
        switch data.starRating {
        case 4...5: return AppColors.bullish
        case 3: return AppColors.neutral
        default: return AppColors.bearish
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Title row with stars
            HStack {
                Text(data.title)
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                // Star display
                HStack(spacing: 2) {
                    ForEach(0..<5, id: \.self) { index in
                        Image(systemName: index < data.starRating ? "star.fill" : "star")
                            .font(.system(size: 8))
                            .foregroundColor(index < data.starRating ? Color(hex: "F59E0B") : AppColors.textMuted)
                    }
                }
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.2))

            // Metrics
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                ForEach(data.metrics) { metric in
                    HStack {
                        Text(metric.label)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                        Spacer()
                        HStack(spacing: 2) {
                            if let trend = metric.trend {
                                Image(systemName: trend.iconName)
                                    .font(.system(size: 8, weight: .bold))
                                    .foregroundColor(trend.color)
                            }
                            Text(metric.value)
                                .font(AppTypography.captionBold)
                                .foregroundColor(AppColors.textPrimary)
                        }
                    }
                }
            }

            Spacer(minLength: 0)

            // Quality label
            Text(data.qualityLabel)
                .font(AppTypography.caption)
                .foregroundColor(ratingColor)
                .italic()
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }
}

#Preview {
    let sample = TickerReportData.sampleOracle.fundamentalMetrics
    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: AppSpacing.md) {
        ForEach(sample) { metric in
            ReportDeepDiveMetricCard(data: metric)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
