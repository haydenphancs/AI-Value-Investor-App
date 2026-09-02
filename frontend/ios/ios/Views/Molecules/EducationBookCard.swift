//
//  EducationBookCard.swift
//  ios
//
//  Molecule: Card showing an educational book with an Ask the Agent button
//

import SwiftUI

struct EducationBookCard: View {
    let book: EducationBook
    /// Whether this book is bookmarked (from BookmarkStore, keyed by title).
    var isBookmarked: Bool = false
    var onTap: (() -> Void)?
    var onChatWithBook: (() -> Void)?
    var onToggleBookmark: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Most Read badge
            if book.isMostRead {
                Text("Most Read")
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(AppColors.textSecondary)
                    .padding(.bottom, -AppSpacing.sm)
            }

            HStack(alignment: .top, spacing: AppSpacing.lg) {
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
            .contentShape(Rectangle())
            .onTapGesture {
                onTap?()
            }

            // Action button — Ask the Agent: OPENS the book chat (it does not ask anything).
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
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.extraLarge)
    }

}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.lg) {
            ForEach(EducationBook.sampleData) { book in
                EducationBookCard(book: book)
            }
        }
        .padding()
    }
    .background(AppColors.background)
}
