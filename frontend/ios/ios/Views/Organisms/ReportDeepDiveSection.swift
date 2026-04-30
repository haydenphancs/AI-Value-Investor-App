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
    // Deferred content build: keeps the tap responsive when the inner
    // section contains heavy charts (Canvas, SwiftUI Charts, GeometryReader).
    // Without this, opening a 2nd/3rd section synchronously builds a chart
    // tree during the same layout pass that resizes the parent — long enough
    // to look like a freeze.
    @State private var contentReady: Bool = false

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

            if isExpanded && contentReady {
                content()
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.lg)
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.15))
        }
        .background(AppColors.cardBackground)
        .task(id: isExpanded) {
            if isExpanded {
                await Task.yield()
                contentReady = true
            } else {
                contentReady = false
            }
        }
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
