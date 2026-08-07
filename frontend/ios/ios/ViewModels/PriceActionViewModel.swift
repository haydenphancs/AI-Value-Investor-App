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
    let tag: String               // "Earnings Miss", "Notable", "Unusual", etc.
    let displayPercentage: String // "+12.4%"
    let percentValue: Double      // +12.4
    let timeLabel: String         // "Last 30 Days" or "Since Feb 2"
    let chartData: [Double]       // sparkline price array
    let eventIndex: Int?          // dot position in chartData (nil = no event)
    let narrative: String
    let isPositive: Bool

    // Volatility context — drives the institutional-rigor sub-label.
    let tier: String?            // "Typical" | "Notable" | "Unusual" | "Extreme"
    let volatilitySubLabel: String?  // "Normal range: ±10.2% (1.52% daily σ)"

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
        let subLabel = volatilitySubLabel(
            band: data.expectedBandPct, sigma: data.sigmaDailyPct,
        )

        guard !prices.isEmpty else {
            return PriceActionContext(
                tag: data.tag,
                displayPercentage: "0.0%",
                percentValue: 0,
                timeLabel: "No data",
                chartData: [],
                eventIndex: nil,
                narrative: data.narrative,
                isPositive: true,
                tier: data.tier,
                volatilitySubLabel: subLabel
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
                isPositive: data.changePct >= 0,   // from the VALUE, not `direction` — see note above
                tier: data.tier,
                volatilitySubLabel: subLabel
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
            isPositive: data.changePct >= 0,   // from the VALUE, not `direction` — see note above
            tier: data.tier,
            volatilitySubLabel: subLabel
        )
    }

    // `isPositive` is derived from `changePct`, NOT from the backend's `direction` string.
    //
    // They are independent fields, and `direction` is tri-state: "flat" was mapped to
    // positive, so a "flat" classification carrying changePct -0.34 rendered the text
    // "-0.3%" in GAIN GREEN. Colour and sign are now two readings of one number and cannot
    // contradict each other — which matters more here than usual, because the percentage was
    // added to this badge specifically so colour would not be the only signal (WCAG 1.4.1).
    // An unrecognised direction token no longer mis-colours anything either.
    private static func formatPercent(_ value: Double) -> String {
        String(format: "%+.1f%%", value)
    }

    /// User-facing context line under the window label. Returns nil when
    /// the backend didn't compute a baseline σ (e.g. ticker with < 30 days
    /// of history). Format: "Normal range: ±10.2% (1.5% daily σ)".
    private static func volatilitySubLabel(band: Double?, sigma: Double?) -> String? {
        guard let band = band, let sigma = sigma, band > 0, sigma > 0 else {
            return nil
        }
        return String(format: "Normal range: \u{00B1}%.1f%% (%.1f%% daily \u{03C3})", band, sigma)
    }
}
