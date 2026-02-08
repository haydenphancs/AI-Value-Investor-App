//
//  ReportDeepDiveSection.swift
//  ios
//
//  Organism: Collapsible deep dive module container
//

import SwiftUI

struct ReportDeepDiveSection: View {
    let module: DeepDiveModule
    let isExpanded: Bool
    let onToggle: () -> Void
    let content: AnyView

    var body: some View {
        VStack(spacing: 0) {
            // Header row (tappable)
            Button(action: onToggle) {
                HStack(spacing: AppSpacing.md) {
                    Image(systemName: module.iconName)
                        .font(.system(size: 14))
                        .foregroundColor(AppColors.primaryBlue)
                        .frame(width: 24)

                    Text(module.title)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.lg)
            }
            .buttonStyle(PlainButtonStyle())

            // Expanded content
            if isExpanded {
                content
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.lg)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.15))
        }
        .background(AppColors.cardBackground)
        .animation(.easeInOut(duration: 0.25), value: isExpanded)
    }
}

#Preview {
    VStack(spacing: 0) {
        ReportDeepDiveSection(
            module: DeepDiveModule(
                title: "Fundamentals & Growth",
                iconName: "chart.bar.fill",
                type: .fundamentalsGrowth
            ),
            isExpanded: true,
            onToggle: {},
            content: AnyView(
                Text("Content goes here")
                    .foregroundColor(AppColors.textSecondary)
            )
        )
        ReportDeepDiveSection(
            module: DeepDiveModule(
                title: "Recent price movement",
                iconName: "chart.xyaxis.line",
                type: .recentPriceMovement
            ),
            isExpanded: false,
            onToggle: {},
            content: AnyView(EmptyView())
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
