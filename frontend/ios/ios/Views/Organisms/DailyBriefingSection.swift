//
//  DailyBriefingSection.swift
//  ios
//
//  Organism: Daily briefing section with list of alerts
//

import SwiftUI

struct DailyBriefingSection: View {
    let items: [DailyBriefingItem]
    var onItemTapped: ((DailyBriefingItem) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section Header
            SectionHeader(title: "Daily Briefing")
                .padding(.horizontal, AppSpacing.lg)

            // Alert Items
            VStack(spacing: AppSpacing.sm) {
                ForEach(items) { item in
                    DailyBriefingRow(item: item) {
                        onItemTapped?(item)
                    }
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        DailyBriefingSection(items: [
            DailyBriefingItem(
                type: .whalesAlert,
                title: "Whales Alert",
                subtitle: "Large crypto whale just moved $50M into COIN stock",
                date: nil,
                badgeText: nil
            ),
            DailyBriefingItem(
                type: .earningsAlert,
                title: "Earnings Alert",
                subtitle: "NVDA reports earnings tomorrow after market close.",
                date: Date(),
                badgeText: "24\nFEB"
            ),
            DailyBriefingItem(
                type: .whalesFollowing,
                title: "Whales Your Following",
                subtitle: "3 hedge funds you follow bought GOOGL this week. Avg. position size: $1.2B",
                date: nil,
                badgeText: nil
            ),
            DailyBriefingItem(
                type: .wiserTrending,
                title: "Wiser: Trending",
                subtitle: "How can I invest in OpenAI even though the company is not yet listed?",
                date: nil,
                badgeText: nil
            )
        ])
    }
    .background(AppColors.background)
}
