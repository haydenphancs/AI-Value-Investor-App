//
//  LibraryBookCard.swift
//  ios
//
//  Molecule: Card showing a library book with mastered/unread states
//

import SwiftUI

struct LibraryBookCard: View {
    let book: LibraryBook
    var onChatWithBook: (() -> Void)?
    var onReadKeyIdeas: (() -> Void)?
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
                                .font(.system(size: 8, weight: .bold))
                                .foregroundColor(.white)
                                .multilineTextAlignment(.center)
                                .lineLimit(3)
                                .padding(.horizontal, AppSpacing.xs)
                        }
                        .frame(width: 80, height: 110)
                    }

                    // Mastered checkmark badge
                    if book.isMastered {
                        ZStack {
                            Circle()
                                .fill(Color(hex: "14A349"))
                                .frame(width: 24, height: 24)
                                

                            Image(systemName: "checkmark")
                                .font(.system(size: 12, weight: .bold))
                                .foregroundColor(.white)
                        }
                        .offset(x: 6, y: -6)
                    }
                }

                // Book details
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    // Curriculum order badge
                    Text("BOOK \(book.curriculumOrder)")
                        .font(AppTypography.captionBold)
                        .foregroundColor(AppColors.primaryBlue)
                        .tracking(0.5)

                    // Title and rating
                    HStack(alignment: .top) {
                        Text(book.title)
                            .font(AppTypography.headline)
                            .foregroundColor(AppColors.textPrimary)
                            .lineLimit(2)

                        Spacer()

                        // Rating
                        HStack(spacing: AppSpacing.xxs) {
                            Image(systemName: "star.fill")
                                .font(.system(size: 12))
                                .foregroundColor(AppColors.neutral)

                            Text(book.formattedRating)
                                .font(AppTypography.footnoteBold)
                                .foregroundColor(AppColors.textPrimary)
                        }
                        .padding(.horizontal, AppSpacing.sm)
                        .padding(.vertical, AppSpacing.xs)
                        .background(AppColors.cardBackgroundLight)
                        .cornerRadius(AppCornerRadius.small)
                    }

                    // Author
                    Text(book.author)
                        .font(AppTypography.callout)
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
                            .font(.system(size: 12))

                        Text("Chat with Book")
                            .font(AppTypography.calloutBold)
                    }
                    .foregroundColor(AppColors.textPrimary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(AppColors.cardBackgroundLight)
                    .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(PlainButtonStyle())

                // Dynamic button based on mastered state
                if book.isMastered {
                    Button(action: {
                        onReview?()
                    }) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.system(size: 12))

                            Text("Review")
                                .font(AppTypography.calloutBold)
                        }
                        .foregroundColor(AppColors.bullish)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.md)
                        .background(AppColors.bullish.opacity(0.15))
                        .cornerRadius(AppCornerRadius.medium)
                    }
                    .buttonStyle(PlainButtonStyle())
                } else {
                    Button(action: {
                        onReadKeyIdeas?()
                    }) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "lightbulb.fill")
                                .font(.system(size: 12))

                            Text("Read Key Ideas")
                                .font(AppTypography.calloutBold)
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
            LibraryBookCard(book: LibraryBook.sampleData[0])
            // Unread book
            LibraryBookCard(book: LibraryBook.sampleData[2])
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
