//
//  GrowthChartSheet.swift
//  ios
//
//  Molecule: the tap-to-expand drill-down opened from the Growth card in the
//  Fundamentals & Growth module. Shows the rich Growth chart (absolute-value
//  bars + YoY% line + dashed sector line) for one of the 5 growth metrics
//  (EPS / Revenue / Net Income / Operating Profit / Free Cash Flow), Annual or
//  Quarterly.
//
//  Deliberately mirrors FundamentalsHistorySheet's chrome 1:1 — same metric
//  chip picker, "<metric> / Current:" header, segmented Annual/Quarterly toggle,
//  sheet background, and the "Company / Sector Average · Latest … vs sector"
//  legend card — so Growth reads identically to Profitability / Valuation /
//  Health. Renders chart data baked into the frozen report; no network call.
//

import SwiftUI

struct GrowthChartSheet: View {
    let card: DeepDiveMetricCard       // nav title ("Growth")
    let growthData: GrowthSectionData  // the rich chart data (frozen in the report)

    @Environment(\.dismiss) private var dismiss
    @State private var selectedMetric: GrowthMetricType
    @State private var selectedPeriod: GrowthPeriodType

    init(card: DeepDiveMetricCard, growthData: GrowthSectionData) {
        self.card = card
        self.growthData = growthData
        // Open on the first metric that actually has data, on Annual when present.
        let avail = GrowthMetricType.allCases.filter {
            growthData.dataPoints(for: $0, period: .annual).count >= 2
                || growthData.dataPoints(for: $0, period: .quarterly).count >= 2
        }
        let first = avail.first ?? .eps
        _selectedMetric = State(initialValue: first)
        let hasAnnual = growthData.dataPoints(for: first, period: .annual).count >= 2
        _selectedPeriod = State(initialValue: hasAnnual ? .annual : .quarterly)
    }

    /// Metrics that have a chartable series in either granularity (no empty chips).
    private var availableMetrics: [GrowthMetricType] {
        GrowthMetricType.allCases.filter {
            growthData.dataPoints(for: $0, period: .annual).count >= 2
                || growthData.dataPoints(for: $0, period: .quarterly).count >= 2
        }
    }

    private var current: [GrowthDataPoint] {
        growthData.dataPoints(for: selectedMetric, period: selectedPeriod)
    }

    private func quarterlyAvailable(_ m: GrowthMetricType) -> Bool {
        growthData.dataPoints(for: m, period: .quarterly).count >= 2
    }

    /// The genuinely-latest charted period (newest bar), regardless of whether
    /// its YoY is meaningful. Header/legend anchor here — never silently
    /// backfilling an OLDER period's positive % when the latest period is a loss.
    private var latestPoint: GrowthDataPoint? { current.last }

    /// YoY of the LATEST period only (nil when n/m) — no backward scan.
    private var latestYoY: Double? { current.last?.yoyChangePercent }

    /// Last period that DID have a meaningful YoY — shown only as an explicitly
    /// labelled "last meaningful" secondary line, never asserted as "Current".
    private var lastMeaningful: GrowthDataPoint? {
        current.last(where: { $0.yoyChangePercent != nil })
    }

    /// Same-period company-vs-sector pair, ONLY for the latest period when it
    /// itself has both — so the legend never quotes a stale ratio from an older
    /// period than the one the header reports.
    private var sectorPair: (company: Double, sector: Double)? {
        guard let p = latestPoint,
              let c = p.yoyChangePercent, let s = p.sectorAverageYoY else { return nil }
        return (c, s)
    }

    /// A charted period has a non-meaningful (nil) YoY → the line breaks / the
    /// YoY row shows "—". Drives the explanatory footnote.
    private var hasNonMeaningfulYoY: Bool {
        current.contains { $0.yoyChangePercent == nil }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    metricPicker
                    header
                    if quarterlyAvailable(selectedMetric) { periodToggle }
                    GrowthChartView(dataPoints: current)
                        .id("\(selectedMetric.rawValue)-\(selectedPeriod.rawValue)")
                        .animation(.easeInOut(duration: 0.25), value: selectedMetric)
                        .animation(.easeInOut(duration: 0.25), value: selectedPeriod)
                    legendAndDelta
                    footnote
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
                        Text(m.rawValue)
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
        // edge (insets the scroll CONTENT, not the viewport).
        .contentMargins(.horizontal, AppSpacing.lg, for: .scrollContent)
    }

