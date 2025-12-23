//
//  ResearchSection.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct ResearchSection: View {
    let researchItems: [ResearchItem]

    var body: some View {
        VStack(spacing: 12) {
            SectionHeader(
                title: "Research: AI Analysis",
                showSeeAll: true,
                onSeeAllTapped: {
                    // See all action
                }
            )

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 12) {
                    ForEach(researchItems) { research in
                        ResearchCard(research: research)
                    }
                }
                .padding(.horizontal, 20)
            }
        }
    }
}

#Preview {
    ResearchSection(researchItems: ResearchItem.mockData)
        .background(AppColors.background)
}
