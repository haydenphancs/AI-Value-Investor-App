//
//  AlertDetailView.swift
//  ios
//
//  Detail screen for alert events (earnings, market, smart money)
//

import SwiftUI

struct AlertDetailView: View {
    let alert: AppAlert

    /// Where the user chose to go. REPORTED UP, never navigated to from here.
    ///
    /// This screen used to push destinations onto its own (sheet's) `NavigationStack`, which
    /// meant `TickerDetailView` — seven `.sheet` modifiers deep — and `WhaleProfileView` (five)
    /// ran inside a sheet, where their own sheets cannot present. The owner of the sheet now
    /// closes it and opens the destination in a cover; see `AlertDestinationCover`.
    ///
    /// ONE channel for both. The lead-whale row used to have a `navigateToWhaleId: String?` of
    /// its own — a data row that happens to navigate, versus a pure destination row — but
    /// `AlertDestination.Target` already models `.whale`, and two channels here meant two
    /// pushes to keep correct.
    var onOpen: (AlertDestination) -> Void

    @Environment(\.dismiss) private var dismiss

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
        // Chrome this screen was missing. It was the documented outlier: presented via
        // `.sheet(item:)` with no Done button, no detents and no drag indicator, so the only way
        // out was a swipe nothing on screen advertised. Matched to the ~19-site convention, and
        // to `NotificationDetailView`, so both halves of Activity dismiss the same way.
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Done") { dismiss() }
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
            }
        }
    }

    /// The "go here" rows for one item of a roll-up.
    ///
    /// Per ITEM, not one action for the card: a roll-up spans several tickers ("AAPL, CRM and 2
    /// more"), so a single button at the bottom could only ever mean one of them.
    @ViewBuilder
    private func destinationRows(for ticker: String) -> some View {
        ForEach(AlertDestination.destinations(forRollupItem: ticker, in: alert)) { destination in
            AlertDestinationRow(destination: destination) {
                onOpen(destination)
            }
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
            destinationRows(for: data.ticker)
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
    }

    // MARK: - Market Detail

    private func marketDetail(_ data: AppAlert.MarketData) -> some View {
        VStack(spacing: AppSpacing.md) {
            detailRow(label: "Event", value: data.eventName)
            detailRow(label: "Details", value: data.description)
            detailRow(label: "Date", value: "\(data.formattedMonth) \(data.formattedDay)")
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
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
                    destinationRows(for: item.ticker)
                }
            }
        }
    }

    @ViewBuilder
    private func leadWhaleRow(name: String, firm: String?, whaleId: String?) -> some View {
        if let whaleId {
            Button {
                onOpen(AlertDestination(
                    label: name,
                    systemImage: "person.crop.circle",
                    target: .whale(id: whaleId)
                ))
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
                    destinationRows(for: item.ticker)
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
                    destinationRows(for: item.ticker)
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
        .cardSurface(cornerRadius: AppCornerRadius.large)
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
        AlertDetailView(alert: AppAlert.sampleData[0]) { _ in }
    }
}
