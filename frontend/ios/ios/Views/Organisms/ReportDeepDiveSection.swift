//
//  ReportDeepDiveSection.swift
//  ios
//
//  Organism: Collapsible deep dive module container
//

import SwiftUI

struct ReportDeepDiveSection<Content: View>: View {
    let module: DeepDiveModule
    @ViewBuilder let content: () -> Content

    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(spacing: 0) {
            // Header row (tappable)
            Button(action: {
                isExpanded.toggle()
            }) {
                HStack(spacing: AppSpacing.md) {
                    Image(systemName: module.iconName)
                        .font(.system(size: module.iconName == "dollarsign.circle" ? 22 : 16))
                        .foregroundColor(AppColors.primaryBlue)
                        .frame(width: module.iconName == "dollarsign.circle" ? 36 : 28, alignment: module.iconName == "dollarsign.circle" ? .leading : .center)

                    Text(module.title)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .offset(x: module.iconName == "dollarsign.circle" ? -8 : 0)

                    Spacer()

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(AppTypography.iconXS).fontWeight(.semibold)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.lg)
            }
            .buttonStyle(PlainButtonStyle())

            if isExpanded {
                content()
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.lg)
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.15))
        }
        .background(AppColors.cardBackground)
    }
}

#Preview {
    VStack(spacing: 0) {
        ReportDeepDiveSection(
            module: DeepDiveModule(
                title: "Fundamentals & Growth",
                iconName: "chart.bar.fill",
                type: .fundamentalsGrowth
            )
        ) {
            Text("Content goes here")
                .foregroundColor(AppColors.textSecondary)
        }
        ReportDeepDiveSection(
            module: DeepDiveModule(
                title: "Recent Price Movement",
                iconName: "chart.xyaxis.line",
                type: .recentPriceMovement
            )
        ) {
            EmptyView()
        }
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
