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
        var hex = hexString.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        // Reject anything that is not pure hex BEFORE scanning.
        //
        // `Scanner.scanHexInt64` stops at the first non-hex character and reports no failure,
        // and `trimmingCharacters` only strips the ENDS — so a typo could match a length arm
        // and still be misread, two ways that are both worse than the documented
        // fall-back-to-black:
        //   "12345G"   scanned 0x12345 → an arbitrary navy, with no diagnostic anywhere
        //   "0xFF0000" scanned 0 into the ARGB alpha byte → alpha 0, i.e. the view renders
        //              NOTHING, which is far harder to notice than a wrong colour
        // Falling through to `default:` gives black for all of them, which is the contract
        // `ThemeContrastAudit.auditHexParsing` now actually pins.
        if !hex.allSatisfy({ $0.isHexDigit }) { hex = "" }
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
    /// dark — and for `.unspecified`, which is deliberate and explained on the
    /// designated initialiser below.
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
            // Exhaustive rather than a ternary so `.unspecified` is a stated decision
            // and nobody "fixes" it by inverting the predicate.
            //
            // `.unspecified` is an OVERRIDE INPUT, never a resolved trait: a UIWindow
            // attached to a UIWindowScene inherits the system style, and SwiftUI's
            // `colorScheme` has no unspecified case. Measured across all six
            // (mode × OS) combinations, every window resolved `.light` or `.dark` —
            // including System, where the override IS `.unspecified`. So this arm is
            // reached only OUTSIDE a window: pre-attach UIKit resolution, or a bare
            // `UITraitCollection.current` on a background queue.
            //
            // There, the right answer is the app's DEFAULT look (`AppearanceMode`
            // defaults to `.dark`), not the opposite of it — an out-of-window
            // resolution that disagrees with the app is a glaring artifact, one that
            // agrees is invisible. The launch-screen colorset's "Any Appearance" slot
            // is held on this same polarity for the same reason; change both or
            // neither.
            switch traits.userInterfaceStyle {
            case .light:
                return UIColor(hexString: light).withAlphaComponent(CGFloat(lightAlpha))
            case .dark, .unspecified:
                return UIColor(hexString: dark).withAlphaComponent(CGFloat(darkAlpha))
            @unknown default:
                return UIColor(hexString: dark).withAlphaComponent(CGFloat(darkAlpha))
            }
        })
    }
}

// MARK: - Server-supplied colour

/// What a server-supplied colour is going to be used FOR. Sets the contrast
/// floor the clamp has to reach. Mirrors `AppColors.TokenRole`, but lives here
/// so the boundary layers (`*Repository`, `Models/`) don't import the audit.
enum ServerColorRole {
    /// Text or a meaningful icon glyph — WCAG 1.4.3 AA.
    case text
    /// Chart series, bars, dots, tile tints — WCAG 1.4.11.
    case graphic

    var floor: Double {
        switch self {
        case .text: return 4.5
        case .graphic: return 3.0
        }
    }
}

