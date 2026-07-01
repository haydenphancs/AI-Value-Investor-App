//
//  ChatHistoryItemRow.swift
//  ios
//
//  Molecule: Row for a single chat history item
//

import SwiftUI

/// Carries each row's 3-dot button bounds up to the history panel so the floating Liquid-Glass
/// options popup can anchor itself directly beneath the button that opened it. Keyed by the STABLE
/// backend `sessionId` (NOT the per-instance `ChatHistoryItem.id`, which is regenerated on every
/// history regroup) so a `loadHistory` landing while the menu is open can't orphan the anchor.
struct ChatRowMenuAnchorKey: PreferenceKey {
    static var defaultValue: [String: Anchor<CGRect>] = [:]
    static func reduce(value: inout [String: Anchor<CGRect>], nextValue: () -> [String: Anchor<CGRect>]) {
        value.merge(nextValue()) { $1 }
    }
}

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

                    // Pinned indicator (set via the 3-dot "Pin" option → persisted `is_saved`).
                    if item.isSaved {
                        Image(systemName: "pin.fill")
                            .font(.system(size: 10))
                            .foregroundColor(AppColors.textMuted)
                    }

                    Spacer()

                    MoreOptionsButton {
                        onMoreOptions?()
                    }
                    .anchorPreference(key: ChatRowMenuAnchorKey.self, value: .bounds) { anchor in
                        // Publish under the stable sessionId; sample/guest rows (no sessionId)
                        // get no anchor — their menu actions no-op anyway.
                        item.sessionId.map { [$0: anchor] } ?? [:]
                    }
                }

                // Title
                Text(item.title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                    .multilineTextAlignment(.leading)

                // Preview
                Text(item.preview)
                    .font(AppTypography.bodySmall)
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
