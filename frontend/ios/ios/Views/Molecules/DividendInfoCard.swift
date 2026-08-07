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
