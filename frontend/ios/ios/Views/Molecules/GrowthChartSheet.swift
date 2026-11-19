//
//  GrowthChartSheet.swift
//  ios
//
//  Molecule: the tap-to-expand drill-down opened from the Growth card in the
//  Fundamentals & Growth module. Shows the rich Growth chart (absolute-value
//  bars + YoY% line + dashed peer line) for one of the 5 growth metrics
//  (EPS / Revenue / Net Income / Operating Profit / Free Cash Flow), Annual or
//  Quarterly.
//
//  Mirrors FundamentalsHistorySheet's chrome — same metric chip picker, header,
//  segmented Annual/Quarterly toggle, sheet background and legend card — so Growth
//  reads like Profitability / Valuation / Health. Owner decision 2026-11-13 ("Name the
//  periods"): every figure on this sheet is a CLOSED year or quarter, so nothing here
//  says "Current" — the header is the newest bar named by its period ("FY2026: 34.33%",
//  "Q1 '27: 54.46%"), legend line 1 is the newest period with both a company and a
//  same-period peer YoY, and line 2 is the card's own figure named by its fiscal year.
//  Renders chart data baked into the frozen report; no network call.
//

import SwiftUI

struct GrowthChartSheet: View {
    let card: DeepDiveMetricCard       // nav title ("Growth") + the card rows (line 2)
    let growthData: GrowthSectionData  // the rich chart data (frozen in the report)

    @Environment(\.dismiss) private var dismiss
    @State private var selectedMetric: GrowthMetricType
    @State private var selectedPeriod: GrowthPeriodType

    init(card: DeepDiveMetricCard, growthData: GrowthSectionData) {
        self.card = card
        self.growthData = growthData
        // Open on the first metric that actually has data, on Annual when present —
        // the SAME helper the Financials-tab GrowthSectionCard uses (this sheet keeps
        // its stricter two-point minimum).
        let start = growthData.initialSelection(minimum: Self.minimumPoints)
        _selectedMetric = State(initialValue: start.metric)
        _selectedPeriod = State(initialValue: start.period)
    }

    /// A series needs two points to show a trend in this drill-down.
    private static let minimumPoints = 2

    /// How far a legend comparison line may shrink to stay on one row (bodySmall 14pt →
    /// 11.9pt, above the 11pt data floor) — the same limit as FundamentalsHistorySheet.
    static let legendLineMinScale: CGFloat = 0.85

    /// Metrics that have a chartable series in either granularity (no empty chips).
    private var availableMetrics: [GrowthMetricType] {
        growthData.metricsWithData(minimum: Self.minimumPoints)
    }

    /// The selected tab's series, oldest → newest.
    private var series: [GrowthDataPoint] {
        growthData.dataPoints(for: selectedMetric, period: selectedPeriod)
    }

    private func quarterlyAvailable(_ m: GrowthMetricType) -> Bool {
        growthData.hasPoints(m, .quarterly, minimum: Self.minimumPoints)
    }

    /// The genuinely-latest charted period (newest bar), regardless of whether
    /// its YoY is meaningful. The header anchors here — never silently
    /// backfilling an OLDER period's positive % when the latest period is a loss.
    private var latestPoint: GrowthDataPoint? { series.last }

    /// Last period that DID have a meaningful YoY — shown only as an explicitly
    /// labelled "last meaningful" line, always named by its own period.
    private var lastMeaningful: GrowthDataPoint? {
        series.last(where: { $0.yoyChangePercent != nil })
    }

