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

            // Chart
            RevenueBreakdownChartView(data: data)

            // Legend
            RevenueBreakdownLegendView(data: data)
                .padding(.top, AppSpacing.md)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
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

                // The "Detail" affordance is hidden: all six handlers in
            // TickerDetailViewModel are `print()` stubs — no detail screen
            // exists — so the button did nothing when tapped. The callback
            // parameter is intentionally kept so re-enabling is a one-line
            // change once the drill-down ships.
                // Button(action: onDetailTapped) {
                // Text("Detail")
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
            }
            .padding()
        }
    }
}
