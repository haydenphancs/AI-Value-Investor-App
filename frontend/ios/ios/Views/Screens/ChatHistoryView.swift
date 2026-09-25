//
//  ChatHistoryView.swift
//  ios
//
//  Chat history view showing all past conversations.
//  Accepts data from ChatViewModel (real) or falls back to sample data.
//

import SwiftUI

struct ChatHistoryView: View {
    var historyGroups: [ChatHistoryGroup]
    var isLoading: Bool = false
    var onItemTap: ((ChatHistoryItem) -> Void)?
    /// Fired when a row's 3-dot is tapped — the history panel opens the Pin/Rename/Delete popup.
    var onItemMoreOptions: ((ChatHistoryItem) -> Void)?
    var onDismiss: (() -> Void)?
    /// When non-empty, the list is showing SEARCH results — drives a search-specific empty state
    /// ("no matches" vs "no conversations yet").
    var searchQuery: String = ""
    /// The fetch FAILED, as opposed to succeeding with zero rows. Without this the two
    /// are indistinguishable and a network failure renders "No conversations yet" — the
    /// user reads that as "my history is gone" and is offered no way to retry.
    var loadFailed: Bool = false
    /// Retry handler for `loadFailed`. Nil hides the button (previews).
    var onRetry: (() -> Void)?

    /// Convenience init with defaults for backward compatibility (previews)
    init(
        historyGroups: [ChatHistoryGroup]? = nil,
        isLoading: Bool = false,
        onItemTap: ((ChatHistoryItem) -> Void)? = nil,
        onItemMoreOptions: ((ChatHistoryItem) -> Void)? = nil,
        onDismiss: (() -> Void)? = nil,
        searchQuery: String = "",
        loadFailed: Bool = false,
        onRetry: (() -> Void)? = nil
    ) {
        self.historyGroups = historyGroups ?? ChatHistoryItem.sampleGroups
        self.isLoading = isLoading
        self.onItemTap = onItemTap
        self.onItemMoreOptions = onItemMoreOptions
        self.onDismiss = onDismiss
        self.searchQuery = searchQuery
        self.loadFailed = loadFailed
        self.onRetry = onRetry
    }

    private var isSearching: Bool {
        !searchQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The unfiltered list failed to load and there is nothing to show: the full-height
    /// failed state. Every other failed shape (a stale list, a no-match search over a
    /// stale list) keeps its content and gets the notice above it instead.
    private var showsFailedState: Bool {
        historyGroups.isEmpty && loadFailed && !isSearching
    }

    var body: some View {
        VStack(spacing: 0) {
            // A refresh that failed AFTER a list was loaded used to be invisible: the
            // failed state only renders when the list is empty, so the stale list stood
            // in for the truth (TestFlight 2026-09-16, E6). Keep the content — stale
            // beats blank — and say so above it, with the retry. Above the branch, not
            // inside the list arm, so a search that hides every row still shows it.
            if loadFailed && !isLoading && !showsFailedState {
                InlineRetryNotice(
                    message: "Couldn\u{2019}t refresh your chats. This list may be out of date.",
                    onRetry: onRetry
                )
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.sm)
                .accessibilityIdentifier("chat.history.staleNotice")
            }
            if isLoading && historyGroups.isEmpty {
                Spacer()
                ProgressView()
                    .tint(AppColors.primaryBlue)
                Spacer()
            } else if showsFailedState {
                // A FAILURE is not an empty account — say so, and offer a way out.
                // (A no-match SEARCH during a failed refresh is still "No matches".)
                failedState
            } else if historyGroups.isEmpty {
                emptyState
            } else {
                ChatHistoryList(
                    groups: historyGroups,
                    onItemTap: handleItemTap,
                    onItemMoreOptions: handleItemMoreOptions
                )
            }
        }
    }

    // MARK: - Failed State

    private var failedState: some View {
        VStack(spacing: AppSpacing.md) {
            Spacer()
            Image(systemName: "arrow.trianglehead.2.clockwise.rotate.90")
                .font(.system(size: 40))
                .foregroundColor(AppColors.textMuted)
            Text("Couldn\u{2019}t load your chats")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
            Text("Check your connection and try again. Your conversations are safe.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
            if let onRetry {
                Button(action: onRetry) {
                    Text("Try Again")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textOnAccent)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                .fill(AppColors.primaryFill)
                        )
                }
                .padding(.top, AppSpacing.xs)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Empty State

    private var emptyState: some View {
        VStack(spacing: AppSpacing.md) {
            Spacer()
            Image(systemName: isSearching ? "magnifyingglass" : "bubble.left.and.bubble.right")
                .font(.system(size: 40))
                .foregroundColor(AppColors.textMuted)
            Text(isSearching ? "No matches" : "No conversations yet")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textMuted)
            Text(isSearching
                 ? "No conversations match \u{201C}\(searchQuery)\u{201D}"
                 : "Start a chat to see your history here")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Action Handlers

    private func handleItemTap(_ item: ChatHistoryItem) {
        print("📖 [History] Open chat: \(item.title)")
        onItemTap?(item)
    }

    private func handleItemMoreOptions(_ item: ChatHistoryItem) {
        onItemMoreOptions?(item)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ChatHistoryView()
    }
}
