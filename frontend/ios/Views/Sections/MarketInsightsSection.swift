//
//  MarketInsightsSection.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct MarketInsightsSection: View {
    let insight: MarketInsight

    var body: some View {
        VStack(spacing: 12) {
            SectionHeader(
                title: "Market Insights - AI Summary",
                iconName: "bolt.fill",
                showSeeAll: true,
                onSeeAllTapped: {
                    // See all action
                }
            )

            MarketInsightCard(insight: insight)
                .padding(.horizontal, 20)
        }
    }
}

#Preview {
    MarketInsightsSection(insight: MarketInsight.mockData)
        .background(AppColors.background)
}
