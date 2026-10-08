//
//  DividendInfoCard.swift
//  ios
//
//  Molecule: Card displaying dividend dates, yield, and status
//

import SwiftUI

struct DividendInfoCard: View {
    let dividendInfo: DividendInfo
    /// Trailing-12-month DIVIDEND yield (`summary.dividendYield`) — the like-for-like
    /// partner of the dividend-only average shown directly beneath it.
    var dividendYield: Double = 0.0
    /// Total shareholder yield (dividends + buybacks), on its own row away from the
    /// average. nil hides the row.
    var totalYield: Double? = nil

    private var formattedDividendYield: String {
        String(format: "%.2f%%", dividendYield)
    }

    var body: some View {
        VStack(spacing: 0) {
            // Ex-Dividend Date row
            DividendInfoRow(
                label: "Ex-Dividend Date",
                value: dividendInfo.formattedExDividendDate
            )

            // Payment Date row — HIDDEN when unknown rather than showing "N/A" forever.
            //
            // The per-payment feed (`/dividends`) is outside the FMP licence, and unlike
            // the ex-dividend date a payment date cannot be derived from price series, so
            // this is permanently nil today. A row that reads "Payment Date  N/A" on
            // every stock in the market is chrome, not information. Kept rather than
            // deleted so it returns by itself if the package is ever bought.
            if dividendInfo.paymentDate != nil {
                divider

                DividendInfoRow(
                    label: "Payment Date",
                    value: dividendInfo.formattedPaymentDate
                )
            }

            // Dividend per share — the amount itself, which this card never showed.
            // From the entitled `ratios` (period=annual); exact against declared totals.
            if dividendInfo.perShare != nil {
                divider

                DividendInfoRow(
                    label: dividendInfo.perShareYear.map { "Dividend / Share (FY\($0))" }
                        ?? "Dividend / Share",
                    value: dividendInfo.formattedPerShare
                )
            }

            // Growth across the series. Absent — not zero — when undefined: a company
            // that began paying inside the window has no rate to report.
            if let growth = dividendInfo.formattedGrowth {
                divider

                DividendInfoRow(
                    label: "Dividend Growth",
                    value: growth,
                    valueColor: (dividendInfo.growthPct ?? 0) < 0
                        ? AppColors.loss : AppColors.gain
                )
            }

            divider

            // Like with like. This row used to be "Current Yield (Div + Buyback)" — the
            // TOTAL yield — directly above a DIVIDEND-only average, so an AAPL-shaped
            // payer read "3.1%" over "0.40%" (8x its own average?) while "Dividend Status"
            // said Fair: the numerator/denominator mismatch the backend fixed for
            // `status`, reintroduced by the layout. Both rows are now dividend-only.
            DividendInfoRow(
                label: "Dividend Yield (T12M)",
                value: formattedDividendYield
            )

            divider

            // The average's REAL window ("Avg Dividend Yield (2Y)" for eight quarters).
            // It was labelled "5Y Avg Yield" over at most eight quarters of data.
            DividendInfoRow(
                label: dividendInfo.averageYieldLabel,
                value: dividendInfo.formattedYield
            )

            divider

            // Dividend Status row
            DividendInfoRow(
                label: "Dividend Status",
                value: dividendInfo.status.rawValue,
                valueColor: dividendInfo.status.color
            )

            divider

            // Dividends + buybacks together, kept apart from the dividend-only rows and
            // next to the buyback verdict it shares a numerator with.
            if let total = totalYield {
                DividendInfoRow(
                    label: "Total Yield (Div + Buyback)",
                    value: String(format: "%.1f%%", total)
                )

                divider
            }

            // Buyback Status row
            DividendInfoRow(
                label: "Buyback Status",
                value: dividendInfo.buybackStatus.rawValue,
                valueColor: dividendInfo.buybackStatus.color
            )
        }
        .padding(.vertical, AppSpacing.md)
        .padding(.horizontal, AppSpacing.lg)
        // `.cardSurface` already draws `cardEdge`. The `.overlay` stroke that used to sit
        // here was inert in dark — `cardBackgroundLight` and `cardBackgroundNested` share
        // the #252B3B dark arm, so it composited to the fill — and a redundant second
        // hairline over `cardEdge` in light. Decoration that renders in one mode only.
        .cardSurface(AppColors.cardBackgroundNested, cornerRadius: AppCornerRadius.medium)
    }

    private var divider: some View {
        // `divider`, not `cardBackground`. This card's surface is
        // `cardBackgroundNested`, whose LIGHT arm is #FFFFFF — identical to
        // `cardBackground`'s — so these six hairlines were 1.0000:1 and drew nothing
        // in light. (Dark was 1.11 and fine, which is why it looked correct.)
        //
        // Not `cardBackgroundLight` either, which is what the other ~40 divider sites
        // use: its DARK arm #252B3B is identical to `cardBackgroundNested`'s, so that
        // would trade a light bug for a dark one. `divider` is alpha over whatever it
        // sits on, so it separates on both arms by construction — the exact shape its
        // own docstring prescribes.
        Rectangle()
            .fill(AppColors.divider)
            .frame(height: 1)
            .padding(.vertical, AppSpacing.md)
    }
}

