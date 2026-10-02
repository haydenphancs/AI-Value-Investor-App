//
//  TechnicalMeter.swift
//  ios
//
//  Technical analysis meter with gauge and signal indicators
//

import SwiftUI

struct TechnicalMeter: View {
    let technicalData: TechnicalAnalysisData
    /// Daily/Weekly, remembered on this device and SHARED with `TechnicalAnalysisDetailView`
    /// through the one key. This was a private `@State` over a private copy of
    /// `TechnicalTimeframe`, and the Details screen started its own `@State .daily` — so the
    /// meter could read Weekly while Details opened on Daily, and both reset on every screen
    /// and relaunch. Both views now read the same `@AppStorage`, so they always agree.
    @AppStorage(TechnicalTimeframe.storageKey) private var timeframeID: String = TechnicalTimeframe.defaultChoice.storageID

    /// Resolved for display only; a tap writes `timeframeID`, nothing else does.
    private var selectedPeriod: TechnicalTimeframe {
        TechnicalTimeframe.stored(timeframeID)
    }

    // Active signal based on selected period
    private var activeSignal: TechnicalSignal {
        switch selectedPeriod {
        case .daily: return technicalData.dailySignal.signal
        case .weekly: return technicalData.weeklySignal.signal
        }
    }

    // Map signal to gauge value (needle position) and level
    private var activeGaugeValue: Double {
        let result: TechnicalIndicatorResult
        switch selectedPeriod {
        case .daily: result = technicalData.dailySignal
        case .weekly: result = technicalData.weeklySignal
        }
        guard result.totalIndicators > 0 else { return 0.5 }
        let ratio = Double(result.matchingIndicators) / Double(result.totalIndicators)

        // Map ratio into the correct zone based on signal
        // Each zone spans 0.2 of the gauge (5 zones: 0-0.2, 0.2-0.4, 0.4-0.6, 0.6-0.8, 0.8-1.0)
        let zoneBase: Double
        switch activeSignal {
        case .strongSell: zoneBase = 0.0
        case .sell:       zoneBase = 0.2
        case .hold:       zoneBase = 0.4
        case .buy:        zoneBase = 0.6
        case .strongBuy:  zoneBase = 0.8
        }
        // Position needle within the zone based on ratio
        let zoneOffset = min(ratio, 1.0) * 0.15 + 0.025
        return min(zoneBase + zoneOffset, 0.99)
    }

    // Level derived from signal (always consistent with label)
    private var activeGaugeLevel: Int {
        switch activeSignal {
        case .strongSell: return 1
        case .sell:       return 2
        case .hold:       return 3
        case .buy:        return 4
        case .strongBuy:  return 5
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
                        timeframeID = TechnicalTimeframe.daily.storageID
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
                        timeframeID = TechnicalTimeframe.weekly.storageID
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

// MARK: - Remembered timeframe

/// One key for the Daily/Weekly choice, read by `TechnicalMeter` (every host: the stock and
/// crypto Analysis tabs, Index, Commodity) and `TechnicalAnalysisDetailView`. Weekly is
/// always displayable: `weeklySignal` is non-optional (no history reads "Not enough
/// history"), the detail's weekly rows are built from the same daily bars, and its weekly
/// summaries fall back to daily in the model.
///
/// Device-only, and deliberately NOT cleared by `AppState.discardDataForEndedSession()`: a
/// display choice of this phone, not account data.
extension TechnicalTimeframe {
    static let storageKey = "caydex_technical_timeframe"
    static let defaultChoice: TechnicalTimeframe = .daily

    /// What is stored — NOT `rawValue`, which is the picker LABEL ("Daily"). Never change an id.
    var storageID: String {
        switch self {
        case .daily:  return "daily"
        case .weekly: return "weekly"
        }
    }

    /// The default for a missing or unknown id. Display-only — never written back.
    static func stored(_ id: String) -> TechnicalTimeframe {
        allCases.first { $0.storageID == id } ?? defaultChoice
    }

    /// `@AppStorage`'s string as a picker binding; only a user change writes.
    static func binding(_ id: Binding<String>) -> Binding<TechnicalTimeframe> {
        Binding(get: { Self.stored(id.wrappedValue) }, set: { id.wrappedValue = $0.storageID })
    }
}

// MARK: - Technical Gauge (Semi-circle style with 5 zones)
//
// A thin wrapper over `MeterGauge` so the Valuation Meter (Analysis tab, 2026-09-17) can
// share the exact same arc, zones, needle and sweep animation with a different centre label.
struct TechnicalGauge: View {
    let signal: TechnicalSignal
    let gaugeValue: Double

    var body: some View {
        MeterGauge(label: signal.displayName, labelColor: signal.color, gaugeValue: gaugeValue)
    }
}

/// The five-zone semicircular meter (red → green) with an animated needle and a centre
/// label. Generic over the label so one drawing serves the Technical and Valuation meters.
struct MeterGauge: View {
    let label: String
    let labelColor: Color
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
                .stroke(AppColors.cardBackgroundLight, lineWidth: 20)
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
                Text(label)
                    .font(AppTypography.titleCompact)
                    .fontWeight(.bold)
                    .foregroundColor(labelColor)
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

// MARK: - Technical Gauge Zones (5 colored segments with smooth gradients)
struct TechnicalGaugeZones: View {
    let size: CGFloat

    var body: some View {
        TechnicalArc()
            .stroke(
                // Sell → Hold → Buy ramp, all adaptive `*Graphic` tokens.
                //
                // This used SwiftUI SYSTEM colours and two frozen hexes, none of
                // which adapt. The worst was `Color.yellow` (#FFCC00) at the
                // apex: 1.51:1 on a white card, so the middle ~36° of the arc —
                // exactly where the needle rests for a Hold signal — washed out
                // in light mode. `#991B1B` had the mirror problem at 1.89:1 on
                // a dark card.
                //
                // The track beneath is stroked at the same lineWidth and is
                // fully occluded, so the arc's real neighbour is the CARD, not
                // the track. Every stop therefore has to clear the 3:1 graphic
                // bar against both card colours on its own.
                AngularGradient(
                    stops: [
                        .init(color: AppColors.lossGraphic, location: 0.0),
                        .init(color: AppColors.lossGraphic, location: 0.12),
                        .init(color: AppColors.loss, location: 0.28),
                        .init(color: AppColors.loss, location: 0.32),
                        .init(color: AppColors.cautionGraphic, location: 0.48),
                        .init(color: AppColors.cautionGraphic, location: 0.52),
                        .init(color: AppColors.gainGraphic, location: 0.68),
                        .init(color: AppColors.gainGraphic, location: 0.72),
                        .init(color: AppColors.gain, location: 0.88),
                        .init(color: AppColors.gain, location: 1.0),
                    ],
                    center: UnitPoint(x: 0.5, y: 1.0),
                    startAngle: .degrees(-180),
                    endAngle: .degrees(0)
                ),
                style: StrokeStyle(lineWidth: 20, lineCap: .round)
            )
            .frame(width: size, height: size / 2)
    }
}

// MARK: - Technical Arc Segment Shape

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
