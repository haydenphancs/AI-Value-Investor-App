//
//  AppTheme.swift
//  ios
//
//  AI Value Investor App - Theme System
//

import SwiftUI

// MARK: - App Colors
struct AppColors {
    // Background Colors
    static let background = Color(hex: "171B26")
    static let cardBackground = Color(hex: "1E2330")
    static let cardBackgroundLight = Color(hex: "252B3B")

    // Accent Colors
    static let primaryBlue = Color(hex: "3B82F6")
    static let accentCyan = Color(hex: "06B6D4")
    static let accentYellow = Color(hex: "FACC15")

    // Sentiment Colors
    static let bullish = Color(hex: "22C55E")
    static let bearish = Color(hex: "EF4444")
    static let neutral = Color(hex: "F59E0B")

    // Text Colors
    static let textPrimary = Color.white
    static let textSecondary = Color(hex: "9CA3AF")
    static let textMuted = Color(hex: "6B7280")

    // Alert Colors
    static let alertOrange = Color(hex: "F97316")
    static let alertBlue = Color(hex: "3B82F6")
    static let alertPurple = Color(hex: "A855F7")

    // Card Gradient Colors
    static let microsoftBlue = Color(hex: "0078D4")
    static let googleBlue = Color(hex: "4285F4")
    static let amdRed = Color(hex: "ED1C24")

    // Tab Bar
    static let tabBarBackground = Color(hex: "171B26")
    static let tabBarSelected = Color(hex: "3B82F6")
    static let tabBarUnselected = Color(hex: "6B7280")

    // Growth Chart Colors
    static let growthBarBlue = Color(hex: "5B9CF6")
    static let growthYoYYellow = Color(hex: "FACC15")
    static let growthSectorGray = Color(hex: "9CA3AF")
    static let chipSelectedBackground = Color(hex: "3B82F6")
    static let chipUnselectedBackground = Color(hex: "2D3548")
    static let toggleBackground = Color(hex: "1E2330")
    static let toggleSelectedBackground = Color(hex: "374151")

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

// MARK: - App Typography
//
// 5 Semantic Levels: Title → Heading → Body → Label → Caption
// Plus specialized tiers for financial data (rounded) and SF Symbol icons.
// All point sizes preserved from the original design for layout stability.
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
    static let headingSmall = Font.system(size: 17, weight: .semibold)

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
    // Rounded design for numeric emphasis in financial context
    static let dataHero    = Font.system(size: 32, weight: .bold, design: .rounded)
    static let dataDisplay = Font.system(size: 28, weight: .bold, design: .rounded)
    static let dataTitle   = Font.system(size: 22, weight: .bold, design: .rounded)
    static let dataHeading = Font.system(size: 20, weight: .bold, design: .rounded)
    static let dataLarge   = Font.system(size: 18, weight: .bold, design: .rounded)
    static let dataMedium  = Font.system(size: 14, weight: .bold, design: .rounded)
    static let dataSmall   = Font.system(size: 10, weight: .semibold, design: .rounded)

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
