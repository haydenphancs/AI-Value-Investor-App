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
    let gaugeType: GaugeType
    
    enum GaugeType {
        case sentiment  // 3 zones: Bullish (0-30), Neutral (31-70), Bearish (71-100)
        case technical  // 5 zones: Strong Sell, Sell, Hold, Buy, Strong Buy
    }

    init(
        value: Double,
        displayValue: String,
        label: String,
        labelColor: Color,
        showLabels: Bool = true,
        size: CGFloat = 200,
        gaugeType: GaugeType = .technical
    ) {
        self.value = min(max(value, 0), 1) // Clamp between 0 and 1
        self.displayValue = displayValue
        self.label = label
        self.labelColor = labelColor
        self.showLabels = showLabels
        self.size = size
        self.gaugeType = gaugeType
    }

    // Gradient colors for sentiment gauge (3 zones)
    private let sentimentGradientColors: [Color] = [
        Color(hex: "22C55E"),  // Green (Bullish) - 0-30
        Color(hex: "22C55E"),  // Green
        Color(hex: "9CA3AF"),  // Grey (Neutral) - 31-70
        Color(hex: "9CA3AF"),  // Grey
        Color(hex: "EF4444"),  // Red (Bearish) - 71-100
        Color(hex: "EF4444")   // Red
    ]
    
    // Gradient colors for technical gauge (5 zones)
    private let technicalGradientColors: [Color] = [
        Color(hex: "991B1B"), // Dark red (Strong Sell)
        Color(hex: "EF4444"), // Red (Sell)
        Color(hex: "F59E0B"), // Yellow (Hold)
        Color(hex: "4ADE80"), // Light green (Buy)
        Color(hex: "22C55E")  // Green (Strong Buy)
    ]
    
    private var gradientColors: [Color] {
        gaugeType == .sentiment ? sentimentGradientColors : technicalGradientColors
    }

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
