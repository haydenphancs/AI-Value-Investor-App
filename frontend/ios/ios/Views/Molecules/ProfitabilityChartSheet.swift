//
//  ProfitabilityChartSheet.swift
//  ios
//
//  Molecule: the report's Profitability drill-down (opened from the Profitability
//  card in Fundamentals & Growth). One metric per chart — Gross / Operating / Net /
//  FCF Margin + ROE / ROA — each a 2-LINE chart (yellow company line + gray dashed
//  sector line, no bars). Mirrors GrowthChartSheet's chrome 1:1: metric chip picker,
//  "<metric> / Current:" header, Annual/Quarterly toggle, and a legend + delta card.
//
//  The 4 MARGINS are read from the frozen `profit_power` payload, so they're IDENTICAL
//  to the free TickerDetailView Profit Power chart. ROE/ROA reuse the card's baked
//  fundamentals history. Renders frozen report data; no network call.
//

import SwiftUI

struct ProfitabilityChartSheet: View {
    let card: DeepDiveMetricCard          // nav title ("Profitability")
    private let allSeries: [ProfitabilityMetricSeries]

    @Environment(\.dismiss) private var dismiss
    @State private var selectedMetric: ProfitabilityMetricType
    @State private var selectedPeriod: GrowthPeriodType

    init(card: DeepDiveMetricCard, marginSeries: [ProfitabilityMetricSeries]) {
        self.card = card
        // 4 margins (from profit_power) + ROE/ROA (from the card's baked history),
        // in display order.
        var series = marginSeries
        // ROE/ROA come from the card's baked history. Each line names its OWN peer group
        // per tab (`toProfitabilitySeries` reads sectorAnnualLevel / sectorQuarterlyLevel:
        // a line is one population, chosen per period type); a report that predates those
        // fields falls back to the margins' payload-wide level.
        let fallbackLevel: String? = marginSeries.first?.peerLevel
        if let roe = card.metrics.first(where: { $0.historyKey == "roe" }) {
            series.append(roe.toProfitabilitySeries(.roe, peerLevel: fallbackLevel))
        }
        if let roa = card.metrics.first(where: { $0.historyKey == "roa" }) {
            series.append(roa.toProfitabilitySeries(.roa, peerLevel: fallbackLevel))
        }
        self.allSeries = series

        // Open on the first metric that has a chartable series, on Annual if present.
        let avail = series.filter { $0.hasData }
        let first = avail.first ?? series.first
        _selectedMetric = State(initialValue: first?.metric ?? .grossMargin)
        _selectedPeriod = State(initialValue: (first?.hasAnnual ?? false) ? .annual : .quarterly)
    }

    /// Metrics with a chartable series in either granularity (no empty chips).
    private var availableMetrics: [ProfitabilityMetricType] {
        allSeries.filter { $0.hasData }.map { $0.metric }
    }

    private func series(_ m: ProfitabilityMetricType) -> ProfitabilityMetricSeries? {
        allSeries.first { $0.metric == m }
    }

    /// "Industry" when the drawn benchmark line is industry-level, else "Sector".
    /// The line ON SCREEN names its own group: the selected metric's level for the selected
    /// TAB first (the backend picks each metric's annual and quarterly line separately, so
    /// the Quarterly tab can be a sector line under an industry Annual one), then the
    /// series' period-agnostic level (an older report), then the ticker-wide card level.
    private var peerWord: String {
        let line: ProfitabilityMetricSeries? = series(selectedMetric)
        let own: String? = selectedPeriod == .annual ? line?.annualPeerLevel : line?.quarterlyPeerLevel
        let level: String? = own ?? line?.peerLevel ?? card.peerGroupLevel
        return level == "industry" ? "Industry" : "Sector"
    }

    private var current: [ProfitabilityChartPoint] {
        series(selectedMetric)?.points(for: selectedPeriod) ?? []
    }

    private func quarterlyAvailable(_ m: ProfitabilityMetricType) -> Bool {
        (series(m)?.quarterly.filter { $0.company != nil }.count ?? 0) >= 2
    }

    /// The genuinely-latest charted period (the chart's right edge), whether or not it
    /// has a company value. Header and legend anchor HERE — mirrors GrowthChartSheet.
    /// It used to skip back to the last period WITH a value, so a biotech whose latest
    /// two years are revenue gaps read "Current: 20.00%" (its 2023 margin) under a
    /// chart ending in two empty years, while the report's Profitability card said "—".
    private var latestPoint: ProfitabilityChartPoint? { current.last }

    /// The last period that DID report a company value — shown only as an explicitly
    /// period-labelled secondary line, never asserted as "Current".
    private var lastReported: ProfitabilityChartPoint? {
        current.last(where: { $0.company != nil })
    }

    /// Same-period company-vs-sector pair for the LATEST period only, when it has both —
    /// so the verdict-coloured vs-industry line is never computed from an older period
    /// than the one the header reports.
    private var sectorPair: (company: Double, sector: Double)? {
        guard let p = latestPoint, let c = p.company, let s = p.sector else { return nil }
        return (c, s)
    }

    /// Latest-period verdict for the delta-text color + band caption. All
    /// profitability metrics are higher-is-better → good when company > sector.
    private var isCurrentlyGood: Bool? {
        guard let pair = sectorPair else { return nil }
        let d = pair.company - pair.sector
        return d == 0 ? nil : d > 0
    }
    private var deltaColor: Color {
        guard let good = isCurrentlyGood else { return AppColors.textPrimary }
        return good ? AppColors.bullish : AppColors.bearish
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    metricPicker
                    header
                    if quarterlyAvailable(selectedMetric) { periodToggle }
                    ProfitabilityChartView(points: current, higherIsBetter: true)
                        .id("\(selectedMetric.rawValue)-\(selectedPeriod.rawValue)")
                    legendAndDelta
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background.ignoresSafeArea())
            .navigationTitle(card.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }

