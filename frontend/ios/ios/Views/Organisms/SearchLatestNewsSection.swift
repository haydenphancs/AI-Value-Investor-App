//
//  SearchLatestNewsSection.swift
//  ios
//
//  Organism: Section displaying latest news in search
//

import SwiftUI

struct SearchLatestNewsSection: View {
    let items: [SearchNewsItem]
    var onItemTapped: ((SearchNewsItem) -> Void)?
    var onReadMore: ((SearchNewsItem) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            Text("Latest News")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // News cards
            VStack(spacing: AppSpacing.md) {
                ForEach(items) { item in
                    SearchNewsCard(
                        item: item,
                        onTap: { onItemTapped?(item) },
                        onReadMore: { onReadMore?(item) }
                    )
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        SearchLatestNewsSection(items: SearchNewsItem.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
