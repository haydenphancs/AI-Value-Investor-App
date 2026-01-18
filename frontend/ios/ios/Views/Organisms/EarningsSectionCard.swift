//
//  EarningsSectionCard.swift
//  ios
//
//  Organism: Complete earnings section card with chart, toggles, and legend
//

import SwiftUI

struct EarningsSectionCard: View {
    let earningsData: EarningsData
    let onDetailTap: (() -> Void)?

    @State private var selectedDataType: EarningsDataType = .eps
    @State private var selectedTimeRange: EarningsTimeRange = .oneYear
    @State private var showPriceLine: Bool = false
    @State private var showInfoSheet: Bool = false

    init(
        earningsData: EarningsData,
        onDetailTap: (() -> Void)? = nil,
        onInfoTap: (() -> Void)? = nil
    ) {
        self.earningsData = earningsData
        self.onDetailTap = onDetailTap
    }

    // Get quarters based on selected data type and time range
    private var displayQuarters: [EarningsQuarterData] {
        let allQuarters = earningsData.quarters(for: selectedDataType)
        
        switch selectedTimeRange {
        case .oneYear:
            // Show last 6 quarters (4 historical + 2 future estimates for 1Y view)
            return Array(allQuarters.suffix(6))
        case .threeYears:
            // Show all quarters (up to 14 for 3 years + future)
            return allQuarters
        }
    }
    
    // Get price history filtered to match displayed quarters
    private var displayPriceHistory: [EarningsPricePoint] {
        let allPriceHistory = earningsData.priceHistory
        
        switch selectedTimeRange {
        case .oneYear:
            // Show last 6 price points (1 year + future)
            return Array(allPriceHistory.suffix(6))
        case .threeYears:
            // Show all price history
            return allPriceHistory
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            headerSection

            // Toggle controls row
            controlsRow

            // Main EPS/Revenue chart
            EarningsChartView(
                quarters: displayQuarters,
                priceHistory: displayPriceHistory,
                showPriceLine: showPriceLine
            )
            
            // Surprise bar chart (3Y only)
            if selectedTimeRange == .threeYears {
                EarningsSurpriseBarChart(quarters: displayQuarters)
            }

            // Surprise percentages row (1Y only - replaced by bar chart in 3Y)
            if selectedTimeRange == .oneYear {
                EarningsSurpriseRow(quarters: displayQuarters)
            }

            // Spacer before legend
            Spacer()
                .frame(height: AppSpacing.md)

            // Legend
            EarningsLegend()
                .frame(maxWidth: .infinity)
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
        .sheet(isPresented: $showInfoSheet) {
            EarningsInfoSheet()
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("Earnings")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                // Info button
                Button {
                    showInfoSheet = true
                } label: {
                    ZStack {
                        Circle()
                            .stroke(AppColors.textMuted, lineWidth: 1.5)
                            .frame(width: 20, height: 20)

                        Text("i")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundColor(AppColors.textMuted)
                    }
                }
                .buttonStyle(PlainButtonStyle())
            }

            Spacer()

            // Detail link
            Button {
                onDetailTap?()
            } label: {
                Text("Detail")
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .buttonStyle(PlainButtonStyle())
        }
    }

    // MARK: - Controls Row

    private var controlsRow: some View {
        HStack {
            // EPS / Revenue toggle
            EarningsDataTypeToggle(selectedType: $selectedDataType)

            Spacer()
                .frame(width: AppSpacing.lg)

            // 1Y / 3Y toggle
            EarningsTimeRangeToggle(selectedRange: $selectedTimeRange)

            Spacer()

            // Price toggle
            EarningsPriceToggle(isEnabled: $showPriceLine)
        }
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            EarningsSectionCard(
                earningsData: EarningsData.sampleData,
                onDetailTap: {
                    print("Detail tapped")
                }
            )
            .padding(AppSpacing.lg)
        }
    }
}
