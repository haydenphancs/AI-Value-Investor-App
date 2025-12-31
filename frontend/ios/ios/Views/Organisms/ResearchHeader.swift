//
//  ResearchHeader.swift
//  ios
//
//  Organism: Research screen header with logo, title, and tab selector
//

import SwiftUI

struct ResearchHeader: View {
    @Binding var selectedTab: ResearchTab
    var onProfileTapped: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Top bar
            HStack(spacing: AppSpacing.md) {
                // Logo placeholder
                Circle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 32, height: 32)
                    .overlay(
                        Text("L")
                            .font(AppTypography.footnoteBold)
                            .foregroundColor(AppColors.textSecondary)
                    )

                // Title with icon
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

                Spacer()

                // Profile button
                Button(action: {
                    onProfileTapped?()
                }) {
                    Circle()
                        .fill(AppColors.cardBackgroundLight)
                        .frame(width: 36, height: 36)
                        .overlay(
                            Image(systemName: "person.fill")
                                .font(.system(size: 14))
                                .foregroundColor(AppColors.textSecondary)
                        )
                }
                .buttonStyle(PlainButtonStyle())
            }

            // Tab selector
            HStack(spacing: 0) {
                ForEach(ResearchTab.allCases, id: \.rawValue) { tab in
                    TabPill(
                        title: tab.rawValue,
                        isSelected: selectedTab == tab
                    ) {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            selectedTab = tab
                        }
                    }
                }
            }
            .padding(AppSpacing.xs)
            .background(
                Capsule()
                    .fill(AppColors.cardBackground)
            )
            .frame(maxWidth: .infinity, alignment: .leading)
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
