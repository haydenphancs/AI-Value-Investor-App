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
    var showExtendedHours: Bool = false
    /// Close prices preceding the visible range, for MA warm-up.
    var lookbackCloses: [Double] = []

    private var lineColor: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    /// Indices of data points that fall outside regular market hours
    private var extendedHoursIndices: Set<Int> {
        guard showExtendedHours else { return [] }
        var indices = Set<Int>()
        for (i, point) in pricePoints.enumerated() {
            if point.isExtendedHours {
                indices.insert(i)
            }
        }
        return indices
    }

    /// Indices where the chart transitions between regular and extended hours (market open/close boundaries)
    private var marketBoundaryIndices: [Int] {
        guard showExtendedHours else { return [] }
        var boundaries: [Int] = []
        for i in 1..<pricePoints.count {
            let prevExtended = pricePoints[i - 1].isExtendedHours
            let currExtended = pricePoints[i].isExtendedHours
            if prevExtended != currExtended {
                boundaries.append(i)
            }
        }
        return boundaries
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

                    // Extended hours background shading
                    if showExtendedHours && !extendedHoursIndices.isEmpty {
                        ExtendedHoursBackground(
                            pricePoints: pricePoints,
                            coord: coord,
                            extendedHoursIndices: extendedHoursIndices
                        )
                    }

                    // Market hour boundary lines
                    if showExtendedHours {
                        ForEach(marketBoundaryIndices, id: \.self) { idx in
                            let x = coord.xPosition(for: idx)
                            Path { path in
                                path.move(to: CGPoint(x: x, y: 0))
                                path.addLine(to: CGPoint(x: x, y: size.height))
                            }
                            .stroke(
                                AppColors.textMuted.opacity(0.4),
                                style: StrokeStyle(lineWidth: 1, dash: [4, 3])
                            )
                        }
                    }

                    // Main chart
                    switch chartType {
                    case .line:
                        LineChartRenderer(
                            closes: pricePoints.map { $0.close },
                            coord: coord,
                            lineColor: lineColor,
                            extendedHoursIndices: showExtendedHours ? extendedHoursIndices : []
                        )
                    case .candle:
                        CandlestickChartRenderer(
                            pricePoints: pricePoints,
                            coord: coord,
                            extendedHoursIndices: showExtendedHours ? extendedHoursIndices : []
                        )
                    case .area:
                        AreaChartRenderer(
                            closes: pricePoints.map { $0.close },
                            coord: coord,
                            lineColor: lineColor,
                            extendedHoursIndices: showExtendedHours ? extendedHoursIndices : []
                        )
                    case .bar:
                        OHLCBarChartRenderer(
                            pricePoints: pricePoints,
                            coord: coord,
                            extendedHoursIndices: showExtendedHours ? extendedHoursIndices : []
                        )
                    }

                    // Overlays (MA lines, Bollinger Bands)
                    if !overlays.isEmpty {
                        OverlayRenderer(overlays: overlays, pricePoints: pricePoints, coord: coord, lookbackCloses: lookbackCloses)
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

// MARK: - Extended Hours Background Shading

private struct ExtendedHoursBackground: View {
    let pricePoints: [StockPricePoint]
    let coord: ChartCoordinateSystem
    let extendedHoursIndices: Set<Int>

    var body: some View {
        GeometryReader { geometry in
            let height = geometry.size.height

            // Find contiguous runs of extended hours indices
            Canvas { context, size in
                var i = 0
                while i < pricePoints.count {
                    if extendedHoursIndices.contains(i) {
                        let startIdx = i
                        while i < pricePoints.count && extendedHoursIndices.contains(i) {
                            i += 1
                        }
                        let endIdx = i - 1
                        let xStart = coord.xPosition(for: startIdx)
                        let xEnd = coord.xPosition(for: endIdx)
                        let halfStep: CGFloat = pricePoints.count > 1
                            ? (coord.xPosition(for: 1) - coord.xPosition(for: 0)) / 2
                            : 0
                        let rect = CGRect(
                            x: xStart - halfStep,
                            y: 0,
                            width: (xEnd - xStart) + halfStep * 2,
                            height: height
                        )
                        context.fill(Path(rect), with: .color(AppColors.textMuted.opacity(0.06)))
                    } else {
                        i += 1
                    }
                }
            }
        }
    }
}
