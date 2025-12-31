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
            // Top bar with centered title using ZStack
            ZStack {
                // Centered title (ignores side elements)
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "sparkles")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(AppColors.neutral)

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
                    // Logo placeholder
                    Circle()
                        .fill(AppColors.cardBackgroundLight)
                        .frame(width: 32, height: 32)
                        .overlay(
                            Text("L")
                                .font(AppTypography.footnoteBold)
                                .foregroundColor(AppColors.textSecondary)
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
