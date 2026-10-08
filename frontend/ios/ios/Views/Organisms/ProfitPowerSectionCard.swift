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
    /// The PEER lookup failed upstream (`degraded` holds "benchmarks"): the margins are
    /// complete but the dashed median is missing, so a muted line says so. Defaults to false.
    var peerComparisonUnavailable: Bool = false

    // MARK: - State

    /// The Annual/Quarterly choice, saved on this device so the card opens the way the user
    /// last left it (it was `@State`, reset by every tab switch, ticker and relaunch — see
    /// `GrowthSectionCard`). Its OWN key: tapping Quarterly on Growth must not flip this card.
    /// A stable token, never the toggle wording; "" or an unknown token reads as Annual.
    /// A display preference of this phone, not account data: deliberately NOT cleared by
    /// `AppState.discardDataForEndedSession()`.
    @AppStorage("caydex_profit_power_period") private var storedPeriodToken: String = ""
    /// Set by a toggle tap on THIS card. From then on the tapped period is shown as is, even
    /// one with no margins (its empty state says why, as it always did) — otherwise that tap
    /// would do nothing. Until then the card shows the SAVED period only where it has data.
    @State private var periodWasTapped: Bool = false
    @State private var showInfoSheet: Bool = false
    @State private var selectedDataPoint: ProfitPowerDataPoint? = nil

    // MARK: - Computed Properties

    /// The saved period (or Annual). Assigned only from a toggle tap, which saves it.
    private var selectedPeriod: ProfitPowerPeriodType {
        get { ProfitPowerPeriodType(preferenceToken: storedPeriodToken) ?? .annual }
        nonmutating set { storedPeriodToken = newValue.preferenceToken }
    }

    /// The period on screen. A saved Quarterly opened on a ticker with no quarterly margins
    /// (annual filings only, or a failed quarterly leg) would land on "Margin data isn't
    /// available for this company." — false, with annual margins one tap away. So, until
    /// the user taps, fall back to the period that HAS margins. Derived, never written back:
    /// the next ticker with quarterly margins opens on Quarterly again.
    private var displayedPeriod: ProfitPowerPeriodType {
        if periodWasTapped || hasCompanyMargins(selectedPeriod) { return selectedPeriod }
        return ProfitPowerPeriodType.allCases.first(where: { hasCompanyMargins($0) }) ?? selectedPeriod
    }

    /// The toggle shows the period on screen; a tap saves it and is shown as is.
    private var periodSelection: Binding<ProfitPowerPeriodType> {
        Binding(
            get: { displayedPeriod },
            set: { period in
                periodWasTapped = true
                selectedPeriod = period
            }
        )
    }

    /// Whether `period` has a COMPANY margin to plot — the same test `ProfitPowerChartView`
    /// applies before its empty state (a peer line alone is not a Profit Power chart).
    private func hasCompanyMargins(_ period: ProfitPowerPeriodType) -> Bool {
        profitPowerData.dataPoints(for: period).contains { p in
            [p.grossMargin, p.operatingMargin, p.fcfMargin, p.netMargin]
                .contains { $0?.isFinite == true }
        }
    }

    private var currentDataPoints: [ProfitPowerDataPoint] {
        profitPowerData.dataPoints(for: displayedPeriod)
    }

    /// "Industry" / "Sector" for the net-margin line ON SCREEN: the backend picks the annual
    /// and the quarterly line's peer group separately, so Annual can be an industry line
    /// while Quarterly is the sector's.
    private var peerWord: String {
        profitPowerData.peerWord(for: displayedPeriod)
    }

    /// Whether the period ON SCREEN draws any dashed peer median. The backend flags a failed
    /// peer lookup ("benchmarks") when EITHER period's read failed and still draws the one
    /// that succeeded, so the "temporarily unavailable" note is shown only over a tab that
    /// really has no peer line — never right under a drawn median and its legend entry.
    private var drawsPeerLine: Bool {
        currentDataPoints.contains(where: { $0.sectorAverageNetMargin != nil })
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title, info icon, and detail link
            headerSection

            // Period toggle (Annual / Quarterly). Switching clears the tooltip: the
            // selection outlived the toggle and drew the other tab's period (e.g. the
            // 2024 annual margins) over the new series until its 2.5s timer fired. The
            // period ON SCREEN can also change with no new choice (a reload brings back the
            // saved period's margins), so that clears it too.
            ProfitPowerPeriodToggle(selectedPeriod: periodSelection)
                .padding(.leading, AppSpacing.xs)
                .onChange(of: selectedPeriod) {
                    selectedDataPoint = nil
                }
                .onChange(of: displayedPeriod) {
                    selectedDataPoint = nil
                }

            // Main chart
            ProfitPowerChartView(
                dataPoints: currentDataPoints,
                selectedDataPoint: $selectedDataPoint,
                peerWord: peerWord,
                isDegraded: isDegraded
            )
            .padding(.top, AppSpacing.sm)

            // Legend
            ProfitPowerLegendView(
                peerWord: peerWord,
                showsPeerLine: currentDataPoints.contains { $0.sectorAverageNetMargin != nil }
            )
                .frame(maxWidth: .infinity)
                .padding(.top, AppSpacing.md)

            // The peer lookup failed AND this tab draws no median: say why it is missing.
            if peerComparisonUnavailable && !drawsPeerLine {
                PeerComparisonUnavailableNote()
            }
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

// MARK: - Saved-choice token

/// What the saved period is stored as: the case name, never `rawValue` (the toggle's
/// wording). An unknown token decodes to nil, so the card shows Annual and leaves the store alone.
private extension ProfitPowerPeriodType {
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
            ProfitPowerSectionCard(
                profitPowerData: ProfitPowerSectionData.sampleData,
                onDetailTapped: {}
            )
            .padding()
        }
    }
}

#Preview("Quarterly leg failed upstream") {
    // A degraded build: annual margins are real, the quarterly statement leg failed. A saved
    // Quarterly opens on Annual; TAPPING Quarterly reads "temporarily unavailable" instead
    // of a fact about the company.
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

#Preview("Per-period peer level") {
    // Annual draws an INDUSTRY median, Quarterly the sector's: the legend follows the tab.
    let data: ProfitPowerSectionData = {
        var d = ProfitPowerSectionData.sampleData
        d.peerGroupLevels = ["annual": "industry", "quarterly": "sector"]
        return d
    }()
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            ProfitPowerSectionCard(profitPowerData: data, onDetailTapped: {})
                .padding()
        }
    }
}

#Preview("Peer lookup failed") {
    // `degraded: ["benchmarks"]`: the margins are complete, no peer line, one muted note.
    // Illustrative margins with the medians removed.
    let data: ProfitPowerSectionData = {
        func withoutPeer(_ points: [ProfitPowerDataPoint]) -> [ProfitPowerDataPoint] {
            points.map {
                ProfitPowerDataPoint(period: $0.period, grossMargin: $0.grossMargin,
                                     operatingMargin: $0.operatingMargin, fcfMargin: $0.fcfMargin,
                                     netMargin: $0.netMargin, sectorAverageNetMargin: nil)
            }
        }
        return ProfitPowerSectionData(
            annualData: withoutPeer(ProfitPowerSectionData.sampleData.annualData),
            quarterlyData: withoutPeer(ProfitPowerSectionData.sampleData.quarterlyData)
        )
    }()
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            ProfitPowerSectionCard(profitPowerData: data, onDetailTapped: {},
                                   peerComparisonUnavailable: true)
                .padding()
        }
    }
}
