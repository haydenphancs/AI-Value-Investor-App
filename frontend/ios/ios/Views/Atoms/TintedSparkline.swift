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

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            let pts = SparklineGeometry.normalizedPoints(points, in: geo.size)

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
                            Color.white.opacity(0.18),
                            style: StrokeStyle(lineWidth: 1, dash: [4, 3])
                        )
                    }

                    // The line itself.
                    Path { path in
                        guard let first = pts.first else { return }
                        path.move(to: first)
                        pts.dropFirst().forEach { path.addLine(to: $0) }
                    }
                    .stroke(
                        color,
                        style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round)
                    )

                    // Optional end-point dot.
                    if showEndDot, let last = pts.last {
                        Circle()
                            .fill(color)
                            .frame(width: 6, height: 6)
                            .position(last)
                    }
                }
            }
        }
    }
}

#Preview {
    VStack(spacing: 24) {
        TintedSparkline(points: [4, 8, 6, 14, 12, 22, 20, 31], color: AppColors.bullish)
            .frame(width: 100, height: 18)
        TintedSparkline(points: [28, 24, 26, 18, 16, 10, 12, 4], color: AppColors.bearish,
                        showBaseline: true, showEndDot: true)
            .frame(width: 104, height: 48)
        TintedSparkline(points: [26, 22, 24, 18, 20, 14, 16, 11], color: AppColors.neutral,
                        showEndDot: true)
            .frame(width: 104, height: 48)
    }
    .padding()
    .background(AppColors.cardBackground)
}
