//
//  ReportInsiderActivityTable.swift
//  ios
//
//  Molecule: Insider activity table showing buy/sell transactions
//

import SwiftUI

struct ReportInsiderActivityTable: View {
    let insiderData: ReportInsiderData
    @State private var showAllTransactions = false

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

            // Insider trend (12-mo buy/sell) + recent transactions — reused from
            // the Holders tab (same numbers), compact: no price line, top-3 + more.
            if let flow = insiderData.insiderFlow, !flow.flowData.isEmpty {
                SmartMoneyFlowChart(
                    priceData: [],
                    dailyPrices: [],
                    flowData: flow.flowData,
                    showPriceChart: false,
                    showVolumeYAxis: true
                )
                SmartMoneyFlowLegend(buyLabel: "Bought", sellLabel: "Sold")
            }

            if !insiderData.recentTransactions.isEmpty {
                Text("Recent Transactions")
                    .font(AppTypography.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.top, AppSpacing.xs)

                ForEach(showAllTransactions
                        ? insiderData.recentTransactions
                        : Array(insiderData.recentTransactions.prefix(3))) { tx in
                    InsiderActivityRow(activity: tx)
                }

                if insiderData.recentTransactions.count > 3 {
                    Button {
                        withAnimation(.easeInOut(duration: 0.2)) { showAllTransactions.toggle() }
                    } label: {
                        HStack(spacing: AppSpacing.xxs) {
                            Text(showAllTransactions
                                 ? "Show less"
                                 : "Show \(insiderData.recentTransactions.count - 3) more")
                                .font(AppTypography.captionEmphasis)
                            Image(systemName: showAllTransactions ? "chevron.up" : "chevron.down")
                                .font(AppTypography.iconTiny).fontWeight(.semibold)
                        }
                        .foregroundColor(AppColors.primaryBlue)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.xs)
                    }
                }
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
