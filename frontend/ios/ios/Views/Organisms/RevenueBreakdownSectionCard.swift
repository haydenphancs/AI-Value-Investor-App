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
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
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
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("How \(data.tickerSymbol) Makes Money")
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
