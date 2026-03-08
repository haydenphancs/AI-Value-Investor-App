//
//  SubChartCanvas.swift
//  ios
//
//  Container for sub-chart indicator panes (Volume, RSI, MACD, Stoch)
//

import SwiftUI

struct SubChartCanvas: View {
    let indicator: TechnicalIndicatorType
    let pricePoints: [StockPricePoint]

    var body: some View {
        VStack(spacing: 0) {
            // Label
            HStack {
                Text(indicator.rawValue)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(AppColors.textMuted)
                Spacer()
            }
            .padding(.horizontal, AppSpacing.lg)

            // Chart content
            GeometryReader { geometry in
                let size = geometry.size
                switch indicator {
                case .volume:
                    VolumeBarRenderer(pricePoints: pricePoints, size: size)
                case .rsi14:
                    RSIRenderer(pricePoints: pricePoints, size: size)
                case .macd:
                    MACDRenderer(pricePoints: pricePoints, size: size)
                case .stochastic:
                    StochasticRenderer(pricePoints: pricePoints, size: size)
                default:
                    EmptyView()
                }
            }
            .frame(height: 50)
            .padding(.horizontal, AppSpacing.lg)
        }
        .frame(height: 65)
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
    let pricePoints: [StockPricePoint]
    let size: CGSize

    var body: some View {
        let closes = pricePoints.map { $0.close }
        let rsiData = TechnicalIndicatorCalculator.rsi(closes: closes, period: 14)
        let count = rsiData.values.count

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
            for (index, value) in rsiData.values.enumerated() {
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
    let pricePoints: [StockPricePoint]
    let size: CGSize

    var body: some View {
        let closes = pricePoints.map { $0.close }
        let macdData = TechnicalIndicatorCalculator.macd(closes: closes)
        let count = macdData.macdLine.count

        Canvas { context, canvasSize in
            guard count > 0 else { return }

            let allValues = (macdData.macdLine + macdData.signalLine + macdData.histogram).compactMap { $0 }
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
            for (index, value) in macdData.histogram.enumerated() {
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
            drawLine(context: context, values: macdData.macdLine, count: count, canvasWidth: canvasSize.width, yPos: yPos, color: .green, lineWidth: 1.5)

            // Signal line
            drawLine(context: context, values: macdData.signalLine, count: count, canvasWidth: canvasSize.width, yPos: yPos, color: .red, lineWidth: 1)
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
    let pricePoints: [StockPricePoint]
    let size: CGSize

    var body: some View {
        let closes = pricePoints.map { $0.close }
        let highs = pricePoints.map { $0.high ?? $0.close }
        let lows = pricePoints.map { $0.low ?? $0.close }
        let stochData = TechnicalIndicatorCalculator.stochastic(highs: highs, lows: lows, closes: closes)
        let count = stochData.kValues.count

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
            drawStochLine(context: context, values: stochData.kValues, count: count, canvasSize: canvasSize, color: .blue, lineWidth: 1.5)

            // %D line (orange)
            drawStochLine(context: context, values: stochData.dValues, count: count, canvasSize: canvasSize, color: .orange, lineWidth: 1)
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
