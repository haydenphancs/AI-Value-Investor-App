//
//  EarningsChartView.swift
//  ios
//
//  Molecule: Interactive chart displaying EPS/Revenue with estimates, actuals, and optional price overlay
//

import SwiftUI
import Charts

// Cached formatter to avoid re-creating on every render
private let _dailyPriceDateFormatter: DateFormatter = {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd"
    f.locale = Locale(identifier: "en_US_POSIX")
    return f
}()

struct EarningsChartView: View {
    let quarters: [EarningsQuarterData]
    let priceHistory: [EarningsPricePoint]
    var dailyPriceHistory: [EarningsDailyPricePoint] = []
    let showPriceLine: Bool
    var dataType: EarningsDataType = .eps
    /// The next report day (`EarningsData.nextEarningsDate`). It ends the price line's time
    /// axis at the first gray estimate dot, so the line stops where today sits between the
    /// last report and the next one. Ignored unless it belongs to that quarter (see
    /// `EarningsPriceTimeline.placement`). The LAST stored property, so every existing
    /// memberwise call keeps compiling.
    var nextEarningsDate: Date? = nil

    // Calculate chart bounds based ONLY on EPS/Revenue data (NOT price).
    // Finite values only — a NaN poisons min()/max() — and no estimate for a quarter
    // that had none (its estimate slot is a copy of the actual, not a consensus).
    private var earningsValues: [Double] {
        var values: [Double] = []
        for quarter in quarters {
            if let actual = quarter.actualValue, actual.isFinite {
                values.append(actual)
            }
            if quarter.hasEstimate, quarter.estimateValue.isFinite {
                values.append(quarter.estimateValue)
            }
        }
        return values
    }

    /// No values → no axis. The fallback domain used to print an invented
    /// "1.10 / 0.50 / -0.10" scale over an empty plot.
    private var hasValues: Bool { !earningsValues.isEmpty }

    // Pad OUTWARD additively from the data span so the domain always CONTAINS every
    // value regardless of sign. The old multiplicative `min*0.9 / max*1.1` moved a
    // NEGATIVE min toward zero (e.g. -2.0*0.9 = -1.8 > -2.0), pushing a loss-maker's
    // EPS dot below the axis and off-screen, and left the labels not bounding the data.
    //
    // ...but never pad ACROSS zero when the data does not cross it. Revenue cannot be
    // negative, yet a series whose smallest value is under a tenth of its span (a
    // dropped-digit 22.2M quarter beside 25B estimates — AVGO's "-2.5B" axis — or a
    // biotech's 0.05B…2.0B) padded the floor below 0 and printed negative revenue.
    private var minValue: Double {
        let lo = earningsValues.min() ?? 0
        let hi = earningsValues.max() ?? 1
        let pad = max((hi - lo) * 0.1, 0.01)
        return lo >= 0 ? max(lo - pad, 0) : lo - pad
    }

    // Mirror of the floor: an all-negative series (a loss-maker's EPS) never pads above 0.
    // `lo < 0` keeps an all-ZERO series from collapsing to a 0...0 domain.
    private var maxValue: Double {
        let lo = earningsValues.min() ?? 0
        let hi = earningsValues.max() ?? 1
        let pad = max((hi - lo) * 0.1, 0.01)
        return (hi <= 0 && lo < 0) ? min(hi + pad, 0) : hi + pad
    }

    /// The three labelled values. Gridlines and labels are BOTH placed through
    /// `normalizedY` at exactly these, so a dot level with a label has that value.
    private var axisValues: [Double] {
        [maxValue, (maxValue + minValue) / 2, minValue]
    }

    // Price bounds for independent normalization (quarterly fallback)
    private var priceValues: [Double] {
        if !dailyPriceHistory.isEmpty {
            return dailyPriceHistory.map { $0.price }
        }
        var values: [Double] = []
        for (index, quarter) in quarters.enumerated() {
            if quarter.actualValue != nil, index < priceHistory.count, priceHistory[index].price > 0 {
                values.append(priceHistory[index].price)
            }
        }
        return values
    }

