//
//  ThemeContrastAudit.swift
//  ios
//
//  DEBUG-only WCAG contrast regression guard for the colour palette.
//
//  WHY THIS EXISTS
//  ---------------
//  Light mode shipped unreadable because nothing checked. The palette declared
//  that its accent colours "read acceptably on both a dark and a light surface"
//  and that claim was simply false — bullish was 2.28:1 on white against a 4.5:1
//  requirement — but there was no mechanism that could notice.
//
//  This resolves every token in `AppColors.auditManifest` in BOTH interface
//  styles and asserts the computed WCAG 2.1 ratio clears its role's floor. It
//  runs once at launch in DEBUG, takes well under a millisecond, and compiles
//  out entirely in release.
//
//  WHAT IT DOES NOT DO
//  -------------------
//  It proves the PALETTE is sound, not that USAGE is. It cannot see that some
//  view renders `textMuted` on `toggleSelectedBackground`. Pair it with the
//  greps in the light-mode plan, and when you discover a token rendering on a
//  surface that isn't in its manifest entry, ADD THE SURFACE rather than
//  shrugging — that is exactly how toggleSelectedBackground was caught at 4.26:1.
//

#if DEBUG

import SwiftUI

enum ThemeContrastAudit {

    // MARK: - WCAG 2.1 maths

