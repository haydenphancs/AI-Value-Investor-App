//
//  EarningsTimelineChart.swift
//  ios
//
//  Molecule: the "continuity" chart for the Earnings Timeline sheet — one
//  yearly axis flowing historical ACTUAL revenue + EPS into the forecast, with
//  an optional normalized price overlay. Reuses ReportForecastChart's
//  revenue-bar + scaled-EPS-line approach; the price line borrows the
//  normalized-overlay idea from EarningsChartView (no separate price axis,
//  matching that screen). Numeric-year x so daily price can sit at fractional
//  years; forecast bars are lighter and a dashed rule marks the actual→forecast
//  boundary.
//

import SwiftUI
import Charts

struct EarningsTimelineChart: View {
    let timeline: [RevenueProjection]      // gapless actuals -> forecast
    let dailyPrices: [EarningsDailyPricePoint]
    let showPrice: Bool

    private struct YearPoint: Identifiable {
        let id = UUID()
        let year: Double
        let revenue: Double
        let eps: Double
        let isForecast: Bool
        let revenueLabel: String
    }

    private var points: [YearPoint] {
        timeline.compactMap { p in
            guard let y = Int(p.period) else { return nil }
            return YearPoint(
                year: Double(y), revenue: p.revenue, eps: p.eps,
                isForecast: p.isForecast, revenueLabel: p.revenueLabel
            )
        }
    }

    private var maxRevenue: Double { max(points.map(\.revenue).max() ?? 1, 1) }
    private var maxEPS: Double { max(points.map(\.eps).max() ?? 1, 1) }
    /// Place the highest EPS dot at ~70% of the max bar (same idea as the
    /// in-module ReportForecastChart so EPS reads as a trajectory, not a scale).
    private var epsScaleFactor: Double { (maxRevenue * 0.70) / maxEPS }

    /// Last reported (actual) year — the dashed boundary sits just past it.
    private var lastActualYear: Double? {
        points.filter { !$0.isForecast }.map(\.year).max()
    }

    // Daily price points at fractional years, normalized into the revenue (bar)
    // domain so the line overlays as a trend (no price axis, like the Earnings
    // screen). Price only spans the years it exists for and stops at "now".
    private struct PricePoint: Identifiable { let id = UUID(); let x: Double; let y: Double }
    private var pricePoints: [PricePoint] {
        guard showPrice, !dailyPrices.isEmpty,
              let minYear = points.map(\.year).min() else { return [] }
        let parsed: [(x: Double, p: Double)] = dailyPrices.compactMap { dp in
            guard dp.date.count >= 10,
                  let y = Int(dp.date.prefix(4)),
                  let m = Int(dp.date.dropFirst(5).prefix(2)),
                  let d = Int(dp.date.dropFirst(8).prefix(2)) else { return nil }
            let frac = Double(y) + (Double(m - 1) * 30.4 + Double(d)) / 365.0
            guard frac >= minYear else { return nil }   // keep it on-chart
            return (frac, dp.price)
        }
        guard let pMin = parsed.map(\.p).min(),
              let pMax = parsed.map(\.p).max(), pMax > pMin else { return [] }
        return parsed.map {
            PricePoint(x: $0.x, y: maxRevenue * ($0.p - pMin) / (pMax - pMin))
        }
    }

    var body: some View {
        Chart {
            // Revenue bars — forecast lighter
            ForEach(points) { pt in
                BarMark(
                    x: .value("Year", pt.year),
                    y: .value("Revenue", pt.revenue),
                    width: .ratio(0.6)
                )
                .foregroundStyle(
                    pt.isForecast
                        ? AppColors.primaryBlue.opacity(0.55)
                        : AppColors.primaryBlue
                )
                .cornerRadius(AppCornerRadius.small)
                .annotation(position: .top, spacing: 2) {
                    Text(pt.revenueLabel)
                        .font(.system(size: 9))
                        .foregroundColor(AppColors.textSecondary)
                }
            }

            // EPS line + dots (scaled into the revenue domain)
            ForEach(points) { pt in
                LineMark(
                    x: .value("Year", pt.year),
                    y: .value("EPS", pt.eps * epsScaleFactor),
                    series: .value("Series", "EPS")
                )
                .foregroundStyle(AppColors.accentYellow)
                .lineStyle(StrokeStyle(lineWidth: 2))
                .interpolationMethod(.linear)
            }
            ForEach(points) { pt in
                PointMark(
                    x: .value("Year", pt.year),
                    y: .value("EPS", pt.eps * epsScaleFactor)
                )
                .foregroundStyle(AppColors.accentYellow)
                .symbolSize(24)
            }

            // Price overlay (normalized trend line, no axis) — toggle-gated
            ForEach(pricePoints) { pp in
                LineMark(
                    x: .value("Year", pp.x),
                    y: .value("Price", pp.y),
                    series: .value("Series", "Price")
                )
                .foregroundStyle(AppColors.accentCyan)
                .lineStyle(StrokeStyle(lineWidth: 1.5))
                .interpolationMethod(.catmullRom)
            }

            // Actual | forecast boundary
            if let boundary = lastActualYear {
                RuleMark(x: .value("Year", boundary + 0.5))
                    .foregroundStyle(AppColors.textMuted.opacity(0.4))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
            }
        }
        .chartYAxis(.hidden)
        .chartYScale(domain: 0...(maxRevenue * 1.18))
        .chartXAxis {
            AxisMarks(values: points.map(\.year)) { value in
                AxisValueLabel {
                    if let y = value.as(Double.self) {
                        Text(String(Int(y)))
                            .font(.system(size: 10))
                            .foregroundStyle(AppColors.textSecondary)
                    }
                }
            }
        }
        .frame(height: 200)
    }
}