    private var minPrice: Double {
        priceValues.min() ?? 0
    }

    private var maxPrice: Double {
        priceValues.max() ?? 1
    }

    private var chartHeight: CGFloat { 200 }
    // Shared with EarningsSurpriseBarChart and EarningsSurpriseRow, so the columns of all
    // three line up (EarningsChartLayout explains the drift this replaced).
    private var yAxisWidth: CGFloat { EarningsChartLayout.yAxisWidth(for: dataType) }

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 0) {
                // Y-axis labels (separate from chart area)
                yAxisLabels()
                    .frame(width: yAxisWidth)

                // Chart area
                GeometryReader { geometry in
                    let width = geometry.size.width
                    let height = geometry.size.height
                    let quarterCount = max(quarters.count, 1)
                    let stepX = width / CGFloat(quarterCount)
                    let range = max(maxValue - minValue, 0.01)

                    ZStack {
                        // Horizontal grid lines
                        gridLines(width: width, height: height)

                        // Price line (optional, rendered first so it's behind)
                        if showPriceLine && !priceValues.isEmpty {
                            if !dailyPriceHistory.isEmpty {
                                dailyPriceLine(width: width, height: height, stepX: stepX)
                            } else {
                                priceLine(width: width, height: height, stepX: stepX)
                            }
                        }

                        // Estimate dots (gray). None for a quarter that had no consensus:
                        // its estimate slot is a copy of the actual, and a gray dot there
                        // claimed an estimate existed.
                        ForEach(Array(quarters.enumerated()), id: \.element.id) { index, quarter in
                            if quarter.hasEstimate, quarter.estimateValue.isFinite {
                                let x = CGFloat(index) * stepX + stepX / 2
                                let y = height - normalizedY(quarter.estimateValue, height: height, range: range)

                                Circle()
                                    .fill(AppColors.textSecondary)
                                    .frame(width: 14, height: 14)
                                    .position(x: x, y: y)
                            }
                        }

                        // Actual result dots (colored based on result)
                        ForEach(Array(quarters.enumerated()), id: \.element.id) { index, quarter in
                            if let actual = quarter.actualValue, actual.isFinite {
                                let x = CGFloat(index) * stepX + stepX / 2
                                let y = height - normalizedY(actual, height: height, range: range)

                                // Dot with appropriate styling
                                ZStack {
                                    Circle()
                                        .fill(quarter.result.dotColor)
                                        .frame(width: 14, height: 14)

                                    // Dashed border for matched results
                                    if quarter.result.hasDashedBorder {
                                        Circle()
                                            .stroke(
                                                AppColors.textPrimary,
                                                style: StrokeStyle(lineWidth: 2, dash: [3, 2])
                                            )
                                            .frame(width: 18, height: 18)
                                    }
                                }
                                .position(x: x, y: y)
                            }
                        }
                    }
                }
                .frame(height: chartHeight)
            }

            // X-axis labels (quarters)
            xAxisLabels()
        }
    }

    // MARK: - Helper Views

    // Gridlines at the three LABELLED values, through the same `normalizedY` as the dots.
    // They used to be four Spacer-spread lines at 0, ⅓, ⅔ and 1 of the frame, matching
    // neither the labels nor any value.
    private func gridLines(width: CGFloat, height: CGFloat) -> some View {
        let range = max(maxValue - minValue, 0.01)
        return Path { path in
            guard hasValues else { return }
            for value in axisValues {
                let y = height - normalizedY(value, height: height, range: range)
                path.move(to: CGPoint(x: 0, y: y))
                path.addLine(to: CGPoint(x: width, y: y))
            }
        }
        .stroke(AppColors.cardBackgroundLight.opacity(0.5), lineWidth: 1)
    }

    // Each label is placed at its own value's y via `normalizedY`, exactly like the dots.
    // A Spacer-spread VStack put the top and bottom labels ~8pt from where their values
    // plot (the 7.5% inset), so on AVGO's axis a dot level with the top label sat ~1.5B
    // below the printed number.
    private func yAxisLabels() -> some View {
        GeometryReader { geometry in
            let height = geometry.size.height
            let range = max(maxValue - minValue, 0.01)
            let centerX = geometry.size.width / 2

            if hasValues {
                ZStack {
                    Text(formatYValue(maxValue))
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                        .position(x: centerX, y: height - normalizedY(maxValue, height: height, range: range))

                    Text(formatYValue((maxValue + minValue) / 2))
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                        .position(x: centerX, y: height - normalizedY((maxValue + minValue) / 2, height: height, range: range))

                    Text(formatYValue(minValue))
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                        .position(x: centerX, y: height - normalizedY(minValue, height: height, range: range))
                }
            }
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.sm)
    }

    private func xAxisLabels() -> some View {
        HStack(spacing: 0) {
            // Spacer for y-axis width alignment
            Spacer()
                .frame(width: yAxisWidth)

            // Display quarter labels based on count
            if quarters.count > 6 {
                // For 3Y view (more than 6 quarters), show condensed labels
                // Group by year and show Q1, Q2, Q3, Q4 with year label
                xAxisLabelsCondensed()
            } else {
                // For 1Y view (6 or fewer quarters), show full quarter labels
                ForEach(quarters) { quarter in
                    Text(quarter.quarter)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .frame(maxWidth: .infinity)
                }
            }
        }
        .padding(.top, AppSpacing.sm)
    }

    private func xAxisLabelsCondensed() -> some View {
        VStack(spacing: 2) {
            // Top row: Q1, Q2, Q3, Q4 labels for each quarter
            HStack(spacing: 0) {
                ForEach(quarters) { quarter in
                    Text(String(quarter.quarter.prefix(2)))
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .frame(maxWidth: .infinity)
                }
            }

            // Bottom row: Year labels positioned under their quarter groups
            GeometryReader { geometry in
                let totalWidth = geometry.size.width
                let totalQuarters = quarters.count
                let stepWidth = totalWidth / CGFloat(totalQuarters)

                // Group quarters by year, maintaining their original indices
                let groupedByYear = Dictionary(grouping: Array(quarters.enumerated())) { element in
                    let components = element.element.quarter.components(separatedBy: " ")
                    return components.count > 1 ? components[1] : ""
                }

                // Sort years
                let sortedYears = groupedByYear.keys.sorted()

                ZStack(alignment: .top) {
                    ForEach(sortedYears, id: \.self) { year in
                        if let yearData = groupedByYear[year]?.sorted(by: { $0.offset < $1.offset }),
                           let firstIndex = yearData.first?.offset,
                           let lastIndex = yearData.last?.offset {

                            let centerIndex = CGFloat(firstIndex + lastIndex) / 2.0
                            let centerX = centerIndex * stepWidth + stepWidth / 2

                            Text(year)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                                .bold()
                                .position(x: centerX, y: 6)
                        }
                    }
                }
            }
            .frame(height: 12)
        }
    }

    // MARK: - Continuous Daily Price Line

    /// The daily closes on the dots' time axis (`EarningsPriceTimeline`). Each reported dot
    /// sits on the day the quarter was ANNOUNCED, so a jump just right of a dot is the
    /// market's answer to that report. The dots used to sit on the quarter's period END,
    /// which drew every report-day move 1-11 weeks right of its own dot: two days after
    /// CSCO's 2026-11-12 report the line already ran 20 days past the newest dot.
    private func dailyPriceLine(width: CGFloat, height: CGFloat, stepX: CGFloat) -> some View {
        // Day numbers, never device-calendar Dates: a "yyyy-MM-dd" is one calendar day in
        // every zone, and day arithmetic has no 23- or 25-hour daylight-saving days.
        let closes: [(day: Int, price: Double)] = dailyPriceHistory
            .compactMap { point -> (day: Int, price: Double)? in
                guard let day = EarningsPriceTimeline.dayNumber(point.date), point.price.isFinite else {
                    return nil
                }
                return (day: day, price: point.price)
            }
            .sorted { $0.day < $1.day }

        guard closes.count >= 2 else {
            return AnyView(EmptyView())
        }

        // The card's next-report Date was parsed by a POSIX, device-zone "yyyy-MM-dd"
        // formatter; the same formatter turns it back into exactly that calendar day.
        let nextDay: Int? = nextEarningsDate.flatMap { date -> Int? in
            EarningsPriceTimeline.dayNumber(_dailyPriceDateFormatter.string(from: date))
        }
        guard let timeline = EarningsPriceTimeline.placement(
            quarters: quarters,
            nextEarningsDay: nextDay,
            lastCloseDay: closes.last?.day
        ) else {
            return AnyView(EmptyView())
        }

        // Place every close FIRST, then scale on the VISIBLE window. The backend sends
        // ~5 years of closes while 1Y shows ~15 months, and the old scale (the whole
        // series' min/max) squeezed a stock that fell $60 → $20-25 into the bottom ~11%
        // of the plot: the reaction around each earnings dot — the reason the Price
        // toggle exists — was a flat line.
        let placed: [(x: CGFloat, price: Double)] = closes.map { close -> (x: CGFloat, price: Double) in
            let slot: Double = timeline.slot(forDay: close.day)
            return (x: CGFloat(slot + 0.5) * stepX, price: close.price)
        }
        let visible = Self.visiblePriceWindow(placed, width: width)
        guard visible.drawn.count >= 2 else {
            return AnyView(EmptyView())
        }
        let visMin = visible.low
        let pRange = max(visible.high - visible.low, 0.01)

        return AnyView(
            Path { path in
                var started = false
                for (x, price) in visible.drawn {
                    let normalizedPrice = (price - visMin) / pRange
                    let y = height - (CGFloat(normalizedPrice) * height * 0.85 + height * 0.075)

                    if !started {
                        path.move(to: CGPoint(x: x, y: y))
                        started = true
                    } else {
                        path.addLine(to: CGPoint(x: x, y: y))
                    }
                }
            }
            .stroke(
                AppColors.accentCyan,
                style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round)
            )
            .clipped()
        )
    }

    // MARK: - Quarterly Price Line (fallback)

    private func priceLine(width: CGFloat, height: CGFloat, stepX: CGFloat) -> some View {
        let priceRange = max(maxPrice - minPrice, 0.01)

        return Path { path in
            var isFirstPoint = true

            for (index, quarter) in quarters.enumerated() {
                // Only draw price for quarters with actual data (not pending/future)
                guard quarter.actualValue != nil,
                      index < priceHistory.count,
                      priceHistory[index].price > 0 else {
                    continue
                }

                let pricePoint = priceHistory[index]
                let x = CGFloat(index) * stepX + stepX / 2

                // Normalize price independently to fit within the chart area
                let normalizedPrice = (pricePoint.price - minPrice) / priceRange
                let y = height - (CGFloat(normalizedPrice) * height * 0.85 + height * 0.075)

                if isFirstPoint {
                    path.move(to: CGPoint(x: x, y: y))
                    isFirstPoint = false
                } else {
                    path.addLine(to: CGPoint(x: x, y: y))
                }
            }
        }
        .stroke(
            AppColors.accentCyan,
            style: StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round)
        )
    }

    /// The closes to draw and the band to scale them on. `drawn` is every close inside the
    /// plot plus the nearest one on each side, so the line still reaches both edges (the
    /// path is clipped). The band comes from the IN-WINDOW closes only, falling back to the
    /// whole series when fewer than two are inside. `placed` is in date order and x grows
    /// with date (`EarningsPriceTimeline` keeps only strictly rising anchors and a positive
    /// rate outside them), so the in-window run is contiguous.
    static func visiblePriceWindow(
        _ placed: [(x: CGFloat, price: Double)],
        width: CGFloat
    ) -> (drawn: [(x: CGFloat, price: Double)], low: Double, high: Double) {
        let inside = placed.indices.filter { placed[$0].x >= 0 && placed[$0].x <= width }
        guard let first = inside.first, let last = inside.last else {
            return (drawn: [], low: 0, high: 1)
        }
        let lowerIndex = max(first - 1, 0)
        let upperIndex = min(last + 1, placed.count - 1)
        let drawn = Array(placed[lowerIndex...upperIndex])
        let band: [Double] = inside.count >= 2 ? inside.map { placed[$0].price } : placed.map { $0.price }
        return (drawn: drawn, low: band.min() ?? 0, high: band.max() ?? 1)
    }

    // MARK: - Helper Functions

    private func normalizedY(_ value: Double, height: CGFloat, range: Double) -> CGFloat {
        let normalized = (value - minValue) / range
        return CGFloat(normalized) * height * 0.85 + height * 0.075
    }

    private func formatYValue(_ value: Double) -> String {
        if dataType == .revenue {
            return Self.dropNegativeZero(formatLargeNumber(value))
        }
        // Precision by MAGNITUDE, sign kept. `value >= 100` / `>= 10` never fire for a
        // negative, so a loss-maker's -11.68 printed all six characters into the 32pt
        // gutter (and wrapped) while +11.68 printed "11.7"; -150 printed "-150.00".
        let magnitude = abs(value)
        let text: String
        if magnitude >= 100 {
            text = String(format: "%.0f", value)
        } else if magnitude >= 10 {
            text = String(format: "%.1f", value)
        } else {
            text = String(format: "%.2f", value)
        }
        return Self.dropNegativeZero(text)
    }

    /// "-0.00" / "-0" → "0.00" / "0": a midpoint a hair below zero is zero on this axis.
    static func dropNegativeZero(_ text: String) -> String {
        guard text.hasPrefix("-"), let parsed = Double(text.dropFirst()), parsed == 0 else {
            return text
        }
        return String(text.dropFirst())
    }

    private func formatLargeNumber(_ value: Double) -> String {
        let absValue = abs(value)
        let sign = value < 0 ? "-" : ""
        if absValue >= 1_000_000_000_000 {
            return "\(sign)\(String(format: "%.1f", absValue / 1_000_000_000_000))T"
        } else if absValue >= 1_000_000_000 {
            return "\(sign)\(String(format: "%.1f", absValue / 1_000_000_000))B"
        } else if absValue >= 1_000_000 {
            return "\(sign)\(String(format: "%.1f", absValue / 1_000_000))M"
        } else if absValue >= 1_000 {
            return "\(sign)\(String(format: "%.1f", absValue / 1_000))K"
        } else {
            return String(format: "%.0f", value)
        }
    }
}

