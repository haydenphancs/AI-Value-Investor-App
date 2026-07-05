//
//  ReportInsiderActivityTable.swift
//  ios
//
//  Molecule: Insider activity table showing buy/sell transactions
//

import SwiftUI

struct ReportInsiderActivityTable: View {
    let insiderData: ReportInsiderData
    /// Tapped insider-chart month, owned by the parent section so a tap outside
    /// the column dismisses the popup (mirrors the Capital Allocation chart).
    @Binding var selectedInsiderPeriod: String?
    @State private var showAllTransactions = false

    init(insiderData: ReportInsiderData, selectedInsiderPeriod: Binding<String?> = .constant(nil)) {
        self.insiderData = insiderData
        self._selectedInsiderPeriod = selectedInsiderPeriod
    }

    // When expanded, the recent-transactions list scrolls INSIDE this bounded
    // height instead of stretching the whole report (mirrors the Holders tab's
    // RecentActivitiesSection, which caps its expanded list and scrolls in place).
    private let expandedListHeight: CGFloat = 420

    /// Per-month informative buy/sell transaction counts, keyed to match the
    /// insider flow bars' "MM/YYYY" months. Derived from `recentTransactions`
    /// (the SAME full, 365-day, informative-only set the bars aggregate, per the
    /// report collector), so the popup counts line up with the columns. UTC
    /// bucketing matches the backend's date-string month key.
    private var monthlyInsiderCounts: [String: (buy: Int, sell: Int)] {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC") ?? .current
        var counts: [String: (buy: Int, sell: Int)] = [:]
        for tx in insiderData.recentTransactions {
            let c = cal.dateComponents([.year, .month], from: tx.date)
            guard let y = c.year, let m = c.month else { continue }
            let key = String(format: "%02d/%04d", m, y)
            var entry = counts[key] ?? (buy: 0, sell: 0)
            if tx.changeInMillions >= 0 { entry.buy += 1 } else { entry.sell += 1 }
            counts[key] = entry
        }
        return counts
    }

    /// Whether there's any insider signal to render — informative buy/sell
    /// counts, a flow chart, or recent transactions. When false, the section
    /// shows an explicit empty state instead of an all-zero "Buys 0 / Sells 0"
    /// table (which reads as an error rather than "no activity").
    private var hasInsiderActivity: Bool {
        insiderData.transactions.contains { $0.count > 0 }
            || !insiderData.recentTransactions.isEmpty
            || (insiderData.insiderFlow.map { !$0.flowData.isEmpty } ?? false)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header with sentiment badge. The badge is hidden when there's no
            // activity — a "Balanced" pill over an all-zero table is noise.
            HStack {
                Text("Insider Activity")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textSecondary)

                Spacer()

                if hasInsiderActivity {
                    ReportSentimentBadge(
                        text: insiderData.sentiment.rawValue,
                        textColor: insiderData.sentiment.color,
                        backgroundColor: insiderData.sentiment.backgroundColor
                    )
                }
            }

            // Timeframe
            Text(insiderData.timeframe)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            if hasInsiderActivity {
                insiderContent
            } else {
                // Empty state — the "Insider Activity" title + timeframe stay
                // visible so an absent table reads as "no data", not an error
                // (mirrors the Congressional Trades empty state).
                Text("No insider transactions in the last 12 months.")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, AppSpacing.xs)
            }
        }
    }

    /// The full insider readout — buy/sell table, flow chart, recent
    /// transactions — rendered only when `hasInsiderActivity`.
    @ViewBuilder
    private var insiderContent: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
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
                    showVolumeYAxis: true,
                    monthlyCounts: monthlyInsiderCounts,
                    selectedMonth: $selectedInsiderPeriod
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
