//
//  PortfolioSection.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct PortfolioSection: View {
    let stocks: [Stock]

    var body: some View {
        VStack(spacing: 12) {
            SectionHeader(
                title: "Holding: Your Portfolio",
                showSeeAll: true,
                onSeeAllTapped: {
                    // See all action
                }
            )

            VStack(spacing: 10) {
                ForEach(stocks) { stock in
                    StockListItem(stock: stock)
                }
            }
            .padding(.horizontal, 20)
        }
    }
}

#Preview {
    PortfolioSection(stocks: Stock.mockPortfolio)
        .background(AppColors.background)
}
