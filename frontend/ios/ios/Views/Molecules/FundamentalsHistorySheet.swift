//
//  FundamentalsHistorySheet.swift
//  ios
//
//  Molecule: the tap-to-expand drill-down opened from a Fundamentals &
//  Growth card. Shows a metric picker (the card's chartable metrics), an
//  Annual/Quarterly toggle, and a 5–10y bar chart so the user can answer
//  "is this ratio normal for them, or a one-off?". Renders the history baked
//  into the frozen report — no network call.
//

import SwiftUI

struct FundamentalsHistorySheet: View {
    let card: DeepDiveMetricCard

    @Environment(\.dismiss) private var dismiss
    @State private var selectedMetricID: UUID
    @State private var period: Period = .annual

    enum Period: String, CaseIterable { case annual = "Annual", quarterly = "Quarterly" }

    init(card: DeepDiveMetricCard) {
        self.card = card
        let first = card.chartableMetrics.first
        _selectedMetricID = State(initialValue: first?.id ?? UUID())
        // Open on Quarterly when the initial metric is quarterly-only (no ≥2 annual
        // points) — otherwise the user would land on an empty "Not enough history"
        // annual chart despite the metric having a real quarterly series.
        _period = State(initialValue: (first?.hasAnnualHistory ?? true) ? .annual : .quarterly)
    }

    private var metrics: [DeepDiveMetric] { card.chartableMetrics }

    /// Valuation + Health render the Profitability-style 2-line chart (yellow
    /// company + gray dashed benchmark lines, value rows below — no bars). These
    /// are the only two cards that reach this sheet (Growth/Profitability route to
    /// their own sheets), so any other/legacy title safely falls back to bars.
    private var usesLineChart: Bool {
        card.title == "Valuation" || card.title == "Health"
    }

    /// Latest-period verdict — is the company on the metric's GOOD side of the
    /// benchmark? Drives the delta-text color and the band caption. nil when the
    /// metric's polarity is unknown, there's no comparable pair, or they're equal.
    /// Also nil on a card the backend left unrated (a bank's Health), as on the card itself.
    private func isCurrentlyGood(_ m: DeepDiveMetric) -> Bool? {
        guard card.starRating > 0,
              let hib = DeepDiveMetric.higherIsBetter(forHistoryKey: m.historyKey),
              let pair = sectorPair(m) else { return nil }
        let d = pair.company - pair.sector
        return d == 0 ? nil : (hib ? d > 0 : d < 0)
    }

    /// Green/red for the delta text (only on the line charts that draw the band);
    /// neutral otherwise.
    private func deltaColor(_ m: DeepDiveMetric) -> Color {
        guard usesLineChart, let good = isCurrentlyGood(m) else { return AppColors.textPrimary }
        return good ? AppColors.bullish : AppColors.bearish
    }

    private var selected: DeepDiveMetric? {
        metrics.first { $0.id == selectedMetricID } ?? metrics.first
    }

    private func points(_ m: DeepDiveMetric) -> [MetricHistoryPoint] {
        switch period {
        case .annual:    return m.annualHistory ?? []
        case .quarterly: return m.quarterlyHistory ?? []
        }
    }

    private func sectorPoints(_ m: DeepDiveMetric) -> [MetricHistoryPoint] {
        switch period {
        case .annual:    return m.sectorAnnualHistory ?? []
        case .quarterly: return m.sectorQuarterlyHistory ?? []
        }
    }

    private func quarterlyAvailable(_ m: DeepDiveMetric) -> Bool {
        (m.quarterlyHistory?.compactMap(\.value).count ?? 0) >= 2
    }

