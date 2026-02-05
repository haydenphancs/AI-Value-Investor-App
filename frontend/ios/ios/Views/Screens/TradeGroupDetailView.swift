//
//  TradeGroupDetailView.swift
//  ios
//
//  Detail screen for a whale's trade group batch showing
//  individual trades with filter tabs and insights.
//

import SwiftUI

// MARK: - Trade Group Detail View
struct TradeGroupDetailView: View {
    @StateObject private var viewModel: TradeGroupDetailViewModel
    @Environment(\.dismiss) private var dismiss

    init(tradeGroup: WhaleTradeGroup, whaleName: String) {
        _viewModel = StateObject(wrappedValue: TradeGroupDetailViewModel(
            tradeGroup: tradeGroup,
            whaleName: whaleName
        ))
    }

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                VStack(spacing: AppSpacing.xl) {
                    // Header
                    TradeGroupDetailHeader(
                        date: viewModel.tradeGroup.formattedDateFull,
                        whaleName: viewModel.whaleName
                    )

                    // Filter Tabs
                    TradeFilterTabBar(
                        selectedFilter: viewModel.selectedFilter,
                        filterCounts: viewModel.filterCounts,
                        onSelect: { viewModel.selectFilter($0) }
                    )

                    // Insights Card
                    if !viewModel.tradeGroup.insights.isEmpty {
                        TradeGroupInsightsCard(insights: viewModel.tradeGroup.insights)
                    }

                    // Trade Cards
                    VStack(spacing: AppSpacing.md) {
                        ForEach(viewModel.filteredTrades) { trade in
                            TradeDetailCard(
                                trade: trade,
                                onTap: { viewModel.viewTrade(trade) }
                            )
                        }
                    }

                    // Empty state
                    if viewModel.filteredTrades.isEmpty {
                        VStack(spacing: AppSpacing.md) {
                            Image(systemName: "tray")
                                .font(.system(size: 36))
                                .foregroundColor(AppColors.textMuted)

                            Text("No trades in this category")
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textSecondary)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.xxl)
                    }

                    Spacer().frame(height: 40)
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
        .navigationBarBackButtonHidden(true)
        .toolbar {
            ToolbarItem(placement: .navigationBarLeading) {
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(AppColors.textPrimary)
                }
            }
        }
        .navigationDestination(item: $viewModel.selectedTickerSymbol) { ticker in
            TickerDetailView(tickerSymbol: ticker)
        }
    }
}

// MARK: - Trade Group Detail Header
struct TradeGroupDetailHeader: View {
    let date: String
    let whaleName: String

    var body: some View {
        VStack(spacing: AppSpacing.xs) {
            Text(date)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)

            Text(whaleName)
                .font(AppTypography.title)
                .foregroundColor(AppColors.textPrimary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.sm)
    }
}

// MARK: - Filter Tab Bar
struct TradeFilterTabBar: View {
    let selectedFilter: TradeFilterTab
    let filterCounts: [TradeFilterTab: Int]
    var onSelect: ((TradeFilterTab) -> Void)?

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: AppSpacing.sm) {
                ForEach(TradeFilterTab.allCases, id: \.self) { tab in
                    TradeFilterPill(
                        tab: tab,
                        isSelected: selectedFilter == tab,
                        count: filterCounts[tab] ?? 0,
                        onTap: { onSelect?(tab) }
                    )
                }
            }
        }
    }
}

// MARK: - Filter Pill
struct TradeFilterPill: View {
    let tab: TradeFilterTab
    let isSelected: Bool
    let count: Int
    var onTap: (() -> Void)?

    // Hide tabs with zero trades (except "All Trades")
    private var shouldShow: Bool {
        tab == .all || count > 0
    }

    var body: some View {
        if shouldShow {
            Button {
                onTap?()
            } label: {
                HStack(spacing: AppSpacing.xs) {
                    if let iconName = tab.iconName {
                        Image(systemName: iconName)
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(isSelected ? .white : tab.iconColor)
                    }

                    Text(tab.rawValue)
                        .font(AppTypography.captionBold)
                }
                .foregroundColor(isSelected ? .white : AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    isSelected
                        ? AppColors.primaryBlue
                        : AppColors.cardBackgroundLight
                )
                .cornerRadius(AppCornerRadius.pill)
            }
            .buttonStyle(.plain)
        }
    }
}

// MARK: - Insights Card
struct TradeGroupInsightsCard: View {
    let insights: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            ForEach(Array(insights.enumerated()), id: \.offset) { _, insight in
                HStack(alignment: .top, spacing: AppSpacing.sm) {
                    Circle()
                        .fill(AppColors.primaryBlue)
                        .frame(width: 6, height: 6)
                        .padding(.top, 6)

                    Text(insight)
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(2)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Trade Detail Card
struct TradeDetailCard: View {
    let trade: WhaleTrade
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: AppSpacing.md) {
                // Logo placeholder
                TradeTickerLogo(ticker: trade.ticker)

                // Ticker + allocation change
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(trade.ticker)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)

                    AllocationChangeText(allocationChange: trade.formattedAllocationChange)
                }

                Spacer()

                // Action badge + amount
                VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                    TradeActionBadge(action: trade.action)

                    Text(trade.formattedAmount)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)
                }
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Allocation Change Text
struct AllocationChangeText: View {
    let allocationChange: String
    
    var body: some View {
        // Parse the allocation change string (e.g., "0% → 1.5%")
        let components = allocationChange.components(separatedBy: "→")
        
        if components.count == 2 {
            HStack(spacing: 2) {
                Text(components[0].trimmingCharacters(in: .whitespaces))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                
                Text("→")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                
                Text(components[1].trimmingCharacters(in: .whitespaces))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textPrimary)
            }
        } else {
            // Fallback if format doesn't match
            Text(allocationChange)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }
}

// MARK: - Trade Ticker Logo
struct TradeTickerLogo: View {
    let ticker: String

    private var backgroundColor: Color {
        let colors: [Color] = [
            AppColors.primaryBlue,
            AppColors.bullish,
            AppColors.alertOrange,
            AppColors.alertPurple,
            AppColors.accentCyan
        ]
        let index = abs(ticker.hashValue) % colors.count
        return colors[index]
    }

    var body: some View {
        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
            .fill(backgroundColor.opacity(0.15))
            .frame(width: 48, height: 48)
            .overlay(
                Text("Logo")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(AppColors.textMuted)
            )
    }
}

// MARK: - Trade Action Badge
struct TradeActionBadge: View {
    let action: WhaleTradeAction

    var body: some View {
        Text(action.rawValue)
            .font(.system(size: 11, weight: .bold))
            .foregroundColor(.white)
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.xs)
            .background(action.color)
            .cornerRadius(AppCornerRadius.small)
    }
}

// MARK: - Preview
#Preview {
    NavigationStack {
        TradeGroupDetailView(
            tradeGroup: WhaleProfile.warrenBuffett.recentTradeGroups.first!,
            whaleName: "Warren Buffett"
        )
    }
    .preferredColorScheme(.dark)
}
