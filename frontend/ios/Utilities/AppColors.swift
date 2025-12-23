//
//  AppColors.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct AppColors {
    // MARK: - Background Colors
    static let background = Color(hex: "#0A0E1A")
    static let cardBackground = Color(hex: "#1A1F2E")
    static let surfaceBackground = Color(hex: "#151B2B")

    // MARK: - Text Colors
    static let primaryText = Color.white
    static let secondaryText = Color(hex: "#8B92A8")
    static let tertiaryText = Color(hex: "#5A6175")

    // MARK: - Accent Colors
    static let positive = Color(hex: "#00D09E")
    static let negative = Color(hex: "#FF4444")
    static let blue = Color(hex: "#4A90E2")
    static let lightBlue = Color(hex: "#5BA4F5")

    // MARK: - Chart Colors
    static let chartPositive = Color(hex: "#00D09E")
    static let chartNegative = Color(hex: "#FF4444")

    // MARK: - Rating Colors
    static let ratingBuy = Color(hex: "#00D09E")
    static let ratingHold = Color(hex: "#FFA500")
    static let ratingSell = Color(hex: "#FF4444")

    // MARK: - Education Type Colors
    static let educationGradientStart = Color(hex: "#7B2CBF")
    static let educationGradientEnd = Color(hex: "#C77DFF")
    static let educationOrange = Color(hex: "#FF6B35")
    static let educationBlue = Color(hex: "#4A90E2")

    // MARK: - Border Colors
    static let borderColor = Color(hex: "#2A3142")

    // MARK: - Tab Bar
    static let tabBarBackground = Color(hex: "#0F1421")
    static let tabBarSelected = Color(hex: "#4A90E2")
    static let tabBarUnselected = Color(hex: "#5A6175")
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
            (a, r, g, b) = (1, 1, 1, 0)
        }

        self.init(
            .sRGB,
            red: Double(r) / 255,
            green: Double(g) / 255,
            blue:  Double(b) / 255,
            opacity: Double(a) / 255
        )
    }
}
