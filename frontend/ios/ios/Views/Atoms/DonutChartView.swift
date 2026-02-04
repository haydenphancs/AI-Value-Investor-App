//
//  DonutChartView.swift
//  ios
//
//  Atom: A reusable donut/ring chart component for displaying
//  proportional data like sector allocation or portfolio distribution.
//

import SwiftUI

// MARK: - Donut Chart Segment
struct DonutChartSegment: Identifiable {
    let id: String
    let value: Double
    let color: Color
    let label: String

    init(id: String = UUID().uuidString, value: Double, color: Color, label: String) {
        self.id = id
        self.value = value
        self.color = color
        self.label = label
    }
}

// MARK: - Donut Chart View
struct DonutChartView: View {
    let segments: [DonutChartSegment]
    let lineWidth: CGFloat
    let showLabels: Bool

    private var total: Double {
        segments.reduce(0) { $0 + $1.value }
    }

    init(
        segments: [DonutChartSegment],
        lineWidth: CGFloat = 24,
        showLabels: Bool = true
    ) {
        self.segments = segments
        self.lineWidth = lineWidth
        self.showLabels = showLabels
    }

    var body: some View {
        HStack(spacing: AppSpacing.xl) {
            // Donut Chart
            ZStack {
                // Background ring
                Circle()
                    .stroke(
                        AppColors.cardBackgroundLight,
                        lineWidth: lineWidth
                    )

                // Segment rings
                ForEach(Array(segments.enumerated()), id: \.element.id) { index, segment in
                    DonutSegmentShape(
                        startAngle: startAngle(for: index),
                        endAngle: endAngle(for: index)
                    )
                    .stroke(
                        segment.color,
                        style: StrokeStyle(
                            lineWidth: lineWidth,
                            lineCap: .butt
                        )
                    )
                }
            }
            .rotationEffect(.degrees(-90))
            .frame(width: 100, height: 100)
            .padding(lineWidth / 2)

            // Legend
            if showLabels {
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    ForEach(segments) { segment in
                        DonutChartLegendItem(
                            color: segment.color,
                            label: segment.label,
                            value: segment.value / total * 100
                        )
                    }
                }
            }
        }
    }

    // MARK: - Angle Calculations

    private func startAngle(for index: Int) -> Angle {
        let precedingTotal = segments.prefix(index).reduce(0) { $0 + $1.value }
        return Angle(degrees: (precedingTotal / total) * 360)
    }

    private func endAngle(for index: Int) -> Angle {
        let includingTotal = segments.prefix(index + 1).reduce(0) { $0 + $1.value }
        return Angle(degrees: (includingTotal / total) * 360)
    }
}

// MARK: - Donut Segment Shape
struct DonutSegmentShape: Shape {
    let startAngle: Angle
    let endAngle: Angle

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let center = CGPoint(x: rect.midX, y: rect.midY)
        let radius = min(rect.width, rect.height) / 2

        path.addArc(
            center: center,
            radius: radius,
            startAngle: startAngle,
            endAngle: endAngle,
            clockwise: false
        )

        return path
    }
}

// MARK: - Legend Item
struct DonutChartLegendItem: View {
    let color: Color
    let label: String
    let value: Double

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)

            Text(label)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)

            Text(String(format: "%.0f%%", value))
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xxl) {
            // Standard donut chart
            DonutChartView(
                segments: [
                    DonutChartSegment(value: 42, color: AppColors.primaryBlue, label: "Tech"),
                    DonutChartSegment(value: 31, color: AppColors.bullish, label: "Finance"),
                    DonutChartSegment(value: 27, color: AppColors.alertOrange, label: "Energy")
                ]
            )
            .padding()
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)

            // Smaller donut chart
            DonutChartView(
                segments: [
                    DonutChartSegment(value: 65, color: AppColors.primaryBlue, label: "Tech"),
                    DonutChartSegment(value: 20, color: AppColors.bullish, label: "Healthcare"),
                    DonutChartSegment(value: 15, color: AppColors.alertOrange, label: "Finance")
                ],
                lineWidth: 16
            )
            .padding()
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .padding()
    }
}
