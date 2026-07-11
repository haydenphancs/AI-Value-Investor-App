//
//  ChatAuraGlow.swift
//  ios
//
//  Atom: a subtle, STATIC blue→cyan "aura" that washes the WHOLE chat screen to give it a
//  futuristic feel. Two soft radial glows (top behind the bar + a fainter one at the bottom
//  behind the input) whose fades extend all the way to the screen corners, so there is NO hard
//  edge / seam between aura and background. Purely decorative — never wraps interactive content
//  (a `.repeatForever` glow on an ancestor of an expandable row once froze the main thread; see
//  ExclusiveSignalsSection). Place as a sibling/overlay layer only.
//

import SwiftUI

struct ChatAuraGlow: View {
    /// Inner (hotter) color — defaults to the app's AI-accent cyan.
    var inner: Color = AppColors.accentCyan
    /// Outer color that fades toward clear — defaults to the brand blue.
    var outer: Color = AppColors.primaryBlue
    /// Overall strength multiplier (0…1). Deliberately faint by default.
    var intensity: Double = 1.0

    var body: some View {
        GeometryReader { geo in
            // Diagonal → the fade reaches the far corners, so no visible ring lands on-screen.
            let diag = sqrt(geo.size.width * geo.size.width + geo.size.height * geo.size.height)
            ZStack {
                // Top wash — strongest behind the top bar, fading gently DOWN the whole screen.
                RadialGradient(
                    colors: [
                        inner.opacity(0.10 * intensity),
                        outer.opacity(0.045 * intensity),
                        .clear,
                    ],
                    center: UnitPoint(x: 0.5, y: 0.0),
                    startRadius: 0,
                    endRadius: diag
                )
                // Bottom wash — a fainter lift behind the input bar.
                RadialGradient(
                    colors: [
                        inner.opacity(0.06 * intensity),
                        outer.opacity(0.028 * intensity),
                        .clear,
                    ],
                    center: UnitPoint(x: 0.5, y: 1.0),
                    startRadius: 0,
                    endRadius: diag * 0.85
                )
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
        .blendMode(.plusLighter)   // additive glow reads cleanly on the dark background
        .allowsHitTesting(false)
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        ChatAuraGlow().ignoresSafeArea()
    }
    .preferredColorScheme(.dark)
}
