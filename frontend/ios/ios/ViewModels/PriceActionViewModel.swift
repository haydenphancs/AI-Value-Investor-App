//
//  PriceActionViewModel.swift
//  ios
//
//  ViewModel: Processes raw price data + optional event into display-ready context.
//  Smart Label Logic determines the most relevant timeframe and tag automatically.
//

import Foundation
import SwiftUI

// MARK: - Output Context

struct PriceActionContext {
    let tag: String               // "Earnings Miss", "Sharp Decline", "Normal"
    let displayPercentage: String // "-12.4%"
    let percentValue: Double      // -12.4
    let timeLabel: String         // "Since Feb 2", "Last 5 Days", "Last 30 Days"
    let chartData: [Double]       // sparkline price array
    let eventIndex: Int?          // dot position in chartData (nil = no event)
    let narrative: String
    let isPositive: Bool

    var trendColor: Color { isPositive ? AppColors.bullish : AppColors.bearish }
}

// MARK: - ViewModel

@MainActor
class PriceActionViewModel: ObservableObject {
    @Published private(set) var context: PriceActionContext

    init(data: PriceActionData) {
        self.context = Self.process(data: data)
    }

    // MARK: - Smart Label Logic

    private static func process(data: PriceActionData) -> PriceActionContext {
        let prices = data.prices
        let current = data.currentPrice

        // 1) Event-driven: calculate change from event date
        if let event = data.event, event.index >= 0, event.index < prices.count {
            let eventPrice = prices[event.index]
            let pct = ((current - eventPrice) / eventPrice) * 100

            // Show a few points before the event for context
            let leadIn = 3
            let startIdx = max(0, event.index - leadIn)
            let chartSlice = Array(prices[startIdx...])
            let adjustedEventIdx = event.index - startIdx

            return PriceActionContext(
                tag: event.tag,
                displayPercentage: formatPercent(pct),
                percentValue: pct,
                timeLabel: "Since \(event.date)",
                chartData: chartSlice,
                eventIndex: adjustedEventIdx,
                narrative: data.narrative,
                isPositive: pct >= 0
            )
        }

        // 2) No event — check for significant momentum windows

        // > 5% move in last 5 days
        if prices.count >= 5 {
            let ref = prices[prices.count - 5]
            let pct = ((current - ref) / ref) * 100
            if abs(pct) > 5 {
                return PriceActionContext(
                    tag: pct > 0 ? "Rally" : "Sharp Decline",
                    displayPercentage: formatPercent(pct),
                    percentValue: pct,
                    timeLabel: "Last 5 Days",
                    chartData: Array(prices.suffix(7)),
                    eventIndex: nil,
                    narrative: data.narrative,
                    isPositive: pct >= 0
                )
            }
        }

        // > 10% move in last 15 days
        if prices.count >= 15 {
            let ref = prices[prices.count - 15]
            let pct = ((current - ref) / ref) * 100
            if abs(pct) > 10 {
                return PriceActionContext(
                    tag: pct > 0 ? "Momentum" : "Correction",
                    displayPercentage: formatPercent(pct),
                    percentValue: pct,
                    timeLabel: "Last 15 Days",
                    chartData: Array(prices.suffix(17)),
                    eventIndex: nil,
                    narrative: data.narrative,
                    isPositive: pct >= 0
                )
            }
        }

        // 3) Default: Last 30 Days → Normal
        let count = min(30, prices.count)
        let ref = prices[prices.count - count]
        let pct = ((current - ref) / ref) * 100

        return PriceActionContext(
            tag: "Normal",
            displayPercentage: formatPercent(pct),
            percentValue: pct,
            timeLabel: "Last 30 Days",
            chartData: Array(prices.suffix(count)),
            eventIndex: nil,
            narrative: data.narrative,
            isPositive: pct >= 0
        )
    }

    private static func formatPercent(_ value: Double) -> String {
        String(format: "%+.1f%%", value)
    }
}
