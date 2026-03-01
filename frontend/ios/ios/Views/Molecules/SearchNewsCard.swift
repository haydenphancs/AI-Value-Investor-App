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
                    // News thumbnail (supports remote URLs and local assets)
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.cardBackgroundLight)
                            .frame(width: 80, height: 80)

                        if let url = item.imageURL {
                            AsyncImage(url: url) { phase in
                                switch phase {
                                case .success(let image):
                                    image
                                        .resizable()
                                        .aspectRatio(contentMode: .fill)
                                case .failure:
                                    Image(systemName: "newspaper.fill")
                                        .font(.title2)
                                        .foregroundColor(AppColors.textMuted)
                                default:
                                    ProgressView()
                                        .tint(AppColors.textMuted)
                                }
                            }
                            .frame(width: 80, height: 80)
                            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.medium))
                        } else if !item.imageName.isEmpty {
                            Image(item.imageName)
                                .resizable()
                                .aspectRatio(contentMode: .fill)
                                .frame(width: 80, height: 80)
                                .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.medium))
                        } else {
                            Image(systemName: "newspaper.fill")
                                .font(.title2)
                                .foregroundColor(AppColors.textMuted)
                                .frame(width: 80, height: 80)
                        }
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
                            .font(AppTypography.bodyEmphasis)
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
                        .font(AppTypography.bodySmall)
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
