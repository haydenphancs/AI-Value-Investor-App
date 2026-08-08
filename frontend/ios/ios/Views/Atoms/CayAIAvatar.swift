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
            .foregroundColor(AppColors.textOnAccent)
            .frame(width: size, height: size)
            .background(
                // `*Fill`, not the text tokens — the glyph above is constant-white
                // `textOnAccent`, and the text tokens lighten in dark (2.24 / 2.43 there).
                LinearGradient(
                    colors: [AppColors.primaryFill, AppColors.accentCyanFill],
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
}
