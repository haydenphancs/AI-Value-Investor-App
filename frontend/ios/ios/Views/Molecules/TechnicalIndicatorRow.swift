//
//  TechnicalIndicatorRow.swift
//  ios
//
//  Row displaying a single technical indicator with name, value, and signal
//

import SwiftUI

struct TechnicalIndicatorRow: View {
    let name: String
    let value: String
    let signal: IndicatorSignal

    var body: some View {
        HStack {
            Text(name)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            Text(value)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textPrimary)

            IndicatorSignalBadge(signal: signal)
                .frame(width: 60, alignment: .trailing)
        }
        .padding(.vertical, AppSpacing.sm)
    }
}

// MARK: - Pivot Point Row (different styling)
struct PivotPointRow: View {
    let name: String
    let value: String
    let valueColor: Color

    var body: some View {
        HStack {
            Text(name)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            Text(value)
                .font(AppTypography.subheadline)
                .fontWeight(.medium)
                .foregroundColor(valueColor)
        }
        .padding(.vertical, AppSpacing.sm)
    }
}

// MARK: - Volume Metric Row
struct VolumeMetricRow: View {
    let label: String
    let value: String
    let valueColor: Color
    let showArrow: Bool
    let isUp: Bool

    init(label: String, value: String, valueColor: Color = AppColors.textPrimary, showArrow: Bool = false, isUp: Bool = true) {
        self.label = label
        self.value = value
        self.valueColor = valueColor
        self.showArrow = showArrow
        self.isUp = isUp
    }

    var body: some View {
        HStack {
            Text(label)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            HStack(spacing: AppSpacing.xxs) {
                Text(value)
                    .font(AppTypography.subheadline)
                    .fontWeight(.medium)
                    .foregroundColor(valueColor)

                if showArrow {
                    Image(systemName: isUp ? "arrow.up" : "arrow.down")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundColor(valueColor)
                }
            }
        }
        .padding(.vertical, AppSpacing.sm)
    }
}

// MARK: - Fibonacci Level Row
struct FibonacciLevelRow: View {
    let percentage: String
    let value: String
    let isKeyLevel: Bool

    var body: some View {
        HStack {
            HStack(spacing: AppSpacing.xs) {
                Text(percentage)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)

                if isKeyLevel {
                    Text(percentage == "0.0%" ? "(High)" : "(Low)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            Spacer()

            Text(value)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textPrimary)
        }
        .padding(.vertical, AppSpacing.sm)
    }
}

// MARK: - Support/Resistance Level Row
struct SupportResistanceLevelRow: View {
    let name: String
    let value: Double
    let strength: LevelStrength

    var body: some View {
        HStack {
            Text(name)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            Text(String(format: "%.2f", value))
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textPrimary)

            Text(strength.rawValue)
                .font(AppTypography.caption)
                .foregroundColor(strength.color)
                .frame(width: 60, alignment: .trailing)
        }
        .padding(.vertical, AppSpacing.sm)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: 0) {
            TechnicalIndicatorRow(name: "MA(5)", value: "172.34", signal: .buy)
            TechnicalIndicatorRow(name: "RSI(14)", value: "58.34", signal: .neutral)
            PivotPointRow(name: "R1", value: "180.67", valueColor: AppColors.bullish)
            VolumeMetricRow(label: "Volume Trend", value: "Increasing", valueColor: AppColors.bullish, showArrow: true, isUp: true)
            FibonacciLevelRow(percentage: "0.0%", value: "182.45", isKeyLevel: true)
            SupportResistanceLevelRow(name: "R1", value: 180.67, strength: .weak)
        }
        .padding()
    }
}
