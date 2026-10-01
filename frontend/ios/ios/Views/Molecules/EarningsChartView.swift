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

    // MARK: - Quarter Label → Approximate Date

    /// Converts "Q1 '24" → approximate fiscal quarter end date.
    /// Q1→Mar 31, Q2→Jun 30, Q3→Sep 30, Q4→Dec 31.
    private func estimatedDate(from quarterLabel: String) -> Date? {
        // Expected format: "Q1 '24"
        let parts = quarterLabel.components(separatedBy: " '")
        guard parts.count == 2,
              let qNum = Int(String(parts[0].dropFirst())),   // "Q1" → 1
              let yr = Int(parts[1]) else { return nil }      // "24" → 24

        let year = yr < 50 ? 2000 + yr : 1900 + yr
        let quarterEndMonths = [3, 6, 9, 12]   // Q1→Mar, Q2→Jun, Q3→Sep, Q4→Dec
        guard qNum >= 1, qNum <= 4 else { return nil }
        let month = quarterEndMonths[qNum - 1]
        let day = (month == 6 || month == 9) ? 30 : 31

        var components = DateComponents()
        components.year = year
        components.month = month
        components.day = day
        return Calendar.current.date(from: components)
    }

    // MARK: - Continuous Daily Price Line

    /// Resolves the actual fiscal date for a quarter, using the backend-provided
    /// fiscal_date when available, falling back to label-based estimation.
    private func actualFiscalDate(for quarter: EarningsQuarterData) -> Date? {
        if let fd = quarter.fiscalDate {
            return _dailyPriceDateFormatter.date(from: fd)
        }
        return estimatedDate(from: quarter.quarter)
    }

    private func dailyPriceLine(width: CGFloat, height: CGFloat, stepX: CGFloat) -> some View {
        // Find historical quarter indices and their fiscal dates
        let historicalWithDates: [(index: Int, date: Date)] = quarters.enumerated()
            .filter { $0.element.actualValue != nil }
            .compactMap { (offset, element) in
                guard let d = actualFiscalDate(for: element) else { return nil }
                return (index: offset, date: d)
            }

        guard historicalWithDates.count >= 2 else {
            return AnyView(EmptyView())
        }

        // Parse daily prices
        let datesAndPrices: [(Date, Double)] = dailyPriceHistory.compactMap { dp in
            guard let d = _dailyPriceDateFormatter.date(from: dp.date) else { return nil }
            return (d, dp.price)
        }.sorted { $0.0 < $1.0 }

        guard datesAndPrices.count >= 2 else {
            return AnyView(EmptyView())
        }

        // Find the best two anchor quarters that have daily price data coverage
        // Use the earliest and latest historical quarters whose fiscal dates
        // fall within (or close to) the daily price data range
        let priceStart = datesAndPrices.first!.0
        _ = datesAndPrices.last!.0

        // Find first anchor: earliest historical quarter with fiscal date >= priceStart (or closest)
        let firstAnchor = historicalWithDates.first { $0.date >= priceStart } ?? historicalWithDates.first!
        // Last anchor: always use the last historical quarter
        let lastAnchor = historicalWithDates.last!

        guard lastAnchor.date > firstAnchor.date else {
            return AnyView(EmptyView())
        }

        let xFirst = CGFloat(firstAnchor.index) * stepX + stepX / 2
        let xLast = CGFloat(lastAnchor.index) * stepX + stepX / 2
        let anchorInterval = lastAnchor.date.timeIntervalSince(firstAnchor.date)
        let rate = (xLast - xFirst) / CGFloat(anchorInterval)

        // Place every close FIRST, then scale on the VISIBLE window. The backend sends
        // ~5 years of closes while 1Y shows ~15 months, and the old scale (the whole
        // series' min/max) squeezed a stock that fell $60 → $20-25 into the bottom ~11%
        // of the plot: the reaction around each earnings dot — the reason the Price
        // toggle exists — was a flat line.
        let placed: [(x: CGFloat, price: Double)] = datesAndPrices.compactMap { pair in
            guard pair.1.isFinite else { return nil }
            return (x: xFirst + rate * CGFloat(pair.0.timeIntervalSince(firstAnchor.date)), price: pair.1)
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
    /// with date (the anchors guarantee a positive rate), so the in-window run is contiguous.
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

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            EarningsChartView(
                quarters: EarningsData.sampleData.epsQuarters,
                priceHistory: EarningsData.sampleData.priceHistory,
                dailyPriceHistory: EarningsData.sampleData.dailyPriceHistory,
                showPriceLine: true
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
