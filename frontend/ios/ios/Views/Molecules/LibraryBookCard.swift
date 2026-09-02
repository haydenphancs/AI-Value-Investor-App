//
//  LibraryBookCard.swift
//  ios
//
//  Molecule: Card showing a library book with mastered/unread states
//

import SwiftUI

struct LibraryBookCard: View {
    let book: LibraryBook
    /// Whether every core in the book has been completed (real progress, from BookProgressStore).
    var isMastered: Bool = false
    /// Whether this book is bookmarked (from BookmarkStore, keyed by title).
    var isBookmarked: Bool = false
    var onChatWithBook: (() -> Void)?
    var onToggleBookmark: (() -> Void)?
    var onReview: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            HStack(alignment: .top, spacing: AppSpacing.lg) {
                // Book cover with optional mastered badge
                ZStack(alignment: .topTrailing) {
                    BookCoverImage(
                        title: book.title,
                        fallbackGradient: (book.coverGradientStart, book.coverGradientEnd)
                    )

                    // Mastered checkmark badge
                    //
                    // `gainFill`, not `gain` — the two are BYTE-EQUAL (asserted by
                    // `test_each_adaptive_fill_is_byte_equal_to_its_text_counterpart`), so this
                    // badge does not change colour. What changes is the ink: `gain` is a TEXT
                    // token, so no fill rule was in jurisdiction here and the white glyph sat at
                    // 2.28:1 on #22C55E in dark. Naming the fill token puts the pair under
                    // `carries: .onFill` and takes the checkmark to 7.79.
                    if isMastered {
                        ZStack {
                            Circle()
                                .fill(AppColors.gainFill)
                                .frame(width: 24, height: 24)


                            Image(systemName: "checkmark")
                                .font(AppTypography.iconXS).fontWeight(.bold)
                                .foregroundColor(AppColors.textOnFill)
                        }
                        .offset(x: 6, y: -6)
                    }
                }

                // Book details
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    // Curriculum order badge
                    Text("BOOK \(book.curriculumOrder)")
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                        .tracking(0.5)

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
                        Text(book.formattedKeyIdeas)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        Text("•")
                            .foregroundColor(AppColors.textMuted)

                        Text(book.formattedPages)
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

                        Text("Ask the Agent")
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

                // Mastered books expose a Review action; otherwise the Ask button stands alone
                if isMastered {
                    Button(action: {
                        onReview?()
                    }) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "checkmark.circle.fill")
                                .font(AppTypography.iconXS)

                            Text("Review")
                                .font(AppTypography.bodySmallEmphasis)
                        }
                        .foregroundColor(AppColors.bullish)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.md)
                        .background(AppColors.bullish.opacity(0.15))
                        .cornerRadius(AppCornerRadius.medium)
                    }
                    .buttonStyle(PlainButtonStyle())
                }
            }
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.extraLarge)
    }

}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.lg) {
            // Mastered book
            LibraryBookCard(book: LibraryBook.sampleData[0], isMastered: true)
            // Unread book
            LibraryBookCard(book: LibraryBook.sampleData[2])
        }
        .padding()
    }
    .background(AppColors.background)
}
