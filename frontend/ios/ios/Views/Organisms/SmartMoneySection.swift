//
//  SmartMoneySection.swift
//  ios
//
//  Organism: Complete Smart Money section card
//  Displays insider/hedge fund/congress trading activity with flow chart
//

import SwiftUI

struct SmartMoneySection: View {
    // MARK: - Properties

    let holdersData: HoldersData

    // MARK: - State

    @State private var selectedTab: SmartMoneyTab = .insider
    @State private var showInfoSheet: Bool = false

    // MARK: - Computed Properties

    private var currentData: SmartMoneyData {
        holdersData.smartMoneyData(for: selectedTab)
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title and info icon
            headerSection

            // Tab selector (Insider / Hedge Funds / Congress)
            SmartMoneyTabSelector(selectedTab: $selectedTab)

            // Period label
            Text("\(currentData.summary.periodDescription) Flow")
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, AppSpacing.xs)

            // Flow chart (price on top, buy/sell volume below)
            SmartMoneyFlowChart(
                priceData: currentData.priceData,
                flowData: currentData.flowData
            )
            .id(selectedTab.rawValue)
            .animation(.easeInOut(duration: 0.3), value: selectedTab)

            // Legend
            SmartMoneyFlowLegend()
                .padding(.top, AppSpacing.sm)

            // Net flow badge
            SmartMoneyNetFlowBadge(summary: currentData.summary)
                .padding(.top, AppSpacing.sm)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .sheet(isPresented: $showInfoSheet) {
            HoldersInfoSheet()
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("Smart Money")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                SmartMoneyInfoIcon {
                    showInfoSheet = true
                }
            }

            Spacer()
        }
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            SmartMoneySection(
                holdersData: HoldersData.sampleData
            )
            .padding()
        }
    }
}
