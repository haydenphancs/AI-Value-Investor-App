//
//  AskCayAIButton.swift
//  ios
//
//  Atom: the Cay AI door in the global header — a sparkle on its own tile.
//

import SwiftUI

/// Sits between `TappableSearchBar` and the profile avatar in `GlobalHeaderView`.
///
/// It used to be a trailing sparkle INSIDE the search bar. Two things were wrong with that:
/// inside a text field a glyph reads as ornament rather than as a button, and the tap target it
/// needed pushed the bar's vertical padding down while still leaving the card taller than the
/// logo and avatar beside it.
///
/// **Sized and cut to match its NEIGHBOURS, not the search bar.** The header row is a set of
/// three 36pt marks — `CaydexLogoMark(size: 36)` on the left, this tile, and `ProfileAvatarView`
/// at 36 on the right. Sizing this one off the search bar's padding instead produced a 44pt tile
/// with a 14pt radius: measurably 8pt taller and visibly rounder than the avatar beside it, which
/// is exactly how it looked. It now shares the avatar's side length AND its
/// `CaydexLogoMark.iconCornerRatio`, so the three read as one family.
struct AskCayAIButton: View {
    var onTap: (() -> Void)?

    /// The header's icon size. The same 36 `LogoView` and `ProfileAvatarView` already use — this
    /// is the row's established mark size, not a new number.
    private static let iconSide: CGFloat = 36

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            // `primaryBlue` is a TEXT-role token (4.52:1) already audited on this
            // `.cardSurface()`. Not a `*Graphic` token: those clear 3:1 only and must never be
            // the sole carrier of meaning, which here it is — there is no label beside it.
            Image(systemName: "sparkles.2")
                .font(AppTypography.iconDefault).fontWeight(.medium)
                .foregroundColor(AppColors.primaryBlue)
                .frame(width: Self.iconSide, height: Self.iconSide)
                .cardSurface(cornerRadius: Self.iconSide * CaydexLogoMark.iconCornerRatio)
                // 36×36, the same target the avatar button beside it has always had.
                //
                // A `.frame(maxHeight: .infinity)` was tried here to stretch the hit area to the
                // row height for free. It does NOT stay inside the row: the unbounded height
                // preference propagates up through the HStack and the whole header grew to fill
                // the screen. A `minHeight` on the row cannot cap that — only a fixed height
                // could, and that would then fight Dynamic Type.
                .contentShape(Rectangle())
        }
        .buttonStyle(PlainButtonStyle())
        // The glyph is the whole control, so without this VoiceOver announces "sparkles two"
        // and never says what it opens.
        .accessibilityLabel("Chat with Cay AI")
        .accessibilityHint("Opens the Cay AI chat")
    }
}

#Preview {
    HStack(spacing: AppSpacing.md) {
        LogoView()
        TappableSearchBar(placeholder: "Search")
        AskCayAIButton()
        ProfileAvatarView(avatarUrl: nil, size: 36)
    }
    .padding()
    .background(AppColors.background)
}
