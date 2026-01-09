//
//  TechnicalMeter.swift
//  ios
//
//  Technical analysis meter with gauge and signal indicators
//

import SwiftUI

struct TechnicalMeter: View {
    let technicalData: TechnicalAnalysisData

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Header
            VStack(spacing: AppSpacing.xs) {
                Text("Technical Meter")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Text("Aggregated technical indicators")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            // Signal badges row
            HStack(spacing: AppSpacing.xl) {
                TechnicalSignalBadge(
                    title: "Daily Signal",
                    signal: technicalData.dailySignal.signal,
                    indicatorCount: technicalData.dailySignal.formattedCount
                )

                TechnicalSignalBadge(
                    title: "Weekly Signal",
                    signal: technicalData.weeklySignal.signal,
                    indicatorCount: technicalData.weeklySignal.formattedCount
                )
            }
            .padding(.horizontal, AppSpacing.lg)

            // Gauge
            TechnicalGauge(
                signal: technicalData.overallSignal,
                gaugeValue: technicalData.gaugeValue
            )

            // Level indicators
            TechnicalLevelIndicatorsRow(
                activeLevel: technicalData.gaugeLevel,
                labels: ["Strong\nSell", "Sell", "Hold", "Buy", "Strong\nBuy"]
            )
        }
    }
}

// MARK: - Technical Gauge (Semi-circle style)
struct TechnicalGauge: View {
    let signal: TechnicalSignal
    let gaugeValue: Double

    private let gradientColors: [Color] = [
        Color(hex: "991B1B"), // Dark red (Strong Sell)
        Color(hex: "EF4444"), // Red (Sell)
        Color(hex: "F59E0B"), // Yellow (Hold)
        Color(hex: "4ADE80"), // Light green (Buy)
        Color(hex: "22C55E")  // Green (Strong Buy)
    ]

    private var needleAngle: Double {
        // Convert value (0-1) to angle (-180 to 0 degrees)
        return -180 + (gaugeValue * 180)
    }

    var body: some View {
        ZStack {
            // Background arc
            TechnicalArc()
                .stroke(AppColors.cardBackgroundLight, lineWidth: 24)
                .frame(width: 220, height: 110)

            // Gradient arc with 5 distinct zones
            TechnicalArc()
                .stroke(
                    AngularGradient(
                        gradient: Gradient(colors: gradientColors),
                        center: .bottom,
                        startAngle: .degrees(180),
                        endAngle: .degrees(0)
                    ),
                    style: StrokeStyle(lineWidth: 24, lineCap: .round)
                )
                .frame(width: 220, height: 110)

            // Needle
            TechnicalNeedle(angle: needleAngle)
                .frame(width: 220, height: 110)

            // Center display
            VStack(spacing: 2) {
                Text(signal.rawValue)
                    .font(AppTypography.title2)
                    .fontWeight(.bold)
                    .foregroundColor(signal.color)
            }
            .offset(y: 20)
        }
        .frame(width: 220, height: 130)
    }
}

// MARK: - Technical Arc Shape
struct TechnicalArc: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let center = CGPoint(x: rect.midX, y: rect.maxY)
        let radius = min(rect.width, rect.height * 2) / 2 - 12

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

// MARK: - Technical Needle
struct TechnicalNeedle: View {
    let angle: Double

    var body: some View {
        GeometryReader { geometry in
            let center = CGPoint(x: geometry.size.width / 2, y: geometry.size.height)
            let needleLength = min(geometry.size.width, geometry.size.height * 2) / 2 - 35

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
                .frame(width: 10, height: 10)
                .position(center)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        TechnicalMeter(technicalData: TechnicalAnalysisData.sampleData)
            .padding()
    }
}
