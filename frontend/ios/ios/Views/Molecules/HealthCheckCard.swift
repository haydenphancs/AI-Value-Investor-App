//
//  HealthCheckCard.swift
//  ios
//
//  Molecule: Individual health check metric card with gauge indicator
//

import SwiftUI

struct HealthCheckCard: View {
    let metric: HealthCheckMetric

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(metric.name)
                    .font(AppTypography.subheadline)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)

                Text(metric.subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            // Value with indicator
            HStack(alignment: .bottom, spacing: AppSpacing.sm) {
                Text(metric.displayValue)
                    .font(AppTypography.title2)
                    .fontWeight(.bold)
                    .foregroundColor(metric.status.color)

                Text(metric.comparisonText)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.bottom, 2)
            }

            // Progress bar indicator
            HealthCheckGauge(
                value: metric.normalizedValue,
                status: metric.status,
                isLowerBetter: metric.isLowerBetter
            )

            // Scale labels
            HStack {
                Text(metric.isLowerBetter ? "Healthy" : "Low")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Spacer()
                Text(metric.isLowerBetter ? "Risky" : "High")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            // Description
            Text(metric.description)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(metric.status.backgroundColor)
        )
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .stroke(metric.status.color.opacity(0.3), lineWidth: 1)
        )
        .frame(width: 280)
    }
}

// MARK: - Health Check Gauge
struct HealthCheckGauge: View {
    let value: Double
    let status: HealthStatus
    let isLowerBetter: Bool

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                // Background track
                RoundedRectangle(cornerRadius: 3)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 6)

                // Gradient fill
                RoundedRectangle(cornerRadius: 3)
                    .fill(
                        LinearGradient(
                            colors: isLowerBetter
                                ? [AppColors.bullish, AppColors.neutral, AppColors.bearish]
                                : [AppColors.bearish, AppColors.neutral, AppColors.bullish],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(width: geometry.size.width * min(max(value, 0), 1), height: 6)

                // Position indicator
                Circle()
                    .fill(status.color)
                    .frame(width: 12, height: 12)
                    .overlay(
                        Circle()
                            .stroke(AppColors.cardBackground, lineWidth: 2)
                    )
                    .offset(x: (geometry.size.width * min(max(value, 0), 1)) - 6)
            }
        }
        .frame(height: 12)
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: AppSpacing.md) {
                ForEach(HealthCheckMetric.sampleData) { metric in
                    HealthCheckCard(metric: metric)
                }
            }
            .padding()
        }
    }
}
