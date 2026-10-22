//
//  SubChartCanvas.swift
//  ios
//
//  Container for sub-chart indicator panes (Volume, RSI, MACD, Stoch).
//  Indicators come from `ChartSeries`, computed ONCE over the whole merged series; a pane
//  draws only the bars of the shared `ChartFrame`, at the same x as the price chart.
//
//  The pane itself is `Equatable` and never observes the crosshair: a scrub step re-renders
//  only the label readout and the crosshair line (`SubChartCrosshairLine`), not the pane —
//  it used to recompute RSI/MACD/Stoch over every loaded bar on every scrub frame, twice.
//

import SwiftUI

struct SubChartCanvas: View {
    let indicator: TechnicalIndicatorType
    let series: ChartSeries
    let frame: ChartFrame
    /// Passed down, not observed: only the readout and the crosshair line re-render on a
    /// scrub step.
    let crosshairState: CrosshairState

    var body: some View {
        VStack(spacing: 0) {
            // Label + crosshair value
            HStack {
                Text(indicator.rawValue)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(AppColors.textMuted)

                SubChartReadout(indicator: indicator, series: series, crosshairState: crosshairState)

                Spacer()
            }
            .padding(.horizontal, AppSpacing.lg)

            ZStack {
                SubChartPane(indicator: indicator, series: series, frame: frame)
                    .equatable()
                SubChartCrosshairLine(series: series, frame: frame, crosshairState: crosshairState)
            }
            .frame(height: 60)
            .clipped()
            .padding(.horizontal, AppSpacing.lg)
        }
        .frame(height: 78)
    }
}

/// The value under the crosshair — an O(1) read of the precomputed arrays.
private struct SubChartReadout: View {
    /// Used to prefix the VOLUME readout with a direction glyph (the bars read it in
    /// `SubChartPane`).
    @Environment(\.differentiateWithoutColor) private var differentiate

    let indicator: TechnicalIndicatorType
    let series: ChartSeries
    @ObservedObject var crosshairState: CrosshairState

    var body: some View {
        if crosshairState.isDragging, let i = crosshairState.index(in: series) {
            Text(text(at: i))
                .font(.system(size: 10, weight: .medium, design: .monospaced))
                .foregroundColor(AppColors.textPrimary)
        }
    }

    private func text(at i: Int) -> String {
        switch indicator {
        case .volume:
            guard let vol = series.volume[i] else { return "" }
            // Prefix the direction. The readout printed magnitude only, so a user who reads
            // the value rather than the bar shape had NO access to up-vs-down. Only under
            // DWC, so the default readout stays unchanged. Direction is vs the PREVIOUS
            // LOADED bar, so the leftmost visible bar is not always "up".
            let prev = i > 0 ? series.close[i - 1] : series.close[i]
            return AppSentiment.marker(isPositive: series.close[i] >= prev, differentiate: differentiate)
                + formatVolume(vol)
        case .rsi14:
            return series.rsi14[i].map { String(format: "%.1f", $0) } ?? ""
        case .macd:
            let m = series.macdLine[i].map { String(format: "%.2f", $0) } ?? "-"
            let s = series.macdSignal[i].map { String(format: "%.2f", $0) } ?? "-"
            let h = series.macdHistogram[i].map { String(format: "%.2f", $0) } ?? "-"
            return "M:\(m) S:\(s) H:\(h)"
        case .stochastic:
            let k = series.stochK[i].map { String(format: "%.1f", $0) } ?? "-"
            let d = series.stochD[i].map { String(format: "%.1f", $0) } ?? "-"
            return "%K:\(k) %D:\(d)"
        case .ma20, .ma50, .ma200, .bollingerBands:
            return ""
        }
    }

    private func formatVolume(_ volume: Double) -> String {
        if volume >= 1_000_000_000 {
            return String(format: "%.1fB", volume / 1_000_000_000)
        } else if volume >= 1_000_000 {
            return String(format: "%.1fM", volume / 1_000_000)
        } else if volume >= 1_000 {
            return String(format: "%.1fK", volume / 1_000)
        } else {
            return String(format: "%.0f", volume)
        }
    }
}

private struct SubChartCrosshairLine: View {
    let series: ChartSeries
    let frame: ChartFrame
    @ObservedObject var crosshairState: CrosshairState

    var body: some View {
        GeometryReader { geometry in
            if crosshairState.isDragging, let i = crosshairState.index(in: series), frame.visibleRange.contains(i) {
                let x = frame.x(i, width: geometry.size.width)
                Path { path in
                    path.move(to: CGPoint(x: x, y: 0))
                    path.addLine(to: CGPoint(x: x, y: geometry.size.height))
                }
                .stroke(AppColors.textMuted.opacity(0.6), style: StrokeStyle(lineWidth: 0.5, dash: [4, 3]))
            }
        }
        .allowsHitTesting(false)
    }
}

