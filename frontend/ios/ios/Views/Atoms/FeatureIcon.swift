//
//  FeatureIcon.swift
//  ios
//
//  Atom: Feature icon with colored background
//

import SwiftUI

struct FeatureIcon: View {
    let systemIconName: String
    let color: Color
    var size: CGFloat = 36

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                .fill(color.opacity(0.2))
                .frame(width: size, height: size)

            Image(systemName: systemIconName)
                .font(.system(size: size * 0.45, weight: .semibold))
                .foregroundColor(color)
        }
    }
}

#Preview {
    HStack(spacing: AppSpacing.lg) {
        FeatureIcon(systemIconName: "chart.pie.fill", color: AppColors.gain)
        FeatureIcon(systemIconName: "building.2.fill", color: AppColors.primaryBlue)
        FeatureIcon(systemIconName: AppSymbols.ai, color: AppColors.alertOrange)
        FeatureIcon(systemIconName: "exclamationmark.triangle.fill", color: AppColors.loss)
    }
    .padding()
    .background(AppColors.background)
}
