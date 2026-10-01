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
    /// Re-runs the Financials load. Offered only on the temporarily-unavailable state of a
    /// DEGRADED build; nil renders that notice without a button (it still says what
    /// happened).
    let onRetry: (() -> Void)?

    @State private var selectedDataType: EarningsDataType = .eps
    @State private var selectedTimeRange: EarningsTimeRange = .oneYear
    @State private var showPriceLine: Bool = false
    @State private var showInfoSheet: Bool = false

    init(
        earningsData: EarningsData,
        onDetailTap: (() -> Void)? = nil,
        onInfoTap: (() -> Void)? = nil,
        onRetry: (() -> Void)? = nil
    ) {
        self.earningsData = earningsData
        self.onDetailTap = onDetailTap
        self.onRetry = onRetry
    }

    // Get quarters based on selected data type and time range
    private var displayQuarters: [EarningsQuarterData] {
        let allQuarters = earningsData.quarters(for: selectedDataType)
        let historical = allQuarters.filter { $0.actualValue != nil }
        let future = allQuarters.filter { $0.actualValue == nil }
        let futureSlice = Array(future.prefix(2))

        switch selectedTimeRange {
        case .oneYear:
            // 4 historical quarters + 2 future estimates
            return Array(historical.suffix(4)) + futureSlice
        case .threeYears:
            // Last 12 historical quarters (3 years) + 2 future estimates
            return Array(historical.suffix(12)) + futureSlice
        }
    }

    // Get price history aligned 1:1 with displayed quarters by label matching
    private var displayPriceHistory: [EarningsPricePoint] {
        let allPriceHistory = earningsData.priceHistory
        return displayQuarters.map { quarter in
            allPriceHistory.first { $0.quarter == quarter.quarter }
                ?? EarningsPricePoint(quarter: quarter.quarter, price: 0)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            headerSection

            // Toggle controls row
            controlsRow

            // An empty series gets an honest line, not a chart. The charts used to draw
            // invented axes ("1.10 / 0.50 / -0.10", "10% / 0% / -10%") over an empty plot,
            // which read as real data with missing points. The controls stay, so the other
            // series is still one tap away.
            if displayQuarters.isEmpty {
                emptySeriesState
            } else {
                // A PARTIAL build (an upstream leg failed) can still draw: say so, because
                // e.g. a failed estimates leg turns every dot into "Reported — no analyst
                // consensus", a claim about the company that the outage made false.
                if earningsData.isDegraded {
                    partialDataNote
                }

                // What the dots are: adjusted EPS (or revenue) against consensus — not the
                // GAAP EPS the Growth card plots for the same quarter.
                Text("\(selectedDataType.seriesTitle) vs. analyst consensus")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)

                // Main EPS/Revenue chart
                EarningsChartView(
                    quarters: displayQuarters,
                    priceHistory: displayPriceHistory,
                    dailyPriceHistory: earningsData.dailyPriceHistory,
                    showPriceLine: showPriceLine,
                    dataType: selectedDataType
                )

                // Surprise bar chart (3Y only). `dataType` sets its y-axis gutter, which
                // must equal the main chart's or every bar sits left of its dot.
                if selectedTimeRange == .threeYears {
                    EarningsSurpriseBarChart(quarters: displayQuarters, dataType: selectedDataType)
                }

                // Surprise percentages row (1Y only - replaced by bar chart in 3Y)
                if selectedTimeRange == .oneYear {
                    EarningsSurpriseRow(quarters: displayQuarters, dataType: selectedDataType)
                }

                // Spacer before legend
                Spacer()
                    .frame(height: AppSpacing.md)

                // Legend
                EarningsLegend(showsReported: displayQuarters.contains { $0.result == .noEstimate })
                    .frame(maxWidth: .infinity)
            }

            // Next Earnings Date
            if let nextEarnings = earningsData.nextEarningsDate {
                NextEarningsDateCard(nextEarningsDate: nextEarnings)
            }
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .sheet(isPresented: $showInfoSheet) {
            EarningsInfoSheet()
        }
    }

    // MARK: - Empty / partial states

    /// Why there is nothing to draw. A DEGRADED build (the server names a failed upstream
    /// leg) is an outage, never a fact about the company: an FMP failure arrives as a 200
    /// with empty quarter lists, and this used to read "No Adjusted EPS history available
    /// for this ticker." for AAPL, with nothing suggesting a second look. Only a complete
    /// build with no quarters (a new listing) may say the history does not exist.
    @ViewBuilder
    private var emptySeriesState: some View {
        if earningsData.isDegraded {
            InlineRetryNotice(
                message: "\(selectedDataType.seriesTitle) history is temporarily unavailable. Please try again shortly.",
                onRetry: onRetry
            )
        } else {
            Text("No \(selectedDataType.seriesTitle) history available for this ticker.")
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textSecondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// One muted line over a partial chart: what is drawn is real, but incomplete.
    private var partialDataNote: some View {
        HStack(alignment: .firstTextBaseline, spacing: AppSpacing.xs) {
            Image(systemName: "exclamationmark.triangle")
                .font(AppTypography.iconXS)
                .foregroundColor(AppColors.caution)
                .accessibilityHidden(true)
            Text("Some earnings data couldn't be loaded right now, so this chart may be incomplete.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("Earnings")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                // Info button
                Button {
                    showInfoSheet = true
                } label: {
                    Image(systemName: "info.circle")
                        .font(AppTypography.iconSmall).fontWeight(.medium)
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(.plain)
            }

            Spacer()

            // The "Details" affordance is hidden: all six handlers in
            // TickerDetailViewModel are `print()` stubs — no detail screen
            // exists — so the button did nothing when tapped. The callback
            // parameter is intentionally kept so re-enabling is a one-line
            // change once the drill-down ships.
            // // Detail link
            // Button {
            // onDetailTap?()
            // } label: {
            // Text("Details")
            // .font(AppTypography.label)
            // .foregroundColor(AppColors.primaryBlue)
            // }
            // .buttonStyle(PlainButtonStyle())
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

#Preview("Empty history") {
    // A COMPLETE build with no quarters (a new listing): the history genuinely does not
    // exist, so the card may say so — an honest line, never an invented axis.
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        EarningsSectionCard(
            earningsData: EarningsData(epsQuarters: [], revenueQuarters: [], priceHistory: [])
        )
        .padding(AppSpacing.lg)
    }
}

#Preview("Temporarily unavailable") {
    // An FMP outage the backend degraded to a 200 with empty lists: an outage, never
    // "No … history available for this ticker".
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        EarningsSectionCard(
            earningsData: EarningsData.sampleTemporarilyUnavailable,
            onRetry: {}
        )
        .padding(AppSpacing.lg)
    }
}
