//
//  TappableSearchBar.swift
//  ios
//
//  Atom: A search bar placeholder that acts as a button to navigate to search
//

import SwiftUI

struct TappableSearchBar: View {
    var placeholder: String = "Search ticker or ask AI..."
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.sm) {
                // `sparkles.2` in `primaryBlue`, NOT a grey magnifying glass — this control is
                // the app's global Cay AI entry, and its collapsed and expanded states used to
                // disagree about that. Tapping it opens `SearchView`, whose field renders this
                // exact glyph (SearchHeader.swift) at the SAME font and weight; the two now read
                // as one control. A magnifier here made the placeholder's "…or ask Cay AI" a
                // claim the icon contradicted, so the AI entry point read as plain search.
                //
                // `primaryBlue` is a TEXT-role token (4.52:1) and is already audited against this
                // `.cardSurface()` — the same pairing SearchHeader ships. Do not swap it for a
                // `*Graphic` token: those clear 3:1 only and must not carry meaning.
                Image(systemName: "sparkles.2")
                    .font(AppTypography.iconDefault).fontWeight(.medium)
                    .foregroundColor(AppColors.primaryBlue)
                    // Decorative: the Button's label already exposes `placeholder` to VoiceOver,
                    // so announcing the glyph too would just read the control twice.
                    .accessibilityHidden(true)

                Text(placeholder)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textMuted)

                Spacer()
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.md)
            .cardSurface(cornerRadius: AppCornerRadius.large)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack {
        TappableSearchBar()
        TappableSearchBar(placeholder: "Search or ask Cay AI...")
    }
    .padding()
    .background(AppColors.background)
}
