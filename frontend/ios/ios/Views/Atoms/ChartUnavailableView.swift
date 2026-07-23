//
//  ChartUnavailableView.swift
//  ios
//
//  Atom: honest placeholder for a chart that has nothing to plot.
//
//  Replaces the previous behaviour where an empty series still rendered its
//  axes off invented fallback bounds (`?? 1`, `?? 50`), producing a labelled
//  grid with no marks on it. A reader can't distinguish that from "all the
//  values are zero", so it states the absence instead.
//

import SwiftUI

struct ChartUnavailableView: View {
    let message: String
    var systemImage: String = "chart.line.uptrend.xyaxis"

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: systemImage)
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.textMuted)

            Text(message)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.lg)
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        ChartUnavailableView(message: "Margin data isn't available for this company.")
    }
    .preferredColorScheme(.dark)
}
