//
//  CommunityDiscussionsSection.swift
//  ios
//
//  Organism: Section showing community discussions
//

import SwiftUI

struct CommunityDiscussionsSection: View {
    let discussions: [CommunityDiscussion]
    var onSeeAll: (() -> Void)?
    var onDiscussionTap: ((CommunityDiscussion) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Community Discussions")
                        .font(AppTypography.title3)
                        .foregroundColor(AppColors.textPrimary)

                    Text("Join the conversation")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                Button(action: {
                    onSeeAll?()
                }) {
                    Text("See All")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            // Discussion rows
            VStack(spacing: AppSpacing.md) {
                ForEach(discussions) { discussion in
                    CommunityDiscussionRow(discussion: discussion) {
                        onDiscussionTap?(discussion)
                    }
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        CommunityDiscussionsSection(discussions: CommunityDiscussion.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
