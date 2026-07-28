//
//  AppTheme.swift
//  ios
//
//  AI Value Investor App - Theme System
//

import SwiftUI

// MARK: - App Colors
//
// APPEARANCE / LIGHT MODE
// -----------------------
// The theme (System / Dark / Light) is driven by `overrideUserInterfaceStyle`
// on the window — see `Core/Utilities/AppearanceManager.swift`. For the app to
// actually *look* different in light mode, the SURFACE and TEXT tokens below
// resolve per `userInterfaceStyle` at render time via `Color(lightHex:darkHex:)`
// (a dynamic `UIColor` under the hood — see the extension at the bottom of this
// file). ACCENT / SENTIMENT / BRAND / CHART tokens are intentionally CONSTANT
// across modes: a brand blue stays blue, gains stay green, losses stay red —
// these read acceptably on both a dark and a light surface, matching how
// Robinhood/Webull/Yahoo treat their semantic colors.
//
// The default choice is Dark (AppearanceManager.current == .dark), and every
// adaptive token's `darkHex` is the ORIGINAL value — so the shipped dark look
// is byte-for-byte unchanged unless the user explicitly picks Light or System.
struct AppColors {
    // Background Colors — adaptive
    static let background = Color(lightHex: "F4F5F8", darkHex: "171B26")
    static let cardBackground = Color(lightHex: "FFFFFF", darkHex: "1E2330")
    static let cardBackgroundLight = Color(lightHex: "EDF0F5", darkHex: "252B3B")

    // Accent Colors — constant across modes
    static let primaryBlue = Color(hex: "3B82F6")
    static let accentCyan = Color(hex: "06B6D4")
    static let accentYellow = Color(hex: "FACC15")

    // Sentiment Colors — constant across modes
    static let bullish = Color(hex: "22C55E")
    static let bearish = Color(hex: "EF4444")
    static let neutral = Color(hex: "F59E0B")

    // Text Colors — adaptive
    static let textPrimary = Color(lightHex: "111827", darkHex: "FFFFFF")
    static let textSecondary = Color(lightHex: "5C6470", darkHex: "9CA3AF")
    static let textMuted = Color(lightHex: "8A93A0", darkHex: "6B7280")

    // Alert Colors — constant across modes
    static let alertOrange = Color(hex: "F97316")
    static let alertBlue = Color(hex: "3B82F6")
    static let alertPurple = Color(hex: "A855F7")

    // Card Gradient Colors — constant (brand marks)
    static let microsoftBlue = Color(hex: "0078D4")
    static let googleBlue = Color(hex: "4285F4")
    static let amdRed = Color(hex: "ED1C24")

    // Tab Bar
    static let tabBarBackground = Color(lightHex: "FFFFFF", darkHex: "171B26")
    static let tabBarSelected = Color(hex: "3B82F6")
    static let tabBarUnselected = Color(lightHex: "8A93A0", darkHex: "6B7280")

    // Growth Chart Colors — series accents constant; control surfaces adaptive
    static let growthBarBlue = Color(hex: "5B9CF6")
    static let growthYoYYellow = Color(hex: "FACC15")
    static let growthSectorGray = Color(hex: "9CA3AF")
    static let chipSelectedBackground = Color(hex: "3B82F6")
    static let chipUnselectedBackground = Color(lightHex: "E7EAF0", darkHex: "2D3548")
    static let toggleBackground = Color(lightHex: "EDF0F5", darkHex: "1E2330")
    static let toggleSelectedBackground = Color(lightHex: "D5DAE3", darkHex: "374151")

    // Profit Power Chart Colors
    static let profitGrossMargin = Color(hex: "3B82F6")       // Blue - matches primaryBlue
    static let profitOperatingMargin = Color(hex: "F97316")   // Orange
    static let profitFCFMargin = Color(hex: "A855F7")         // Purple
    static let profitNetMargin = Color(hex: "22C55E")         // Green - matches bullish
    static let profitSectorAverage = Color(hex: "9CA3AF")     // Gray - matches textSecondary

