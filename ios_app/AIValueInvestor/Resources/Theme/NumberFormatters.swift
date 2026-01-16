import Foundation

/// Number formatting utilities for financial data display
enum NumberFormatters {

    // MARK: - Currency Formatters

    /// Formats large numbers with appropriate suffix (K, M, B, T)
    static func formatLargeNumber(_ value: Double, decimals: Int = 2) -> String {
        let trillion = 1_000_000_000_000.0
        let billion = 1_000_000_000.0
        let million = 1_000_000.0
        let thousand = 1_000.0

        let absValue = abs(value)
        let sign = value < 0 ? "-" : ""

        if absValue >= trillion {
            return "\(sign)\(formatDecimal(absValue / trillion, decimals: decimals))T"
        } else if absValue >= billion {
            return "\(sign)\(formatDecimal(absValue / billion, decimals: decimals))B"
        } else if absValue >= million {
            return "\(sign)\(formatDecimal(absValue / million, decimals: decimals))M"
        } else if absValue >= thousand {
            return "\(sign)\(formatDecimal(absValue / thousand, decimals: decimals))K"
        } else {
            return "\(sign)\(formatDecimal(absValue, decimals: decimals))"
        }
    }

    /// Formats a value in billions
    static func formatBillions(_ value: Double, decimals: Int = 1) -> String {
        let formatted = formatDecimal(value, decimals: decimals)
        return "\(formatted)B"
    }

    /// Formats a percentage value
    static func formatPercentage(_ value: Double, decimals: Int = 2, includeSign: Bool = true) -> String {
        let formatted = String(format: "%.\(decimals)f", abs(value))
        let sign: String

        if includeSign {
            if value > 0 {
                sign = "+"
            } else if value < 0 {
                sign = "-"
            } else {
                sign = ""
            }
        } else {
            sign = value < 0 ? "-" : ""
        }

        return "\(sign)\(formatted)%"
    }

    /// Formats a percentage change with color-coding indicator
    static func formatPercentageChange(_ value: Double) -> (text: String, isPositive: Bool) {
        let text = formatPercentage(value, decimals: 2, includeSign: true)
        return (text, value >= 0)
    }

    // MARK: - Private Helpers

    private static func formatDecimal(_ value: Double, decimals: Int) -> String {
        String(format: "%.\(decimals)f", value)
    }
}

// MARK: - Double Extensions

extension Double {
    /// Formats the number with appropriate suffix for financial display
    var formattedLarge: String {
        NumberFormatters.formatLargeNumber(self)
    }

    /// Formats the number as billions
    var formattedBillions: String {
        NumberFormatters.formatBillions(self)
    }

    /// Formats the number as a percentage
    var formattedPercentage: String {
        NumberFormatters.formatPercentage(self)
    }

    /// Formats as percentage without sign
    var formattedPercentageUnsigned: String {
        NumberFormatters.formatPercentage(self, includeSign: false)
    }
}
