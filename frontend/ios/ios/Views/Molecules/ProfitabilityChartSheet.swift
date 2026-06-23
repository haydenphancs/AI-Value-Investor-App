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
        if let roe = card.metrics.first(where: { $0.historyKey == "roe" }) {
            series.append(roe.toProfitabilitySeries(.roe))
        }
        if let roa = card.metrics.first(where: { $0.historyKey == "roa" }) {
            series.append(roa.toProfitabilitySeries(.roa))
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

    private var current: [ProfitabilityChartPoint] {
        series(selectedMetric)?.points(for: selectedPeriod) ?? []
    }

    private func quarterlyAvailable(_ m: ProfitabilityMetricType) -> Bool {
        (series(m)?.quarterly.filter { $0.company != nil }.count ?? 0) >= 2
    }

    /// Latest charted period with a real company value (header/legend anchor here).
    private var latestPoint: ProfitabilityChartPoint? {
        current.last(where: { $0.company != nil })
    }

    /// Same-period company-vs-sector pair, only for the latest period where BOTH
    /// exist — so the delta never quotes a stale ratio from an older period.
    private var sectorPair: (company: Double, sector: Double)? {
        guard let p = current.last(where: { $0.company != nil && $0.sector != nil }),
              let c = p.company, let s = p.sector else { return nil }
        return (c, s)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    metricPicker
                    header
                    if quarterlyAvailable(selectedMetric) { periodToggle }
                    ProfitabilityChartView(points: current)
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
        }
    }

    private var currentText: String {
        guard let v = latestPoint?.company else { return "—" }
        return pct(v)
    }

    // MARK: - Period toggle

    private var periodToggle: some View {
        Picker("Period", selection: $selectedPeriod) {
            ForEach(GrowthPeriodType.allCases) { p in
                Text(p.rawValue).tag(p)
            }
        }
        .pickerStyle(.segmented)
    }

    // MARK: - Legend + latest vs-sector delta

    private func pct(_ v: Double) -> String {
        abs(v) >= 100 ? String(format: "%.0f%%", v) : String(format: "%.2f%%", v)
    }

    private func deltaText(company: Double, sector: Double) -> String {
        let c = pct(company)
        let s = pct(sector)
        let spread = String(format: "%+.1f pts vs sector", company - sector)
        // The "×" multiple only means anything when the sector base is non-trivial.
        if company > 0 && sector >= 2.0 {
            return "Latest \(c) · Sector \(s) · \(String(format: "%.2f×", company / sector)) vs sector"
        }
        return "Latest \(c) · Sector \(s) · \(spread)"
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
                HStack(spacing: 5) {  // dashed-gray sector swatch
                    HStack(spacing: 2) {
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
                Text(deltaText(company: pair.company, sector: pair.sector))
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textPrimary)
                    .multilineTextAlignment(.center)
            } else if let v = latestPoint?.company {
                Text("Latest \(pct(v)) · Company only")
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
