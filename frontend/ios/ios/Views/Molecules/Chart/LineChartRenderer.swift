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

    var body: some View {
        ZStack {
            // Line
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
}
