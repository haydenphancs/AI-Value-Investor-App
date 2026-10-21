//
//  IndicatorSelfCheck.swift
//  ios
//
//  DEBUG-only proof that the chart's indicator maths is right.
//
//  There is no XCTest target, and the indicators are computed by allocation-free O(n)
//  code in `TechnicalIndicatorCalculator` (run once per data change by `ChartSeries`).
//  This re-derives every series NAIVELY — the textbook definition, written the slow,
//  obvious way — on deterministic synthetic data, and asserts the two agree. It also pins
//  the textbook first index of each indicator and the claim `ChartWarmup.bars` rests on:
//  that 210 bars of history make RSI/MACD equal to their full-history values.
//
//  Runs once at launch in DEBUG next to `ThemeContrastAudit`; an app that stays alive is
//  the pass signal. Compiles out of release.
//

#if DEBUG

import Foundation
import os

enum IndicatorSelfCheck {
    private static let log = Logger(subsystem: "com.caydex.ios", category: "IndicatorSelfCheck")

    static func run() {
        var failures: [String] = []
        func check(_ ok: Bool, _ what: @autoclosure () -> String) {
            if !ok { failures.append(what()) }
        }

        for (name, closes, highs, lows) in fixtures() {
            let n = closes.count

            // SMA
            for period in [20, 50, 200] {
                let fast = TechnicalIndicatorCalculator.sma(closes: closes, period: period)
                let ref = naiveSMA(closes, period)
                check(same(fast, ref), "\(name): SMA(\(period)) differs from the naive window mean")
                if n > period { check(firstIndex(fast) == period - 1, "\(name): SMA(\(period)) first index") }
            }

            // Bollinger (population σ, ×2)
            let bb = TechnicalIndicatorCalculator.bollingerBands(closes: closes)
            let (refUpper, refLower) = naiveBollinger(closes, 20, 2)
            check(same(bb.upper, refUpper) && same(bb.lower, refLower), "\(name): Bollinger differs from the naive bands")

            // RSI (Wilder)
            let rsi = TechnicalIndicatorCalculator.rsi(closes: closes, period: 14).values
            check(same(rsi, naiveRSI(closes, 14)), "\(name): RSI differs from the naive Wilder RSI")
            if n > 14 { check(firstIndex(rsi) == 14, "\(name): RSI first index") }

            // MACD (12, 26, 9), SMA-seeded EMAs
            let macd = TechnicalIndicatorCalculator.macd(closes: closes)
            let (refLine, refSignal) = naiveMACD(closes)
            check(same(macd.macdLine, refLine) && same(macd.signalLine, refSignal), "\(name): MACD differs from the naive MACD")
            if n > 33 {
                check(firstIndex(macd.macdLine) == 25, "\(name): MACD line first index")
                check(firstIndex(macd.signalLine) == 33, "\(name): MACD signal first index")
            }

            // Slow stochastic (14,3,3)
            let stoch = TechnicalIndicatorCalculator.slowStochastic(highs: highs, lows: lows, closes: closes)
            let (refK, refD) = naiveSlowStochastic(highs, lows, closes)
            check(same(stoch.kValues, refK) && same(stoch.dValues, refD), "\(name): slow Stoch differs from the naive (14,3,3)")
            if n > 17, highs.allSatisfy(\.isFinite), lows.allSatisfy(\.isFinite) {
                check(firstIndex(stoch.kValues) == 15, "\(name): slow %K first index")
                check(firstIndex(stoch.dValues) == 17, "\(name): slow %D first index")
            }
        }

        // ChartWarmup.bars: a series that begins `bars` before the window gives the same
        // RSI/MACD/Stoch in the window as the full history does.
        let (long, longH, longL) = walk(count: 1_200, seed: 7, start: 180)
        let cut = 400
        let w = ChartWarmup.bars
        let fullRSI = TechnicalIndicatorCalculator.rsi(closes: long).values
        let tailRSI = TechnicalIndicatorCalculator.rsi(closes: Array(long[(cut - w)...])).values
        let fullMACD = TechnicalIndicatorCalculator.macd(closes: long).signalLine
        let tailMACD = TechnicalIndicatorCalculator.macd(closes: Array(long[(cut - w)...])).signalLine
        let fullStoch = TechnicalIndicatorCalculator.slowStochastic(highs: longH, lows: longL, closes: long).dValues
        let tailStoch = TechnicalIndicatorCalculator.slowStochastic(
            highs: Array(longH[(cut - w)...]), lows: Array(longL[(cut - w)...]), closes: Array(long[(cut - w)...])
        ).dValues
        for i in cut..<long.count {
            let j = i - (cut - w)
            check(close(fullRSI[i], tailRSI[j], tolerance: 1e-4), "warm-up: RSI at bar \(i) is not converged after \(w) bars")
            check(close(fullMACD[i], tailMACD[j], tolerance: 1e-4), "warm-up: MACD signal at bar \(i) is not converged after \(w) bars")
            check(close(fullStoch[i], tailStoch[j], tolerance: 1e-9), "warm-up: Stoch at bar \(i) differs")
        }

        if failures.isEmpty {
            log.debug("indicator self-check passed")
        } else {
            let summary = failures.prefix(12).joined(separator: "\n")
            log.error("indicator self-check FAILED:\n\(summary, privacy: .public)")
            assertionFailure("Indicator maths regression:\n" + summary)
        }
    }

