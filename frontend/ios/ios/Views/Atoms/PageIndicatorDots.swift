//
//  PageIndicatorDots.swift
//  ios
//
//  Atom: Page indicator dots for carousel/pagination
//

import SwiftUI

struct PageIndicatorDots: View {
    let currentPage: Int
    let totalPages: Int

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            ForEach(0..<totalPages, id: \.self) { index in
                Circle()
                    .fill(index == currentPage ? AppColors.textPrimary : AppColors.textMuted)
                    .frame(width: 8, height: 8)
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        PageIndicatorDots(currentPage: 0, totalPages: 3)
        PageIndicatorDots(currentPage: 1, totalPages: 3)
        PageIndicatorDots(currentPage: 2, totalPages: 3)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
