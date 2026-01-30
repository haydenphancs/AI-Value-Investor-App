//
//  MoneyMoveArticleDetailView.swift
//  ios
//
//  Full article detail screen for Money Move articles
//  Displays hero header, content sections, statistics, comments, and related articles
//

import SwiftUI

struct MoneyMoveArticleDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var isBookmarked: Bool = false
    @State private var isFollowing: Bool = false
    @State private var showShareSheet: Bool = false
    @State private var showMoreOptions: Bool = false
    @State private var scrollOffset: CGFloat = 0

    let article: MoneyMoveArticle

    // Computed property for header opacity based on scroll
    private var headerOpacity: Double {
        let fadeStart: CGFloat = 200
        let fadeEnd: CGFloat = 280
        if scrollOffset < fadeStart { return 0 }
        if scrollOffset > fadeEnd { return 1 }
        return Double((scrollOffset - fadeStart) / (fadeEnd - fadeStart))
    }

    var body: some View {
        ZStack(alignment: .top) {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main scrollable content
            ScrollView(showsIndicators: false) {
                VStack(spacing: 0) {
                    // Hero header
                    MoneyMoveArticleHeroHeader(
                        article: article,
                        onBackTapped: handleBackTapped,
                        onShareTapped: handleShareTapped
                    )

                    // Content
                    MoneyMoveArticleContent(
                        article: article,
                        onAuthorTapped: handleAuthorTapped,
                        onFollowTapped: handleFollowTapped,
                        isFollowing: isFollowing
                    )
                    .padding(.top, AppSpacing.lg)
                }
                .background(
                    GeometryReader { proxy in
                        Color.clear
                            .preference(
                                key: ScrollOffsetPreferenceKey.self,
                                value: -proxy.frame(in: .named("scroll")).origin.y
                            )
                    }
                )
            }
            .coordinateSpace(name: "scroll")
            .onPreferenceChange(ScrollOffsetPreferenceKey.self) { value in
                scrollOffset = value
            }

            // Sticky mini header (appears on scroll)
            if headerOpacity > 0 {
                miniHeader
                    .opacity(headerOpacity)
            }

            // Bottom action bar
            VStack {
                Spacer()
                ArticleActionBar(
                    hasAudioVersion: article.hasAudioVersion,
                    isBookmarked: isBookmarked,
                    onListenTapped: handleListenTapped,
                    onShareTapped: handleShareTapped,
                    onBookmarkTapped: handleBookmarkTapped,
                    onMoreTapped: handleMoreTapped
                )
            }
            .ignoresSafeArea(.container, edges: .bottom)
        }
        .navigationBarHidden(true)
        .preferredColorScheme(.dark)
        .onAppear {
            isBookmarked = article.isBookmarked
        }
        .confirmationDialog("Options", isPresented: $showMoreOptions) {
            Button("Share Article") { handleShareTapped() }
            Button(isBookmarked ? "Remove Bookmark" : "Save Article") { handleBookmarkTapped() }
            Button("Report Issue") { handleReportTapped() }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(items: [article.title, article.subtitle])
        }
    }

    // MARK: - Mini Header

    private var miniHeader: some View {
        HStack(spacing: AppSpacing.md) {
            // Back button
            Button(action: handleBackTapped) {
                Image(systemName: "chevron.left")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(AppColors.textPrimary)
            }
            .buttonStyle(PlainButtonStyle())

            // Title
            Text(article.title)
                .font(AppTypography.bodyBold)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)

            Spacer()

            // Actions
            HStack(spacing: AppSpacing.lg) {
                Button(action: handleShareTapped) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())

                Button(action: handleBookmarkTapped) {
                    Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundColor(isBookmarked ? AppColors.primaryBlue : AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(
            AppColors.background
                .shadow(color: Color.black.opacity(0.2), radius: 4, y: 2)
        )
    }

    // MARK: - Action Handlers

    private func handleBackTapped() {
        dismiss()
    }

    private func handleShareTapped() {
        showShareSheet = true
    }

    private func handleBookmarkTapped() {
        withAnimation(.spring(response: 0.3)) {
            isBookmarked.toggle()
        }
    }

    private func handleFollowTapped() {
        withAnimation(.spring(response: 0.3)) {
            isFollowing.toggle()
        }
    }

    private func handleAuthorTapped() {
        print("Navigate to author profile: \(article.author.name)")
    }

    private func handleListenTapped() {
        print("Start audio playback")
    }

    private func handleMoreTapped() {
        showMoreOptions = true
    }

    private func handleReportTapped() {
        print("Report article")
    }
}

// MARK: - Scroll Offset Preference Key

private struct ScrollOffsetPreferenceKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

// MARK: - Preview

#Preview {
    MoneyMoveArticleDetailView(article: MoneyMoveArticle.sampleDigitalFinance)
}
