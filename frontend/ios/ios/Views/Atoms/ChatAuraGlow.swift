//
//  ChatAuraGlow.swift
//  ios
//
//  Atom: a soft, slowly-pulsing blue→cyan radial "aura" used to give the AI chat screen a
//  futuristic feel behind the top bar and the input bar. Purely decorative — never wraps
//  interactive content (a `.repeatForever` glow on an ancestor of an expandable row once froze
//  the main thread; see ExclusiveSignalsSection). Place it as a sibling/overlay layer only.
//

import SwiftUI

struct ChatAuraGlow: View {
    /// Inner (hotter) color — defaults to the app's AI-accent cyan.
    var inner: Color = AppColors.accentCyan
    /// Outer color that fades to clear — defaults to the brand blue.
    var outer: Color = AppColors.primaryBlue
    /// Overall strength (0…1). Kept subtle by default.
    var intensity: Double = 1.0

    @State private var pulse = false

    var body: some View {
        GeometryReader { geo in
            let maxR = max(geo.size.width, geo.size.height)
            RadialGradient(
                colors: [
                    inner.opacity(0.22 * intensity),
                    outer.opacity(0.10 * intensity),
                    .clear
                ],
                center: .center,
                startRadius: 0,
                endRadius: maxR * 0.75
            )
            .frame(width: geo.size.width, height: geo.size.height)
            .scaleEffect(pulse ? 1.08 : 0.92)
            .opacity(pulse ? 1.0 : 0.65)
        }
        .blendMode(.plusLighter)          // additive glow reads cleanly on the dark background
        .allowsHitTesting(false)
        .onAppear {
            withAnimation(.easeInOut(duration: 2.4).repeatForever(autoreverses: true)) {
                pulse = true
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        VStack {
            ChatAuraGlow().frame(height: 220)
            Spacer()
            ChatAuraGlow().frame(height: 200)
        }
        .ignoresSafeArea()
    }
    .preferredColorScheme(.dark)
}
