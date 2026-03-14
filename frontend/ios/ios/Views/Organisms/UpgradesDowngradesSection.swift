//
//  UpgradesDowngradesSection.swift
//  ios
//
//  Full view displaying list of analyst upgrades and downgrades
//

import SwiftUI

struct UpgradesDowngradesSection: View {
    let actions: [AnalystAction]
    var onInfoTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            Text("Upgrades & Downgrades")
                .font(AppTypography.titleCompact)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Actions list
            LazyVStack(spacing: AppSpacing.sm) {
                ForEach(actions) { action in
                    AnalystActionCard(action: action)
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

// MARK: - Full Screen View for Modal Presentation
struct UpgradesDowngradesView: View {
    let actions: [AnalystAction]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Navigation Header
                HStack {
                    Button(action: {
                        dismiss()
                    }) {
                        Image(systemName: "chevron.down")
                            .font(AppTypography.iconMedium).fontWeight(.medium)
                            .foregroundColor(AppColors.textPrimary)
                    }

                    Spacer()

                    Text("Upgrades & Downgrades")
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    // Spacer to balance the chevron on the left
                    Color.clear
                        .frame(width: 24, height: 24)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)
                .background(AppColors.background)

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Scrollable content
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.sm) {
                        ForEach(actions) { action in
                            AnalystActionCard(action: action)
                        }
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.lg)
                }
            }
        }
        .navigationBarHidden(true)
    }
}

#Preview("Section") {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            UpgradesDowngradesSection(actions: AnalystAction.sampleData)
                .padding(.top, AppSpacing.lg)
        }
    }
}

#Preview("Full View") {
    UpgradesDowngradesView(actions: AnalystAction.sampleData)
}
