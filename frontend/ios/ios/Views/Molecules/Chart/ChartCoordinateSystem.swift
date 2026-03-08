//
//  ChartCoordinateSystem.swift
//  ios
//
//  Shared coordinate math for chart rendering
//

import SwiftUI

struct ChartCoordinateSystem {
    let width: CGFloat
    let height: CGFloat
    let minValue: Double
    let maxValue: Double
    let dataCount: Int

    private var valueRange: Double {
        max(maxValue - minValue, Double.ulpOfOne)
    }

    func xPosition(for index: Int) -> CGFloat {
        guard dataCount > 1 else { return width / 2 }
        return CGFloat(index) * width / CGFloat(dataCount - 1)
    }

    func yPosition(for value: Double) -> CGFloat {
        let normalized = (value - minValue) / valueRange
        return height - (CGFloat(normalized) * height * 0.9) - height * 0.05
    }

    /// Create from close prices
    static func from(closes: [Double], size: CGSize) -> ChartCoordinateSystem {
        ChartCoordinateSystem(
            width: size.width,
            height: size.height,
            minValue: closes.min() ?? 0,
            maxValue: closes.max() ?? 1,
            dataCount: closes.count
        )
    }

    /// Create from OHLCV data (uses full high/low range)
    static func from(pricePoints: [StockPricePoint], size: CGSize) -> ChartCoordinateSystem {
        let highs = pricePoints.compactMap { $0.high }
        let lows = pricePoints.compactMap { $0.low }
        let closes = pricePoints.map { $0.close }

        let maxVal = highs.isEmpty ? (closes.max() ?? 1) : max(highs.max() ?? 1, closes.max() ?? 1)
        let minVal = lows.isEmpty ? (closes.min() ?? 0) : min(lows.min() ?? 0, closes.min() ?? 0)

        return ChartCoordinateSystem(
            width: size.width,
            height: size.height,
            minValue: minVal,
            maxValue: maxVal,
            dataCount: pricePoints.count
        )
    }
}
