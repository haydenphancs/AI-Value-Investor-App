//
//  LineChartRenderer.swift
//  ios
//
//  Line chart with gradient fill and current price dot
//

import SwiftUI

/// ⚠️ DELIBERATELY has no Differentiate Without Color cue, and this is the reasoning
/// rather than an oversight — `test_ios_a11y_parity.py` exempts it by name.
///
/// The obvious cue for a line is a dash, and DASH IS ALREADY TAKEN here: line 27 uses
/// `dash: [4, 2]` to mark extended-hours segments. A second meaning on the same channel
/// makes both unreadable, which is worse than the problem being solved. Unlike
/// `CandlestickChartRenderer` — where extended hours could move onto opacity because it
/// was already half-encoded there — this renderer has no free channel: opacity is also
/// taken (0.35 on the same line), and width carries the same distinction.
///
/// It is acceptable because this is the DETAIL chart, and the direction it encodes is
/// stated in text directly above it: the price header renders a signed change and an
/// arrow. The chart is not the only carrier of the claim.
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