    // MARK: - Fixtures

    private static func fixtures() -> [(String, [Double], [Double], [Double])] {
        var out: [(String, [Double], [Double], [Double])] = []
        let (c1, h1, l1) = walk(count: 600, seed: 1, start: 50)
        out.append(("walk", c1, h1, l1))
        let (c2, h2, l2) = walk(count: 400, seed: 2, start: 98_000)
        out.append(("high-priced", c2, h2, l2))
        out.append(("flat", Array(repeating: 25, count: 260), Array(repeating: 25, count: 260), Array(repeating: 25, count: 260)))
        // A missing high/low in the middle: the stochastic must be nil across it, not 50.
        let (c3, h3Full, l3Full) = walk(count: 120, seed: 3, start: 12)
        var h3 = h3Full, l3 = l3Full
        h3[60] = .nan
        l3[60] = .nan
        out.append(("missing-range", c3, h3, l3))
        for n in 14...18 {
            let (c, h, l) = walk(count: n, seed: UInt64(n), start: 30)
            out.append(("short-\(n)", c, h, l))
        }
        return out
    }

    /// Deterministic random walk (LCG) with a plausible high/low around each close.
    private static func walk(count: Int, seed: UInt64, start: Double) -> ([Double], [Double], [Double]) {
        var state = seed &* 6364136223846793005 &+ 1442695040888963407
        func next() -> Double {
            state = state &* 6364136223846793005 &+ 1442695040888963407
            return Double(state >> 11) / Double(1 << 53)
        }
        var closes: [Double] = [], highs: [Double] = [], lows: [Double] = []
        closes.reserveCapacity(count); highs.reserveCapacity(count); lows.reserveCapacity(count)
        var price = start
        for _ in 0..<count {
            price = max(price * (1 + (next() - 0.5) * 0.04), 0.01)
            closes.append(price)
            highs.append(price * (1 + next() * 0.015))
            lows.append(price * (1 - next() * 0.015))
        }
        return (closes, highs, lows)
    }

    // MARK: - Naive references (deliberately the slow, obvious definitions)

    private static func naiveSMA(_ x: [Double], _ p: Int) -> [Double?] {
        (0..<x.count).map { i in
            guard i >= p - 1 else { return nil }
            return x[(i - p + 1)...i].reduce(0, +) / Double(p)
        }
    }

