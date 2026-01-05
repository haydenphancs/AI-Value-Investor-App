//
//  SearchNewsCard.swift
//  ios
//
//  Molecule: Card displaying a news item in search results
//

import SwiftUI

struct SearchNewsCard: View {
    let item: SearchNewsItem
    var onTap: (() -> Void)?
    var onReadMore: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                HStack(alignment: .top, spacing: AppSpacing.md) {
                    // News thumbnail
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.cardBackgroundLight)
                            .frame(width: 80, height: 80)

                        Image(item.imageName)
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                            .frame(width: 80, height: 80)
                            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.medium))
                    }

                    VStack(alignment: .leading, spacing: AppSpacing.xs) {
                        // Source and time
                        HStack(spacing: AppSpacing.sm) {
                            Text(item.source)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)

                            Text(item.timeAgo)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                        }

                        // Headline
                        Text(item.headline)
                            .font(AppTypography.bodyBold)
                            .foregroundColor(AppColors.textPrimary)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)

                        // Summary
                        Text(item.summary)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                            .lineLimit(3)
                            .multilineTextAlignment(.leading)
                    }
                }

                // Read More link
                Button(action: {
                    onReadMore?()
                }) {
                    Text(item.readMoreAction)
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.bearish)
                }
                .buttonStyle(PlainButtonStyle())
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.md) {
            ForEach(SearchNewsItem.sampleData) { item in
                SearchNewsCard(item: item)
            }
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
