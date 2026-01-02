//
//  KeyConceptCard.swift
//  ios
//
//  Molecule: Card showing a key concept with bookmark functionality
//

import SwiftUI

struct KeyConceptCard: View {
    let concept: KeyConcept
    var onTap: (() -> Void)?
    var onBookmark: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                // Header with icon and bookmark
                HStack(alignment: .top) {
                    // Icon
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(concept.iconBackgroundColor)
                            .frame(width: 40, height: 40)

                        Image(systemName: concept.iconName)
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundColor(.white)
                    }

                    Spacer()

                    // Bookmark button
                    BookmarkButton(isBookmarked: concept.isBookmarked) {
                        onBookmark?()
                    }
                }

                // Title
                Text(concept.title)
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                // Subtitle
                Text(concept.subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                Spacer(minLength: 0)

                // Meta info
                HStack(spacing: AppSpacing.lg) {
                    ReadTimeLabel(minutes: concept.estimatedMinutes)
                    LearnerCountBadge(count: concept.learnerCount)
                }
            }
            .padding(AppSpacing.lg)
            .frame(width: 200, height: 200)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.extraLarge)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: AppSpacing.lg) {
            ForEach(KeyConcept.sampleData) { concept in
                KeyConceptCard(concept: concept)
            }
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
