//
//  SemiCircleGauge.swift
//  ios
//
//  Semi-circle gauge for displaying sentiment/technical analysis scores
//

import SwiftUI

// MARK: - Gauge Type
enum GaugeType {
    case sentiment   // 3 zones: Bearish (0-30), Neutral (31-70), Bullish (71-100)
    case technical   // 5 zones: Strong Sell, Sell, Hold, Buy, Strong Buy

    var zoneColors: [Color] {
        switch self {
        case .sentiment:
            // Red (Bearish) -> Grey (Neutral) -> Green (Bullish)
            return [
                AppColors.bearish,           // 0-30: Red (Bearish)
                AppColors.bearish,
                Color(hex: "6B7280"),         // 31-70: Grey (Neutral)
                Color(hex: "6B7280"),
                AppColors.bullish            // 71-100: Green (Bullish)
            ]
        case .technical:
            // 5 distinct zones
            return [
                Color(hex: "991B1B"),         // Strong Sell - Dark Red
                AppColors.bearish,            // Sell - Red
                AppColors.neutral,            // Hold - Yellow
                Color(hex: "4ADE80"),         // Buy - Light Green
                AppColors.bullish             // Strong Buy - Green
            ]
        }
    }
}

struct SemiCircleGauge: View {
    let value: Double // 0.0 to 1.0
    let displayValue: String
    let label: String
    let labelColor: Color
    let gaugeType: GaugeType
    let showLabels: Bool
    let size: CGFloat

    init(
        value: Double,
        displayValue: String,
        label: String,
        labelColor: Color,
        gaugeType: GaugeType = .technical,
        showLabels: Bool = true,
        size: CGFloat = 200
    ) {
        self.value = min(max(value, 0), 1) // Clamp between 0 and 1
        self.displayValue = displayValue
        self.label = label
        self.labelColor = labelColor
        self.gaugeType = gaugeType
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

                // Colored zone arcs
                gaugeArcs

                // Needle
                GaugeNeedle(angle: needleAngle)
                    .frame(width: size, height: size / 2)

                // Center display
                VStack(spacing: 2) {
                    Text(displayValue)
                        .font(.system(size: size * 0.2, weight: .bold, design: .rounded))
                        .foregroundColor(labelColor)
                        .contentTransition(.numericText())

                    Text(label)
                        .font(AppTypography.headingSmall)
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
            .frame(width: size, height: size * 0.6)
        }
    }

    @ViewBuilder
    private var gaugeArcs: some View {
        switch gaugeType {
        case .sentiment:
            SentimentGaugeArcs(size: size)
        case .technical:
            TechnicalGaugeArcs(size: size)
        }
    }
}

// MARK: - Sentiment Gauge Arcs (3 zones with smooth AngularGradient)
// Single arc with AngularGradient — no segments, no z-ordering issues
struct SentimentGaugeArcs: View {
    let size: CGFloat
    private let lineWidth: CGFloat = 20

    var body: some View {
        // AngularGradient sweeps from left (0°=red) to right (180°=green)
        // Location = ourAngle / 180
        SemiCircleArc()
            .stroke(
                AngularGradient(
                    stops: [
                        .init(color: AppColors.bearish, location: 0.0),
                        .init(color: AppColors.bearish, location: 0.25),
                        .init(color: Color(hex: "6B7280"), location: 0.32),
                        .init(color: Color(hex: "6B7280"), location: 0.68),
                        .init(color: AppColors.bullish, location: 0.75),
                        .init(color: AppColors.bullish, location: 1.0),
                    ],
                    center: UnitPoint(x: 0.5, y: 1.0),
                    startAngle: .degrees(-180),
                    endAngle: .degrees(0)
                ),
                style: StrokeStyle(lineWidth: lineWidth, lineCap: .round)
            )
            .frame(width: size, height: size / 2)
    }
}

// MARK: - Technical Gauge Arcs (5 zones with smooth AngularGradient)
struct TechnicalGaugeArcs: View {
    let size: CGFloat
    private let lineWidth: CGFloat = 20

    var body: some View {
        SemiCircleArc()
            .stroke(
                AngularGradient(
                    stops: [
                        .init(color: Color(hex: "991B1B"), location: 0.0),
                        .init(color: Color(hex: "991B1B"), location: 0.12),
                        .init(color: AppColors.bearish, location: 0.28),
                        .init(color: AppColors.bearish, location: 0.32),
                        .init(color: AppColors.neutral, location: 0.48),
                        .init(color: AppColors.neutral, location: 0.52),
                        .init(color: Color(hex: "4ADE80"), location: 0.68),
                        .init(color: Color(hex: "4ADE80"), location: 0.72),
                        .init(color: AppColors.bullish, location: 0.88),
                        .init(color: AppColors.bullish, location: 1.0),
                    ],
                    center: UnitPoint(x: 0.5, y: 1.0),
                    startAngle: .degrees(-180),
                    endAngle: .degrees(0)
                ),
                style: StrokeStyle(lineWidth: lineWidth, lineCap: .round)
            )
            .frame(width: size, height: size / 2)
    }
}

// MARK: - Semi Circle Arc Segment Shape
struct SemiCircleArcSegment: Shape {
    let startAngle: Double
    let endAngle: Double

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let center = CGPoint(x: rect.midX, y: rect.maxY)
        let radius = min(rect.width, rect.height * 2) / 2 - 10

        path.addArc(
            center: center,
            radius: radius,
            startAngle: .degrees(startAngle + 180),  // Adjust for coordinate system
            endAngle: .degrees(endAngle + 180),
            clockwise: true
        )

        return path
    }
}

// MARK: - Semi Circle Arc Shape (full arc for background)
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
    var angle: Double

    var body: some View {
        GeometryReader { geometry in
            let center = CGPoint(x: geometry.size.width / 2, y: geometry.size.height)
            let needleLength = min(geometry.size.width, geometry.size.height * 2) / 2 - 30

            NeedleShape(angle: angle, needleLength: needleLength)
                .stroke(AppColors.textPrimary, style: StrokeStyle(lineWidth: 3, lineCap: .round))

            // Center circle
            Circle()
                .fill(AppColors.textPrimary)
                .frame(width: 12, height: 12)
                .position(center)
        }
    }
}

// MARK: - Animatable Needle Shape
struct NeedleShape: Shape {
    var angle: Double
    var needleLength: CGFloat

    var animatableData: Double {
        get { angle }
        set { angle = newValue }
    }

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let center = CGPoint(x: rect.midX, y: rect.maxY)
        path.move(to: center)
        let endX = center.x + needleLength * cos(angle * .pi / 180)
        let endY = center.y + needleLength * sin(angle * .pi / 180)
        path.addLine(to: CGPoint(x: endX, y: endY))
        return path
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xxxl) {
            // Sentiment Gauge - Bearish (low value = bearish)
            VStack {
                Text("Sentiment Gauge (Bearish - Score 24)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                SemiCircleGauge(
                    value: 0.24,
                    displayValue: "24",
                    label: "Bearish",
                    labelColor: AppColors.bearish,
                    gaugeType: .sentiment
                )
            }

            // Technical Gauge - Buy
            VStack {
                Text("Technical Gauge (Buy)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                SemiCircleGauge(
                    value: 0.72,
                    displayValue: "Buy",
                    label: "12 of 18 indicators",
                    labelColor: AppColors.bullish,
                    gaugeType: .technical
                )
            }
        }
    }
}
