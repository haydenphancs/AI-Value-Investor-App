//
//  ThemeCompanyRow.swift
//  ios
//
//  Molecule: one company row in the theme detail's "Companies" list — a logo +
//  company name + ticker on the left, current price + green/red daily change on
//  the right. Tappable → the stock's detail. Flat row (the parent list wraps it
//  in a card with hairline dividers), matching the constituents-list design.
//

import SwiftUI

struct ThemeCompanyRow: View {
    let company: ThemeConstituent
    var onTap: (() -> Void)? = nil

    var body: some View {
        Button { onTap?() } label: {
            HStack(spacing: AppSpacing.md) {
                CompanyLogoView(ticker: company.ticker, size: 40)

                VStack(alignment: .leading, spacing: 2) {
                    Text(company.name)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)
                    Text(company.ticker)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer(minLength: AppSpacing.sm)

                VStack(alignment: .trailing, spacing: 2) {
                    if !company.priceText.isEmpty {
                        Text(company.priceText)
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                    }
                    if !company.changeText.isEmpty {
                        Text(company.changeText)
                            .font(AppTypography.caption)
                            .foregroundColor(company.isPositive ? AppColors.bullish : AppColors.bearish)
                    }
                }
            }
            .padding(.vertical, AppSpacing.md)
            .padding(.horizontal, AppSpacing.md)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    VStack(spacing: 0) {
        ThemeCompanyRow(company: ThemeConstituent(
            ticker: "NVDA", name: "NVIDIA Corp.", priceText: "$1,204.20",
            changeText: "+2.10%", isPositive: true, marketCapText: "3.0T Cap"))
        ThemeCompanyRow(company: ThemeConstituent(
            ticker: "AMD", name: "Advanced Micro Devices", priceText: "$168.40",
            changeText: "-1.80%", isPositive: false, marketCapText: "270.0B Cap"))
    }
    .background(AppColors.cardBackground)
    .padding()
    .background(AppColors.background)
}