// MARK: - Price line time axis

/// Where the Earnings card's price line puts a calendar day. The dots are evenly spaced
/// slots (they must stay lined up with the surprise row and bars), so the line's time axis
/// is pinned to them — dot i (centre = slot i) on its quarter's day — and runs
/// piecewise-linearly between consecutive pinned dots.
///
/// Report mode (the normal case): each reported dot is pinned to the day it was ANNOUNCED
/// (`reportDate`), and the first gray estimate dot to the next report day, so the line ends
/// where today sits between the last report and the next. Period-end mode (`fiscalDate`):
/// only when fewer than two reported dots carry a usable report day — a build whose earnings
/// feed failed, or an older backend — and never with the next report day, which would mix
/// announcement days into a period-end axis and could run backwards.
///
/// Pure: `test_ios_earnings_price_timeline.py` ports it line for line, and the source-scan
/// guards pin the constants and branches the port relies on.
enum EarningsPriceTimeline {
    /// A dot pinned to a calendar day (`day` = days since 1970-01-01).
    struct Anchor: Equatable {
        let slot: Int
        let day: Int
    }

    enum Mode: Equatable {
        case reportDay
        case periodEnd
    }

    struct Placement {
        /// At least two; `slot` and `day` both strictly rising.
        let anchors: [Anchor]
        /// The median days per slot between the pinned dots (> 0): the rate before the
        /// first anchor and after the last one.
        let daysPerSlot: Double
        let mode: Mode

