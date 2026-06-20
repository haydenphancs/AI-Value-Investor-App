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
        _selectedMetricID = State(initialValue: card.chartableMetrics.first?.id ?? UUID())
    }

    private var metrics: [DeepDiveMetric] { card.chartableMetrics }

    private var selected: DeepDiveMetric? {
        metrics.first { $0.id == selectedMetricID } ?? metrics.first
    }

    private func points(_ m: DeepDiveMetric) -> [MetricHistoryPoint] {
        switch period {
        case .annual:    return m.annualHistory ?? []
        case .quarterly: return m.quarterlyHistory ?? []
        }
    }

    private func quarterlyAvailable(_ m: DeepDiveMetric) -> Bool {
        (m.quarterlyHistory?.compactMap(\.value).count ?? 0) >= 2
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    if let m = selected {
                        metricPicker
                        header(m)
                        if quarterlyAvailable(m) { periodToggle }
                        MetricHistoryChart(points: points(m), unit: m.historyUnit)
                            .id("\(m.id.uuidString)-\(period.rawValue)")
                            .animation(.easeInOut(duration: 0.25), value: selectedMetricID)
                            .animation(.easeInOut(duration: 0.25), value: period)
                        summaryStrip(m)
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
                        // A metric may lack quarterly data — fall back to annual.
                        if period == .quarterly && !quarterlyAvailable(m) {
                            period = .annual
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
    }

    // MARK: - Header (current value)

    private func header(_ m: DeepDiveMetric) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(m.historyTitle)
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
            Text("Current: \(m.value)")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
        }
    }

    // MARK: - Period toggle

    private var periodToggle: some View {
        Picker("Period", selection: $period) {
            ForEach(Period.allCases, id: \.self) { p in
                Text(p.rawValue).tag(p)
            }
        }
        .pickerStyle(.segmented)
    }

    // MARK: - Summary strip (low / latest / high)

    private func summaryStrip(_ m: DeepDiveMetric) -> some View {
        let values = points(m).compactMap(\.value)
        let lo = values.min()
        let hi = values.max()
        let latest = values.last
        return HStack(spacing: 0) {
            stat("Low", lo, m.historyUnit)
            divider
            stat("Latest", latest, m.historyUnit)
            divider
            stat("High", hi, m.historyUnit)
        }
        .padding(.vertical, AppSpacing.sm)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }

    private func stat(_ label: String, _ value: Double?, _ unit: String?) -> some View {
        VStack(spacing: 2) {
            Text(label)
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
            Text(value.map { Self.format($0, unit: unit) } ?? "—")
                .font(AppTypography.dataMedium)
                .foregroundColor(AppColors.textPrimary)
        }
        .frame(maxWidth: .infinity)
    }

    private var divider: some View {
        Rectangle()
            .fill(AppColors.textMuted.opacity(0.2))
            .frame(width: 1, height: 28)
    }

    private var footnote: some View {
        Text(period == .annual
             ? "Annual figures by fiscal year."
             : "Quarterly figures; growth shown year-over-year.")
            .font(AppTypography.labelSmall)
            .foregroundColor(AppColors.textMuted)
    }

    // MARK: - Formatting (shared with MetricHistoryChart's units)

    static func format(_ v: Double, unit: String?) -> String {
        switch unit {
        case "percent": return String(format: "%.1f%%", v)
        case "score":   return String(format: "%.1f", v)
        default:        return String(format: "%.1fx", v)
        }
    }
}

#Preview {
    let annual = (2015...2024).map {
        MetricHistoryPoint(period: String($0), value: 30 + Double($0 - 2015) * 1.5)
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
                quarterlyHistory: nil
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
