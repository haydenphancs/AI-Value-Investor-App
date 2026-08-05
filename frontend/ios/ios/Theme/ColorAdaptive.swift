//
//  ColorAdaptive.swift
//  ios
//
//  Adaptive (light / dark) colour primitives backing `AppColors`.
//
//  Extracted from AppTheme.swift so the palette file is purely a list of token
//  VALUES and this file owns the mechanism. Both halves of a token are declared
//  in one place — you cannot add a light value and forget the dark one, because
//  the initialiser requires both.
//
//  Why a dynamic `UIColor` rather than an asset catalog or a `colorScheme`
//  branch in the view: the closure is re-evaluated by UIKit/SwiftUI whenever the
//  effective `userInterfaceStyle` changes (including when `AppearanceManager`
//  flips the window override), so a token switches instantly with NO view
//  rebuild and no `@Environment(\.colorScheme)` plumbing at 4,000+ call sites.
//

import SwiftUI

// MARK: - Hex parsing

extension UIColor {
    /// Build a `UIColor` from a hex string (RGB / ARGB), mirroring `Color(hex:)`.
    convenience init(hexString: String) {
        let hex = hexString.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let a, r, g, b: UInt64
        switch hex.count {
        case 3: // RGB (12-bit)
            (a, r, g, b) = (255, (int >> 8) * 17, (int >> 4 & 0xF) * 17, (int & 0xF) * 17)
        case 6: // RGB (24-bit)
            (a, r, g, b) = (255, int >> 16, int >> 8 & 0xFF, int & 0xFF)
        case 8: // ARGB (32-bit)
            (a, r, g, b) = (int >> 24, int >> 16 & 0xFF, int >> 8 & 0xFF, int & 0xFF)
        default:
            (a, r, g, b) = (255, 0, 0, 0)
        }
        self.init(
            red: CGFloat(r) / 255,
            green: CGFloat(g) / 255,
            blue: CGFloat(b) / 255,
            alpha: CGFloat(a) / 255
        )
    }
}

extension Color {
    init(hex: String) {
        self = Color(uiColor: UIColor(hexString: hex))
    }
}

// MARK: - Adaptive tokens

extension Color {
    /// Adaptive token: resolves to `lightHex` in light mode and to `darkHex` in
    /// dark (and for `.unspecified`).
    init(lightHex light: String, darkHex dark: String) {
        self.init(lightHex: light, lightAlpha: 1, darkHex: dark, darkAlpha: 1)
    }

    /// Adaptive token with a per-mode alpha.
    ///
    /// Borders, dividers, shadows and scrims are expressed as an ALPHA over
    /// whatever surface they land on rather than as a solid hex. That is what
    /// lets ONE `border` token sit correctly on the page, on a card, and on a
    /// nested card — a solid hex tuned for `#FFFFFF` is wrong on `#EDF0F5`, and
    /// the alpha form is also what survives on a tinted gain/loss surface.
    ///
    /// Light and dark need different alphas because elevation works in opposite
    /// directions: in light a border is dark ink laid ON a light surface, in
    /// dark it is light ink on a dark surface, and the eye needs roughly twice
    /// the ink in light to read the same edge. (Same reason Apple's `systemFill`
    /// alphas roughly double between the two modes.)
    init(lightHex light: String, lightAlpha: Double, darkHex dark: String, darkAlpha: Double) {
        self = Color(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .light
                ? UIColor(hexString: light).withAlphaComponent(CGFloat(lightAlpha))
                : UIColor(hexString: dark).withAlphaComponent(CGFloat(darkAlpha))
        })
    }
}
