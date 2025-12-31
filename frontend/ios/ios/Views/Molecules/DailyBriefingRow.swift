//
//  DailyBriefingRow.swift
//  ios
//
//  Molecule: Individual alert row in daily briefing
//

import SwiftUI

struct DailyBriefingRow: View {
    let item: DailyBriefingItem
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.md) {
                // Icon
                AlertIconView(type: item.type)

                // Content
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(item.title)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)

                    Text(item.subtitle)
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }

                Spacer()

                // Date badge or chevron
                if item.hasDateBadge, let date = item.date {
                    DateBadge(from: date)
                } else {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)
                }
            }
            .padding(AppSpacing.md)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.medium)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack(spacing: 12) {
        DailyBriefingRow(item: DailyBriefingItem(
            type: .whalesAlert,
            title: "Whales Alert",
            subtitle: "Large crypto whale just moved $50M into COIN stock",
            date: nil,
            badgeText: nil
        ))

        DailyBriefingRow(item: DailyBriefingItem(
            type: .earningsAlert,
            title: "Earnings Alert",
            subtitle: "NVDA reports earnings tomorrow after market close.",
            date: Date(),
            badgeText: "24\nFEB"
        ))

        DailyBriefingRow(item: DailyBriefingItem(
            type: .wiserTrending,
            title: "Wiser: Trending",
            subtitle: "How can I invest in OpenAI even though the company is not yet listed?",
            date: nil,
            badgeText: nil
        ))
    }
    .padding()
    .background(AppColors.background)
}