        /// The fractional slot of a day (dot i's centre is `Double(i)`).
        func slot(forDay day: Int) -> Double {
            guard let first = anchors.first, let last = anchors.last, daysPerSlot > 0 else {
                return 0
            }
            if day <= first.day {
                return Double(first.slot) - Double(first.day - day) / daysPerSlot
            }
            if day >= last.day {
                return Double(last.slot) + Double(day - last.day) / daysPerSlot
            }
            var lower: Int = 0
            var upper: Int = anchors.count - 1
            while upper - lower > 1 {
                let middle: Int = (lower + upper) / 2
                if anchors[middle].day <= day {
                    lower = middle
                } else {
                    upper = middle
                }
            }
            let from: Anchor = anchors[lower]
            let to: Anchor = anchors[upper]
            let fraction: Double = Double(day - from.day) / Double(to.day - from.day)
            return Double(from.slot) + fraction * Double(to.slot - from.slot)
        }
    }

    /// A report day this many days after its own period end may pin its dot: the client's
    /// own bound on a malformed payload. The backend sends 7-110 days, and up to
    /// `_A2_REPORT_MAX_LAG_DAYS` (200) for a late Q4 its pairing gave the earlier quarter.
    static let reportLagDays: ClosedRange<Int> = 1...200
    /// The next report day pins the first gray dot only this many days after that
    /// quarter's period end — the backend's `_MIN_ANNOUNCE_LAG_DAYS`..`_LATE_RELEASE_MAX_DAYS`.
    /// A next date outside it belongs to some other quarter (a rescheduled leftover, or the
    /// quarter after an overdue one) and would stretch the line over the wrong slot.
    static let nextLagDays: ClosedRange<Int> = 7...110
    /// ...and only within this many days of the pinned dots' own median lag (report day −
    /// period end). The report one quarter LATER is ~91 days further out, so half a quarter
    /// tells them apart. Without it a fast reporter's next date for the FOLLOWING quarter
    /// (CSCO: 109 days, inside 7-110) was accepted the morning after an after-close report,
    /// while FMP had not filled the actual yet, and the report-day move drew half a slot
    /// left of its gray dot for the 24 h that build is cached.
    static let nextLagToleranceDays: Int = 45

