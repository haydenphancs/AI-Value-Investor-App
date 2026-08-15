//
//  SparklineView.swift
//  ios
//
//  Atom: Mini chart for market tickers
//  Shows dual-colored line (green above / red below reference),
//  gradient fills, dotted reference line, and end-point dot.
//

import SwiftUI

struct SparklineView: View {
    let data: [Double]
    let isPositive: Bool
    /// Baseline for the dotted reference line + green/red split. When set (e.g.
    /// the previous trading day's close) the chart anchors to it instead of the
    /// first data point — matching Apple Stocks / Robinhood.
    var referencePrice: Double? = nil
    /// Where `data` sits inside its trading session, as fractions of the width.
    ///
    /// Server-supplied (`spark_from` / `spark_to`). The series is a bare `[Double]`
    /// with no timestamps, so without this pair the points spread edge to edge and
    /// a 10:15 chart was pixel-identical to a completed day — the card read as
    /// "the market already closed" beside a live, moving price, and it contradicted
    /// the asset-detail 1D chart, which has always left the untraded remainder of
    /// the session empty.
    ///
    /// Defaults are the full width, i.e. the behaviour before spans existed, so a
    /// caller with no session information (previews, `MarketTicker`) is unchanged.
    var spanFrom: Double = 0
    var spanTo: Double = 1

    // 2.5pt radius = a 5pt dot (was 6pt). It only has to mark where the series
    // ends; on a 22pt Market Pulse tile the larger one read as a bullet. Kept in
    // step with `TintedSparkline.endDotRadius` — both appear on the Home screen,
    // so a size difference reads as a bug. It also drives the plot inset below,
    // so shrinking it hands the line back a little vertical amplitude.
    private let dotRadius: CGFloat = 2.5
    private let lineWidth: CGFloat = 1.5

    /// The area split is ALREADY a positional cue — green sits above the dashed reference
    /// line, red below — so under Differentiate Without Color only the two marks that are
    /// purely chromatic need help: the below-reference LINE (dashed) and the end DOT
    /// (hollow). Dashing the above-reference line too would make the dash decorative.
    @Environment(\.differentiateWithoutColor) private var differentiate

