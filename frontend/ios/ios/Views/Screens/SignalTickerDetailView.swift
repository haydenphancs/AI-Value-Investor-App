//
//  SignalTickerDetailView.swift
//  ios
//
//  Screen: per-ticker drill-down reached by tapping a ticker in the Home
//  "Whale Accumulation" or "Congressional Buys" signal cards. Shows WHO bought/
//  added the ticker, WHEN, and HOW MUCH. The ticker header is tappable →
//  TickerDetailView; registry holders are tappable → WhaleProfileView.
//  Style mirrors AlertDetailView ("Whales Sold").
//

import SwiftUI

struct SignalTickerDetailView: View {
    @StateObject private var viewModel: SignalTickerDetailViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var navigateToWhaleId: String?      // registry holder → profile (push)
    @State private var selectedTicker: MarketTicker?   // ticker header → TickerDetailView (cover)

    init(kind: String, ticker: String) {
        _viewModel = StateObject(
            wrappedValue: SignalTickerDetailViewModel(kind: kind, ticker: ticker)
        )
    }

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()
            content
        }
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(true)
        .toolbar {
            ToolbarItem(placement: .navigationBarLeading) {
                Button { dismiss() } label: {
                    Image(systemName: "chevron.left")
                        .font(AppTypography.iconSmall).fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                }
            }
            ToolbarItem(placement: .principal) {
                Text(viewModel.ticker)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
            }
        }
        .task { await viewModel.load() }
        // Registry holder → their profile (push within this NavigationStack).
        .navigationDestination(item: $navigateToWhaleId) { whaleId in
            WhaleProfileView(whaleId: whaleId)
        }
        // Ticker header → full TickerDetailView (same router pattern as HomeDashboardView).
        .fullScreenCover(item: $selectedTicker) { ticker in
            NavigationStack {
                TickerDetailView(tickerSymbol: ticker.symbol)
                    .navigationBarHidden(true)
            }
            .preferredColorScheme(.dark)
        }
    }

    // MARK: - Content states

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading && viewModel.detail == nil {
            ProgressView().tint(AppColors.primaryBlue)
        } else if let error = viewModel.errorMessage, viewModel.detail == nil {
            errorState(error)
        } else if let detail = viewModel.detail {
            ScrollView(showsIndicators: false) {
                VStack(spacing: AppSpacing.xl) {
                    header(detail)
                    if detail.isEmpty {
                        emptyState(detail.emptyText)
                    } else {
                        VStack(spacing: AppSpacing.sm) {
                            ForEach(detail.holders) { holder in
                                SignalHolderRow(holder: holder) {
                                    if let wid = holder.whaleId { navigateToWhaleId = wid }
                                }
                            }
                        }
                    }
                    Spacer().frame(height: 40)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.md)
            }
        } else {
            Color.clear
        }
    }

    // MARK: - Header (tappable ticker → TickerDetailView)

    private func header(_ detail: SignalTickerDetail) -> some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Button {
                    selectedTicker = MarketTicker(
                        name: detail.companyName.isEmpty ? detail.symbol : detail.companyName,
                        symbol: detail.symbol,
                        type: .stock,
                        price: 0,
                        changePercent: 0,
                        sparklineData: []
                    )
                } label: {
                    HStack(spacing: 4) {
                        Text(detail.symbol)
                            .font(AppTypography.title)
                            .foregroundColor(AppColors.primaryBlue)
                        Image(systemName: "chevron.right")
                            .font(AppTypography.iconSmall)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                }
                .buttonStyle(.plain)

                if !detail.companyName.isEmpty {
                    Text(detail.companyName)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(1)
                }

                Text(detail.subtitleLine)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            Spacer(minLength: AppSpacing.md)

            VStack(alignment: .trailing, spacing: 4) {
                if !detail.priceText.isEmpty {
                    Text(detail.priceText)
                        .font(AppTypography.dataTitle)
                        .foregroundColor(AppColors.textPrimary)
                }
                if !detail.marketCapText.isEmpty {
                    Text(detail.marketCapText)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    // MARK: - Empty / error

    private func emptyState(_ text: String) -> some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "tray")
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.textMuted)
            Text(text)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xxl)
    }

    private func errorState(_ message: String) -> some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "wifi.exclamationmark")
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.neutral)
            Text(message)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
            Button("Retry") { Task { await viewModel.load() } }
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.primaryBlue)
        }
        .frame(maxWidth: .infinity)
        .padding(AppSpacing.xl)
    }
}
