//
//  ChatHistoryView.swift
//  ios
//
//  Chat history view showing all past conversations
//

import SwiftUI

struct ChatHistoryView: View {
    @State private var historyGroups: [ChatHistoryGroup] = ChatHistoryItem.sampleGroups
    var onItemTap: ((ChatHistoryItem) -> Void)?
    var onDismiss: (() -> Void)?

    var body: some View {
        VStack(spacing: 0) {
            // History list
            ChatHistoryList(
                groups: historyGroups,
                onItemTap: handleItemTap,
                onItemMoreOptions: handleItemMoreOptions,
                onSectionTap: handleSectionTap
            )
        }
    }

    // MARK: - Action Handlers
    private func handleItemTap(_ item: ChatHistoryItem) {
        print("Open chat history: \(item.title)")
        onItemTap?(item)
    }

    private func handleItemMoreOptions(_ item: ChatHistoryItem) {
        print("More options for: \(item.title)")
    }

    private func handleSectionTap(_ section: ChatHistorySection) {
        print("Section tapped: \(section.rawValue)")
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
