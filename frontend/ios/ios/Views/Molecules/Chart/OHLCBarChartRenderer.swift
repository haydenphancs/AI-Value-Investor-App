//
//  OHLCBarChartRenderer.swift
//  ios
//
//  OHLC bar chart (vertical line + left/right ticks)
//

import SwiftUI

struct OHLCBarChartRenderer: View {
    let pricePoints: [StockPricePoint]
    let coord: ChartCoordinateSystem

    var body: some View {
        Canvas { context, size in
            let count = pricePoints.count
            guard count > 0 else { return }

            let tickWidth = max(2, min(6, size.width / CGFloat(count) * 0.3))

            for (index, point) in pricePoints.enumerated() {
                let open = point.open ?? point.close
                let high = point.high ?? max(open, point.close)
                let low = point.low ?? min(open, point.close)
                let isBullish = point.close >= open

                let x = coord.xPosition(for: index)
                let color = isBullish ? AppColors.bullish : AppColors.bearish

                // Vertical line (high to low)
                let yHigh = coord.yPosition(for: high)
                let yLow = coord.yPosition(for: low)
                var vLine = Path()
                vLine.move(to: CGPoint(x: x, y: yHigh))
                vLine.addLine(to: CGPoint(x: x, y: yLow))
                context.stroke(vLine, with: .color(color), lineWidth: 1.5)

                // Left tick (open)
                let yOpen = coord.yPosition(for: open)
                var openTick = Path()
                openTick.move(to: CGPoint(x: x - tickWidth, y: yOpen))
                openTick.addLine(to: CGPoint(x: x, y: yOpen))
                context.stroke(openTick, with: .color(color), lineWidth: 1.5)

                // Right tick (close)
                let yClose = coord.yPosition(for: point.close)
                var closeTick = Path()
                closeTick.move(to: CGPoint(x: x, y: yClose))
                closeTick.addLine(to: CGPoint(x: x + tickWidth, y: yClose))
                context.stroke(closeTick, with: .color(color), lineWidth: 1.5)
            }
        }
    }
}
