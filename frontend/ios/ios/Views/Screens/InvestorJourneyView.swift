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
                            // ⚠️ The Study Schedule section is intentionally NOT rendered.
                            // Every control in it was inert: both time rows were empty
                            // closures with a "show time picker" comment, and the section
                            // wrote straight through a `@Binding`, so
                            // `InvestorPathViewModel.updateMorningSessionTime` /
                            // `updateReviewTime` / `toggleDailyReminder` were never called
                            // and `saveSchedule()` never ran — the toggle reset to its
                            // default the moment the `@StateObject` was recreated.
                            //
                            // "Daily Reminder" is the part that made this worse than dead
                            // UI: it promises a notification, and there is no such kind in
                            // `notification_kinds.py` (9 kinds, none of them a study
                            // reminder), so nothing could ever have been scheduled. Adding
                            // one is a feature with a registry entry, a preference key, a
                            // sender and a settings toggle — not a bug fix. The organism
                            // and the ViewModel methods are left in place, ready for it.

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
                        // Capture the lesson BEFORE dismissing — `dismissLessonStory()`
                        // clears `selectedLesson`, so reading it afterwards always gave nil
                        // and the CTA lost the only context that makes it specific.
                        let lessonTitle = viewModel.selectedLesson?.title
                        viewModel.dismissLessonStory()
                        handleCTANavigation(destination, lessonTitle: lessonTitle)
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
        // OPENS the chat, empty and grounded — no auto-sent question. Unlike the seeding
        // path there is no in-flight guard to lose to: preparing always takes, and it
        // deliberately replaces whatever the shared view model was holding, because that is
        // what tapping this card asks for. Any abandoned turn is already persisted
        // server-side and reachable from history.
        guard let library = LibraryBook.sampleData.first(
            where: { $0.curriculumOrder == book.curriculumOrder }
        ) else {
            chatViewModel.prepareGroundedConversation()
            showChat = true
            return
        }
        chatViewModel.prepareGroundedConversation(
            context: library.studyGuideContext(),
            contextType: .book,
            referenceId: String(library.curriculumOrder)
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
        // `context:` is a deliberate fallback, not redundancy. The backend resolves the
        // grounding block from the LIVE journey catalog; if that is unseeded or degrades to
        // `lessons: []`, `_resolve_journey_lesson` returns None and `resolve` falls back to
        // `client_context`. Without this the model would receive nothing while the UI still
        // showed a "Grounded on Lesson" chip — an answer that overclaims what it read.
        guard chatViewModel.startNewConversation(
            firstMessage: "Explain \"\(lesson.title)\" in simple terms.",
            context: "The user just finished the lesson \"\(lesson.title)\". \(lesson.description)",
            contextType: .journeyLesson,
            referenceId: lesson.title
        ) else { return }
        showChat = true
    }

    /// Acts on the lesson-completion CTA.
    ///
    /// Every arm of this used to be a bare `print()`, so in release the lesson simply closed
    /// and the advertised destination was never reached — the same dead-button class that was
    /// already fixed once for the deep-dive book card and left in place here.
    ///
    /// All six route into Cay AI rather than to another tab, deliberately. There is no
    /// cross-tab navigation affordance in the app (the tab selection is `@State` in
    /// `ContentView`, not routable from a Learn sub-screen), and two of the destinations name
    /// features that do not exist at all — there is no quiz engine and no video player. Chat
    /// is a real, reachable destination that is already wired on this screen, and it can
    /// genuinely do each of these things. Seeds are written so the answer continues the
    /// lesson rather than restating it.
    private func handleCTANavigation(_ destination: LessonCTADestination, lessonTitle: String?) {
        let lesson = lessonTitle.map { " I just finished the lesson \"\($0)\"." } ?? ""
        let seed: String
        switch destination {
        case .analyzeStock:
            seed = "Walk me through how to analyse a stock, step by step.\(lesson)"
        case .viewPortfolio:
            seed = "How should I think about reviewing a portfolio as a whole?\(lesson)"
        case .practiceQuiz:
            seed = lessonTitle.map { "Quiz me on \"\($0)\" — ask me one question at a time." }
                ?? "Quiz me on what I have been learning — one question at a time."
        case .readArticle(let articleId):
            seed = "Explain the key ideas in \"\(articleId)\".\(lesson)"
        case .watchVideo(let videoId):
            seed = "Explain \"\(videoId)\" to me.\(lesson)"
        case .custom(let action):
            seed = "\(action)\(lesson)"
        }
        guard chatViewModel.startNewConversation(firstMessage: seed) else { return }
        showChat = true
    }
}

#Preview {
    InvestorJourneyView()
}
