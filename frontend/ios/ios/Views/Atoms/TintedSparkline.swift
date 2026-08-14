//
//  TintedSparkline.swift
//  ios
//
//  Atom: a single-tone sparkline (line + soft area fill) tinted by one color.
//
//  Distinct from `SparklineView`, which renders a dual-tone green-above /
//  red-below split anchored to a reference price. The Caydex Home design uses a
//  single accent tone per chart (green / red / amber), an optional dashed
//  mid-line, and an optional end dot — so this is its own primitive rather than
//  an override of SparklineView. Hand-drawn with `Path`, matching the codebase's
//  chart approach.
//

import SwiftUI

struct TintedSparkline: View {
    /// Series where a LARGER value sits HIGHER on the chart.
    let points: [Double]
    let color: Color
    var fillOpacity: Double = 0.16
    var showBaseline: Bool = false
    var showEndDot: Bool = false
    var lineWidth: CGFloat = 2
    /// Direction this series represents, for the non-colour cue under Differentiate
    /// Without Color. `nil` = the tint carries no sentiment (a neutral series), so no
    /// cue is owed. Callers pass `bullish`/`bearish` as `color`, and the COLOUR was the
    /// only thing distinguishing them — hence this parameter rather than inferring from
    /// the series, whose slope is not the same claim as the caller's verdict.
    var isPositive: Bool? = nil
    /// Where `points` sits inside its trading session, as fractions of the width.
    /// Defaults to the full width, so a caller with no session information (a
    /// non-intraday series, a preview) is unchanged. See `SparklineGeometry`.
    var spanFrom: Double = 0
    var spanTo: Double = 1

    /// Radius of the end dot. One constant so the plot inset that reserves room
    /// for it can never drift from the circle actually drawn.
    ///
    /// Kept equal to `SparklineView.dotRadius` — the scanner card and the Market
    /// Pulse tiles sit on the same screen, so a size difference reads as a bug.
    private static let endDotRadius: CGFloat = 2.5

    @Environment(\.differentiateWithoutColor) private var differentiate

    /// Dash the negative line only. `isPositive == nil` keeps the solid style.
    private var lineStyle: StrokeStyle {
        AppSentiment.strokeStyle(isPositive: isPositive ?? true,
                                 differentiate: differentiate && isPositive != nil,
                                 lineWidth: lineWidth)
    }

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            // The end dot is a 6pt circle centred on the last point, so the plot
            // has to give up its radius or a series closing at its own high/low
            // renders a half dot flattened against the canvas edge. Without a dot
            // only the stroke needs room.
            let pts = SparklineGeometry.normalizedPoints(
                points, in: geo.size,
                spanFrom: spanFrom, spanTo: spanTo,
                markInset: showEndDot ? Self.endDotRadius : lineWidth / 2
            )

            if pts.count > 1 {
                ZStack {
                    // Soft area fill under the line.
                    Path { path in
                        guard let first = pts.first, let last = pts.last else { return }
                        path.move(to: CGPoint(x: first.x, y: h))
                        pts.forEach { path.addLine(to: $0) }
                        path.addLine(to: CGPoint(x: last.x, y: h))
                        path.closeSubpath()
                    }
                    .fill(
                        LinearGradient(
                            colors: [color.opacity(fillOpacity), color.opacity(0)],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )

                    // Optional dashed mid-line (matches the design's y=18/36 guide).
                    if showBaseline {
                        Path { path in
                            path.move(to: CGPoint(x: 0, y: h / 2))
                            path.addLine(to: CGPoint(x: w, y: h / 2))
                        }
                        .stroke(
                            AppColors.chartGridline,
                            style: StrokeStyle(lineWidth: 1, dash: [4, 3])
                        )
                    }

                    // The line itself.
                    Path { path in
                        guard let first = pts.first else { return }
                        path.move(to: first)
                        pts.dropFirst().forEach { path.addLine(to: $0) }
                    }
                    .stroke(color, style: lineStyle)

                    // Optional end-point dot. Under DWC it becomes a hollow ring for a
                    // negative series — same trick as a hollow candle body, and it reads
                    // at 6pt where a dash would not.
                    if showEndDot, let last = pts.last {
                        Group {
                            if differentiate, isPositive == false {
                                Circle().strokeBorder(color, lineWidth: 2)
                            } else {
                                Circle().fill(color)
                            }
                        }
                        .frame(width: Self.endDotRadius * 2, height: Self.endDotRadius * 2)
                        .position(last)
                    }
                }
            }
        }
    }
}

#Preview("Default") {
    TintedSparklinePreviewBody()
        .differentiateWithoutColor(false)
}

/// The whole point of the override: SwiftUI's own DWC key is get-only, so without
/// `.differentiateWithoutColor(_:)` there is no way to see this state in a canvas.
#Preview("Differentiate Without Color") {
    TintedSparklinePreviewBody()
        .differentiateWithoutColor(true)
}

private struct TintedSparklinePreviewBody: View {
    var body: some View {
        VStack(spacing: 24) {
            TintedSparkline(points: [4, 8, 6, 14, 12, 22, 20, 31], color: AppColors.bullish,
                            showEndDot: true, isPositive: true)
                .frame(width: 104, height: 48)
            TintedSparkline(points: [28, 24, 26, 18, 16, 10, 12, 4], color: AppColors.bearish,
                            showBaseline: true, showEndDot: true, isPositive: false)
                .frame(width: 104, height: 48)
            // Neutral: no sentiment, so no cue is owed and none appears.
            TintedSparkline(points: [26, 22, 24, 18, 20, 14, 16, 11], color: AppColors.neutral,
                            showEndDot: true)
                .frame(width: 104, height: 48)
        }
        .padding()
        .background(AppColors.cardBackground)
    }
}
