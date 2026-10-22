//
//  MainChartCanvas.swift
//  ios
//
//  Orchestrator for the main price chart area with overlays
//
//  Draws one `ChartFrame` of a precomputed `ChartSeries`. `Equatable`, and `.equatable()`
//  at the call site: a crosshair scrub (which changes nothing here) no longer re-runs this
//  body or re-rasterises the drawing group — only a new window, new data or a new setting
//  does. Every drawing input is in `==`; leaving one out would freeze it on screen.
//

import SwiftUI

struct MainChartCanvas: View, Equatable {
    /// Only read to bust the `.drawingGroup()` raster cache — see below.
    @Environment(\.colorScheme) private var colorScheme
    /// Read for the dash cue AND to bust the same raster. This canvas rasterises FIVE
    /// sub-renderers, so a stale texture here would freeze the cue across the whole
    /// chart — and it would only show on a LIVE toggle, never on a cold launch.
    @Environment(\.differentiateWithoutColor) private var differentiate

    let series: ChartSeries
    let frame: ChartFrame
    /// Shared with the crosshair, so its dot sits on this line.
    let yRange: ChartYRange
    let isPositive: Bool
    let chartType: ChartType
    let overlays: [TechnicalIndicatorType]
    var showExtendedHours: Bool = false
    /// Earnings and dividend dates to render as "E" markers
    var chartEventDates: ChartEventDates? = nil
    /// When true, use time-based X positioning (1D intraday)
    var useIntradayTimeMapping: Bool = false
    /// The session that mapping is measured against. Defaults to the equity bell;
    /// crypto and commodity futures need the whole calendar day, or every
    /// overnight bar clamps to 0 and piles up on the left edge.
    var sessionWindow: TradingDayHelper.SessionWindow = .regular
    /// Reference price for the dashed baseline (e.g. prior-day close on 1D).
    /// Folded into the y-range so it's always visible. Falls back to the current
    /// price when nil.
    var baselineClose: Double? = nil

    static func == (lhs: MainChartCanvas, rhs: MainChartCanvas) -> Bool {
        lhs.series.revision == rhs.series.revision
            && lhs.series.count == rhs.series.count
            && lhs.frame == rhs.frame
            && lhs.yRange == rhs.yRange
            && lhs.isPositive == rhs.isPositive
            && lhs.chartType == rhs.chartType
            && lhs.overlays == rhs.overlays
            && lhs.showExtendedHours == rhs.showExtendedHours
            && lhs.chartEventDates == rhs.chartEventDates
            && lhs.useIntradayTimeMapping == rhs.useIntradayTimeMapping
            && lhs.sessionWindow == rhs.sessionWindow
            && lhs.baselineClose == rhs.baselineClose
    }

    private var lineColor: Color {
        isPositive ? AppColors.gainGraphic : AppColors.lossGraphic
    }

    /// Extended-hours shading only exists on the 1D session view, where every bar is drawn.
    private var shadesExtendedHours: Bool {
        showExtendedHours && useIntradayTimeMapping
    }

