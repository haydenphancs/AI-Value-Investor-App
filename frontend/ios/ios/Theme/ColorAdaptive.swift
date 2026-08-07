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
    init(lightHex light: String, darkHex dark: String,
         boostsUnderIncreasedContrast: Bool = true) {
        self.init(lightHex: light, lightAlpha: 1, darkHex: dark, darkAlpha: 1,
                  boostsUnderIncreasedContrast: boostsUnderIncreasedContrast)
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
    /// - Parameter boostsUnderIncreasedContrast: whether this token participates in the
    ///   Increase Contrast boost. `false` for SURFACES and for constant-by-design pairs
    ///   (`textOnAccent`, `mediaSurface`) — moving those breaks the contract they exist
    ///   to hold, and a surface has nothing to be pushed away from.
    init(lightHex light: String, lightAlpha: Double, darkHex dark: String, darkAlpha: Double,
         boostsUnderIncreasedContrast: Bool = true) {
        self = Color(uiColor: UIColor { traits in
            // `isLight` is written as an equality against `.light`, NOT as a ternary on
            // `.dark`, so that `.unspecified` (and any future case) lands on DARK. That
            // polarity is a stated decision, not an accident — keep the predicate this
            // way round.
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
            // INCREASE CONTRAST is the second axis, and it used to be a literal no-op.
            //
            // This closure receives the WHOLE trait collection, and it only ever read
            // `userInterfaceStyle` — so the one OS lever a low-vision user has against a
            // borderline token moved nothing. A hand-rolled dynamic `UIColor` gets no
            // High Contrast variant for free (an asset-catalog colorset would), which is
            // exactly why it has to be read here.
            //
            // The boost is COMPUTED rather than hand-authored per token: push the colour
            // away from the surface it will land on until it clears WCAG AAA (7:1),
            // reusing the same clamp the server-colour path uses. Hue and saturation
            // survive; only lightness moves — so the palette keeps its identity and no
            // second set of 61 hexes has to be maintained in step with the first.
            //
            // SURFACES and alpha'd DECORATIVE tokens are left alone: pushing a surface
            // "away from itself" is meaningless, and a border/shadow is defined by its
            // alpha, not by a contrast floor. `boostsUnderIncreasedContrast` names that.
            let isLight = traits.userInterfaceStyle == .light
            let base: UIColor = isLight
                ? UIColor(hexString: light).withAlphaComponent(CGFloat(lightAlpha))
                : UIColor(hexString: dark).withAlphaComponent(CGFloat(darkAlpha))

            guard traits.accessibilityContrast == .high,
                  boostsUnderIncreasedContrast,
                  base.cgColor.alpha >= 0.99
            else { return base }

            let surface = UIColor(hexString: isLight ? "EDF0F5" : "252B3B")   // the binding surfaces
            return base.clamped(toContrast: 7.0, against: surface, darken: isLight)
        })
    }
}

// MARK: - Server-supplied colour

/// What a server-supplied colour is going to be used FOR. Sets the contrast
/// floor the clamp has to reach. Mirrors `AppColors.TokenRole`, but lives here
/// so the boundary layers (`*Repository`, `Models/`) don't import the audit.
enum ServerColorRole {
    /// Text or a meaningful icon glyph drawn ON a card — WCAG 1.4.3 AA.
    case text
    /// Chart series, bars, dots, decorative tints drawn ON a card — WCAG 1.4.11.
    /// Carries no ink of its own.
    case graphic
    /// A SATURATED BACKGROUND that `AppColors.textOnAccent` ink sits on.
    ///
    /// Measured the other way round from the two above: the server colour is the
    /// BACKGROUND and the ink is the foreground, so the floor is "textOnAccent ON
    /// it ≥ 4.5", not "it ON a card ≥ N". That inversion is the whole reason this
    /// case exists.
    ///
    /// `.graphic` measures against the CARD, which is why it read as correct while
    /// being exactly wrong for a tile: in DARK it BRIGHTENS the colour — right for a
    /// chart dot on #1E2330, backwards under white ink — and seven call sites drew
    /// server hexes as tiles/gradients carrying light ink. Measured on the hexes
    /// shipping today, white on the resulting tile was 1.92–3.96:1 in dark and
    /// 3.04–3.96:1 in LIGHT. So `darken:` was never the bug; the ROLE was.
    ///
    /// Appearance-INVARIANT by construction, exactly like the `*Fill` tokens (see
    /// AppTheme.swift "FILLS — saturated backgrounds that WHITE TEXT sits on"): a
    /// value dark enough to carry white ink in light is dark enough in dark. One
    /// value, both jobs, no second calibration.
    case fill

