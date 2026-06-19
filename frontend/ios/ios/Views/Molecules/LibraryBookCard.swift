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
    var onChatWithBook: (() -> Void)?
    var onReview: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            HStack(alignment: .top, spacing: AppSpacing.lg) {
                // Book cover with optional mastered badge
                ZStack(alignment: .topTrailing) {
                    // Book cover
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(bookCoverGradient)
                            .frame(width: 80, height: 110)

                        // Book title overlay
                        VStack {
                            Text(book.title.uppercased())
                                .font(AppTypography.captionTiny).fontWeight(.bold)
                                .foregroundColor(.white)
                                .multilineTextAlignment(.center)
                                .lineLimit(3)
                                .padding(.horizontal, AppSpacing.xs)
                        }
                        .frame(width: 80, height: 110)
                    }

                    // Mastered checkmark badge
                    if isMastered {
                        ZStack {
                            Circle()
                                .fill(Color(hex: "14A349"))
                                .frame(width: 24, height: 24)
                                

                            Image(systemName: "checkmark")
                                .font(AppTypography.iconXS).fontWeight(.bold)
                                .foregroundColor(.white)
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

                        // Rating
                        HStack(spacing: AppSpacing.xxs) {
                            Image(systemName: "star.fill")
                                .font(AppTypography.iconXS)
                                .foregroundColor(AppColors.neutral)

                            Text(book.formattedRating)
                                .font(AppTypography.labelSmallEmphasis)
                                .foregroundColor(AppColors.textPrimary)
                        }
                        .padding(.horizontal, AppSpacing.sm)
                        .padding(.vertical, AppSpacing.xs)
                        .background(AppColors.cardBackgroundLight)
                        .cornerRadius(AppCornerRadius.small)
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
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.extraLarge)
    }

    private var bookCoverGradient: LinearGradient {
        LinearGradient(
            colors: [
                Color(hex: book.coverGradientStart),
                Color(hex: book.coverGradientEnd)
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
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
    .preferredColorScheme(.dark)
}
