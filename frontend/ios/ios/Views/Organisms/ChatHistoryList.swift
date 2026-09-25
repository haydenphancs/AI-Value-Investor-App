//
//  ChatHistoryList.swift
//  ios
//
//  Organism: Full chat history list grouped by sections
//

import SwiftUI

struct ChatHistoryList: View {
    let groups: [ChatHistoryGroup]
    var onItemTap: ((ChatHistoryItem) -> Void)?
    var onItemMoreOptions: ((ChatHistoryItem) -> Void)?

    var body: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: 0, pinnedViews: []) {
                ForEach(groups) { group in
                    // Section header
                    ChatHistorySectionHeader(section: group.section)
                        .padding(.horizontal, AppSpacing.lg)
                        // Tight on purpose — the rows are compact now, and the header is an
                        // 11pt caption; the old md/xl + sm read as a gap, not a section.
                        .padding(.top, group.section == .today ? AppSpacing.sm : AppSpacing.lg)
                        .padding(.bottom, AppSpacing.xs)

                    // Items in section
                    ForEach(group.items) { item in
                        VStack(spacing: 0) {
                            ChatHistoryItemRow(
                                item: item,
                                onTap: { onItemTap?(item) },
                                onMoreOptions: { onItemMoreOptions?(item) }
                            )
                            .padding(.horizontal, AppSpacing.lg)

                            // Divider between items (not after last item in section)
                            if item.id != group.items.last?.id {
                                Divider()
                                    .overlay(AppColors.cardBackgroundLight)
                                    .padding(.horizontal, AppSpacing.lg)
                            }
                        }
                    }
                }

                // Bottom padding
                Color.clear.frame(height: AppSpacing.xxxl)
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ChatHistoryList(groups: ChatHistoryItem.sampleGroups)
    }
}
