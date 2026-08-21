//
//  DividendInfoCard.swift
//  ios
//
//  Molecule: Card displaying dividend dates, yield, and status
//

import SwiftUI

struct DividendInfoCard: View {
    let dividendInfo: DividendInfo
    var currentYield: Double = 0.0

    private var formattedCurrentYield: String {
        String(format: "%.1f%%", currentYield)
    }

    var body: some View {
        VStack(spacing: 0) {
            // Ex-Dividend Date row
            DividendInfoRow(
                label: "Ex-Dividend Date",
                value: dividendInfo.formattedExDividendDate
            )

            divider

            // Payment Date row
            DividendInfoRow(
                label: "Payment Date",
                value: dividendInfo.formattedPaymentDate
            )

            divider

            // Current Yield row (Dividends + Buyback)
            DividendInfoRow(
                label: "Current Yield (Div + Buyback)",
                value: formattedCurrentYield
            )

            divider

            // 5Y Avg Yield row
            DividendInfoRow(
                label: "5Y Avg Yield",
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

    private var formattedBuybackYield: String {
        String(format: "%.1f%%", buybackYield)
    }

    private var formattedShareCountChange: String {
        // Sign is meaningful here: negative == shrinking share count == buybacks.
        String(format: "%+.1f%%", shareCountChange)
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
                // A shrinking count is the shareholder-friendly direction.
                valueColor: shareCountChange < 0 ? AppColors.gain
                    : (shareCountChange > 0 ? AppColors.loss : AppColors.textPrimary)
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

        VStack(spacing: AppSpacing.lg) {
            DividendInfoCard(dividendInfo: .sample, currentYield: 2.9)

            // High yield example
            DividendInfoCard(
                dividendInfo: DividendInfo(
                    exDividendDate: Date(),
                    paymentDate: Date().addingTimeInterval(86400 * 7),
                    fiveYearAvgYield: 3.45,
                    status: .high,
                    buybackStatus: .high
                ),
                currentYield: 5.8
            )
        }
        .padding()
    }
}
