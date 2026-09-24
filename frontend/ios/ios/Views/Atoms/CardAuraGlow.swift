//
//  CardAuraGlow.swift
//  ios
//
//  Atom: a soft coloured halo around a rounded card that slowly "breathes".
//
//  How to use it — all three parts are load-bearing:
//
//  1. Apply it as the card's `.background { … }` AFTER the card's `.clipShape`. A halo is
//     drawn OUTSIDE the card's bounds, so anything the clip encloses — including a shadow on
//     the card's own background shape — is cut off. That is exactly how the App-Exclusive
//     Signals glow went invisible on 2026-08-27 while its comment still said "same glow".
//  2. Never wrap content in it. It is a sibling layer sized by its host, not an ancestor of
//     the card's rows: a `.repeatForever` glow on an ANCESTOR of an expandable row, entangled
//     with the row's animated expand, once hard-froze the main thread (ExclusiveSignalsSection).
//  3. The card on top must be opaque. The halo's fill sits directly behind it; only the blur
//     that spills past the edges is meant to show.
//
//  The breath is a SCOPED `.animation(_:body:)` on the layer's opacity, not `withAnimation`:
//  it can only ever animate that one opacity, so it cannot pick up the card's resize or a
//  first-layout geometry change and loop THAT forever.
//

import SwiftUI

struct CardAuraGlow: View {
    let color: Color
    let cornerRadius: CGFloat
    /// Blur radius of the halo.
    var radius: CGFloat = 18
    /// Halo strength at the top and bottom of a breath, and the steady strength shown when
    /// Reduce Motion is on. Defaults are the original App-Exclusive Signals glow.
    var peak: Double = 0.32
    var trough: Double = 0.12
    var still: Double = 0.22
    /// One breath in, in seconds. The loop auto-reverses, so a full cycle is twice this.
    var period: Double = 2.2

    /// Tabs are opacity-mounted, so a hidden tab never disappears and this is the only
    /// signal to stop — same gate as `MarketPulseSection`'s `BlinkingDot`.
    @Environment(\.isActiveTab) private var isActiveTab
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var breathing = false
    /// Home is a plain (non-lazy) stack, so a card scrolled far off screen stays in the tree
    /// and would keep asking for frames. Starts true: outside a scroll view no callback comes.
    @State private var onScreen = true

    var body: some View {
        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
            .fill(color)
            // A fixed radius on a SHAPE: rasterised from one rounded rectangle, never from
            // the card's content. Only the opacity below changes.
            .shadow(color: color, radius: radius, x: 0, y: 0)
            // Repeating while breathing; a finite animation when not, which replaces the
            // repeating one and so ends the loop.
            .animation(breathing
                       ? .easeInOut(duration: period).repeatForever(autoreverses: true)
                       : .easeOut(duration: 0.3)) { layer in
                layer.opacity(level)
            }
            .onScrollVisibilityChange(threshold: 0.01) { visible in
                onScreen = visible
            }
            .onChange(of: isActiveTab && !reduceMotion && onScreen, initial: true) { _, shouldBreathe in
                breathing = shouldBreathe
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    private var level: Double {
        if breathing { return peak }
        return reduceMotion ? still : trough
    }
}

#Preview("Dark") {
    CardAuraGlowPreviewCard()
        .environment(\.colorScheme, .dark)
}

#Preview("Light") {
    CardAuraGlowPreviewCard()
        .environment(\.colorScheme, .light)
}

private struct CardAuraGlowPreviewCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("App-Exclusive Signals")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
            Text("An opaque card with the halo behind it.")
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textSecondary)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(AppColors.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .background { CardAuraGlow(color: AppColors.primaryBlue, cornerRadius: 18) }
        .padding(AppSpacing.lg)
        .frame(maxHeight: .infinity)
        .background(AppColors.background)
    }
}
