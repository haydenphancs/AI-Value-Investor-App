//
//  OHLCBarChartRenderer.swift
//  ios
//
//  OHLC bar chart (vertical line + left/right ticks)
//

import SwiftUI

struct OHLCBarChartRenderer: View {
    let series: ChartSeries
    let frame: ChartFrame
    let yRange: ChartYRange
    /// Mute the pre/after-hours bars (1D with extended bars only).
    var showExtendedHours: Bool = false

    var body: some View {
        Canvas { context, size in
            let range = frame.drawRange
            guard !range.isEmpty, range.upperBound <= series.count else { return }
            let coord = ChartCoordinateSystem(width: size.width, height: size.height, yRange: yRange, frame: frame)
            let bucket = coord.bucket
            let tickWidth = max(1, min(6, coord.slotWidth * CGFloat(bucket) * 0.3))

            var up = Path(), down = Path(), extUp = Path(), extDown = Path()
            ChartPath.forEachBucket(range, bucket: bucket) { lo, hi in
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
                var bar = Path()
                bar.move(to: CGPoint(x: x, y: coord.yPosition(for: high)))
                bar.addLine(to: CGPoint(x: x, y: coord.yPosition(for: low)))
                let yOpen = coord.yPosition(for: open), yClose = coord.yPosition(for: close)
                bar.move(to: CGPoint(x: x - tickWidth, y: yOpen))
                bar.addLine(to: CGPoint(x: x, y: yOpen))
                bar.move(to: CGPoint(x: x, y: yClose))
                bar.addLine(to: CGPoint(x: x + tickWidth, y: yClose))
                switch (close >= open, extended) {
                case (true, false):  up.addPath(bar)
                case (false, false): down.addPath(bar)
                case (true, true):   extUp.addPath(bar)
                case (false, true):  extDown.addPath(bar)
                }
            }
            context.stroke(up, with: .color(AppColors.bullish), lineWidth: 1.5)
            context.stroke(down, with: .color(AppColors.bearish), lineWidth: 1.5)
            context.stroke(extUp, with: .color(AppColors.bullish.opacity(0.3)), lineWidth: 0.75)
            context.stroke(extDown, with: .color(AppColors.bearish.opacity(0.3)), lineWidth: 0.75)
        }
    }
}