extension Color {
    /// A colour the BACKEND chose, made legible in both appearances.
    ///
    /// WHY THIS EXISTS
    /// ---------------
    /// Several palettes are server-driven — Home tile accents, whale donut
    /// segments, analyst-distribution bars. The client cannot assume they are
    /// readable: six sampled Home accents measured **1.67–2.64:1** against a
    /// light card, i.e. every Home tile glyph was low-contrast in light mode.
    /// Retinting them in the view layer is impossible (the hex arrives at
    /// runtime), and a hardcoded token would throw away the backend's intent.
    ///
    /// So: keep the HUE the server picked, correct only its LIGHTNESS, per
    /// appearance, until it clears `role.floor` against the surface it will sit
    /// on. A cyan stays cyan; it just stops being a pale cyan on white.
    ///
    /// Hue and saturation are preserved exactly — this walks HSB brightness (and
    /// only falls back to desaturating when a fully-saturated hue still cannot
    /// reach the floor, which happens for yellows against white). That is why it
    /// is a clamp and not a "snap to nearest token".
    ///
    /// - Parameters:
    ///   - hex: the raw server value. A malformed string degrades to the
    ///     `fallback` token rather than to black.
    ///   - role: contrast floor to satisfy.
    ///   - fallback: used when `hex` cannot be parsed at all.
    init(themedHex hex: String?,
         role: ServerColorRole,
         fallback: Color = AppColors.textSecondary) {
        // Force opaque. `wcagLuminance` measures the colour's own channels and ignores its
        // alpha, so a translucent server value was certified against contrast it could never
        // deliver: "80CC1F1F" (50% red) MEASURES 5.55:1 on white and RENDERS 2.43:1. ARGB is
        // a supported input (`validatingHexString` accepts 8 chars, and the audit pins
        // "80FFFFFF" as valid), so this is reachable, not theoretical.
        //
        // Dropping the alpha rather than compositing inside `clamped` is the stronger fix: at
        // alpha < 1 there are hues for which NO candidate can reach 4.5:1 against the card, so
        // compositing would leave the clamp unable to satisfy its own contract. A server's
        // chosen opacity was never part of the contrast contract; its hue is.
        guard let hex, let base = UIColor(validatingHexString: hex)?.withAlphaComponent(1) else {
            self = fallback
            return
        }
        self = Color(uiColor: UIColor { traits in
            // Same `.unspecified → dark` polarity as the adaptive-token initialiser
            // above; see the reasoning there. Keep the two in step.
            let isLight = traits.userInterfaceStyle == .light
            // Clamp against the surface these actually land on: a card.
            let surface = UIColor(hexString: isLight ? "FFFFFF" : "1E2330")
            return base.clamped(toContrast: role.floor, against: surface, darken: isLight)
        })
    }
}

extension UIColor {
    /// Strict hex parse — `nil` rather than a silent fallback, so callers can
    /// choose what a bad value degrades to.
    convenience init?(validatingHexString hex: String) {
        let cleaned = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        guard [3, 6, 8].contains(cleaned.count),
              cleaned.allSatisfy({ $0.isHexDigit }) else { return nil }
        self.init(hexString: cleaned)
    }

    /// WCAG 2.1 relative luminance.
    var wcagLuminance: Double {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        guard getRed(&r, green: &g, blue: &b, alpha: &a) else { return 0 }
        func lin(_ v: CGFloat) -> Double {
            let d = max(0, min(1, Double(v)))
            return d <= 0.03928 ? d / 12.92 : pow((d + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    }

    func wcagContrast(against other: UIColor) -> Double {
        let (a, b) = (wcagLuminance, other.wcagLuminance)
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)
    }

    /// Walk brightness toward `darken`'s direction until this colour clears
    /// `target` contrast against `surface`. Hue and saturation are untouched
    /// until brightness alone is exhausted.
    ///
    /// Returns self unchanged when it already passes — the common case, so a
    /// well-chosen server colour costs one luminance comparison.
    func clamped(toContrast target: Double, against surface: UIColor, darken: Bool) -> UIColor {
        guard wcagContrast(against: surface) < target else { return self }

        var h: CGFloat = 0, s: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        guard getHue(&h, saturation: &s, brightness: &b, alpha: &a) else { return self }

        // 24 steps of 4% brightness covers the full range with sub-JND grain.
        for step in 1...24 {
            let delta = CGFloat(step) * 0.04
            let nb = darken ? max(0, b - delta) : min(1, b + delta)
            let candidate = UIColor(hue: h, saturation: s, brightness: nb, alpha: a)
            if candidate.wcagContrast(against: surface) >= target { return candidate }
        }

        // Brightness alone was not enough (saturated yellows/cyans on white).
        // Now trade saturation, which is the last thing worth giving up.
        for step in 1...10 {
            let ns = max(0, s - CGFloat(step) * 0.1)
            let nb: CGFloat = darken ? 0 : 1
            let candidate = UIColor(hue: h, saturation: ns, brightness: nb, alpha: a)
            if candidate.wcagContrast(against: surface) >= target { return candidate }
        }

        // Unreachable in practice (black/white always clear 3:1 and 4.5:1 on a
        // card), but degrade to maximum contrast rather than returning a value
        // known to fail.
        return darken ? .black : .white
    }
}
