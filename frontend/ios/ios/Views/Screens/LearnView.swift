//
//  LearnView.swift
//  ios
//
//  Main Learn (Wiser) screen combining all organisms
//

import SwiftUI

// MARK: - LearnContentView (Used in TabView)
struct LearnContentView: View {
    @EnvironmentObject private var audioManager: AudioManager
    @StateObject private var viewModel = LearnViewModel()
    @State private var showingInvestorJourney = false
    @State private var shouldScrollToNextLesson = false
    @State private var showingMoneyMovesDetail = false
    @State private var showingBookLibrary = false

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
                    searchText: $viewModel.searchText,
                    selectedTab: $viewModel.selectedTab,
                    onSearchSubmit: handleSearchSubmit
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

                // Next Lesson Section
                if let lesson = viewModel.nextLesson {
                    NextLessonSection(lesson: lesson) {
                        handleStartLesson(lesson)
                    }
                }

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
    private func handleSearchSubmit() {
        print("Search submitted: \(viewModel.searchText)")
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

    private func handleStartLesson(_ lesson: NextLesson) {
        viewModel.startLesson(lesson)
    }

    private func handleSeeAllMoneyMoves() {
        showingMoneyMovesDetail = true
    }

    private func handleMoneyMoveTap(_ moneyMove: MoneyMove) {
        viewModel.openMoneyMove(moneyMove)
    }

    private func handleBookmarkMoneyMove(_ moneyMove: MoneyMove) {
        viewModel.toggleBookmark(for: moneyMove)
    }

    private func handleSeeAllBooks() {
        showingBookLibrary = true
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
