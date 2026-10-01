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
    /// The build lost a company data leg upstream (`TickerDetailViewModel.profitPowerIsDegraded`):
    /// a tab it emptied reads "temporarily unavailable", not "isn't available for this
    /// company". Defaults to false, so a caller that does not pass it keeps today's wording.
    var isDegraded: Bool = false

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

            // Period toggle (Annual / Quarterly). Switching clears the tooltip: the
            // selection outlived the toggle and drew the other tab's period (e.g. the
            // 2024 annual margins) over the new series until its 2.5s timer fired.
            ProfitPowerPeriodToggle(selectedPeriod: $selectedPeriod)
                .padding(.leading, AppSpacing.xs)
                .onChange(of: selectedPeriod) {
                    selectedDataPoint = nil
                }

            // Main chart
            ProfitPowerChartView(
                dataPoints: currentDataPoints,
                selectedDataPoint: $selectedDataPoint,
                peerWord: profitPowerData.peerWord,
                isDegraded: isDegraded
            )
            .padding(.top, AppSpacing.sm)

            // Legend
            ProfitPowerLegendView(
                peerWord: profitPowerData.peerWord,
                showsPeerLine: currentDataPoints.contains { $0.sectorAverageNetMargin != nil }
            )
                .frame(maxWidth: .infinity)
                .padding(.top, AppSpacing.md)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
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
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                ProfitPowerInfoIcon {
                    showInfoSheet = true
                }
            }

            Spacer()

            // The "Details" affordance is hidden: all six handlers in
            // TickerDetailViewModel are `print()` stubs — no detail screen
            // exists — so the button did nothing when tapped. The callback
            // parameter is intentionally kept so re-enabling is a one-line
            // change once the drill-down ships.
            // Button(action: onDetailTapped) {
            // Text("Details")
            // .font(AppTypography.bodySmallEmphasis)
            // .foregroundColor(AppColors.primaryBlue)
            // }
            // .buttonStyle(.plain)
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

#Preview("Quarterly leg failed upstream") {
    // A degraded build: annual margins are real, the quarterly statement leg failed. The
    // Quarterly tab reads "temporarily unavailable" instead of a fact about the company.
    let data = ProfitPowerSectionData(
        annualData: ProfitPowerSectionData.sampleData.annualData,
        quarterlyData: [],
        peerGroupLevel: "industry"
    )
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            ProfitPowerSectionCard(profitPowerData: data, onDetailTapped: {}, isDegraded: true)
                .padding()
        }
    }
}