    var floor: Double {
        switch self {
        case .text: return 4.5
        case .graphic: return 3.0
        // Same 4.5 as `.text`, but measured under `textOnAccent` rather than
        // against a card — see `Color.init(themedHex:role:fallback:)`.
        case .fill: return 4.5
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
            // Exhaustive on purpose — no `default:`. A fourth role must be a compile
            // error here, not a silent inheritance of the card clamp, which is exactly
            // how `.fill`'s call sites spent their life being measured the wrong way
            // round. Same argument as `APIEndpoint.authPolicy`.
            switch role {
            case .text, .graphic:
                // Clamp against the surface these actually land on: a card.
                let surface = UIColor(hexString: isLight ? "FFFFFF" : "1E2330")
                return base.clamped(toContrast: role.floor, against: surface, darken: isLight)

            case .fill:
                // The INK is the foreground and THIS is the background, so walk DOWN in
                // both appearances. `textOnAccent` is resolved through `traits` rather
                // than hardcoded to white so this tracks the token if it ever stops
                // being constant; today both arms are #FFFFFF, which is precisely why
                // the result is identical in light and dark — the `*Fill` contract.
                let ink = UIColor(AppColors.textOnAccent).resolvedColor(with: traits)
                return base.clampedAsFill(carrying: ink, toContrast: role.floor)
            }
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
        //
        // Keep the brightness the loop above REACHED rather than slamming to the
        // extreme. `nb = darken ? 0 : 1` was loop-invariant, so on the darken path
        // every one of these ten iterations constructed pure BLACK (brightness 0 is
        // black at any saturation) and iteration 1 returned it — the loop was
        // functionally `return .black`, discarding both hue and saturation and
        // contradicting the comment directly above it. Desaturation is only a second
        // axis if the first one holds its position.
        let exhausted: CGFloat = darken ? max(0, b - 24 * 0.04) : min(1, b + 24 * 0.04)
        for step in 1...10 {
            let ns = max(0, s - CGFloat(step) * 0.1)
            let candidate = UIColor(hue: h, saturation: ns, brightness: exhausted, alpha: a)
            if candidate.wcagContrast(against: surface) >= target { return candidate }
        }

        // Unreachable in practice (black/white always clear 3:1 and 4.5:1 on a
        // card), but degrade to maximum contrast rather than returning a value
        // known to fail.
        return darken ? .black : .white
    }

    /// Clamp this colour AS A FILL: darken until `ink` laid ON TOP of it clears `target`.
    ///
    /// Delegates to `clamped(toContrast:against:darken:)` rather than duplicating the
    /// 24-step walk, and it is correct to do so: `wcagContrast` is
    /// `(max L + 0.05)/(min L + 0.05)` — SYMMETRIC. It does not know or care which side
    /// is the foreground. The only two things that differ between a fill and a
    /// foreground are which colour goes into `against:` and the direction of the walk,
    /// and this wrapper fixes both. It exists so that no call site has to pass its INK
    /// into a parameter spelled `against:` and rely on the next reader noticing.
    ///
    /// `darken: true` unconditionally, because `textOnAccent` is white in BOTH
    /// appearances: down is the only direction that can help. It always converges —
    /// brightness 0 is black at 21:1 — so the desaturation fallback above is
    /// unreachable here and hue AND saturation are preserved exactly. Measured over
    /// the server hexes in the app today the deepest walk was 13 of 24 steps
    /// (#FFFF00 → #7A7A00, 4.53:1).
    func clampedAsFill(carrying ink: UIColor, toContrast target: Double = 4.5) -> UIColor {
        clamped(toContrast: target, against: ink, darken: true)
    }
}
