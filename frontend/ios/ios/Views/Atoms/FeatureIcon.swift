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
        FeatureIcon(systemIconName: "chart.pie.fill", color: Color(hex: "22C55E"))
        FeatureIcon(systemIconName: "building.2.fill", color: Color(hex: "3B82F6"))
        FeatureIcon(systemIconName: "sparkles", color: Color(hex: "F97316"))
        FeatureIcon(systemIconName: "exclamationmark.triangle.fill", color: Color(hex: "EF4444"))
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
