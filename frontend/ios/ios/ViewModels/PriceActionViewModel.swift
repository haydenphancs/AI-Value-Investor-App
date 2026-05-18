//
//  PriceActionViewModel.swift
//  ios
//
//  ViewModel: Renders backend-provided direction/magnitude/window/tag.
//  All adaptive logic now lives server-side in _build_price_action so the
//  AI narrative and the chart numbers stay in sync.
//

import Foundation
import SwiftUI
import Combine

// MARK: - Output Context

struct PriceActionContext {
    let tag: String               // "Earnings Miss", "Momentum", "Normal"
    let displayPercentage: String // "+12.4%"
    let percentValue: Double      // +12.4
    let timeLabel: String         // "Last 30 Days" or "Since Feb 2"
    let chartData: [Double]       // sparkline price array
    let eventIndex: Int?          // dot position in chartData (nil = no event)
    let narrative: String
    let isPositive: Bool

    var trendColor: Color { isPositive ? AppColors.bullish : AppColors.bearish }
}

// MARK: - ViewModel

class PriceActionViewModel: ObservableObject {
    @Published private(set) var context: PriceActionContext

    init(data: PriceActionData) {
        self.context = Self.process(data: data)
    }

    private static func process(data: PriceActionData) -> PriceActionContext {
        let prices = data.prices

        guard !prices.isEmpty else {
            return PriceActionContext(
                tag: data.tag,
                displayPercentage: "0.0%",
                percentValue: 0,
                timeLabel: "No data",
                chartData: [],
                eventIndex: nil,
                narrative: data.narrative,
                isPositive: true
            )
        }

        // Event-driven slice: show a few points before the event for visual context.
        if let event = data.event, event.index >= 0, event.index < prices.count {
            let leadIn = 3
            let startIdx = max(0, event.index - leadIn)
            let chartSlice = Array(prices[startIdx...])
            let adjustedEventIdx = event.index - startIdx

            return PriceActionContext(
                tag: data.tag,
                displayPercentage: formatPercent(data.changePct),
                percentValue: data.changePct,
                timeLabel: data.windowLabel,
                chartData: chartSlice,
                eventIndex: adjustedEventIdx,
                narrative: data.narrative,
                isPositive: data.direction == "up" || data.direction == "flat"
            )
        }

        // No event — render the full backend-provided window.
        return PriceActionContext(
            tag: data.tag,
            displayPercentage: formatPercent(data.changePct),
            percentValue: data.changePct,
            timeLabel: data.windowLabel,
            chartData: prices,
            eventIndex: nil,
            narrative: data.narrative,
            isPositive: data.direction == "up" || data.direction == "flat"
        )
    }

    private static func formatPercent(_ value: Double) -> String {
        String(format: "%+.1f%%", value)
    }
}
