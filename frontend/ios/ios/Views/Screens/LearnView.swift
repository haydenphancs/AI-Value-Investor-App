//
//  LearnView.swift
//  ios
//
//  Main Learn (Wiser) screen combining all organisms
//

import SwiftUI

// MARK: - LearnContentView (Used in TabView)
struct LearnContentView: View {
    @Environment(\.appState) private var appState
    @EnvironmentObject private var audioManager: AudioManager
    @StateObject private var viewModel = LearnViewModel()
    @State private var showingInvestorJourney = false
    @State private var shouldScrollToNextLesson = false
    @State private var showingMoneyMovesDetail = false
    @State private var showingBookLibrary = false
    @State private var showProfile = false
    @State private var selectedMoneyMoveArticle: MoneyMoveArticle?
    @State private var selectedLibraryBook: LibraryBook?

    var body: some View {
        NavigationStack {
        ZStack {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main Content
            VStack(spacing: 0) {
                // Header with search and tabs
                LearnHeader(
                    selectedTab: $viewModel.selectedTab,
                    onSearchTapped: handleSearchTapped,
                    onProfileTapped: handleProfileTapped
                )

                // Tab content - no swipe gesture between tabs
                Group {
                    switch viewModel.selectedTab {
                    case .learn:
                        learnTabContent
                    case .chat:
                        chatTabContent
                    }
                }
                .animation(.easeInOut(duration: 0.2), value: viewModel.selectedTab)
            }

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .navigationDestination(isPresented: $showingInvestorJourney) {
            InvestorJourneyView(scrollToNextLesson: shouldScrollToNextLesson)
                .environmentObject(audioManager)
        }
        .navigationDestination(isPresented: $showingMoneyMovesDetail) {
            MoneyMovesDetailView()
                .environmentObject(audioManager)
        }
        .navigationDestination(isPresented: $showingBookLibrary) {
            BookLibraryView()
                .environmentObject(audioManager)
        }
        .fullScreenCover(item: $selectedMoneyMoveArticle) { article in
            MoneyMoveArticleDetailView(article: article)
                .environmentObject(audioManager)
        }
        .fullScreenCover(item: $selectedLibraryBook) { book in
            BookDetailView(book: book)
                .environmentObject(audioManager)
        }
        .fullScreenCover(isPresented: $showProfile) {
            ProfileView()
                .environment(appState)
                .environment(\.appState, appState)
                .preferredColorScheme(.dark)
        }
        .navigationBarHidden(true)
        }
    }

    // MARK: - Learn Tab Content
    private var learnTabContent: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xxl) {
                // Investor Journey Section (includes journey progress)
                InvestorJourneySection(
                    currentLevel: viewModel.currentLevel,
                    journeyTrack: viewModel.journeyTrack,
                    onSeeAll: handleSeeAllJourney,
                    onContinue: handleContinueJourney,
                    onItemTap: handleJourneyItemTap
                )
                .padding(.top, AppSpacing.md)

                // Money Moves Section
                if !viewModel.moneyMoves.isEmpty {
                    MoneyMovesSection(
                        concepts: viewModel.moneyMoves,
                        onSeeAll: handleSeeAllMoneyMoves,
                        onConceptTap: handleMoneyMoveTap,
                        onBookmark: handleBookmarkMoneyMove
                    )
                }

                // AI-Enabled Books Section
                if !viewModel.books.isEmpty {
                    AIBooksSection(
                        books: viewModel.books,
                        onSeeAll: handleSeeAllBooks,
                        onBookTap: handleBookTap,
                        onChatWithBook: handleChatWithBook,
                        onReadKeyIdeas: handleReadKeyIdeas
                    )
                }

                // Community Discussions Section
                if !viewModel.discussions.isEmpty {
                    CommunityDiscussionsSection(
                        discussions: viewModel.discussions,
                        onSeeAll: handleSeeAllDiscussions,
                        onDiscussionTap: handleDiscussionTap
                    )
                }

                // Credits Balance Section
                if let balance = viewModel.creditBalance {
                    LearnCreditsSection(
                        balance: balance,
                        onAddCredits: handleAddCredits
                    )
                }

                // Bottom padding for tab bar
                Color.clear.frame(height: AppSpacing.xxxl)
            }
        }
        .refreshable {
            await viewModel.refresh()
        }
    }

    // MARK: - Chat Tab Content
    private var chatTabContent: some View {
        ChatTabView {
            handleHistoryTap()
        }
    }

    private func handleHistoryTap() {
        print("Chat history tapped")
    }

    // MARK: - Action Handlers
    private func handleSearchTapped() {
        print("Search tapped")
    }

    private func handleProfileTapped() {
        showProfile = true
    }

    private func handleSeeAllJourney() {
        shouldScrollToNextLesson = false
        showingInvestorJourney = true
    }

    private func handleContinueJourney() {
        shouldScrollToNextLesson = true
        showingInvestorJourney = true
    }

    private func handleJourneyItemTap(_ item: JourneyItem) {
        print("Journey item tapped: \(item.title)")
    }

    private func handleSeeAllMoneyMoves() {
        showingMoneyMovesDetail = true
    }

    private func handleMoneyMoveTap(_ moneyMove: MoneyMove) {
        selectedMoneyMoveArticle = createArticleFromMove(moneyMove)
    }

    private func handleBookmarkMoneyMove(_ moneyMove: MoneyMove) {
        viewModel.toggleBookmark(for: moneyMove)
    }

    private func handleSeeAllBooks() {
        showingBookLibrary = true
    }

    private func handleBookTap(_ book: EducationBook) {
        // Find matching LibraryBook by title
        if let libraryBook = LibraryBook.sampleData.first(where: { $0.title == book.title }) {
            selectedLibraryBook = libraryBook
        }
    }

    private func handleChatWithBook(_ book: EducationBook) {
        viewModel.chatWithBook(book)
    }

    private func handleReadKeyIdeas(_ book: EducationBook) {
        viewModel.readKeyIdeas(book)
    }

    private func handleSeeAllDiscussions() {
        print("See all discussions")
    }

    private func handleDiscussionTap(_ discussion: CommunityDiscussion) {
        viewModel.openDiscussion(discussion)
    }

    private func handleAddCredits() {
        viewModel.addCredits()
    }

    // MARK: - Helpers

    /// Creates a full MoneyMoveArticle from a MoneyMove card data
    private func createArticleFromMove(_ move: MoneyMove) -> MoneyMoveArticle {
        let gradientColors: [String]
        switch move.category {
        case .blueprints:
            gradientColors = ["059669", "047857", "064E3B"]
        case .valueTraps:
            gradientColors = ["DC2626", "991B1B", "7F1D1D"]
        case .battles:
            gradientColors = ["7C3AED", "5B21B6", "4C1D95"]
        }

        return MoneyMoveArticle(
            title: move.title,
            subtitle: move.subtitle,
            category: move.category,
            author: ArticleAuthor(
                name: "The Alpha",
                avatarName: nil,
                title: "Investment Research",
                isVerified: true,
                followerCount: "45.2k"
            ),
            publishedAt: Date(),
            readTimeMinutes: move.estimatedMinutes,
            viewCount: move.learnerCount,
            commentCount: Int.random(in: 20...200),
            isBookmarked: move.isBookmarked,
            hasAudioVersion: true,
            heroGradientColors: gradientColors,
            tagLabel: move.category == .blueprints ? "BLUEPRINT" : (move.category == .valueTraps ? "CASE STUDY" : "VS"),
            isFeatured: false,
            keyHighlights: [
                ArticleHighlight(
                    icon: "lightbulb.fill",
                    title: "Key Insight",
                    description: "Understanding the core principles behind this investment case study."
                ),
                ArticleHighlight(
                    icon: "chart.line.uptrend.xyaxis",
                    title: "Market Impact",
                    description: "How this story influenced market dynamics and investor behavior."
                ),
                ArticleHighlight(
                    icon: "exclamationmark.triangle.fill",
                    title: "Lessons Learned",
                    description: "Critical takeaways for modern investors and portfolio managers."
                )
            ],
            sections: [
                ArticleSection(
                    title: "Overview",
                    icon: "doc.text.fill",
                    content: [
                        .paragraph("This case study explores the key factors that led to this notable investment story. Understanding these dynamics is crucial for making informed investment decisions in today's complex market environment."),
                        .paragraph("By analyzing the events, decisions, and market reactions, we can extract valuable lessons applicable to future investment opportunities and risk management strategies.")
                    ],
                    hasGlowEffect: true
                ),
                ArticleSection(
                    title: "Background & Context",
                    icon: "clock.fill",
                    content: [
                        .paragraph("To fully appreciate this case study, we must understand the market conditions and competitive landscape that shaped its trajectory."),
                        .callout(
                            icon: "info.circle.fill",
                            text: "The events discussed here occurred during a period of significant market transformation, making them particularly relevant for today's investors.",
                            style: .info
                        ),
                        .bulletList([
                            "Market conditions at the time",
                            "Key players and their motivations",
                            "Regulatory environment",
                            "Technological factors"
                        ])
                    ]
                ),
                ArticleSection(
                    title: "Key Takeaways",
                    icon: "star.fill",
                    content: [
                        .subheading("For Value Investors"),
                        .bulletList([
                            "Understanding market dynamics is essential for long-term success",
                            "Due diligence prevents costly mistakes and protects capital",
                            "Long-term thinking creates lasting value for shareholders",
                            "Risk management is non-negotiable in volatile markets"
                        ]),
                        .subheading("Practical Applications"),
                        .paragraph("These lessons can be directly applied to your investment process. Consider how each principle might have changed outcomes in your own portfolio decisions.")
                    ]
                ),
                ArticleSection(
                    title: "Conclusion",
                    icon: "checkmark.seal.fill",
                    content: [
                        .paragraph("This case study demonstrates the importance of fundamental analysis, proper due diligence, and maintaining a long-term perspective in investing."),
                        .callout(
                            icon: "quote.opening",
                            text: "The best investment you can make is in your own education and understanding of what drives business value.",
                            style: .highlight
                        )
                    ]
                )
            ],
            statistics: [
                ArticleStatistic(value: move.learnerCount, label: "Investors Learning", trend: .up, trendValue: "12%"),
                ArticleStatistic(value: "\(move.estimatedMinutes)m", label: "Read Time"),
                ArticleStatistic(value: "4.8", label: "Rating", trend: .up, trendValue: "0.3")
            ],
            comments: [
                ArticleComment(
                    authorName: "Michael Chen",
                    authorAvatar: nil,
                    content: "Excellent analysis! This really helped me understand the key factors at play.",
                    postedAt: Calendar.current.date(byAdding: .hour, value: -3, to: Date())!,
                    likeCount: 24,
                    replyCount: 5,
                    isVerified: false
                ),
                ArticleComment(
                    authorName: "Sarah Williams",
                    authorAvatar: nil,
                    content: "The section on risk management was particularly valuable. Would love to see more case studies like this.",
                    postedAt: Calendar.current.date(byAdding: .hour, value: -8, to: Date())!,
                    likeCount: 18,
                    replyCount: 2,
                    isVerified: true
                )
            ],
            relatedArticles: MoneyMoveArticle.sampleDigitalFinance.relatedArticles
        )
    }
}

// MARK: - Legacy LearnView (for backward compatibility)
struct LearnView: View {
    var body: some View {
        LearnContentView()
    }
}

#Preview {
    LearnView()
        .environmentObject(AudioManager.shared)
        .preferredColorScheme(.dark)
}
