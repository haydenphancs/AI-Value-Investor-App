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
                Text("Fiscal year")
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
                        Text(period.fiscalPeriod)
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                        // The count is shown per row rather than only in the header
                        // because it genuinely varies by year — AAPL runs 29 analysts on
                        // FY2027 and 8 on FY2029, and a far year is a much thinner claim.
                        Text("\(period.analystCount) analysts")
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
