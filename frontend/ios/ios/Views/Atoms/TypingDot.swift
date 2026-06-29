//
//  TypingDot.swift
//  ios
//
//  Atom: a single pulsing dot used in the "Cay AI is thinking..." typing indicator.
//  Extracted from the (now-removed) ChatTabView so it can be shared by AIChatScreen.
//

import SwiftUI

struct TypingDot: View {
    let delay: Double
    @State private var isAnimating = false

    var body: some View {
        Circle()
            .fill(AppColors.primaryBlue)
            .frame(width: 8, height: 8)
            .opacity(isAnimating ? 1.0 : 0.3)
            .animation(
                .easeInOut(duration: 0.6)
                .repeatForever(autoreverses: true)
                .delay(delay),
                value: isAnimating
            )
            .onAppear { isAnimating = true }
    }
}

#Preview {
    HStack(spacing: AppSpacing.sm) {
        TypingDot(delay: 0.0)
        TypingDot(delay: 0.2)
        TypingDot(delay: 0.4)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
