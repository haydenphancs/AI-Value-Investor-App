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

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Bumped on tap to fire the discrete bounce. Never bumped under Reduce Motion —
    /// `.symbolEffect(.bounce, value:)` only animates on a CHANGE, so a frozen counter is a
    /// still glyph with no second code path to keep in sync.
    @State private var bounceTrigger = 0

    /// Deliberately 40, where `LogoView` and `ProfileAvatarView` are 36 — an OPTICAL match, not
    /// a mathematical one.
    ///
    /// Its neighbours are full-bleed art (a dark logo tile, a photographic avatar) that fill
    /// their squares edge to edge. This tile is a `.cardSurface()` whose light-mode fill is
    /// near-white on a near-white page, so its bounds are barely visible and an equal 36 read as
    /// smaller than both. 44 was tried first and read as clearly LARGER than the avatar; 40 is
    /// the middle that reads level.
    ///
    /// Safe for layout: the row's `globalHeaderRowHeight()` floor is 44, so a 40pt tile still
    /// fits without the header changing height on any tab.
    private static let iconSide: CGFloat = 40

    var body: some View {
        Button(action: {
            if !reduceMotion { bounceTrigger += 1 }
            onTap?()
        }) {
            // `primaryBlue` is a TEXT-role token (4.52:1) already audited on this
            // `.cardSurface()`. Not a `*Graphic` token: those clear 3:1 only and must never be
            // the sole carrier of meaning, which here it is — there is no label beside it.
            Image(systemName: "sparkles.2")
                // `iconLarge` (20pt), NOT `iconDefault` (16pt). The neighbouring logo and avatar
                // are full-bleed art filling their whole 36pt tile; a 16pt glyph fills 0.44 of
                // this one — the lowest ratio in the app (IconTile 0.50, CayAIAvatar 0.52) — so
                // the tile read as SMALLER than the avatar even though both measure 36.0pt. 20pt
                // puts it at 0.56.
                //
                // A scaling token is safe here despite the fixed tile, which is the opposite of
                // IconTile's call: `iconLarge` is `scaledTight`, capped at `dataCap` 1.25×, so the
                // glyph tops out at 25pt inside 36pt and cannot overflow — while still growing for
                // low-vision users who are not on VoiceOver.
                .font(AppTypography.iconLarge).fontWeight(.medium)
                .foregroundColor(AppColors.primaryBlue)
                // Idle: a slow whole-symbol scale, so the app's one AI entry point reads as live.
                //
                // `.breathe` rather than `.variableColor` deliberately — variable colour needs the
                // symbol to ship layered variants, and if `sparkles.2` lacks them the effect is a
                // SILENT no-op that looks identical to never having added it. Breathe scales the
                // whole symbol and cannot fail that way.
                //
                // ⚠️ `.plain` IS LOAD-BEARING — do not "simplify" it to a bare `.breathe`.
                // The default variant is `.pulse`, which animates OPACITY as well as scale.
                // Measured on the simulator, that took this glyph to **1.40:1** against its tile
                // at the dim end of every cycle, versus the 4.5:1 AA bar `primaryBlue` is picked
                // to clear at 4.52. The glyph is the SOLE carrier of meaning here — no label sits
                // beside it — so for part of every cycle the app's AI entry point was invisible to
                // a low-vision user. `.plain` scales only and holds contrast flat.
                // `ThemeContrastAudit` cannot catch this: it measures static token pairs and has
                // no way to see an animated opacity.
                //
                // Stopped with `isActive:`, never by dropping the modifier conditionally: removing
                // a modifier re-identifies the view and cancels the animation mid-cycle.
                .symbolEffect(.breathe.plain, isActive: !reduceMotion)
                // Discrete: one bounce on tap. Largely occluded in practice — the chat cover slides
                // up immediately — so it plays under the presentation transition.
                .symbolEffect(.bounce, value: bounceTrigger)
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
