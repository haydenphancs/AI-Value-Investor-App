//
//  SubChartCanvas.swift
//  ios
//
//  Container for sub-chart indicator panes (Volume, RSI, MACD, Stoch).
//  Indicators are computed from the full dataset to avoid warm-up gaps,
//  then only the visible portion is rendered.
//

import SwiftUI

struct SubChartCanvas: View {
    let indicator: TechnicalIndicatorType
    let pricePoints: [StockPricePoint]          // visible slice (for rendering positions)
    var allPricePoints: [StockPricePoint]? = nil // full dataset (for indicator computation)
    var visibleStartIndex: Int = 0               // where visible slice starts in allPricePoints
    @ObservedObject var crosshairState: CrosshairState

    /// The data used for indicator calculation (full dataset if available)
    private var computePoints: [StockPricePoint] {
        allPricePoints ?? pricePoints
    }

    /// The range of indices (in computePoints) that correspond to the visible slice
    private var visibleRange: Range<Int> {
        let start = max(0, min(visibleStartIndex, computePoints.count))
        let end = min(start + pricePoints.count, computePoints.count)
        guard start < end else { return 0..<0 }
        return start..<end
    }

    var body: some View {
        VStack(spacing: 0) {
            // Label + crosshair value
            HStack {
                Text(indicator.rawValue)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(AppColors.textMuted)

                if crosshairState.isDragging, let idx = crosshairState.selectedIndex,
                   idx >= 0, idx < pricePoints.count {
                    Text(crosshairValueText(for: idx))
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()
            }
            .padding(.horizontal, AppSpacing.lg)

            // Chart content with crosshair overlay
            ZStack {
                GeometryReader { geometry in
                    let size = geometry.size
                    ZStack {
                        switch indicator {
                        case .volume:
                            VolumeBarRenderer(
                                pricePoints: pricePoints,
                                size: size
                            )
                        case .rsi14:
                            RSIRenderer(
                                allCloses: computePoints.map { $0.close },
                                visibleRange: visibleRange,
                                size: size
                            )
                        case .macd:
                            MACDRenderer(
                                allCloses: computePoints.map { $0.close },
                                visibleRange: visibleRange,
                                size: size
                            )
                        case .stochastic:
                            StochasticRenderer(
                                allPricePoints: computePoints,
                                visibleRange: visibleRange,
                                size: size
                            )
                        default:
                            EmptyView()
                        }

                        // Crosshair vertical line on sub-chart
                        if crosshairState.isDragging, let idx = crosshairState.selectedIndex,
                           idx >= 0, idx < pricePoints.count {
                            let x = CGFloat(idx) * size.width / CGFloat(max(1, pricePoints.count - 1))
                            Path { path in
                                path.move(to: CGPoint(x: x, y: 0))
                                path.addLine(to: CGPoint(x: x, y: size.height))
                            }
                            .stroke(AppColors.textMuted.opacity(0.6), style: StrokeStyle(lineWidth: 0.5, dash: [4, 3]))
                        }
                    }
                }
            }
            .frame(height: 60)
            .clipped()
            .padding(.horizontal, AppSpacing.lg)
        }
        .frame(height: 78)
    }

    /// Returns a formatted string for the indicator value at the given visible index
    private func crosshairValueText(for visibleIndex: Int) -> String {
        switch indicator {
        case .volume:
            guard visibleIndex < pricePoints.count,
                  let vol = pricePoints[visibleIndex].volume else { return "" }
            return formatVolume(vol)
        case .rsi14:
            let allCloses = computePoints.map { $0.close }
            let rsiData = TechnicalIndicatorCalculator.rsi(closes: allCloses, period: 14)
            let globalIndex = visibleStartIndex + visibleIndex
            guard globalIndex >= 0, globalIndex < rsiData.values.count,
                  let val = rsiData.values[globalIndex] else { return "" }
            return String(format: "%.1f", val)
        case .macd:
            let allCloses = computePoints.map { $0.close }
            let macdData = TechnicalIndicatorCalculator.macd(closes: allCloses)
            let globalIndex = visibleStartIndex + visibleIndex
            guard globalIndex >= 0, globalIndex < macdData.macdLine.count else { return "" }
            let m = macdData.macdLine[globalIndex].map { String(format: "%.2f", $0) } ?? "-"
            let s = macdData.signalLine[globalIndex].map { String(format: "%.2f", $0) } ?? "-"
            let h = macdData.histogram[globalIndex].map { String(format: "%.2f", $0) } ?? "-"
            return "M:\(m) S:\(s) H:\(h)"
        case .stochastic:
            let closes = computePoints.map { $0.close }
            let highs = computePoints.map { $0.high ?? $0.close }
            let lows = computePoints.map { $0.low ?? $0.close }
            let stochData = TechnicalIndicatorCalculator.stochastic(highs: highs, lows: lows, closes: closes)
            let globalIndex = visibleStartIndex + visibleIndex
            guard globalIndex >= 0, globalIndex < stochData.kValues.count else { return "" }
            let k = stochData.kValues[globalIndex].map { String(format: "%.1f", $0) } ?? "-"
            let d = stochData.dValues[globalIndex].map { String(format: "%.1f", $0) } ?? "-"
            return "%K:\(k) %D:\(d)"
        default:
            return ""
        }
    }

    private func formatVolume(_ volume: Double) -> String {
        if volume >= 1_000_000_000 {
            return String(format: "%.1fB", volume / 1_000_000_000)
        } else if volume >= 1_000_000 {
            return String(format: "%.1fM", volume / 1_000_000)
        } else if volume >= 1_000 {
            return String(format: "%.1fK", volume / 1_000)
        } else {
            return String(format: "%.0f", volume)
        }
    }
}

// MARK: - Volume Bar Renderer

struct VolumeBarRenderer: View {
    let pricePoints: [StockPricePoint]
    let size: CGSize

