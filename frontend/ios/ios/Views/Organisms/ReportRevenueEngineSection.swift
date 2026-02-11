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
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)

            HStack(alignment: .firstTextBaseline, spacing: AppSpacing.xs) {
                Text(data.formattedTotalRevenue)
                    .font(.system(size: 24, weight: .bold, design: .rounded))
                    .foregroundColor(AppColors.textPrimary)

                Text(data.period)
                    .font(AppTypography.subheadline)
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
                        .font(.system(size: 11, weight: .bold))
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
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Segment name
            Text(segment.name)
                .font(AppTypography.bodyBold)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(2)

            // Bottom row: Percentage + Growth
            HStack(spacing: AppSpacing.lg) {
                // Percentage of total
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "chart.pie.fill")
                        .font(.system(size: 11))
                        .foregroundColor(AppColors.textSecondary)

                    Text(segment.formattedPercentage)
                        .font(AppTypography.subheadline)
                        .foregroundColor(AppColors.textSecondary)

                    Text("of total")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                // Growth indicator
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: segment.growth >= 0 ? "arrow.up.right" : "arrow.down.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(segment.growthColor)

                    Text(segment.formattedGrowth)
                        .font(AppTypography.subheadline)
                        .foregroundColor(segment.growthColor)
                }
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .strokeBorder(AppColors.textMuted.opacity(0.15), lineWidth: 1)
        )
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .strokeBorder(role.borderColor.opacity(0.2), lineWidth: 1)
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
                    .font(.system(size: 16, weight: .semibold))

                Text("Insight")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(LinearGradient(
                        colors: [.indigo, .cyan],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ))
            }

            Text(note)
                .font(AppTypography.subheadline)
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