// MARK: - Buyback-Only Info Card

/// The buyback half of `DividendInfoCard`, for companies that pay no dividend.
///
/// `SignalOfConfidenceSectionCard` gates the dividend card on a non-nil
/// `dividendInfo`, and the backend returns nil for every non-payer — so the buyback
/// verdict, which depends only on buyback yield and share-count change, was computed
/// and then never shown. That silently hid it for AMZN, BRK-B and NFLX, three of the
/// largest repurchasers on the market.
///
/// Deliberately NOT solved by synthesising an empty `DividendInfo`: that would render
/// "Ex-Dividend Date —" and "5Y Avg Yield 0.00%" for a company that has never paid a
/// dividend, which reads as real data rather than absent data.
struct BuybackOnlyInfoCard: View {
    let buybackStatus: BuybackStatus
    var buybackYield: Double = 0.0
    var shareCountChange: Double = 0.0
    /// False when fewer than two quarters reported a share count: `shareCountChange` is
    /// then a 0.0 placeholder, and printing it as "+0.0%" claimed a measured flat count.
    var shareCountChangeKnown: Bool = true
    /// The quarter the change is measured from (`SignalOfConfidenceSectionData
    /// .shareCountWindowStart`), e.g. "Q4 '24". A bare "+36.3%" did not say over what span —
    /// and for a company that returns no capital, that dilution IS the confidence signal
    /// (TestFlight 1.0 (11), CRWV). nil keeps the bare figure.
    var shareCountWindowStart: String? = nil

    private var formattedBuybackYield: String {
        String(format: "%.1f%%", buybackYield)
    }

    private var formattedShareCountChange: String {
        guard shareCountChangeKnown else { return "—" }
        // Sign is meaningful here: negative == shrinking share count == buybacks.
        let change = String(format: "%+.1f%%", shareCountChange)
        guard let start = shareCountWindowStart, !start.isEmpty else { return change }
        return "\(change) since \(start)"
    }

    private var shareCountChangeColor: Color {
        guard shareCountChangeKnown else { return AppColors.textSecondary }
        // A shrinking count is the shareholder-friendly direction.
        return shareCountChange < 0 ? AppColors.gain
            : (shareCountChange > 0 ? AppColors.loss : AppColors.textPrimary)
    }

    var body: some View {
        VStack(spacing: 0) {
            DividendInfoRow(
                label: "Dividend",
                value: "None"
            )

            divider

            DividendInfoRow(
                label: "Buyback Yield",
                value: formattedBuybackYield
            )

            divider

            DividendInfoRow(
                label: "Share Count Change",
                value: formattedShareCountChange,
                valueColor: shareCountChangeColor
            )

            divider

            DividendInfoRow(
                label: "Buyback Status",
                value: buybackStatus.rawValue,
                valueColor: buybackStatus.color
            )
        }
        .padding(.vertical, AppSpacing.md)
        .padding(.horizontal, AppSpacing.lg)
        // Nested inside the Signal of Confidence card — MUST pass
        // `cardBackgroundNested` or it shares its parent's fill and vanishes in dark.
        .cardSurface(AppColors.cardBackgroundNested, cornerRadius: AppCornerRadius.medium)
    }

    private var divider: some View {
        // Same reasoning as DividendInfoCard.divider — `divider` is alpha over
        // whatever it sits on, so it separates on both appearance arms.
        Rectangle()
            .fill(AppColors.divider)
            .frame(height: 1)
            .padding(.vertical, AppSpacing.md)
    }
}

// MARK: - Dividend Info Row

private struct DividendInfoRow: View {
    let label: String
    let value: String
    var valueColor: Color = AppColors.textPrimary

    var body: some View {
        HStack {
            Text(label)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            Text(value)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(valueColor)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
        VStack(spacing: AppSpacing.lg) {
            DividendInfoCard(dividendInfo: .sample, dividendYield: 2.95, totalYield: 3.4)

            // High yield example — no window from the backend (an older payload), so the
            // average row falls back to the window-neutral label.
            DividendInfoCard(
                dividendInfo: DividendInfo(
                    exDividendDate: Date(),
                    paymentDate: Date().addingTimeInterval(86400 * 7),
                    fiveYearAvgYield: 3.45,
                    status: .high,
                    buybackStatus: .high
                ),
                dividendYield: 3.6,
                totalYield: 5.8
            )

            // A non-payer whose share count was never reported twice: "—", not "+0.0%".
            BuybackOnlyInfoCard(
                buybackStatus: .moderate,
                buybackYield: 1.4,
                shareCountChange: 0.0,
                shareCountChangeKnown: false
            )

            // Returns no capital and dilutes: the change names its window (sample values).
            BuybackOnlyInfoCard(
                buybackStatus: .diluting,
                buybackYield: 0.0,
                shareCountChange: 36.3,
                shareCountWindowStart: "Q4 '24"
            )
        }
        .padding()
        }
    }
}
