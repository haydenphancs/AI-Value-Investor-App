import Foundation

/// Represents a single data point in the growth chart
struct GrowthDataPoint: Identifiable, Equatable {
    let id = UUID()

    /// Period label (e.g., "2020", "2021", "Q1 2024")
    let period: String

    /// Actual value for this period (in raw units, not formatted)
    let value: Double

    /// Year-over-Year percentage change
    let yoyPercentage: Double

    /// Sector average YoY percentage for comparison
    let sectorAverageYoY: Double

    /// Convenience initializer with all parameters
    init(period: String, value: Double, yoyPercentage: Double, sectorAverageYoY: Double) {
        self.period = period
        self.value = value
        self.yoyPercentage = yoyPercentage
        self.sectorAverageYoY = sectorAverageYoY
    }
}

/// Complete growth data for a specific metric
struct GrowthMetricData: Identifiable, Equatable {
    let id = UUID()

    /// The metric type this data represents
    let metricType: GrowthMetricType

    /// Array of data points sorted by period
    let dataPoints: [GrowthDataPoint]

    /// The maximum value across all data points (for chart scaling)
    var maxValue: Double {
        dataPoints.map(\.value).max() ?? 0
    }

    /// The minimum value across all data points
    var minValue: Double {
        dataPoints.map(\.value).min() ?? 0
    }

    /// The maximum YoY percentage (for chart scaling)
    var maxYoY: Double {
        dataPoints.map(\.yoyPercentage).max() ?? 0
    }

    /// The minimum YoY percentage
    var minYoY: Double {
        dataPoints.map(\.yoyPercentage).min() ?? 0
    }
}

/// Response model for growth data from API
struct GrowthDataResponse: Codable {
    let ticker: String
    let metricType: String
    let periodType: String
    let data: [GrowthDataPointDTO]

    struct GrowthDataPointDTO: Codable {
        let period: String
        let value: Double
        let yoyPercentage: Double
        let sectorAverageYoY: Double

        enum CodingKeys: String, CodingKey {
            case period
            case value
            case yoyPercentage = "yoy_percentage"
            case sectorAverageYoY = "sector_average_yoy"
        }
    }
}

// MARK: - Sample Data for Previews and Testing

extension GrowthMetricData {
    /// Sample revenue data matching the design reference
    static let sampleRevenue = GrowthMetricData(
        metricType: .revenue,
        dataPoints: [
            GrowthDataPoint(period: "2020", value: 105, yoyPercentage: -2.30, sectorAverageYoY: 40),
            GrowthDataPoint(period: "2021", value: 98, yoyPercentage: -7.42, sectorAverageYoY: 55),
            GrowthDataPoint(period: "2022", value: 175, yoyPercentage: 7.92, sectorAverageYoY: 110),
            GrowthDataPoint(period: "2023", value: 215, yoyPercentage: 5.22, sectorAverageYoY: 135),
            GrowthDataPoint(period: "2024", value: 145, yoyPercentage: -10.92, sectorAverageYoY: 85),
            GrowthDataPoint(period: "2025", value: 105, yoyPercentage: -3.32, sectorAverageYoY: 50)
        ]
    )

    /// Sample EPS data
    static let sampleEPS = GrowthMetricData(
        metricType: .eps,
        dataPoints: [
            GrowthDataPoint(period: "2020", value: 3.28, yoyPercentage: 5.12, sectorAverageYoY: 2.5),
            GrowthDataPoint(period: "2021", value: 3.69, yoyPercentage: 12.50, sectorAverageYoY: 8.0),
            GrowthDataPoint(period: "2022", value: 4.18, yoyPercentage: 13.28, sectorAverageYoY: 10.5),
            GrowthDataPoint(period: "2023", value: 5.02, yoyPercentage: 20.10, sectorAverageYoY: 12.0),
            GrowthDataPoint(period: "2024", value: 4.85, yoyPercentage: -3.39, sectorAverageYoY: 5.0),
            GrowthDataPoint(period: "2025", value: 5.15, yoyPercentage: 6.19, sectorAverageYoY: 7.5)
        ]
    )

    /// Sample Net Income data
    static let sampleNetIncome = GrowthMetricData(
        metricType: .netIncome,
        dataPoints: [
            GrowthDataPoint(period: "2020", value: 45, yoyPercentage: -5.20, sectorAverageYoY: 3.0),
            GrowthDataPoint(period: "2021", value: 52, yoyPercentage: 15.56, sectorAverageYoY: 12.0),
            GrowthDataPoint(period: "2022", value: 78, yoyPercentage: 50.00, sectorAverageYoY: 25.0),
            GrowthDataPoint(period: "2023", value: 95, yoyPercentage: 21.79, sectorAverageYoY: 18.0),
            GrowthDataPoint(period: "2024", value: 72, yoyPercentage: -24.21, sectorAverageYoY: -5.0),
            GrowthDataPoint(period: "2025", value: 68, yoyPercentage: -5.56, sectorAverageYoY: 2.0)
        ]
    )

    /// Sample Operating Profit data
    static let sampleOperatingProfit = GrowthMetricData(
        metricType: .operatingProfit,
        dataPoints: [
            GrowthDataPoint(period: "2020", value: 55, yoyPercentage: 2.30, sectorAverageYoY: 5.0),
            GrowthDataPoint(period: "2021", value: 48, yoyPercentage: -12.73, sectorAverageYoY: 3.0),
            GrowthDataPoint(period: "2022", value: 82, yoyPercentage: 70.83, sectorAverageYoY: 35.0),
            GrowthDataPoint(period: "2023", value: 110, yoyPercentage: 34.15, sectorAverageYoY: 25.0),
            GrowthDataPoint(period: "2024", value: 88, yoyPercentage: -20.00, sectorAverageYoY: -2.0),
            GrowthDataPoint(period: "2025", value: 92, yoyPercentage: 4.55, sectorAverageYoY: 8.0)
        ]
    )

    /// Sample Free Cash Flow data
    static let sampleFreeCashFlow = GrowthMetricData(
        metricType: .freeCashFlow,
        dataPoints: [
            GrowthDataPoint(period: "2020", value: 38, yoyPercentage: 8.57, sectorAverageYoY: 5.0),
            GrowthDataPoint(period: "2021", value: 42, yoyPercentage: 10.53, sectorAverageYoY: 8.0),
            GrowthDataPoint(period: "2022", value: 65, yoyPercentage: 54.76, sectorAverageYoY: 30.0),
            GrowthDataPoint(period: "2023", value: 85, yoyPercentage: 30.77, sectorAverageYoY: 22.0),
            GrowthDataPoint(period: "2024", value: 62, yoyPercentage: -27.06, sectorAverageYoY: -8.0),
            GrowthDataPoint(period: "2025", value: 58, yoyPercentage: -6.45, sectorAverageYoY: 3.0)
        ]
    )
}
