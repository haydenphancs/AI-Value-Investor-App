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
            // Off-calendar filers only (Planet Labs, NVDA, Apple…): says which months the
            // "FY" columns cover, so the year in progress isn't read as a future year.
            if let note = timeline.fiscalYearNote {
                Text(note)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, AppSpacing.xs)
            }

            // No forecast column (a ticker without analyst estimates): the price line stops
            // at the last fiscal year-end, so the months since would read as missing.
            if showPrice, let cutoff = Self.priceCutoffNote(timeline: timeline, prices: dailyPrices) {
                Text(cutoff)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, AppSpacing.xs)
            }
        }
    }

    /// "Price shown to Dec 31, 2025." — only when no column is a forecast, the frozen price
    /// series runs past the last column's period end, AND the chart actually draws a line.
    /// EarningsTimelineChart draws a close only from the first column's period END (its
    /// first bar: `yearEndShift` puts each year-end on its bar, and the lead-in before it is
    /// trimmed) through the last column's, so the later closes are not drawn (owner,
    /// 2026-11-14: "Caption", not an extra column). A listing that began after the last
    /// year-end has no drawn close at all, and "shown to" would describe a line that is not
    /// there; nor is one drawn from a single close or a flat series. nil whenever the chart is
    /// not on its period-end mapping (a column without a period end, or ends out of order).
    static func priceCutoffNote(
        timeline: [RevenueProjection],
        prices: [EarningsDailyPricePoint]
    ) -> String? {
        guard !timeline.isEmpty, !timeline.contains(where: { $0.isForecast }) else { return nil }
        var ends: [String] = []
        for column in timeline {
            guard let end = column.periodEnd, end.count >= 10 else { return nil }
            let day = String(end.prefix(10))
            if let previous = ends.last, day <= previous { return nil }
            ends.append(day)
        }
        guard let firstEnd = ends.first, let lastEnd = ends.last,
              let firstEndDay = EarningsPriceTimeline.dayNumber(firstEnd),
              let lastEndDay = EarningsPriceTimeline.dayNumber(lastEnd) else { return nil }
        var drawn: [Double] = []
        var latestDay: Int? = nil
        for point in prices {
            guard let day = EarningsPriceTimeline.dayNumber(point.date) else { continue }
            latestDay = max(latestDay ?? day, day)
            if day >= firstEndDay, day <= lastEndDay, point.price.isFinite {
                drawn.append(point.price)
            }
        }
        guard drawn.count >= 2, let low = drawn.min(), let high = drawn.max(), high > low,
              let latest = latestDay, latest > lastEndDay,
              let shown = shortDate(lastEnd) else { return nil }
        return "Price shown to \(shown)."
    }

    private static let monthAbbreviations: [String] = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]

    /// "2025-12-31" → "Dec 31, 2025", by string arithmetic (no DateFormatter, no zone).
    static func shortDate(_ ymd: String) -> String? {
        let parts: [Substring] = ymd.split(separator: "-", omittingEmptySubsequences: false)
        guard parts.count == 3,
              let year = Int(parts[0]), let month = Int(parts[1]), let day = Int(parts[2]),
              (1...12).contains(month), (1...31).contains(day) else { return nil }
        return "\(monthAbbreviations[month - 1]) \(day), \(year)"
    }

    /// True when the timeline has GAAP actual years AND consensus forecast years.
    private var mixesEPSBases: Bool {
        let bases = Set(timeline.compactMap(\.epsBasis))
        return bases.contains("gaap") && bases.contains("consensus")
    }

    /// True when the reported years plot ADJUSTED EPS that differs from GAAP somewhere: the
    /// legend then reads "Adj. EPS". That label is the only marker — no note and no GAAP figure
    /// (owner, 2026-11-14: "we don't need to show GAAP anymore", then "Remove the note").
    private var showsAdjustedEPS: Bool {
        RevenueProjection.showsAdjustedEPS(timeline)
    }

    private enum LegendShape { case bar, dot, line }

    private var legend: some View {
        HStack(spacing: AppSpacing.lg) {
            legendItem(color: AppColors.primaryBlue, shape: .bar, label: "Revenue")
            legendItem(color: AppColors.accentYellow, shape: .dot,
                       label: showsAdjustedEPS ? "Adj. EPS" : "EPS")
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
