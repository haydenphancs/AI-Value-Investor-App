//
//  CayAIAvatar.swift
//  ios
//
//  Atom: the Cay AI mark — a gradient sparkle badge used on assistant messages
//  and the chat header. Purely presentational (no app data).
//

import SwiftUI

struct CayAIAvatar: View {
    var size: CGFloat = 24

    var body: some View {
        Image(systemName: "sparkles.2")
            .font(.system(size: size * 0.52, weight: .bold))
            .foregroundColor(.white)
            .frame(width: size, height: size)
            .background(
                LinearGradient(
                    colors: [AppColors.primaryBlue, AppColors.accentCyan],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .clipShape(Circle())
    }
}

#Preview {
    HStack(spacing: 12) {
        CayAIAvatar(size: 20)
        CayAIAvatar(size: 28)
        CayAIAvatar(size: 40)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
