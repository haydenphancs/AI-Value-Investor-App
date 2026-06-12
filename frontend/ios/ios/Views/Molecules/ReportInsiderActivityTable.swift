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

    // When expanded, the recent-transactions list scrolls INSIDE this bounded
    // height instead of stretching the whole report (mirrors the Holders tab's
    // RecentActivitiesSection, which caps its expanded list and scrolls in place).
    private let expandedListHeight: CGFloat = 420

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

            // Buys/Sells table — gray card (matches the Capital Allocation
            // metrics card: cardBackgroundLight + rounded corner + md padding).
            VStack(spacing: AppSpacing.sm) {
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
            }
            .padding(AppSpacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackgroundLight)
            )

            // The red `ownership_note` banner that used to live here was
            // removed — it paraphrased the buy/sell table directly above
            // ("Insiders dumped $2.6M…") so it added no information.
            // Interpretation now lives in the Key Management Insight
            // (ReportKeyManagementTable), which anchors in the dominant
            // holder's stake instead of restating the table.

            // Insider trend (12-mo buy/sell) + recent transactions — reused from
            // the Holders tab (same numbers). Price line overlaid on the bars
            // (like the Holders tab) so you can read whether insiders sold into
            // strength or weakness; the backend windows the daily price to the
            // bars' 365-day span so the line and bars share one timeline.
            if let flow = insiderData.insiderFlow, !flow.flowData.isEmpty {
                SmartMoneyFlowChart(
                    priceData: flow.priceData,
                    dailyPrices: flow.dailyPrices,
                    flowData: flow.flowData,
                    showPriceChart: true,
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

                // Expanded → the full list scrolls inside a bounded box (mirrors
                // the Holders tab) so "Show more" doesn't lengthen the whole
                // report. Collapsed → top 3 inline.
                if showAllTransactions {
                    ScrollView {
                        LazyVStack(spacing: AppSpacing.md) {
                            ForEach(insiderData.recentTransactions) { tx in
                                transactionRow(tx)
                            }
                        }
                    }
                    .scrollIndicators(.visible)
                    .frame(maxHeight: expandedListHeight)
                } else {
                    VStack(spacing: AppSpacing.md) {
                        ForEach(Array(insiderData.recentTransactions.prefix(3))) { tx in
                            transactionRow(tx)
                        }
                    }
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

    // One recent-transaction row — shared by the collapsed (top-3) and the
    // expanded scrollable list so they render identically.
    @ViewBuilder
    private func transactionRow(_ tx: InsiderActivity) -> some View {
        ReportListRow(
            leftPrimary: tx.name,
            leftLines: [
                ReportRowText(text: tx.title),
                ReportRowText(text: tx.formattedDate),
            ],
            rightLines: [
                ReportRowText(text: tx.formattedChange, color: tx.changeColor, isPrimary: true),
                ReportRowText(text: tx.transactionType.rawValue, color: tx.transactionType.color),
                ReportRowText(text: tx.formattedPrice),
            ]
        )
    }
}

#Preview {
    ReportInsiderActivityTable(insiderData: TickerReportData.sampleOracle.insiderData)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
