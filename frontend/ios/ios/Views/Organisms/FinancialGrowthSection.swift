//
//  FinancialGrowthSection.swift
//  ios
//
//  Organism: Growth section with metric tabs, period toggle, and chart
//

import SwiftUI

struct FinancialGrowthSection: View {
    let data: GrowthData
    @Binding var selectedMetric: GrowthMetricType
    @Binding var selectedPeriod: GrowthPeriodType
    var onDetailTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section Header
            FinancialSectionHeader(
                title: "Growth",
                infoTitle: "Growth",
                infoDescription: FinancialInfoContent.growth,
                showDetailLink: true,
                onDetailTap: onDetailTap
            )

            // Metric tabs (scrollable)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.sm) {
                    ForEach(GrowthMetricType.allCases, id: \.self) { metric in
                        GrowthMetricTab(
                            metric: metric,
                            isSelected: selectedMetric == metric,
                            onTap: { selectedMetric = metric }
                        )
                    }
                }
            }

            // Period toggle
            HStack {
                FinancialSegmentedControl(
                    selection: $selectedPeriod,
                    style: .toggle
                )
                Spacer()
            }

            // Chart
            GrowthChart(
                data: data.dataForMetric(selectedMetric),
                metricType: selectedMetric,
                showSectorAverage: true
            )
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

// MARK: - Growth Metric Tab
struct GrowthMetricTab: View {
    let metric: GrowthMetricType
    let isSelected: Bool
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            Text(metric.shortName)
                .font(AppTypography.subheadline)
                .fontWeight(isSelected ? .semibold : .regular)
                .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    Group {
                        if isSelected {
                            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                .fill(AppColors.primaryBlue)
                        } else {
                            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                .fill(AppColors.cardBackground)
                        }
                    }
                )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            FinancialGrowthSection(
                data: GrowthData.sampleData,
                selectedMetric: .constant(.revenue),
                selectedPeriod: .constant(.annual)
            )
            .padding(.vertical)
        }
    }
}
