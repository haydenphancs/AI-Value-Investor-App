//
//  LiveIndicator.swift
//  ios
//
//  Atom: Animated live/pulsing indicator dot
//

import SwiftUI

struct LiveIndicator: View {
    @State private var isAnimating = false

    var body: some View {
        ZStack {
            // Outer pulsing ring (fixed position)
            Circle()
                .fill(AppColors.bearish.opacity(0.3))
                .frame(width: 12, height: 12)
                .scaleEffect(isAnimating ? 1.0 : 0.8)
                .opacity(isAnimating ? 0.0 : 0.5)

            // Inner solid dot (never moves)
            Circle()
                .fill(AppColors.bearish)
                .frame(width: 8, height: 8)
        }
        .frame(width: 12, height: 12) // Fixed frame prevents layout shifts
        .animation(
            Animation.easeInOut(duration: 1.0)
                .repeatForever(autoreverses: true),
            value: isAnimating
        )
        .onAppear {
            isAnimating = true
        }
    }
}

#Preview {
    HStack(spacing: 8) {
        LiveIndicator()
        Text("Live News")
            .font(AppTypography.bodyBold)
            .foregroundColor(AppColors.textPrimary)
    }
    .padding()
    .background(AppColors.background)
}
