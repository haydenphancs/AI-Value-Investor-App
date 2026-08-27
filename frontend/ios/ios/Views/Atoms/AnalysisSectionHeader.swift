//
//  AnalysisSectionHeader.swift
//  ios
//
//  Header for analysis sections with title and optional action button
//

import SwiftUI

enum SectionHeaderIcon {
    case ellipsis
    case info
}

struct AnalysisSectionHeader: View {
    let title: String
    let subtitle: String?
    let actionText: String?
    let onAction: (() -> Void)?
    let showMoreButton: Bool
    let iconType: SectionHeaderIcon

    init(
        title: String,
        subtitle: String? = nil,
        actionText: String? = nil,
        onAction: (() -> Void)? = nil,
        showMoreButton: Bool = true,
        iconType: SectionHeaderIcon = .ellipsis
    ) {
        self.title = title
        self.subtitle = subtitle
        self.actionText = actionText
        self.onAction = onAction
        self.showMoreButton = showMoreButton
        self.iconType = iconType
    }

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.headingSmall)
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
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.primaryBlue)
                }
            } else if showMoreButton {
                Button {
                    onAction?()
                } label: {
                    Image(systemName: iconType == .info ? "info.circle" : "ellipsis")
                        .font(AppTypography.iconDefault).fontWeight(.medium)
                        .foregroundColor(AppColors.textSecondary)
                        // 24pt was barely half the minimum target. Widen the BOX to
                        // 44 and leave its height alone: this row is
                        // `HStack(alignment: .top)` against a ~20pt title, so a 44pt
                        // SQUARE would both drop the glyph ~10pt and pad every section
                        // header out to 44pt tall. `.trailing` keeps the glyph exactly
                        // where it renders today and grows the box leftward into the
                        // Spacer, where there is nothing to collide with.
                        //
                        // The vertical axis stays bounded by the row — the same parent
                        // -clipping limit documented in HitSlop.swift — so this is a
                        // 44x24 target plus slop, not a 44x44 one.
                        .frame(width: HitSlop.minimumTarget, height: 24, alignment: .trailing)
                        .hitSlop()
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