    /// Valuation + Health → 2-line chart; any other/legacy card → bar chart.
    @ViewBuilder
    private func chartView(_ m: DeepDiveMetric) -> some View {
        if usesLineChart {
            MetricHistoryLineChart(
                points: points(m), sector: sectorPoints(m), unit: m.historyUnit,
                higherIsBetter: card.starRating > 0
                    ? DeepDiveMetric.higherIsBetter(forHistoryKey: m.historyKey) : nil,
                thresholds: MetricThresholdZones.forHistoryKey(m.historyKey)
            )
        } else {
            MetricHistoryChart(points: points(m), unit: m.historyUnit, sector: sectorPoints(m))
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    if let m = selected {
                        metricPicker
                        header(m)
                        if quarterlyAvailable(m) { periodToggle }
                        chartView(m)
                            .id("\(m.id.uuidString)-\(period.rawValue)")
                            .animation(.easeInOut(duration: 0.25), value: selectedMetricID)
                        legendAndDelta(m)
                        ttmNote(m)
                        omissionNote(m)
                    } else {
                        Text("No history available.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textMuted)
                            .frame(maxWidth: .infinity, minHeight: 200)
                    }
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
                ForEach(metrics) { m in
                    let isSelected = m.id == selectedMetricID
                    Button {
                        selectedMetricID = m.id
                        // Keep the toggle on a granularity this metric actually has:
                        // a metric may lack quarterly data (→ annual), or be
                        // quarterly-only with no annual chart (→ quarterly).
                        if period == .quarterly && !quarterlyAvailable(m) {
                            period = .annual
                        } else if period == .annual && !m.hasAnnualHistory && quarterlyAvailable(m) {
                            period = .quarterly
                        }
                    } label: {
                        Text(m.historyTitle)
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
        // Breathing room at BOTH scroll ends so the first/last chip clears the
        // edge (matches GrowthChartSheet's chip picker 1:1).
        .contentMargins(.horizontal, AppSpacing.lg, for: .scrollContent)
    }

    // MARK: - Header (current value)

    /// The word for the card row's own figure, and the ONLY place "Current" is spelled
    /// (owner decision 2026-11-13: "Current" means now, never a closed year or quarter).
    /// Valuation rows are FMP trailing-twelve-month multiples; Health rows are the latest
    /// balance-sheet readings (D/E, current and quick ratio), so they say no "(TTM)"; a Growth
    /// card (here only when the report has no growth chart) shows its latest fiscal year's
    /// rates, which are neither current nor TTM.
    private var currentPrefix: String {
        switch card.title {
        case "Health": return "Current"
        case "Growth": return "Latest"
        default: return "Current (TTM)"
        }
    }

    /// The word for THIS row's figure: `currentPrefix`, unless the backend named the closed
    /// period it covers (`currentPeriod`, 2026-11-13). A Growth row's figure is a fiscal year's
    /// YoY ("FY2026"); a Price row shown on a last-fiscal-year basis divides today's price by
    /// that year's statement ("FY2025 FCF basis", owner: "Fallback TTM labels") — never "TTM".
    private func prefix(for m: DeepDiveMetric) -> String {
        guard let period = m.currentPeriod, !period.isEmpty else { return currentPrefix }
        guard card.title == "Valuation" else { return period }
        switch m.historyKey {
        case "pfcf":      return "\(period) FCF basis"
        case "ev_ebitda": return "\(period) EBITDA basis"
        default:          return "\(period) basis"
        }
    }

    private func header(_ m: DeepDiveMetric) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(m.historyTitle)
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
            Text("\(prefix(for: m)): \(currentValueText(m.value))")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
        }
    }

    // MARK: - Period toggle

    private var periodToggle: some View {
        Picker("Period", selection: periodBinding) {
            ForEach(Period.allCases, id: \.self) { p in
                Text(p.rawValue).tag(p)
            }
        }
        .pickerStyle(.segmented)
    }

    /// Toggling Annual/Quarterly commits the state change with animations
    /// disabled, so the chart swaps instantly instead of cross-fading the
    /// `.id`-keyed view. (Metric switches still animate via `.animation(value:)`.)
    private var periodBinding: Binding<Period> {
        Binding(
            get: { period },
            set: { newValue in
                var txn = Transaction()
                txn.disablesAnimations = true
                withTransaction(txn) { period = newValue }
            }
        )
    }

    // MARK: - Legend + latest vs-sector delta

    /// The most recent period where BOTH the company AND the sector have a
    /// value — so the vs-sector delta stays a valid same-period comparison
    /// even when the latest quarter's sector median isn't in yet (otherwise it
    /// would mislead with "Company only" despite earlier sector coverage).
    private func sectorPair(_ m: DeepDiveMetric) -> (company: Double, sector: Double, period: String)? {
        var sectorByPeriod: [String: Double] = [:]
        for p in sectorPoints(m) {
            if let v = p.value { sectorByPeriod[p.period] = v }
        }
        for p in points(m).reversed() {
            if let c = p.value, let s = sectorByPeriod[p.period] {
                return (c, s, p.period)
            }
        }
        return nil
    }

    /// Latest company point with a value (for the "Company only" fallback line).
    private func latestCompanyPoint(_ m: DeepDiveMetric) -> MetricHistoryPoint? {
        points(m).last(where: { $0.value != nil })
    }

    /// A closed period's legend name: "FY2025" on Annual (the axis reads "2025"; the "FY"
    /// says it is the company's fiscal year, which for ORCL ends in May), the quarter label
    /// ("Q4 '26") on Quarterly. Never "Current" — that word is the TTM line's.
    private func periodLabel(_ label: String) -> String {
        period == .annual ? "FY\(label)" : label
    }

    /// Line 2 (owner decision 2026-11-13): the card row's OWN comparison — the value and the
    /// exact median the card printed (`currentValue` / `currentPeerMedian`), so it matches the
    /// card to the digit. Reads no period, series or line level, so it is the same on both
    /// tabs. nil on a report generated before 2026-11-13, which carries no median: such a
    /// report shows the period line only (the label is never parsed — it rounds the median
    /// and omits the ratios the card refused to print).
    private func currentLineText(_ m: DeepDiveMetric) -> String? {
        guard let median = m.currentPeerMedian else { return nil }
        let peer = currentPeerWord(m)
        let shown = m.currentValue.map { Self.format($0, unit: m.historyUnit) }
            ?? currentValueText(m.value)
        var text = "\(prefix(for: m)) \(shown) · \(peer) \(Self.format(median, unit: m.historyUnit))"
        if let value = m.currentValue, value > 0, median > 0 {
            // A ratio that rounds to "0.00×" says nothing (a 0.02% yield against 5.00%):
            // the card's own label leaves it out too, and the colour still carries the verdict.
            let ratio = String(format: "%.2f×", value / median)
            if ratio != "0.00×" {
                text += " · \(ratio) vs \(peer.lowercased())"
            }
        } else if m.currentValue == nil {
            text += " · N/A vs \(peer.lowercased())"
        }
        return text
    }

    /// The group of the median line 2 quotes: the row's own `peerLevel`, else the card's.
    private func currentPeerWord(_ m: DeepDiveMetric) -> String {
        (m.peerLevel ?? card.peerGroupLevel) == "industry" ? "Industry" : "Sector"
    }

    /// Line 2's colour is the card's own verdict on the row (owner: "Match the card"): the
    /// score it gave (>= 4 good, <= 2 bad) — never the bare side of the median, which would
    /// paint red a P/E the card calls in line. Neutral on an unrated card or an unscored
    /// row; muted when there is no current number to judge.
    private func currentLineColor(_ m: DeepDiveMetric) -> Color {
        guard m.currentValue != nil else { return AppColors.textMuted }
        guard card.starRating > 0, let score = m.currentScore else { return AppColors.textPrimary }
        if score >= 4 { return AppColors.gain }
        if score <= 2 { return AppColors.loss }
        return AppColors.textPrimary
    }

    /// "Industry" or "Sector": the group THIS metric's line (for the selected period) is
    /// drawn from — a line is one population and can differ from the card-wide word —
    /// falling back to the card's level on reports generated before 2026-10-07.
    private func peerWord(_ m: DeepDiveMetric) -> String {
        let own = period == .annual ? m.sectorAnnualLevel : m.sectorQuarterlyLevel
        return (own ?? card.peerGroupLevel) == "industry" ? "Industry" : "Sector"
    }

    private func deltaText(
        prefix: String, company: Double, sector: Double, unit: String?, peer: String
    ) -> String {
        let c = Self.format(company, unit: unit)
        let s = Self.format(sector, unit: unit)
        if sector > 0 && company > 0 {
            return "\(prefix) \(c) · \(peer) \(s) · \(String(format: "%.2f×", company / sector)) vs \(peer.lowercased())"
        }
        return "\(prefix) \(c) · \(peer) \(s)"
    }

    @ViewBuilder
    private func legendAndDelta(_ m: DeepDiveMetric) -> some View {
        let pair = sectorPair(m)
        VStack(alignment: .center, spacing: AppSpacing.xs) {
            HStack(spacing: AppSpacing.md) {
                if usesLineChart {
                    HStack(spacing: 5) {  // solid yellow company line swatch (matches the chart)
                        Capsule()
                            .fill(AppColors.growthYoYYellow)
                            .frame(width: 14, height: 3)
                        Text("Company")
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textSecondary)
                    }
                } else {
                    HStack(spacing: 6) {
                        RoundedRectangle(cornerRadius: 1)
                            .fill(AppColors.primaryBlue)
                            .frame(width: 11, height: 11)
                        Text("Company")
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
                if m.hasSector {
                    HStack(spacing: 5) {
                        HStack(spacing: 2) {  // dashed-line swatch
                            ForEach(0..<3, id: \.self) { _ in
                                Capsule().fill(AppColors.textSecondary).frame(width: 4, height: 2)
                            }
                        }
                        Text("\(peerWord(m)) Average")
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
            if let tz = MetricThresholdZones.forHistoryKey(m.historyKey) {
                // Absolute-threshold metric (Altman Z): show the zone verdict + the threshold
                // caption in place of an industry comparison. Only the card's own figure is
                // "Current"; with none, the newest charted period names its own zone.
                if let z = Double(m.value) {
                    Text("\(currentPrefix): \(m.value) · \(tz.zoneLabel(z))")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(tz.zoneColor(z))
                        .multilineTextAlignment(.center)
                } else if let lc = latestCompanyPoint(m), let z = lc.value {
                    Text("\(periodLabel(lc.period)) \(Self.format(z, unit: m.historyUnit)) · \(tz.zoneLabel(z))")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(tz.zoneColor(z))
                        .multilineTextAlignment(.center)
                }
                Text(tz.caption)
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
                    .multilineTextAlignment(.center)
            } else {
                // Line 1: a closed year or quarter against the SAME period's peer median
                // (`sectorPair`), named by its period — "Current" belongs to line 2 alone.
                // Each comparison stays on ONE row (owner, 2026-11-13: "i don't want to make new
                // line for the second line"): it shrinks a little before it would wrap, never
                // below 85% (14pt → 11.9pt, above the 11pt data floor).
                if let pair {
                    Text(deltaText(prefix: periodLabel(pair.period),
                                   company: pair.company, sector: pair.sector, unit: m.historyUnit,
                                   peer: peerWord(m)))
                        .font(AppTypography.bodySmall)
                        .foregroundColor(deltaColor(m))
                        .multilineTextAlignment(.center)
                        .lineLimit(1)
                        .minimumScaleFactor(Self.legendLineMinScale)
                        .allowsTightening(true)
                } else if let lc = latestCompanyPoint(m), let v = lc.value {
                    Text("\(periodLabel(lc.period)) \(Self.format(v, unit: m.historyUnit)) · Company only")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textMuted)
                        .multilineTextAlignment(.center)
                }
                // Line 2: today's figure against the median the card compared it with.
                if usesLineChart, let current = currentLineText(m) {
                    Text(current)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(currentLineColor(m))
                        .multilineTextAlignment(.center)
                        .lineLimit(1)
                        .minimumScaleFactor(Self.legendLineMinScale)
                        .allowsTightening(true)
                }
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

    /// What "TTM" means, on the Valuation sheet only (owner decision 2026-11-13): the header
    /// and line 2 are trailing-twelve-month multiples at the report's price, while every
    /// chart point is priced at its own period's end — the reason the two can sit far apart
    /// (ORCL: 18.21 now against 41.8x for FY2026, after the price fell by half). The owner's
    /// sentence names earnings (P/E); each multiple names its own denominator (review
    /// 2026-11-13), and P/B, whose "TTM" is the latest book value, says so.
    @ViewBuilder
    private func ttmNote(_ m: DeepDiveMetric) -> some View {
        if card.title == "Valuation" {
            HStack(alignment: .top, spacing: 5) {
                Image(systemName: "info.circle")
                    .font(AppTypography.labelSmall)
                Text(ttmNoteText(m))
                    .font(AppTypography.labelSmall)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .foregroundColor(AppColors.textMuted)
        }
    }

    private func ttmNoteText(_ m: DeepDiveMetric) -> String {
        let pricing = "at the price when the report was made. Chart points use each period's own price."
        // A row shown on a last-fiscal-year basis (the last 12 months were not available):
        // say which year it divides by, never "TTM".
        if let period = m.currentPeriod, !period.isEmpty {
            let base: String
            switch m.historyKey {
            case "pfcf":      base = "free cash flow"
            case "ev_ebitda": base = "EBITDA"
            default:          base = "results"
            }
            return "Uses \(period) \(base) (the last 12 months were not available), \(pricing)"
        }
        switch m.historyKey {
        case "pb":        return "For P/B, TTM means the latest book value, \(pricing)"
        case "ps":        return "TTM = the last 12 months of sales, \(pricing)"
        case "pfcf":      return "TTM = the last 12 months of free cash flow, \(pricing)"
        case "ev_ebitda": return "TTM = the last 12 months of EBITDA, \(pricing)"
        default:          return "TTM = the last 12 months of earnings, \(pricing)"
        }
    }

    /// A ratio can be undefined for a period (e.g. P/FCF when free cash flow
    /// is negative, P/E or Earnings Yield when earnings are negative). Those
    /// bars are dropped — so when the current value reads "Neg."/"—" or any
    /// historical period was dropped, say so instead of leaving a silent gap.
    @ViewBuilder
    private func omissionNote(_ m: DeepDiveMetric) -> some View {
        // Show ONLY when the currently-shown series actually has a gap (a "—" in
        // the chart). No gap → no note.
        let omitted = points(m).filter { $0.value == nil }.count
        if omitted > 0 {
            HStack(alignment: .top, spacing: 5) {
                Image(systemName: "info.circle")
                    .font(AppTypography.labelSmall)
                Text(usesLineChart
                     ? "A gap (—) in a line means the ratio was negative or undefined that period (e.g. negative free cash flow)."
                     : "A red mark at the 0 line means the ratio was negative or undefined that period (e.g. negative free cash flow).")
                    .font(AppTypography.labelSmall)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .foregroundColor(AppColors.textMuted)
        }
    }


    // MARK: - Formatting (shared with MetricHistoryChart's units)

    /// How far a legend comparison line may shrink to stay on one row.
    static let legendLineMinScale: CGFloat = 0.85

    /// Expand the compact "Neg." sentinel to "Negative" for the sheet header.
    /// The report card keeps "Neg." (it's space-constrained).
    private func currentValueText(_ value: String) -> String {
        value == "Neg." ? "Negative" : value
    }

    // 2-decimal precision so the legend's "Latest …" matches the header's
    // "Current: 65.20%" (which renders the backend's 2-decimal value string).
    static func format(_ v: Double, unit: String?) -> String {
        switch unit {
        case "percent": return String(format: "%.2f%%", v)
        case "score":   return String(format: "%.2f", v)
        default:        return String(format: "%.2fx", v)
        }
    }
}

// ORCL shape (fiscal year ends May): the industry line ends at its last complete year, so
// line 1 compares FY2025 with FY2025, and line 2 is today's TTM against the card's median.
#Preview("Valuation") {
    let years = ["2021", "2022", "2023", "2024", "2025", "2026"]
    let pe: [Double?] = [17.04, 28.88, 33.63, 30.76, 37.11, 41.78]
    let pfcf: [Double?] = [22.0, 25.4, 30.1, nil, nil, nil]
    let industryPE: [Double?] = [31.8, 26.8, 33.0, 29.7, 27.3, nil]
    let industryPFCF: [Double?] = [24.0, 23.5, 26.1, 28.0, 31.4, nil]
    let series: ([Double?]) -> [MetricHistoryPoint] = { values in
        zip(years, values).map { MetricHistoryPoint(period: $0, value: $1) }
    }
    let card = DeepDiveMetricCard(
        title: "Valuation",
        starRating: 4,
        metrics: [
            DeepDiveMetric(
                label: "P/E (0.62x sector avg 29.3)",
                value: "18.21",
                trend: nil,
                historyKey: "pe",
                historyUnit: "x",
                annualHistory: series(pe),
                sectorAnnualHistory: series(industryPE),
                sectorAnnualLevel: "industry",
                currentValue: 18.2068,
                currentPeerMedian: 29.3172,
                currentScore: 5,
                peerLevel: "industry"
            ),
            DeepDiveMetric(
                label: "P/FCF (sector avg 31.4)",
                value: "Neg.",
                trend: nil,
                historyKey: "pfcf",
                historyUnit: "x",
                annualHistory: series(pfcf),
                sectorAnnualHistory: series(industryPFCF),
                sectorAnnualLevel: "industry",
                currentPeerMedian: 31.4,
                currentScore: 3,
                peerLevel: "industry"
            ),
        ],
        qualityLabel: "Fairly priced",
        qualitySentiment: "neutral",
        peerGroupLevel: "industry"
    )
    FundamentalsHistorySheet(card: card)
}

// An unrated Health card: no verdict colour on either line; Altman Z keeps its zone line.
#Preview("Health, unrated") {
    let years = ["2021", "2022", "2023", "2024", "2025"]
    let de = years.enumerated().map { MetricHistoryPoint(period: $1, value: 1.1 + Double($0) * 0.1) }
    let peerDE = years.map { MetricHistoryPoint(period: $0, value: 0.9) }
    let z = years.enumerated().map { MetricHistoryPoint(period: $1, value: 2.4 + Double($0) * 0.2) }
    let card = DeepDiveMetricCard(
        title: "Health",
        starRating: 0,
        metrics: [
            DeepDiveMetric(
                label: "Debt-to-Equity (vs sector 0.90)",
                value: "1.52",
                trend: nil,
                historyKey: "debt_to_equity",
                historyUnit: "x",
                annualHistory: de,
                sectorAnnualHistory: peerDE,
                currentValue: 1.52,
                currentPeerMedian: 0.9,
                currentScore: 2
            ),
            DeepDiveMetric(
                label: "Altman Z-Score",
                value: "3.25",
                trend: nil,
                historyKey: "altman_z",
                historyUnit: "score",
                annualHistory: z,
                currentValue: 3.25
            ),
        ],
        qualityLabel: "Data unavailable",
        qualitySentiment: "neutral",
        peerGroupLevel: "sector"
    )
    FundamentalsHistorySheet(card: card)
}
