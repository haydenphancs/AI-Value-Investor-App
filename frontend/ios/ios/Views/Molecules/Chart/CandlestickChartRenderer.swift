//
//  CandlestickChartRenderer.swift
//  ios
//
//  Candlestick chart (OHLC) rendering
//

import SwiftUI

struct CandlestickChartRenderer: View {
    let pricePoints: [StockPricePoint]
    let coord: ChartCoordinateSystem
    var extendedHoursIndices: Set<Int> = []

    /// ⚠️ Outline-vs-fill is ALREADY taken here: it encodes extended-hours trading
    /// (`isExtended` below). Two meanings cannot share one channel, so under DWC the
    /// extended-hours cue moves entirely onto OPACITY — which it already half-used —
    /// and outline/fill is freed for direction: hollow body = up, filled = down, the
    /// convention every trading platform uses.
    @Environment(\.differentiateWithoutColor) private var differentiate

    var body: some View {
        Canvas { context, size in
            let count = pricePoints.count
            guard count > 0 else { return }

            let bodyWidth = max(2, min(8, size.width / CGFloat(count) * 0.6))

            for (index, point) in pricePoints.enumerated() {
                let open = point.open ?? point.close
                let high = point.high ?? max(open, point.close)
                let low = point.low ?? min(open, point.close)
                let isBullish = point.close >= open
                let isExtended = extendedHoursIndices.contains(index)

                let x = coord.xPosition(for: index)
                let baseColor = isBullish ? AppColors.bullish : AppColors.bearish
                let color = isExtended ? baseColor.opacity(0.3) : baseColor

                // Wick (high to low)
                let wickTop = coord.yPosition(for: high)
                let wickBottom = coord.yPosition(for: low)
                var wickPath = Path()
                wickPath.move(to: CGPoint(x: x, y: wickTop))
                wickPath.addLine(to: CGPoint(x: x, y: wickBottom))
                context.stroke(wickPath, with: .color(color), lineWidth: isExtended ? 0.5 : 1)

                // Body (open to close)
                let bodyTop = coord.yPosition(for: max(open, point.close))
                let bodyBottom = coord.yPosition(for: min(open, point.close))
                let bodyHeight = max(1, bodyBottom - bodyTop)
                let bodyRect = CGRect(
                    x: x - bodyWidth / 2,
                    y: bodyTop,
                    width: bodyWidth,
                    height: bodyHeight
                )

                if differentiate {
                    // Direction owns outline-vs-fill: hollow = up, filled = down.
                    // Extended hours is still distinguishable — `color` is already at
                    // 0.3 opacity for those candles (line 31), and the wick above is
                    // drawn at half width.
                    if isBullish {
                        context.stroke(Path(bodyRect), with: .color(color),
                                       lineWidth: isExtended ? 0.5 : 1)
                    } else {
                        context.fill(Path(bodyRect), with: .color(color))
                    }
                } else if isExtended {
                    // Draw outline only for extended hours candles
                    context.stroke(Path(bodyRect), with: .color(color), lineWidth: 0.5)
                } else {
                    context.fill(Path(bodyRect), with: .color(color))
                }
            }
        }
    }
}
