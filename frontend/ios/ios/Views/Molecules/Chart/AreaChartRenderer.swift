//
//  AreaChartRenderer.swift
//  ios
//
//  Area chart with prominent gradient fill
//

import SwiftUI

struct AreaChartRenderer: View {
    let closes: [Double]
    let coord: ChartCoordinateSystem
    let lineColor: Color
    var extendedHoursIndices: Set<Int> = []

    private var hasExtendedHours: Bool { !extendedHoursIndices.isEmpty }

    private var areaGradient: LinearGradient {
        LinearGradient(
            colors: [lineColor.opacity(0.4), lineColor.opacity(0.05)],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    private var extendedAreaGradient: LinearGradient {
        LinearGradient(
            colors: [lineColor.opacity(0.15), lineColor.opacity(0.02)],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    var body: some View {
        ZStack {
            if hasExtendedHours {
                // Regular hours filled area
                filledAreaPath(extended: false)
                    .fill(areaGradient)

                // Extended hours filled area (muted)
                filledAreaPath(extended: true)
                    .fill(extendedAreaGradient)

                // Regular hours top edge
                segmentedLinePath(extended: false)
                    .stroke(lineColor, style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))

                // Extended hours top edge (muted + dashed)
                segmentedLinePath(extended: true)
                    .stroke(lineColor.opacity(0.35), style: StrokeStyle(lineWidth: 1, lineCap: .round, lineJoin: .round, dash: [4, 2]))
            } else {
                // Standard filled area
                Path { path in
                    path.move(to: CGPoint(x: 0, y: coord.height))
                    for (index, value) in closes.enumerated() {
                        let x = coord.xPosition(for: index)
                        let y = coord.yPosition(for: value)
                        path.addLine(to: CGPoint(x: x, y: y))
                    }
                    path.addLine(to: CGPoint(x: coord.xPosition(for: closes.count - 1), y: coord.height))
                    path.closeSubpath()
                }
                .fill(areaGradient)

                // Standard top edge line
                Path { path in
                    for (index, value) in closes.enumerated() {
                        let x = coord.xPosition(for: index)
                        let y = coord.yPosition(for: value)
                        if index == 0 {
                            path.move(to: CGPoint(x: x, y: y))
                        } else {
                            path.addLine(to: CGPoint(x: x, y: y))
                        }
                    }
                }
                .stroke(lineColor, style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
            }
        }
    }

    /// Build a closed area path for contiguous runs of the given type
    private func filledAreaPath(extended: Bool) -> Path {
        Path { path in
            var i = 0
            while i < closes.count {
                let isExt = extendedHoursIndices.contains(i)
                if isExt == extended {
                    let segStart = i
                    while i < closes.count && (extendedHoursIndices.contains(i) == extended) {
                        i += 1
                    }
                    let segEnd = i - 1

                    // Extend by one point at boundaries for seamless fill
                    let drawStart = max(0, segStart - (segStart > 0 ? 1 : 0))
                    let drawEnd = min(closes.count - 1, segEnd + (segEnd < closes.count - 1 ? 1 : 0))

                    let xStart = coord.xPosition(for: drawStart)
                    let xEnd = coord.xPosition(for: drawEnd)

                    path.move(to: CGPoint(x: xStart, y: coord.height))
                    for j in drawStart...drawEnd {
                        path.addLine(to: CGPoint(x: coord.xPosition(for: j), y: coord.yPosition(for: closes[j])))
                    }
                    path.addLine(to: CGPoint(x: xEnd, y: coord.height))
                    path.closeSubpath()
                } else {
                    i += 1
                }
            }
        }
    }

    /// Build a line path for segments of the given type
    private func segmentedLinePath(extended: Bool) -> Path {
        Path { path in
            var inSegment = false
            for (index, value) in closes.enumerated() {
                let isExt = extendedHoursIndices.contains(index)
                let belongs = isExt == extended
                let x = coord.xPosition(for: index)
                let y = coord.yPosition(for: value)

                if belongs {
                    if !inSegment {
                        if index > 0 {
                            let prevX = coord.xPosition(for: index - 1)
                            let prevY = coord.yPosition(for: closes[index - 1])
                            path.move(to: CGPoint(x: prevX, y: prevY))
                            path.addLine(to: CGPoint(x: x, y: y))
                        } else {
                            path.move(to: CGPoint(x: x, y: y))
                        }
                        inSegment = true
                    } else {
                        path.addLine(to: CGPoint(x: x, y: y))
                    }
                } else {
                    if inSegment {
                        path.addLine(to: CGPoint(x: x, y: y))
                        inSegment = false
                    }
                }
            }
        }
    }
}
