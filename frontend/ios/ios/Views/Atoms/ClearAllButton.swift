//
//  ClearAllButton.swift
//  ios
//
//  Atom: Button to clear all items (e.g., recent searches)
//

import SwiftUI

struct ClearAllButton: View {
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Text("Clear All")
                .font(AppTypography.callout)
                .foregroundColor(AppColors.bearish)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ClearAllButton()
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
