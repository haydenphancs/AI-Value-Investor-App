//
//  DetailHeaderChartSkeleton.swift
//  ios
//
//  Molecule: instant-render placeholder for the price-header + chart region of the
//  asset detail screens, shown while the first data loads — replacing the old
//  full-screen blocking spinner (which also intercepted the back tap). Reuses the
//  ShimmerEffect atom. The chart block matches the real chart's height footprint so
//  there is no layout jump when live data swaps in.
//

import SwiftUI

struct DetailHeaderChartSkeleton: View {

    private var bar: some View {
        RoundedRectangle(cornerRadius: 6, style: .continuous)
            .fill(AppColors.cardBackgroundLight)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Price-header placeholders (company name / price / change).
            VStack(alignment: .leading, spacing: 8) {
                bar.frame(width: 150, height: 14)
                bar.frame(width: 120, height: 30)
                bar.frame(width: 90, height: 14)
            }
            .padding(.top, AppSpacing.sm)

            // Chart placeholder (same height footprint as TickerChartView).
            RoundedRectangle(cornerRadius: AppCornerRadius.medium, style: .continuous)
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 180)
                .padding(.top, AppSpacing.sm)

            // Range-selector pills.
            HStack(spacing: 8) {
                ForEach(0..<7, id: \.self) { _ in
                    Capsule()
                        .fill(AppColors.cardBackgroundLight)
                        .frame(width: 34, height: 24)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, AppSpacing.lg)
        .shimmer()
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        DetailHeaderChartSkeleton()
    }
    .preferredColorScheme(.dark)
}