    var body: some View {
        Canvas { context, canvasSize in
            let count = pricePoints.count
            guard count > 0 else { return }

            let volumes = pricePoints.compactMap { $0.volume }
            guard !volumes.isEmpty else { return }
            let maxVol = volumes.max() ?? 1

            let barWidth = max(1, canvasSize.width / CGFloat(count) * 0.7)

            for (index, point) in pricePoints.enumerated() {
                guard let vol = point.volume, vol > 0 else { continue }
                let x = CGFloat(index) * canvasSize.width / CGFloat(max(1, count - 1))
                let barHeight = CGFloat(vol / maxVol) * canvasSize.height * 0.9

                let previousClose = index > 0 ? pricePoints[index - 1].close : point.close
                let isBullish = point.close >= previousClose
                let color = isBullish ? AppColors.bullish.opacity(0.6) : AppColors.bearish.opacity(0.6)

                let rect = CGRect(
                    x: x - barWidth / 2,
                    y: canvasSize.height - barHeight,
                    width: barWidth,
                    height: barHeight
                )
                context.fill(Path(rect), with: .color(color))
            }
        }
    }
}

// MARK: - RSI Renderer

struct RSIRenderer: View {
    let allCloses: [Double]
    let visibleRange: Range<Int>
    let size: CGSize

    var body: some View {
        let rsiData = TechnicalIndicatorCalculator.rsi(closes: allCloses, period: 14)
        let safeRange = visibleRange.clamped(to: 0..<rsiData.values.count)
        let visibleValues = Array(rsiData.values[safeRange])
        let count = visibleValues.count

        Canvas { context, canvasSize in
            guard count > 0 else { return }

            // Reference lines at 30 and 70
            let y30 = canvasSize.height * (1 - 30.0 / 100.0)
            let y70 = canvasSize.height * (1 - 70.0 / 100.0)

            // Overbought/oversold zones
            let zoneRect = CGRect(x: 0, y: y70, width: canvasSize.width, height: y30 - y70)
            context.fill(Path(zoneRect), with: .color(Color.yellow.opacity(0.05)))

            // 30 line
            var line30 = Path()
            line30.move(to: CGPoint(x: 0, y: y30))
            line30.addLine(to: CGPoint(x: canvasSize.width, y: y30))
            context.stroke(line30, with: .color(Color.gray.opacity(0.3)), style: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))

            // 70 line
            var line70 = Path()
            line70.move(to: CGPoint(x: 0, y: y70))
            line70.addLine(to: CGPoint(x: canvasSize.width, y: y70))
            context.stroke(line70, with: .color(Color.gray.opacity(0.3)), style: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))

            // RSI line
            var rsiPath = Path()
            var started = false
            for (index, value) in visibleValues.enumerated() {
                guard let v = value else { continue }
                let x = CGFloat(index) * canvasSize.width / CGFloat(max(1, count - 1))
                let y = canvasSize.height * (1 - CGFloat(v) / 100.0)
                if !started {
                    rsiPath.move(to: CGPoint(x: x, y: y))
                    started = true
                } else {
                    rsiPath.addLine(to: CGPoint(x: x, y: y))
                }
            }
            context.stroke(rsiPath, with: .color(Color.yellow), style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
        }
    }
}

// MARK: - MACD Renderer

struct MACDRenderer: View {
    let allCloses: [Double]
    let visibleRange: Range<Int>
    let size: CGSize

