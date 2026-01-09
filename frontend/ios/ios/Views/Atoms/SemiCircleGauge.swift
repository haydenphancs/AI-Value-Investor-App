//
//  SemiCircleGauge.swift
//  ios
//
//  Semi-circle gauge for displaying sentiment/technical analysis scores
//

import SwiftUI

struct SemiCircleGauge: View {
    let value: Double // 0.0 to 1.0
    let displayValue: String
    let label: String
    let labelColor: Color
    let showLabels: Bool
    let size: CGFloat

    init(
        value: Double,
        displayValue: String,
        label: String,
        labelColor: Color,
        showLabels: Bool = true,
        size: CGFloat = 200
    ) {
        self.value = min(max(value, 0), 1) // Clamp between 0 and 1
        self.displayValue = displayValue
        self.label = label
        self.labelColor = labelColor
        self.showLabels = showLabels
        self.size = size
    }

    // Gradient colors for the gauge arc
    private let gradientColors: [Color] = [
        Color(hex: "EF4444"), // Red (bearish)
        Color(hex: "F97316"), // Orange
        Color(hex: "F59E0B"), // Yellow (neutral)
        Color(hex: "84CC16"), // Lime
        Color(hex: "22C55E")  // Green (bullish)
    ]

    private var needleAngle: Double {
        // Convert value (0-1) to angle (-180 to 0 degrees)
        // 0 = -180°, 0.5 = -90°, 1 = 0°
        return -180 + (value * 180)
    }

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            ZStack {
                // Background arc
                SemiCircleArc()
                    .stroke(AppColors.cardBackgroundLight, lineWidth: 20)
                    .frame(width: size, height: size / 2)

                // Gradient arc
                SemiCircleArc()
                    .stroke(
                        AngularGradient(
                            colors: gradientColors,
                            center: .bottom,
                            startAngle: .degrees(180),
                            endAngle: .degrees(0)
                        ),
                        style: StrokeStyle(lineWidth: 20, lineCap: .round)
                    )
                    .frame(width: size, height: size / 2)

                // Needle
                GaugeNeedle(angle: needleAngle)
                    .frame(width: size, height: size / 2)

                // Center display
                VStack(spacing: 2) {
                    Text(displayValue)
                        .font(.system(size: size * 0.2, weight: .bold, design: .rounded))
                        .foregroundColor(labelColor)

                    Text(label)
                        .font(AppTypography.headline)
                        .foregroundColor(labelColor)
                }
                .offset(y: size * 0.1)

                // Scale labels
                if showLabels {
                    HStack {
                        Text("0")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        Spacer()

                        Text("50")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        Spacer()

                        Text("100")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }
                    .frame(width: size + 20)
                    .offset(y: size * 0.35)
                }
            }
            .frame(width: size, height: size * 0.7)
        }
    }
}

// MARK: - Semi Circle Arc Shape
struct SemiCircleArc: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let center = CGPoint(x: rect.midX, y: rect.maxY)
        let radius = min(rect.width, rect.height * 2) / 2 - 10

        path.addArc(
            center: center,
            radius: radius,
            startAngle: .degrees(180),
            endAngle: .degrees(0),
            clockwise: false
        )

        return path
    }
}

// MARK: - Gauge Needle
struct GaugeNeedle: View {
    let angle: Double

    var body: some View {
        GeometryReader { geometry in
            let center = CGPoint(x: geometry.size.width / 2, y: geometry.size.height)
            let needleLength = min(geometry.size.width, geometry.size.height * 2) / 2 - 30

            Path { path in
                path.move(to: center)
                let endX = center.x + needleLength * cos(angle * .pi / 180)
                let endY = center.y + needleLength * sin(angle * .pi / 180)
                path.addLine(to: CGPoint(x: endX, y: endY))
            }
            .stroke(AppColors.textPrimary, style: StrokeStyle(lineWidth: 3, lineCap: .round))

            // Center circle
            Circle()
                .fill(AppColors.textPrimary)
                .frame(width: 12, height: 12)
                .position(center)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xxxl) {
            SemiCircleGauge(
                value: 0.24,
                displayValue: "24",
                label: "Bearish",
                labelColor: AppColors.bearish
            )

            SemiCircleGauge(
                value: 0.72,
                displayValue: "Buy",
                label: "12 of 18 indicators",
                labelColor: AppColors.bullish
            )
        }
    }
}
