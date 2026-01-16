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
    static let tabBarBackground = Color(hex: "0D1117")
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
struct AppTypography {
    // Headlines
    static let largeTitle = Font.system(size: 28, weight: .bold)
    static let title = Font.system(size: 22, weight: .bold)
    static let title2 = Font.system(size: 20, weight: .semibold)
    static let title3 = Font.system(size: 18, weight: .semibold)

    // Body
    static let headline = Font.system(size: 17, weight: .semibold)
    static let body = Font.system(size: 15, weight: .regular)
    static let bodyBold = Font.system(size: 15, weight: .semibold)
    static let callout = Font.system(size: 14, weight: .regular)
    static let calloutBold = Font.system(size: 14, weight: .semibold)

    // Small
    static let subheadline = Font.system(size: 13, weight: .regular)
    static let footnote = Font.system(size: 12, weight: .regular)
    static let footnoteBold = Font.system(size: 12, weight: .semibold)
    static let caption = Font.system(size: 11, weight: .regular)
    static let captionBold = Font.system(size: 11, weight: .medium)

    // Ticker Numbers
    static let tickerPrice = Font.system(size: 14, weight: .bold, design: .rounded)
    static let tickerChange = Font.system(size: 10, weight: .semibold, design: .rounded)
    static let tickerName = Font.system(size: 9, weight: .regular)
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
