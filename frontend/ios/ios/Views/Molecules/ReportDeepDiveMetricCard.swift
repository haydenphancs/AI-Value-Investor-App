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

    /// Footer color follows the takeaway's SENTIMENT, not the star rating —
    /// a negative note ("Debt 4.21, Far Too High") must read red even on a
    /// high-starred card. Neutral/mixed (and legacy reports without the field)
    /// fall back to the star-based color, preserving the prior look.
    private var labelColor: Color {
        switch data.qualitySentiment.lowercased() {
        case "negative": return AppColors.bearish
        case "positive": return AppColors.bullish
        default: return ratingColor
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Title row with stars
            HStack {
                Text(data.title)
                    .font(AppTypography.label)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)

                Spacer(minLength: 4)

                // Star display
                HStack(spacing: 2) {
                    ForEach(0..<5, id: \.self) { index in
                        Image(systemName: index < data.starRating ? "star.fill" : "star")
                            .font(AppTypography.iconMicro)
                            .foregroundColor(index < data.starRating ? Color(hex: "F59E0B") : AppColors.textMuted)
                    }
                }

                // Drill-down affordance — present only when this card carries
                // a tap-to-expand time series (the section wires the tap).
                if data.hasHistory {
                    Image(systemName: "chevron.right")
                        .font(AppTypography.iconMicro)
                        .foregroundColor(AppColors.textMuted)
                        .padding(.leading, 1)
                }
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.2))

            // Metrics
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                ForEach(data.metrics) { metric in
                    HStack {
                        Text(metric.displayLabel)
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textMuted)
                            .lineLimit(2)
                            .truncationMode(.tail)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 4)
                        HStack(spacing: 2) {
                            if let trend = metric.trend {
                                Image(systemName: trend.iconName)
                                    .font(AppTypography.iconMicro).fontWeight(.bold)
                                    .foregroundColor(trend.color)
                            }
                            Text(metric.value)
                                .font(AppTypography.labelSmall)
                                .fontWeight(.semibold)
                                .foregroundColor(AppColors.textPrimary)
                                .lineLimit(1)
                        }
                    }
                }
            }

            Spacer(minLength: 0)

            // Quality label
            Text(data.qualityLabel)
                .font(AppTypography.labelSmall)
                .foregroundColor(labelColor)
                .lineLimit(2)
                .minimumScaleFactor(0.9)
                .italic()
        }
        // Tighter horizontal padding gives the label/value rows more room (so
        // "Interest Coverage *" fits on one line and big values have headroom);
        // vertical rhythm unchanged.
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, AppSpacing.md)
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
