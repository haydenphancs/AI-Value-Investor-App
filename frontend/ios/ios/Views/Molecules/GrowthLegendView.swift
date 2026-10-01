//
//  GrowthLegendView.swift
//  ios
//
//  Molecule: Legend showing YoY, Value, and peer-average (industry or sector) indicators
//

import SwiftUI

struct GrowthLegendView: View {
    /// "Industry" or "Sector" — whichever peer group the dashed line was drawn from.
    /// It used to read "Sector Average" unconditionally while the line was the INDUSTRY
    /// median whenever one existed (AVGO: Semiconductors, not Technology).
    var peerWord: String = "Sector"
    /// False when the shown series draws no dashed line (no benchmark for any of its
    /// periods, e.g. before the calendar-quarter recompute has run): the legend must not
    /// name a line that is not on the chart.
    var showsPeerLine: Bool = true

    var body: some View {
        HStack(spacing: AppSpacing.xl) {
            // Value Legend
            HStack(spacing: AppSpacing.xs) {
                GrowthLegendDot(color: AppColors.growthBarBlue)
                    .offset(y: 1)
                Text("Value")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            // YoY Legend
            HStack(spacing: AppSpacing.xs) {
                GrowthLegendDot(color: AppColors.growthYoYYellow)
                    .offset(y: 1)
                Text("YoY")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Peer Average Legend (industry or sector)
            if showsPeerLine {
                HStack(spacing: AppSpacing.xs) {
                    GrowthLegendDot(color: AppColors.growthSectorGray, style: .dashed)
                        .offset(y: 1)
                    Text("\(peerWord) Average (YoY)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            GrowthLegendView()
            GrowthLegendView(peerWord: "Industry")
            GrowthLegendView(showsPeerLine: false)
        }
        .padding()
    }
}
