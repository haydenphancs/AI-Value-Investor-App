//
//  AddTickerButton.swift
//  ios
//
//  Molecule: Button to add a new ticker to watchlist
//

import SwiftUI

struct AddTickerButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "plus")
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(AppColors.textSecondary)
                .frame(width: 32, height: 32)
                .background(AppColors.cardBackgroundLight)
                .clipShape(Circle())
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    AddTickerButton(action: {})
        .padding()
        .background(AppColors.background)
}
