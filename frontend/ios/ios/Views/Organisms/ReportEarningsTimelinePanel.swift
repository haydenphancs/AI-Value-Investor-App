//
//  ReportEarningsTimelinePanel.swift
//  ios
//
//  Organism: the Earnings Timeline shown INLINE in the Future Forecast module —
//  it replaced the old 4-bar ReportForecastChart. Reported actual revenue + EPS
//  flowing GAPLESSLY into the analyst forecast, with a share-PRICE toggle,
//  horizontal scroll, and a tap-to-inspect popup (all in EarningsTimelineChart).
//  The PRICE series is EMBEDDED in the report payload (frozen at generation),
//  so the panel renders it directly — NO live /earnings fetch. That keeps an
//  old report point-in-time accurate (price reflects the run date, not today).
//
//  This is the same content that used to live in the "View full timeline" sheet,
//  minus the sheet chrome and the explanatory caption notes.
//

import SwiftUI

struct ReportEarningsTimelinePanel: View {
    let timeline: [RevenueProjection]   // gapless actuals -> forecast
    /// Frozen monthly price series embedded in the report (NOT fetched live), so
    /// the overlay shows the price as of when the report was generated.
    let dailyPrices: [EarningsDailyPricePoint]
    /// Tapped column for the chart's inspect popup — owned by the section so a
    /// tap outside the chart can dismiss it.
    @Binding var selectedIndex: Int?

    @State private var showPrice = true

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Spacer()
                EarningsPriceToggle(isEnabled: $showPrice)
            }

            // Tight gap below the Price toggle. The chart's top headroom + the
            // inspect popup (pushed down via popupCenterY) keep the popup from
            // crowding the button despite the smaller gap.
            EarningsTimelineChart(
                timeline: timeline,
                dailyPrices: dailyPrices,
                showPrice: showPrice,
                selectedIndex: $selectedIndex
            )
            .padding(.top, AppSpacing.xxs)

            legend
                .padding(.top, AppSpacing.md)

            // Reported years plot GAAP EPS; forecast years plot the analyst consensus,
            // which most companies report on an ADJUSTED basis — so the line can step at
            // the boundary without that being growth (the backend leaves that year's EPS
            // YoY empty). Shown only when the report says so (`eps_basis`, newer reports).
            if mixesEPSBases {
                Text("EPS: reported years are GAAP; forecast years are the analyst consensus, usually adjusted.")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, AppSpacing.xs)
            }
        }
    }

    /// True when the timeline has GAAP actual years AND consensus forecast years.
    private var mixesEPSBases: Bool {
        let bases = Set(timeline.compactMap(\.epsBasis))
        return bases.contains("gaap") && bases.contains("consensus")
    }

    private enum LegendShape { case bar, dot, line }

    private var legend: some View {
        HStack(spacing: AppSpacing.lg) {
            legendItem(color: AppColors.primaryBlue, shape: .bar, label: "Revenue")
            legendItem(color: AppColors.accentYellow, shape: .dot, label: "EPS")
            if showPrice {
                legendItem(color: AppColors.accentCyan, shape: .line, label: "Price")
            }
        }
        .frame(maxWidth: .infinity)
    }

    private func legendItem(color: Color, shape: LegendShape, label: String) -> some View {
        HStack(spacing: AppSpacing.xs) {
            Group {
                switch shape {
                case .bar: RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 12, height: 12)
                case .dot: Circle().fill(color).frame(width: 8, height: 8)
                case .line: RoundedRectangle(cornerRadius: 1).fill(color).frame(width: 14, height: 3)
                }
            }
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
    }
}

#Preview {
    ReportEarningsTimelinePanel(
        timeline: TickerReportData.sampleOracle.revenueForecast.annualTimeline,
        dailyPrices: TickerReportData.sampleOracle.revenueForecast.timelinePrices,
        selectedIndex: .constant(nil)
    )
    .padding()
    .background(AppColors.cardBackground)
}
