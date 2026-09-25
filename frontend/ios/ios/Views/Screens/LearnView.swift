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
    @Environment(\.isActiveTab) private var isActiveTab
    @EnvironmentObject private var audioManager: AudioManager
    @StateObject private var viewModel = LearnViewModel()
    @ObservedObject private var bookmarks = BookmarkStore.shared
    /// Separate, ephemeral VM for the "Ask the Agent" book chat, so seeding it with a book
    /// never clobbers the general conversation. That general one is owned by `ContentView` now and
    /// raised from the header bar's sparkle — this screen no longer holds a chat view model of its
    /// own, which is what let the `Learn | Chat` control go.
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
                // Header — the global row. Its trailing sparkle raises the shared Cay AI chat
                // via AppState; this screen owns no part of that presentation any more.
                LearnHeader(
                    onSearchTapped: handleSearchTapped,
                    onProfileTapped: handleProfileTapped
                )

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
        // Covers only. `showingBookLibrary` / `showingInvestorJourney` / `showingMoneyMovesDetail`
        // are `.navigationDestination` PUSHES inside this tab's own stack — the user is leaving
        // for another tab, not abandoning Learn, so that stack is theirs to come back to.
        .onPresentationReset {
            selectedMoneyMoveArticle = nil
            selectedLibraryBook = nil
            showProfile = false
            showSearch = false
            showBookChat = false
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
        // `.task(id: isActiveTab)`, matching the other four tab roots. This was a plain
        // `.task { }` — the ONLY tab root with no activation gate — so all five hydrations
        // below ran at cold launch on a tab nobody was looking at, staggered against
        // `AppState.hydrateLearnStores()`. The stores' `hydrateTask` join only collapses
        // OVERLAPPING calls, and these two chains never overlapped (each is a serial chain of
        // different awaits), so `learn/progress/journey_lesson` and `learn/progress/money_move`
        // were each fetched twice on every launch.
        .task(id: isActiveTab) {
            guard isActiveTab else { return }
            // Upgrade the Wiser-screen Money Moves row to fresh backend content so it matches
            // the See-All screen. Bundled content already painted synchronously from the store.
            await viewModel.prefetchMoneyMoves()
            // Pull server-side progress into the local caches (best-effort; books/journey/money moves).
            await JourneyProgressStore.shared.hydrate()
            await MoneyMovesProgressStore.shared.hydrate()
            await bookmarks.hydrate()
        }
        // Wiser was the ONLY one of the five tab roots without this. `.task(id: isActiveTab)`
        // above covers a hydrate that merely raced session restore, but it cannot fire for
        // someone already looking at this tab when the identity changes — and the Learn stores
        // are the ones where that matters most, because they are device-global: `AppState`
        // clears them on sign-out (auth.md §7), so without a re-hydrate the tab kept showing
        // an emptied set of completions until the app was killed, and signing IN never pulled
        // the new account's progress at all.
        //
        // Clearing is already owned by `AppState.discardDataForEndedSession()`; this only has
        // to re-fetch, so it gates the whole body on the active tab.
        .reloadOnIdentityChange { isActive in
            guard isActive else { return }
            await viewModel.prefetchMoneyMoves()
            await JourneyProgressStore.shared.hydrate()
            await MoneyMovesProgressStore.shared.hydrate()
            await bookmarks.hydrate()
        }
        // "Ask the Agent" book chat keeps its own VM + cover so a book-grounded session
        // never overwrites the general conversation `ContentView` owns.
        .aiChatCover(isPresented: $showBookChat, viewModel: bookChatViewModel)
        }
        // Narration is Pro/Max: the audio ENGINE refuses a locked episode and asks for
        // an upgrade, so this presenter is what turns that into the plan sheet. Needed on
        // each screen because these are fullScreenCovers — a modifier on the presenter
        // does not reach them.
        .learnAudioPaywall()
    }

    // MARK: - Learn Tab Content
    private var learnTabContent: some View {
        VStack(spacing: 0) {
            ScrollView(showsIndicators: false) {
                // A plain VStack, NOT LazyVStack - see HomeDashboardView.content for the full write-up.
                // The direct children here are a fixed, hand-written list, so laziness bought nothing,
                // while a lazy stack whose child RESIZES IN PLACE re-walks its predecessor chain and can
                // wedge the main thread at 100% inside LazySubviewPlacements -> _ViewList_Node.applyNodes.
                //
                // The books / money-moves sections land async behind `if !isEmpty`.
                // NOTE: unlike Home, this subtree DOES render AsyncImage (BookCoverImage, MoneyMoveCoverImage),
                // so going eager fires those on entry. Bounded (~10 books, ~15 articles) and measured.
                VStack(spacing: AppSpacing.xxl) {
                    // Wiser is the only gated tab that does NOT go blank without an account:
                    // the Books roster and the Journey roadmap are compiled-in, and Money Moves
                    // falls back to its bundled JSON. So this is a NOTICE beside the content,
                    // not `AccountGateEmptyState` in place of it — gating the page would delete
                    // a feature that genuinely works.
                    //
                    // But the silence was its own bug: all five stores (`MoneyMovesContentStore`,
                    // `JourneyContentStore`, `JourneyProgressStore`, `MoneyMovesProgressStore`,
                    // `BookmarkStore`) swallow a refused `.signInRequired` into a `print`, so
                    // completion ticks and bookmarks silently revert to device-local and the
                    // screen says nothing. This says it.
                    accountGateNotice

                    fullLearnDashboard

                    // Bottom padding for tab bar
                    Color.clear.frame(height: AppSpacing.xxxl)
                }
            }
            .refreshable {
                await viewModel.refresh()
            }
        }
    }

    // MARK: - Account gate

    /// A notice when the session is not armed, and nothing at all when it is.
    ///
    /// Read LIVE from `appState.auth.status` rather than snapshotted during a load, which is
    /// the opposite of what the other four tabs do — deliberately. Those snapshot because they
    /// have a load to report on; here there is no single load to attach to (five independent
    /// stores hydrate on their own schedule), and `AppState` is `@Observable`, so a live read
    /// makes the notice disappear the moment the session heals with nothing to re-run.
    ///
    /// `.restoring` gets no button: `AppState.requestSignIn` declines to prompt while a restore
    /// is pending, so it would do nothing (auth.md §5).
    @ViewBuilder
    private var accountGateNotice: some View {
        switch appState.auth.status {
        case .authenticated:
            EmptyView()

        case .restoring:
            InlineRetryNotice(
                message: "Reconnecting your account… Your progress is saved on this device "
                    + "and will sync once you're back.",
                systemImage: "arrow.clockwise",
                iconColor: AppColors.textMuted
            )
            .padding(.horizontal, AppSpacing.lg)

        case .unauthenticated, .unknown, .loading:
            // Normally unreachable — the sign-in wall in `iosApp.swift` means a signed-out
            // user never sees a tab. Kept because a mid-session credential death lands here
            // for the frame before the root swaps, and because a notice that only exists in
            // one reachable state rots the moment another one opens up.
            InlineRetryNotice(
                message: "Sign in to save your progress. Lessons you finish and articles you "
                    + "bookmark are kept on your account, so they follow you across devices.",
                systemImage: "person.crop.circle.badge.checkmark",
                iconColor: AppColors.textMuted,
                retryTitle: "Sign In",
                onRetry: { appState.requestSignIn(for: "save your learning progress") }
            )
            .padding(.horizontal, AppSpacing.lg)
        }
    }

    // MARK: - Dashboard
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

    /// The row under "Resume Lessons" is the current lesson, so a tap does what Resume does.
    /// It used to only `print`, which made a visible, tappable row do nothing.
    private func handleJourneyItemTap(_ item: JourneyItem) {
        handleContinueJourney()
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

    /// The Learn tab renders `EducationBook`, but every grounded feature needs the richer
    /// `LibraryBook` (its `curriculumOrder` is what identifies the book to the backend).
    /// One lookup, used by both entry points: two copies of a title match is precisely the
    /// drift that would silently un-ground a chat.
    private func libraryBook(matching title: String) -> LibraryBook? {
        LibraryBook.sampleData.first(where: { $0.title == title })
    }

    private func handleBookTap(_ book: EducationBook) {
        if let libraryBook = libraryBook(matching: book.title) {
            selectedLibraryBook = libraryBook
        }
    }

    private func handleChatWithBook(_ book: EducationBook) {
        // "Ask the Agent" → OPEN the book chat, empty. It used to auto-send a synthesised
        // "Tell me about ..." question, which spent a turn the user never typed. Its own VM
        // so it doesn't clobber the resumable Wiser Chat-tab conversation.
        //
        // Fail HONEST if the title doesn't resolve: open an ungrounded chat rather than one
        // whose chip claims a study guide the backend never received.
        guard let library = libraryBook(matching: book.title) else {
            bookChatViewModel.prepareGroundedConversation()
            showBookChat = true
            return
        }
        bookChatViewModel.prepareGroundedConversation(
            context: library.studyGuideContext(),
            contextType: .book,
            referenceId: String(library.curriculumOrder)
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
        // Was an inline switch duplicated verbatim in MoneyMovesDetailView — see
        // MoneyMoveCategory.gradientColors.
        let gradientColors = move.category.gradientColors

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
