//
//  BookmarkedMoneyMoveRow.swift
//  ios
//
//  Molecule: the ONE saved Money Move topic, as a shortcut row under the Featured Deep Dive hero.
//  Mirrors the Book Library hero card's "🔖 <title> ›" affordance, scaled down to its own row.
//

import SwiftUI

struct BookmarkedMoneyMoveRow: View {
    let article: MoneyMoveArticle
    var onTap: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // `primaryBlue` is a TEXT-role token (4.5:1 in both appearances) — correct for a
            // glyph read as meaning. A *Graphic token here would fail AA.
            Image(systemName: "bookmark.fill")
                .font(AppTypography.iconDefault)
                .foregroundColor(AppColors.primaryBlue)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Saved")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)

                Text(article.title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
            }

            Spacer(minLength: AppSpacing.sm)

            Image(systemName: "chevron.right")
                .font(AppTypography.iconSmall)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(AppSpacing.md)
        // A SIBLING of the hero card above, not a child of it — so the plain surface is right.
        // `cardBackgroundNested` is for a card drawn INSIDE another card, where the shared fill
        // measures 1.00:1 against its parent in dark and the row vanishes.
        .cardSurface(cornerRadius: AppCornerRadius.extraLarge)
        .contentShape(RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge))
        .onTapGesture { onTap?() }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Saved topic, \(article.title)")
        .accessibilityAddTraits(.isButton)
    }
}

#Preview {
    VStack {
        BookmarkedMoneyMoveRow(article: .sampleDigitalFinance)
            .padding(AppSpacing.lg)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(AppColors.background)
}
