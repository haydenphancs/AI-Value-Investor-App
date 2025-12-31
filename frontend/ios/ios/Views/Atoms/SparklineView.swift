//
//  SparklineView.swift
//  ios
//
//  Atom: Mini chart for market tickers
//

import SwiftUI

struct SparklineView: View {
    let data: [Double]
    let isPositive: Bool

    private var lineColor: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    private var gradientColor: LinearGradient {
        LinearGradient(
            colors: [lineColor.opacity(0.3), lineColor.opacity(0.0)],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    var body: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let height = geometry.size.height

            if data.count > 1 {
                let minValue = data.min() ?? 0
                let maxValue = data.max() ?? 1
                let range = maxValue - minValue
                let stepX = width / CGFloat(data.count - 1)

                ZStack {
                    // Gradient fill
                    Path { path in
                        path.move(to: CGPoint(x: 0, y: height))

                        for (index, value) in data.enumerated() {
                            let x = CGFloat(index) * stepX
                            let y = height - (CGFloat((value - minValue) / range) * height)
                            if index == 0 {
                                path.addLine(to: CGPoint(x: x, y: y))
                            } else {
                                path.addLine(to: CGPoint(x: x, y: y))
                            }
                        }

                        path.addLine(to: CGPoint(x: width, y: height))
                        path.closeSubpath()
                    }
                    .fill(gradientColor)

                    // Line
                    Path { path in
                        for (index, value) in data.enumerated() {
                            let x = CGFloat(index) * stepX
                            let y = height - (CGFloat((value - minValue) / range) * height)
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
    }
}

#Preview {
    VStack(spacing: 20) {
        SparklineView(
            data: [100, 102, 98, 105, 103, 108, 110, 107, 112, 115],
            isPositive: true
        )
        .frame(width: 80, height: 30)

        SparklineView(
            data: [115, 112, 108, 105, 110, 103, 100, 98, 95, 92],
            isPositive: false
        )
        .frame(width: 80, height: 30)
    }
    .padding()
    .background(AppColors.cardBackground)
}
