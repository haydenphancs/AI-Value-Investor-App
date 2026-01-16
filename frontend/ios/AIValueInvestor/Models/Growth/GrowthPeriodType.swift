import Foundation

/// Represents the time period granularity for growth data
enum GrowthPeriodType: String, CaseIterable, Identifiable {
    case annual = "Annual"
    case quarterly = "Quarterly"

    var id: String { rawValue }

    /// Display name for the toggle
    var displayName: String {
        rawValue
    }

    /// Description of the period type
    var description: String {
        switch self {
        case .annual:
            return "Year-over-year comparison of annual figures"
        case .quarterly:
            return "Quarter-over-quarter comparison"
        }
    }
}
