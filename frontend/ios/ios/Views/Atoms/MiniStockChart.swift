//
//  MiniStockChart.swift
//  ios
//
//  Atom: Mini chart for stock performance display
//

import SwiftUI

struct MiniStockChart: View {
    /// Only read to bust the `.drawingGroup()` raster cache — see below.
    @Environment(\.colorScheme) private var colorScheme
    /// Read for the dash cue AND, like `colorScheme`, to bust the raster: a
    /// `.drawingGroup()` is a Metal texture that is not re-rendered just because an
    /// accessibility setting changed, so it must participate in the `.id()`.
    @Environment(\.differentiateWithoutColor) private var differentiate

    let data: [Double]
    let isPositive: Bool
    var height: CGFloat = 80

    private var normalizedData: [CGFloat] {
        guard let minVal = data.min(), let maxVal = data.max(), maxVal > minVal else {
            return data.map { _ in CGFloat(0.5) }
        }
        return data.map { CGFloat(($0 - minVal) / (maxVal - minVal)) }
    }

    var body: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let stepX = width / CGFloat(max(normalizedData.count - 1, 1))

            ZStack {
                // Gradient fill under the line
                Path { path in
                    guard !normalizedData.isEmpty else { return }

                    path.move(to: CGPoint(x: 0, y: height))

                    for (index, value) in normalizedData.enumerated() {
                        let x = CGFloat(index) * stepX
                        let y = height - (value * height)
                        path.addLine(to: CGPoint(x: x, y: y))
                    }

                    path.addLine(to: CGPoint(x: width, y: height))
                    path.closeSubpath()
                }
                .fill(
                    LinearGradient(
                        colors: [
                            (isPositive ? AppColors.bullish : AppColors.bearish).opacity(0.3),
                            (isPositive ? AppColors.bullish : AppColors.bearish).opacity(0.0)
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )

                // Line
                Path { path in
                    guard !normalizedData.isEmpty else { return }

                    for (index, value) in normalizedData.enumerated() {
                        let x = CGFloat(index) * stepX
                        let y = height - (value * height)

                        if index == 0 {
                            path.move(to: CGPoint(x: x, y: y))
                        } else {
                            path.addLine(to: CGPoint(x: x, y: y))
                        }
                    }
                }
                .stroke(
                    isPositive ? AppColors.bullish : AppColors.bearish,
                    style: AppSentiment.strokeStyle(isPositive: isPositive,
                                                    differentiate: differentiate,
                                                    lineWidth: 2)
                )
            }
            // Flatten path rendering into a single Metal texture
            .drawingGroup()
            .accessibilityIgnoresInvertColors()
            .id("\(colorScheme)-\(differentiate)")
        }
        .frame(height: height)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        MiniStockChart(data: [220, 225, 218, 230, 235, 228, 240, 238, 245, 242], isPositive: true)
        MiniStockChart(data: [250, 245, 240, 235, 230, 228, 225, 220], isPositive: false)
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
}
