//
//  MiniLineChart.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct MiniLineChart: View {
    let dataPoints: [Double]
    let isPositive: Bool
    let lineWidth: CGFloat

    init(dataPoints: [Double], isPositive: Bool, lineWidth: CGFloat = 2) {
        self.dataPoints = dataPoints
        self.isPositive = isPositive
        self.lineWidth = lineWidth
    }

    var body: some View {
        GeometryReader { geometry in
            Path { path in
                guard dataPoints.count > 1 else { return }

                let maxValue = dataPoints.max() ?? 1
                let minValue = dataPoints.min() ?? 0
                let range = maxValue - minValue
                let height = geometry.size.height
                let width = geometry.size.width
                let stepX = width / CGFloat(dataPoints.count - 1)

                for (index, point) in dataPoints.enumerated() {
                    let x = CGFloat(index) * stepX
                    let normalizedY = range > 0 ? (point - minValue) / range : 0.5
                    let y = height - (CGFloat(normalizedY) * height)

                    if index == 0 {
                        path.move(to: CGPoint(x: x, y: y))
                    } else {
                        path.addLine(to: CGPoint(x: x, y: y))
                    }
                }
            }
            .stroke(
                isPositive ? AppColors.chartPositive : AppColors.chartNegative,
                style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round)
            )
        }
    }
}

#Preview {
    VStack(spacing: 20) {
        MiniLineChart(dataPoints: [0.3, 0.5, 0.4, 0.7, 0.9], isPositive: true)
            .frame(height: 40)

        MiniLineChart(dataPoints: [0.9, 0.7, 0.5, 0.3, 0.2], isPositive: false)
            .frame(height: 40)
    }
    .padding()
    .background(AppColors.background)
}