/// One pane. Up-vs-down volume is encoded by HUE ALONE in the default style, and unlike
/// every other pane there is no second channel already doing the work: bars always grow up
/// from the baseline and their height is VOLUME, so position says nothing about direction.
/// Under Differentiate Without Color the DOWN bars are hatched.
///
/// HATCH, not a hollow body: at 1Y a bar is ~1 pt wide, and a hollow 1 pt rect stroked at
/// 1.5 pt is a filled rect — the cue would vanish exactly where the bars are densest. Dash
/// is spent app-wide on reference lines, opacity on extended hours, outline-vs-fill on
/// candle direction; hatch has no other meaning anywhere.
private struct SubChartPane: View, Equatable {
    @Environment(\.differentiateWithoutColor) private var differentiate

    let indicator: TechnicalIndicatorType
    let series: ChartSeries
    let frame: ChartFrame

    static func == (lhs: SubChartPane, rhs: SubChartPane) -> Bool {
        lhs.indicator == rhs.indicator
            && lhs.series.revision == rhs.series.revision
            && lhs.series.count == rhs.series.count
            && lhs.frame == rhs.frame
    }

    var body: some View {
        Canvas { context, size in
            let range = frame.drawRange
            guard !range.isEmpty, range.upperBound <= series.count, size.width > 0 else { return }
            switch indicator {
            case .volume:     drawVolume(context: context, size: size, range: range)
            case .rsi14:      drawOscillator(context: context, size: size, range: range, lines: [(series.rsi14, AppColors.cautionGraphic, 1.5)], bands: (30, 70), shadeBand: true)
            case .stochastic: drawOscillator(context: context, size: size, range: range, lines: [(series.stochK, AppColors.primaryGraphic, 1.5), (series.stochD, AppColors.cautionGraphic, 1)], bands: (20, 80), shadeBand: false)
            case .macd:       drawMACD(context: context, size: size, range: range)
            case .ma20, .ma50, .ma200, .bollingerBands: break
            }
        }
    }

    private func coord(_ size: CGSize, min lo: Double, max hi: Double) -> ChartCoordinateSystem {
        // Panes use the full height (no 5% padding) for 0–100 oscillators; the y mapping
        // below converts directly.
        ChartCoordinateSystem(width: size.width, height: size.height, yRange: ChartYRange(minValue: lo, maxValue: hi), frame: frame)
    }

    // MARK: Volume

    private func drawVolume(context: GraphicsContext, size: CGSize, range: Range<Int>) {
        let c = coord(size, min: 0, max: 1)
        let bucket = c.bucket
        // Scaled to the bars on screen; the partly visible edge bars are drawn, not measured.
        var maxVol = 0.0
        for i in frame.visibleRange { if let v = series.volume[i], v > maxVol { maxVol = v } }
        guard maxVol > 0 else { return }
        let barWidth = max(1, c.slotWidth * CGFloat(bucket) * 0.7)

        var up = Path(), down = Path()
        ChartPath.forEachBucket(range, bucket: bucket) { lo, hi in
            var vol = 0.0
            for j in lo..<hi { if let v = series.volume[j], v > vol { vol = v } }
            guard vol > 0 else { return }
            // Direction vs the bar BEFORE the column — the previous LOADED bar, so the
            // leftmost visible bar is not always drawn "up".
            let prevClose = lo > 0 ? series.close[lo - 1] : series.close[lo]
            let isBullish = series.close[hi - 1] >= prevClose
            let x = bucket > 1 ? (c.xPosition(for: lo) + c.xPosition(for: hi - 1)) / 2 : c.xPosition(for: lo)
            let h = CGFloat(min(vol / maxVol, 1)) * size.height * 0.9
            let rect = CGRect(x: x - barWidth / 2, y: size.height - h, width: barWidth, height: h)
            if isBullish { up.addRect(rect) } else { down.addRect(rect) }
        }
        let upColor = AppColors.bullish.opacity(0.6), downColor = AppColors.bearish.opacity(0.6)
        context.fill(up, with: .color(upColor))
        context.fill(down, with: .color(downColor))
        // Hatch the DOWN bars only — the cue has to be a difference. One clip, one stroke.
        if differentiate, !down.isEmpty {
            context.drawLayer { layer in
                layer.clip(to: down)
                layer.stroke(AppSentiment.hatch(in: down.boundingRect, differentiate: true, spacing: 4),
                             with: .color(downColor), lineWidth: 0.75)
            }
        }
    }

    // MARK: RSI / Stochastic (fixed 0–100 scale)

    private func drawOscillator(
        context: GraphicsContext,
        size: CGSize,
        range: Range<Int>,
        lines: [([Double?], Color, CGFloat)],
        bands: (Double, Double),
        shadeBand: Bool
    ) {
        let yLow = size.height * (1 - bands.0 / 100.0)
        let yHigh = size.height * (1 - bands.1 / 100.0)
        if shadeBand {
            context.fill(Path(CGRect(x: 0, y: yHigh, width: size.width, height: yLow - yHigh)),
                         with: .color(AppColors.cautionGraphic.opacity(0.08)))
        }
        var refs = Path()
        refs.move(to: CGPoint(x: 0, y: yLow)); refs.addLine(to: CGPoint(x: size.width, y: yLow))
        refs.move(to: CGPoint(x: 0, y: yHigh)); refs.addLine(to: CGPoint(x: size.width, y: yHigh))
        context.stroke(refs, with: .color(AppColors.chartGridline), style: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))

