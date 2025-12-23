//
//  HomeView.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct HomeView: View {
    // MARK: - State
    @State private var marketIndices = MarketIndex.mockData
    @State private var marketInsight = MarketInsight.mockData
    @State private var portfolioStocks = Stock.mockPortfolio
    @State private var researchItems = ResearchItem.mockData
    @State private var educationItems = EducationItem.mockData

    var body: some View {
        ZStack(alignment: .bottom) {
            // Main Content
            ScrollView(.vertical, showsIndicators: false) {
                VStack(spacing: 24) {
                    // Search Bar
                    SearchBar()

                    // Market Overview
                    MarketOverviewSection(marketIndices: marketIndices)

                    // Market Insights
                    MarketInsightsSection(insight: marketInsight)

                    // Portfolio
                    PortfolioSection(stocks: portfolioStocks)

                    // Research
                    ResearchSection(researchItems: researchItems)

                    // Education
                    EducationSection(educationItems: educationItems)

                    // Bottom padding for tab bar
                    Color.clear.frame(height: 80)
                }
                .padding(.top, 8)
            }
            .background(AppColors.background)

            // Tab Bar
            CustomTabBar(selectedTab: .constant(.home))
        }
        .ignoresSafeArea(.all, edges: .bottom)
    }
}

#Preview {
    HomeView()
}
