//
//  AskCayAIPill.swift
//  ios
//
//  Atom: a small labelled "Ask Cay AI" capsule for a card footer.
//
//  The header's `AskCayAIButton` is an icon-only tile for the app-wide chat; this is the
//  labelled form a CARD uses to open a chat about that card, where a bare sparkle would not
//  say what it does. Knows nothing about what it opens — the caller decides.
//

import SwiftUI

struct AskCayAIPill: View {
    var title: String = "Ask Cay AI"
    let action: () -> Void

    /// The hit frame's floor: the app's in-card target size (`DiversificationCard`'s
    /// `segmentMinHeight`, ~34 pt — the 44 pt HIG figure read as oversized in a card). The
    /// old 4 pt pad around a ~21 pt capsule left a ~29 pt target on the Insights card, whose
    /// own tap opens Sources — a near miss opened the wrong sheet.
    static let minHitHeight: CGFloat = 18 + 2 * AppSpacing.sm

    var body: some View {
        Button(action: action) {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: AppSymbols.ai)
                    .font(AppTypography.caption)
                Text(title)
                    .font(AppTypography.captionEmphasis)
                    .lineLimit(1)
            }
            // `primaryBlue` is a TEXT-role token, audited at 4.5:1 on `.cardSurface()`. The
            // capsule is an OUTLINE, not a tinted fill: a blue wash behind blue text would
            // pull the pair under AA in light mode.
            .foregroundColor(AppColors.primaryBlue)
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.xs)
            .overlay(
                Capsule().strokeBorder(AppColors.primaryBlue.opacity(0.45), lineWidth: 1)
            )
            // A Button hit-tests what its label draws, and clips its hit region to its own
            // frame (a hitSlop / negative padding does not enlarge it). A min-height frame
            // AFTER the outline keeps the visible capsule its size; `.contentShape` LAST makes
            // the whole frame tappable.
            .frame(minHeight: Self.minHitHeight)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityHint("Opens a chat with Cay AI")
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        AskCayAIPill(action: {})
        AskCayAIPill(title: "Ask about this trend", action: {})
    }
    .padding()
    .cardSurface()
    .padding()
    .background(AppColors.background)
}
