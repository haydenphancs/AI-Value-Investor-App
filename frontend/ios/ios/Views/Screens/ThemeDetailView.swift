//
//  ThemeDetailView.swift
//  ios
//
//  Screen: the Emerging Frontiers theme drill-down, opened by tapping a theme
//  card on Home. A hero image with the theme title + subtitle overlaid, then a
//  "Companies" list of the theme's constituents (logo + name + ticker + current
//  price + green/red daily change), each tappable → the stock's TickerDetailView.
//  All content is server-driven (GET /home/themes/{slug}).
//

import SwiftUI

struct ThemeDetailView: View {
    @StateObject private var viewModel: ThemeDetailViewModel
    @Environment(\.dismiss) private var dismiss

    /// A tapped company row → the full TickerDetailView (same router as Home).
    @State private var selectedTicker: MarketTicker?

    init(slug: String) {
        _viewModel = StateObject(wrappedValue: ThemeDetailViewModel(slug: slug))
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            AppColors.background.ignoresSafeArea()
            content
            backButton
        }
        .navigationBarHidden(true)
        .task { await viewModel.load() }
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
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let error = viewModel.errorMessage, viewModel.detail == nil {
            errorState(error)
        } else if let detail = viewModel.detail {
            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    hero(detail)

                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        Text("Companies")
                            .font(AppTypography.heading)
                            .foregroundColor(AppColors.textPrimary)
                            .padding(.horizontal, AppSpacing.lg)

                        if detail.isEmpty {
                            emptyState
                        } else {
                            companyList(detail.companies)
                                .padding(.horizontal, AppSpacing.lg)
                        }
                    }

                    Spacer().frame(height: 40)
                }
            }
            .ignoresSafeArea(edges: .top)   // hero bleeds under the status bar
        } else {
            Color.clear
        }
    }

    // MARK: - Hero (image + scrim + overlaid title/subtitle)

    private func hero(_ detail: ThemeDetail) -> some View {
        ZStack(alignment: .bottomLeading) {
            heroImage(detail)

            LinearGradient(
                colors: [.clear, .black.opacity(0.15), .black.opacity(0.78)],
                startPoint: .top, endPoint: .bottom
            )

            VStack(alignment: .leading, spacing: 6) {
                Text(detail.title)
                    .font(AppTypography.titleLarge)
                    .foregroundColor(.white)
                    .lineLimit(2)
                if !detail.subtitle.isEmpty {
                    Text(detail.subtitle)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(.white.opacity(0.9))
                        .lineLimit(2)
                }
            }
            .padding(AppSpacing.lg)
        }
        .frame(height: 300)
        .frame(maxWidth: .infinity)
        .clipped()
    }

    @ViewBuilder
    private func heroImage(_ detail: ThemeDetail) -> some View {
        if let s = detail.imageUrl, s.hasPrefix("http"), let url = URL(string: s) {
            AsyncImage(url: url) { phase in
                if let image = phase.image {
                    image.resizable().aspectRatio(contentMode: .fill)
                } else {
                    heroFallback(detail)   // loading + error both fall back
                }
            }
        } else {
            heroFallback(detail)
        }
    }

    private func heroFallback(_ detail: ThemeDetail) -> some View {
        LinearGradient(
            colors: [detail.accent.opacity(0.85), detail.accent.opacity(0.25)],
            startPoint: .topLeading, endPoint: .bottomTrailing
        )
    }

    // MARK: - Company list (card with hairline dividers)

    private func companyList(_ companies: [ThemeConstituent]) -> some View {
        VStack(spacing: 0) {
            ForEach(Array(companies.enumerated()), id: \.element.id) { pair in
                ThemeCompanyRow(company: pair.element) {
                    selectedTicker = MarketTicker(
                        name: pair.element.name,
                        symbol: pair.element.ticker,
                        type: .stock,
                        price: 0,
                        changePercent: 0,
                        sparklineData: []
                    )
                }
                if pair.offset < companies.count - 1 {
                    Rectangle()
                        .fill(Color.white.opacity(0.06))
                        .frame(height: 1)
                        .padding(.leading, 68)   // align under the text, past the logo
                }
            }
        }
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Back button (floats over the hero)

    private var backButton: some View {
        Button { dismiss() } label: {
            Image(systemName: "chevron.left")
                .font(AppTypography.iconSmall).fontWeight(.semibold)
                .foregroundColor(.white)
                .frame(width: 36, height: 36)
                .background(Circle().fill(Color.black.opacity(0.35)))
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.sm)
    }

    // MARK: - Empty / error

    private var emptyState: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "tray")
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.textMuted)
            Text("No companies to show yet.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
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
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(AppSpacing.xl)
    }
}

#Preview {
    NavigationStack {
        ThemeDetailView(slug: "silicon-rush")
    }
    .preferredColorScheme(.dark)
}
