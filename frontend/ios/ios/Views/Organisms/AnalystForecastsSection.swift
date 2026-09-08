//
//  AnalystForecastsSection.swift
//  ios
//
//  Forward Street estimates for the Analysis tab.
//
//  ⚠️ THIS IS NOT THE ANALYST RATINGS CARD, and the distinction is the whole reason the
//  file exists. `grades` and `price-target-consensus` fell outside the signed FMP Order
//  Form on 2026-09-03, so `AnalystRatingsSection` — consensus, price target, momentum,
//  upgrades — has no data and `TickerAnalysisContent` hides it via `sectionAvailable`.
//
//  `analyst-estimates` IS licensed and carries something different: forward revenue and
//  EPS consensus with real contributing-analyst counts. It contains NO rating, NO price
//  target and NO upgrade history, so nothing here may be phrased as a recommendation, and
//  this card must never be wired to `sectionAvailable` — doing so would render the ratings
//  card's zero defaults as a confident HOLD at a $0.00 target.
//

import SwiftUI

struct AnalystForecastsSection: View {
    let ratingsData: AnalystRatingsData

    private var periods: [AnalystEstimatePeriod] { ratingsData.forwardEstimates }

    /// The honest denominator for the word "Street" — the deepest coverage on any period
    /// shown, so the subtitle never overstates how many desks are behind the numbers.
    private var analystCount: Int { periods.map(\.analystCount).max() ?? 0 }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            AnalysisSectionHeader(
                title: "Street Estimates",
                subtitle: analystCount > 0
                    ? "Forward consensus from up to \(analystCount) analysts"
                    : nil,
                showMoreButton: false
            )

            HStack(spacing: 0) {
                Text("Period ending")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("Revenue")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textSecondary)
                    .frame(width: 90, alignment: .trailing)
                Text("EPS")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textSecondary)
                    .frame(width: 70, alignment: .trailing)
            }

            ForEach(periods) { period in
                HStack(spacing: 0) {
                    VStack(alignment: .leading, spacing: 2) {
                        // ⚠️ The period END, not an invented "FY" name. `FY{year}` took
                        // the calendar year the period ENDS in, but Target, Home Depot,
                        // Lowe's and Kroger name a fiscal year by the year it BEGINS — so a
                        // period ending 2027-01-31 is their fiscal 2026 while the column,
                        // literally headed "Fiscal year", said FY2027. Walmart uses the
                        // opposite convention, so no single rule is right for everyone.
                        // The end date is unambiguous and needs no convention at all.
                        Text(period.periodEndLabel)
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                        // Per-column counts, not one blended number. The two genuinely
                        // differ — AMC's measured shape is 2 revenue analysts and 1 on EPS
                        // — and collapsing them to `max()` advertised a single desk's EPS
                        // as "2 analysts". A column below the floor shows no count at all
                        // rather than borrowing its sibling's.
                        Text(period.analystSummary)
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textSecondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    // `formattedRevenue` / `formattedEPS` render an em dash when the
                    // backend sent null. They must never substitute a zero: an unknown
                    // forecast beside a real analyst count reads as a measurement.
                    Text(period.formattedRevenue)
                        .font(AppTypography.dataMedium)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 90, alignment: .trailing)

                    Text(period.formattedEPS)
                        .font(AppTypography.dataMedium)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 70, alignment: .trailing)
                }
            }

            Text("Estimates are analysts' forecasts of future results, not a rating or a "
                 + "price target, and not a recommendation.")
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .cardSurface()
    }
}

#Preview {
    AnalystForecastsSection(ratingsData: AnalystRatingsData.sampleWithEstimates)
        .padding()
}
