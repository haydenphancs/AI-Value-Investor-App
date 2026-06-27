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
    private func isCurrentlyGood(_ m: DeepDiveMetric) -> Bool? {
        guard let hib = DeepDiveMetric.higherIsBetter(forHistoryKey: m.historyKey),
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
                higherIsBetter: DeepDiveMetric.higherIsBetter(forHistoryKey: m.historyKey)
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
                        omissionNote(m)
                        footnote
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
                            .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
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

    private func header(_ m: DeepDiveMetric) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(m.historyTitle)
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
            Text("Current: \(currentValueText(m.value))")
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

    /// "Current" when `period` is the newest charted period, else the period label
    /// itself — so a metric whose latest periods are dropped (e.g. P/FCF when free
    /// cash flow turns negative) reads "2024 …" instead of falsely "Current …".
    /// The header already shows the true current value ("Negative"); this keeps the
    /// legend figure from contradicting it.
    private func periodPrefix(_ period: String, _ m: DeepDiveMetric) -> String {
        period == points(m).last?.period ? "Current" : period
    }

    /// The current value is negative/undefined (the card shows "Neg."/"N/A"/"—") —
    /// e.g. P/FCF when free cash flow is negative. Mirrors omissionNote's check.
    private func currentIsUndefined(_ m: DeepDiveMetric) -> Bool {
        let v = m.value.lowercased()
        return v.contains("neg") || v.contains("n/a") || m.value == "—"
    }

    /// The most recent industry/sector value (the "current" benchmark), regardless
    /// of whether the company has a value that period.
    private func currentIndustry(_ m: DeepDiveMetric) -> Double? {
        sectorPoints(m).last(where: { $0.value != nil })?.value
    }

    /// Delta line when the CURRENT value is undefined: stay "all about the current" —
    /// echo the header's state, show the current industry, and N/A for the multiple
    /// (no meaningful ratio against a negative/undefined value).
    private func undefinedCurrentText(_ m: DeepDiveMetric) -> String {
        let state = currentValueText(m.value)
        guard let ind = currentIndustry(m) else { return "Current: \(state)" }
        return "Current: \(state) · \(peerWord) \(Self.format(ind, unit: m.historyUnit)) · N/A vs \(peerWord.lowercased())"
    }

    /// "Industry" when the card's benchmark comparison is industry-level, else "Sector".
    private var peerWord: String {
        card.peerGroupLevel == "industry" ? "Industry" : "Sector"
    }

    private func deltaText(prefix: String, company: Double, sector: Double, unit: String?) -> String {
        let c = Self.format(company, unit: unit)
        let s = Self.format(sector, unit: unit)
        let peer = peerWord
        if sector > 0 && company > 0 {
            return "\(prefix) \(c) · \(peer) \(s) · \(String(format: "%.2f×", company / sector)) vs \(peer.lowercased())"
        }
        return "\(prefix) \(c) · \(peer) \(s)"
    }

    /// The "Green = better … red = worse" hint — shown whenever the line chart's
    /// good/bad band is drawn (a known-polarity metric).
    @ViewBuilder
    private func bandCaption(_ m: DeepDiveMetric) -> some View {
        if usesLineChart, DeepDiveMetric.higherIsBetter(forHistoryKey: m.historyKey) != nil {
            Text("Green = better than \(peerWord.lowercased()), red = worse.")
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
        }
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
                        Text("\(peerWord) Average")
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
            if currentIsUndefined(m) {
                // The CURRENT ratio is negative/undefined (header shows "Negative"),
                // so there's no like-for-like comparison: mirror the current state,
                // show the current industry value, and N/A for the multiple — instead
                // of quoting a stale older period the user would misread as "now".
                Text(undefinedCurrentText(m))
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textMuted)
                    .multilineTextAlignment(.center)
                bandCaption(m)
            } else if let pair {
                Text(deltaText(prefix: periodPrefix(pair.period, m),
                               company: pair.company, sector: pair.sector, unit: m.historyUnit))
                    .font(AppTypography.bodySmall)
                    .foregroundColor(deltaColor(m))
                    .multilineTextAlignment(.center)
                bandCaption(m)
            } else if let lc = latestCompanyPoint(m), let v = lc.value {
                Text("\(periodPrefix(lc.period, m)) \(Self.format(v, unit: m.historyUnit)) · Company only")
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

    /// A ratio can be undefined for a period (e.g. P/FCF when free cash flow
    /// is negative, P/E or Earnings Yield when earnings are negative). Those
    /// bars are dropped — so when the current value reads "Neg."/"—" or any
    /// historical period was dropped, say so instead of leaving a silent gap.
    @ViewBuilder
    private func omissionNote(_ m: DeepDiveMetric) -> some View {
        let omitted = points(m).filter { $0.value == nil }.count
        let v = m.value.lowercased()
        let nonNumeric = v.contains("neg") || v.contains("n/a") || m.value == "—"
        if omitted > 0 || nonNumeric {
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

    private var footnote: some View {
        Text(period == .annual
             ? "Annual figures by fiscal year."
             : "Quarterly figures; growth shown year-over-year.")
            .font(AppTypography.labelSmall)
            .foregroundColor(AppColors.textMuted)
    }

    // MARK: - Formatting (shared with MetricHistoryChart's units)

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

#Preview {
    let annual = (2015...2024).map {
        MetricHistoryPoint(period: String($0), value: 30 + Double($0 - 2015) * 1.5)
    }
    let sectorAnnual = (2015...2024).map {
        MetricHistoryPoint(period: String($0), value: 22 + Double($0 - 2015) * 0.4)
    }
    let card = DeepDiveMetricCard(
        title: "Profitability",
        starRating: 4,
        metrics: [
            DeepDiveMetric(
                label: "Gross Margin (1.2x sector avg 38%)",
                value: "46.9%",
                trend: nil,
                historyKey: "gross_margin",
                historyUnit: "percent",
                annualHistory: annual,
                quarterlyHistory: nil,
                sectorAnnualHistory: sectorAnnual,
                sectorQuarterlyHistory: nil
            ),
            DeepDiveMetric(
                label: "Net Margin",
                value: "26.9%",
                trend: nil,
                historyKey: "net_margin",
                historyUnit: "percent",
                annualHistory: annual.map {
                    MetricHistoryPoint(period: $0.period, value: ($0.value ?? 0) * 0.6)
                },
                quarterlyHistory: nil
            ),
        ],
        qualityLabel: "A Cash Machine",
        qualitySentiment: "positive"
    )
    FundamentalsHistorySheet(card: card)
        .preferredColorScheme(.dark)
}
