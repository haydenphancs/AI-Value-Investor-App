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

    private let dotRadius: CGFloat = 3
    private let lineWidth: CGFloat = 1.5

    var body: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let height = geometry.size.height

            if data.count > 1 {
                // Baseline = previous trading day's close when provided, else the
                // first point. Included in the scale so the dotted line is always
                // visible even if price never traded back to it.
                let referenceValue = referencePrice ?? data[0]
                let minValue = min(data.min() ?? 0, referenceValue)
                let maxValue = max(data.max() ?? 1, referenceValue)
                let range = max(maxValue - minValue, .ulpOfOne)
                let stepX = width / CGFloat(data.count - 1)

                let points: [CGPoint] = data.enumerated().map { index, value in
                    let x = CGFloat(index) * stepX
                    let y = height - (CGFloat((value - minValue) / range) * height)
                    return CGPoint(x: x, y: y)
                }

                let referenceY = height - (CGFloat((referenceValue - minValue) / range) * height)

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
                    var refLine = Path()
                    refLine.move(to: CGPoint(x: 0, y: referenceY))
                    refLine.addLine(to: CGPoint(x: size.width, y: referenceY))
                    context.stroke(
                        refLine,
                        with: .color(.white.opacity(0.3)),
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

                    // --- Red line (below reference) ---
                    context.drawLayer { ctx in
                        ctx.clip(to: belowClip)
                        ctx.stroke(
                            line,
                            with: .color(AppColors.bearish),
                            style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round)
                        )
                    }

                    // --- End dot ---
                    let dotRect = CGRect(
                        x: lastPoint.x - dotRadius,
                        y: lastPoint.y - dotRadius,
                        width: dotRadius * 2,
                        height: dotRadius * 2
                    )
                    let dotPath = Path(ellipseIn: dotRect)
                    context.fill(dotPath, with: .color(endIsAbove ? AppColors.bullish : AppColors.bearish))
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
    }
    .padding()
    .background(AppColors.cardBackground)
}
