//
//  DividendInfoCard.swift
//  ios
//
//  Molecule: Card displaying dividend dates, yield, and status
//

import SwiftUI

struct DividendInfoCard: View {
    let dividendInfo: DividendInfo

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

            // 5Y Avg Yield row
            DividendInfoRow(
                label: "5Y Avg Yield",
                value: dividendInfo.formattedYield
            )

            divider

            // Status row
            DividendInfoRow(
                label: "Status",
                value: dividendInfo.status.rawValue,
                valueColor: dividendInfo.status.color
            )
        }
        .padding(.vertical, AppSpacing.md)
        .padding(.horizontal, AppSpacing.lg)
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .stroke(AppColors.cardBackgroundLight.opacity(0.5), lineWidth: 1)
        )
    }

    private var divider: some View {
        Rectangle()
            .fill(AppColors.cardBackground)
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
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            Text(value)
                .font(AppTypography.bodyBold)
                .foregroundColor(valueColor)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            DividendInfoCard(dividendInfo: .sample)

            // High yield example
            DividendInfoCard(
                dividendInfo: DividendInfo(
                    exDividendDate: Date(),
                    paymentDate: Date().addingTimeInterval(86400 * 7),
                    fiveYearAvgYield: 3.45,
                    status: .high
                )
            )
        }
        .padding()
    }
}