    var body: some View {
        if frame.drawRange.count > 1 || (useIntradayTimeMapping && frame.drawRange.count == 1) {
            ZStack {
                // Extended-hours shading, market boundaries, the baseline and the event
                // markers: one canvas, no per-bar views.
                Canvas { context, size in
                    let coord = ChartCoordinateSystem(width: size.width, height: size.height, yRange: yRange, frame: frame)
                    drawSessionShading(context: context, coord: coord, size: size)
                    drawBaseline(context: context, coord: coord, size: size)
                }

                // Main chart
                switch chartType {
                case .line:
                    LineChartRenderer(series: series, frame: frame, yRange: yRange, lineColor: lineColor, showExtendedHours: shadesExtendedHours)
                case .candle:
                    CandlestickChartRenderer(series: series, frame: frame, yRange: yRange, showExtendedHours: shadesExtendedHours)
                case .area:
                    AreaChartRenderer(series: series, frame: frame, yRange: yRange, lineColor: lineColor, showExtendedHours: shadesExtendedHours)
                case .bar:
                    OHLCBarChartRenderer(series: series, frame: frame, yRange: yRange, showExtendedHours: shadesExtendedHours)
                }

                // Overlays (MA lines, Bollinger Bands)
                if !overlays.isEmpty {
                    OverlayRenderer(overlays: overlays, series: series, frame: frame, yRange: yRange)
                }

                // Earnings markers
                if let events = chartEventDates, !events.earningsDates.isEmpty {
                    ChartEventMarkers(series: series, frame: frame, earningsDates: events.earningsDates)
                }
            }
            .drawingGroup()
            .accessibilityIgnoresInvertColors()
            .id("\(colorScheme)-\(differentiate)")
        } else {
            Text("No chart data")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func drawSessionShading(context: GraphicsContext, coord: ChartCoordinateSystem, size: CGSize) {
        guard shadesExtendedHours else { return }
        let range = frame.drawRange
        guard !range.isEmpty, range.upperBound <= series.count else { return }
        let half = coord.slotWidth / 2
        var shading = Path()
        var boundaries = Path()
        var i = range.lowerBound
        while i < range.upperBound {
            if i > range.lowerBound, series.isExtended[i] != series.isExtended[i - 1] {
                let x = coord.xPosition(for: i)
                boundaries.move(to: CGPoint(x: x, y: 0))
                boundaries.addLine(to: CGPoint(x: x, y: size.height))
            }
            if series.isExtended[i] {
                let start = i
                while i < range.upperBound && series.isExtended[i] { i += 1 }
                let xStart = coord.xPosition(for: start) - half
                let xEnd = coord.xPosition(for: i - 1) + half
                shading.addRect(CGRect(x: xStart, y: 0, width: max(0, xEnd - xStart), height: size.height))
            } else {
                i += 1
            }
        }
        context.fill(shading, with: .color(AppColors.textMuted.opacity(0.06)))
        context.stroke(boundaries, with: .color(AppColors.textMuted.opacity(0.4)), style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
    }

    /// Baseline horizontal dash line — prior-day close on 1D (else the start-of-range
    /// reference). Falls back to the current price only when no baseline is supplied.
    private func drawBaseline(context: GraphicsContext, coord: ChartCoordinateSystem, size: CGSize) {
        guard let baseline = baselineClose ?? series.close.last else { return }
        let y = coord.yPosition(for: baseline)
        var path = Path()
        path.move(to: CGPoint(x: 0, y: y))
        path.addLine(to: CGPoint(x: size.width, y: y))
        context.stroke(path, with: .color(AppColors.chartGridline), style: StrokeStyle(lineWidth: 0.5, dash: [4, 3]))
    }
}

// MARK: - Earnings Event Markers

private struct ChartEventMarkers: View {
    let series: ChartSeries
    let frame: ChartFrame
    let earningsDates: [String]

    var body: some View {
        Canvas { context, canvasSize in
            let range = frame.drawRange
            guard range.count > 1, range.upperBound <= series.count else { return }
            let firstDate = String(series.dates[range.lowerBound].prefix(10))
            let lastDate = String(series.dates[range.upperBound - 1].prefix(10))
            let coord = ChartCoordinateSystem(width: canvasSize.width, height: canvasSize.height, yRange: .unit, frame: frame)

            // Each event lands on the first bar dated on or after it (daily: that day;
            // weekly/monthly: the bar containing it). Binary search, not a scan per event.
            var xs: [CGFloat] = []
            for event in earningsDates.sorted() where event >= firstDate && event <= lastDate {
                let idx = series.lowerBound(event)
                guard idx < range.upperBound, idx >= range.lowerBound else { continue }
                xs.append(coord.xPosition(for: idx))
            }
            // Zoomed out over decades, quarterly markers merge into one band. When the
            // typical gap between consecutive earnings is under a marker's width the row says
            // nothing, so none are drawn; otherwise never two closer than a marker's width.
            let markerSize: CGFloat = 16
            if xs.count >= 3 {
                let gaps = zip(xs, xs.dropFirst()).map { $1 - $0 }.sorted()
                if gaps[gaps.count / 2] < markerSize * 1.25 { return }
            }
            var lastDrawn = -CGFloat.infinity
            let markerY = canvasSize.height - 14
            for x in xs where x - lastDrawn >= markerSize {
                drawMarker(context: context, text: "E", x: x, y: markerY, size: markerSize)
                lastDrawn = x
            }
        }
    }

    private func drawMarker(context: GraphicsContext, text: String, x: CGFloat, y: CGFloat, size: CGFloat) {
        let rect = CGRect(x: x - size / 2, y: y - size / 2, width: size, height: size)
        context.fill(RoundedRectangle(cornerRadius: 3).path(in: rect), with: .color(AppColors.cautionFill))
        let resolvedText = context.resolve(
            Text(text)
                .font(.system(size: 10, weight: .bold))
                .foregroundColor(AppColors.textOnAccent)
        )
        context.draw(resolvedText, at: CGPoint(x: x, y: y))
    }
}
