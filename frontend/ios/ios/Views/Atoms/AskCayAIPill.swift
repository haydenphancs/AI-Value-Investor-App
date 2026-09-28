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
            // A Button hit-tests what its label draws; the outer padding widens the target
            // toward 44 pt without growing the visible capsule.
            .padding(.vertical, AppSpacing.xs)
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
