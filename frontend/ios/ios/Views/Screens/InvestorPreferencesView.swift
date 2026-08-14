//
//  InvestorPreferencesView.swift
//  ios
//
//  Screen: edit the learning preferences Cay AI uses to decide what to cover first and
//  how to word an explanation.
//
//  These questions were previously asked ONCE, during first-run onboarding, and could
//  never be revisited — onboarding is gated behind `@AppStorage("has_completed_onboarding")`.
//  The Settings row nevertheless told readers to "add some interests in Settings", which
//  was unfollowable. This is that screen.
//
//  Deliberately the same wording and the same chips as onboarding (`FlowOptionChips`,
//  promoted out of `OnboardingView` for exactly this reason), so a reader recognises the
//  questions they already answered rather than meeting new ones.
//

import SwiftUI

struct InvestorPreferencesView: View {
    @StateObject private var viewModel = InvestorPreferencesViewModel()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: AppSpacing.xl) {
                header

                if viewModel.hasLoadFailed {
                    unavailableNotice
                } else {
                    section("Where are you at?") {
                        FlowOptionChips(
                            options: InvestorExperienceLevel.allCases,
                            title: { $0.title },
                            isSelected: { viewModel.experienceLevel == $0 },
                            // Tapping the selected chip clears it — nothing here is
                            // required, so every answer must be un-answerable. Same rule
                            // as onboarding.
                            onTap: { viewModel.experienceLevel = (viewModel.experienceLevel == $0) ? nil : $0 }
                        )
                    }

                    section("Language") {
                        FlowOptionChips(
                            options: InvestorExplanationStyle.allCases,
                            title: { $0.title },
                            isSelected: { viewModel.explanationStyle == $0 },
                            onTap: { viewModel.explanationStyle = (viewModel.explanationStyle == $0) ? nil : $0 }
                        )
                    }

                    section("Answer length") {
                        FlowOptionChips(
                            options: InvestorAnswerDepth.allCases,
                            title: { $0.title },
                            isSelected: { viewModel.answerDepth == $0 },
                            onTap: { viewModel.answerDepth = (viewModel.answerDepth == $0) ? nil : $0 }
                        )
                    }

                    section("Subjects you find interesting") {
                        FlowOptionChips(
                            options: InvestorTopic.allCases,
                            title: { $0.title },
                            isSelected: { viewModel.topics.contains($0) },
                            onTap: { toggle($0, in: &viewModel.topics) }
                        )
                    }

                    section("What you want to get better at") {
                        FlowOptionChips(
                            options: InvestorLearningGoal.allCases,
                            title: { $0.title },
                            isSelected: { viewModel.learningGoals.contains($0) },
                            onTap: { toggle($0, in: &viewModel.learningGoals) }
                        )
                    }

                    footerNote
                }
            }
            .padding(.vertical, AppSpacing.lg)
        }
        .background(AppColors.background.ignoresSafeArea())
        .navigationTitle("Learning Preferences")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") {
                    Task {
                        await viewModel.save()
                        if viewModel.errorMessage == nil { dismiss() }
                    }
                }
                // Disabled until a load lands: saving the empty in-memory state over a
                // stored profile would clear answers the reader never touched.
                .disabled(!viewModel.canSave)
            }
        }
        .task { await viewModel.load() }
        .alert(
            "Couldn't save",
            isPresented: Binding(
                get: { viewModel.errorMessage != nil },
                set: { if !$0 { viewModel.errorMessage = nil } }
            )
        ) {
            Button("OK", role: .cancel) { viewModel.errorMessage = nil }
        } message: {
            Text(viewModel.errorMessage ?? "")
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("How would you like Cay AI to explain things?")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
            Text("These only change what gets covered first and how it is worded. They never change our analysis, ratings or estimates — those are the same for everyone.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    private var unavailableNotice: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Couldn't load your preferences")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
            // Never render the chips on a failed load: they would show every option
            // unselected, which reads as "you have chosen nothing" — and Save would then
            // write that over a stored profile.
            Text("Check your connection and try again. Nothing has been changed.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
            Button("Try again") { Task { await viewModel.load() } }
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.primaryBlue)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    private var footerNote: some View {
        Text("Leave anything blank if you would rather not say. You can change these at any time.")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
            .padding(.horizontal, AppSpacing.lg)
    }

    @ViewBuilder
    private func section<Content: View>(
        _ title: String, @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text(title)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)
            content()
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    private func toggle<T: Hashable>(_ value: T, in set: inout Set<T>) {
        if set.contains(value) { set.remove(value) } else { set.insert(value) }
    }
}

#Preview {
    NavigationStack {
        InvestorPreferencesView()
    }
}
