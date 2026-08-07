//
//  GlassPanel.swift
//  ios
//
//  Floating-panel material for popups and dropdown menus, with a pre-iOS-26 fallback.
//
//  WHY THIS EXISTS — it is the reason the app can ship below iOS 26.
//
//  `.glassEffect(_:in:)` is iOS 26-only, and it was the ONLY iOS 26-exclusive API in the
//  codebase: four call sites, all decorative chrome on a floating panel. Nothing else required
//  26, yet `IPHONEOS_DEPLOYMENT_TARGET` was 26.2 — so every device not on the newest OS could
//  not install the app at all, and would not even see it in the App Store. That is a very large
//  addressable market traded for four modifiers, and it looks like an Xcode default nobody
//  revisited rather than a decision (there is not a single `@available(iOS 26…)` in the tree).
//
//  Centralised rather than four inline `if #available` blocks so the fallback is defined once.
//  Behaviour matches what the original call-site comments described:
//
//      "the material supplies the translucent frost, the rounded shape, the adaptive edge
//       highlight, AND the floating-layer shadow, so there's no manual fill / stroke / shadow
//       (an opaque fill would defeat the translucency; a manual shadow would double up)"
//
//  On iOS 26 that all still holds — `glassEffect` does the work and nothing is added. Below 26
//  the material supplies only the frost and the shape, so the edge and the shadow ARE applied
//  by hand, from the same tokens the rest of the app uses. Reduce Transparency is honoured on
//  both paths: `.ultraThinMaterial` degrades to an opaque fill automatically, exactly as
//  `glassEffect` does.
//

import SwiftUI

extension View {
    /// Floating-panel material: dropdown menus, popovers, chat row menus.
    ///
    /// Use this instead of calling `.glassEffect` directly — a bare call compiles only against
    /// an iOS 26 deployment target and would silently re-raise the floor for the whole app.
    ///
    /// - Parameter cornerRadius: matches the panel's own rounding. Pass an `AppCornerRadius`
    ///   token, never a literal.
    func glassPanel(cornerRadius: CGFloat) -> some View {
        modifier(GlassPanelModifier(cornerRadius: cornerRadius))
    }
}

private struct GlassPanelModifier: ViewModifier {
    let cornerRadius: CGFloat

    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            // The native material supplies frost + shape + edge + shadow. Adding any of them
            // here would double up — see the file header.
            content.glassEffect(.regular, in: RoundedRectangle(cornerRadius: cornerRadius))
        } else {
            content
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: cornerRadius))
                // `.ultraThinMaterial` gives frost and shape but no edge and no lift, so both
                // come from the shared tokens. `cardEdge` is transparent in dark by design
                // (dark separates by fill, light by border — see .claude/rules/ios-swiftui.md),
                // and `AppShadows.overlay` is the token for anything floating over content.
                .overlay(
                    RoundedRectangle(cornerRadius: cornerRadius)
                        .strokeBorder(AppColors.cardEdge, lineWidth: 1)
                )
                .shadow(
                    color: AppShadows.overlay.color,
                    radius: AppShadows.overlay.radius,
                    x: AppShadows.overlay.x,
                    y: AppShadows.overlay.y
                )
        }
    }
}
