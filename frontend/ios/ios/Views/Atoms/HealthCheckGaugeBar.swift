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
        }
    }

    var body: some View {
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

                // Sector average marker (white vertical line at ~60%)
                Rectangle()
                    .fill(Color.white.opacity(0.6))
                    .frame(width: 2, height: height + 4)
                    .offset(x: geometry.size.width * 0.6 - 1)

                // Position indicator (white circle)
                Circle()
                    .fill(Color.white)
                    .frame(width: height + 6, height: height + 6)
                    .shadow(color: Color.black.opacity(0.3), radius: 2, x: 0, y: 1)
                    .offset(x: geometry.size.width * clampedPosition - (height + 6) / 2)
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
        }
        .padding()
    }
}
