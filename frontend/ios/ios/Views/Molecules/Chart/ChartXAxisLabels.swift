//
//  ChartXAxisLabels.swift
//  ios
//
//  Adaptive x-axis date labels for the chart, changes format per timeframe
//

import SwiftUI

struct ChartXAxisLabels: View {
    let pricePoints: [StockPricePoint]
    let selectedRange: ChartTimeRange

    var body: some View {
        let labels = computeLabels()

        HStack {
            ForEach(Array(labels.enumerated()), id: \.offset) { index, label in
                Text(label)
                    .font(.system(size: 10, weight: .regular))
                    .foregroundColor(AppColors.textMuted)
                if index < labels.count - 1 {
                    Spacer()
                }
            }
        }
    }

    private func computeLabels() -> [String] {
        let count = pricePoints.count
        guard count > 1 else {
            if let first = pricePoints.first {
                return [selectedRange.formatDateForXAxis(first.date)]
            }
            return []
        }

        // Clamp label count to available data points
        let desiredCount = selectedRange.xAxisLabelCount
        let labelCount = min(desiredCount, count)
        guard labelCount > 1 else {
            return [selectedRange.formatDateForXAxis(pricePoints[0].date)]
        }

        // Pick evenly-spaced indices
        var labels: [String] = []
        var seen = Set<String>()

        for i in 0..<labelCount {
            let idx = i * (count - 1) / (labelCount - 1)
            let label = selectedRange.formatDateForXAxis(pricePoints[idx].date)
            if seen.contains(label) {
                continue
            }
            seen.insert(label)
            labels.append(label)
        }

        // If ALL range deduped too aggressively (e.g., same year), try with month-year
        if labels.count < 2 && selectedRange == .all && count > 2 {
            labels.removeAll()
            seen.removeAll()
            let altLabelCount = min(4, count)
            for i in 0..<altLabelCount {
                let idx = i * (count - 1) / (altLabelCount - 1)
                let dateStr = pricePoints[idx].date
                guard let date = ChartDateFormatters.parseDate(dateStr) else { continue }
                let label = ChartDateFormatters.monthYear.string(from: date)
                if seen.contains(label) { continue }
                seen.insert(label)
                labels.append(label)
            }
        }

        return labels
    }
}
