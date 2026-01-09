//
//  TechnicalDetailSections.swift
//  ios
//
//  Section cards for Technical Analysis Detail view
//

import SwiftUI

// MARK: - Moving Averages Section
struct MovingAveragesSection: View {
    let indicators: [MovingAverageIndicator]
    let summary: IndicatorSummary

    var body: some View {
        TechnicalSectionCard(title: "Moving Averages") {
            VStack(spacing: 0) {
                // Summary badges
                HStack {
                    IndicatorSummaryBadges(summary: summary)
                    Spacer()
                }
                .padding(.bottom, AppSpacing.md)

                // Indicator rows
                ForEach(indicators) { indicator in
                    TechnicalIndicatorRow(
                        name: indicator.name,
                        value: indicator.formattedValue,
                        signal: indicator.signal
                    )

                    if indicator.id != indicators.last?.id {
                        Divider()
                            .background(AppColors.cardBackgroundLight)
                    }
                }
            }
        }
    }
}

// MARK: - Oscillators Section
struct OscillatorsSection: View {
    let indicators: [OscillatorIndicator]
    let summary: IndicatorSummary

    var body: some View {
        TechnicalSectionCard(title: "Oscillators") {
            VStack(spacing: 0) {
                // Summary badges
                HStack {
                    IndicatorSummaryBadges(summary: summary)
                    Spacer()
                }
                .padding(.bottom, AppSpacing.md)

                // Indicator rows
                ForEach(indicators) { indicator in
                    TechnicalIndicatorRow(
                        name: indicator.name,
                        value: indicator.formattedValue,
                        signal: indicator.signal
                    )

                    if indicator.id != indicators.last?.id {
                        Divider()
                            .background(AppColors.cardBackgroundLight)
                    }
                }
            }
        }
    }
}

// MARK: - Pivot Points Section
struct PivotPointsSection: View {
    let pivotData: PivotPointsData

    var body: some View {
        TechnicalSectionCard(title: "Pivot Points", subtitle: pivotData.method) {
            VStack(spacing: 0) {
                ForEach(pivotData.levels) { level in
                    PivotPointRow(
                        name: level.name,
                        value: level.formattedValue,
                        valueColor: level.valueColor
                    )

                    if level.id != pivotData.levels.last?.id {
                        Divider()
                            .background(AppColors.cardBackgroundLight)
                    }
                }
            }
        }
    }
}

// MARK: - Volume Analysis Section
struct VolumeAnalysisSection: View {
    let volumeData: VolumeAnalysisData

    var body: some View {
        TechnicalSectionCard(title: "Volume Analysis") {
            VStack(spacing: AppSpacing.md) {
                // Volume cards
                HStack(spacing: AppSpacing.md) {
                    VolumeCard(
                        title: "Current Volume",
                        value: volumeData.formattedCurrentVolume,
                        subtitle: volumeData.formattedVolumeChange,
                        subtitleColor: volumeData.volumeChangeColor
                    )

                    VolumeCard(
                        title: "Avg Volume (30d)",
                        value: volumeData.formattedAvgVolume,
                        subtitle: "Daily Average",
                        subtitleColor: AppColors.textMuted
                    )
                }

                Divider()
                    .background(AppColors.cardBackgroundLight)

                // Volume metrics
                VolumeMetricRow(
                    label: "Volume Trend",
                    value: volumeData.volumeTrend.rawValue,
                    valueColor: volumeData.volumeTrend.color,
                    showArrow: volumeData.volumeTrend != .stable,
                    isUp: volumeData.volumeTrend == .increasing
                )

                Divider()
                    .background(AppColors.cardBackgroundLight)

                VolumeMetricRow(
                    label: "OBV",
                    value: volumeData.formattedOBV,
                    valueColor: volumeData.obvColor
                )

                Divider()
                    .background(AppColors.cardBackgroundLight)

                VolumeMetricRow(
                    label: "Money Flow Index",
                    value: volumeData.formattedMFI,
                    valueColor: AppColors.textPrimary
                )
            }
        }
    }
}

// MARK: - Volume Card
struct VolumeCard: View {
    let title: String
    let value: String
    let subtitle: String
    let subtitleColor: Color

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text(title)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Text(value)
                .font(AppTypography.title2)
                .fontWeight(.bold)
                .foregroundColor(AppColors.textPrimary)

            Text(subtitle)
                .font(AppTypography.caption)
                .foregroundColor(subtitleColor)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(AppSpacing.md)
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
    }
}

// MARK: - Fibonacci Retracement Section
struct FibonacciRetracementSection: View {
    let fibData: FibonacciRetracementData

    var body: some View {
        TechnicalSectionCard(title: "Fibonacci Retracement", subtitle: fibData.timeframe) {
            VStack(spacing: 0) {
                ForEach(fibData.levels) { level in
                    FibonacciLevelRow(
                        percentage: level.percentage,
                        value: level.formattedValue,
                        isKeyLevel: level.isKey
                    )

                    if level.id != fibData.levels.last?.id {
                        Divider()
                            .background(AppColors.cardBackgroundLight)
                    }
                }
            }
        }
    }
}

// MARK: - Key Support & Resistance Section
struct SupportResistanceSection: View {
    let srData: SupportResistanceData

    var body: some View {
        TechnicalSectionCard(title: "Key Support & Resistance") {
            VStack(spacing: AppSpacing.md) {
                // Resistance levels
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text("Resistance Levels")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)

                    ForEach(srData.resistanceLevels) { level in
                        SupportResistanceLevelRow(
                            name: level.name,
                            value: level.value,
                            strength: level.strength
                        )

                        if level.id != srData.resistanceLevels.last?.id {
                            Divider()
                                .background(AppColors.cardBackgroundLight)
                        }
                    }
                }

                // Current price
                HStack {
                    Spacer()
                    VStack(spacing: AppSpacing.xxs) {
                        Text(srData.formattedCurrentPrice)
                            .font(AppTypography.title2)
                            .fontWeight(.bold)
                            .foregroundColor(AppColors.textPrimary)

                        Text("Current Price")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }
                    Spacer()
                }
                .padding(.vertical, AppSpacing.md)
                .background(AppColors.cardBackgroundLight)
                .cornerRadius(AppCornerRadius.medium)

                // Support levels
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text("Support Levels")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)

                    ForEach(srData.supportLevels) { level in
                        SupportResistanceLevelRow(
                            name: level.name,
                            value: level.value,
                            strength: level.strength
                        )

                        if level.id != srData.supportLevels.last?.id {
                            Divider()
                                .background(AppColors.cardBackgroundLight)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Generic Section Card
struct TechnicalSectionCard<Content: View>: View {
    let title: String
    let subtitle: String?
    let content: Content

    init(title: String, subtitle: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.subtitle = subtitle
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            HStack {
                Text(title)
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                if let subtitle = subtitle {
                    Text(subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            content
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            VStack(spacing: AppSpacing.lg) {
                MovingAveragesSection(
                    indicators: Array(MovingAverageIndicator.sampleData.prefix(3)),
                    summary: MovingAverageIndicator.sampleSummary
                )

                OscillatorsSection(
                    indicators: Array(OscillatorIndicator.sampleData.prefix(3)),
                    summary: OscillatorIndicator.sampleSummary
                )

                PivotPointsSection(pivotData: PivotPointsData.sampleData)

                VolumeAnalysisSection(volumeData: VolumeAnalysisData.sampleData)

                FibonacciRetracementSection(fibData: FibonacciRetracementData.sampleData)

                SupportResistanceSection(srData: SupportResistanceData.sampleData)
            }
            .padding()
        }
    }
}
