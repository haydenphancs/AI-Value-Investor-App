//
//  LiveIndicator.swift
//  ios
//
//  Atom: Animated live/pulsing indicator dot — heartbeat style
//

import SwiftUI

struct LiveIndicator: View {
    @State private var beatScale: CGFloat = 1.0
    @State private var ringScale: CGFloat = 0.6
    @State private var ringOpacity: Double = 0.0

    var body: some View {
        ZStack {
            // Outer ring — expands outward on each beat
            Circle()
                .fill(AppColors.bearish.opacity(0.3))
                .frame(width: 14, height: 14)
                .scaleEffect(ringScale)
                .opacity(ringOpacity)

            // Inner solid dot — heartbeat squeeze
            Circle()
                .fill(AppColors.bearish)
                .frame(width: 8, height: 8)
                .scaleEffect(beatScale)
        }
        .fixedSize()
        .frame(width: 14, height: 14)
        .onAppear { startHeartbeat() }
    }

    private func startHeartbeat() {
        // Heartbeat: quick double-pump then rest
        // beat 1 → beat 2 → pause → repeat
        Timer.scheduledTimer(withTimeInterval: 1.4, repeats: true) { _ in
            // — First beat —
            withAnimation(.easeOut(duration: 0.1)) {
                beatScale = 1.25
                ringScale = 0.8
                ringOpacity = 0.5
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                withAnimation(.easeIn(duration: 0.12)) {
                    beatScale = 1.0
                }
            }

            // — Second beat (slightly softer) —
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.28) {
                withAnimation(.easeOut(duration: 0.1)) {
                    beatScale = 1.15
                    ringScale = 1.0
                    ringOpacity = 0.35
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.38) {
                withAnimation(.easeIn(duration: 0.15)) {
                    beatScale = 1.0
                    ringOpacity = 0.0
                    ringScale = 0.6
                }
            }
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
