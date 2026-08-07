//
//  AppSentiment.swift
//  ios
//
//  The NON-COLOUR half of sentiment encoding — dash patterns, hollow bodies, hatching
//  and direction glyphs for when hue alone must not carry the meaning.
//
//  WHY THIS IS IN Theme/ AND NOT Views/
//  ------------------------------------
//  `AppColors` centralises the hue; this centralises everything else about the same
//  encoding, so they belong side by side. Consumers span Atoms → Organisms AND `Models/`
//  (`PivotLevelType` in TickerDetailModels.swift resolves both a colour and a prefix), so
//  a home under `Views/` would invert the dependency.
//
//  WHY THIS CANNOT BE DONE IN THE PALETTE, UNLIKE INCREASE CONTRAST
//  ---------------------------------------------------------------
//  Increase Contrast was a one-line win: `ColorAdaptive.swift`'s `UIColor { traits in }`
//  provider reads `traits.accessibilityContrast`, and every one of the ~4,000 colour call
//  sites got it for free. **That pattern does not transfer to Differentiate Without
//  Color, and copying it would ship a bug that passes every cold-launch test.**
//
//  `UITraitCollection.h` lists the traits that participate in dynamic-colour resolution —
//  userInterfaceIdiom, userInterfaceStyle, displayGamut, accessibilityContrast,
//  userInterfaceLevel. `differentiateWithoutColor` is NOT among them; it lives in
//  `UIAccessibility.h` as a free function plus a change notification. UIKit re-invokes a
//  colour provider only on a TRAIT change, so a provider that called
//  `UIAccessibility.shouldDifferentiateWithoutColor` would resolve once, cache the
//  `CGColor`, and never update when the user flips the switch. It would look correct on
//  launch and be silently dead thereafter.
//
//  SwiftUI's `@Environment(\.accessibilityDifferentiateWithoutColor)` observes the
//  notification and invalidates dependent views, so that is the channel — which means
//  per-view plumbing rather than a palette change. That is the right shape anyway: the
//  fix for DWC is never a different colour, it is an ADDED CUE.
//
//  FOUR ENTRY POINTS, BECAUSE THERE ARE FOUR GEOMETRIES
//  ---------------------------------------------------
//  lines → dash · bodies/bars → hollow · areas → hatch · dots/labels → glyph.
//  Anything with fewer becomes a special case somebody re-invents inconsistently.
//

import SwiftUI

enum AppSentiment {

    // MARK: - Lines

    /// Solid for positive, dashed for negative.
    ///
    /// Exactly ONE of the pair changes. Dashing both would make the dash decorative and
    /// encode nothing — the cue has to be a DIFFERENCE, not a texture.
    static func strokeStyle(isPositive: Bool,
                            differentiate: Bool,
                            lineWidth: CGFloat = 2,
                            lineCap: CGLineCap = .round,
                            lineJoin: CGLineJoin = .round,
                            dash: [CGFloat] = [5, 3]) -> StrokeStyle {
        StrokeStyle(lineWidth: lineWidth,
                    lineCap: lineCap,
                    lineJoin: lineJoin,
                    dash: (differentiate && !isPositive) ? dash : [])
    }

    // MARK: - Bodies and bars

    /// Candle / OHLC body: `nil` means "fill it", a width means "stroke it hollow".
    ///
    /// Hollow-up / filled-down is the standard convention in every trading platform, and
    /// it costs ZERO layout — which is why it is the treatment of choice for the two OHLC
    /// renderers rather than a dash.
    ///
    /// ⚠️ `CandlestickChartRenderer` ALREADY uses outline-vs-fill to encode extended-hours
    /// trading. Two encodings cannot share one channel: keep extended hours on opacity and
    /// leave outline/fill to direction.
    static func hollowBodyLineWidth(isPositive: Bool,
                                    differentiate: Bool,
                                    lineWidth: CGFloat = 1.5) -> CGFloat? {
        (differentiate && isPositive) ? lineWidth : nil
    }

    // MARK: - Areas

    /// A 45° hatch for a filled REGION — good/bad bands, stacked segments — where a dash
    /// on an outline would be invisible because there is no outline to dash.
    ///
    /// Returns an EMPTY path when `differentiate` is false, so a caller can add it
    /// unconditionally as an overlay and pay nothing when the setting is off.
    static func hatch(in rect: CGRect,
                      differentiate: Bool,
                      spacing: CGFloat = 6) -> Path {
        var path = Path()
        guard differentiate, rect.width > 0, rect.height > 0 else { return path }
        // Sweep x from -height so the 45° lines cover the top-left corner too.
        var x = rect.minX - rect.height
        while x < rect.maxX {
            path.move(to: CGPoint(x: x, y: rect.maxY))
            path.addLine(to: CGPoint(x: x + rect.height, y: rect.minY))
            x += spacing
        }
        return path
    }

    // MARK: - Dots and labels

    /// A leading direction glyph for a value label — "▲ " / "▼ " — or "" when off.
    ///
    /// For marks that have room for a character but not for a legend. Prefer a real SF
    /// Symbol where the layout allows one; this exists for `Text` interpolation and for
    /// `GraphicsContext.draw(_:at:)` inside a `Canvas`, where a `View` is not available.
    static func marker(isPositive: Bool, differentiate: Bool) -> String {
        guard differentiate else { return "" }
        return isPositive ? "▲ " : "▼ "
    }

    /// SF Symbol name for the same job where a real `Image` fits.
    static func symbolName(isPositive: Bool) -> String {
        isPositive ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill"
    }
}

// MARK: - Environment

/// Test/preview override for `accessibilityDifferentiateWithoutColor`.
///
/// SwiftUI's own key is GET-ONLY (`EnvironmentValues.accessibilityDifferentiateWithoutColor`
/// has no setter outside the `_accessibility…` SPI), so a `#Preview` cannot turn it on and
/// neither can a snapshot harness. This key is the supported way in. Modelled on the
/// `IsActiveTabKey` pattern in `Views/Modifiers/GlobalAudioOverlay.swift`, which is the
/// codebase's existing precedent for a custom environment key.
private struct DifferentiateOverrideKey: EnvironmentKey {
    static let defaultValue: Bool? = nil
}

extension EnvironmentValues {
    var caydexDifferentiateOverride: Bool? {
        get { self[DifferentiateOverrideKey.self] }
        set { self[DifferentiateOverrideKey.self] = newValue }
    }

    /// The effective Differentiate Without Color flag — READ THIS, not the system key.
    ///
    /// Falls through to the real accessibility setting unless a preview or test has
    /// overridden it. Views read it with
    /// `@Environment(\.differentiateWithoutColor) private var differentiate`.
    var differentiateWithoutColor: Bool {
        caydexDifferentiateOverride ?? accessibilityDifferentiateWithoutColor
    }
}

extension View {
    /// Force the flag on/off for a `#Preview` or a harness run.
    func differentiateWithoutColor(_ on: Bool) -> some View {
        environment(\.caydexDifferentiateOverride, on)
    }
}
