import SwiftUI

/// App-wide color definitions matching the design system
enum AppColors {

    // MARK: - Background Colors

    /// Primary dark background - #0D1421
    static let backgroundPrimary = Color(red: 0.051, green: 0.078, blue: 0.129)

    /// Card/Section background - #151B2B
    static let backgroundCard = Color(red: 0.082, green: 0.106, blue: 0.169)

    /// Elevated surface background - #1E2538
    static let backgroundElevated = Color(red: 0.118, green: 0.145, blue: 0.220)

    // MARK: - Accent Colors

    /// Primary blue accent - #3B82F6
    static let accentBlue = Color(red: 0.231, green: 0.510, blue: 0.965)

    /// Secondary blue (lighter) - #60A5FA
    static let accentBlueLight = Color(red: 0.376, green: 0.647, blue: 0.980)

    /// Chart bar blue - #4DA3FF
    static let chartBarBlue = Color(red: 0.302, green: 0.639, blue: 1.0)

    // MARK: - Text Colors

    /// Primary text color - White
    static let textPrimary = Color.white

    /// Secondary text color - #9CA3AF
    static let textSecondary = Color(red: 0.612, green: 0.639, blue: 0.686)

    /// Tertiary text color - #6B7280
    static let textTertiary = Color(red: 0.420, green: 0.447, blue: 0.502)

    // MARK: - Semantic Colors

    /// Positive/Growth color - #22C55E
    static let positive = Color(red: 0.133, green: 0.773, blue: 0.369)

    /// Negative/Decline color - #EF4444
    static let negative = Color(red: 0.937, green: 0.267, blue: 0.267)

    /// Warning/Neutral color - #FACC15
    static let warning = Color(red: 0.980, green: 0.800, blue: 0.082)

    // MARK: - Chart Colors

    /// YoY line color (yellow/gold) - #F59E0B
    static let chartYoY = Color(red: 0.961, green: 0.620, blue: 0.043)

    /// Sector average line color (gray) - #6B7280
    static let chartSectorAverage = Color(red: 0.420, green: 0.447, blue: 0.502)

    /// Value bar color (blue) - #4DA3FF
    static let chartValue = Color(red: 0.302, green: 0.639, blue: 1.0)

    // MARK: - Component Colors

    /// Tab/Pill unselected background - #2D3748
    static let tabUnselected = Color(red: 0.176, green: 0.216, blue: 0.282)

    /// Tab/Pill unselected text - #9CA3AF
    static let tabUnselectedText = Color(red: 0.612, green: 0.639, blue: 0.686)

    /// Toggle background - #1F2937
    static let toggleBackground = Color(red: 0.122, green: 0.161, blue: 0.216)

    /// Divider color - #374151
    static let divider = Color(red: 0.216, green: 0.255, blue: 0.318)

    /// Border color - #4B5563
    static let border = Color(red: 0.294, green: 0.333, blue: 0.388)
}

// MARK: - Color Extensions

extension Color {
    /// Returns appropriate color for percentage value (positive = green, negative = red)
    static func forPercentage(_ value: Double) -> Color {
        if value > 0 {
            return AppColors.positive
        } else if value < 0 {
            return AppColors.negative
        } else {
            return AppColors.textSecondary
        }
    }
}
