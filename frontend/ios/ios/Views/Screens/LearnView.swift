//
//  LearnView.swift
//  ios
//
//  Main Learn (Wiser) screen combining all organisms
//

import SwiftUI

// MARK: - LearnContentView (Used in TabView)
struct LearnContentView: View {
    @StateObject private var viewModel = LearnViewModel()

    var body: some View {
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

                // Tab content
                TabView(selection: $viewModel.selectedTab) {
                    learnTabContent
                        .tag(LearnTab.learn)

                    chatTabContent
                        .tag(LearnTab.chat)

                    savedTabContent
                        .tag(LearnTab.saved)
                }
                .tabViewStyle(.page(indexDisplayMode: .never))
            }

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
    }

    // MARK: - Learn Tab Content
    private var learnTabContent: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xxl) {
                // Investor Path Section
                InvestorPathSection(
                    currentLevel: viewModel.currentLevel,
                    onSeeAll: handleSeeAllPath
                )
                .padding(.top, AppSpacing.md)

                // Your Journey Section
                if let track = viewModel.journeyTrack {
                    YourJourneySection(
                        track: track,
                        onContinue: handleContinueJourney,
                        onItemTap: handleJourneyItemTap
                    )
                }

                // Next Lesson Section
                if let lesson = viewModel.nextLesson {
                    NextLessonSection(lesson: lesson) {
                        handleStartLesson(lesson)
                    }
                }

                // Key Concepts Section
                if !viewModel.keyConcepts.isEmpty {
                    KeyConceptsSection(
                        concepts: viewModel.keyConcepts,
                        onSeeAll: handleSeeAllConcepts,
                        onConceptTap: handleConceptTap,
                        onBookmark: handleBookmarkConcept
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

    // MARK: - Saved Tab Content
    private var savedTabContent: some View {
        VStack {
            Spacer()
            VStack(spacing: AppSpacing.lg) {
                Image(systemName: "bookmark.fill")
                    .font(.system(size: 48))
                    .foregroundColor(AppColors.textMuted)

                Text("No Saved Content")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                Text("Bookmark concepts and books to find them here")
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
                    .multilineTextAlignment(.center)
            }
            .padding()
            Spacer()
        }
    }

    // MARK: - Action Handlers
    private func handleSearchSubmit() {
        print("Search submitted: \(viewModel.searchText)")
    }

    private func handleSeeAllPath() {
        print("See all investor path levels")
    }

    private func handleContinueJourney() {
        viewModel.continueJourney()
    }

    private func handleJourneyItemTap(_ item: JourneyItem) {
        print("Journey item tapped: \(item.title)")
    }

    private func handleStartLesson(_ lesson: NextLesson) {
        viewModel.startLesson(lesson)
    }

    private func handleSeeAllConcepts() {
        print("See all concepts")
    }

    private func handleConceptTap(_ concept: KeyConcept) {
        viewModel.openConcept(concept)
    }

    private func handleBookmarkConcept(_ concept: KeyConcept) {
        viewModel.toggleBookmark(for: concept)
    }

    private func handleSeeAllBooks() {
        print("See all books")
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
        .preferredColorScheme(.dark)
}
