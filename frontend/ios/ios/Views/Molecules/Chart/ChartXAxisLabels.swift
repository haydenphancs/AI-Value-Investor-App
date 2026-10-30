//
//  ChartXAxisLabels.swift
//  ios
//
//  Adaptive x-axis date labels for the chart. Each label sits AT its bar's x (the same
//  `ChartFrame` mapping the canvas uses), and its format follows the span actually in view
//  — after panning a 3M chart back three years, "Jan 15" would no longer say which January.
//  Every label is in market time, New York's clock and calendar (`ChartDateFormatters`).
//

import SwiftUI

struct ChartXAxisLabels: View {
    let series: ChartSeries
    let frame: ChartFrame
    let selectedRange: ChartTimeRange
    var useIntradayTimeMapping: Bool = false
    /// The session the labels describe. Must match the one the price line above
    /// is drawn against — a 24/7 asset captioned 9:30 AM – 4:00 PM would put the
    /// wrong clock under the right chart.
    var sessionWindow: TradingDayHelper.SessionWindow = .regular

    var body: some View {
        if useIntradayTimeMapping {
            sessionLabels
        } else {
            GeometryReader { geometry in
                let labels = barLabels(width: geometry.size.width)
                ZStack(alignment: .topLeading) {
                    ForEach(labels, id: \.index) { label in
                        Text(label.text)
                            .font(.system(size: 10, weight: .regular))
                            .foregroundColor(AppColors.textMuted)
                            .fixedSize()
                            .alignmentGuide(.leading) { d in
                                // Centre on the bar, clamped inside the axis.
                                -min(max(label.x - d.width / 2, 0), max(geometry.size.width - d.width, 0))
                            }
                    }
                }
                .frame(width: geometry.size.width, alignment: .topLeading)
            }
            .frame(height: 14)
        }
    }

    // MARK: 1D — session times, each at its own place on the axis

    private var sessionLabels: some View {
        let refDate = frame.visibleRange.first.flatMap { ChartDateFormatters.parseDate(series.dates[$0]) }
        let labels = TradingDayHelper.sessionTimeLabels(
            count: selectedRange.xAxisLabelCount,
            referenceDate: refDate,
            // Same window the line above is drawn against, or the axis
            // captions a 24/7 asset's day as 9:30 AM - 4:00 PM.
            window: sessionWindow
        )
        // New York times on every phone, so the axis names its zone once, on the last label
        // ("4:00 PM ET"): bare, a Denver reader took 9:30 AM for their own clock.
        var shown = labels
        if let last = shown.last { shown[shown.count - 1] = last + " ET" }
        return GeometryReader { geometry in
            ZStack(alignment: .topLeading) {
                ForEach(Array(shown.enumerated()), id: \.offset) { index, label in
                    // The times are evenly spaced across the window, as `timeFractions` places
                    // the bars, so each label is centred at its own fraction of the axis (the
                    // first and last clamp inside it). An HStack of Spacers spread them by text
                    // width instead, and the longer "4:00 PM ET" pulled "1:50 PM" about 20
                    // minutes early.
                    let fraction: CGFloat = shown.count > 1 ? CGFloat(index) / CGFloat(shown.count - 1) : 0
                    Text(label)
                        .font(.system(size: 10, weight: .regular))
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize()
                        .alignmentGuide(.leading) { d in
                            -min(max(geometry.size.width * fraction - d.width / 2, 0), max(geometry.size.width - d.width, 0))
                        }
                }
            }
            .frame(width: geometry.size.width, alignment: .topLeading)
        }
        .frame(height: 14)
    }

    // MARK: Bar-positioned labels

    private struct Label {
        let index: Int
        let x: CGFloat
        let text: String
    }

    private func barLabels(width: CGFloat) -> [Label] {
        let range = frame.visibleRange
        guard range.count > 1, range.upperBound <= series.count, width > 0 else { return [] }
        let count = max(2, selectedRange.xAxisLabelCount)
        let format = Self.format(
            first: series.dates[max(range.lowerBound, 0)],
            last: series.dates[range.upperBound - 1]
        )

        var out: [Label] = []
        var lastText: String?
        // Evenly spaced in SCREEN space, inset half a label from each edge; each snaps to
        // the bar under it.
        for k in 0..<count {
            let x = width * (CGFloat(k) + 0.5) / CGFloat(count)
            guard let i = frame.index(atX: x, width: width), i < series.count else { continue }
            guard let date = ChartDateFormatters.parseDate(series.dates[i]) else { continue }
            let text = format.string(from: date)
            if text == lastText || out.contains(where: { $0.index == i }) { continue }
            lastText = text
            out.append(Label(index: i, x: frame.x(i, width: width), text: text))
        }
        // A clock axis names its zone once, on the last label ("Mon 3 PM ET"), as the 1D axis
        // does. A date-only axis has no zone to name.
        if format === ChartDateFormatters.timeOnly || format === ChartDateFormatters.weekdayTime,
           let last = out.last {
            out[out.count - 1] = Label(index: last.index, x: last.x, text: last.text + " ET")
        }
        return out
    }

    /// The label format for the dates actually in view. "This year" and "this week" are judged
    /// in market time, the zone every label prints in (`ChartDateFormatters`).
    private static func format(first: String, last: String) -> DateFormatter {
        guard let a = ChartDateFormatters.parseDate(first),
              let b = ChartDateFormatters.parseDate(last) else { return ChartDateFormatters.dayMonth }
        let days = b.timeIntervalSince(a) / 86_400
        let calendar = ChartDateFormatters.marketCalendar
        let sameYearAsNow = calendar.component(.year, from: a) == calendar.component(.year, from: Date())
            && calendar.component(.year, from: b) == calendar.component(.year, from: Date())
        switch days {
        case ..<1.5:
            return ChartDateFormatters.isIntraday(first) ? ChartDateFormatters.timeOnly : ChartDateFormatters.dayMonth
        case ..<12:
            // A week of intraday bars: "Mon 10 AM" is unambiguous only for the current week.
            if ChartDateFormatters.isIntraday(first), calendar.isDate(b, equalTo: Date(), toGranularity: .weekOfYear) {
                return ChartDateFormatters.weekdayTime
            }
            return ChartDateFormatters.dayMonth
        case ..<300:
            return sameYearAsNow ? ChartDateFormatters.dayMonth : ChartDateFormatters.monthYear
        case ..<(365 * 3.5):
            return ChartDateFormatters.monthYear
        default:
            return ChartDateFormatters.year
        }
    }
}
