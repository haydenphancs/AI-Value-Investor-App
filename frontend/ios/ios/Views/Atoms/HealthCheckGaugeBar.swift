//
//  HealthCheckGaugeBar.swift
//  ios
//
//  Atom: Gradient gauge bar for health check metrics with position indicator
//

import SwiftUI

struct HealthCheckGaugeBar: View {
    let position: Double  // 0.0 to 1.0
    let metricType: HealthCheckMetricType
    var height: CGFloat = 8

    private var clampedPosition: Double {
        min(max(position, 0.02), 0.98)  // Keep indicator visible
    }

    /// Returns gradient colors based on metric type
    /// Some metrics are "lower is better" (green->yellow->red)
    /// Others are "higher is better" (red->yellow->green)
    private var gradientColors: [Color] {
        switch metricType {
        case .debtToEquity:
            // Lower is better: green -> yellow -> red
            return [
                AppColors.bullish,
                Color(hex: "84CC16"),  // Lime
                AppColors.neutral,
                AppColors.alertOrange,
                AppColors.bearish
            ]
        case .peRatio:
            // Lower is better (value): green -> yellow -> red
            return [
                AppColors.bullish,
                Color(hex: "84CC16"),
                AppColors.neutral,
                AppColors.alertOrange,
                AppColors.bearish
            ]
        case .returnOnEquity:
            // Higher is better: red -> yellow -> green
            return [
                AppColors.bearish,
                AppColors.alertOrange,
                AppColors.neutral,
                Color(hex: "84CC16"),
                AppColors.bullish
            ]
        case .currentRatio:
            // Higher is better (within reason): red -> yellow -> green
            return [
                AppColors.bearish,
                AppColors.alertOrange,
                AppColors.neutral,
                Color(hex: "84CC16"),
                AppColors.bullish
            ]
        case .altmanZScore:
            // Higher is better: red (distress) -> yellow (grey zone) -> green (safe)
            return [
                AppColors.bearish,
                AppColors.alertOrange,
                AppColors.neutral,
                Color(hex: "84CC16"),
                AppColors.bullish
            ]
        }
    }

    /// Whether this metric uses zone-based rendering (distinct segments) vs gradient + dot
    private var isZoneBased: Bool {
        metricType == .altmanZScore
    }

    var body: some View {
        if isZoneBased {
            zoneBasedGauge
        } else {
            gradientGauge
        }
    }

    // MARK: - Gradient gauge (sector-comparison metrics)

    private var gradientGauge: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                // Gradient background bar
                RoundedRectangle(cornerRadius: height / 2)
                    .fill(
                        LinearGradient(
                            colors: gradientColors,
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(height: height)

                // Sector average marker (white vertical line at 50% — matches backend gauge anchor)
                Rectangle()
                    .fill(Color.white.opacity(0.6))
                    .frame(width: 2, height: height + 4)
                    .offset(x: geometry.size.width * 0.5 - 1)

                // Position indicator (white circle)
                Circle()
                    .fill(Color.white)
                    .frame(width: height + 6, height: height + 6)
                    .shadow(color: Color.black.opacity(0.3), radius: 2, x: 0, y: 1)
                    .offset(x: geometry.size.width * clampedPosition - (height + 6) / 2.0)
            }
        }
        .frame(height: height + 6)
    }

    // MARK: - Zone-based gauge (Altman Z-Score)
    // Shows three distinct colored segments: Distress (≤1.8), Grey (1.8–3.0), Safe (>3.0)
    // with a triangle marker for the current value position

    /// Altman Z-Score zone boundaries mapped to gauge fractions.
    /// Max display range is 0–6.0 (anything above 6 clamps to the right edge).
    private static let zScoreMax: Double = 6.0
    private static let distressFrac: Double = 1.8 / zScoreMax   // 0.30
    private static let greyFrac: Double = 3.0 / zScoreMax       // 0.50

    private var zScorePosition: Double {
        // Map the actual Z-Score value to 0.0–1.0 within the 0–6 range
        let zValue = position * 4.5  // undo backend mapping (backend: z / 4.5)
        return min(max(zValue / Self.zScoreMax, 0.02), 0.98)
    }

    private var zoneBasedGauge: some View {
        GeometryReader { geometry in
            let w = geometry.size.width
            let distressWidth = w * Self.distressFrac
            let greyWidth = w * (Self.greyFrac - Self.distressFrac)
            let safeWidth = w * (1.0 - Self.greyFrac)
            let gap: CGFloat = 2

            ZStack(alignment: .leading) {
                // Three zone segments
                HStack(spacing: gap) {
                    // Distress zone (red)
                    RoundedRectangle(cornerRadius: height / 2)
                        .fill(AppColors.bearish)
                        .frame(width: max(distressWidth - gap, 0), height: height)

                    // Grey zone (yellow/orange)
                    RoundedRectangle(cornerRadius: height / 2)
                        .fill(AppColors.neutral)
                        .frame(width: max(greyWidth - gap, 0), height: height)

                    // Safe zone (green)
                    RoundedRectangle(cornerRadius: height / 2)
                        .fill(AppColors.bullish)
                        .frame(width: max(safeWidth - gap, 0), height: height)
                }

                // Position indicator (white circle, same as other metrics)
                Circle()
                    .fill(Color.white)
                    .frame(width: height + 6, height: height + 6)
                    .shadow(color: Color.black.opacity(0.3), radius: 2, x: 0, y: 1)
                    .offset(x: w * zScorePosition - (height + 6) / 2.0)
            }
        }
        .frame(height: height + 6)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xxl) {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Debt-to-Equity (Low = Good)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                HealthCheckGaugeBar(position: 0.25, metricType: .debtToEquity)
            }

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("P/E Ratio (Low = Good)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                HealthCheckGaugeBar(position: 0.42, metricType: .peRatio)
            }

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("ROE (High = Good)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                HealthCheckGaugeBar(position: 0.35, metricType: .returnOnEquity)
            }

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Current Ratio (High = Good)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                HealthCheckGaugeBar(position: 0.68, metricType: .currentRatio)
            }

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Altman Z-Score (Zone-based)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                HealthCheckGaugeBar(position: 0.98, metricType: .altmanZScore)
            }

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Altman Z-Score (Grey Zone)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                HealthCheckGaugeBar(position: 0.53, metricType: .altmanZScore)
            }
        }
        .padding()
    }
}