    /// Days since 1970-01-01 of a strict "yyyy-MM-dd" (a longer string's first 10
    /// characters), or nil. Proleptic Gregorian arithmetic, no `Calendar`: neither the
    /// device calendar (Buddhist, Japanese) nor the time zone can move a day.
    static func dayNumber(_ text: String?) -> Int? {
        guard let text = text else { return nil }
        let parts: [Substring] = text.prefix(10).split(separator: "-", omittingEmptySubsequences: false)
        guard parts.count == 3, parts[0].count == 4, parts[1].count == 2, parts[2].count == 2 else {
            return nil
        }
        for part in parts where !part.allSatisfy({ $0 >= "0" && $0 <= "9" }) {
            return nil
        }
        guard let year = Int(parts[0]), let month = Int(parts[1]), let day = Int(parts[2]),
              (1...12).contains(month), day >= 1 else {
            return nil
        }
        let isLeap: Bool = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
        let monthLengths: [Int] = [31, isLeap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        guard day <= monthLengths[month - 1] else { return nil }
        // days_from_civil (H. Hinnant); year >= 0 here.
        let shiftedYear: Int = month <= 2 ? year - 1 : year
        let era: Int = shiftedYear / 400
        let yearOfEra: Int = shiftedYear - era * 400
        let shiftedMonth: Int = (month + 9) % 12
        let dayOfYear: Int = (153 * shiftedMonth + 2) / 5 + day - 1
        let dayOfEra: Int = yearOfEra * 365 + yearOfEra / 4 - yearOfEra / 100 + dayOfYear
        return era * 146_097 + dayOfEra - 719_468
    }

    /// The candidates (in rising slot order) whose day is strictly later than EVERY earlier
    /// candidate's and strictly earlier than EVERY later one's. An inverted or shared pair
    /// loses both — nothing says which day is wrong — and never greedily: one early outlier
    /// must not unpin every dot after it.
    static func orderedAnchors(_ candidates: [Anchor]) -> [Anchor] {
        let count: Int = candidates.count
        guard count >= 2 else { return candidates }
        var latestBefore: [Int?] = Array(repeating: nil, count: count)
        var earliestAfter: [Int?] = Array(repeating: nil, count: count)
        var running: Int? = nil
        for index in 0..<count {
            latestBefore[index] = running
            running = max(running ?? candidates[index].day, candidates[index].day)
        }
        running = nil
        for index in stride(from: count - 1, through: 0, by: -1) {
            earliestAfter[index] = running
            running = min(running ?? candidates[index].day, candidates[index].day)
        }
        var kept: [Anchor] = []
        for index in 0..<count {
            let day: Int = candidates[index].day
            if let before = latestBefore[index], day <= before { continue }
            if let after = earliestAfter[index], day >= after { continue }
            kept.append(candidates[index])
        }
        return kept
    }

    /// The median of `values`, or nil when there are none.
    static func median(_ values: [Double]) -> Double? {
        guard !values.isEmpty else { return nil }
        let sorted: [Double] = values.sorted()
        let middle: Int = sorted.count / 2
        if sorted.count % 2 == 1 {
            return sorted[middle]
        }
        return (sorted[middle - 1] + sorted[middle]) / 2
    }

    /// The median days per slot over consecutive anchors, or nil if any segment is not
    /// strictly rising in both slot and day.
    static func medianDaysPerSlot(_ anchors: [Anchor]) -> Double? {
        guard anchors.count >= 2 else { return nil }
        var rates: [Double] = []
        for index in 1..<anchors.count {
            let slots: Int = anchors[index].slot - anchors[index - 1].slot
            let days: Int = anchors[index].day - anchors[index - 1].day
            guard slots > 0, days > 0 else { return nil }
            rates.append(Double(days) / Double(slots))
        }
        return median(rates)
    }

    /// The time axis for `quarters` (the displayed dots, historical first), or nil when
    /// fewer than two dots can be pinned (no line is drawn — never a guessed one).
    static func placement(
        quarters: [EarningsQuarterData],
        nextEarningsDay: Int?,
        lastCloseDay: Int?
    ) -> Placement? {
        let historicalSlots: [Int] = quarters.indices.filter { quarters[$0].actualValue != nil }

        // Report mode: every reported dot whose report day is a plausible lag after its own
        // period end; the rest are interpolated between their neighbours.
        var reportCandidates: [Anchor] = []
        for slot in historicalSlots {
            guard let reportDay = dayNumber(quarters[slot].reportDate),
                  let periodEndDay = dayNumber(quarters[slot].fiscalDate),
                  reportLagDays.contains(reportDay - periodEndDay) else { continue }
            reportCandidates.append(Anchor(slot: slot, day: reportDay))
        }
        let reportAnchors: [Anchor] = orderedAnchors(reportCandidates)
        if reportAnchors.count >= 2, let rate = medianDaysPerSlot(reportAnchors), let last = reportAnchors.last {
            var anchors: [Anchor] = reportAnchors
            // This company's usual report lag, from the dots just pinned.
            let ownLags: [Double] = reportAnchors.compactMap { anchor -> Double? in
                guard let periodEndDay = dayNumber(quarters[anchor.slot].fiscalDate) else { return nil }
                return Double(anchor.day - periodEndDay)
            }
            // The first gray dot on the next report day: only when that day is still ahead
            // of the line and plausibly THIS quarter's release — inside the pairing window
            // AND near the company's usual lag (a later quarter's is ~91 days further out).
            if let nextDay = nextEarningsDay,
               let typicalLag = median(ownLags),
               let futureSlot = quarters.indices.first(where: { $0 > last.slot && quarters[$0].actualValue == nil }),
               let futurePeriodEnd = dayNumber(quarters[futureSlot].fiscalDate),
               nextDay > last.day,
               nextDay >= (lastCloseDay ?? nextDay),
               nextLagDays.contains(nextDay - futurePeriodEnd),
               abs(Double(nextDay - futurePeriodEnd) - typicalLag) <= Double(nextLagToleranceDays) {
                anchors.append(Anchor(slot: futureSlot, day: nextDay))
            }
            return Placement(anchors: anchors, daysPerSlot: rate, mode: .reportDay)
        }

        // Period-end mode: no next-report anchor, ever.
        var periodCandidates: [Anchor] = []
        for slot in historicalSlots {
            guard let periodEndDay = dayNumber(quarters[slot].fiscalDate) else { continue }
            periodCandidates.append(Anchor(slot: slot, day: periodEndDay))
        }
        let periodAnchors: [Anchor] = orderedAnchors(periodCandidates)
        guard periodAnchors.count >= 2, let rate = medianDaysPerSlot(periodAnchors) else {
            return nil
        }
        return Placement(anchors: periodAnchors, daysPerSlot: rate, mode: .periodEnd)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            EarningsChartView(
                quarters: EarningsData.sampleData.epsQuarters,
                priceHistory: EarningsData.sampleData.priceHistory,
                dailyPriceHistory: EarningsData.sampleData.dailyPriceHistory,
                showPriceLine: true,
                // The sample's next date predates its last report: exercises "ignored".
                nextEarningsDate: EarningsData.sampleData.nextEarningsDate?.date
            )
            .padding()
        }
    }
}

