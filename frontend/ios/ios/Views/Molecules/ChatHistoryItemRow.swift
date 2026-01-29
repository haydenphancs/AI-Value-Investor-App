//
//  ChatHistoryItemRow.swift
//  ios
//
//  Molecule: Row for a single chat history item
//

import SwiftUI

struct ChatHistoryItemRow: View {
    let item: ChatHistoryItem
    var onTap: (() -> Void)?
    var onMoreOptions: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Top row: Type badge, time ago, more button
                HStack(alignment: .center) {
                    ChatHistoryTypeBadge(type: item.type)

                    TimeAgoLabel(text: item.timeAgo)

                    Spacer()

                    MoreOptionsButton {
                        onMoreOptions?()
                    }
                }

                // Title
                Text(item.title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                    .multilineTextAlignment(.leading)

                // Preview
                Text(item.preview)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
            }
            .padding(.vertical, AppSpacing.md)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack(spacing: 0) {
        ForEach(ChatHistoryItem.todayItems) { item in
            ChatHistoryItemRow(item: item)
            Divider()
                .background(AppColors.cardBackgroundLight)
        }
    }
    .padding(.horizontal, AppSpacing.lg)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
