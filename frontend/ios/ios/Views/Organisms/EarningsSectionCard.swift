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

    // Get quarters based on selected time range
    private var displayQuarters: [EarningsQuarterData] {
        earningsData.quarters(for: selectedDataType)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            headerSection

            // Toggle controls row
            controlsRow

            // Chart
            EarningsChartView(
                quarters: displayQuarters,
                priceHistory: earningsData.priceHistory,
                showPriceLine: showPriceLine
            )

            // Surprise percentages row
            EarningsSurpriseRow(quarters: displayQuarters)
                .padding(.horizontal, AppSpacing.sm)

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