#Preview("Revenue - floor at 0, no-consensus quarter") {
    // All-positive revenue with one near-zero quarter: the axis must bottom out at "0",
    // never a negative revenue ("-2.5B"). The blue dot is a quarter reported with no
    // analyst consensus — no gray estimate dot under it.
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        EarningsChartView(
            quarters: { () -> [EarningsQuarterData] in
                var uncovered = EarningsQuarterData(quarter: "Q4 '25", actualValue: 18.0e9, estimateValue: 18.0e9, surprisePercent: nil)
                uncovered.hasEstimate = false
                return [
                    EarningsQuarterData(quarter: "Q2 '25", actualValue: 15.0e9, estimateValue: 14.8e9, surprisePercent: 1.4),
                    EarningsQuarterData(quarter: "Q3 '25", actualValue: 15.9e9, estimateValue: 15.8e9, surprisePercent: 0.8),
                    uncovered,
                    EarningsQuarterData(quarter: "Q1 '26", actualValue: 0.0222e9, estimateValue: 19.1e9, surprisePercent: -99.9),
                    EarningsQuarterData(quarter: "Q2 '26", actualValue: nil, estimateValue: 24.1e9, surprisePercent: nil),
                    EarningsQuarterData(quarter: "Q3 '26", actualValue: nil, estimateValue: 25.0e9, surprisePercent: nil),
                ]
            }(),
            priceHistory: [],
            showPriceLine: false,
            dataType: .revenue
        )
        .padding()
    }
}
