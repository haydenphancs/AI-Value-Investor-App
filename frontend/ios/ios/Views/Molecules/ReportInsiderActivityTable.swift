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
                    .font(AppTypography.bodySmallEmphasis)
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
                        .font(AppTypography.label)
                        .foregroundColor(transaction.type == "Buys" ? AppColors.bullish : AppColors.bearish)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    Text("\(transaction.count)")
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 50, alignment: .center)

                    Text(transaction.shares)
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 50, alignment: .center)

                    Text(transaction.value)
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 60, alignment: .trailing)
                }
            }

            // The red `ownership_note` banner that used to live here was
            // removed — it paraphrased the buy/sell table directly above
            // ("Insiders dumped $2.6M…") so it added no information.
            // Interpretation now lives in the Key Management Insight
            // (ReportKeyManagementTable), which anchors in the dominant
            // holder's stake instead of restating the table.
        }
    }
}

#Preview {
    ReportInsiderActivityTable(insiderData: TickerReportData.sampleOracle.insiderData)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