        // A 0–100 value maps to the FULL pane height (y = h·(1 − v/100)), as before.
        // `ChartYRange` keeps a 5% margin top and bottom, so the range that reproduces the
        // full-height mapping exactly is [5, 95].
        let c = coord(size, min: 5, max: 95)
        for (values, color, width) in lines where values.count == series.count {
            let path = ChartPath.line(range: range, coord: c) { values[$0] }
            context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: width, lineCap: .round, lineJoin: .round))
        }
    }

    // MARK: MACD (symmetric scale around a centred zero line)

    private func drawMACD(context: GraphicsContext, size: CGSize, range: Range<Int>) {
        guard series.macdLine.count == series.count else { return }
        var maxAbs = 0.0
        var priceLevel = 0.0
        func take(_ v: Double?) {
            if let v, v.isFinite { maxAbs = max(maxAbs, abs(v)) }
        }
        for i in frame.visibleRange {
            take(series.macdLine[i])
            take(series.macdSignal[i])
            take(series.macdHistogram[i])
            let c = series.close[i]
            if c.isFinite { priceLevel = max(priceLevel, abs(c)) }
        }
        // A floor RELATIVE to the price: MACD is in price units, so an absolute floor flattened
        // every coin priced below ~1e-6, while no floor let a flat series' rounding noise
        // (~1e-15 of the price) fill the pane edge to edge. All zero → a stand-in scale (0/0
        // would be NaN coordinates and blank the pane).
        let noiseFloor = priceLevel * 1e-12
        if !(maxAbs > noiseFloor) { maxAbs = noiseFloor > 0 ? noiseFloor : 1 }
        let midY = size.height / 2

        var zero = Path()
        zero.move(to: CGPoint(x: 0, y: midY)); zero.addLine(to: CGPoint(x: size.width, y: midY))
        context.stroke(zero, with: .color(AppColors.chartGridline), style: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))

        func yPos(_ v: Double) -> CGFloat { midY - CGFloat(v / maxAbs) * midY * 0.9 }
        let line = ChartCoordinateSystem(width: size.width, height: size.height, yRange: .unit, frame: frame)
        let bucket = line.bucket
        let barWidth = max(1, line.slotWidth * CGFloat(bucket) * 0.5)

        var pos = Path(), neg = Path()
        ChartPath.forEachBucket(range, bucket: bucket) { lo, hi in
            // A column shows its most extreme histogram value.
            var pick: Double? = nil
            for j in lo..<hi {
                if let v = series.macdHistogram[j], v.isFinite, abs(v) > abs(pick ?? 0) || pick == nil { pick = v }
            }
            guard let v = pick else { return }
            let x = bucket > 1 ? (line.xPosition(for: lo) + line.xPosition(for: hi - 1)) / 2 : line.xPosition(for: lo)
            let y = yPos(v)
            let rect = CGRect(x: x - barWidth / 2, y: min(midY, y), width: barWidth, height: abs(y - midY))
            if v >= 0 { pos.addRect(rect) } else { neg.addRect(rect) }
        }
        context.fill(pos, with: .color(AppColors.bullish.opacity(0.4)))
        context.fill(neg, with: .color(AppColors.bearish.opacity(0.4)))

        // MACD and signal lines. Apple's `.green` is 2.22:1 on white and does not adapt —
        // these were bare system hues once; they stay app tokens.
        let scale = MACDScale(midY: midY, maxAbs: maxAbs, frame: frame, width: size.width)
        context.stroke(scale.path(range: range) { series.macdLine[$0] }, with: .color(AppColors.gainGraphic),
                       style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
        context.stroke(scale.path(range: range) { series.macdSignal[$0] }, with: .color(AppColors.lossGraphic),
                       style: StrokeStyle(lineWidth: 1, lineCap: .round, lineJoin: .round))
    }
}

/// MACD's symmetric y mapping expressed as a `ChartYRange`, so its lines go through the same
/// decimated `ChartPath.line` as every other series.
private struct MACDScale {
    let coord: ChartCoordinateSystem

    init(midY: CGFloat, maxAbs: Double, frame: ChartFrame, width: CGFloat) {
        // The histogram maps y(v) = h/2 − (v/maxAbs)·(h/2)·0.9. `ChartYRange` maps
        // y(v) = h − ((v − lo)/(hi − lo))·0.9h − 0.05h, and with [lo, hi] = [−maxAbs, maxAbs]
        // that is exactly 0.5h − 0.45h·v/maxAbs — the same line.
        coord = ChartCoordinateSystem(
            width: width,
            height: midY * 2,
            yRange: ChartYRange(minValue: -maxAbs, maxValue: maxAbs),
            frame: frame
        )
    }

    func path(range: Range<Int>, value: (Int) -> Double?) -> Path {
        ChartPath.line(range: range, coord: coord, value: value)
    }
}
