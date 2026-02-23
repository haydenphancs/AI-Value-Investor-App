//
//  ResearchHeader.swift
//  ios
//
//  Organism: Research screen header with standardized logo, title, profile, and tab selector
//

import SwiftUI

struct ResearchHeader: View {
    @Environment(\.appState) private var appState
    @Binding var selectedTab: ResearchTab
    var onProfileTapped: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Top bar with centered title using ZStack
            ZStack {
                // Centered title (ignores side elements)
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "sparkles")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(AppColors.primaryBlue)

                    Text("AI Research Analysis")
                        .font(AppTypography.headline)
                        .foregroundColor(AppColors.textPrimary)
                }
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    Capsule()
                        .fill(AppColors.cardBackground)
                )

                // Side elements
                HStack {
                    // App Logo (standardized)
                    LogoView()

                    Spacer()

                    // Profile Avatar (standardized, with avatar URL from AppState)
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
            }

            // Tab selector - stretched to full width
            HStack(spacing: 0) {
                ForEach(ResearchTab.allCases, id: \.rawValue) { tab in
                    Button(action: {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            selectedTab = tab
                        }
                    }) {
                        Text(tab.rawValue)
                            .font(AppTypography.callout)
                            .fontWeight(selectedTab == tab ? .semibold : .regular)
                            .foregroundColor(selectedTab == tab ? AppColors.textPrimary : AppColors.textSecondary)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, AppSpacing.sm)
                            .background(
                                Group {
                                    if selectedTab == tab {
                                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                            .fill(AppColors.cardBackgroundLight)
                                    }
                                }
                            )
                    }
                    .buttonStyle(PlainButtonStyle())
                }
            }
            .padding(AppSpacing.xs)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
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
