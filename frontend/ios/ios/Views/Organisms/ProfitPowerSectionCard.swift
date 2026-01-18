//
//  ProfitPowerSectionCard.swift
//  ios
//
//  Organism: Complete Profit Power Section card for the Financial tab
//  Displays multiple profit margin metrics over time with sector comparison
//

import SwiftUI

struct ProfitPowerSectionCard: View {
    // MARK: - Properties

    let profitPowerData: ProfitPowerSectionData
    let onDetailTapped: () -> Void

    // MARK: - State

    @State private var selectedPeriod: ProfitPowerPeriodType = .annual
    @State private var showInfoSheet: Bool = false
    @State private var selectedDataPoint: ProfitPowerDataPoint? = nil

    // MARK: - Computed Properties

    private var currentDataPoints: [ProfitPowerDataPoint] {
        profitPowerData.dataPoints(for: selectedPeriod)
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title, info icon, and detail link
            headerSection

            // Period toggle (Annual / Quarterly)
            ProfitPowerPeriodToggle(selectedPeriod: $selectedPeriod)
                .padding(.leading, AppSpacing.xs)

            // Main chart
            ProfitPowerChartView(
                dataPoints: currentDataPoints,
                selectedDataPoint: $selectedDataPoint
            )
            .padding(.top, AppSpacing.sm)

            // Legend
            ProfitPowerLegendView()
                .frame(maxWidth: .infinity)
                .padding(.top, AppSpacing.md)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .sheet(isPresented: $showInfoSheet) {
            ProfitPowerInfoSheet()
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("Profit Power")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                ProfitPowerInfoIcon {
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
            ProfitPowerSectionCard(
                profitPowerData: ProfitPowerSectionData.sampleData,
                onDetailTapped: {}
            )
            .padding()
        }
    }
}