    // Signal of Confidence Chart Colors
    static let confidenceDividends = Color(hex: "3B82F6")     // Blue - matches primaryBlue
    static let confidenceBuybacks = Color(hex: "22C55E")      // Green - matches bullish
    static let confidenceSharesOutstanding = Color(hex: "FACC15") // Yellow - matches growthYoYYellow
}

// MARK: - Color Extension for Hex
extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
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
            .sRGB,
            red: Double(r) / 255,
            green: Double(g) / 255,
            blue: Double(b) / 255,
            opacity: Double(a) / 255
        )
    }
}

// MARK: - Adaptive (light / dark) color support
//
// Backs the surface + text tokens in `AppColors`. The dynamic `UIColor` closure
// is re-evaluated by UIKit/SwiftUI whenever the effective `userInterfaceStyle`
// changes (including when `AppearanceManager` flips the window override), so a
// token switches instantly with no view rebuild required.

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
    /// Adaptive token: resolves to `light` in light mode and to `dark` in dark
    /// (and for `.unspecified`). Because the app's default appearance is Dark,
    /// callers passing the original value as `darkHex` keep the exact shipped
    /// look until the user chooses Light or System.
    init(lightHex light: String, darkHex dark: String) {
        self = Color(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .light
                ? UIColor(hexString: light)
                : UIColor(hexString: dark)
        })
    }
}

// MARK: - App Typography
//
// 5 Semantic Levels: Title → Heading → Body → Label → Caption
// Plus specialized tiers for financial data (rounded) and SF Symbol icons.
//
// TWO COMFORT TIERS (the rule that keeps cards balanced — modeled on
// Robinhood/Webull/Yahoo Finance): READING PROSE breathes, DENSE DATA stays tight.
//   • Reading prose (insight/narrative/description sentences) → `body` (15, Apple Subheadline — compact, data-dense like Webull).
//   • De-emphasize metadata with COLOR (textMuted/textSecondary), NOT by
//     shrinking below ~13 — never render a human-readable sentence at caption
//     sizes (≤11) or captionTiny (9).
//   • Chart axes, table cells, badges = the DATA tier → caption/labelSmall
//     (10–12) is correct; keep them small.
// Report-card ladder: badge 11 · metadata 13 (label) · prose 15 (body) ·
//   section title 16 (headingSmall) · hero number 16–20 (dataHeading). A section
//   header must never be smaller than the prose beneath it.
//

struct AppTypography {

    // ━━━ TITLE (Level 1) ━━━
    // Hero content, screen titles, major section headers
    static let titleHero    = Font.system(size: 32, weight: .bold)
    static let titleLarge   = Font.system(size: 28, weight: .bold)
    static let title        = Font.system(size: 22, weight: .bold)
    static let titleCompact = Font.system(size: 20, weight: .semibold)

    // ━━━ HEADING (Level 2) ━━━
    // Section & card headers, emphasized inline content
    static let heading      = Font.system(size: 18, weight: .semibold)
    static let headingSmall = Font.system(size: 16, weight: .semibold)

    // ━━━ BODY (Level 3) ━━━
    // Primary readable content
    static let bodyEmphasis      = Font.system(size: 15, weight: .semibold)
    static let body              = Font.system(size: 15, weight: .regular)
    static let bodySmallEmphasis = Font.system(size: 14, weight: .semibold)
    static let bodySmall         = Font.system(size: 14, weight: .regular)

    // ━━━ LABEL (Level 4) ━━━
    // Metadata, supporting info, subtitles
    static let labelEmphasis      = Font.system(size: 13, weight: .semibold)
    static let label              = Font.system(size: 13, weight: .regular)
    static let labelSmallEmphasis = Font.system(size: 12, weight: .semibold)
    static let labelSmall         = Font.system(size: 12, weight: .regular)

    // ━━━ CAPTION (Level 5) ━━━
    // Micro-copy, badges, chart axes, fine print
    static let captionEmphasis      = Font.system(size: 11, weight: .semibold)
    static let caption              = Font.system(size: 11, weight: .regular)
    static let captionSmallEmphasis = Font.system(size: 10, weight: .semibold)
    static let captionSmall         = Font.system(size: 10, weight: .regular)
    static let captionTiny          = Font.system(size: 9, weight: .regular)

