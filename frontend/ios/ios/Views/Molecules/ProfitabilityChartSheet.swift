//
//  ProfitabilityChartSheet.swift
//  ios
//
//  Molecule: the report's Profitability drill-down (opened from the Profitability
//  card in Fundamentals & Growth). One metric per chart — Gross / Operating / Net /
//  FCF Margin + ROE / ROA — each a 2-LINE chart (yellow company line + gray dashed
//  sector line, no bars). Mirrors GrowthChartSheet's chrome 1:1: metric chip picker,
//  "<metric> / Current (TTM):" header, Annual/Quarterly toggle, and a legend + delta card.
//
//  The 4 MARGINS are read from the frozen `profit_power` payload, so they're IDENTICAL
//  to the free TickerDetailView Profit Power chart. ROE/ROA reuse the card's baked
//  fundamentals history. Renders frozen report data; no network call.
//
//  The legend has two lines (owner decision 2026-11-13, as on FundamentalsHistorySheet):
//  line 1 is a CLOSED period ("FY2025", "Q4 '26") against the same period's peer median;
//  line 2 is the Profitability card row's own TTM figure against the median the card
//  scored it with. "Current" is never a closed period. FCF Margin has no card row, so it
//  draws line 1 only.
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
    /// has a company value. A header without the card's TTM figure names THIS period —
    /// a biotech whose latest two years are revenue gaps reads "FY2025: —", never its
    /// 2023 margin presented as the latest one.
    private var latestPoint: ProfitabilityChartPoint? { current.last }

    /// The last period that DID report a company value — shown only as an explicitly
    /// period-labelled line, never asserted as "Current".
    private var lastReported: ProfitabilityChartPoint? {
        current.last(where: { $0.company != nil })
    }

    /// Line 1's pair: the NEWEST period where the company AND its peer line both have a
    /// value, with that period's label (mirrors FundamentalsHistorySheet.sectorPair). An
    /// off-calendar filer's newest closed year (ORCL's FY2026, ended May) has no complete
    /// peer year yet, so the line compares FY2025 with FY2025 instead of reading "Company
    /// only"; the label says which year it is, so an older pair is never mistaken for now.
    private var sectorPair: (company: Double, sector: Double, period: String)? {
        for p in current.reversed() {
            if let c = p.company, let s = p.sector {
                return (c, s, p.period)
            }
        }
        return nil
    }

    /// Line 1's verdict for its colour. All profitability metrics are higher-is-better →
    /// good when company > sector. nil on a card the backend left unrated, as on the card.
    private var isCurrentlyGood: Bool? {
        guard card.starRating > 0, let pair = sectorPair else { return nil }
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
                    ProfitabilityChartView(points: current, higherIsBetter: card.starRating > 0 ? true : nil)
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

    /// The word for the card row's own TTM figure, and the ONLY place "Current" is spelled
    /// (owner decision 2026-11-13: "Current" means now, never a closed year or quarter).
    /// Every Profitability card row is a trailing-twelve-month figure.
    private var currentPrefix: String { "Current (TTM)" }

    /// The Profitability card's own row for the selected metric — the source of the
    /// header's current figure and of line 2. nil for FCF Margin (the card has no FCF row)
    /// and on a report whose card lacks the row.
    private var cardRow: DeepDiveMetric? {
        guard let key = selectedMetric.cardHistoryKey else { return nil }
        return card.metrics.first(where: { $0.historyKey == key })
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(selectedMetric.rawValue)
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
            Text(headerText)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
            // The latest period has no value (a no-revenue year, a quarter whose cash
            // flow has not landed): name the last period that did, with its period.
            if latestPoint?.company == nil, let lr = lastReported, let v = lr.company {
                Text("Last reported: \(pct(v)) (\(periodLabel(lr.period)))")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    /// "Current (TTM): 43.10%" — the card row's own printed figure — only when the row
    /// carries `currentValue`, which the backend sets for a TTM figure alone (never for a
    /// fiscal-year fallback, an "N/M" or a "—"). Otherwise the chart's newest period, named
    /// by its period ("FY2025: 41.20%", "FY2025: —"): a report generated before 2026-11-13,
    /// FCF Margin, or a row with no current number.
    private var headerText: String {
        if let row = cardRow, row.currentValue != nil {
            return "\(currentPrefix): \(row.value)"
        }
        guard let p = latestPoint else { return "—" }
        guard let v = p.company else { return "\(periodLabel(p.period)): —" }
        return "\(periodLabel(p.period)): \(pct(v))"
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

    /// How far a legend comparison line may shrink to stay on one row (the same floor as
    /// FundamentalsHistorySheet.legendLineMinScale).
    static let legendLineMinScale: CGFloat = 0.85

    private func pct(_ v: Double) -> String {
        abs(v) >= 100 ? String(format: "%.0f%%", v) : String(format: "%.2f%%", v)
    }

    /// A closed period's legend name: "FY2025" on Annual (the axis reads "2025"; the "FY"
    /// says it is the company's fiscal year, which for ORCL ends in May), the quarter label
    /// ("Q4 '26") on Quarterly. Never "Current" — that word is the TTM figure's.
    private func periodLabel(_ label: String) -> String {
        selectedPeriod == .annual ? "FY\(label)" : label
    }

    /// Line 1: a closed period against the SAME period's peer median, named by `prefix`
    /// (its period label).
    private func deltaText(prefix: String, company: Double, sector: Double) -> String {
        let c = pct(company)
        let s = pct(sector)
        let peer = peerWord            // "Industry" or "Sector"
        let peerLower = peer.lowercased()
        let spread = String(format: "%+.1f pts vs \(peerLower)", company - sector)
        // The "×" multiple only means anything when the peer base is non-trivial.
        if company > 0 && sector >= 2.0 {
            return "\(prefix) \(c) · \(peer) \(s) · \(String(format: "%.2f×", company / sector)) vs \(peerLower)"
        }
        return "\(prefix) \(c) · \(peer) \(s) · \(spread)"
    }

    /// Line 2 (owner decision 2026-11-13): the card row's OWN comparison — the TTM figure it
    /// printed and the exact median it scored that figure against (`currentValue` /
    /// `currentPeerMedian`), so the ratio matches the card's label to the digit. Reads no
    /// period, series or line level, so it is the same on both tabs. nil without a carried
    /// median (a report generated before 2026-11-13, a fiscal-year fallback, an "N/M" row, a
    /// card with no peer group): the label is never parsed — it rounds the median.
    private func currentLineText(_ row: DeepDiveMetric) -> String? {
        guard let median = row.currentPeerMedian else { return nil }
        let peer = currentPeerWord(row)
        var text = "\(currentPrefix) \(row.value) · \(peer) \(String(format: "%.2f%%", median))"
        if let value = row.currentValue, value > 0, median > 0 {
            // A ratio that rounds to "0.00×" says nothing; the colour still carries the
            // verdict. A negative figure prints no ratio, exactly as the card's label.
            let ratio = String(format: "%.2f×", value / median)
            if ratio != "0.00×" {
                text += " · \(ratio) vs \(peer.lowercased())"
            }
        } else if row.currentValue == nil {
            text += " · N/A vs \(peer.lowercased())"
        }
        return text
    }

    /// The group of the median line 2 quotes: the row's own `peerLevel`, else the card's.
    private func currentPeerWord(_ row: DeepDiveMetric) -> String {
        (row.peerLevel ?? card.peerGroupLevel) == "industry" ? "Industry" : "Sector"
    }

    /// Line 2's colour is the card's own verdict on the row (owner: "Match the card"): the
    /// score it gave (>= 4 good, <= 2 bad) — never the bare side of the median. Neutral on
    /// an unrated card or an unscored row; muted when there is no current number to judge.
    private func currentLineColor(_ row: DeepDiveMetric) -> Color {
        guard row.currentValue != nil else { return AppColors.textMuted }
        guard card.starRating > 0, let score = row.currentScore else { return AppColors.textPrimary }
        if score >= 4 { return AppColors.gain }
        if score <= 2 { return AppColors.loss }
        return AppColors.textPrimary
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
            // Line 1: a closed year or quarter against the SAME period's peer median
            // (`sectorPair`), named by its period — "Current" belongs to line 2 alone.
            // Each comparison stays on ONE row, as on FundamentalsHistorySheet (owner,
            // 2026-11-13: "i don't want to make new line for the second line"): it shrinks a
            // little before it would wrap, never below 85% (14pt → 11.9pt, above the 11pt
            // data floor).
            if let pair = sectorPair {
                Text(deltaText(prefix: periodLabel(pair.period),
                               company: pair.company, sector: pair.sector))
                    .font(AppTypography.bodySmall)
                    .foregroundColor(deltaColor)
                    .multilineTextAlignment(.center)
                    .lineLimit(1)
                    .minimumScaleFactor(Self.legendLineMinScale)
                    .allowsTightening(true)
            } else if let lr = lastReported, let v = lr.company {
                Text("\(periodLabel(lr.period)) \(pct(v)) · Company only")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textMuted)
                    .multilineTextAlignment(.center)
            }
            // Line 2: today's TTM figure against the median the card compared it with.
            if let row = cardRow, let text = currentLineText(row) {
                Text(text)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(currentLineColor(row))
                    .multilineTextAlignment(.center)
                    .lineLimit(1)
                    .minimumScaleFactor(Self.legendLineMinScale)
                    .allowsTightening(true)
            }
        }
        .padding(.vertical, AppSpacing.sm)
        // `sm`, not `md`: the comparison lines use more of the card's width (owner,
        // 2026-11-13: "stress out the line a bit to both side of the card").
        .padding(.horizontal, AppSpacing.sm)
        .frame(maxWidth: .infinity, alignment: .center)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }
}

// ORCL shape (fiscal year ends May): the industry line ends at its last complete year, so
// line 1 compares FY2025 with FY2025, and line 2 is the card row's TTM figure against the
// median the card scored it with. FCF Margin (no card row) shows line 1 only.
#Preview("Profitability") {
    let years: [String] = ["2022", "2023", "2024", "2025", "2026"]
    let gross: [Double?] = [79.1, 77.7, 71.4, 70.5, 68.6]
    let industryGross: [Double?] = [61.0, 62.2, 63.1, 63.8, nil]
    let roe: [Double?] = [101.2, 120.4, 159.4, 69.1, 72.5]
    let industryROE: [Double?] = [18.0, 17.5, 19.2, 20.1, nil]
    let chartPoints: [ProfitabilityChartPoint] = zip(years, zip(gross, industryGross)).map {
        ProfitabilityChartPoint(period: $0.0, company: $0.1.0, sector: $0.1.1)
    }
    let grossSeries = ProfitabilityMetricSeries(
        metric: .grossMargin, annual: chartPoints, quarterly: [],
        annualPeerLevel: "industry"
    )
    let roeHistory: [MetricHistoryPoint] = zip(years, roe).map {
        MetricHistoryPoint(period: $0.0, value: $0.1)
    }
    let roePeers: [MetricHistoryPoint] = zip(years, industryROE).map {
        MetricHistoryPoint(period: $0.0, value: $0.1)
    }
    let roeRow = DeepDiveMetric(
        label: "Return on Equity (ROE) (3.45x sector avg 19.8%)",
        value: "68.40%",
        trend: nil,
        historyKey: "roe",
        historyUnit: "percent",
        annualHistory: roeHistory,
        sectorAnnualHistory: roePeers,
        sectorAnnualLevel: "industry",
        currentValue: 68.4,
        currentPeerMedian: 19.83,
        currentScore: 5,
        peerLevel: "industry"
    )
    let grossRow = DeepDiveMetric(
        label: "Gross Margin (1.04x sector avg 66.0%)",
        value: "68.62%",
        trend: nil,
        historyKey: "gross_margin",
        historyUnit: "percent",
        currentValue: 68.62,
        currentPeerMedian: 66.02,
        currentScore: 3,
        peerLevel: "industry"
    )
    let card = DeepDiveMetricCard(
        title: "Profitability",
        starRating: 4,
        metrics: [grossRow, roeRow],
        qualityLabel: "Above Industry",
        qualitySentiment: "positive",
        peerGroupLevel: "industry"
    )
    ProfitabilityChartSheet(card: card, marginSeries: [grossSeries])
}
