//
//  ReportEarningsTimelinePanel.swift
//  ios
//
//  Organism: the Earnings Timeline shown INLINE in the Future Forecast module —
//  it replaced the old 4-bar ReportForecastChart. Reported actual revenue + EPS
//  flowing GAPLESSLY into the analyst forecast, with a share-PRICE toggle,
//  horizontal scroll, and a tap-to-inspect popup (all in EarningsTimelineChart).
//  Price is fetched lazily from the /earnings endpoint (not carried in the
//  report payload), so the panel owns that small bit of async state.
//
//  This is the same content that used to live in the "View full timeline" sheet,
//  minus the sheet chrome and the explanatory caption notes.
//

import SwiftUI

struct ReportEarningsTimelinePanel: View {
    let ticker: String
    let timeline: [RevenueProjection]   // gapless actuals -> forecast
    /// Tapped column for the chart's inspect popup — owned by the section so a
    /// tap outside the chart can dismiss it.
    @Binding var selectedIndex: Int?

    @State private var showPrice = true
    @State private var dailyPrices: [EarningsDailyPricePoint] = []
    @State private var didLoad = false

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
            .padding(.top, AppSpacing.xs)

            legend
                .padding(.top, AppSpacing.md)
        }
        .task {
            guard !didLoad else { return }
            didLoad = true
            // Lazy, on-demand — price isn't in the report payload. Reuses the
            // cached /earnings endpoint; silent on failure (chart just omits the
            // price line).
            if let dto = try? await StockRepository.shared.getEarnings(ticker: ticker) {
                dailyPrices = dto.toDisplayModel().dailyPriceHistory
            }
        }
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
        ticker: "ORCL",
        timeline: TickerReportData.sampleOracle.revenueForecast.annualTimeline,
        selectedIndex: .constant(nil)
    )
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
