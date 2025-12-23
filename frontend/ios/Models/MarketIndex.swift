//
//  MarketIndex.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import Foundation

struct MarketIndex: Identifiable {
    let id = UUID()
    let name: String
    let value: String
    let changePercentage: Double
    let chartData: [Double]

    var isPositive: Bool {
        changePercentage >= 0
    }

    var formattedChange: String {
        let sign = isPositive ? "+" : ""
        return "\(sign)\(String(format: "%.2f", changePercentage))%"
    }
}

// MARK: - Mock Data
extension MarketIndex {
    static let mockData: [MarketIndex] = [
        MarketIndex(
            name: "S&P 500",
            value: "6,783.45",
            changePercentage: 0.85,
            chartData: [0.3, 0.5, 0.4, 0.6, 0.8, 0.7, 0.9]
        ),
        MarketIndex(
            name: "Nasdaq",
            value: "23,293.23",
            changePercentage: 0.85,
            chartData: [0.4, 0.3, 0.5, 0.7, 0.6, 0.8, 0.9]
        ),
        MarketIndex(
            name: "Bitcoin",
            value: "$89,394.43",
            changePercentage: -2.34,
            chartData: [0.9, 0.8, 0.7, 0.5, 0.4, 0.3, 0.2]
        )
    ]
}
