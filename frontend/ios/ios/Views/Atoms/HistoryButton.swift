//
//  HistoryButton.swift
//  ios
//
//  Atom: Hamburger menu button for chat history
//

import SwiftUI

struct HistoryButton: View {
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Image(systemName: "line.3.horizontal")
                .font(.system(size: 20, weight: .medium))
                .foregroundColor(AppColors.textPrimary)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    HistoryButton()
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
