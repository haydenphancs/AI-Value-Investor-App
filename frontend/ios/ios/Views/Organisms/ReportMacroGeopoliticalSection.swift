//
//  ReportMacroGeopoliticalSection.swift
//  ios
//
//  Organism: Macro-Economic & Geopolitical deep dive content.
//  Intelligence briefing design: DEFCON-style threat level bar,
//  2-column risk gauge grid, and classified-style AI intelligence brief.
//

import SwiftUI

struct ReportMacroGeopoliticalSection: View {
    let data: ReportMacroData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxl) {
            // Threat Level Bar
            threatLevelSection

            // Intelligence Brief
            intelligenceBriefSection

            // Timestamp
            HStack {
                Spacer()
                Text(data.lastUpdated)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    // MARK: - Threat Level

    private var threatLevelSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ReportThreatLevelBar(level: data.overallThreatLevel)

            // Headline
            Text(data.headline)
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)
                .lineSpacing(2)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .stroke(data.overallThreatLevel.color.opacity(0.2), lineWidth: 1)
                )
        )
    }

    // MARK: - Intelligence Brief

    private var intelligenceBriefSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Classified-style header
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "doc.text.magnifyingglass")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(AppColors.alertPurple)

                Text("INTELLIGENCE BRIEF")
                    .font(AppTypography.captionBold)
                    .foregroundColor(AppColors.alertPurple)
                    .tracking(1.2)

                Spacer()

                // Confidential badge
                Text("AI GENERATED")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundColor(AppColors.textMuted)
                    .tracking(0.8)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 3)
                    .background(
                        RoundedRectangle(cornerRadius: 3)
                            .stroke(AppColors.textMuted.opacity(0.3), lineWidth: 1)
                    )
            }

            // Decorative top line
            Rectangle()
                .fill(
                    LinearGradient(
                        colors: [AppColors.alertPurple.opacity(0.6), AppColors.alertPurple.opacity(0.0)],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .frame(height: 1)

            // Brief text
            Text(data.intelligenceBrief)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(4)

            // Decorative bottom line
            Rectangle()
                .fill(
                    LinearGradient(
                        colors: [AppColors.alertPurple.opacity(0.0), AppColors.alertPurple.opacity(0.6)],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .frame(height: 1)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.alertPurple.opacity(0.04))
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .stroke(AppColors.alertPurple.opacity(0.15), lineWidth: 1)
                )
        )
    }
}

#Preview {
    ScrollView {
        ReportMacroGeopoliticalSection(
            data: TickerReportData.sampleOracle.macroData
        )
        .padding()
    }
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
