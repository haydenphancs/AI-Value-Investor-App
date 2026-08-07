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

        failures.append(contentsOf: auditManifestCompleteness())
        failures.append(contentsOf: auditLightParity())
        failures.append(contentsOf: auditHexParsing())

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

    // MARK: - Manifest completeness

    /// Every colour token declared on `AppColors` must appear in `auditManifest`.
    ///
    /// Without this, adding a token is silently adding an UNAUDITED token — the
    /// contrast guard would keep printing ✅ while the new value fails.
    ///
    /// ⚠️ This does NOT make the palette self-maintaining, despite what this comment used
    /// to claim. `Mirror` cannot enumerate `AppColors`' static members, so it walks
    /// `AppColors.tokenInventory` — a HAND-WRITTEN duplicate of the palette. It therefore
    /// catches "in `TokenInventory` but not in `auditManifest`" and nothing else: a token
    /// added to `AppColors` and forgotten in `TokenInventory` is invisible to this check and
    /// ships unaudited with a green ✅. `theme-lint.sh` rule 8 closes that hole at the source
    /// level, which is the only layer that can see it.
    ///
    /// Computed aliases (`bullish`, `divider`, `tabBarSelected`, …) are `var`s
    /// that forward to a canonical token, so they are intentionally exempt —
    /// Mirror only sees stored properties, which is exactly the set that needs
    /// auditing.
    private static func auditManifestCompleteness() -> [Failure] {
        let audited = Set(AppColors.auditManifest.map(\.name))
        let declared = Mirror(reflecting: AppColors.tokenInventory)
            .children
            .compactMap { $0.label }

        return declared
            .filter { !audited.contains($0) }
            .map { name in
                Failure(style: "MANIFEST",
                        token: name,
                        surface: "auditManifest",
                        measured: 0,
                        required: 1)
            }
    }

    // MARK: - Light-mode parity

    /// Three tokens exist ONLY to zero something out in dark. Each one's LIGHT arm
    /// must stay bit-identical to the token it replaced, because the entire premise
    /// of removing dark card borders was "light mode does not move".
    ///
    /// That premise is otherwise unfalsifiable: light and dark are separate arms of
    /// the same declaration, so a later edit that retunes `cardEdge` "a little"
    /// silently drifts light away from `border` with nothing to catch it. Screenshots
    /// cannot catch it either — the app's own content (prices, charts, timestamps)
    /// changes between any two runs, so there is no byte-comparable baseline.
    ///
    /// The contrast loop above cannot see these at all: all three are `.decorative`
    /// or `.surface`, whose floor is 0, so `run()` skips them before measuring
    /// anything. This is the check that covers them.
    private static func auditLightParity() -> [Failure] {
        let pairs: [(name: String, mustEqual: String, lhs: Color, rhs: Color)] = [
            ("cardEdge", "border", AppColors.cardEdge, AppColors.border),
            ("shadowCard", "shadowAmbient", AppColors.shadowCard, AppColors.shadowAmbient),
            ("cardBackgroundNested", "cardBackground",
             AppColors.cardBackgroundNested, AppColors.cardBackground),
        ]

        var failures = pairs.compactMap { pair -> Failure? in
            let lhs = resolve(pair.lhs, .light)
            let rhs = resolve(pair.rhs, .light)
            guard !componentsEqual(lhs, rhs) else { return nil }
            return Failure(style: "PARITY",
                           token: pair.name,
                           surface: "must equal \(pair.mustEqual) in light",
                           measured: 0,
                           required: 1)
        }

        // Negative control. Every assertion above passes when `componentsEqual` returns
        // true, so a comparator that ALWAYS returned true would make this whole check
        // silently vacuous — it would keep printing ✅ while light mode drifted. Prove
        // it can distinguish two tokens that are genuinely different (#F4F5F8 page vs
        // #FFFFFF card).
        if componentsEqual(resolve(AppColors.background, .light),
                           resolve(AppColors.cardBackground, .light)) {
            failures.append(Failure(style: "PARITY",
                                    token: "componentsEqual",
                                    surface: "comparator cannot detect a difference",
                                    measured: 0,
                                    required: 1))
        }

        return failures
    }

    /// RGBA equality. Compares components rather than `UIColor ==` because the two
    /// sides are built by different dynamic providers, and `isEqual` on those can be
    /// false for colours that resolve identically.
    private static func componentsEqual(_ a: UIColor, _ b: UIColor) -> Bool {
        var ar: CGFloat = 0, ag: CGFloat = 0, ab: CGFloat = 0, aa: CGFloat = 0
        var br: CGFloat = 0, bg: CGFloat = 0, bb: CGFloat = 0, ba: CGFloat = 0
        guard a.getRed(&ar, green: &ag, blue: &ab, alpha: &aa),
              b.getRed(&br, green: &bg, blue: &bb, alpha: &ba) else { return false }
        // Tolerance covers 8-bit hex → CGFloat round-tripping, nothing more: one
        // step of 1/255 is ~0.0039, so 0.001 cannot mask a real difference.
        let e = 0.001
        return abs(ar - br) < e && abs(ag - bg) < e && abs(ab - bb) < e && abs(aa - ba) < e
    }

    // MARK: - Hex parsing

    /// `UIColor(hexString:)` has a `default:` arm that yields opaque BLACK for
    /// anything it cannot parse. A typo'd 5-character hex therefore becomes a
    /// black token with no diagnostic anywhere — it just looks like someone
    /// chose black. These cases pin the contract so that silent-black path
    /// cannot widen, and prove the strict `init?(validatingHexString:)` used by
    /// the server-colour clamp actually rejects what it claims to.
    private static func auditHexParsing() -> [Failure] {
        var failures: [Failure] = []

        func fail(_ what: String) {
            failures.append(Failure(style: "HEX", token: what,
                                    surface: "parser", measured: 0, required: 1))
        }

        // Valid forms round-trip.
        if UIColor(hexString: "FF0000").wcagContrast(against: .black) < 5 { fail("6-digit parse") }
        if UIColor(hexString: "#FFFFFF").wcagContrast(against: .black) < 20 { fail("hash-prefixed parse") }
        if UIColor(hexString: "FFF").wcagContrast(against: .black) < 20 { fail("3-digit shorthand") }

        // The strict parser must REJECT what the lenient one silently blackens.
        for bad in ["", "GGGGGG", "12345", "1234567", "nope"] {
            if UIColor(validatingHexString: bad) != nil { fail("accepted invalid '\(bad)'") }
        }
        // …and must accept every form the lenient one does.
        for good in ["FFF", "FF0000", "#00FF00", "80FFFFFF"] {
            if UIColor(validatingHexString: good) == nil { fail("rejected valid '\(good)'") }
        }

        // Pin what the LENIENT parser actually does with junk. Nothing asserted this before,
        // so the "silently blackens" contract above was a claim about untested behaviour —
        // and it turns out to be only half true. `Scanner.scanHexInt64` stops at the first
        // non-hex character WITHOUT reporting failure, and `trimmingCharacters` only strips
        // the ends, so a string can match a length arm and still be misread:
        //   "0O1122" (letter O) → scan stops immediately → BLACK
        //   "12345G"            → scans 0x12345 → an arbitrary navy, no diagnostic
        // Both are what a typo'd token looks like, so they are worth freezing rather than
        // discovering later as "that colour looks a bit off".
        for blackened in ["", "#", "zzz", "12", "1234", "1234567", "0O1122"] {
            if UIColor(hexString: blackened).wcagContrast(against: .black) > 1.01 {
                fail("lenient parser no longer blackens '\(blackened)'")
            }
        }
        // The invisible class is the dangerous one: an 8-char string whose first byte scans as
        // 0 yields alpha 0, so the view renders NOTHING rather than something wrong — which is
        // far harder to notice. "0xFF0000" is the realistic typo (a CSS/Swift-style prefix).
        for form in ["0xFF0000", "FF 00 00"] {
            var alpha: CGFloat = 0
            UIColor(hexString: form).getRed(nil, green: nil, blue: nil, alpha: &alpha)
            if alpha < 0.99 { fail("lenient parser yields an invisible colour for '\(form)'") }
        }

        // The clamp must actually reach its floor, in both directions.
        let lightCard = UIColor(hexString: "FFFFFF")
        let darkCard = UIColor(hexString: "1E2330")
        for hex in ["22D3EE", "FBBF24", "34D399", "C084FC", "FFFF00"] {
            let base = UIColor(hexString: hex)
            let onLight = base.clamped(toContrast: 3.0, against: lightCard, darken: true)
            let onDark = base.clamped(toContrast: 3.0, against: darkCard, darken: false)
            if onLight.wcagContrast(against: lightCard) < 3.0 { fail("clamp light #\(hex)") }
            if onDark.wcagContrast(against: darkCard) < 3.0 { fail("clamp dark #\(hex)") }

            // Hue must survive the clamp — that is the whole point of clamping
            // rather than snapping to a token.
            var h0: CGFloat = 0, h1: CGFloat = 0, s: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
            base.getHue(&h0, saturation: &s, brightness: &b, alpha: &a)
            onLight.getHue(&h1, saturation: &s, brightness: &b, alpha: &a)
            if s > 0.05, abs(h0 - h1) > 0.02 { fail("clamp shifted hue #\(hex)") }
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
