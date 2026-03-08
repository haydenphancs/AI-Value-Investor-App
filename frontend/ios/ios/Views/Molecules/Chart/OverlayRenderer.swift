//
//  OverlayRenderer.swift
//  ios
//
//  Draws MA lines and Bollinger Bands on top of the main chart
//

import SwiftUI

struct OverlayRenderer: View {
    let overlays: [TechnicalIndicatorType]
    let pricePoints: [StockPricePoint]
    let coord: ChartCoordinateSystem

    var body: some View {
        let closes = pricePoints.map { $0.close }

        ForEach(overlays) { indicator in
            switch indicator {
            case .ma20:
                MALineOverlay(values: TechnicalIndicatorCalculator.sma(closes: closes, period: 20), coord: coord, color: indicator.defaultColor)
            case .ma50:
                MALineOverlay(values: TechnicalIndicatorCalculator.sma(closes: closes, period: 50), coord: coord, color: indicator.defaultColor)
            case .ma200:
                MALineOverlay(values: TechnicalIndicatorCalculator.sma(closes: closes, period: 200), coord: coord, color: indicator.defaultColor)
            case .bollingerBands:
                BollingerBandOverlay(data: TechnicalIndicatorCalculator.bollingerBands(closes: closes), coord: coord)
            default:
                EmptyView()
            }
        }
    }
}

// MARK: - MA Line

struct MALineOverlay: View {
    let values: [Double?]
    let coord: ChartCoordinateSystem
    let color: Color

    var body: some View {
        Path { path in
            var started = false
            for (index, value) in values.enumerated() {
                guard let v = value else { continue }
                let x = coord.xPosition(for: index)
                let y = coord.yPosition(for: v)
                if !started {
                    path.move(to: CGPoint(x: x, y: y))
                    started = true
                } else {
                    path.addLine(to: CGPoint(x: x, y: y))
                }
            }
        }
        .stroke(color, style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
    }
}

// MARK: - Bollinger Bands

struct BollingerBandOverlay: View {
    let data: BollingerBandData
    let coord: ChartCoordinateSystem

    var body: some View {
        ZStack {
            // Fill between upper and lower
            bandFill

            // Upper band
            bandLine(values: data.upper)
                .stroke(Color.cyan.opacity(0.7), style: StrokeStyle(lineWidth: 1, dash: [4, 3]))

            // Lower band
            bandLine(values: data.lower)
                .stroke(Color.cyan.opacity(0.7), style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
        }
    }

    private var bandFill: some View {
        Path { path in
            // Upper line forward
            var upperPoints: [(Int, Double)] = []
            var lowerPoints: [(Int, Double)] = []
            for (i, u) in data.upper.enumerated() {
                if let uVal = u, let lVal = data.lower[i] {
                    upperPoints.append((i, uVal))
                    lowerPoints.append((i, lVal))
                }
            }
            guard !upperPoints.isEmpty else { return }

            // Draw upper line forward
            let first = upperPoints[0]
            path.move(to: CGPoint(x: coord.xPosition(for: first.0), y: coord.yPosition(for: first.1)))
            for point in upperPoints.dropFirst() {
                path.addLine(to: CGPoint(x: coord.xPosition(for: point.0), y: coord.yPosition(for: point.1)))
            }
            // Draw lower line backward
            for point in lowerPoints.reversed() {
                path.addLine(to: CGPoint(x: coord.xPosition(for: point.0), y: coord.yPosition(for: point.1)))
            }
            path.closeSubpath()
        }
        .fill(Color.cyan.opacity(0.08))
    }

    private func bandLine(values: [Double?]) -> Path {
        Path { path in
            var started = false
            for (index, value) in values.enumerated() {
                guard let v = value else { continue }
                let x = coord.xPosition(for: index)
                let y = coord.yPosition(for: v)
                if !started {
                    path.move(to: CGPoint(x: x, y: y))
                    started = true
                } else {
                    path.addLine(to: CGPoint(x: x, y: y))
                }
            }
        }
    }
}
