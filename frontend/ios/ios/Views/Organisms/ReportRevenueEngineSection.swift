//
//  ReportRevenueEngineSection.swift
//  ios
//
//  Organism: Revenue Engine deep dive content.
//  Shows revenue segment breakdown with automatic role assignment,
//  growth metrics, and visual indicators for each segment.
//

import SwiftUI

struct ReportRevenueEngineSection: View {
    let data: ReportRevenueEngineData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header: Total Revenue & Period
            headerSection

            // Segments List
            VStack(spacing: AppSpacing.md) {
                ForEach(data.segments) { segment in
                    segmentCard(segment)
                }
            }

            // Analysis Note (if available)
            if let note = data.analysisNote {
                analysisNoteSection(note)
            }
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text("Total Revenue")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)

            HStack(alignment: .firstTextBaseline, spacing: AppSpacing.xs) {
                Text(data.formattedTotalRevenue)
                    .font(AppTypography.dataLarge)
                    .foregroundColor(AppColors.textPrimary)

                Text(data.period)
                    .font(AppTypography.label)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    // MARK: - Segment Card

    private func segmentCard(_ segment: RevenueSegment) -> some View {
        let role = data.roleForSegment(segment)

        return VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Top row: Role badge + Revenue
            HStack(alignment: .center) {
                // Role badge
                HStack(spacing: AppSpacing.xs) {
                    Text(role.rawValue.uppercased())
                        .font(AppTypography.captionTiny)
                        .foregroundColor(role.color)
                }
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xs)
                .background(
                    Capsule()
                        .fill(role.backgroundColor)
                )

                Spacer()

                // Revenue amount
                Text(segment.formattedRevenue)
                    .font(AppTypography.labelSmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Segment name
            Text(segment.name)
                .font(AppTypography.labelSmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(2)

            // Bottom row: Percentage + Growth
            HStack(spacing: AppSpacing.lg) {
                // Percentage of total
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "chart.pie.fill")
                        .font(AppTypography.captionTiny)
                        .foregroundColor(AppColors.textSecondary)

                    Text(segment.formattedPercentage)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)

                    Text("of total")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                // Growth indicator
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: segment.growth >= 0 ? "arrow.up.right" : "arrow.down.right")
                        .font(.system(size: 7, weight: .semibold))
                        .foregroundColor(segment.growthColor)

                    Text(segment.formattedGrowth)
                        .font(AppTypography.caption)
                        .foregroundColor(segment.growthColor)
                }
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }

    // MARK: - Analysis Note Section

    private func analysisNoteSection(_ note: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "sparkles.2")
                    .foregroundStyle(
                        LinearGradient(
                            colors: [.indigo],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .font(AppTypography.iconDefault).fontWeight(.semibold)

                Text("Insight")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundStyle(LinearGradient(
                        colors: [.indigo, .cyan],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ))
            }

            Text(note)
                .font(AppTypography.label)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Preview

#Preview {
    ScrollView {
        ReportRevenueEngineSection(
            data: ReportRevenueEngineData.sampleOracle
        )
        .padding()
    }
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
