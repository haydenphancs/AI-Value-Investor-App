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
    @ObservedObject private var bookmarks = BookmarkStore.shared
    /// Owns the Wiser chat conversation for the app session so the Chat tab RESUMES the last
    /// conversation each time it's reopened (the Chat tab now opens AIChatScreen as a cover).
    @StateObject private var chatViewModel = ChatViewModel()
    @State private var showAIChat = false
    /// Separate, ephemeral VM for the "Ask the Author Agent" book chat so seeding it never clobbers
    /// the resumable Wiser Chat-tab thread above.
    @StateObject private var bookChatViewModel = ChatViewModel()
    @State private var showBookChat = false
    @State private var showingInvestorJourney = false
    @State private var shouldScrollToNextLesson = false
    @State private var showingMoneyMovesDetail = false
    @State private var showingBookLibrary = false
    @State private var showProfile = false
    @State private var showSearch = false
    @State private var selectedMoneyMoveArticle: MoneyMoveArticle?
    @State private var selectedLibraryBook: LibraryBook?

    /// Feature flag — Community Discussions section.
    /// Hidden for now: too few active users to sustain a community feed, and moderation adds
    /// complexity we're not ready for. Kept (not deleted) so it can be turned back on later by
    /// flipping this to `true` once there's a real user base. See the gated block in learnTabContent.
    private let showCommunityDiscussions = false

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
                    onProfileTapped: handleProfileTapped,
                    onChatTapped: { showAIChat = true }
                )

                // Tab content — the "Chat" tab opens the full-screen AIChatScreen cover (see
                // onChatTapped above); only the Learn content renders inline here.
                learnTabContent
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
        }
        .fullScreenCover(isPresented: $showSearch) {
            SearchView()
        }
        .navigationBarHidden(true)
        .task {
            // Upgrade the Wiser-screen Money Moves row to fresh backend content so it matches
            // the See-All screen. Bundled content already painted synchronously from the store.
            await viewModel.prefetchMoneyMoves()
            // Pull server-side progress into the local caches (best-effort; books/journey/money moves).
            await JourneyProgressStore.shared.hydrate()
            await MoneyMovesProgressStore.shared.hydrate()
            await bookmarks.hydrate()
        }
        // The Wiser "Chat" tab opens the unified full-screen chat (AIChatScreen) as a cover, which
        // owns its own audio compact via .globalAudioOverlay — so the Wiser screen no longer manages
        // chat-tab audio here.
        .aiChatCover(isPresented: $showAIChat, viewModel: chatViewModel)
        // "Ask the Author Agent" book chat uses its own VM so it never overwrites the resumable
        // Chat-tab thread.
        .aiChatCover(isPresented: $showBookChat, viewModel: bookChatViewModel)
        }
    }

    // MARK: - Learn Tab Content
    private var learnTabContent: some View {
        VStack(spacing: 0) {
            // Keyword filter over the Money Moves + Books on this dashboard.
            // Client-side only — distinct from the top "Search or ask Cay AI" bar,
            // which is global entity/AI search. Shown once there's content to
            // filter, or while a keyword is active (so it can be cleared).
            if showContentFilterBar {
                SearchBar(
                    text: $viewModel.contentFilter,
                    placeholder: "Filter Money Moves & books…"
                )
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.sm)
                .padding(.bottom, AppSpacing.sm)
            }

            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: AppSpacing.xxl) {
                    if viewModel.isFilteringContent {
                        filteredLearnContent
                    } else {
                        fullLearnDashboard
                    }

                    // Bottom padding for tab bar
                    Color.clear.frame(height: AppSpacing.xxxl)
                }
            }
            .refreshable {
                await viewModel.refresh()
            }
        }
    }

    /// Show the filter field once the dashboard has content, or while a filter is
    /// active (so an empty result set can still be cleared).
    private var showContentFilterBar: Bool {
        !viewModel.moneyMoves.isEmpty || !viewModel.books.isEmpty || viewModel.isFilteringContent
    }

    // MARK: - Full dashboard (no filter active)
    @ViewBuilder
    private var fullLearnDashboard: some View {
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
                onConceptTap: handleMoneyMoveTap
            )
        }

        // AI-Enabled Books Section
        if !viewModel.books.isEmpty {
            AIBooksSection(
                books: viewModel.books,
                onSeeAll: handleSeeAllBooks,
                onBookTap: handleBookTap,
                onChatWithBook: handleChatWithBook,
                isBookmarked: { bookmarks.isBookmarked($0.title) },
                onToggleBookmark: { bookmarks.toggle($0.title) }
            )
        }

        // Community Discussions Section — HIDDEN for now.
        // The app doesn't have enough active users yet to make a social/community
        // feed worthwhile, and moderating discussions adds complexity we don't want
        // to take on at this stage. Intentionally hidden (not removed) so it can be
        // re-enabled later once there's a real user base — flip the flag below.
        // All supporting code is intact: viewModel.discussions, the section/row views
        // (CommunityDiscussionsSection, CommunityDiscussionRow), and the tap handlers.
        if showCommunityDiscussions, !viewModel.discussions.isEmpty {
            CommunityDiscussionsSection(
                discussions: viewModel.discussions,
                onSeeAll: handleSeeAllDiscussions,
                onDiscussionTap: handleDiscussionTap
            )
        }

        // Credits live on the RESEARCH tab, where they are actually spent — a balance
        // shown next to reading material invited a top-up at the moment the user was
        // least likely to need one, and duplicated a number that must never disagree
        // with itself across two screens.
    }

    // MARK: - Filtered content (keyword active)
    //
    // Only the two list-like sections are filterable. The Journey section is a
    // progress track (current level only), not a content browser, and Credits
    // isn't content — both are hidden while filtering.
    @ViewBuilder
    private var filteredLearnContent: some View {
        if viewModel.filteredMoneyMoves.isEmpty && viewModel.filteredBooks.isEmpty {
            learnFilterEmptyState
        } else {
            if !viewModel.filteredMoneyMoves.isEmpty {
                MoneyMovesSection(
                    concepts: viewModel.filteredMoneyMoves,
                    onSeeAll: handleSeeAllMoneyMoves,
                    onConceptTap: handleMoneyMoveTap
                )
                .padding(.top, AppSpacing.md)
            }

            if !viewModel.filteredBooks.isEmpty {
                AIBooksSection(
                    books: viewModel.filteredBooks,
                    onSeeAll: handleSeeAllBooks,
                    onBookTap: handleBookTap,
                    onChatWithBook: handleChatWithBook,
                    isBookmarked: { bookmarks.isBookmarked($0.title) },
                    onToggleBookmark: { bookmarks.toggle($0.title) }
                )
            }
        }
    }

    private var learnFilterEmptyState: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "magnifyingglass")
                .font(AppTypography.iconXXL)
                .foregroundColor(AppColors.textMuted)

            Text("No content matches “\(viewModel.contentFilter)”")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .multilineTextAlignment(.center)

            Text("Try a different word, or clear the filter.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.xxl)
        .padding(.horizontal, AppSpacing.xl)
    }

    // MARK: - Action Handlers
    private func handleSearchTapped() {
        showSearch = true
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
        // Prefer authored content (backend → bundled, via MoneyMovesContentStore); fall back to
        // generated placeholder for cards not yet authored. Mirrors MoneyMovesDetailView.
        selectedMoneyMoveArticle = MoneyMovesContentStore.shared.article(forTitle: moneyMove.title)
            ?? createArticleFromMove(moneyMove)
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
        // "Ask the Author Agent" → open the unified full-screen chat seeded with the book, in its
        // OWN VM so it doesn't clobber the resumable Wiser Chat-tab conversation.
        bookChatViewModel.startNewConversation(
            firstMessage: "Tell me about \"\(book.title)\" by \(book.author).",
            context: "The user wants to learn about the book \"\(book.title)\" by \(book.author). Discuss its key ideas.",
            contextType: .book
        )
        showBookChat = true
    }

    private func handleSeeAllDiscussions() {
        print("See all discussions")
    }

    private func handleDiscussionTap(_ discussion: CommunityDiscussion) {
        viewModel.openDiscussion(discussion)
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
                name: "Caydex Research",
                avatarName: nil,
                title: "Editorial",
                isVerified: false,
                followerCount: ""
            ),
            publishedAt: Date(),
            readTimeMinutes: move.estimatedMinutes,
            // No invented engagement metrics. These were a fabricated learner count
            // and a RANDOM comment count, both presented to users as real.
            viewCount: "",
            commentCount: 0,
            isBookmarked: false,
            hasAudioVersion: false,   // placeholder card: no narration audio (real articles carry audioUrl)
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
            // Read Time is the only statistic we can state truthfully. The removed two
            // were an invented "Investors Learning" count with a fabricated +12% trend
            // and a hardcoded "4.8 Rating" — neither is measured anywhere.
            statistics: [
                ArticleStatistic(value: "\(move.estimatedMinutes)m", label: "Read Time")
            ],
            // No invented reader comments. There is no comment feature to source them
            // from, so any comment shown here is fiction presented as user content.
            comments: [],
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
}
