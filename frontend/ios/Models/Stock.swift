//
//  Stock.swift
//  ios
//
//  Created by Hai Phan on 12/23/25.
//

import Foundation

struct Stock: Identifiable {
    let id = UUID()
    let ticker: String
    let companyName: String
    let currentPrice: Double
    let changePercentage: Double
    let openPrice: Double
    let volume: String
    let chartData: [Double]

    var isPositive: Bool {
        changePercentage >= 0
    }

    var formattedPrice: String {
        return "$\(String(format: "%.2f", currentPrice))"
    }

    var formattedChange: String {
        let sign = isPositive ? "+" : ""
        return "\(sign)\(String(format: "%.2f", changePercentage))%"
    }

    var formattedOpen: String {
        return "Open:$\(String(format: "%.2f", openPrice))"
    }

    var formattedVolume: String {
        return "Vol:\(volume)"
    }
}

// MARK: - Mock Data
extension Stock {
    static let mockPortfolio: [Stock] = [
        Stock(
            ticker: "AAPL",
            companyName: "Apple Inc.",
            currentPrice: 185.92,
            changePercentage: 2.45,
            openPrice: 183.20,
            volume: "58.2M",
            chartData: [0.2, 0.3, 0.4, 0.5, 0.7, 0.8, 0.9]
        ),
        Stock(
            ticker: "NVDA",
            companyName: "NVIDIA Corp.",
            currentPrice: 446.92,
            changePercentage: 2.45,
            openPrice: 444.20,
            volume: "58.2M",
            chartData: [0.3, 0.2, 0.4, 0.6, 0.7, 0.8, 0.95]
        ),
        Stock(
            ticker: "META",
            companyName: "Meta Platform",
            currentPrice: 331.92,
            changePercentage: -2.45,
            openPrice: 333.20,
            volume: "58.2M",
            chartData: [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]
        )
    ]
}