    // MARK: - Header (current growth)

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(selectedMetric.rawValue)
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
            Text("Current: \(currentText)")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
            // When the latest period's growth is n/m (loss / sign flip), surface
            // the last period that DID have a meaningful growth — explicitly
            // labelled, so it never masquerades as "Current".
            if latestPoint?.yoyChangePercent == nil,
               let lm = lastMeaningful, let y = lm.yoyChangePercent {
                Text("Last meaningful growth: \(pct(y)) (\(lm.period))")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    private var currentText: String {
        guard let p = latestPoint else { return "—" }
        if let y = p.yoyChangePercent { return pct(y) }
        // Latest period is n/m: show its actual value + period, never a stale %.
        return "\(p.formattedValue) (\(p.period)) · YoY n/m"
    }

    // MARK: - Period toggle (segmented)

    private var periodToggle: some View {
        Picker("Period", selection: $selectedPeriod) {
            ForEach(GrowthPeriodType.allCases) { p in
                Text(p.rawValue).tag(p)
            }
        }
        .pickerStyle(.segmented)
    }

    // MARK: - Legend + latest vs-sector delta (YoY growth)

    /// Compact, correct % — drops decimals when large (a sign-flip YoY can run to
    /// thousands of %); keeps 2 decimals for normal magnitudes.
    private func pct(_ v: Double) -> String {
        abs(v) >= 100 ? String(format: "%.0f%%", v) : String(format: "%.2f%%", v)
    }

    private func deltaText(company: Double, sector: Double) -> String {
        let c = pct(company)
        let s = pct(sector)
        // Percentage-point spread is meaningful at any sign/magnitude.
        let spread = String(format: "%+.1f pts vs sector", company - sector)
        // The "×" multiple only means anything when the sector base is non-trivial
        // — a tiny-positive denominator (e.g. 0.5%) explodes into an absurd ratio.
        if company > 0 && sector >= 2.0 {
            return "Latest \(c) · Sector \(s) · \(String(format: "%.2f×", company / sector)) (\(spread))"
        }
        return "Latest \(c) · Sector \(s) · \(spread)"
    }

    @ViewBuilder
    private var legendAndDelta: some View {
        VStack(alignment: .center, spacing: AppSpacing.xs) {
            HStack(spacing: AppSpacing.md) {
                HStack(spacing: 6) {
                    RoundedRectangle(cornerRadius: 1)
                        .fill(AppColors.growthBarBlue)
                        .frame(width: 11, height: 11)
                    Text("Company")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textSecondary)
                }
                HStack(spacing: 5) {
                    HStack(spacing: 2) {  // dashed-line swatch
                        ForEach(0..<3, id: \.self) { _ in
                            Capsule().fill(AppColors.growthSectorGray).frame(width: 4, height: 2)
                        }
                    }
                    Text("Sector Average")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textSecondary)
                }
            }
            if let pair = sectorPair {
                // Latest period has BOTH company and sector YoY.
                Text(deltaText(company: pair.company, sector: pair.sector))
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textPrimary)
                    .multilineTextAlignment(.center)
            } else if let y = latestYoY {
                // Latest has a company YoY but no sector benchmark.
                Text("Latest \(pct(y)) · Company only")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textMuted)
                    .multilineTextAlignment(.center)
            } else if let lm = lastMeaningful, let y = lm.yoyChangePercent {
                // Latest is n/m, but an earlier period had meaningful growth.
                Text("Latest period n/m · last meaningful \(pct(y)) (\(lm.period))")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textMuted)
                    .multilineTextAlignment(.center)
            } else if let p = latestPoint {
                // No meaningful YoY anywhere (chronic loss-maker): an explicit,
                // honest message instead of an empty labelled box.
                Text("Latest \(p.formattedValue) (\(p.period)) · growth % not meaningful")
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

    private var footnote: some View {
        let base = selectedPeriod == .annual
            ? "Annual figures by fiscal year. The oldest year is the YoY baseline (not charted)."
            : "Quarterly figures; growth shown year-over-year. The oldest reported year is the YoY baseline (not charted)."
        // Explain the "—" so intact negative-period data doesn't read as missing.
        let nm = hasNonMeaningfulYoY
            ? " “—” marks a period where a growth % isn’t meaningful (a loss or sign change); the bar still shows the actual value."
            : ""
        return Text(base + nm)
            .font(AppTypography.labelSmall)
            .foregroundColor(AppColors.textMuted)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

#Preview {
    GrowthChartSheet(
        card: TickerReportData.sampleOracle.fundamentalMetrics.first { $0.title == "Growth" }
            ?? TickerReportData.sampleOracle.fundamentalMetrics[0],
        growthData: GrowthSectionData.sampleData
    )
    .preferredColorScheme(.dark)
}
