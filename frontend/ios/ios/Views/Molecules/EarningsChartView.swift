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

    // Calculate chart bounds based ONLY on EPS/Revenue data (NOT price)
    private var earningsValues: [Double] {
        var values: [Double] = []
        for quarter in quarters {
            if let actual = quarter.actualValue {
                values.append(actual)
            }
            values.append(quarter.estimateValue)
        }
        return values
    }

    private var minValue: Double {
        (earningsValues.min() ?? 0) * 0.9
    }

    private var maxValue: Double {
        (earningsValues.max() ?? 1) * 1.1
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
    private var yAxisWidth: CGFloat { dataType == .revenue ? 50 : 40 }

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
                    let quarterCount = quarters.count
                    let stepX = width / CGFloat(quarterCount)
                    let range = max(maxValue - minValue, 0.01)

                    ZStack {
                        // Horizontal grid lines
                        gridLines(height: height)

                        // Price line (optional, rendered first so it's behind)
                        if showPriceLine && !priceValues.isEmpty {
                            if !dailyPriceHistory.isEmpty {
                                dailyPriceLine(width: width, height: height, stepX: stepX)
                            } else {
                                priceLine(width: width, height: height, stepX: stepX)
                            }
                        }

                        // Estimate dots (gray)
                        ForEach(Array(quarters.enumerated()), id: \.element.id) { index, quarter in
                            let x = CGFloat(index) * stepX + stepX / 2
                            let y = height - normalizedY(quarter.estimateValue, height: height, range: range)

                            Circle()
                                .fill(AppColors.textSecondary)
                                .frame(width: 14, height: 14)
                                .position(x: x, y: y)
                        }

                        // Actual result dots (colored based on result)
                        ForEach(Array(quarters.enumerated()), id: \.element.id) { index, quarter in
                            if let actual = quarter.actualValue {
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

    private func gridLines(height: CGFloat) -> some View {
        VStack(spacing: 0) {
            ForEach(0..<4) { index in
                Rectangle()
                    .fill(AppColors.cardBackgroundLight.opacity(0.5))
                    .frame(height: 1)
                if index < 3 {
                    Spacer()
                }
            }
        }
    }

    private func yAxisLabels() -> some View {
        VStack {
            Text(formatYValue(maxValue))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
            Spacer()
            Text(formatYValue((maxValue + minValue) / 2))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
            Spacer()
            Text(formatYValue(minValue))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
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

        let pRange = max(maxPrice - minPrice, 0.01)

        return AnyView(
            Path { path in
                var started = false
                for (date, price) in datesAndPrices {
                    let x = xFirst + rate * CGFloat(date.timeIntervalSince(firstAnchor.date))

                    // Skip points that fall before the chart area
                    guard x >= 0 else { continue }

                    let normalizedPrice = (price - minPrice) / pRange
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

    // MARK: - Helper Functions

    private func normalizedY(_ value: Double, height: CGFloat, range: Double) -> CGFloat {
        let normalized = (value - minValue) / range
        return CGFloat(normalized) * height * 0.85 + height * 0.075
    }

    private func formatYValue(_ value: Double) -> String {
        if dataType == .revenue {
            return formatLargeNumber(value)
        }
        if value >= 100 {
            return String(format: "%.0f", value)
        } else if value >= 10 {
            return String(format: "%.1f", value)
        } else {
            return String(format: "%.2f", value)
        }
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
