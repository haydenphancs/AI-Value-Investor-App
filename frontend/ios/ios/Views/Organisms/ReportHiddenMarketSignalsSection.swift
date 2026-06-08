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

    private static let dateParser: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

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
                Text("Congressional Trades")
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

            // 12-month dual-axis trend — green bars = short interest (shares,
            // left axis), white line = short float % (right axis). Only when
            // the FINRA settlement series is available.
            shortChart(s)
        }
        .modifier(HMSCardBackground())
    }

    @ViewBuilder
    private func shortChart(_ s: ShortInterestSignal) -> some View {
        let points: [SIPoint] = s.history.compactMap { p in
            guard let ds = p.settlementDate,
                  let d = Self.dateParser.date(from: ds),
                  let ss = p.sharesShort else { return nil }
            return SIPoint(date: d, sharesM: ss / 1_000_000)
        }
        // % of float per million shares — lets the right axis re-label the
        // shares domain as a % scale (constant-float approximation).
        let pctPerM: Double? = {
            guard let pf = s.percentOfFloat, let ss = s.sharesShort, ss > 0 else { return nil }
            return pf / (ss / 1_000_000)
        }()

        if points.count >= 2 {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                HStack(spacing: AppSpacing.md) {
                    legendItem(color: AppColors.bullish, label: "Short interest")
                    if pctPerM != nil {
                        legendItem(color: .white, label: "Short float")
                    }
                }
                ShortInterestTrendChart(points: points, pctPerM: pctPerM)
            }
            .padding(.top, AppSpacing.xs)
        }
    }

    private func legendItem(color: Color, label: String) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 1.5)
                .fill(color)
                .frame(width: 14, height: 3)
            Text(label)
                .font(.system(size: 10))
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

// MARK: - Short-interest trend chart

private struct SIPoint: Identifiable {
    let id = UUID()
    let date: Date
    let sharesM: Double
}

/// Dual-axis combo: green bars = short interest (shares, left axis in M),
/// white line = short float % (right axis, re-labeled off the shares domain
/// via `pctPerM`). Extracted into its own View so the SwiftUI type-checker
/// can handle the chart expression.
private struct ShortInterestTrendChart: View {
    let points: [SIPoint]
    let pctPerM: Double?

    var body: some View {
        let vals = points.map { $0.sharesM }
        let yMin = (vals.min() ?? 0) * 0.9
        let yMax = (vals.max() ?? 1) * 1.05

        return Chart {
            ForEach(points) { item in
                BarMark(
                    x: .value("Date", item.date),
                    y: .value("Shares Short", item.sharesM)
                )
                .foregroundStyle(AppColors.bullish)
            }
            ForEach(points) { item in
                LineMark(
                    x: .value("Date", item.date),
                    y: .value("Shares Short", item.sharesM)
                )
                .foregroundStyle(pctPerM == nil ? Color.clear : Color.white)
                .lineStyle(StrokeStyle(lineWidth: 1.5))
                .interpolationMethod(.catmullRom)
            }
        }
        .chartYScale(domain: yMin...yMax)
        .chartYAxis {
            AxisMarks(position: .leading) { value in
                AxisGridLine().foregroundStyle(AppColors.textMuted.opacity(0.12))
                AxisValueLabel {
                    if let m = value.as(Double.self) {
                        Text("\(Int(m))M")
                            .font(.system(size: 9))
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            }
            if let k = pctPerM {
                AxisMarks(position: .trailing) { value in
                    AxisValueLabel {
                        if let m = value.as(Double.self) {
                            Text(String(format: "%.1f%%", m * k))
                                .font(.system(size: 9))
                                .foregroundColor(AppColors.textMuted)
                        }
                    }
                }
            }
        }
        .chartXAxis {
            AxisMarks(values: .stride(by: .month, count: 3)) { _ in
                AxisGridLine().foregroundStyle(AppColors.textMuted.opacity(0.1))
                AxisValueLabel(format: .dateTime.month(.abbreviated))
            }
        }
        .frame(height: 150)
    }
}

#Preview {
    let history: [ShortInterestPoint] = (0..<12).map { (i: Int) -> ShortInterestPoint in
        let shares: Double = Double(28_000_000 + i * 900_000)
        let dtc: Double = 1.5 + Double(i) * 0.05
        let date: String = String(format: "2025-%02d-15", i + 1)
        return ShortInterestPoint(settlementDate: date, sharesShort: shares, daysToCover: dtc)
    }
    let signal = ShortInterestSignal(
        percentOfFloat: 2.1, daysToCover: 1.6, sharesShort: 38_000_000,
        change3m: 8.0, settlementDate: "2025-12-15", history: history
    )
    let congress = CongressSignal(
        numBuyers: 4, numSellers: 1,
        totalBuysInMillions: 2.3, totalSellsInMillions: 0.4,
        netDirection: "buy", period: "Last 12 Months"
    )
    let model = ReportHiddenMarketSignals(
        congress: congress, shortInterest: signal,
        insight: "Congress is net buying while short interest climbs to 6.2% of float — a notable tension."
    )
    return ReportHiddenMarketSignalsSection(data: model)
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
