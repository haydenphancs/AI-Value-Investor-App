//
//  EducationSection.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct EducationSection: View {
    let educationItems: [EducationItem]

    var body: some View {
        VStack(spacing: 12) {
            SectionHeader(
                title: "Wiser: Learn and Grow",
                showSeeAll: true,
                onSeeAllTapped: {
                    // See all action
                }
            )

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 12) {
                    ForEach(educationItems) { education in
                        EducationCard(education: education)
                    }
                }
                .padding(.horizontal, 20)
            }
        }
    }
}

#Preview {
    EducationSection(educationItems: EducationItem.mockData)
        .background(AppColors.background)
}
