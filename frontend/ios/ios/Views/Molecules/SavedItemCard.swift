//
//  SavedItemCard.swift
//  ios
//
//  Molecule: Card displaying a saved item (book, concept, chat, report)
//

import SwiftUI

struct SavedItemCard: View {
    let item: SavedItem
    var onActionTap: (() -> Void)?
    var onMoreOptions: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Top row: Type badge, time ago, icon, more button
            HStack(alignment: .center) {
                SavedItemTypeBadge(type: item.type)

                TimeAgoLabel(text: item.timeAgo)

                Spacer()

                SavedItemIcon(type: item.type)

                MoreOptionsButton {
                    onMoreOptions?()
                }
            }

            // Title
            Text(item.title)
                .font(AppTypography.bodyBold)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)
                .multilineTextAlignment(.leading)

            // Description
            Text(item.description)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .lineLimit(2)
                .multilineTextAlignment(.leading)

            // Bottom row: Progress/Messages/Level indicator + Action button
            HStack(alignment: .center) {
                // Left side: Context info
                HStack(spacing: AppSpacing.sm) {
                    if let progress = item.progress?.formattedChapter {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "bookmark.fill")
                                .font(.system(size: 10))
                                .foregroundColor(AppColors.primaryBlue)
                            Text(progress)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }

                    if let level = item.level {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "chart.bar.fill")
                                .font(.system(size: 10))
                                .foregroundColor(AppColors.textMuted)
                            Text(level)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }

                    if let messages = item.formattedMessages {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "bubble.left.fill")
                                .font(.system(size: 10))
                                .foregroundColor(AppColors.textMuted)
                            Text(messages)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }
                }

                Spacer()

                // Action button
                Button(action: {
                    onActionTap?()
                }) {
                    Text(item.actionButtonTitle)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(PlainButtonStyle())
            }
            .padding(.top, AppSpacing.xs)
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ForEach(SavedItem.sampleData) { item in
            SavedItemCard(item: item)
        }
    }
    .padding(.horizontal, AppSpacing.lg)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