    private static func naiveBollinger(_ x: [Double], _ p: Int, _ k: Double) -> ([Double?], [Double?]) {
        var up = [Double?](repeating: nil, count: x.count), lo = up
        for i in 0..<x.count where i >= p - 1 {
            let window = Array(x[(i - p + 1)...i])
            let mean = window.reduce(0, +) / Double(p)
            let sd = sqrt(window.map { ($0 - mean) * ($0 - mean) }.reduce(0, +) / Double(p))
            up[i] = mean + k * sd
            lo[i] = mean - k * sd
        }
        return (up, lo)
    }

    private static func naiveEMA(_ x: [Double?], _ p: Int) -> [Double?] {
        // EMA over the non-nil values, seeded with the SMA of the first `p`, mapped back.
        let values = x.compactMap { $0 }
        var out = [Double?](repeating: nil, count: x.count)
        guard values.count >= p else { return out }
        let offset = x.count - values.count
        var e = values[0..<p].reduce(0, +) / Double(p)
        out[offset + p - 1] = e
        let a = 2.0 / Double(p + 1)
        for i in p..<values.count {
            e = values[i] * a + e * (1 - a)
            out[offset + i] = e
        }
        return out
    }

    private static func naiveMACD(_ x: [Double]) -> ([Double?], [Double?]) {
        let fast = naiveEMA(x.map { $0 }, 12)
        let slow = naiveEMA(x.map { $0 }, 26)
        let line: [Double?] = zip(fast, slow).map { f, s in
            guard let f, let s else { return nil }
            return f - s
        }
        return (line, naiveEMA(line, 9))
    }

    private static func naiveRSI(_ x: [Double], _ p: Int) -> [Double?] {
        var out = [Double?](repeating: nil, count: x.count)
        guard x.count > p else { return out }
        var avgGain = 0.0, avgLoss = 0.0
        for i in 1...p {
            let d = x[i] - x[i - 1]
            avgGain += max(d, 0) / Double(p)
            avgLoss += max(-d, 0) / Double(p)
        }
        func value() -> Double { avgLoss == 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss) }
        out[p] = value()
        guard x.count > p + 1 else { return out }
        for i in (p + 1)..<x.count {
            let d = x[i] - x[i - 1]
            avgGain = (avgGain * Double(p - 1) + max(d, 0)) / Double(p)
            avgLoss = (avgLoss * Double(p - 1) + max(-d, 0)) / Double(p)
            out[i] = value()
        }
        return out
    }

    private static func naiveSlowStochastic(_ h: [Double], _ l: [Double], _ c: [Double]) -> ([Double?], [Double?]) {
        let n = c.count
        var raw = [Double?](repeating: nil, count: n)
        for i in 0..<n where i >= 13 {
            let hs = Array(h[(i - 13)...i]), ls = Array(l[(i - 13)...i])
            guard hs.allSatisfy(\.isFinite), ls.allSatisfy(\.isFinite) else { continue }
            let hh = hs.max()!, ll = ls.min()!
            raw[i] = hh - ll > 0 ? (c[i] - ll) / (hh - ll) * 100 : 50
        }
        func sma3(_ v: [Double?]) -> [Double?] {
            (0..<v.count).map { i in
                guard i >= 2, let a = v[i - 2], let b = v[i - 1], let d = v[i] else { return nil }
                return (a + b + d) / 3
            }
        }
        let k = sma3(raw)
        return (k, sma3(k))
    }

    // MARK: - Comparison

    private static func firstIndex(_ v: [Double?]) -> Int? { v.firstIndex { $0 != nil } }

    private static func close(_ a: Double?, _ b: Double?, tolerance: Double) -> Bool {
        switch (a, b) {
        case (nil, nil): return true
        case let (x?, y?): return abs(x - y) <= tolerance * max(1, abs(x), abs(y))
        default: return false
        }
    }

    private static func same(_ a: [Double?], _ b: [Double?]) -> Bool {
        guard a.count == b.count else { return false }
        return zip(a, b).allSatisfy { close($0, $1, tolerance: 1e-9) }
    }
}

#endif
