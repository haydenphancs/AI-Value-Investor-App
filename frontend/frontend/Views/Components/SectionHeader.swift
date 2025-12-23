//
//  SectionHeader.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct SectionHeader: View {
    let title: String
    let iconName: String?
    let showSeeAll: Bool
    let onSeeAllTapped: (() -> Void)?

    init(
        title: String,
        iconName: String? = nil,
        showSeeAll: Bool = true,
        onSeeAllTapped: (() -> Void)? = nil
    ) {
        self.title = title
        self.iconName = iconName
        self.showSeeAll = showSeeAll
        self.onSeeAllTapped = onSeeAllTapped
    }

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            if let iconName = iconName {
                Image(systemName: iconName)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(AppColors.lightBlue)
            }

            Text(title)
                .sectionHeaderStyle()

            Spacer()

            if showSeeAll {
                Button(action: {
                    onSeeAllTapped?()
                }) {
                    Text("See All")
                        .seeAllButtonStyle()
                }
            }
        }
        .padding(.horizontal, 20)
    }
}

#Preview {
    VStack(spacing: 20) {
        SectionHeader(title: "Market Insights - AI Summary", iconName: "bolt.fill")
        SectionHeader(title: "Holding: Your Portfolio", showSeeAll: true)
        SectionHeader(title: "Research: AI Analysis", showSeeAll: false)
    }
    .background(AppColors.background)
}
