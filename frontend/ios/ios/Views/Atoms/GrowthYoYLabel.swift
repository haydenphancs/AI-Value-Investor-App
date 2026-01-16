//
//  GrowthYoYLabel.swift
//  ios
//
//  Atom: Year-over-Year percentage label with color coding
//

import SwiftUI

struct GrowthYoYLabel: View {
    let yoyPercent: Double

    var body: some View {
        Text(formattedValue)
            .font(AppTypography.caption)
            .foregroundColor(yoyColor)
    }

    private var formattedValue: String {
        String(format: "%.2f%%", yoyPercent)
    }

    private var yoyColor: Color {
        yoyPercent >= 0 ? AppColors.bullish : AppColors.bearish
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.lg) {
            GrowthYoYLabel(yoyPercent: 7.92)
            GrowthYoYLabel(yoyPercent: -2.30)
            GrowthYoYLabel(yoyPercent: 0.00)
            GrowthYoYLabel(yoyPercent: -10.92)
        }
        .padding()
    }
}
