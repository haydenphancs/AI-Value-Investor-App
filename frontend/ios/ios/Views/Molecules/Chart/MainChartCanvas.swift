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
    /// Earnings and dividend dates to render as "E" and "D" markers
    var chartEventDates: ChartEventDates? = nil

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
                let baseCoord = chartType == .candle || chartType == .bar
                    ? ChartCoordinateSystem.from(pricePoints: pricePoints, size: size)
                    : ChartCoordinateSystem.from(closes: pricePoints.map { $0.close }, size: size)

                // Expand coordinate range to include overlay values so MA/Bollinger Bands aren't clipped
                let coord: ChartCoordinateSystem = {
                    guard !overlays.isEmpty else { return baseCoord }
                    let visibleCloses = pricePoints.map { $0.close }
                    let allCloses = lookbackCloses + visibleCloses
                    let offset = lookbackCloses.count

                    var minVal = baseCoord.minValue
                    var maxVal = baseCoord.maxValue

                    for overlay in overlays {
                        switch overlay {
                        case .ma20:
                            let values = TechnicalIndicatorCalculator.sma(closes: allCloses, period: 20)
                            let visible = values.count > offset ? Array(values[offset...]) : values
                            let nums = visible.compactMap { $0 }
                            if let lo = nums.min() { minVal = min(minVal, lo) }
                            if let hi = nums.max() { maxVal = max(maxVal, hi) }
                        case .ma50:
                            let values = TechnicalIndicatorCalculator.sma(closes: allCloses, period: 50)
                            let visible = values.count > offset ? Array(values[offset...]) : values
                            let nums = visible.compactMap { $0 }
                            if let lo = nums.min() { minVal = min(minVal, lo) }
                            if let hi = nums.max() { maxVal = max(maxVal, hi) }
                        case .ma200:
                            let values = TechnicalIndicatorCalculator.sma(closes: allCloses, period: 200)
                            let visible = values.count > offset ? Array(values[offset...]) : values
                            let nums = visible.compactMap { $0 }
                            if let lo = nums.min() { minVal = min(minVal, lo) }
                            if let hi = nums.max() { maxVal = max(maxVal, hi) }
                        case .bollingerBands:
                            let bb = TechnicalIndicatorCalculator.bollingerBands(closes: allCloses)
                            let visUpper = bb.upper.count > offset ? Array(bb.upper[offset...]) : bb.upper
                            let visLower = bb.lower.count > offset ? Array(bb.lower[offset...]) : bb.lower
                            if let hi = visUpper.compactMap({ $0 }).max() { maxVal = max(maxVal, hi) }
                            if let lo = visLower.compactMap({ $0 }).min() { minVal = min(minVal, lo) }
                        default:
                            break
                        }
                    }

                    return ChartCoordinateSystem(
                        width: size.width,
                        height: size.height,
                        minValue: minVal,
                        maxValue: maxVal,
                        dataCount: pricePoints.count
                    )
                }()

                ZStack {
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

                    // Current price horizontal dash line
                    if let lastClose = pricePoints.last?.close {
                        let currentY = coord.yPosition(for: lastClose)
                        Path { path in
                            path.move(to: CGPoint(x: 0, y: currentY))
                            path.addLine(to: CGPoint(x: size.width, y: currentY))
                        }
                        .stroke(
                            Color.gray.opacity(0.4),
                            style: StrokeStyle(lineWidth: 0.5, dash: [4, 3])
                        )
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

                    // Earnings & Dividend markers
                    if let events = chartEventDates {
                        ChartEventMarkers(
                            pricePoints: pricePoints,
                            coord: coord,
                            earningsDates: Set(events.earningsDates),
                            dividendDates: Set(events.dividendDates)
                        )
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

// MARK: - Earnings & Dividend Event Markers

private struct ChartEventMarkers: View {
    let pricePoints: [StockPricePoint]
    let coord: ChartCoordinateSystem
    let earningsDates: Set<String>
    let dividendDates: Set<String>

    var body: some View {
        Canvas { context, canvasSize in
            guard pricePoints.count > 1 else { return }

            // Use nearest-date matching so markers work for all intervals
            // (daily, weekly, monthly, intraday). For each event date, find
            // the first price point whose date >= the event date. This maps
            // events to the correct aggregation bar even for weekly/monthly charts.
            let earningsIndices = eventIndices(for: earningsDates)
            let dividendIndices = eventIndices(for: dividendDates)

            for index in 0..<pricePoints.count {
                let isEarnings = earningsIndices.contains(index)
                let isDividend = dividendIndices.contains(index)

                guard isEarnings || isDividend else { continue }

                let x = coord.xPosition(for: index)
                let markerY = canvasSize.height - 14

                if isEarnings {
                    drawMarker(context: context, text: "E", x: x, y: markerY,
                               bgColor: Color.orange.opacity(0.85), textColor: .white)
                }
                if isDividend {
                    // Offset slightly higher if both fall on same bar
                    let dY = isEarnings ? markerY - 18 : markerY
                    drawMarker(context: context, text: "D", x: x, y: dY,
                               bgColor: AppColors.bullish.opacity(0.85), textColor: .white)
                }
            }
        }
    }

    /// For each event date, find the first price point whose date prefix >= the event date.
    /// Works for all chart intervals: daily (exact), weekly/monthly (maps to the bar containing the event).
    private func eventIndices(for eventDates: Set<String>) -> Set<Int> {
        guard let firstDate = pricePoints.first.map({ String($0.date.prefix(10)) }),
              let lastDate = pricePoints.last.map({ String($0.date.prefix(10)) }) else {
            return []
        }

        var indices = Set<Int>()
        for eventDate in eventDates {
            // Skip events outside the chart's visible date range
            guard eventDate >= firstDate, eventDate <= lastDate else { continue }

            for (index, point) in pricePoints.enumerated() {
                let pointDate = String(point.date.prefix(10))
                if pointDate >= eventDate {
                    indices.insert(index)
                    break
                }
            }
        }
        return indices
    }

    private func drawMarker(context: GraphicsContext, text: String, x: CGFloat, y: CGFloat, bgColor: Color, textColor: Color) {
        let size: CGFloat = 16
        let rect = CGRect(x: x - size / 2, y: y - size / 2, width: size, height: size)
        let roundedRect = RoundedRectangle(cornerRadius: 3).path(in: rect)
        context.fill(roundedRect, with: .color(bgColor))

        let resolvedText = context.resolve(
            Text(text)
                .font(.system(size: 10, weight: .bold))
                .foregroundColor(textColor)
        )
        context.draw(resolvedText, at: CGPoint(x: x, y: y))
    }
}
