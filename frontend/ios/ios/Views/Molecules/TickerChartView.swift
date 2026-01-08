//
//  TickerChartView.swift
//  ios
//
//  Molecule: Price chart with time range selector for Ticker Detail
//

import SwiftUI

struct TickerChartView: View {
    let chartData: [Double]
    let isPositive: Bool
    @Binding var selectedRange: ChartTimeRange

    private var lineColor: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    private var gradientColor: LinearGradient {
        LinearGradient(
            colors: [lineColor.opacity(0.3), lineColor.opacity(0.05)],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // Chart
            GeometryReader { geometry in
                let width = geometry.size.width
                let height = geometry.size.height

                if chartData.count > 1 {
                    let minValue = chartData.min() ?? 0
                    let maxValue = chartData.max() ?? 1
                    let range = max(maxValue - minValue, 0.01)
                    let stepX = width / CGFloat(chartData.count - 1)

                    ZStack {
                        // Grid lines (horizontal)
                        VStack(spacing: 0) {
                            ForEach(0..<4) { index in
                                Rectangle()
                                    .fill(AppColors.cardBackgroundLight.opacity(0.5))
                                    .frame(height: 1)
                                if index < 3 {
                                    Spacer()
                                }
                            }
                        }

                        // Gradient fill under line
                        Path { path in
                            path.move(to: CGPoint(x: 0, y: height))

                            for (index, value) in chartData.enumerated() {
                                let x = CGFloat(index) * stepX
                                let y = height - (CGFloat((value - minValue) / range) * height * 0.9) - height * 0.05
                                path.addLine(to: CGPoint(x: x, y: y))
                            }

                            path.addLine(to: CGPoint(x: width, y: height))
                            path.closeSubpath()
                        }
                        .fill(gradientColor)

                        // Line chart
                        Path { path in
                            for (index, value) in chartData.enumerated() {
                                let x = CGFloat(index) * stepX
                                let y = height - (CGFloat((value - minValue) / range) * height * 0.9) - height * 0.05

                                if index == 0 {
                                    path.move(to: CGPoint(x: x, y: y))
                                } else {
                                    path.addLine(to: CGPoint(x: x, y: y))
                                }
                            }
                        }
                        .stroke(lineColor, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))

                        // Current price dot at end
                        if let lastValue = chartData.last {
                            let x = width
                            let y = height - (CGFloat((lastValue - minValue) / range) * height * 0.9) - height * 0.05

                            Circle()
                                .fill(lineColor)
                                .frame(width: 8, height: 8)
                                .position(x: x, y: y)
                        }
                    }
                }
            }
            .frame(height: 140)
            .padding(.horizontal, AppSpacing.lg)

            // Time range selector
            HStack(spacing: 2) {
                ForEach(ChartTimeRange.allCases, id: \.rawValue) { range in
                    TimeRangeButton(range: range, isSelected: selectedRange == range) {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            selectedRange = range
                        }
                    }
                }

                Spacer()

                // Chart type icon
                Button(action: {
                    // Toggle chart type
                }) {
                    Image(systemName: "chart.xyaxis.line")
                        .font(.system(size: 16, weight: .regular))
                        .foregroundColor(AppColors.textMuted)
                        .padding(.leading, 4)
                        .padding(.trailing, 8)
                }
                .buttonStyle(PlainButtonStyle())

                // Settings icon
                Button(action: {
                    // Open chart settings
                }) {
                    Image(systemName: "gearshape")
                        .font(.system(size: 16, weight: .regular))
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selectedRange: ChartTimeRange = .threeMonths

        var body: some View {
            TickerChartView(
                chartData: [165, 168, 170, 172, 169, 174, 171, 175, 173, 178, 176, 180, 177, 182, 178],
                isPositive: true,
                selectedRange: $selectedRange
            )
            .padding(.vertical)
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
