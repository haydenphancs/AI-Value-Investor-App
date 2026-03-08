//
//  TechnicalIndicatorCalculator.swift
//  ios
//
//  Pure computation utility for technical indicator calculations.
//  Operates on arrays of price data and returns indicator results.
//

import Foundation
import SwiftUI

enum TechnicalIndicatorCalculator {

    // MARK: - Simple Moving Average

    static func sma(closes: [Double], period: Int) -> [Double?] {
        guard period > 0 else { return Array(repeating: nil, count: closes.count) }
        var result = [Double?](repeating: nil, count: closes.count)
        guard closes.count >= period else { return result }

        var sum = closes[0..<period].reduce(0, +)
        result[period - 1] = sum / Double(period)

        for i in period..<closes.count {
            sum += closes[i] - closes[i - period]
            result[i] = sum / Double(period)
        }
        return result
    }

    // MARK: - Exponential Moving Average

    static func ema(closes: [Double], period: Int) -> [Double?] {
        guard period > 0, closes.count >= period else {
            return Array(repeating: nil, count: closes.count)
        }
        var result = [Double?](repeating: nil, count: closes.count)
        let multiplier = 2.0 / Double(period + 1)

        // Seed with SMA
        let seed = closes[0..<period].reduce(0, +) / Double(period)
        result[period - 1] = seed

        var prev = seed
        for i in period..<closes.count {
            let val = (closes[i] - prev) * multiplier + prev
            result[i] = val
            prev = val
        }
        return result
    }

    // MARK: - RSI (Relative Strength Index)

    static func rsi(closes: [Double], period: Int = 14) -> RSIData {
        guard period > 0, closes.count > period else {
            return RSIData(values: Array(repeating: nil, count: closes.count))
        }
        var result = [Double?](repeating: nil, count: closes.count)

        // Calculate price changes
        var gains = [Double]()
        var losses = [Double]()
        for i in 1..<closes.count {
            let change = closes[i] - closes[i - 1]
            gains.append(max(change, 0))
            losses.append(max(-change, 0))
        }

        guard gains.count >= period else {
            return RSIData(values: result)
        }

        // Initial average gain/loss (simple average)
        var avgGain = gains[0..<period].reduce(0, +) / Double(period)
        var avgLoss = losses[0..<period].reduce(0, +) / Double(period)

        // First RSI value
        if avgLoss == 0 {
            result[period] = 100
        } else {
            let rs = avgGain / avgLoss
            result[period] = 100 - (100 / (1 + rs))
        }

        // Wilder's smoothing for subsequent values
        for i in period..<gains.count {
            avgGain = (avgGain * Double(period - 1) + gains[i]) / Double(period)
            avgLoss = (avgLoss * Double(period - 1) + losses[i]) / Double(period)

            if avgLoss == 0 {
                result[i + 1] = 100
            } else {
                let rs = avgGain / avgLoss
                result[i + 1] = 100 - (100 / (1 + rs))
            }
        }
        return RSIData(values: result)
    }

    // MARK: - MACD

    static func macd(
        closes: [Double],
        fastPeriod: Int = 12,
        slowPeriod: Int = 26,
        signalPeriod: Int = 9
    ) -> MACDData {
        let count = closes.count
        guard count >= slowPeriod else {
            return MACDData(
                macdLine: Array(repeating: nil, count: count),
                signalLine: Array(repeating: nil, count: count),
                histogram: Array(repeating: nil, count: count)
            )
        }

        let fastEMA = ema(closes: closes, period: fastPeriod)
        let slowEMA = ema(closes: closes, period: slowPeriod)

        // MACD line = fast EMA - slow EMA
        var macdLine = [Double?](repeating: nil, count: count)
        for i in 0..<count {
            if let fast = fastEMA[i], let slow = slowEMA[i] {
                macdLine[i] = fast - slow
            }
        }

        // Signal line = EMA of MACD line
        let macdValues = macdLine.compactMap { $0 }
        let signalEMA = ema(closes: macdValues, period: signalPeriod)

        // Map signal EMA back to full-length array
        var signalLine = [Double?](repeating: nil, count: count)
        var signalIdx = 0
        let startIdx = count - macdValues.count
        for i in startIdx..<count {
            signalLine[i] = signalEMA[signalIdx]
            signalIdx += 1
        }

        // Histogram = MACD - Signal
        var histogram = [Double?](repeating: nil, count: count)
        for i in 0..<count {
            if let m = macdLine[i], let s = signalLine[i] {
                histogram[i] = m - s
            }
        }

        return MACDData(macdLine: macdLine, signalLine: signalLine, histogram: histogram)
    }

    // MARK: - Bollinger Bands

    static func bollingerBands(
        closes: [Double],
        period: Int = 20,
        stdDevMultiplier: Double = 2.0
    ) -> BollingerBandData {
        let count = closes.count
        guard count >= period else {
            return BollingerBandData(
                upper: Array(repeating: nil, count: count),
                middle: Array(repeating: nil, count: count),
                lower: Array(repeating: nil, count: count)
            )
        }

        let middle = sma(closes: closes, period: period)
        var upper = [Double?](repeating: nil, count: count)
        var lower = [Double?](repeating: nil, count: count)

        for i in (period - 1)..<count {
            guard let mid = middle[i] else { continue }
            let slice = Array(closes[(i - period + 1)...i])
            let mean = mid
            let variance = slice.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / Double(period)
            let stdDev = sqrt(variance)
            upper[i] = mid + stdDevMultiplier * stdDev
            lower[i] = mid - stdDevMultiplier * stdDev
        }

        return BollingerBandData(upper: upper, middle: middle, lower: lower)
    }

    // MARK: - Stochastic Oscillator

    static func stochastic(
        highs: [Double],
        lows: [Double],
        closes: [Double],
        kPeriod: Int = 14,
        dPeriod: Int = 3
    ) -> StochasticData {
        let count = closes.count
        guard count >= kPeriod,
              highs.count == count,
              lows.count == count else {
            return StochasticData(
                kValues: Array(repeating: nil, count: count),
                dValues: Array(repeating: nil, count: count)
            )
        }

        // %K
        var kValues = [Double?](repeating: nil, count: count)
        for i in (kPeriod - 1)..<count {
            let highSlice = Array(highs[(i - kPeriod + 1)...i])
            let lowSlice = Array(lows[(i - kPeriod + 1)...i])
            let highestHigh = highSlice.max() ?? 0
            let lowestLow = lowSlice.min() ?? 0
            let range = highestHigh - lowestLow
            if range > 0 {
                kValues[i] = ((closes[i] - lowestLow) / range) * 100
            } else {
                kValues[i] = 50
            }
        }

        // %D = SMA of %K
        var dValues = [Double?](repeating: nil, count: count)
        for i in (kPeriod - 1 + dPeriod - 1)..<count {
            var sum = 0.0
            var validCount = 0
            for j in (i - dPeriod + 1)...i {
                if let k = kValues[j] {
                    sum += k
                    validCount += 1
                }
            }
            if validCount == dPeriod {
                dValues[i] = sum / Double(dPeriod)
            }
        }

        return StochasticData(kValues: kValues, dValues: dValues)
    }
}
