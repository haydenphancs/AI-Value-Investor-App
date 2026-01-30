//
//  ArticleSectionIcon.swift
//  ios
//
//  Atom: Icon badge for article section headers
//

import SwiftUI

struct ArticleSectionIcon: View {
    let icon: String
    var color: Color = AppColors.primaryBlue
    var size: CGFloat = 32

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.25)
                .fill(color.opacity(0.15))
                .frame(width: size, height: size)

            Image(systemName: icon)
                .font(.system(size: size * 0.45, weight: .semibold))
                .foregroundColor(color)
        }
    }
}

#Preview {
    HStack(spacing: AppSpacing.lg) {
        ArticleSectionIcon(icon: "chart.bar.fill", color: AppColors.primaryBlue)
        ArticleSectionIcon(icon: "cpu.fill", color: AppColors.alertPurple)
        ArticleSectionIcon(icon: "shield.checkered", color: AppColors.bullish)
        ArticleSectionIcon(icon: "flame.fill", color: AppColors.bearish, size: 40)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
