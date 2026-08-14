//
//  SearchBookCard.swift
//  ios
//
//  Molecule: Card displaying a book in search results with AI actions
//

import SwiftUI

struct SearchBookCard: View {
    let book: SearchBookItem
    /// Whether this book is bookmarked (from BookmarkStore, keyed by title).
    var isBookmarked: Bool = false
    var onChatWithBook: (() -> Void)?
    var onToggleBookmark: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            HStack(alignment: .top, spacing: AppSpacing.lg) {
                // Was `Image(book.coverImageName)` over a gradient, with the title
                // fallback hardcoded to `.opacity(0)`. No such asset has ever existed in
                // Assets.xcassets, so this card rendered a bare gradient with NO cover
                // and NO title — the only one of the four that showed nothing at all.
                BookCoverImage(title: book.title)

                // Book details
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    // Title and rating
                    HStack(alignment: .top) {
                        Text(book.title)
                            .font(AppTypography.headingSmall)
                            .foregroundColor(AppColors.textPrimary)
                            .lineLimit(2)

                        Spacer()

                        // Bookmark toggle (replaces the old star rating)
                        BookmarkButton(isBookmarked: isBookmarked, size: 18) {
                            onToggleBookmark?()
                        }
                    }

                    // Author
                    Text(book.author)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)

                    // Description
                    Text(book.description)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(2)

                    // Meta info
                    HStack(spacing: AppSpacing.md) {
                        Image(systemName: "book.closed.fill")
                            .font(AppTypography.iconTiny)
                            .foregroundColor(AppColors.textMuted)

                        Text(book.formattedPages)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        Text("•")
                            .foregroundColor(AppColors.textMuted)

                        Text(book.formattedPublished)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            }

            // Action buttons
            HStack(spacing: AppSpacing.md) {
                Button(action: {
                    onChatWithBook?()
                }) {
                    HStack(spacing: AppSpacing.sm) {
                        Image(systemName: "bubble.left.fill")
                            .font(AppTypography.iconXS)

                        Text("Ask the Author Agent")
                            .font(AppTypography.bodySmallEmphasis)
                            .lineLimit(1)
                            .minimumScaleFactor(0.85)
                    }
                    .foregroundColor(AppColors.primaryBlue)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(AppColors.primaryBlue.opacity(0.15))
                    .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.extraLarge)
    }

}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.lg) {
            ForEach(SearchBookItem.sampleData) { book in
                SearchBookCard(book: book)
            }
        }
        .padding()
    }
    .background(AppColors.background)
}
