//
//  OverlayRenderer.swift
//  ios
//
//  Draws MA lines and Bollinger Bands on top of the main chart
//
//  Values come from `ChartSeries`, computed ONCE over the whole merged series — so a line
//  that exists at a bar is drawn at that bar, from the left edge, whatever the viewport.
//  (This used to recompute every overlay twice per frame over "the bars before the
//  viewport + the visible bars", which started lines partway in near the first loaded bar
//  and, on 1D, averaged across a multi-day hole.)
//

import SwiftUI

struct OverlayRenderer: View {
    let overlays: [TechnicalIndicatorType]
    let series: ChartSeries
    let frame: ChartFrame
    let yRange: ChartYRange

    var body: some View {
        Canvas { context, size in
            let range = frame.drawRange
            guard !range.isEmpty, range.upperBound <= series.count else { return }
            let coord = ChartCoordinateSystem(width: size.width, height: size.height, yRange: yRange, frame: frame)

            for indicator in overlays {
                switch indicator {
                case .ma20, .ma50, .ma200:
                    let values = series.movingAverage(indicator)
                    guard values.count == series.count else { continue }
                    let path = ChartPath.line(range: range, coord: coord) { values[$0] }
                    context.stroke(path, with: .color(indicator.defaultColor),
                                   style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
                case .bollingerBands:
                    drawBands(context: context, range: range, coord: coord)
                case .volume, .rsi14, .macd, .stochastic:
                    break
                }
            }
        }
    }

    private func drawBands(context: GraphicsContext, range: Range<Int>, coord: ChartCoordinateSystem) {
        // Fill between the bands, one polygon per contiguous run where both exist. Zoomed
        // out, each column contributes its HIGHEST upper and LOWEST lower so the envelope
        // is never narrower than the data.
        var fill = Path()
        var upperPts: [CGPoint] = [], lowerPts: [CGPoint] = []
        func flush() {
            guard let first = upperPts.first else { return }
            fill.move(to: first)
            for p in upperPts.dropFirst() { fill.addLine(to: p) }
            for p in lowerPts.reversed() { fill.addLine(to: p) }
            fill.closeSubpath()
            upperPts.removeAll(keepingCapacity: true)
            lowerPts.removeAll(keepingCapacity: true)
        }
        let bucket = coord.bucket
        ChartPath.forEachBucket(range, bucket: bucket) { lo, hi in
            var up = -Double.infinity, low = Double.infinity
            var any = false
            for j in lo..<hi {
                if let u = series.bbUpper[j], let l = series.bbLower[j] {
                    up = max(up, u); low = min(low, l); any = true
                }
            }
            guard any else { flush(); return }
            let x = bucket > 1
                ? (coord.xPosition(for: lo) + coord.xPosition(for: hi - 1)) / 2
                : coord.xPosition(for: lo)
            upperPts.append(CGPoint(x: x, y: coord.yPosition(for: up)))
            lowerPts.append(CGPoint(x: x, y: coord.yPosition(for: low)))
        }
        flush()
        context.fill(fill, with: .color(AppColors.accentGraphic.opacity(0.12)))

        let dash = StrokeStyle(lineWidth: 1, dash: [4, 3])
        let upper = ChartPath.line(range: range, coord: coord) { series.bbUpper[$0] }
        let lower = ChartPath.line(range: range, coord: coord) { series.bbLower[$0] }
        context.stroke(upper, with: .color(AppColors.accentGraphic.opacity(0.7)), style: dash)
        context.stroke(lower, with: .color(AppColors.accentGraphic.opacity(0.7)), style: dash)
    }
}
