//
//  LineChartRenderer.swift
//  ios
//
//  Line chart with gradient fill and current price dot
//

import SwiftUI

struct LineChartRenderer: View {
    let closes: [Double]
    let coord: ChartCoordinateSystem
    let lineColor: Color
    var extendedHoursIndices: Set<Int> = []

    private var hasExtendedHours: Bool { !extendedHoursIndices.isEmpty }

    var body: some View {
        ZStack {
            if hasExtendedHours {
                // Regular hours segments (full opacity)
                segmentedPath(extended: false)
                    .stroke(lineColor, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))

                // Extended hours segments (muted)
                segmentedPath(extended: true)
                    .stroke(lineColor.opacity(0.35), style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round, dash: [4, 2]))
            } else {
                // Standard full line
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
                .stroke(lineColor, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
            }

            // Current price dot
            if let lastValue = closes.last {
                let x = coord.xPosition(for: closes.count - 1)
                let y = coord.yPosition(for: lastValue)
                Circle()
                    .fill(lineColor)
                    .frame(width: 8, height: 8)
                    .position(x: x, y: y)
            }
        }
    }

    /// Build a path containing only segments where points match the given extended hours state.
    /// Each segment connects consecutive points of the same type, plus one overlap point
    /// at boundaries to keep the line visually connected.
    private func segmentedPath(extended: Bool) -> Path {
        Path { path in
            var inSegment = false
            for (index, value) in closes.enumerated() {
                let isExt = extendedHoursIndices.contains(index)
                let belongs = isExt == extended

                let x = coord.xPosition(for: index)
                let y = coord.yPosition(for: value)

                if belongs {
                    if !inSegment {
                        // Start new segment — connect from previous point if available
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
                        // End segment — draw to this transition point
                        path.addLine(to: CGPoint(x: x, y: y))
                        inSegment = false
                    }
                }
            }
        }
    }
}
