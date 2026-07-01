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

    /// Convenience init with defaults for backward compatibility (previews)
    init(
        historyGroups: [ChatHistoryGroup]? = nil,
        isLoading: Bool = false,
        onItemTap: ((ChatHistoryItem) -> Void)? = nil,
        onItemMoreOptions: ((ChatHistoryItem) -> Void)? = nil,
        onDismiss: (() -> Void)? = nil,
        searchQuery: String = ""
    ) {
        self.historyGroups = historyGroups ?? ChatHistoryItem.sampleGroups
        self.isLoading = isLoading
        self.onItemTap = onItemTap
        self.onItemMoreOptions = onItemMoreOptions
        self.onDismiss = onDismiss
        self.searchQuery = searchQuery
    }

    private var isSearching: Bool {
        !searchQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(spacing: 0) {
            if isLoading && historyGroups.isEmpty {
                Spacer()
                ProgressView()
                    .tint(AppColors.primaryBlue)
                Spacer()
            } else if historyGroups.isEmpty {
                emptyState
            } else {
                ChatHistoryList(
                    groups: historyGroups,
                    onItemTap: handleItemTap,
                    onItemMoreOptions: handleItemMoreOptions,
                    onSectionTap: handleSectionTap
                )
            }
        }
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

    private func handleSectionTap(_ section: ChatHistorySection) {
        print("📂 [History] Section: \(section.rawValue)")
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ChatHistoryView()
    }
    .preferredColorScheme(.dark)
}
