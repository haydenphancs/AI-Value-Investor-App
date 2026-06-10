//
//  ReportFutureForecastSection.swift
//  ios
//
//  Organism: Future Forecast deep dive content with revenue chart and management guidance
//

import SwiftUI

struct ReportFutureForecastSection: View {
    let forecast: ReportRevenueForecast
    /// Opens the full yearly continuity sheet. Passed nil (button hidden) when
    /// the backend didn't supply the annual_timeline series (older reports).
    var onViewTimeline: (() -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            ReportForecastChart(forecast: forecast)

            // Entry point to the full yearly continuity view (historical actuals
            // → forecast, with an optional price line). Keeps this module compact
            // while the whole arc lives one tap away.
            if let onViewTimeline {
                Button(action: onViewTimeline) {
                    HStack(spacing: AppSpacing.xxs) {
                        Image(systemName: "chart.xyaxis.line")
                            .font(AppTypography.iconTiny)
                        Text("View full timeline")
                            .font(AppTypography.captionEmphasis)
                        Image(systemName: "chevron.right")
                            .font(AppTypography.iconTiny).fontWeight(.semibold)
                    }
                    .foregroundColor(AppColors.primaryBlue)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.xs)
                    .contentShape(Rectangle())
                }
                .buttonStyle(PlainButtonStyle())
            }

            // Earnings beat/miss track record — last reported quarters vs
            // estimate. Hidden when the backend produced no earnings data.
            if !forecast.earningsTrackRecord.isEmpty {
                beatMissStrip
            }

            // Insight — Stage-B narrative explaining WHY the forward
            // trajectory looks the way it does. Hidden when the backend
            // didn't produce one (older cached reports / fallback path).
            if let insight = forecast.insight, !insight.isEmpty {
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "sparkles.2")
                            .foregroundStyle(
                                LinearGradient(
                                    colors: [.indigo],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                            )
                            .font(AppTypography.iconDefault).fontWeight(.semibold)

                        Text("Insight")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundStyle(LinearGradient(
                                colors: [.indigo, .cyan],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            ))
                    }

                    Text(insight)
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(3)
                }
                .padding(AppSpacing.md)
            }
        }
    }

    // MARK: - Earnings beat/miss strip

    private var beatMissStrip: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack {
                Text("Earnings Track Record")
                    .font(AppTypography.label)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                Spacer()
                if let summary = forecast.beatSummary {
                    Text(summary)
                        .font(AppTypography.labelSmall)
                        .fontWeight(.semibold)
                        .foregroundColor(AppColors.bullish)
                        .padding(.horizontal, AppSpacing.sm)
                        .padding(.vertical, 2)
                        .background(Capsule().fill(AppColors.bullish.opacity(0.15)))
                }
            }

            HStack(spacing: AppSpacing.xs) {
                ForEach(forecast.earningsTrackRecord) { q in
                    VStack(spacing: 3) {
                        Image(systemName: q.beat ? "arrow.up" : "arrow.down")
                            .font(.system(size: 9, weight: .bold))
                            .foregroundColor(q.beat ? AppColors.bullish : AppColors.bearish)
                            .frame(width: 22, height: 22)
                            .background(
                                Circle().fill(
                                    (q.beat ? AppColors.bullish : AppColors.bearish).opacity(0.15)
                                )
                            )
                        Text(q.period)
                            .font(.system(size: 9))
                            .foregroundColor(AppColors.textMuted)
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                    }
                    .frame(maxWidth: .infinity)
                }
            }
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
    ReportFutureForecastSection(forecast: TickerReportData.sampleOracle.revenueForecast)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
