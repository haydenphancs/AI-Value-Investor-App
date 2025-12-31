//
//  ManageAssetsButton.swift
//  ios
//
//  Molecule: Button to manage watchlist assets
//

import SwiftUI

struct ManageAssetsButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "slider.horizontal.3")
                .font(.system(size: 16, weight: .medium))
                .foregroundColor(AppColors.textSecondary)
                .frame(width: 36, height: 36)
                .background(AppColors.cardBackgroundLight)
                .clipShape(Circle())
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ManageAssetsButton(action: {})
        .padding()
        .background(AppColors.background)
}
