//
//  ProfitPowerModels.swift
//  ios
//
//  Data models for the Profit Power Section in the Financial tab
//  Displays margin metrics over time with sector comparison
//

import Foundation
import SwiftUI

// MARK: - Profit Power Period Type

enum ProfitPowerPeriodType: String, CaseIterable, Identifiable {
    case annual = "Annual"
    case quarterly = "Quarterly"

    var id: String { rawValue }
}

// MARK: - Profit Margin Type

enum ProfitMarginType: String, CaseIterable, Identifiable {
    case grossMargin = "Gross Margin"
    case operatingMargin = "Operating Margin"
    case fcfMargin = "FCF Margin"
    case netMargin = "Net Margin"
    case sectorAverage = "Sector Average Net Margin"

    var id: String { rawValue }

    var shortName: String {
        switch self {
        case .grossMargin: return "Gross Margin"
        case .operatingMargin: return "Operating Margin"
        case .fcfMargin: return "FCF Margin"
        case .netMargin: return "Net Margin"
        case .sectorAverage: return "Sector Average\nNet Margin"
        }
    }

    var color: Color {
        switch self {
        case .grossMargin: return AppColors.profitGrossMargin
        case .operatingMargin: return AppColors.profitOperatingMargin
        case .fcfMargin: return AppColors.profitFCFMargin
        case .netMargin: return AppColors.profitNetMargin
        case .sectorAverage: return AppColors.profitSectorAverage
        }
    }

    var isDashed: Bool {
        self == .sectorAverage
    }

    var description: String {
        switch self {
        case .grossMargin:
            return "Revenue minus cost of goods sold, divided by revenue. Shows pricing power and production efficiency."
        case .operatingMargin:
            return "Operating income divided by revenue. Measures operational efficiency before interest and taxes."
        case .fcfMargin:
            return "Free cash flow divided by revenue. Indicates how much cash the business generates relative to sales."
        case .netMargin:
            return "Net income divided by revenue. The bottom line profitability after all expenses."
        case .sectorAverage:
            return "Average net margin of peer companies in the same sector for comparison."
        }
    }
}

// MARK: - Profit Power Data Point

struct ProfitPowerDataPoint: Identifiable {
    let id = UUID()
    let period: String              // e.g., "2020", "2021" or "Q1 '24"
    let grossMargin: Double         // Percentage (e.g., 45.5 for 45.5%)
    let operatingMargin: Double     // Percentage
    let fcfMargin: Double           // Percentage
    let netMargin: Double           // Percentage
    let sectorAverageNetMargin: Double  // Percentage

    /// Returns margin value for a specific margin type
    func margin(for type: ProfitMarginType) -> Double {
        switch type {
        case .grossMargin: return grossMargin
        case .operatingMargin: return operatingMargin
        case .fcfMargin: return fcfMargin
        case .netMargin: return netMargin
        case .sectorAverage: return sectorAverageNetMargin
        }
    }
}

// MARK: - Profit Power Section Data

struct ProfitPowerSectionData {
    let annualData: [ProfitPowerDataPoint]
    let quarterlyData: [ProfitPowerDataPoint]

    func dataPoints(for period: ProfitPowerPeriodType) -> [ProfitPowerDataPoint] {
        switch period {
        case .annual: return annualData
        case .quarterly: return quarterlyData
        }
    }

    /// Get all margin values for chart scaling
    var allMarginValues: [Double] {
        let annual = annualData.flatMap { [
            $0.grossMargin, $0.operatingMargin, $0.fcfMargin,
            $0.netMargin, $0.sectorAverageNetMargin
        ] }
        let quarterly = quarterlyData.flatMap { [
            $0.grossMargin, $0.operatingMargin, $0.fcfMargin,
            $0.netMargin, $0.sectorAverageNetMargin
        ] }
        return annual + quarterly
    }

    var maxMargin: Double {
        (allMarginValues.max() ?? 50) * 1.1
    }

    var minMargin: Double {
        min(allMarginValues.min() ?? 0, 0)
    }
}

// MARK: - Sample Data

