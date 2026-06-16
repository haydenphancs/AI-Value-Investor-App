//
//  ReportFutureForecastSection.swift
//  ios
//
//  Organism: Future Forecast deep dive content with revenue chart and management guidance
//

import SwiftUI

struct ReportFutureForecastSection: View {
    let forecast: ReportRevenueForecast
    /// Ticker — the inline Earnings Timeline panel uses it to lazily fetch the
    /// share-price overlay from the /earnings endpoint.
    let ticker: String
    /// Selected timeline column for the chart's inspect popup. Owned here so a
    /// tap anywhere in this module outside the chart dismisses it.
    @State private var selectedTimelineIndex: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // The continuity Earnings Timeline — reported actuals → analyst
            // forecast, with a price toggle, horizontal scroll, and a
            // tap-to-inspect popup — shown INLINE; it replaced the old 4-bar
            // forecast chart. Falls back to the legacy chart only for older
            // reports that predate the annual_timeline payload.
            if !forecast.annualTimeline.isEmpty {
                ReportEarningsTimelinePanel(
                    timeline: forecast.annualTimeline,
                    dailyPrices: forecast.timelinePrices,
                    selectedIndex: $selectedTimelineIndex
                )
            } else {
                ReportForecastChart(forecast: forecast)
            }

            companyGuidance

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
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(3)
                }
                .padding(AppSpacing.md)
            }
        }
        // Tap anywhere in this module OUTSIDE the chart dismisses the inspect
        // popup. A discrete tap → coexists with vertical/horizontal scrolling
        // (scroll never triggers it); taps on the chart hit its own gesture
        // first, so they still drive selection. Mirrors ReportInsiderSection.
        .contentShape(Rectangle())
        .onTapGesture { selectedTimelineIndex = nil }
    }

    // MARK: - Company Guidance

    /// Management guidance badge + verbatim quote. Lifted out of
    /// ReportForecastChart so it shows under either chart (inline timeline or the
    /// legacy fallback).
    private var companyGuidance: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Text("Company Guidance")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textSecondary)
            }

            ReportSentimentBadge(
                text: forecast.managementGuidance.rawValue,
                textColor: forecast.managementGuidance.color,
                backgroundColor: forecast.managementGuidance.backgroundColor
            )

            if let quote = forecast.guidanceQuote {
                HStack(alignment: .top, spacing: AppSpacing.sm) {
                    Rectangle()
                        .fill(AppColors.primaryBlue)
                        .frame(width: 2)

                    Text("\"\(quote)\"")
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textSecondary)
                        .italic()
                }
                .padding(.top, AppSpacing.xs)
            }
        }
    }

    // MARK: - Earnings beat/miss strip

    private var beatMissStrip: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Title OUTSIDE the card — same style as Company Guidance.
            Text("EPS Track Record")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            // Gray card: a header row (label + Beat summary) above the
            // horizontally-scrolling quarter cells.
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                HStack(alignment: .firstTextBaseline) {
                    // Clarifies the per-quarter % is the beat/miss vs estimate,
                    // not a YoY increase/decrease.
                    Text("Beat/Miss %")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .offset(y: -2)
                    Spacer()
                    if let summary = forecast.beatSummary {
                        Text(summary)
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.bullish)
                            .padding(.horizontal, AppSpacing.sm)
                            .padding(.vertical, 2)
                            .background(Capsule().fill(AppColors.bullish.opacity(0.15)))
                    }
                }

                // Up to 10 reported quarters — scrolls horizontally so the full
                // streak fits. Each cell shows the beat/miss arrow, the signed EPS
                // surprise %, and the fiscal quarter.
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: AppSpacing.sm) {
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
                                Text(q.surpriseText)
                                    .font(.system(size: 9, weight: .semibold))
                                    .foregroundColor(q.beat ? AppColors.bullish : AppColors.bearish)
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.7)
                                Text(q.period)
                                    .font(.system(size: 9))
                                    .foregroundColor(AppColors.textMuted)
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.7)
                            }
                            .frame(width: 50)
                        }
                    }
                }
                .defaultScrollAnchor(.trailing)
            }
            .padding(AppSpacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackgroundLight)
            )
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

#Preview {
    ReportFutureForecastSection(forecast: TickerReportData.sampleOracle.revenueForecast, ticker: "ORCL")
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
