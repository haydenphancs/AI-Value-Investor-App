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

    private var areaGradient: LinearGradient {
        LinearGradient(
            colors: [lineColor.opacity(0.4), lineColor.opacity(0.05)],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    var body: some View {
        ZStack {
            // Filled area
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

            // Top edge line
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
