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

    let article: NewsArticle

    init(article: NewsArticle) {
        self.article = article
        self._viewModel = StateObject(wrappedValue: NewsDetailViewModel(article: article))
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

            // Read Full Story Button (Fixed at bottom)
            if viewModel.articleDetail != nil {
                readFullStoryButton
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.xxl)
            }

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .preferredColorScheme(.dark)
        .navigationBarHidden(true)
        .task {
            viewModel.loadArticleDetail()
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
        .confirmationDialog("Options", isPresented: $showMoreOptions) {
            Button("Share Article") {
                showShareSheet = true
            }
            Button("Save Article") {
                handleSaveArticle()
            }
            Button("Report Issue") {
                handleReportIssue()
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
                    .font(.system(size: 16, weight: .semibold))

                Text("Read full story")
                    .font(AppTypography.bodyBold)
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
        print("Navigate to ticker: \(ticker)")
        // TODO: Navigate to ticker detail screen
    }

    private func handleReadFullStory() {
        viewModel.openFullStory()
    }

    private func handleSaveArticle() {
        print("Save article")
        // TODO: Implement save functionality
    }

    private func handleReportIssue() {
        print("Report issue")
        // TODO: Implement report functionality
    }
}

// MARK: - Share Sheet (UIKit Bridge)
struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

// MARK: - Shimmer Effect
extension View {
    func shimmer() -> some View {
        self.modifier(ShimmerModifier())
    }
}

struct ShimmerModifier: ViewModifier {
    @State private var phase: CGFloat = 0

    func body(content: Content) -> some View {
        content
            .overlay(
                GeometryReader { geometry in
                    LinearGradient(
                        gradient: Gradient(colors: [
                            Color.clear,
                            Color.white.opacity(0.1),
                            Color.clear
                        ]),
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                    .frame(width: geometry.size.width * 2)
                    .offset(x: -geometry.size.width + phase * geometry.size.width * 2)
                    .onAppear {
                        withAnimation(
                            Animation.linear(duration: 1.5)
                                .repeatForever(autoreverses: false)
                        ) {
                            phase = 1
                        }
                    }
                }
            )
            .clipped()
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
                relatedTickers: ["APPL", "ORCL", "TSLA"]
            )
        )
    }
}

#Preview {
    NewsDetailViewStandalone()
}
