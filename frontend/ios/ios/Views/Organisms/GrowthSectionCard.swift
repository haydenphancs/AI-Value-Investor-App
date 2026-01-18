//
//  GrowthSectionCard.swift
//  ios
//
//  Organism: Complete Growth Section card for the Financial tab
//  Displays growth metrics with selectable metric types and time periods
//

import SwiftUI

struct GrowthSectionCard: View {
    // MARK: - Properties

    let growthData: GrowthSectionData
    let onDetailTapped: () -> Void

    // MARK: - State

    @State private var selectedMetric: GrowthMetricType = .eps
    @State private var selectedPeriod: GrowthPeriodType = .annual
    @State private var showInfoSheet: Bool = false

    // MARK: - Computed Properties

    private var currentDataPoints: [GrowthDataPoint] {
        growthData.dataPoints(for: selectedMetric, period: selectedPeriod)
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title, info icon, and detail link
            headerSection

            // Metric type selector (EPS, Revenue, etc.)
            GrowthMetricSelector(selectedMetric: $selectedMetric)

            // Period toggle (Annual / Quarterly)
            GrowthPeriodToggle(selectedPeriod: $selectedPeriod)
                .padding(.leading, AppSpacing.xs)

            // Main chart
            GrowthChartView(dataPoints: currentDataPoints)
                .id("\(selectedMetric.rawValue)-\(selectedPeriod.rawValue)")
                .padding(.top, AppSpacing.sm)
                .animation(.easeInOut(duration: 0.3), value: selectedMetric)
                .animation(.easeInOut(duration: 0.3), value: selectedPeriod)

            // Legend
            GrowthLegendView()
                .frame(maxWidth: .infinity)
                .padding(.top, AppSpacing.md)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .sheet(isPresented: $showInfoSheet) {
            GrowthInfoSheet()
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("Growth")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                GrowthInfoIcon {
                    showInfoSheet = true
                }
            }

            Spacer()

            Button(action: onDetailTapped) {
                Text("Detail")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .buttonStyle(.plain)
        }
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            GrowthSectionCard(
                growthData: GrowthSectionData.sampleData,
                onDetailTapped: {}
            )
            .padding()
        }
    }
}
