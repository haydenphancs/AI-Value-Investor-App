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
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button(action: {
                    onJoinDiscussion?()
                }) {
                    Text("Join Discussion")
                        .font(AppTypography.bodySmall)
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
    // A neutral layout sample, built here so nothing like it ships in a release build:
    // a placeholder user, no investor names, no endorsement of the product.
    ScrollView {
        CommunityInsightsSection(insights: [
            CommunityInsight(
                userName: "Preview User",
                userAvatarName: "",
                postedAt: Date().addingTimeInterval(-7200),
                comment: "Sample comment text, long enough to wrap onto a second line so the row's layout can be checked.",
                likesCount: 3,
                commentsCount: 1
            )
        ])
    }
    .background(AppColors.background)
}
