//
//  ChatHistoryItemRow.swift
//  ios
//
//  Molecule: Row for a single chat history item
//
//  Compact on purpose: meta line + the title, ~51pt at the default text size. It used to be
//  ~140pt — the 3-dot's 44pt tap-target frame sat INSIDE the meta HStack (an 11pt caption
//  line 44pt tall) and a 2-line preview of the last answer, cut mid-word server-side, hung
//  under a title that is already the user's first question. Four and a half chats per screen.
//
//  The 3-dot is a trailing OVERLAY so its 44pt frame no longer sets the meta line's height;
//  the row reserves that width on the trailing edge so a truncated title's "…" never hides
//  under the glyph, and holds a 44pt minimum height so the target is never clipped at small
//  Dynamic Type sizes (HitSlop.swift: slop is clipped by the parent's bounds).
//
//  `item.preview` is deliberately NOT rendered — it stays on the model because the history
//  search (AIChatScreen.filteredHistoryGroups) matches answer text as well as titles.
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
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                // Meta line: type badge, time ago, pinned indicator. Text-height only — the
                // 3-dot lives in the overlay below, not here.
                HStack(alignment: .center, spacing: AppSpacing.sm) {
                    ChatHistoryTypeBadge(type: item.type)

                    TimeAgoLabel(text: item.timeAgo)

                    // Pinned indicator (set via the 3-dot "Pin" option → persisted `is_saved`).
                    if item.isSaved {
                        Image(systemName: "pin.fill")
                            .font(.system(size: 10))
                            .foregroundColor(AppColors.textMuted)
                    }

                    Spacer(minLength: 0)
                }

                // Title = the user's first question (server auto-title). One line; the full
                // question is on screen the moment the chat opens.
                Text(item.title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                    .multilineTextAlignment(.leading)
            }
            // Keep the title (and its truncation "…") clear of the 3-dot overlay.
            .padding(.trailing, HitSlop.minimumTarget)
            .padding(.vertical, AppSpacing.sm)
            .frame(maxWidth: .infinity, minHeight: HitSlop.minimumTarget, alignment: .leading)
            // A Button hit-tests what its label DRAWS — without this the trailing gap and the
            // Spacer are dead, and a tap there does nothing.
            .contentShape(Rectangle())
        }
        .buttonStyle(PlainButtonStyle())
        .overlay(alignment: .trailing) {
            MoreOptionsButton {
                onMoreOptions?()
            }
            .anchorPreference(key: ChatRowMenuAnchorKey.self, value: .bounds) { anchor in
                // Publish under the stable sessionId; sample/guest rows (no sessionId)
                // get no anchor — their menu actions no-op anyway.
                item.sessionId.map { [$0: anchor] } ?? [:]
            }
        }
    }
}

#Preview {
    VStack(spacing: 0) {
        ForEach(ChatHistoryItem.todayItems) { item in
            ChatHistoryItemRow(item: item)
            Divider()
                .overlay(AppColors.cardBackgroundLight)
        }
    }
    .padding(.horizontal, AppSpacing.lg)
    .background(AppColors.background)
}
