//
//  ETFDividendHistoryView.swift
//  ios
//
//  Simple screen showing full dividend history for an ETF
//  Columns: Ex-Div Date, Dividend Per Share
//

import SwiftUI

struct ETFDividendHistoryView: View {
    @Environment(\.dismiss) private var dismiss
    let symbol: String
    let dividendHistory: [ETFDividendPayment]

    var body: some View {
        VStack(spacing: 0) {
            // Navigation header
            HStack {
                Button(action: { dismiss() }) {
                    Image(systemName: "chevron.down")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()

                Text("\(symbol) Dividend History")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                // Invisible spacer to center title
                Image(systemName: "chevron.down")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(.clear)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)

            // Column headers
            HStack {
                Text("Ex-Div Date")
                    .font(AppTypography.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity, alignment: .leading)

                Text("Dividend Per Share")
                    .font(AppTypography.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .background(AppColors.cardBackground)

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)

            // Dividend list
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(dividendHistory) { payment in
                        HStack {
                            Text(payment.exDividendDate)
                                .font(AppTypography.footnote)
                                .foregroundColor(AppColors.textSecondary)
                                .frame(maxWidth: .infinity, alignment: .leading)

                            Text(payment.dividendPerShare)
                                .font(AppTypography.footnote)
                                .fontWeight(.semibold)
                                .foregroundColor(AppColors.textSecondary)
                                .frame(maxWidth: .infinity, alignment: .trailing)
                        }
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.md)

                        Rectangle()
                            .fill(AppColors.cardBackgroundLight.opacity(0.5))
                            .frame(height: 1)
                    }
                }
            }
        }
        .background(AppColors.background)
        .preferredColorScheme(.dark)
    }
}

#Preview {
    ETFDividendHistoryView(
        symbol: "SPY",
        dividendHistory: ETFDetailData.sampleSPY.netYield.dividendHistory
    )
}