extension ProfitPowerSectionData {
    static let sampleData = ProfitPowerSectionData(
        annualData: [
            ProfitPowerDataPoint(
                period: "2020",
                grossMargin: 38.2,
                operatingMargin: 6.5,
                fcfMargin: 5.2,
                netMargin: 9.8,
                sectorAverageNetMargin: 8.5
            ),
            ProfitPowerDataPoint(
                period: "2021",
                grossMargin: 38.5,
                operatingMargin: 8.2,
                fcfMargin: 4.8,
                netMargin: 12.5,
                sectorAverageNetMargin: 9.2
            ),
            ProfitPowerDataPoint(
                period: "2022",
                grossMargin: 45.0,
                operatingMargin: 14.8,
                fcfMargin: 11.5,
                netMargin: 21.2,
                sectorAverageNetMargin: 22.5
            ),
            ProfitPowerDataPoint(
                period: "2023",
                grossMargin: 48.2,
                operatingMargin: 18.5,
                fcfMargin: 11.2,
                netMargin: 21.5,
                sectorAverageNetMargin: 20.8
            ),
            ProfitPowerDataPoint(
                period: "2024",
                grossMargin: 49.5,
                operatingMargin: 14.5,
                fcfMargin: 11.8,
                netMargin: 20.8,
                sectorAverageNetMargin: 21.2
            ),
            ProfitPowerDataPoint(
                period: "2025",
                grossMargin: 52.0,
                operatingMargin: 16.2,
                fcfMargin: 12.5,
                netMargin: 22.0,
                sectorAverageNetMargin: 21.0
            )
        ],
        quarterlyData: [
            ProfitPowerDataPoint(
                period: "Q1 '24",
                grossMargin: 48.8,
                operatingMargin: 15.2,
                fcfMargin: 10.5,
                netMargin: 20.2,
                sectorAverageNetMargin: 19.8
            ),
            ProfitPowerDataPoint(
                period: "Q2 '24",
                grossMargin: 49.2,
                operatingMargin: 14.8,
                fcfMargin: 11.8,
                netMargin: 21.0,
                sectorAverageNetMargin: 20.5
            ),
            ProfitPowerDataPoint(
                period: "Q3 '24",
                grossMargin: 50.1,
                operatingMargin: 13.5,
                fcfMargin: 12.2,
                netMargin: 20.5,
                sectorAverageNetMargin: 21.2
            ),
            ProfitPowerDataPoint(
                period: "Q4 '24",
                grossMargin: 51.5,
                operatingMargin: 14.2,
                fcfMargin: 13.0,
                netMargin: 21.8,
                sectorAverageNetMargin: 21.5
            ),
            ProfitPowerDataPoint(
                period: "Q1 '25",
                grossMargin: 52.2,
                operatingMargin: 15.5,
                fcfMargin: 12.8,
                netMargin: 22.2,
                sectorAverageNetMargin: 20.8
            ),
            ProfitPowerDataPoint(
                period: "Q2 '25",
                grossMargin: 52.8,
                operatingMargin: 16.8,
                fcfMargin: 13.2,
                netMargin: 22.5,
                sectorAverageNetMargin: 21.0
            )
        ]
    )
}

// MARK: - Profit Power Info Item

struct ProfitPowerInfoItem: Identifiable {
    let id = UUID()
    let title: String
    let description: String
    let icon: String
    let example: String?

    init(title: String, description: String, icon: String, example: String? = nil) {
        self.title = title
        self.description = description
        self.icon = icon
        self.example = example
    }
}

extension ProfitPowerInfoItem {
    static let valueInvestingTips: [ProfitPowerInfoItem] = [
        ProfitPowerInfoItem(
            title: "Gross Margin Stability",
            description: "A stable or expanding gross margin indicates pricing power and competitive moat. Declining gross margins may signal increasing competition or rising input costs.",
            icon: "chart.line.uptrend.xyaxis",
            example: "Apple's gross margin consistently above 38% demonstrates strong brand pricing power."
        ),
        ProfitPowerInfoItem(
            title: "Operating Leverage",
            description: "When operating margin grows faster than gross margin, it shows the company is achieving scale. Fixed costs are being spread over more revenue.",
            icon: "scalemass.fill",
            example: "Operating margin expanding from 10% to 15% while revenue doubles indicates excellent operating leverage."
        ),
        ProfitPowerInfoItem(
            title: "Free Cash Flow Quality",
            description: "FCF margin shows how much actual cash the business generates. Companies can manipulate earnings but not cash. Higher is better.",
            icon: "banknote.fill",
            example: "A company with 15% net margin but only 5% FCF margin may have aggressive accounting."
        ),
        ProfitPowerInfoItem(
            title: "Net Margin Trends",
            description: "The bottom line. Compare to sector average - consistently above sector suggests competitive advantages worth paying for.",
            icon: "arrow.up.right.circle.fill",
            example: "Net margin 5% above sector average for 5+ years indicates a durable moat."
        ),
        ProfitPowerInfoItem(
            title: "Margin Compression Warning",
            description: "When all margins decline together, it's a red flag. Could indicate new competition, market saturation, or loss of pricing power.",
            icon: "exclamationmark.triangle.fill",
            example: "If gross margin drops while competitors maintain theirs, investigate the cause."
        ),
        ProfitPowerInfoItem(
            title: "Sector Comparison",
            description: "The dashed line shows sector average. Persistent outperformance justifies premium valuation; persistent underperformance warrants caution.",
            icon: "chart.bar.xaxis",
            example: "Operating 3% above sector average over a full business cycle shows genuine excellence."
        )
    ]
}
