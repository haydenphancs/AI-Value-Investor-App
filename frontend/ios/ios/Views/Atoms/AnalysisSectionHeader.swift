//
//  AnalysisSectionHeader.swift
//  ios
//
//  Header for analysis sections with title and optional action button
//

import SwiftUI

struct AnalysisSectionHeader: View {
    let title: String
    let subtitle: String?
    let actionText: String?
    let onAction: (() -> Void)?
    let showMoreButton: Bool

    init(
        title: String,
        subtitle: String? = nil,
        actionText: String? = nil,
        onAction: (() -> Void)? = nil,
        showMoreButton: Bool = true
    ) {
        self.title = title
        self.subtitle = subtitle
        self.actionText = actionText
        self.onAction = onAction
        self.showMoreButton = showMoreButton
    }

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                if let subtitle = subtitle {
                    Text(subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            Spacer()

            if let actionText = actionText, let onAction = onAction {
                Button(action: onAction) {
                    Text(actionText)
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.primaryBlue)
                }
            } else if showMoreButton {
                Button {
                    onAction?()
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                        .frame(width: 24, height: 24)
                }
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            AnalysisSectionHeader(
                title: "Analyst Ratings",
                subtitle: "Total Analysts 40\nUpdated On 01/05/2026 ET"
            )

            AnalysisSectionHeader(
                title: "Sentiment Analysis"
            )

            AnalysisSectionHeader(
                title: "Technical Analysis",
                actionText: "Detail",
                onAction: {}
            )
        }
        .padding()
    }
}
