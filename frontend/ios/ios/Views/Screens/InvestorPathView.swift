//
//  InvestorPathView.swift
//  ios
//
//  The Investor Path - Full learning journey screen from Novice to Master
//

import SwiftUI

struct InvestorPathView: View {
    @StateObject private var viewModel = InvestorPathViewModel()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main content
            VStack(spacing: 0) {
                // Header
                InvestorPathHeader(
                    completedLessons: viewModel.totalLessonsCompleted,
                    totalLessons: viewModel.totalLessons,
                    onBackTapped: {
                        dismiss()
                    }
                )

                // Scrollable content
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
                        // Level 1: Foundation
                        if let foundationLevel = viewModel.getLevelProgress(for: .foundation) {
                            InvestorPathLevelSection(
                                levelProgress: foundationLevel,
                                onLessonTap: { lesson in
                                    viewModel.selectLesson(lesson)
                                }
                            )
                        }

                        // Level 2: Analysis
                        if let analysisLevel = viewModel.getLevelProgress(for: .analysis) {
                            InvestorPathLevelSection(
                                levelProgress: analysisLevel,
                                onLessonTap: { lesson in
                                    viewModel.selectLesson(lesson)
                                }
                            )
                        }

                        // Chat with book prompt (between Level 2 and 3)
                        ChatWithBookPromptCard {
                            viewModel.openChatWithBook()
                        }
                        .padding(.horizontal, AppSpacing.lg)

                        // Level 3: Strategies
                        if let strategiesLevel = viewModel.getLevelProgress(for: .strategies) {
                            InvestorPathLevelSection(
                                levelProgress: strategiesLevel,
                                onLessonTap: { lesson in
                                    viewModel.selectLesson(lesson)
                                }
                            )
                        }

                        // Level 4: Mastery
                        if let masteryLevel = viewModel.getLevelProgress(for: .mastery) {
                            InvestorPathLevelSection(
                                levelProgress: masteryLevel,
                                onLessonTap: { lesson in
                                    viewModel.selectLesson(lesson)
                                }
                            )
                        }

                        // Study Schedule section
                        InvestorPathStudyScheduleSection(
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
            }

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .navigationBarHidden(true)
    }
}

// MARK: - Loading Overlay
private struct LoadingOverlay: View {
    var body: some View {
        ZStack {
            Color.black.opacity(0.3)
                .ignoresSafeArea()

            ProgressView()
                .progressViewStyle(CircularProgressViewStyle(tint: .white))
                .scaleEffect(1.2)
        }
    }
}

#Preview {
    InvestorPathView()
        .preferredColorScheme(.dark)
}
