//
//  ReportInsiderActivityTable.swift
//  ios
//
//  Molecule: Insider activity table showing buy/sell transactions
//

import SwiftUI

struct ReportInsiderActivityTable: View {
    let insiderData: ReportInsiderData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header with sentiment badge
            HStack {
                Text("Insider Activity")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textSecondary)

                Spacer()

                ReportSentimentBadge(
                    text: insiderData.sentiment.rawValue,
                    textColor: insiderData.sentiment.color,
                    backgroundColor: insiderData.sentiment.backgroundColor
                )
            }

            // Timeframe
            Text(insiderData.timeframe)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            // Table header
            HStack {
                Text("")
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("Count")
                    .frame(width: 50, alignment: .center)
                Text("Shares")
                    .frame(width: 50, alignment: .center)
                Text("Value")
                    .frame(width: 60, alignment: .trailing)
            }
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)

            // Table rows
            ForEach(insiderData.transactions) { transaction in
                HStack {
                    Text(transaction.type)
                        .font(AppTypography.subheadline)
                        .foregroundColor(transaction.type == "Buys" ? AppColors.bullish : AppColors.bearish)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    Text("\(transaction.count)")
                        .font(AppTypography.subheadline)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 50, alignment: .center)

                    Text(transaction.shares)
                        .font(AppTypography.subheadline)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 50, alignment: .center)

                    Text(transaction.value)
                        .font(AppTypography.subheadline)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 60, alignment: .trailing)
                }
            }

            // Ownership note
            if let note = insiderData.ownershipNote {
                Text(note)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.bearish)
                    .padding(AppSpacing.sm)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.small)
                            .fill(AppColors.bearish.opacity(0.1))
                    )
            }
        }
    }
}

#Preview {
    ReportInsiderActivityTable(insiderData: TickerReportData.sampleOracle.insiderData)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
