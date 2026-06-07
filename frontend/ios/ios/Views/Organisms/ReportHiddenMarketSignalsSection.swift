//
//  ReportHiddenMarketSignalsSection.swift
//  ios
//
//  Organism: Hidden Market Signals deep dive — congressional trades (reused
//  from the Holders tab data, so numbers match) + short interest snapshot and
//  a 12-point trend chart + an AI insight.
//

import SwiftUI
import Charts

struct ReportHiddenMarketSignalsSection: View {
    let data: ReportHiddenMarketSignals

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            if let congress = data.congress {
                congressCard(congress)
            }
            if let si = data.shortInterest {
                shortInterestCard(si)
            }
            if !data.insight.isEmpty {
                insightView(data.insight)
            }
        }
    }

    // MARK: - Congress

    private func congressCard(_ c: CongressSignal) -> some View {
        let netColor: Color = c.netDirection == "buy" ? AppColors.bullish
            : c.netDirection == "sell" ? AppColors.bearish : AppColors.neutral
        return VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack {
                Label("Congressional Trades", systemImage: "building.columns")
                    .font(AppTypography.label).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                Spacer()
                Text(c.period)
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
            }
            HStack(spacing: AppSpacing.sm) {
                statPill(value: "\(c.numBuyers)", label: "Buyers", color: AppColors.bullish)
                statPill(value: "\(c.numSellers)", label: "Sellers", color: AppColors.bearish)
                statPill(value: c.netDirection.capitalized, label: "Net", color: netColor)
            }
        }
        .modifier(HMSCardBackground())
    }

    // MARK: - Short interest

    private func shortInterestCard(_ s: ShortInterestSignal) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Short Interest")
                .font(AppTypography.label).fontWeight(.semibold)
                .foregroundColor(AppColors.textPrimary)

            HStack(spacing: AppSpacing.sm) {
                if let pf = s.percentOfFloat {
                    statPill(value: String(format: "%.1f%%", pf), label: "of Float", color: shortColor(pf))
                }
                if let dtc = s.daysToCover {
                    statPill(value: String(format: "%.1f", dtc), label: "Days to Cover", color: AppColors.textPrimary)
                }
                if let ch = s.change3m {
                    statPill(
                        value: String(format: "%@%.0f%%", ch >= 0 ? "+" : "", ch),
                        label: "vs 3mo",
                        color: ch > 0 ? AppColors.bearish : AppColors.bullish
                    )
                }
            }

            // 12-point trend chart — only when the FINRA series is available.
            shortChart(s.history)
        }
        .modifier(HMSCardBackground())
    }

    @ViewBuilder
    private func shortChart(_ history: [ShortInterestPoint]) -> some View {
        let points: [(Int, Double)] = history.enumerated().compactMap { idx, p in
            guard let v = p.sharesShort else { return nil }
            return (idx, v / 1_000_000)
        }
        if points.count >= 2 {
            Chart(points, id: \.0) { item in
                LineMark(x: .value("Period", item.0), y: .value("Shares Short (M)", item.1))
                    .foregroundStyle(AppColors.bearish)
                    .interpolationMethod(.catmullRom)
                AreaMark(x: .value("Period", item.0), y: .value("Shares Short (M)", item.1))
                    .foregroundStyle(AppColors.bearish.opacity(0.12))
                    .interpolationMethod(.catmullRom)
            }
            .chartXAxis(.hidden)
            .chartYAxis { AxisMarks(position: .leading) }
            .frame(height: 90)
            .padding(.top, AppSpacing.xs)

            Text("Shares short (millions) · last \(points.count) settlement dates")
                .font(.system(size: 9))
                .foregroundColor(AppColors.textMuted)
        }
    }

    // MARK: - Insight

    private func insightView(_ insight: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.xs) {
            Image(systemName: "sparkles")
                .font(AppTypography.iconDefault)
                .foregroundColor(.indigo)
            Text(insight)
                .font(AppTypography.label)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .padding(.horizontal, AppSpacing.sm)
    }

    // MARK: - Helpers

    private func statPill(value: String, label: String, color: Color) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(AppTypography.headingSmall)
                .foregroundColor(color)
                .lineLimit(1).minimumScaleFactor(0.7)
            Text(label)
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.sm)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    private func shortColor(_ pctFloat: Double) -> Color {
        if pctFloat >= 10 { return AppColors.bearish }
        if pctFloat >= 5 { return AppColors.alertOrange }
        return AppColors.textPrimary
    }
}

private struct HMSCardBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(AppSpacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackgroundLight)
            )
    }
}

#Preview {
    ReportHiddenMarketSignalsSection(
        data: ReportHiddenMarketSignals(
            congress: CongressSignal(
                numBuyers: 4, numSellers: 1,
                totalBuysInMillions: 2.3, totalSellsInMillions: 0.4,
                netDirection: "buy", period: "Last 12 Months"
            ),
            shortInterest: ShortInterestSignal(
                percentOfFloat: 6.2, daysToCover: 2.1, sharesShort: 5_000_000,
                change3m: 12.0, settlementDate: "2026-05-15",
                history: (0..<8).map { i in
                    ShortInterestPoint(
                        settlementDate: "2026-0\(i)-15",
                        sharesShort: Double(4_000_000 + i * 150_000),
                        daysToCover: 1.5 + Double(i) * 0.1
                    )
                }
            ),
            insight: "Congress is net buying while short interest climbs to 6.2% of float — a notable tension."
        )
    )
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
