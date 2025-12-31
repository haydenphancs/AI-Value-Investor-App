//
//  CommunityInsightsSection.swift
//  ios
//
//  Organism: Community insights section with discussion link
//

import SwiftUI

struct CommunityInsightsSection: View {
    let insights: [CommunityInsight]
    var onJoinDiscussion: (() -> Void)?
    var onLike: ((CommunityInsight) -> Void)?
    var onComment: ((CommunityInsight) -> Void)?
    var onShare: ((CommunityInsight) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header
            HStack {
                Text("Community Insights")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button(action: {
                    onJoinDiscussion?()
                }) {
                    Text("Join Discussion")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(PlainButtonStyle())
            }

            // Insights list
            VStack(spacing: AppSpacing.md) {
                ForEach(insights) { insight in
                    CommunityInsightRow(
                        insight: insight,
                        onLike: { onLike?(insight) },
                        onComment: { onComment?(insight) },
                        onShare: { onShare?(insight) }
                    )
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    ScrollView {
        CommunityInsightsSection(insights: CommunityInsight.mockInsights)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
