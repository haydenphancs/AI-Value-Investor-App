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
            // Outer container circle (invisible) to maintain size
            Circle()
                .fill(Color.clear)
                .frame(width: 12, height: 12)
            
            // Animated dot
                . overlay(
                Circle()
                    .fill(AppColors.bearish)
                    .frame(width: 8, height: 8)
                    .scaleEffect(isAnimating ? 1.2 : 1.0)
                    .opacity(isAnimating ? 0.7 : 1.0)
                )
            }
        
        .frame(width: 12, height: 12) // Fixed frame to prevent layout shifts
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
