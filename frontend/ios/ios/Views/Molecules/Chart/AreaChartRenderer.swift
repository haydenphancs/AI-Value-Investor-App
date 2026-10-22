//
//  AreaChartRenderer.swift
//  ios
//
//  Area chart with prominent gradient fill
//

import SwiftUI

struct AreaChartRenderer: View {
    let series: ChartSeries
    let frame: ChartFrame
    let yRange: ChartYRange
    let lineColor: Color
    /// Mute the pre/after-hours runs (1D with extended bars only).
    var showExtendedHours: Bool = false

    var body: some View {
        Canvas { context, size in
            let range = frame.drawRange
            guard !range.isEmpty, range.upperBound <= series.count else { return }
            let coord = ChartCoordinateSystem(width: size.width, height: size.height, yRange: yRange, frame: frame)
            let top = CGPoint(x: 0, y: 0), bottom = CGPoint(x: 0, y: size.height)
            let fill = Gradient(colors: [lineColor.opacity(0.4), lineColor.opacity(0.05)])
            let mutedFill = Gradient(colors: [lineColor.opacity(0.15), lineColor.opacity(0.02)])

            if showExtendedHours {
                for (run, extended) in runs(range) {
                    let area = areaPath(run, coord: coord, height: size.height)
                    context.fill(area, with: .linearGradient(extended ? mutedFill : fill, startPoint: top, endPoint: bottom))
                }
                let (regular, extended) = ChartSegments.split(range: range, series: series, coord: coord)
                context.stroke(regular, with: .color(lineColor), style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
                context.stroke(extended, with: .color(lineColor.opacity(0.35)),
                               style: StrokeStyle(lineWidth: 1, lineCap: .round, lineJoin: .round, dash: [4, 2]))
            } else {
                let line = ChartPath.line(range: range, coord: coord) { series.close[$0] }
                var area = line
                let lastX = coord.xPosition(for: range.upperBound - 1)
                let firstX = coord.xPosition(for: range.lowerBound)
                area.addLine(to: CGPoint(x: lastX, y: size.height))
                area.addLine(to: CGPoint(x: firstX, y: size.height))
                area.closeSubpath()
                context.fill(area, with: .linearGradient(fill, startPoint: top, endPoint: bottom))
                context.stroke(line, with: .color(lineColor), style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
            }
        }
    }

    /// Contiguous regular / extended runs, each widened by one bar at a boundary so the
    /// fills meet without a seam.
    private func runs(_ range: Range<Int>) -> [(Range<Int>, Bool)] {
        var out: [(Range<Int>, Bool)] = []
        var i = range.lowerBound
        while i < range.upperBound {
            let ext = series.isExtended[i]
            let start = i
            while i < range.upperBound && series.isExtended[i] == ext { i += 1 }
            let lo = max(range.lowerBound, start - (start > range.lowerBound ? 1 : 0))
            let hi = min(range.upperBound, i + (i < range.upperBound ? 1 : 0))
            out.append((lo..<hi, ext))
        }
        return out
    }

    private func areaPath(_ run: Range<Int>, coord: ChartCoordinateSystem, height: CGFloat) -> Path {
        var path = Path()
        guard let first = run.first, let last = run.last else { return path }
        path.move(to: CGPoint(x: coord.xPosition(for: first), y: height))
        for j in run {
            path.addLine(to: CGPoint(x: coord.xPosition(for: j), y: coord.yPosition(for: series.close[j])))
        }
        path.addLine(to: CGPoint(x: coord.xPosition(for: last), y: height))
        path.closeSubpath()
        return path
    }
}
