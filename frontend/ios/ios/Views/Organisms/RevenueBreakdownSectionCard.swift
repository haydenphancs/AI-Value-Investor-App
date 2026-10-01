//
//  RevenueBreakdownSectionCard.swift
//  ios
//
//  Organism: "How [TICKER] Makes Money" section card for the Financial tab
//

import SwiftUI

struct RevenueBreakdownSectionCard: View {
    // MARK: - Properties

    let data: RevenueBreakdownData
    let onDetailTapped: () -> Void

    // MARK: - State

    @State private var showInfoSheet: Bool = false

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Header
            headerSection

            // Chart (or its "No revenue reported" state — see `hasChartableMagnitude`)
            RevenueBreakdownChartView(data: data)

            // Legend — omitted when there is nothing to chart: it would only list a column
            // of zeros, and a "Net Profit 0" derived from no data is a fabricated figure.
            if data.hasChartableMagnitude {
                RevenueBreakdownLegendView(data: data)
                    .padding(.top, AppSpacing.md)
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
        .sheet(isPresented: $showInfoSheet) {
            RevenueBreakdownInfoSheet()
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .center) {
                Text("How \(data.tickerSymbol) Makes Money")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                GrowthInfoIcon {
                    showInfoSheet = true
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

            if !data.fiscalYear.isEmpty {
                Text("FY \(data.fiscalYear)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            VStack(spacing: AppSpacing.lg) {
                RevenueBreakdownSectionCard(
                    data: RevenueBreakdownData.sampleApple,
                    onDetailTapped: {}
                )

                RevenueBreakdownSectionCard(
                    data: RevenueBreakdownData.sampleLossCompany,
                    onDetailTapped: {}
                )

                // Outlier shapes (illustrative sample figures): the chart must stay inside
                // the card, above the legend.
                RevenueBreakdownSectionCard(
                    data: RevenueBreakdownData.sampleOperatingLossTurnedProfit,
                    onDetailTapped: {}
                )

                RevenueBreakdownSectionCard(
                    data: RevenueBreakdownData.sampleGainAboveRevenue,
                    onDetailTapped: {}
                )

                RevenueBreakdownSectionCard(
                    data: RevenueBreakdownData.sampleNoRevenue,
                    onDetailTapped: {}
                )
            }
            .padding()
        }
    }
}