    /// Relative luminance per WCAG 2.1. Validated against the known anchors
    /// #767676-on-white = 4.54:1 and #000-on-#FFF = 21.00:1 (see `selfTest`).
    private static func luminance(_ color: UIColor) -> Double {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        // Resolve through extended sRGB so a P3 token doesn't skew the maths.
        guard color.getRed(&r, green: &g, blue: &b, alpha: &a) else { return 0 }
        func channel(_ v: CGFloat) -> Double {
            let d = max(0, min(1, Double(v)))
            return d <= 0.03928 ? d / 12.92 : pow((d + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    }

    static func ratio(_ a: UIColor, _ b: UIColor) -> Double {
        let (la, lb) = (luminance(a), luminance(b))
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    }

    /// Flatten a translucent foreground onto its background before measuring.
    /// Without this, `getRed` reports the un-composited colour and every border
    /// / shadow / scrim token measures against the wrong value.
    private static func composite(_ fg: UIColor, over bg: UIColor) -> UIColor {
        var fr: CGFloat = 0, fg_: CGFloat = 0, fb: CGFloat = 0, fa: CGFloat = 0
        var br: CGFloat = 0, bg_: CGFloat = 0, bb: CGFloat = 0, ba: CGFloat = 0
        guard fg.getRed(&fr, green: &fg_, blue: &fb, alpha: &fa),
              bg.getRed(&br, green: &bg_, blue: &bb, alpha: &ba) else { return fg }
        guard fa < 1 else { return fg }
        return UIColor(
            red:   fr * fa + br * (1 - fa),
            green: fg_ * fa + bg_ * (1 - fa),
            blue:  fb * fa + bb * (1 - fa),
            alpha: 1
        )
    }

    /// The load-bearing call. Works for dynamic tokens (`Color(lightHex:darkHex:)`
    /// bridges to a dynamic `UIColor` that resolves per trait) and for constant
    /// ones (returns itself). It is the only way to assert BOTH modes from a
    /// single process without actually flipping the app's appearance.
    private static func resolve(_ color: Color, _ style: UIUserInterfaceStyle) -> UIColor {
        UIColor(color).resolvedColor(with: UITraitCollection(userInterfaceStyle: style))
    }

    // MARK: - Floors

    static func floor(for role: AppColors.TokenRole) -> Double {
        switch role {
        case .text:
            // WCAG 1.4.3 AA. NO epsilon: `caution` (4.50) and `primaryBlue`
            // (4.52) clear the nested surface by ~0.02, so a fudge factor here
            // would hide the exact regression this is meant to catch.
            return 4.5
        case .largeText, .graphic:
            // WCAG 1.4.11 / large-text AA. A threshold, not a rounding target —
            // 2.999:1 fails.
            return 3.0
        case .surface, .decorative:
            // Surfaces are separated by a border, not by luminance; decorative
            // tokens (gridlines, disabled, scrims) are exempt by design.
            return 0
        }
    }

    // MARK: - Run

    struct Failure {
        let style: String
        let token: String
        let surface: String
        let measured: Double
        let required: Double

        var description: String {
            String(format: "[%@] %@ on %@ = %.2f:1 (need %.2f:1)",
                   style, token, surface, measured, required)
        }
    }

    @discardableResult
    static func run(assertOnFailure: Bool = true) -> [Failure] {
        selfTest()

        var failures: [Failure] = []

        for style in [UIUserInterfaceStyle.light, .dark] {
            let styleName = style == .light ? "LIGHT" : "DARK"

            for spec in AppColors.auditManifest {
                let required = floor(for: spec.role)

                // A fill is checked in reverse: is on-accent text legible ON it?
                if spec.carriesOnAccentText {
                    let fill = resolve(spec.color, style)
                    let onAccent = resolve(AppColors.textOnAccent, style)
                    let measured = ratio(onAccent, fill)
                    if measured < 4.5 {
                        failures.append(Failure(style: styleName,
                                               token: "textOnAccent",
                                               surface: spec.name,
                                               measured: measured,
                                               required: 4.5))
                    }
                }

                guard required > 0 else { continue }

                for key in spec.surfaces {
                    guard let surfaceColor = AppColors.surfaceRegistry[key] else {
                        assertionFailure("auditManifest: '\(spec.name)' names unknown surface '\(key)'")
                        continue
                    }
                    let bg = resolve(surfaceColor, style)
                    let fg = composite(resolve(spec.color, style), over: bg)
                    let measured = ratio(fg, bg)
                    if measured < required {
                        failures.append(Failure(style: styleName,
                                               token: spec.name,
                                               surface: key,
                                               measured: measured,
                                               required: required))
                    }
                }
            }
        }

        if failures.isEmpty {
            print("✅ ThemeContrastAudit: \(AppColors.auditManifest.count) tokens pass WCAG in light + dark")
        } else {
            print("❌ ThemeContrastAudit: \(failures.count) contrast failure(s)")
            failures.forEach { print("   \($0.description)") }
            if assertOnFailure {
                assertionFailure("Theme contrast regression:\n"
                                 + failures.map(\.description).joined(separator: "\n"))
            }
        }

        return failures
    }

    /// Guards the maths itself. If someone "optimises" `luminance` and breaks
    /// the sRGB linearisation, every ratio above becomes meaningless while still
    /// looking plausible — so verify against two published anchors first.
    private static func selfTest() {
        let white = UIColor(hexString: "FFFFFF")
        let black = UIColor(hexString: "000000")
        let grey = UIColor(hexString: "767676")   // the canonical 4.5:1-on-white value

        let maxContrast = ratio(black, white)
        let greyOnWhite = ratio(grey, white)

        assert(abs(maxContrast - 21.0) < 0.01,
               "WCAG maths broken: black-on-white = \(maxContrast), expected 21.00")
        assert(abs(greyOnWhite - 4.54) < 0.02,
               "WCAG maths broken: #767676-on-white = \(greyOnWhite), expected 4.54")
    }

    // MARK: - Reporting

    /// Prints the full measured table. Not called automatically — invoke from
    /// the debugger (`ThemeContrastAudit.report()`) when tuning values.
    static func report() {
        for style in [UIUserInterfaceStyle.light, .dark] {
            print("\n━━━ \(style == .light ? "LIGHT" : "DARK") ━━━")
            for spec in AppColors.auditManifest where floor(for: spec.role) > 0 {
                let cells = spec.surfaces.compactMap { key -> String? in
                    guard let surface = AppColors.surfaceRegistry[key] else { return nil }
                    let bg = resolve(surface, style)
                    let fg = composite(resolve(spec.color, style), over: bg)
                    return String(format: "%@ %.2f", key, ratio(fg, bg))
                }
                print("  \(spec.name.padding(toLength: 28, withPad: " ", startingAt: 0)) \(cells.joined(separator: " · "))")
            }
        }
    }
}

#endif
