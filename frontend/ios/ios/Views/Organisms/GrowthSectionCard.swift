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
    /// The build lost a company data leg upstream (`TickerDetailViewModel.growthIsDegraded`):
    /// a series or period it emptied is an outage, so the card says the data is temporarily
    /// unavailable instead of silently dropping a chip or the Quarterly toggle.
    private(set) var isDegraded: Bool = false

    // MARK: - State

    @State private var selectedMetric: GrowthMetricType
    @State private var selectedPeriod: GrowthPeriodType
    @State private var showInfoSheet: Bool

    // MARK: - Init

    init(growthData: GrowthSectionData, onDetailTapped: @escaping () -> Void) {
        self.growthData = growthData
        self.onDetailTapped = onDetailTapped
        // Open on the first metric that HAS data (on Annual when it has it) — the shared
        // helper the report's GrowthChartSheet uses. A hard-coded .eps/.annual opened on
        // an empty chart for a ticker whose EPS is null on every FMP row.
        let start = growthData.initialSelection()
        _selectedMetric = State(initialValue: start.metric)
        _selectedPeriod = State(initialValue: start.period)
        _showInfoSheet = State(initialValue: false)
    }

    /// Same card, told whether its build is degraded (`isDegraded`).
    init(growthData: GrowthSectionData, isDegraded: Bool, onDetailTapped: @escaping () -> Void) {
        self.init(growthData: growthData, onDetailTapped: onDetailTapped)
        self.isDegraded = isDegraded
    }

    // MARK: - Computed Properties

    /// Chips offered: only metrics with at least one point in either period.
    private var availableMetrics: [GrowthMetricType] {
        growthData.metricsWithData()
    }

    /// The metric actually shown. Falls back to the first available one when the
    /// selection has no data (e.g. a refreshed payload lost that series), so the card
    /// never renders a selected-but-empty chip.
    private var displayedMetric: GrowthMetricType {
        availableMetrics.contains(selectedMetric) ? selectedMetric : (availableMetrics.first ?? selectedMetric)
    }

    /// Periods the shown metric has data for. The toggle appears only when there are two.
    private var availablePeriods: [GrowthPeriodType] {
        growthData.periodsWithData(for: displayedMetric)
    }

    /// The period actually shown: the selection when the metric has it, else the period
    /// it does have (Quarterly → Annual when the quarterly leg failed). Derived rather than
    /// written back, so switching back to a metric with both restores the user's choice.
    private var displayedPeriod: GrowthPeriodType {
        availablePeriods.contains(selectedPeriod) ? selectedPeriod : (availablePeriods.first ?? selectedPeriod)
    }

    private var currentDataPoints: [GrowthDataPoint] {
        growthData.dataPoints(for: displayedMetric, period: displayedPeriod)
    }

    /// Whether the shown series draws a dashed peer line at all.
    private var showsPeerLine: Bool {
        currentDataPoints.contains { $0.sectorAverageYoY != nil }
    }

    // MARK: - Body

    var body: some View {
        // No metric has any data: hide the card rather than show an empty shell.
        if !availableMetrics.isEmpty {
            card
        }
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title, info icon, and detail link
            headerSection

            // What is drawn is real but incomplete: an upstream leg failed.
            if isDegraded {
                partialDataNote
            }

            // Metric chips — only metrics that have data. Composed from the chip atom
            // directly (never GrowthMetricType.allCases, which would offer empty metrics).
            metricChips

            // Period toggle (Annual / Quarterly) — only when the metric has both.
            if availablePeriods.count > 1 {
                GrowthPeriodToggle(selectedPeriod: $selectedPeriod)
                    .padding(.leading, AppSpacing.xs)
            }

            // Main chart — the SAME shared GrowthChartView the report sheet uses
            // (sign-corrected YoY, robust line scaling, no right axis). No leading
            // offset, so the fixed $-axis sits flush with the card padding exactly
            // like the report (the old -AppSpacing.md offset cramped the axis).
            GrowthChartView(dataPoints: currentDataPoints)
                .id("\(displayedMetric.rawValue)-\(displayedPeriod.rawValue)")
                .padding(.top, AppSpacing.sm)
                .animation(.easeInOut(duration: 0.3), value: selectedMetric)
                .animation(.easeInOut(duration: 0.3), value: selectedPeriod)

            // Legend — names the peer group the dashed line actually comes from.
            GrowthLegendView(
                peerWord: growthData.peerWord(for: displayedMetric, period: displayedPeriod),
                showsPeerLine: showsPeerLine
            )
            .frame(maxWidth: .infinity)
            .padding(.top, AppSpacing.xs)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
        .sheet(isPresented: $showInfoSheet) {
            GrowthInfoSheet()
        }
    }

    /// One muted line over a partial card (the Earnings card's pattern): an outage, not a
    /// fact about the company.
    private var partialDataNote: some View {
        HStack(alignment: .firstTextBaseline, spacing: AppSpacing.xs) {
            Image(systemName: "exclamationmark.triangle")
                .font(AppTypography.iconXS)
                .foregroundColor(AppColors.caution)
                .accessibilityHidden(true)
            Text("Some growth data is temporarily unavailable, so this chart may be incomplete.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: - Metric chips

    private var metricChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: AppSpacing.sm) {
                ForEach(availableMetrics) { metric in
                    GrowthMetricChip(
                        metricType: metric,
                        isSelected: displayedMetric == metric,
                        action: {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                selectedMetric = metric
                            }
                        }
                    )
                }
            }
            .padding(.horizontal, AppSpacing.xs)
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("Growth")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                GrowthInfoIcon {
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
            GrowthSectionCard(
                growthData: GrowthSectionData.sampleData,
                onDetailTapped: {}
            )
            .padding()
        }
    }
}

#Preview("Quarterly leg missing, industry peers") {
    // A degraded build: no quarterly series and no EPS at all. Chips show only the
    // metrics with data, the toggle hides, and the legend reads "Industry".
    let data: GrowthSectionData = {
        var d = GrowthSectionData(
            epsAnnual: [],
            epsQuarterly: [],
            revenueAnnual: GrowthSectionData.sampleData.revenueAnnual,
            revenueQuarterly: [],
            netIncomeAnnual: GrowthSectionData.sampleData.netIncomeAnnual,
            netIncomeQuarterly: [],
            operatingProfitAnnual: [],
            operatingProfitQuarterly: [],
            freeCashFlowAnnual: [],
            freeCashFlowQuarterly: []
        )
        d.peerGroupLevels = ["revenue_annual": "industry", "net_income_annual": "sector"]
        return d
    }()
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            GrowthSectionCard(growthData: data, isDegraded: true, onDetailTapped: {})
                .padding()
        }
    }
}
