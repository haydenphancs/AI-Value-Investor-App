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
    /// Close prices preceding the visible range, used as warm-up
    /// so MA lines start from the left edge of the chart.
    var lookbackCloses: [Double] = []

    var body: some View {
        let visibleCloses = pricePoints.map { $0.close }
        let allCloses = lookbackCloses + visibleCloses
        let offset = lookbackCloses.count

        ForEach(overlays) { indicator in
            switch indicator {
            case .ma20:
                MALineOverlay(values: sliceAfterOffset(TechnicalIndicatorCalculator.sma(closes: allCloses, period: 20), offset: offset), coord: coord, color: indicator.defaultColor)
            case .ma50:
                MALineOverlay(values: sliceAfterOffset(TechnicalIndicatorCalculator.sma(closes: allCloses, period: 50), offset: offset), coord: coord, color: indicator.defaultColor)
            case .ma200:
                MALineOverlay(values: sliceAfterOffset(TechnicalIndicatorCalculator.sma(closes: allCloses, period: 200), offset: offset), coord: coord, color: indicator.defaultColor)
            case .bollingerBands:
                let full = TechnicalIndicatorCalculator.bollingerBands(closes: allCloses)
                let sliced = BollingerBandData(
                    upper: sliceAfterOffset(full.upper, offset: offset),
                    middle: sliceAfterOffset(full.middle, offset: offset),
                    lower: sliceAfterOffset(full.lower, offset: offset)
                )
                BollingerBandOverlay(data: sliced, coord: coord)
            default:
                EmptyView()
            }
        }
    }

    /// Strip the lookback portion so the result aligns with the visible coordinate system.
    private func sliceAfterOffset(_ values: [Double?], offset: Int) -> [Double?] {
        guard offset > 0, values.count > offset else { return values }
        return Array(values[offset...])
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
