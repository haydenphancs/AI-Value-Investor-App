//
//  NewsDetailView.swift
//  ios
//
//  Main News Detail screen displaying full article information
//

import SwiftUI

struct NewsDetailView: View {
    @StateObject private var viewModel: NewsDetailViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showShareSheet = false
    @State private var showMoreOptions = false
    /// Ticker the user tapped in the Related Tickers row. Navigation from a news
    /// article into ticker detail is not wired into this nav tree yet.
    @State private var pendingTicker: String?
    /// Stable token keying this screen's audio overlay host registration.
    @State private var compactToken = UUID().uuidString

    let article: NewsArticle

    /// Backend scope this article came from, so on-demand AI enrichment targets
    /// the right cache partition. Defaults to the market feed.
    init(article: NewsArticle, scope: String = UpdatesScope.market) {
        self.article = article
        self._viewModel = StateObject(
            wrappedValue: NewsDetailViewModel(article: article, scope: scope)
        )
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main Content
            VStack(spacing: 0) {
                // Header
                NewsDetailHeader(
                    source: article.source,
                    onBackTapped: handleBackTapped,
                    onMoreTapped: handleMoreTapped
                )

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Scrollable Content
                ScrollView(showsIndicators: false) {
                    if let articleDetail = viewModel.articleDetail {
                        NewsDetailContent(
                            article: articleDetail,
                            isEnriching: viewModel.isEnriching,
                            onTickerTapped: handleTickerTapped
                        )
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.top, AppSpacing.lg)
                    } else {
                        // Loading placeholder
                        loadingPlaceholder
                    }

                    // Bottom spacing for button
                    Spacer()
                        .frame(height: 100)
                }
                .refreshable {
                    await viewModel.refresh()
                }
            }

            // "Read full story" only when there IS a story to open. It used to
            // render unconditionally and pointed at https://example.com/article.
            if viewModel.articleDetail?.articleURL != nil {
                readFullStoryButton
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.xxl)
            }
        }
        .preferredColorScheme(.dark)
        .navigationBarHidden(true)
        // Keep the audio player visible above this fullScreenCover (bottom mini player; content is
        // inset so the fixed "Read Full Story" button isn't covered).
        .globalAudioOverlay(token: compactToken, showBottomMiniPlayer: true)
        .task {
            await viewModel.load()
        }
        .gesture(
            DragGesture()
                .onEnded { value in
                    // Swipe right to dismiss
                    if value.translation.width > 100 {
                        handleBackTapped()
                    }
                }
        )
        // "Save Article" and "Report Issue" were removed: both were `print()`
        // TODOs, so tapping them did nothing while looking like a real action.
        .confirmationDialog("Options", isPresented: $showMoreOptions) {
            if viewModel.articleDetail?.articleURL != nil {
                Button("Share Article") {
                    showShareSheet = true
                }
            }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(isPresented: $showShareSheet) {
            if let url = viewModel.articleDetail?.articleURL {
                ShareSheet(items: [url])
            }
        }
    }

    // MARK: - Subviews

    private var readFullStoryButton: some View {
        Button(action: handleReadFullStory) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "arrow.up.right.square")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)

                Text("Read full story")
                    .font(AppTypography.bodyEmphasis)
            }
            .foregroundColor(AppColors.textPrimary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.lg)
            .background(AppColors.primaryBlue)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(PlainButtonStyle())
    }

    private var loadingPlaceholder: some View {
        VStack(spacing: AppSpacing.lg) {
            // Headline placeholder
            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 60)

            // Meta row placeholder
            HStack {
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 100, height: 20)
                Spacer()
            }

            // Image placeholder
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 220)

            // Takeaways placeholder
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 300)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
        .shimmer()
    }

    // MARK: - Action Handlers

    private func handleBackTapped() {
        dismiss()
    }

    private func handleMoreTapped() {
        showMoreOptions = true
    }

    private func handleTickerTapped(_ ticker: String) {
        // Ticker navigation from a news article isn't wired into the nav tree
        // yet; the row stays non-interactive rather than silently doing nothing.
        pendingTicker = ticker
    }

    private func handleReadFullStory() {
        viewModel.openFullStory()
    }
}

// MARK: - Preview
struct NewsDetailViewStandalone: View {
    var body: some View {
        NewsDetailView(
            article: NewsArticle(
                headline: "NVIDIA Announces Record Q4 Earnings, Missed Expectations and CEO step down",
                summary: nil,
                source: NewsSource(name: "CNBC", iconName: nil),
                sentiment: .negative,
                publishedAt: Date(),
                thumbnailName: nil,
                relatedTickers: ["AAPL", "ORCL", "TSLA"]
            )
        )
    }
}

#Preview {
    NewsDetailViewStandalone()
}