    var body: some View {
        GeometryReader { geometry in
            if data.count > 1 {
                // Baseline = previous trading day's close when provided, else the
                // first point. Included in the scale so the dotted line is always
                // visible even if price never traded back to it.
                let referenceValue = referencePrice ?? data[0]
                let minValue = min(data.min() ?? 0, referenceValue)
                let maxValue = max(data.max() ?? 1, referenceValue)
                let range = max(maxValue - minValue, .ulpOfOne)
                // Reserve room for the end dot, which is centred ON the last
                // point. The y-scale sends the series minimum to the very bottom
                // and the maximum to the very top, so without this a session that
                // closes at its own low or high renders a HALF dot flattened
                // against the canvas edge — measured on-device at 9px instead of
                // 18px on the S&P tile, which closed at its low, while the Dow
                // (which did not) drew a full circle.
                let plot = SparklineGeometry.plotRect(in: geometry.size, markInset: dotRadius)
                // The series occupies only the elapsed slice of its session; the
                // rest of the card stays empty. `clampedSpan` is shared with
                // `SparklineGeometry` so the two primitives can't disagree about
                // what an unusable span means (both fall back to full width).
                let span = SparklineGeometry.clampedSpan(from: spanFrom, to: spanTo)
                let originX = plot.minX + plot.width * span.from
                let stepX = (plot.width * (span.to - span.from)) / CGFloat(data.count - 1)

                let points: [CGPoint] = data.enumerated().map { index, value in
                    let x = originX + CGFloat(index) * stepX
                    let y = plot.maxY - (CGFloat((value - minValue) / range) * plot.height)
                    return CGPoint(x: x, y: y)
                }

                // Same mapping as the data, so the dashed line stays where the
                // price level actually sits relative to the plotted series.
                let referenceY = plot.maxY - (CGFloat((referenceValue - minValue) / range) * plot.height)

                let lastPoint = points.last!
                let endIsAbove = data.last! >= referenceValue

                Canvas { context, size in
                    // --- Green gradient fill (above reference) ---
                    let aboveClip = Path(CGRect(x: 0, y: 0, width: size.width, height: referenceY))
                    let fillShape = buildFillPath(points: points, baseY: referenceY)

                    context.drawLayer { ctx in
                        ctx.clip(to: aboveClip)
                        let greenGradient = Gradient(colors: [
                            AppColors.bullish.opacity(0.25),
                            AppColors.bullish.opacity(0.0)
                        ])
                        ctx.fill(
                            fillShape,
                            with: .linearGradient(
                                greenGradient,
                                startPoint: CGPoint(x: 0, y: 0),
                                endPoint: CGPoint(x: 0, y: referenceY)
                            )
                        )
                    }

                    // --- Red gradient fill (below reference) ---
                    let belowClip = Path(CGRect(x: 0, y: referenceY, width: size.width, height: size.height - referenceY))

                    context.drawLayer { ctx in
                        ctx.clip(to: belowClip)
                        let redGradient = Gradient(colors: [
                            AppColors.bearish.opacity(0.0),
                            AppColors.bearish.opacity(0.25)
                        ])
                        ctx.fill(
                            fillShape,
                            with: .linearGradient(
                                redGradient,
                                startPoint: CGPoint(x: 0, y: referenceY),
                                endPoint: CGPoint(x: 0, y: size.height)
                            )
                        )
                    }

                    // --- Dotted reference line ---
                    // Spans the FULL width on purpose, even when the series
                    // covers only part of the session: this is a price LEVEL
                    // (yesterday's close), not data. Trimming it to `span` would
                    // make the untraded remainder of the day look like a gap in
                    // the axis rather than a day that hasn't happened yet.
                    var refLine = Path()
                    refLine.move(to: CGPoint(x: 0, y: referenceY))
                    refLine.addLine(to: CGPoint(x: size.width, y: referenceY))
                    context.stroke(
                        refLine,
                        with: .color(AppColors.chartCrosshair),
                        style: StrokeStyle(lineWidth: 1, dash: [4, 3])
                    )

                    // --- Green line (above reference) ---
                    let line = buildLinePath(points: points)

                    context.drawLayer { ctx in
                        ctx.clip(to: aboveClip)
                        ctx.stroke(
                            line,
                            with: .color(AppColors.bullish),
                            style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round)
                        )
                    }

                    // --- Red line (below reference), dashed under DWC ---
                    context.drawLayer { ctx in
                        ctx.clip(to: belowClip)
                        ctx.stroke(
                            line,
                            with: .color(AppColors.bearish),
                            style: AppSentiment.strokeStyle(isPositive: false,
                                                            differentiate: differentiate,
                                                            lineWidth: lineWidth,
                                                            dash: [3, 2])
                        )
                    }

                    // --- End dot: filled when above the reference, hollow when below ---
                    let dotRect = CGRect(
                        x: lastPoint.x - dotRadius,
                        y: lastPoint.y - dotRadius,
                        width: dotRadius * 2,
                        height: dotRadius * 2
                    )
                    let dotPath = Path(ellipseIn: dotRect)
                    let dotColor = endIsAbove ? AppColors.bullish : AppColors.bearish
                    if differentiate && !endIsAbove {
                        context.stroke(dotPath, with: .color(dotColor), lineWidth: 1.5)
                    } else {
                        context.fill(dotPath, with: .color(dotColor))
                    }
                }
            }
        }
    }

    // MARK: - Path Builders

    private func buildLinePath(points: [CGPoint]) -> Path {
        Path { path in
            for (index, point) in points.enumerated() {
                if index == 0 {
                    path.move(to: point)
                } else {
                    path.addLine(to: point)
                }
            }
        }
    }

    private func buildFillPath(points: [CGPoint], baseY: CGFloat) -> Path {
        Path { path in
            guard let first = points.first, let last = points.last else { return }
            path.move(to: CGPoint(x: first.x, y: baseY))
            for point in points {
                path.addLine(to: point)
            }
            path.addLine(to: CGPoint(x: last.x, y: baseY))
            path.closeSubpath()
        }
    }
}

#Preview {
    VStack(spacing: 20) {
        SparklineView(
            data: [100, 98, 102, 105, 103, 108, 110, 107, 112, 115],
            isPositive: true
        )
        .frame(width: 120, height: 40)

        SparklineView(
            data: [115, 112, 108, 105, 110, 103, 100, 98, 95, 92],
            isPositive: false
        )
        .frame(width: 120, height: 40)

        SparklineView(
            data: [100, 95, 92, 98, 96, 102, 105, 99, 103, 108],
            isPositive: true
        )
        .frame(width: 120, height: 40)

        // Mid-morning: ~2/5 of the session traded, the rest of the card empty.
        SparklineView(
            data: [100, 98, 102, 105, 103, 108],
            isPositive: true,
            referencePrice: 100,
            spanFrom: 0,
            spanTo: 0.43
        )
        .frame(width: 120, height: 40)
    }
    .padding()
    .background(AppColors.cardBackground)
}
