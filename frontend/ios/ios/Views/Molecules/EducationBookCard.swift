//
//  EducationBookCard.swift
//  ios
//
//  Molecule: Card showing an educational book with chat and key ideas buttons
//

import SwiftUI

struct EducationBookCard: View {
    let book: EducationBook
    var onChatWithBook: (() -> Void)?
    var onReadKeyIdeas: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Most Read badge
            if book.isMostRead {
                Text("Most Read")
                    .font(AppTypography.captionBold)
                    .foregroundColor(AppColors.textSecondary)
                    .padding(.bottom, -AppSpacing.sm)
            }

            HStack(alignment: .top, spacing: AppSpacing.lg) {
                // Book cover placeholder
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

                // Book details
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
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
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.extraLarge)
    }

    private var bookCoverGradient: LinearGradient {
        // Different colors for different books
        let colors: [Color]
        switch book.title {
        case "The Intelligent Investor":
            colors = [Color(hex: "1E3A5F"), Color(hex: "0F1F35")]
        case "One Up On Wall Street":
            colors = [Color(hex: "2D4A3E"), Color(hex: "1A2D25")]
        case "Common Stocks and Uncommon Profits":
            colors = [Color(hex: "4A1E1E"), Color(hex: "2D1212")]
        default:
            colors = [Color(hex: "3B3B5C"), Color(hex: "1E1E2E")]
        }

        return LinearGradient(
            colors: colors,
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
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
    .preferredColorScheme(.dark)
}
