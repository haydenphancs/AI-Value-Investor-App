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
    /// The PEER lookup failed upstream (`degraded` holds "benchmarks"): the company's growth
    /// is complete but no dashed median is drawn, so a muted line says so.
    private(set) var peerComparisonUnavailable: Bool = false

    // MARK: - State

    /// The metric chip and the Annual/Quarterly choice, saved on this device so the card
    /// opens the way the user last left it. They were `@State`: `TickerDetailView`'s tab
    /// switch tears the Financials tab down, so every tab switch, ticker and relaunch put
    /// them back (TestFlight 1.0 (9): "set up once and permanently keep them").
    ///
    /// Stored as a stable token (`preferenceToken`), never the chip wording. "" (nothing
    /// saved yet) or a token this build does not know reads as the fallback (the metric
    /// `fallbackSelection` picked; the shown metric's first period — see `selectedPeriod`). Only a
    /// TAP writes here — the `displayed…` fallbacks below are derived, so a ticker that
    /// lacks the saved metric or period cannot overwrite it. `@AppStorage` also keeps every
    /// live card in step, so a screen further down the stack follows the change.
    ///
    /// Display preferences of this phone, not account data: deliberately NOT cleared by
    /// `AppState.discardDataForEndedSession()` (the standing `caydex_preferred_chart_type`
    /// has). The period key is Growth's own; Profit Power keeps a separate one.
    @AppStorage("caydex_growth_metric") private var storedMetricToken: String = ""
    @AppStorage("caydex_growth_period") private var storedPeriodToken: String = ""
    @State private var showInfoSheet: Bool

    /// What the card opens on while nothing usable is saved.
    private let fallbackSelection: (metric: GrowthMetricType, period: GrowthPeriodType)

    // MARK: - Init

    init(growthData: GrowthSectionData, onDetailTapped: @escaping () -> Void) {
        self.growthData = growthData
        self.onDetailTapped = onDetailTapped
        // With nothing saved, open on the first metric that HAS data (on Annual when it has
        // it) — the shared helper the report's GrowthChartSheet uses. A hard-coded
        // .eps/.annual opened on an empty chart for a ticker whose EPS is null on every FMP row.
        self.fallbackSelection = growthData.initialSelection()
        _showInfoSheet = State(initialValue: false)
    }

    /// Same card, told whether its build is degraded (`isDegraded`) and whether only its peer
    /// line is missing (`peerComparisonUnavailable`).
    init(growthData: GrowthSectionData, isDegraded: Bool, peerComparisonUnavailable: Bool = false,
         onDetailTapped: @escaping () -> Void) {
        self.init(growthData: growthData, onDetailTapped: onDetailTapped)
        self.isDegraded = isDegraded
        self.peerComparisonUnavailable = peerComparisonUnavailable
    }

    // MARK: - Computed Properties

    /// The saved metric (or the fallback). Assigned only from a chip tap, which saves it.
    private var selectedMetric: GrowthMetricType {
        get { GrowthMetricType(preferenceToken: storedMetricToken) ?? fallbackSelection.metric }
        nonmutating set { storedMetricToken = newValue.preferenceToken }
    }

    /// The saved period. With none saved, the first period the metric ON SCREEN has (Annual
    /// first) — not `fallbackSelection.period`, which belongs to the metric
    /// `initialSelection()` picked: with Revenue saved and an EPS that is quarterly-only, that
    /// opened Revenue on Quarterly although it has Annual. `availablePeriods` reads only
    /// `displayedMetric`, never this, so there is no cycle. Assigned only from a toggle tap,
    /// which saves it.
    private var selectedPeriod: GrowthPeriodType {
        get { GrowthPeriodType(preferenceToken: storedPeriodToken) ?? (availablePeriods.first ?? fallbackSelection.period) }
        nonmutating set { storedPeriodToken = newValue.preferenceToken }
    }

    /// The toggle shows the period on screen and a tap saves it. The toggle is offered only
    /// when the metric has both periods, where the two always agree.
    private var periodSelection: Binding<GrowthPeriodType> {
        Binding(get: { displayedPeriod }, set: { selectedPeriod = $0 })
    }

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
    /// written back, so switching back to a metric (or opening a ticker) with both restores
    /// the user's saved choice.
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
                GrowthPeriodToggle(selectedPeriod: periodSelection)
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

            // The peer lookup failed AND the shown series draws no median: say why it is
            // missing. The backend flags "benchmarks" when EITHER period's read failed and
            // still draws the one that succeeded — never put the note under a drawn line.
            if peerComparisonUnavailable && !showsPeerLine {
                PeerComparisonUnavailableNote()
            }
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

// MARK: - Saved-choice tokens

/// What the saved chip is stored as: the case name, never `rawValue` — that is the chip's
/// wording ("Net Income"), and rewording a chip must not lose anyone's saved choice. An
/// unknown token decodes to nil, so the card shows its fallback and leaves the store alone.
private extension GrowthMetricType {
    var preferenceToken: String {
        switch self {
        case .eps: return "eps"
        case .revenue: return "revenue"
        case .netIncome: return "netIncome"
        case .operatingProfit: return "operatingProfit"
        case .freeCashFlow: return "freeCashFlow"
        }
    }

    init?(preferenceToken: String) {
        guard let match = Self.allCases.first(where: { $0.preferenceToken == preferenceToken }) else { return nil }
        self = match
    }
}

/// Same contract for the period ("Annual" / "Quarterly" are the toggle's wording).
private extension GrowthPeriodType {
    var preferenceToken: String {
        switch self {
        case .annual: return "annual"
        case .quarterly: return "quarterly"
        }
    }

    init?(preferenceToken: String) {
        guard let match = Self.allCases.first(where: { $0.preferenceToken == preferenceToken }) else { return nil }
        self = match
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

#Preview("Peer lookup failed") {
    // `degraded: ["benchmarks"]`: the company's growth is complete, no dashed median, one
    // muted note. Illustrative series with the medians removed.
    let data: GrowthSectionData = {
        func withoutPeer(_ points: [GrowthDataPoint]) -> [GrowthDataPoint] {
            points.map {
                GrowthDataPoint(period: $0.period, value: $0.value,
                                yoyChangePercent: $0.yoyChangePercent, sectorAverageYoY: nil)
            }
        }
        let s = GrowthSectionData.sampleData
        return GrowthSectionData(
            epsAnnual: withoutPeer(s.epsAnnual), epsQuarterly: withoutPeer(s.epsQuarterly),
            revenueAnnual: withoutPeer(s.revenueAnnual), revenueQuarterly: withoutPeer(s.revenueQuarterly),
            netIncomeAnnual: withoutPeer(s.netIncomeAnnual), netIncomeQuarterly: withoutPeer(s.netIncomeQuarterly),
            operatingProfitAnnual: withoutPeer(s.operatingProfitAnnual),
            operatingProfitQuarterly: withoutPeer(s.operatingProfitQuarterly),
            freeCashFlowAnnual: withoutPeer(s.freeCashFlowAnnual),
            freeCashFlowQuarterly: withoutPeer(s.freeCashFlowQuarterly)
        )
    }()
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            GrowthSectionCard(growthData: data, isDegraded: false, peerComparisonUnavailable: true,
                              onDetailTapped: {})
                .padding()
        }
    }
}