    // MARK: - Metric picker (chips)

    private var metricPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: AppSpacing.sm) {
                ForEach(availableMetrics) { m in
                    let isSelected = m == selectedMetric
                    Button {
                        selectedMetric = m
                        if selectedPeriod == .quarterly && !quarterlyAvailable(m) {
                            selectedPeriod = .annual
                        }
                    } label: {
                        Text(m.rawValue)
                            .font(AppTypography.labelSmall)
                            .fontWeight(isSelected ? .semibold : .regular)
                            .foregroundColor(isSelected ? AppColors.textOnAccent : AppColors.textSecondary)  // selected chip sits on primaryFill: textPrimary is 3.81:1 in light
                            .padding(.horizontal, AppSpacing.md)
                            .padding(.vertical, AppSpacing.sm)
                            .background(
                                Capsule().fill(
                                    isSelected
                                        ? AppColors.chipSelectedBackground
                                        : AppColors.chipUnselectedBackground
                                )
                            )
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 2)
        }
        .contentMargins(.horizontal, AppSpacing.lg, for: .scrollContent)
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(selectedMetric.rawValue)
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
            Text("Current: \(currentText)")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
            // The latest period has no value (a no-revenue year, a quarter whose cash
            // flow has not landed): name the last period that did, with its period.
            if latestPoint?.company == nil, let lr = lastReported, let v = lr.company {
                Text("Last reported: \(pct(v)) (\(lr.period))")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    private var currentText: String {
        guard let p = latestPoint else { return "—" }
        guard let v = p.company else { return "— (\(p.period))" }
        return pct(v)
    }

    // MARK: - Period toggle

    private var periodToggle: some View {
        Picker("Period", selection: periodBinding) {
            ForEach(GrowthPeriodType.allCases) { p in
                Text(p.rawValue).tag(p)
            }
        }
        .pickerStyle(.segmented)
    }

    /// Toggling Annual/Quarterly commits the state change with animations
    /// disabled, so the chart swaps instantly instead of cross-fading the
    /// `.id`-keyed view. (Metric chips are plain Buttons and already don't animate.)
    private var periodBinding: Binding<GrowthPeriodType> {
        Binding(
            get: { selectedPeriod },
            set: { newValue in
                var txn = Transaction()
                txn.disablesAnimations = true
                withTransaction(txn) { selectedPeriod = newValue }
            }
        )
    }

    // MARK: - Legend + latest vs-sector delta

    private func pct(_ v: Double) -> String {
        abs(v) >= 100 ? String(format: "%.0f%%", v) : String(format: "%.2f%%", v)
    }

    private func deltaText(company: Double, sector: Double) -> String {
        let c = pct(company)
        let s = pct(sector)
        let peer = peerWord            // "Industry" or "Sector"
        let peerLower = peer.lowercased()
        let spread = String(format: "%+.1f pts vs \(peerLower)", company - sector)
        // The "×" multiple only means anything when the peer base is non-trivial.
        if company > 0 && sector >= 2.0 {
            return "Current \(c) · \(peer) \(s) · \(String(format: "%.2f×", company / sector)) vs \(peerLower)"
        }
        return "Current \(c) · \(peer) \(s) · \(spread)"
    }

    /// Legend line when the LATEST period has no company value; nil when it has one (or
    /// the series is empty).
    private var notReportedText: String? {
        guard let p = latestPoint, p.company == nil else { return nil }
        let head: String = "\(p.period) not reported"
        guard let lr = lastReported, let v = lr.company else { return head }
        let tail: String = "last reported \(pct(v)) (\(lr.period))"
        return head + " · " + tail
    }

    @ViewBuilder
    private var legendAndDelta: some View {
        VStack(alignment: .center, spacing: AppSpacing.xs) {
            HStack(spacing: AppSpacing.md) {
                HStack(spacing: 5) {  // solid yellow company line swatch
                    Capsule()
                        .fill(AppColors.growthYoYYellow)
                        .frame(width: 14, height: 3)
                    Text("Company")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textSecondary)
                }
                // Only a line that is DRAWN is named (2026-10-08): with no peer value on
                // screen there is no dashed line, so no "Industry/Sector Average" entry.
                if current.contains(where: { $0.sector != nil }) {
                    HStack(spacing: 5) {  // dashed-gray sector swatch
                        HStack(spacing: 2) {
                            ForEach(0..<3, id: \.self) { _ in
                                Capsule().fill(AppColors.growthSectorGray).frame(width: 4, height: 2)
                            }
                        }
                        Text("\(peerWord) Average")
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
            if let pair = sectorPair {
                Text(deltaText(company: pair.company, sector: pair.sector))
                    .font(AppTypography.bodySmall)
                    .foregroundColor(deltaColor)
                    .multilineTextAlignment(.center)
            } else if let v = latestPoint?.company {
                Text("Current \(pct(v)) · Company only")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textMuted)
                    .multilineTextAlignment(.center)
            } else if let text = notReportedText {
                // Latest period has no company value: say so, with its period, and quote
                // the last reported value only under its own period — never a verdict.
                Text(text)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textMuted)
                    .multilineTextAlignment(.center)
            }
        }
        .padding(.vertical, AppSpacing.sm)
        .padding(.horizontal, AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .center)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }
}
