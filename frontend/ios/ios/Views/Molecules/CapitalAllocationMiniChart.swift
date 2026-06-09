//
//  CapitalAllocationMiniChart.swift
//  ios
//
//  Molecule: compact dividends/buybacks (bars) + shares-outstanding (line)
//  chart for the Insider & Management → Capital Allocation card. A pared-down
//  cousin of SignalOfConfidenceChartView (Financials tab): no toggle, no axes,
//  no per-quarter data-label rows — just the trend that explains the
//  "Diluting" verdict (rising shares line) at ~120pt instead of ~280pt.
//
//  Reuses the SignalOfConfidenceDataPoint model and AppColors.confidence*
//  tokens so the colors match the full chart exactly.
//

import SwiftUI
import Charts

struct CapitalAllocationMiniChart: View {
    let dataPoints: [SignalOfConfidenceDataPoint]

    private let chartHeight: CGFloat = 120
    private let barWidth: CGFloat = 10

    var body: some View {
        if dataPoints.count >= 2 {
            Chart {
                // Dividend-yield bars
                ForEach(dataPoints) { dp in
                    BarMark(
                        x: .value("Quarter", dp.period),
                        y: .value("Dividends", dp.dividendYield),
                        width: .fixed(barWidth)
                    )
                    .foregroundStyle(AppColors.confidenceDividends)
                    .cornerRadius(2)
                    .position(by: .value("Type", "Dividends"))
                }

                // Buyback-yield bars
                ForEach(dataPoints) { dp in
                    BarMark(
                        x: .value("Quarter", dp.period),
                        y: .value("Buybacks", dp.buybackYield),
                        width: .fixed(barWidth)
                    )
                    .foregroundStyle(AppColors.confidenceBuybacks)
                    .cornerRadius(2)
                    .position(by: .value("Type", "Buybacks"))
                }

                // Shares-outstanding line — normalized into the bar band so it
                // overlays on one Y-scale and stays the dominant signal even
                // when dividends/buybacks are ~0 (the common "Diluting" case).
                ForEach(dataPoints) { dp in
                    LineMark(
                        x: .value("Quarter", dp.period),
                        y: .value("Shares", normalizeShares(dp.sharesOutstanding))
                    )
                    .foregroundStyle(AppColors.confidenceSharesOutstanding)
                    .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                    .interpolationMethod(.linear)
                }
                ForEach(dataPoints) { dp in
                    PointMark(
                        x: .value("Quarter", dp.period),
                        y: .value("Shares", normalizeShares(dp.sharesOutstanding))
                    )
                    .foregroundStyle(AppColors.confidenceSharesOutstanding)
                    .symbolSize(28)
                }
            }
            .chartYScale(domain: 0...maxBarValue)
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartLegend(.hidden)
            .frame(height: chartHeight)
        }
    }

    // MARK: - Scaling

    /// Top of the bar (yield) scale. Floored so the Y domain is always valid —
    /// when yields are ~0 (pure dilution) the floor keeps the shares line on a
    /// readable band instead of collapsing to a zero-height domain.
    private var maxBarValue: Double {
        let maxStacked = dataPoints.map { $0.dividendYield + $0.buybackYield }.max() ?? 0
        return max(maxStacked * 1.15, 0.5)
    }

    /// Padded shares range — widens a tight series (e.g. 1000M→1037M) so the
    /// dilution slope is visually obvious rather than a flat line.
    private var sharesBand: (min: Double, max: Double) {
        let values = dataPoints.map { $0.sharesOutstanding }
        let lo = values.min() ?? 0
        let hi = values.max() ?? 1
        let spread = hi - lo
        guard spread > .ulpOfOne else { return (lo - 1, hi + 1) }
        let pad = spread * 0.25
        return (lo - pad, hi + pad)
    }

    /// Map a shares value into the middle ~70% band of the bar Y-scale.
    private func normalizeShares(_ shares: Double) -> Double {
        let band = sharesBand
        let range = band.max - band.min
        guard range > 0 else { return maxBarValue * 0.5 }
        let normalized = (shares - band.min) / range // 0...1
        return maxBarValue * (0.15 + normalized * 0.70)
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        VStack(spacing: AppSpacing.lg) {
            CapitalAllocationMiniChart(
                dataPoints: SignalOfConfidenceSectionData.sampleData.dataPoints
            )
            SignalOfConfidenceLegendView()
        }
        .padding()
    }
}