    // ━━━ DATA (Financial numerics) ━━━
    // Rounded design for numeric emphasis in financial context.
    // .monospacedDigit() = tabular figures so prices/%/columns align and digits
    // don't jitter as values update — the fintech-standard treatment used by
    // Robinhood / Webull / Yahoo Finance. Numerals only (these tokens never back prose).
    static let dataHero    = Font.system(size: 32, weight: .bold, design: .rounded).monospacedDigit()
    static let dataDisplay = Font.system(size: 28, weight: .bold, design: .rounded).monospacedDigit()
    static let dataTitle   = Font.system(size: 22, weight: .bold, design: .rounded).monospacedDigit()
    static let dataHeading = Font.system(size: 20, weight: .bold, design: .rounded).monospacedDigit()
    static let dataLarge   = Font.system(size: 18, weight: .bold, design: .rounded).monospacedDigit()
    static let dataCompact = Font.system(size: 16, weight: .bold, design: .rounded).monospacedDigit()
    static let dataMedium  = Font.system(size: 14, weight: .bold, design: .rounded).monospacedDigit()
    static let dataSmall   = Font.system(size: 10, weight: .semibold, design: .rounded).monospacedDigit()

    // ━━━ ICONS (SF Symbol sizing) ━━━
    // Size-only tokens — add .fontWeight() modifier for weight control
    static let iconSplash  = Font.system(size: 80)
    static let iconHero    = Font.system(size: 48)
    static let iconXXL     = Font.system(size: 40)
    static let iconJumbo   = Font.system(size: 36)
    static let iconDisplay = Font.system(size: 32)
    static let iconXL      = Font.system(size: 24)
    static let iconLarge   = Font.system(size: 20)
    static let iconMedium  = Font.system(size: 18)
    static let iconDefault = Font.system(size: 16)
    static let iconSmall   = Font.system(size: 14)
    static let iconXS      = Font.system(size: 12)
    static let iconTiny    = Font.system(size: 10)
    static let iconMicro   = Font.system(size: 8)
}

// MARK: - App Spacing
struct AppSpacing {
    static let xxs: CGFloat = 2
    static let xs: CGFloat = 4
    static let sm: CGFloat = 8
    static let md: CGFloat = 12
    static let lg: CGFloat = 16
    static let xl: CGFloat = 20
    static let xxl: CGFloat = 24
    static let xxxl: CGFloat = 32
}

// MARK: - App Corner Radius
struct AppCornerRadius {
    static let small: CGFloat = 6
    static let medium: CGFloat = 10
    static let large: CGFloat = 14
    static let extraLarge: CGFloat = 18
    static let pill: CGFloat = 20
}

// MARK: - App Gradients

/// Shared multi-stop fills. Defined once so surfaces that are meant to match
/// cannot drift apart when one of them is restyled.
struct AppGradients {
    /// The Cay-AI mark: indigo → cyan, top-leading to bottom-trailing.
    ///
    /// Worn by the "Insight"/"Insights" label and by the AI-summary badge beside
    /// it. These two sit inches apart on the Updates card, so they read as one
    /// element — inlining the colours in each would let a later edit to one
    /// silently break the pairing.
    static let ai = LinearGradient(
        colors: [.indigo, .cyan],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    /// The same ramp at badge-fill strength. Kept beside `ai` so the tint always
    /// tracks the text it sits behind.
    static let aiSubtle = LinearGradient(
        colors: [Color.indigo.opacity(0.18), Color.cyan.opacity(0.18)],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )
}

// MARK: - App Shadows
struct AppShadows {
    static let cardShadow = Shadow(color: Color.black.opacity(0.2), radius: 8, x: 0, y: 4)
    static let buttonShadow = Shadow(color: Color.black.opacity(0.15), radius: 4, x: 0, y: 2)
}

struct Shadow {
    let color: Color
    let radius: CGFloat
    let x: CGFloat
    let y: CGFloat
}