    var body: some View {
        let macdData = TechnicalIndicatorCalculator.macd(closes: allCloses)
        let safeRange = visibleRange.clamped(to: 0..<macdData.macdLine.count)
        let visibleMACD = Array(macdData.macdLine[safeRange])
        let visibleSignal = Array(macdData.signalLine[safeRange])
        let visibleHistogram = Array(macdData.histogram[safeRange])
        let count = visibleMACD.count

        Canvas { context, canvasSize in
            guard count > 0 else { return }

            let allValues = (visibleMACD + visibleSignal + visibleHistogram).compactMap { $0 }
            guard !allValues.isEmpty else { return }
            let maxVal = allValues.map { abs($0) }.max() ?? 1
            let midY = canvasSize.height / 2

            func yPos(_ value: Double) -> CGFloat {
                midY - CGFloat(value / maxVal) * midY * 0.9
            }

            // Zero line
            var zeroLine = Path()
            zeroLine.move(to: CGPoint(x: 0, y: midY))
            zeroLine.addLine(to: CGPoint(x: canvasSize.width, y: midY))
            context.stroke(zeroLine, with: .color(Color.gray.opacity(0.3)), style: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))

            // Histogram bars
            let barWidth = max(1, canvasSize.width / CGFloat(count) * 0.5)
            for (index, value) in visibleHistogram.enumerated() {
                guard let v = value else { continue }
                let x = CGFloat(index) * canvasSize.width / CGFloat(max(1, count - 1))
                let barY = yPos(v)
                let rect = CGRect(
                    x: x - barWidth / 2,
                    y: min(midY, barY),
                    width: barWidth,
                    height: abs(barY - midY)
                )
                let color = v >= 0 ? AppColors.bullish.opacity(0.4) : AppColors.bearish.opacity(0.4)
                context.fill(Path(rect), with: .color(color))
            }

            // MACD line
            drawLine(context: context, values: visibleMACD, count: count, canvasWidth: canvasSize.width, yPos: yPos, color: .green, lineWidth: 1.5)

            // Signal line
            drawLine(context: context, values: visibleSignal, count: count, canvasWidth: canvasSize.width, yPos: yPos, color: .red, lineWidth: 1)
        }
    }

    private func drawLine(context: GraphicsContext, values: [Double?], count: Int, canvasWidth: CGFloat, yPos: (Double) -> CGFloat, color: Color, lineWidth: CGFloat) {
        var path = Path()
        var started = false
        for (index, value) in values.enumerated() {
            guard let v = value else { continue }
            let x = CGFloat(index) * canvasWidth / CGFloat(max(1, count - 1))
            let y = yPos(v)
            if !started {
                path.move(to: CGPoint(x: x, y: y))
                started = true
            } else {
                path.addLine(to: CGPoint(x: x, y: y))
            }
        }
        context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round))
    }
}

// MARK: - Stochastic Renderer

struct StochasticRenderer: View {
    let allPricePoints: [StockPricePoint]
    let visibleRange: Range<Int>
    let size: CGSize

    var body: some View {
        let closes = allPricePoints.map { $0.close }
        let highs = allPricePoints.map { $0.high ?? $0.close }
        let lows = allPricePoints.map { $0.low ?? $0.close }
        let stochData = TechnicalIndicatorCalculator.stochastic(highs: highs, lows: lows, closes: closes)
        let safeRange = visibleRange.clamped(to: 0..<stochData.kValues.count)
        let visibleK = Array(stochData.kValues[safeRange])
        let visibleD = Array(stochData.dValues[safeRange])
        let count = visibleK.count

        Canvas { context, canvasSize in
            guard count > 0 else { return }

            // Reference lines at 20 and 80
            let y20 = canvasSize.height * (1 - 20.0 / 100.0)
            let y80 = canvasSize.height * (1 - 80.0 / 100.0)

            var line20 = Path()
            line20.move(to: CGPoint(x: 0, y: y20))
            line20.addLine(to: CGPoint(x: canvasSize.width, y: y20))
            context.stroke(line20, with: .color(Color.gray.opacity(0.3)), style: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))

            var line80 = Path()
            line80.move(to: CGPoint(x: 0, y: y80))
            line80.addLine(to: CGPoint(x: canvasSize.width, y: y80))
            context.stroke(line80, with: .color(Color.gray.opacity(0.3)), style: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))

            // %K line (blue)
            drawStochLine(context: context, values: visibleK, count: count, canvasSize: canvasSize, color: .blue, lineWidth: 1.5)

            // %D line (orange)
            drawStochLine(context: context, values: visibleD, count: count, canvasSize: canvasSize, color: .orange, lineWidth: 1)
        }
    }

    private func drawStochLine(context: GraphicsContext, values: [Double?], count: Int, canvasSize: CGSize, color: Color, lineWidth: CGFloat) {
        var path = Path()
        var started = false
        for (index, value) in values.enumerated() {
            guard let v = value else { continue }
            let x = CGFloat(index) * canvasSize.width / CGFloat(max(1, count - 1))
            let y = canvasSize.height * (1 - CGFloat(v) / 100.0)
            if !started {
                path.move(to: CGPoint(x: x, y: y))
                started = true
            } else {
                path.addLine(to: CGPoint(x: x, y: y))
            }
        }
        context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round))
    }
}
