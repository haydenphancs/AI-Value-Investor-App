//
//  LearnTabControl.swift
//  ios
//
//  Molecule: the Wiser header control — a "Learn" segment plus a "Chat" ACTION.
//

import SwiftUI

/// The two halves are written out rather than driven by `ForEach(LearnTab.allCases)`, because
/// they are not the same kind of control and the shared branch was a bug:
///
///   • **Learn** is a real segment — it flips `selectedTab` and the fill says "you are here".
///   • **Chat** is an ACTION — it calls `onChatTapped`, which presents `AIChatScreen` as a
///     `fullScreenCover`. `selectedTab` never becomes `.chat`.
///
/// The old `ForEach` ran both through `selectedTab == tab`, which is **false forever** for
/// `.chat` — so Chat rendered permanently in `textMuted` with no fill, i.e. exactly like a
/// disabled segment, when it was the live action on the screen. Selection-by-fill still means
/// "you are here"; `primaryBlue` + `sparkles.2` means "this opens something". The sparkle is the
/// same glyph, size and colour as `TappableSearchBar` directly above it, so the header reads as
/// one family.
struct LearnTabControl: View {
    @Binding var selectedTab: LearnTab
    /// When set, tapping "Chat" calls this instead of switching `selectedTab` — the Wiser
    /// screen uses it to present the full-screen AIChatScreen cover. "Learn" still switches inline.
    var onChatTapped: (() -> Void)?

    var body: some View {
        HStack(spacing: 0) {
            // MARK: Learn — a selectable segment
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    selectedTab = .learn
                }
            } label: {
                Text(LearnTab.learn.rawValue)
                    .font(AppTypography.bodyEmphasis)
                    .lineLimit(1)
                    .foregroundColor(selectedTab == .learn ? AppColors.textPrimary : AppColors.textMuted)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        selectedTab == .learn
                            ? AppColors.cardBackgroundLight
                            : Color.clear
                    )
                    .cornerRadius(AppCornerRadius.medium)
                    // Without this the unselected half's hit area is the text bounds, not the
                    // segment — `Color.clear` paints nothing for hit-testing to land on.
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            // MARK: Chat — an action that opens the chat cover
            Button {
                onChatTapped?()
            } label: {
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "sparkles.2")
                        .font(AppTypography.iconDefault).fontWeight(.medium)

                    Text(LearnTab.chat.rawValue)
                        .font(AppTypography.bodyEmphasis)
                        .lineLimit(1)

                    // Says "this leaves the screen", which is the half of the meaning the
                    // sparkle alone doesn't carry.
                    Image(systemName: "chevron.right")
                        .font(AppTypography.captionEmphasis)
                }
                // One `foregroundColor` for all three: they are one label, not a glyph
                // decorating text, and letting them drift is how a control stops reading
                // as a single target.
                .foregroundColor(AppColors.primaryBlue)
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.md)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            // A Button already merges its label's children into ONE element and carries
            // `.isButton`, so `.accessibilityLabel` just renames it — no
            // `.accessibilityElement(children: .ignore)`, which would mint a fresh element and
            // can drop the Button's own activation action. Without the rename VoiceOver reads
            // three fragments ("sparkles two, Chat, chevron right") and never says it opens a chat.
            .accessibilityLabel("Chat with Cay AI")
            .accessibilityHint("Opens the Cay AI chat")
        }
        .padding(AppSpacing.xs)
        .cardSurface(cornerRadius: AppCornerRadius.large)
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selected = LearnTab.learn

        var body: some View {
            LearnTabControl(selectedTab: $selected)
                .padding()
                .background(AppColors.background)
        }
    }

    return PreviewWrapper()
}
