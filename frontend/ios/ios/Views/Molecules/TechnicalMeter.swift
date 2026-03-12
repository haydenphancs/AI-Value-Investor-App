//
//  TechnicalMeter.swift
//  ios
//
//  Technical analysis meter with gauge and signal indicators
//

import SwiftUI

struct TechnicalMeter: View {
    let technicalData: TechnicalAnalysisData
    @State private var selectedPeriod: TechnicalPeriod = .daily

    enum TechnicalPeriod {
        case daily
        case weekly
    }

    // Active signal based on selected period
    private var activeSignal: TechnicalSignal {
        switch selectedPeriod {
        case .daily: return technicalData.dailySignal.signal
        case .weekly: return technicalData.weeklySignal.signal
        }
    }

    // Gauge value derived from the selected period's indicator ratio
    private var activeGaugeValue: Double {
        let result: TechnicalIndicatorResult
        switch selectedPeriod {
        case .daily: result = technicalData.dailySignal
        case .weekly: result = technicalData.weeklySignal
        }
        guard result.totalIndicators > 0 else { return 0.5 }
        return Double(result.matchingIndicators) / Double(result.totalIndicators)
    }

    // Map gauge value to 1-5 level
    private var activeGaugeLevel: Int {
        switch activeGaugeValue {
        case 0..<0.2: return 1
        case 0.2..<0.4: return 2
        case 0.4..<0.6: return 3
        case 0.6..<0.8: return 4
        default: return 5
        }
    }

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Header
            VStack(spacing: AppSpacing.xs) {
                Text("Technical Meter")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text("Aggregated technical indicators")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            // Signal badges row (toggleable)
            HStack(spacing: AppSpacing.md) {
                TechnicalSignalBadge(
                    title: "Daily Signal",
                    signal: technicalData.dailySignal.signal,
                    indicatorCount: technicalData.dailySignal.formattedCount,
                    isSelected: selectedPeriod == .daily
                )
                .onTapGesture {
                    withAnimation(.easeInOut(duration: 0.6)) {
                        selectedPeriod = .daily
                    }
                }

                TechnicalSignalBadge(
                    title: "Weekly Signal",
                    signal: technicalData.weeklySignal.signal,
                    indicatorCount: technicalData.weeklySignal.formattedCount,
                    isSelected: selectedPeriod == .weekly
                )
                .onTapGesture {
                    withAnimation(.easeInOut(duration: 0.6)) {
                        selectedPeriod = .weekly
                    }
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            // Gauge — driven by selected period
            TechnicalGauge(
                signal: activeSignal,
                gaugeValue: activeGaugeValue
            )

            // Level indicators — driven by selected period
            TechnicalLevelIndicatorsRow(
                activeLevel: activeGaugeLevel,
                labels: ["Strong\nSell", "Sell", "Neutral", "Buy", "Strong\nBuy"]
            )
        }
    }
}

// MARK: - Technical Gauge (Semi-circle style with 5 zones)
struct TechnicalGauge: View {
    let signal: TechnicalSignal
    let gaugeValue: Double

    @State private var animatedValue: Double = 0.5  // Start at center (neutral)
    @State private var hasAppeared: Bool = false

    private var needleAngle: Double {
        // Convert value (0-1) to angle (-180 to 0 degrees)
        return -180 + (animatedValue * 180)
    }

    var body: some View {
        ZStack {
            // Background arc
            TechnicalArc()
                .stroke(AppColors.cardBackgroundLight, lineWidth: 24)
                .frame(width: 220, height: 110)

            // 5 distinct zone arcs
            TechnicalGaugeZones(size: 220)

            // Animated needle (needleLength = 220/2 - 35 = 75)
            NeedleShape(angle: needleAngle, needleLength: 75)
                .stroke(AppColors.textPrimary, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                .frame(width: 220, height: 110)

            // Center circle
            Circle()
                .fill(AppColors.textPrimary)
                .frame(width: 10, height: 10)
                .offset(y: 55) // Position at gauge center (110 height / 2)

            // Center display
            VStack(spacing: 2) {
                Text(signal.rawValue)
                    .font(AppTypography.titleCompact)
                    .fontWeight(.bold)
                    .foregroundColor(signal.color)
                    .contentTransition(.numericText())
            }
            .offset(y: 20)
        }
        .frame(width: 220, height: 130)
        .onAppear {
            guard !hasAppeared else { return }
            hasAppeared = true
            // Sweep needle from center to actual value on first appear
            withAnimation(.easeInOut(duration: 0.8).delay(0.2)) {
                animatedValue = gaugeValue
            }
        }
        .onChange(of: gaugeValue) {
            // Animate needle when toggling Daily/Weekly
            withAnimation(.easeInOut(duration: 0.6)) {
                animatedValue = gaugeValue
            }
        }
    }
}

// MARK: - Technical Gauge Zones (5 colored segments)
struct TechnicalGaugeZones: View {
    let size: CGFloat

    var body: some View {


            ZStack {
                // Strong sell – red
                TechnicalArcSegment(startAngle: 36, endAngle: 0)
                    .stroke(Color(hex: "991B1B"), style: StrokeStyle(lineWidth: 24, lineCap: .butt))
                    .frame(width: size, height: size / 2)
                // Sell – light red
                TechnicalArcSegment(startAngle: 72, endAngle: 36)
                    .stroke(Color.red.opacity(0.8), style: StrokeStyle(lineWidth: 24, lineCap: .butt))
                    .frame(width: size, height: size / 2)

                // Neutral – Yellow
                TechnicalArcSegment(startAngle: 108, endAngle: 72)
                    .stroke(Color.yellow, style: StrokeStyle(lineWidth: 24, lineCap: .butt))
                    .frame(width: size, height: size / 2)

                // Buy – Light Green
                TechnicalArcSegment(startAngle: 144, endAngle: 108)
                    .stroke(Color.green.opacity(0.8), style: StrokeStyle(lineWidth: 24, lineCap: .butt))
                    .frame(width: size, height: size / 2)

                // Strong Buy – Green
                TechnicalArcSegment(startAngle: 180, endAngle: 144)

                    .stroke(Color(hex: "15803D"), style: StrokeStyle(lineWidth: 24, lineCap: .butt))
                    .frame(width: size, height: size / 2)

            }

    }
}

// MARK: - Technical Arc Segment Shape
struct TechnicalArcSegment: Shape {
    let startAngle: Double
    let endAngle: Double

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let center = CGPoint(x: rect.midX, y: rect.maxY)
        let radius = min(rect.width, rect.height * 2) / 2 - 12

        path.addArc(
            center: center,
            radius: radius,
            startAngle: .degrees(startAngle + 180),
            endAngle: .degrees(endAngle + 180),
            clockwise: true
        )

        return path
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

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        TechnicalMeter(technicalData: TechnicalAnalysisData.sampleData)
            .padding()
    }
}
