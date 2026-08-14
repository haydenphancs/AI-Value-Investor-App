//
//  InvestorJourneyView.swift
//  ios
//
//  The Investor Journey - Full learning journey screen from Novice to Master
//

import SwiftUI

struct InvestorJourneyView: View {
    @StateObject private var viewModel = InvestorJourneyViewModel()
    @Environment(\.dismiss) private var dismiss
    var scrollToNextLesson: Bool = false
    @Namespace private var lessonNamespace

    // Its OWN ChatViewModel, like every other .aiChatCover host: sharing the Wiser tab's
    // resumable thread would clobber it. Caller-owned so "resume last conversation" works.
    @StateObject private var chatViewModel = ChatViewModel()
    @State private var showChat = false

    var body: some View {
        ZStack {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main content
            VStack(spacing: 0) {
                // Header
                InvestorJourneyHeader(
                    completedLessons: viewModel.totalLessonsCompleted,
                    totalLessons: viewModel.totalLessons,
                    onBackTapped: {
                        dismiss()
                    }
                )

                // Scrollable content
                ScrollViewReader { proxy in
                    ScrollView(showsIndicators: false) {
                        LazyVStack(spacing: AppSpacing.xxl) {
                            // Level 1: Foundation
                            if let foundationLevel = viewModel.getLevelProgress(for: .foundation) {
                                InvestorJourneyLevelSection(
                                    levelProgress: foundationLevel,
                                    onLessonTap: { lesson in
                                        viewModel.selectLesson(lesson)
                                    }
                                )
                                .id(foundationLevel.id)
                            }

                            // Level 2: Analysis
                            if let analysisLevel = viewModel.getLevelProgress(for: .analysis) {
                                InvestorJourneyLevelSection(
                                    levelProgress: analysisLevel,
                                    onLessonTap: { lesson in
                                        viewModel.selectLesson(lesson)
                                    }
                                )
                                .id(analysisLevel.id)
                            }

                            // Chat with book prompt (between Level 2 and 3)
                            ChatWithBookPromptCard(
                                bookTitle: viewModel.deepDiveBook.title
                            ) {
                                handleChatWithBook()
                            }
                            .padding(.horizontal, AppSpacing.lg)

                            // Level 3: Strategies
                            if let strategiesLevel = viewModel.getLevelProgress(for: .strategies) {
                                InvestorJourneyLevelSection(
                                    levelProgress: strategiesLevel,
                                    onLessonTap: { lesson in
                                        viewModel.selectLesson(lesson)
                                    }
                                )
                                .id(strategiesLevel.id)
                            }

                            // Level 4: Mastery
                            if let masteryLevel = viewModel.getLevelProgress(for: .mastery) {
                                InvestorJourneyLevelSection(
                                    levelProgress: masteryLevel,
                                    onLessonTap: { lesson in
                                        viewModel.selectLesson(lesson)
                                    }
                                )
                                .id(masteryLevel.id)
                            }

                            // Study Schedule section
                            InvestorJourneyStudyScheduleSection(
                                schedule: $viewModel.studySchedule,
                                onMorningTimeTap: {
                                    // Show time picker for morning session
                                },
                                onReviewTimeTap: {
                                    // Show time picker for review time
                                }
                            )

                            // Inspirational quote
                            InvestorQuoteCard(quote: viewModel.quote)
                                .padding(.horizontal, AppSpacing.lg)

                            // Bottom padding for safe area
                            Color.clear.frame(height: AppSpacing.xxxl)
                        }
                        .padding(.top, AppSpacing.md)
                    }
                    .refreshable {
                        await viewModel.refresh()
                    }
                    .onAppear {
                        if scrollToNextLesson, let nextLevelId = viewModel.nextLessonLevelId {
                            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                                withAnimation {
                                    proxy.scrollTo(nextLevelId, anchor: .top)
                                }
                            }
                        }
                    }
                }
            }

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .navigationBarHidden(true)
        .aiChatCover(isPresented: $showChat, viewModel: chatViewModel)
        .fullScreenCover(isPresented: $viewModel.showLessonStory) {
            if let lesson = viewModel.selectedLesson {
                LessonTopicCardView(
                    storyContent: viewModel.getStoryContent(for: lesson),
                    onDismiss: {
                        viewModel.dismissLessonStory()
                    },
                    onCTATapped: { destination in
                        viewModel.dismissLessonStory()
                        // Handle CTA navigation based on destination
                        handleCTANavigation(destination)
                    },
                    onLessonCompleted: {
                        viewModel.markSelectedLessonCompleted()
                    },
                    onAskAI: {
                        handleAskAboutLesson(lesson)
                    }
                )
            }
        }
    }

    /// Seeds a chat about the Journey's deep-dive book.
    ///
    /// Replaces a `print()` that shipped as a dead button. Reads the title from the
    /// ViewModel so the card's label and this seed cannot drift apart.
    private func handleChatWithBook() {
        let book = viewModel.deepDiveBook
        chatViewModel.startNewConversation(
            firstMessage: "Tell me about \"\(book.title)\" by \(book.author).",
            context: "The user wants to learn about the book \"\(book.title)\" by \(book.author). Discuss its key ideas.",
            contextType: .book
        )
        showChat = true
    }

    /// Seeds a chat grounded on the lesson just finished.
    ///
    /// ⚠️ The reference is the lesson TITLE, not `lesson.id`: that id is a client-side
    /// `UUID()` regenerated on every launch and unknown to the backend, so sending it
    /// would never resolve. `ChatContextResolver._resolve_journey_lesson` matches on the
    /// backend id OR the title, and the title is the only value both sides share.
    ///
    /// This is the FIRST caller of `.journeyLesson`. The backend branch has been built,
    /// tested and deployed all along with nothing on iOS ever sending it.
    private func handleAskAboutLesson(_ lesson: Lesson) {
        viewModel.dismissLessonStory()
        chatViewModel.startNewConversation(
            firstMessage: "Explain \"\(lesson.title)\" in simple terms.",
            contextType: .journeyLesson,
            referenceId: lesson.title
        )
        showChat = true
    }

    private func handleCTANavigation(_ destination: LessonCTADestination) {
        switch destination {
        case .analyzeStock:
            // Navigate to stock analysis - implement based on your navigation
            print("Navigate to stock analysis")
        case .viewPortfolio:
            // Navigate to portfolio
            print("Navigate to portfolio")
        case .readArticle(let articleId):
            print("Navigate to article: \(articleId)")
        case .watchVideo(let videoId):
            print("Navigate to video: \(videoId)")
        case .practiceQuiz:
            print("Navigate to quiz")
        case .custom(let action):
            print("Custom action: \(action)")
        }
    }
}

#Preview {
    InvestorJourneyView()
}
