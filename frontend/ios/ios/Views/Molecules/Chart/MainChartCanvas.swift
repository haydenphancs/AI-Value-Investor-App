//
//  MainChartCanvas.swift
//  ios
//
//  Orchestrator for the main price chart area with overlays
//

import SwiftUI

struct MainChartCanvas: View {
    let pricePoints: [StockPricePoint]
    let isPositive: Bool
    let chartType: ChartType
    let overlays: [TechnicalIndicatorType]

    private var lineColor: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    private var gradientColor: LinearGradient {
        LinearGradient(
            colors: [lineColor.opacity(0.3), lineColor.opacity(0.05)],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    var body: some View {
        GeometryReader { geometry in
            let size = geometry.size

            if pricePoints.count > 1 {
                let coord = chartType == .candle || chartType == .bar
                    ? ChartCoordinateSystem.from(pricePoints: pricePoints, size: size)
                    : ChartCoordinateSystem.from(closes: pricePoints.map { $0.close }, size: size)

                ZStack {
                    // Grid lines
                    ChartGridLines()

                    // Main chart
                    switch chartType {
                    case .line:
                        LineChartRenderer(
                            closes: pricePoints.map { $0.close },
                            coord: coord,
                            lineColor: lineColor,
                            gradientColor: gradientColor
                        )
                    case .candle:
                        CandlestickChartRenderer(pricePoints: pricePoints, coord: coord)
                    case .area:
                        AreaChartRenderer(
                            closes: pricePoints.map { $0.close },
                            coord: coord,
                            lineColor: lineColor
                        )
                    case .bar:
                        OHLCBarChartRenderer(pricePoints: pricePoints, coord: coord)
                    }

                    // Overlays (MA lines, Bollinger Bands)
                    if !overlays.isEmpty {
                        OverlayRenderer(overlays: overlays, pricePoints: pricePoints, coord: coord)
                    }
                }
                .drawingGroup()
            } else {
                Text("No chart data")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }
}
