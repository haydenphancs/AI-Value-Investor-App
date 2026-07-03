//
//  AlertDetailView.swift
//  ios
//
//  Detail screen for alert events (earnings, market, smart money)
//

import SwiftUI

struct AlertDetailView: View {
    let alert: AppAlert
    @Environment(\.dismiss) private var dismiss
    @State private var navigateToWhaleId: String?

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                VStack(spacing: AppSpacing.xl) {
                    // Header Icon
                    headerIcon
                        .padding(.top, AppSpacing.xxl)

                    // Title & Description
                    VStack(spacing: AppSpacing.sm) {
                        Text(alert.title)
                            .font(AppTypography.titleCompact)
                            .foregroundColor(AppColors.textPrimary)

                        Text(alert.description)
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, AppSpacing.lg)
                    }

                    // Type-specific content
                    detailContent
                        .padding(.horizontal, AppSpacing.lg)

                    Spacer()
                        .frame(height: 40)
                }
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text(alert.title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
            }
        }
        .navigationDestination(item: $navigateToWhaleId) { whaleId in
            WhaleProfileView(whaleId: whaleId)
        }
    }

    // MARK: - Header Icon

    private var headerIcon: some View {
        ZStack {
            Circle()
                .fill(alert.iconColor.opacity(0.15))
                .frame(width: 72, height: 72)

            Image(systemName: alert.iconName)
                .font(AppTypography.iconDisplay).fontWeight(.semibold)
                .foregroundColor(alert.iconColor)
        }
    }

    // MARK: - Detail Content

    @ViewBuilder
    private var detailContent: some View {
        switch alert {
        case .earnings(let data):
            earningsDetail(data)
        case .market(let data):
            marketDetail(data)
        case .whaleTrade(let data):
            whaleTradeDetail(data)
        case .analystRating(let data):
            analystRatingDetail(data)
        case .insiderTransaction(let data):
            insiderTransactionDetail(data)
        }
    }

    // MARK: - Earnings Detail

    private func earningsDetail(_ data: AppAlert.EarningsData) -> some View {
        VStack(spacing: AppSpacing.md) {
            detailRow(label: "Ticker", value: data.ticker)
            detailRow(label: "Company", value: data.companyName)
            if let timing = data.reportTime {
                detailRow(label: "Report Time", value: timing.displayText.capitalized)
            }
            if !data.consensus.isEmpty {
                detailRow(label: "Consensus", value: data.consensus)
            }
            detailRow(label: "Date", value: "\(data.formattedMonth) \(data.formattedDay)")
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    // MARK: - Market Detail

    private func marketDetail(_ data: AppAlert.MarketData) -> some View {
        VStack(spacing: AppSpacing.md) {
            detailRow(label: "Event", value: data.eventName)
            detailRow(label: "Details", value: data.description)
            detailRow(label: "Date", value: "\(data.formattedMonth) \(data.formattedDay)")
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    // MARK: - Whale Trade Detail

    private func whaleTradeDetail(_ data: AppAlert.WhaleTradeAlertData) -> some View {
        VStack(spacing: AppSpacing.md) {
            summaryCard {
                detailRow(label: "Action", value: data.action.rawValue)
                detailRow(label: "Tickers", value: "\(data.items.count)")
                detailRow(label: "Total Amount", value: data.totalAmount)
                detailRow(label: "Window", value: data.timeWindowLabel.capitalized)
            }

            ForEach(data.items) { item in
                summaryCard {
                    detailRow(label: "Ticker", value: item.ticker)
                    detailRow(label: "Company", value: item.companyName)
                    detailRow(label: "Whales", value: "\(item.whaleCount)")
                    if let lead = item.leadWhaleName {
                        leadWhaleRow(name: lead, firm: item.leadWhaleFirm, whaleId: item.leadWhaleId)
                    }
                    detailRow(label: "Amount", value: item.amount)
                }
            }
        }
    }

    @ViewBuilder
    private func leadWhaleRow(name: String, firm: String?, whaleId: String?) -> some View {
        if let whaleId {
            Button {
                navigateToWhaleId = whaleId
            } label: {
                HStack {
                    Text("Lead Whale")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textMuted)
                    Spacer()
                    // Person-fronted whales always show name + firm together.
                    VStack(alignment: .trailing, spacing: 1) {
                        Text(name)
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.primaryBlue)
                        if let firm, !firm.isEmpty {
                            Text(firm)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }
                    Image(systemName: "chevron.right")
                        .font(AppTypography.iconSmall)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
            .buttonStyle(.plain)
        } else if let firm, !firm.isEmpty {
            detailRow(label: "Lead Whale", value: "\(name) · \(firm)")
        } else {
            detailRow(label: "Lead Whale", value: name)
        }
    }

    // MARK: - Analyst Rating Detail

    private func analystRatingDetail(_ data: AppAlert.AnalystRatingAlertData) -> some View {
        VStack(spacing: AppSpacing.md) {
            summaryCard {
                detailRow(label: "Changes", value: "\(data.items.count)")
                detailRow(label: "Window", value: data.timeWindowLabel.capitalized)
            }

            ForEach(data.items) { item in
                summaryCard {
                    detailRow(label: "Ticker", value: item.ticker)
                    detailRow(label: "Firm", value: item.firmName)
                    detailRow(label: "Action", value: item.action.rawValue.capitalized)
                    if let prev = item.previousRating {
                        detailRow(label: "Rating", value: "\(prev) → \(item.newRating)")
                    } else {
                        detailRow(label: "Rating", value: item.newRating)
                    }
                    if let pt = item.priceTarget {
                        let ptStr = "$\(Int(pt))"
                        if let prevPt = item.previousPriceTarget {
                            detailRow(label: "Price Target", value: "$\(Int(prevPt)) → \(ptStr)")
                        } else {
                            detailRow(label: "Price Target", value: ptStr)
                        }
                    }
                    if item.day > 0 {
                        detailRow(label: "Date", value: "\(item.formattedMonth) \(item.formattedDay)")
                    }
                }
            }
        }
    }

    // MARK: - Insider Transaction Detail

    private func insiderTransactionDetail(_ data: AppAlert.InsiderTransactionAlertData) -> some View {
        VStack(spacing: AppSpacing.md) {
            summaryCard {
                detailRow(label: "Action", value: data.action.rawValue)
                detailRow(label: "Insiders", value: "\(data.items.count)")
                detailRow(label: "Total Amount", value: data.totalAmount)
                detailRow(label: "Window", value: data.timeWindowLabel.capitalized)
            }

            ForEach(data.items) { item in
                summaryCard {
                    detailRow(label: "Ticker", value: item.ticker)
                    detailRow(label: "Insider", value: item.insiderName)
                    detailRow(label: "Title", value: item.insiderTitle)
                    detailRow(label: "Amount", value: item.amount)
                    if item.day > 0 {
                        detailRow(label: "Date", value: "\(item.formattedMonth) \(item.formattedDay)")
                    }
                }
            }
        }
    }

    // MARK: - Summary Card Wrapper

    private func summaryCard<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(spacing: AppSpacing.md) {
            content()
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    // MARK: - Detail Row

    private func detailRow(label: String, value: String) -> some View {
        HStack {
            Text(label)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(value)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}

#Preview {
    NavigationStack {
        AlertDetailView(alert: AppAlert.sampleData[0])
    }
    .preferredColorScheme(.dark)
}
