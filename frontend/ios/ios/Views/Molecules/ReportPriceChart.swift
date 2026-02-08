//
//  ReportPriceChart.swift
//  ios
//
//  Molecule: Area line chart with gradient fill for price movement visualization.
//  Uses native Swift Charts. Color-coded green/red based on trend direction.
//

import SwiftUI
import Charts

struct ReportPriceChart: View {
    let points: [PricePoint]
    let stats: PriceMovementStats

    private var trendColor: Color { stats.trendColor }

    private var yMin: Double {
        (points.map(\.price).min() ?? 0) * 0.998
    }

    private var yMax: Double {
        (points.map(\.price).max() ?? 0) * 1.002
    }

    var body: some View {
        VStack(spacing: 0) {
            // Price chart
            Chart {
                ForEach(points) { point in
                    // Area fill
                    AreaMark(
                        x: .value("Time", point.index),
                        yStart: .value("Min", yMin),
                        yEnd: .value("Price", point.price)
                    )
                    .foregroundStyle(
                        LinearGradient(
                            colors: [trendColor.opacity(0.3), trendColor.opacity(0.0)],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                    .interpolationMethod(.catmullRom)

                    // Line
                    LineMark(
                        x: .value("Time", point.index),
                        y: .value("Price", point.price)
                    )
                    .foregroundStyle(trendColor)
                    .lineStyle(StrokeStyle(lineWidth: 2))
                    .interpolationMethod(.catmullRom)
                }
            }
            .chartYScale(domain: yMin...yMax)
            .chartXAxis(.hidden)
            .chartYAxis {
                AxisMarks(position: .trailing, values: .automatic(desiredCount: 3)) { value in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [4, 4]))
                        .foregroundStyle(AppColors.textMuted.opacity(0.2))
                    AxisValueLabel()
                        .foregroundStyle(AppColors.textMuted)
                        .font(AppTypography.caption)
                }
            }
            .frame(height: 160)

            // Volume bars
            if points.contains(where: { $0.volume != nil }) {
                Chart {
                    ForEach(points) { point in
                        if let volume = point.volume {
                            BarMark(
                                x: .value("Time", point.index),
                                y: .value("Volume", volume)
                            )
                            .foregroundStyle(trendColor.opacity(0.25))
                        }
                    }
                }
                .chartXAxis(.hidden)
                .chartYAxis(.hidden)
                .frame(height: 32)
            }
        }
    }
}

#Preview {
    let sample = TickerReportData.sampleOracle.priceMovement
    ReportPriceChart(
        points: sample.points[.oneDay] ?? [],
        stats: sample.stats[.oneDay]!
    )
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
