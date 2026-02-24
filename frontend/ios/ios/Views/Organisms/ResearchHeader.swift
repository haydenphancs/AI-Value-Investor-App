//
//  ResearchHeader.swift
//  ios
//
//  Organism: Research screen header — uses the same layout as TrackingHeader
//  with "AI Research Analysis" title in place of the search bar.
//

import SwiftUI

struct ResearchHeader: View {
    @Environment(\.appState) private var appState
    @Binding var selectedTab: ResearchTab
    var onProfileTapped: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Standardized header row (same as TrackingHeader but with title instead of search)
            HStack(spacing: AppSpacing.md) {
                // Left: App Logo
                LogoView()

                // Center: AI Research Analysis title
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "sparkles")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(AppColors.primaryBlue)

                    Text("AI Research Analysis")
                        .font(AppTypography.headline)
                        .foregroundColor(AppColors.textPrimary)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    Capsule()
                        .fill(AppColors.cardBackground)
                )

                // Right: Profile Avatar
                Button(action: {
                    onProfileTapped?()
                }) {
                    ProfileAvatarView(
                        avatarUrl: appState.user.profile?.avatarUrl,
                        size: 36
                    )
                }
                .buttonStyle(PlainButtonStyle())
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.sm)

            // Segmented Tab Control (same as TrackingHeader)
            SegmentedTabControl(
                tabs: ResearchTab.allCases,
                selectedTab: $selectedTab
            )
            .padding(.horizontal, AppSpacing.lg)
        }
        .padding(.bottom, AppSpacing.sm)
    }
}

#Preview {
    VStack {
        ResearchHeader(selectedTab: .constant(.research))
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
