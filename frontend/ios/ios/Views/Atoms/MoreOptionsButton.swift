//
//  MoreOptionsButton.swift
//  ios
//
//  Atom: Three-dot menu button for more options
//

import SwiftUI

struct MoreOptionsButton: View {
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Image(systemName: "ellipsis")
                .font(.system(size: 16, weight: .medium))
                .foregroundColor(AppColors.textMuted)
                .rotationEffect(.degrees(90))
                .frame(width: 24, height: 24)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    MoreOptionsButton()
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
