//
//  TappableSearchBar.swift
//  ios
//
//  Atom: A search bar placeholder that acts as a button to navigate to search
//

import SwiftUI

/// Search only. The Cay AI door is `AskCayAIButton`, a separate card beside this one.
///
/// This briefly carried a trailing sparkle button of its own. Folding two actions into one card
/// made the glyph read as decoration rather than as a control, and the tap target it needed
/// pushed the card taller than the logo and avatar it sits between — hence the split, and hence
/// the plain `md` vertical padding below, which is what keeps the row level.
struct TappableSearchBar: View {
    var placeholder: String = "Search"
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "magnifyingglass")
                    .font(AppTypography.iconDefault).fontWeight(.medium)
                    .foregroundColor(AppColors.textMuted)
                    // Decorative: the Button's label already exposes `placeholder` to VoiceOver,
                    // so announcing the glyph too would just read the control twice.
                    .accessibilityHidden(true)

                Text(placeholder)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textMuted)
                    // The row now holds four elements, so the bar is ~205pt on a 393pt screen.
                    // Never let a longer placeholder wrap the card taller than its neighbours.
                    .lineLimit(1)

                Spacer(minLength: 0)
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.md)
            .cardSurface(cornerRadius: AppCornerRadius.large)
            // `Spacer` paints nothing for hit-testing to land on, so without this the tappable
            // area is the glyph + text bounds rather than the whole card.
            .contentShape(Rectangle())
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        TappableSearchBar()
        TappableSearchBar(placeholder: "Add tickers")
    }
    .padding()
    .background(AppColors.background)
}
