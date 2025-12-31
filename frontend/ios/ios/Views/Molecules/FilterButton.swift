//
//  FilterButton.swift
//  ios
//
//  Molecule: Filter/settings button for news feed
//

import SwiftUI

struct FilterButton: View {
    var hasActiveFilters: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack(alignment: .topTrailing) {
                Image(systemName: "slider.horizontal.3")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(AppColors.textSecondary)
                    .frame(width: 32, height: 32)
                    .background(AppColors.cardBackgroundLight)
                    .clipShape(Circle())

                if hasActiveFilters {
                    Circle()
                        .fill(AppColors.primaryBlue)
                        .frame(width: 8, height: 8)
                        .offset(x: 2, y: -2)
                }
            }
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    HStack(spacing: 20) {
        FilterButton(hasActiveFilters: false, action: {})
        FilterButton(hasActiveFilters: true, action: {})
    }
    .padding()
    .background(AppColors.background)
}
