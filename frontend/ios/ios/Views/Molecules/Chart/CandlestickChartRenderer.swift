//
//  CandlestickChartRenderer.swift
//  ios
//
//  Candlestick chart (OHLC) rendering
//

import SwiftUI

struct CandlestickChartRenderer: View {
    let series: ChartSeries
    let frame: ChartFrame
    let yRange: ChartYRange
    /// Mute the pre/after-hours candles (1D with extended bars only).
    var showExtendedHours: Bool = false

    /// ⚠️ Outline-vs-fill is ALREADY taken here: it encodes extended-hours trading
    /// (`isExtended` below). Two meanings cannot share one channel, so under DWC the
    /// extended-hours cue moves entirely onto OPACITY — which it already half-used —
    /// and outline/fill is freed for direction: hollow body = up, filled = down, the
    /// convention every trading platform uses.
    @Environment(\.differentiateWithoutColor) private var differentiate

    var body: some View {
        Canvas { context, size in
            let range = frame.drawRange
            guard !range.isEmpty, range.upperBound <= series.count else { return }
            let coord = ChartCoordinateSystem(width: size.width, height: size.height, yRange: yRange, frame: frame)
            let bucket = coord.bucket
            let bodyWidth = max(1, min(8, coord.slotWidth * CGFloat(bucket) * 0.6))

            // Batched: one path per (direction × style) instead of 2–3 draw calls per bar.
            var upWicks = Path(), downWicks = Path(), extUpWicks = Path(), extDownWicks = Path()
            var upBodies = Path(), downBodies = Path(), extUpBodies = Path(), extDownBodies = Path()

            ChartPath.forEachBucket(range, bucket: bucket) { lo, hi in
                // One candle per column when zoomed out: first open, last close, the column's
                // high/low. A bar with no OHLC (old history) contributes its close only.
                let open = series.open[lo] ?? series.close[lo]
                let close = series.close[hi - 1]
                var high = max(open, close), low = min(open, close)
                var extended = true
                for j in lo..<hi {
                    high = max(high, series.high[j] ?? series.close[j])
                    low = min(low, series.low[j] ?? series.close[j])
                    if !(showExtendedHours && series.isExtended[j]) { extended = false }
                }
                let x = bucket > 1
                    ? (coord.xPosition(for: lo) + coord.xPosition(for: hi - 1)) / 2
                    : coord.xPosition(for: lo)
                let isBullish = close >= open

                var wick = Path()
                wick.move(to: CGPoint(x: x, y: coord.yPosition(for: high)))
                wick.addLine(to: CGPoint(x: x, y: coord.yPosition(for: low)))
                let bodyTop = coord.yPosition(for: max(open, close))
                let bodyBottom = coord.yPosition(for: min(open, close))
                let rect = CGRect(x: x - bodyWidth / 2, y: bodyTop, width: bodyWidth, height: max(1, bodyBottom - bodyTop))

                switch (isBullish, extended) {
                case (true, false):  upWicks.addPath(wick);     upBodies.addRect(rect)
                case (false, false): downWicks.addPath(wick);   downBodies.addRect(rect)
                case (true, true):   extUpWicks.addPath(wick);  extUpBodies.addRect(rect)
                case (false, true):  extDownWicks.addPath(wick); extDownBodies.addRect(rect)
                }
            }

            let up = AppColors.bullish, down = AppColors.bearish
            context.stroke(upWicks, with: .color(up), lineWidth: 1)
            context.stroke(downWicks, with: .color(down), lineWidth: 1)
            context.stroke(extUpWicks, with: .color(up.opacity(0.3)), lineWidth: 0.5)
            context.stroke(extDownWicks, with: .color(down.opacity(0.3)), lineWidth: 0.5)

            if differentiate {
                // Direction owns outline-vs-fill: hollow = up, filled = down. Extended hours
                // stays distinguishable through its 0.3 opacity and half-width wick.
                context.stroke(upBodies, with: .color(up), lineWidth: 1)
                context.fill(downBodies, with: .color(down))
                context.stroke(extUpBodies, with: .color(up.opacity(0.3)), lineWidth: 0.5)
                context.fill(extDownBodies, with: .color(down.opacity(0.3)))
            } else {
                context.fill(upBodies, with: .color(up))
                context.fill(downBodies, with: .color(down))
                // Extended-hours candles are drawn as outlines only.
                context.stroke(extUpBodies, with: .color(up.opacity(0.3)), lineWidth: 0.5)
                context.stroke(extDownBodies, with: .color(down.opacity(0.3)), lineWidth: 0.5)
            }
        }
    }
}