    /// Line 1's pair: walks back from the newest bar to the newest point that has BOTH a
    /// company YoY and a peer YoY. One point is one join period, so the two figures always
    /// describe the same year or quarter — and the line is named by that period, so it
    /// never borrows the newer bar's name when the newest peer figure is not out yet.
    private var sectorPair: (company: Double, sector: Double, period: String)? {
        for p in series.reversed() {
            guard let c = p.yoyChangePercent, let s = p.sectorAverageYoY,
                  c.isFinite, s.isFinite else { continue }
            return (c, s, p.period)
        }
        return nil
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    metricPicker
                    header
                    if quarterlyAvailable(selectedMetric) { periodToggle }
                    GrowthChartView(dataPoints: series)
                        .id("\(selectedMetric.rawValue)-\(selectedPeriod.rawValue)")
                        .animation(.easeInOut(duration: 0.25), value: selectedMetric)
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

    // MARK: - Metric picker (chips) — same style as FundamentalsHistorySheet

    private var metricPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: AppSpacing.sm) {
                ForEach(availableMetrics) { m in
                    let isSelected = m == selectedMetric
                    Button {
                        selectedMetric = m
                        // Keep the toggle on a granularity this metric has.
                        if selectedPeriod == .quarterly && !quarterlyAvailable(m) {
                            selectedPeriod = .annual
                        }
                    } label: {
                        Text(m.displayName)
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
        // edge (insets the scroll CONTENT, not the viewport).
        .contentMargins(.horizontal, AppSpacing.lg, for: .scrollContent)
    }

    // MARK: - Header (the newest bar, named by its period)

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(selectedMetric.displayName)
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
            Text(headerText)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
            // When the latest period's growth is n/m (zero base / missing value), surface
            // the last period that DID have a meaningful growth — named by its own period,
            // so it never reads as the newest bar's figure.
            if latestPoint?.yoyChangePercent == nil,
               let lm = lastMeaningful, let y = lm.yoyChangePercent {
                Text("Last meaningful growth: \(pct(y)) (\(periodLabel(lm.period)))")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    /// "FY2026: 34.33%" (Annual) / "Q1 '27: 54.46%" (Quarterly): the newest bar's YoY,
    /// named by its period. An n/m newest bar shows its value instead, never a stale %.
    private var headerText: String {
        guard let p = latestPoint else { return "—" }
        let name: String = periodLabel(p.period)
        if let y = p.yoyChangePercent { return "\(name): \(pct(y))" }
        return "\(name): \(p.formattedValue) · YoY n/m"
    }

    /// A closed period's name: "FY2026" on Annual (the axis reads "2026"; the "FY" says it
    /// is the company's fiscal year, which for ORCL ends in May), the quarter label
    /// ("Q1 '27") on Quarterly. A label that already says "FY" is not prefixed twice.
    private func periodLabel(_ label: String) -> String {
        guard selectedPeriod == .annual, !label.hasPrefix("FY") else { return label }
        return "FY\(label)"
    }

    // MARK: - Period toggle (segmented)

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
    /// `.id`-keyed view. (Metric switches still animate via `.animation(value:)`.)
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

    // MARK: - Legend + the two comparison lines (YoY growth)

    /// A growth % for this sheet's text, always to 2 decimals — the exact figure, however
    /// large (ORCL FCF -5911.70%: owner, "Keep the exact figure"). Rounded to the cent
    /// first so a tiny negative prints "0.00%", never "-0.00%"; "—" for a non-finite value.
    /// The chart's own axis/point labels are GrowthChartView's and are unchanged.
    private func pct(_ v: Double) -> String {
        guard v.isFinite else { return "—" }
        let cents: Double = (v * 100).rounded() / 100
        let shown: Double = cents.isFinite ? (cents == 0 ? 0 : cents) : v
        return String(format: "%.2f%%", shown)
    }

    /// "Industry" when THIS series' benchmark line is industry-level, else "Sector".
    /// The level is per series (the lookup falls back industry → sector per cell); the
    /// card's single ticker-wide level is only the fallback for a report frozen before
    /// `peer_group_levels` existed.
    private var peerWord: String {
        growthData.peerWord(for: selectedMetric, period: selectedPeriod, legacyLevel: card.peerGroupLevel)
    }

    /// The series draws a dashed peer line (it has at least one benchmark value).
    private var showsPeerLine: Bool {
        series.contains { $0.sectorAverageYoY != nil }
    }

    /// "<name> <c> · <Peer> <s> · <ratio>× vs <peer>". The "×" multiple only when the
    /// company grew and the peer base is non-trivial (>= 2%: a 0.5% base explodes into an
    /// absurd multiple) and it does not round to "0.00×"; otherwise the percentage-point
    /// spread, which is meaningful at any sign or size.
    private func deltaText(name: String, company: Double, sector: Double, peer: String) -> String {
        let peerLower: String = peer.lowercased()
        let head: String = "\(name) \(pct(company)) · \(peer) \(pct(sector))"
        if company > 0 && sector >= 2.0 {
            let ratio: String = String(format: "%.2f×", company / sector)
            if ratio != "0.00×" {
                return "\(head) · \(ratio) vs \(peerLower)"
            }
        }
        let spread: String = String(format: "%+.1f", company - sector)
        return "\(head) · \(spread) pts vs \(peerLower)"
    }

    /// Line 1's colour: which side of the SAME period's peer growth the company is on
    /// (higher growth is better) — only on a rated card; an unrated card (starRating 0)
    /// paints no verdict, as on the card itself.
    private func pairColor(company: Double, sector: Double) -> Color {
        guard card.starRating > 0 else { return AppColors.textPrimary }
        let d: Double = company - sector
        if d > 0 { return AppColors.gain }
        if d < 0 { return AppColors.loss }
        return AppColors.textPrimary
    }

    /// Line 1 when no point has both figures — still named by period, muted.
    private var companyOnlyText: String? {
        guard let p = latestPoint else { return nil }
        let name: String = periodLabel(p.period)
        if let y = p.yoyChangePercent {
            return "\(name) \(pct(y)) · Company only"
        }
        if let lm = lastMeaningful, let y = lm.yoyChangePercent {
            return "\(name) n/m · last meaningful \(pct(y)) (\(periodLabel(lm.period)))"
        }
        return "\(name) \(p.formattedValue) · growth % not meaningful"
    }

    /// The card row's `history_key` for a chart metric. Net Income has no card row.
    static func cardHistoryKey(for metric: GrowthMetricType) -> String? {
        switch metric {
        case .eps: return "eps_growth"
        case .revenue: return "revenue_growth"
        case .operatingProfit: return "operating_income_growth"
        case .freeCashFlow: return "fcf_growth"
        case .netIncome: return nil
        }
    }

    /// The Growth card row the selected chart metric came from (line 2's source).
    private var cardRow: DeepDiveMetric? {
        guard let key = Self.cardHistoryKey(for: selectedMetric) else { return nil }
        return card.metrics.first { $0.historyKey == key }
    }

    /// Line 2 (owner decision 2026-11-13): the card's OWN figure, named by the fiscal year
    /// it covers ("FY2026 34.33% · Industry 25.00% · 1.37× vs industry"), or — when that
    /// year's peer figure is not published yet — "FY2026 34.33% · Industry not out yet".
    /// Drawn only when the row carries both its value and its period (a report generated
    /// before 2026-11-13 has neither: period line only). Reads no tab state, so it is the
    /// same on Annual and Quarterly.
    private var cardLineText: String? {
        guard let row = cardRow, let value = row.currentValue, value.isFinite,
              let period = row.currentPeriod, !period.isEmpty else { return nil }
        let peer: String = cardPeerWord(row)
        if let median = row.currentPeerMedian, median.isFinite {
            return deltaText(name: period, company: value, sector: median, peer: peer)
        }
        // "Not out yet" only for a company that HAS a peer line (some fiscal year carries a
        // peer figure). A company compared with no peer group at all (a listed non-lender
        // member, a Financial Services industry under 10 companies) has no figure coming.
        guard annualSeriesHasPeer else { return "\(period) \(pct(value))" }
        return "\(period) \(pct(value)) · \(peer) not out yet"
    }

    /// Whether the selected metric's ANNUAL series carries a peer figure for any year.
    private var annualSeriesHasPeer: Bool {
        growthData.dataPoints(for: selectedMetric, period: .annual)
            .contains { $0.sectorAverageYoY != nil }
    }

    /// Line 1 when it would not repeat line 2: on the Annual tab, once the card's own
    /// fiscal year has its peer figure, both lines would name the same year — draw only
    /// line 2 (the card's verdict colour).
    private var periodPair: (company: Double, sector: Double, period: String)? {
        guard let pair = sectorPair else { return nil }
        if cardLineText != nil, let cardPeriod = cardRow?.currentPeriod,
           periodLabel(pair.period) == cardPeriod {
            return nil
        }
        return pair
    }

    /// The group line 2's median comes from: the row's own `peerLevel`, else the group the
    /// metric's ANNUAL peer line is drawn from (the card's figure is a fiscal year), else
    /// the card's ticker-wide level.
    private func cardPeerWord(_ row: DeepDiveMetric) -> String {
        let level: String? = row.peerLevel
            ?? growthData.peerGroupLevel(for: selectedMetric, period: .annual)
            ?? card.peerGroupLevel
        return level == "industry" ? "Industry" : "Sector"
    }

    /// Line 2's colour is the card's own verdict on the row (the score it gave: >= 4 good,
    /// <= 2 bad), never the bare side of the median. Neutral on an unrated card, an
    /// unscored row, or a score outside the card's 1-5 scale.
    private func cardLineColor(_ row: DeepDiveMetric) -> Color {
        guard card.starRating > 0, let score = row.currentScore,
              (1...5).contains(score) else { return AppColors.textPrimary }
        if score >= 4 { return AppColors.gain }
        if score <= 2 { return AppColors.loss }
        return AppColors.textPrimary
    }

    /// One comparison line: ONE row (owner, 2026-11-13: no new line for the second line),
    /// shrinking a little before it would wrap, never below `legendLineMinScale`.
    private func legendLine(_ text: String, color: Color) -> some View {
        Text(text)
            .font(AppTypography.bodySmall)
            .foregroundColor(color)
            .multilineTextAlignment(.center)
            .lineLimit(1)
            .minimumScaleFactor(Self.legendLineMinScale)
            .allowsTightening(true)
    }

    @ViewBuilder
    private var legendAndDelta: some View {
        VStack(alignment: .center, spacing: AppSpacing.xs) {
            HStack(spacing: AppSpacing.md) {
                HStack(spacing: 6) {
                    RoundedRectangle(cornerRadius: 1)
                        .fill(AppColors.growthBarBlue)
                        .frame(width: 11, height: 11)
                    Text("Value")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textSecondary)
                }
                HStack(spacing: 5) {  // solid yellow YoY line swatch
                    Capsule()
                        .fill(AppColors.growthYoYYellow)
                        .frame(width: 14, height: 3)
                    Text("YoY")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textSecondary)
                }
                // Only when the series draws the dashed line (a series with no peer
                // benchmark has none) — never name a line that is not there.
                if showsPeerLine {
                    HStack(spacing: 5) {
                        HStack(spacing: 2) {  // dashed-line swatch
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
            // Line 1: the newest period with BOTH a company and a same-period peer YoY,
            // unless it is the card's own year (line 2 says it).
            if let pair = periodPair {
                legendLine(
                    deltaText(name: periodLabel(pair.period), company: pair.company,
                              sector: pair.sector, peer: peerWord),
                    color: pairColor(company: pair.company, sector: pair.sector)
                )
            } else if sectorPair == nil, let text = companyOnlyText {
                legendLine(text, color: AppColors.textMuted)
            }
            // Line 2: the card's own figure, named by its fiscal year.
            if let row = cardRow, let text = cardLineText {
                legendLine(text, color: cardLineColor(row))
            }
        }
        .padding(.vertical, AppSpacing.sm)
        // `sm`, not `md`: the comparison lines use more of the card's width (owner,
        // 2026-11-13), as on FundamentalsHistorySheet.
        .padding(.horizontal, AppSpacing.sm)
        .frame(maxWidth: .infinity, alignment: .center)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }
}

// ORCL shape (fiscal year ends May): the industry figure for FY2026 is not out yet, so line 1
// walks back to FY2025 (Q4 '26 on Quarterly), and line 2 is the card's own FY2026 rate.
#Preview("ORCL, industry not out yet") {
    let annual: [GrowthDataPoint] = [
        GrowthDataPoint(period: "2022", value: 2.41, yoyChangePercent: -46.89, sectorAverageYoY: 9.10),
        GrowthDataPoint(period: "2023", value: 3.07, yoyChangePercent: 27.39, sectorAverageYoY: 11.40),
        GrowthDataPoint(period: "2024", value: 3.71, yoyChangePercent: 20.85, sectorAverageYoY: 14.20),
        GrowthDataPoint(period: "2025", value: 4.34, yoyChangePercent: 16.98, sectorAverageYoY: 18.75),
        GrowthDataPoint(period: "2026", value: 5.83, yoyChangePercent: 34.33, sectorAverageYoY: nil),
    ]
    let quarterly: [GrowthDataPoint] = [
        GrowthDataPoint(period: "Q2 '26", value: 1.10, yoyChangePercent: 52.78, sectorAverageYoY: 22.10),
        GrowthDataPoint(period: "Q3 '26", value: 1.21, yoyChangePercent: 30.11, sectorAverageYoY: 24.60),
        GrowthDataPoint(period: "Q4 '26", value: 1.95, yoyChangePercent: 21.85, sectorAverageYoY: 27.50),
        GrowthDataPoint(period: "Q1 '27", value: 2.31, yoyChangePercent: 54.46, sectorAverageYoY: nil),
    ]
    let data = GrowthSectionData(
        epsAnnual: annual, epsQuarterly: quarterly,
        revenueAnnual: [], revenueQuarterly: [],
        netIncomeAnnual: [], netIncomeQuarterly: [],
        operatingProfitAnnual: [], operatingProfitQuarterly: [],
        freeCashFlowAnnual: [], freeCashFlowQuarterly: [],
        peerGroupLevels: ["eps_annual": "industry", "eps_quarterly": "industry"]
    )
    let card = DeepDiveMetricCard(
        title: "Growth",
        starRating: 4,
        metrics: [
            DeepDiveMetric(
                label: "EPS Growth",
                value: "34.33%",
                trend: nil,
                historyKey: "eps_growth",
                historyUnit: "percent",
                annualHistory: [
                    MetricHistoryPoint(period: "2025", value: 16.98),
                    MetricHistoryPoint(period: "2026", value: 34.33),
                ],
                currentValue: 34.33,
                currentScore: 5,
                currentPeriod: "FY2026"
            ),
        ],
        qualityLabel: "Strong Growth",
        qualitySentiment: "positive",
        peerGroupLevel: "industry"
    )
    GrowthChartSheet(card: card, growthData: data)
}
