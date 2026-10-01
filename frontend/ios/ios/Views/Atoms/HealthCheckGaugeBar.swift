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
    /// Whether a peer benchmark backs the 50% anchor tick.
    var hasBenchmark: Bool = true
    var height: CGFloat = 8
    /// The TRUE Altman Z (the metric's `value`), for the zone gauge. The backend's
    /// `gauge_position` is `clamp(z / 4.5, 0.02, 0.98)` rounded to 2 dp, so inverting it
    /// can never place a Z above 4.41 (every mega-cap, Z 4.5 or 60, sat at the same 73.5%)
    /// and snapped Z 1.78–1.82 onto the 1.8 boundary. nil only in previews / old callers.
    var zValue: Double? = nil

    private var clampedPosition: Double {
        // NaN survives `min(max(...))` — with a NaN both comparisons are false,
        // so the result is NaN and the `.offset` below becomes invalid (the
        // indicator vanishes). Check finiteness first.
        guard position.isFinite else { return 0.5 }
        return min(max(position, 0.02), 0.98)  // Keep indicator visible
    }

    private var markerDiameter: CGFloat { height + 6 }

    /// Leading offset that centres the marker at `fraction` of `width`, with the centre
    /// clamped so the whole circle stays on the track (at 0.98 of a 248pt bar it used to
    /// hang 2pt past the trailing edge).
    private func markerOffset(width: CGFloat, fraction: Double) -> CGFloat {
        let radius = markerDiameter / 2
        guard width > markerDiameter else { return max(width / 2 - radius, 0) }
        let centre = min(max(width * CGFloat(fraction), radius), width - radius)
        return centre - radius
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
                AppColors.cautionGraphic,  // Lime
                AppColors.neutral,
                AppColors.alertOrange,
                AppColors.bearish
            ]
        case .peRatio:
            // Lower is better (value): green -> yellow -> red
            return [
                AppColors.bullish,
                AppColors.cautionGraphic,
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
                AppColors.cautionGraphic,
                AppColors.bullish
            ]
        case .currentRatio:
            // Higher is better (within reason): red -> yellow -> green
            return [
                AppColors.bearish,
                AppColors.alertOrange,
                AppColors.neutral,
                AppColors.cautionGraphic,
                AppColors.bullish
            ]
        case .altmanZScore:
            // Higher is better: red (distress) -> yellow (grey zone) -> green (safe)
            return [
                AppColors.bearish,
                AppColors.alertOrange,
                AppColors.neutral,
                AppColors.cautionGraphic,
                AppColors.bullish
            ]
        case .interestCoverage, .quickRatio:
            // Higher is better: red -> yellow -> green
            return [
                AppColors.bearish,
                AppColors.alertOrange,
                AppColors.neutral,
                AppColors.cautionGraphic,
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

                // Peer-average marker (white vertical line at 50% — matches the
                // backend gauge anchor). Drawn ONLY when a benchmark actually
                // exists: it used to render for every metric, so a metric with
                // `comparison_value: null` (thin industry, or Altman Z, which
                // has no peer benchmark at all) showed a tick for a benchmark
                // that isn't there — while its "vs X" caption was hidden.
                if hasBenchmark {
                    Rectangle()
                        .fill(Color.white.opacity(0.6))
                        .frame(width: 2, height: height + 4)
                        .offset(x: geometry.size.width * 0.5 - 1)
                }

                // Position indicator (white circle)
                Circle()
                    .fill(AppColors.mediaSurface)
                    .frame(width: markerDiameter, height: markerDiameter)
                    .shadow(color: AppColors.shadowKey, radius: 2, x: 0, y: 1)
                    .offset(x: markerOffset(width: geometry.size.width, fraction: clampedPosition))
            }
        }
        .frame(height: markerDiameter)
    }

    // MARK: - Zone-based gauge (Altman Z-Score)
    // Shows three distinct colored segments: Distress (≤ 1.8), Grey (above 1.8 up to
    // 3.0), Safe (> 3.0) — the backend `_zscore_status` convention — with a circle
    // marker for the current value.

    /// Altman Z-Score zone boundaries mapped to gauge fractions.
    /// Display range is 0–6.0: a Z of 6 or more (or 0 or less) pins the marker to the
    /// end of the track, which `zValue` makes reachable.
    private static let zScoreMax: Double = 6.0
    private static let distressFrac: Double = 1.8 / zScoreMax   // 0.30
    private static let greyFrac: Double = 3.0 / zScoreMax       // 0.50

    private var zScorePosition: Double {
        // The true Z when the caller has it; inverting the backend's clamped, rounded
        // gauge is only the fallback (it tops out at Z 4.41).
        let z = zValue ?? position * 4.5
        guard z.isFinite else { return 0.5 }
        return min(max(z / Self.zScoreMax, 0.02), 0.98)
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
                    .fill(AppColors.mediaSurface)
                    .frame(width: markerDiameter, height: markerDiameter)
                    .shadow(color: AppColors.shadowKey, radius: 2, x: 0, y: 1)
                    .offset(x: markerOffset(width: w, fraction: zScorePosition))
            }
        }
        .frame(height: markerDiameter)
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

            // True-value placement: Z 4.5 and Z 60 used to share one spot at 73.5%.
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Altman Z-Score 4.5 vs 60 (true value)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                HealthCheckGaugeBar(position: 0.98, metricType: .altmanZScore, zValue: 4.5)
                HealthCheckGaugeBar(position: 0.98, metricType: .altmanZScore, zValue: 60)
            }
        }
        .padding()
    }
}
