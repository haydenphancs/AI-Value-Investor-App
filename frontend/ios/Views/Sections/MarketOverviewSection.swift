//
//  MarketOverviewSection.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct MarketOverviewSection: View {
    let marketIndices: [MarketIndex]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                ForEach(marketIndices) { index in
                    MarketOverviewCard(marketIndex: index)
                        .frame(width: 130)
                }
            }
            .padding(.horizontal, 20)
        }
    }
}

#Preview {
    MarketOverviewSection(marketIndices: MarketIndex.mockData)
        .background(AppColors.background)
}
