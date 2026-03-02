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
    var onItemDelete: ((ChatHistoryItem) -> Void)?
    var onDismiss: (() -> Void)?

    /// Convenience init with defaults for backward compatibility (previews)
    init(
        historyGroups: [ChatHistoryGroup]? = nil,
        isLoading: Bool = false,
        onItemTap: ((ChatHistoryItem) -> Void)? = nil,
        onItemDelete: ((ChatHistoryItem) -> Void)? = nil,
        onDismiss: (() -> Void)? = nil
    ) {
        self.historyGroups = historyGroups ?? ChatHistoryItem.sampleGroups
        self.isLoading = isLoading
        self.onItemTap = onItemTap
        self.onItemDelete = onItemDelete
        self.onDismiss = onDismiss
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
            Image(systemName: "bubble.left.and.bubble.right")
                .font(.system(size: 40))
                .foregroundColor(AppColors.textMuted)
            Text("No conversations yet")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textMuted)
            Text("Start a chat to see your history here")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Action Handlers

    private func handleItemTap(_ item: ChatHistoryItem) {
        print("📖 [History] Open chat: \(item.title)")
        onItemTap?(item)
    }

    private func handleItemMoreOptions(_ item: ChatHistoryItem) {
        print("🗑️ [History] Delete: \(item.title)")
        onItemDelete?(item)
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
