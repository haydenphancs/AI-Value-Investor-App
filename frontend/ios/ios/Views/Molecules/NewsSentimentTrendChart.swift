//
//  NewsSentimentTrendChart.swift
//  ios
//
//  Molecule: the Updates tab's news-tone chart — how many headlines Cay AI scored
//  positive / negative / neutral for this feed on each day, over 7 / 30 / 90 days.
//
//  Diverging bars on one zero line: positive rises above it, negative falls below it, and
//  a day with neutral headlines carries a thin grey mark on the line itself (neutral has no
//  direction). A missing day is drawn as nothing — the backend sends only days that had a
//  scored headline, and "nothing scored" must not look like "no news".
//
//  Honest labelling: these are the headlines Cay AI SCORED for this feed (the feed's recent
//  window, refreshed through the trading day), not every article published. The card counts
//  "headlines", never "all news"; the legend row says how far back the scored headlines on
//  file go ("since Jul 1" from the backend's `tracking_since`, "120+ days" once that reaches
//  the log's retention edge); and the chart never extrapolates a day it was not given.
//
//  No "Ask" button and no "Headlines Cay AI scored" footer (owner, TestFlight 1.0 (11)): a
//  question about tone is asked from the Insights card above, whose chat is grounded on every
//  window of this card (backend `summarize_tone`).
//

import SwiftUI
import Charts

struct NewsSentimentTrendChart: View {
    let trend: SentimentTrend
    /// The toggle's selection. The ViewModel cuts every window from one 90-day answer, so this
    /// normally equals `trend.window` at once; should it ever differ, the bars on screen are
    /// another window's and stay dimmed under a spinner rather than pass for this one.
    @Binding var window: SentimentTrendWindow
    /// The app stopped re-checking a history that is still being built (the backend can queue
    /// a ticker for hours). The placeholder then stops spinning and stops promising minutes.
    var buildingStalled: Bool = false

    @State private var selectedKey: String?

    private static let chartHeight: CGFloat = 132

    private var isUpdating: Bool { trend.window != window }

    private var selectedDay: SentimentTrendDay? {
        guard let key = selectedKey else { return nil }
        return trend.days.first { $0.dayKey == key }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            header
            summaryLine
            if showsBuildingPlaceholder {
                buildingPlaceholder
            } else {
                chart
                legend
            }
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        // A new window or scope invalidates the tapped day.
        .onChange(of: trend) { _, _ in selectedKey = nil }
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "chart.bar.xaxis")
                .font(AppTypography.iconSmall)
                .foregroundColor(AppColors.textSecondary)
            Text("News Tone")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
            if isUpdating {
                ProgressView()
                    .controlSize(.small)
                    .tint(AppColors.textMuted)
            }
            Spacer(minLength: AppSpacing.sm)
            AnalysisTimeframeToggle(
                selectedOption: $window,
                options: SentimentTrendWindow.allCases.map { $0 }
            )
        }
    }

    // MARK: - Summary / tapped-day line

    private var summaryLine: some View {
        Text(summaryText)
            .font(AppTypography.bodySmall)
            .foregroundColor(AppColors.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
            .animation(nil, value: selectedKey)
    }

    /// Nothing to draw yet because the 90-day history is still being fetched. The card says
    /// so instead of drawing an empty axis — and never invents a bar to fill the space.
    private var showsBuildingPlaceholder: Bool {
        trend.days.isEmpty && trend.isBuildingHistory
    }

    private var buildingPlaceholder: some View {
        VStack(spacing: AppSpacing.sm) {
            if buildingStalled {
                Image(systemName: "clock")
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.textMuted)
            } else {
                ProgressView()
                    .tint(AppColors.textMuted)
            }
            Text(buildingStalled ? "Still building 90-day history" : "Building 90-day history…")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
            Text(buildingStalled
                 ? "This is taking longer than usual. Pull down to check again later."
                 : "Usually a minute or two. It fills in newest weeks first.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, minHeight: Self.chartHeight)
        .accessibilityElement(children: .combine)
    }

    private var summaryText: String {
        if showsBuildingPlaceholder {
            return "Scoring this ticker's recent news…"
        }
        if let day = selectedDay {
            let when = Self.dayLabel(day.date) + (day.isPartial ? " (so far)" : "")
            return "\(when) · \(Self.headlines(day.total)) — "
                + "\(day.positive) positive · \(day.negative) negative · \(day.neutral) neutral"
        }
        let total = trend.total
        guard total > 0 else { return "No scored headlines in this window yet." }
        return "\(Self.headlines(total)) · \(Self.toneWord(trend.netScore)) "
            + "(net \(Self.signed(trend.netScore)))"
    }

    // MARK: - Chart

    private var chart: some View {
        let days: [SentimentTrendDay] = trend.days
        let peak: Int = max(1, days.map { max($0.positive, $0.negative) }.max() ?? 1)
        let domain: ClosedRange<Date> = xDomain
        return Chart {
            ForEach(days) { day in
                positiveBar(day)
                negativeBar(day)
            }
            ForEach(days.filter { $0.neutral > 0 }) { day in
                neutralMark(day)
            }
            // The reference axis: the whole chart reads "above = positive, below = negative".
            RuleMark(y: .value("Zero", 0))
                .foregroundStyle(AppColors.borderStrong)
                .lineStyle(StrokeStyle(lineWidth: 1))
        }
        .chartXScale(domain: domain)
        .chartYScale(domain: Double(-peak)...Double(peak))
        .chartPlotStyle { plot in plot.clipped() }
        .chartXAxis { xAxis }
        .chartYAxis {
            AxisMarks(position: .trailing, values: [Double(-peak), 0, Double(peak)]) { value in
                AxisGridLine().foregroundStyle(AppColors.chartGridline)
                AxisValueLabel {
                    if let v = value.as(Double.self) {
                        Text("\(Int(abs(v)))")
                            .font(AppTypography.caption)
                            .foregroundStyle(AppColors.textMuted)
                    }
                }
            }
        }
        .chartOverlay { proxy in
            GeometryReader { geo in
                Rectangle()
                    .fill(.clear)
                    .contentShape(Rectangle())
                    // A discrete tap, not a drag, so it coexists with the feed's ScrollView.
                    .gesture(
                        SpatialTapGesture().onEnded { tap in
                            guard let plot = proxy.plotFrame else { return }
                            let x = tap.location.x - geo[plot].origin.x
                            guard let date = proxy.value(atX: x, as: Date.self) else { return }
                            let key = nearestDayKey(to: date)
                            selectedKey = (key == selectedKey) ? nil : key
                        }
                    )
            }
        }
        .frame(height: Self.chartHeight)
        .opacity(isUpdating ? 0.45 : 1)
        .animation(.easeInOut(duration: 0.2), value: isUpdating)
        .accessibilityElement(children: .ignore)
        // Says who scored them: the visible "Headlines Cay AI scored" footer is gone.
        .accessibilityLabel("News tone of the headlines Cay AI scored, last \(trend.window.days) days")
        .accessibilityValue(accessibilitySummary)
    }

    private func barOpacity(_ day: SentimentTrendDay) -> Double {
        selectedKey == nil || selectedKey == day.dayKey ? 1.0 : 0.35
    }

    private func positiveBar(_ day: SentimentTrendDay) -> some ChartContent {
        BarMark(
            x: .value("Day", day.date, unit: .day),
            yStart: .value("Start", 0.0),
            yEnd: .value("Positive", Double(day.positive))
        )
        .foregroundStyle(AppColors.gainGraphic)
        .opacity(barOpacity(day))
        .cornerRadius(2)
    }

    private func negativeBar(_ day: SentimentTrendDay) -> some ChartContent {
        BarMark(
            x: .value("Day", day.date, unit: .day),
            yStart: .value("Start", 0.0),
            yEnd: .value("Negative", -Double(day.negative))
        )
        .foregroundStyle(AppColors.lossGraphic)
        .opacity(barOpacity(day))
        .cornerRadius(2)
    }

    private func neutralMark(_ day: SentimentTrendDay) -> some ChartContent {
        RectangleMark(
            x: .value("Day", day.date, unit: .day),
            y: .value("Neutral", 0.0),
            height: .fixed(4)
        )
        .foregroundStyle(AppColors.textMuted)
        .opacity(barOpacity(day))
        .cornerRadius(1)
    }

    private var xAxis: some AxisContent {
        AxisMarks(values: axisDates) { value in
            // The newest 30D tick is today, one day from the axis end: a label starting there
            // ran into the trailing y-axis column and was cut to "S". Anchoring that one label
            // at its trailing edge keeps it inside the plot. 7D labels are centred in their
            // day slot (narrow weekday names); 90D drops month ticks near the end instead.
            //
            // Greedy collision resolution with today's tick placed FIRST: at large Dynamic
            // Type, on a 375 pt phone or in a locale with longer month names, the trailing
            // label now reaches back into the today-7 label, and the neighbour is the one
            // dropped — never today's.
            AxisValueLabel(
                format: axisFormat,
                centered: trend.window == .week,
                anchor: isTrailingTick(value.index, of: value.count) ? .topTrailing : nil,
                collisionResolution: .greedy(
                    priority: value.index == value.count - 1 ? 1 : 0,
                    minimumSpacing: 4
                )
            )
            .font(AppTypography.caption)
            .foregroundStyle(AppColors.textMuted)
        }
    }

    private func isTrailingTick(_ index: Int, of count: Int) -> Bool {
        trend.window == .month && count > 0 && index == count - 1
    }

    /// Explicit tick dates, counted back from today so the newest label is always today:
    /// every day for 7D, weekly for 30D, and each month start for 90D. Returned OLDEST FIRST,
    /// so `isTrailingTick`'s "last index" is today's tick — built newest-first, the trailing
    /// anchor landed on the oldest label instead.
    private var axisDates: [Date] {
        axisDatesUnsorted.sorted()
    }

    private var axisDatesUnsorted: [Date] {
        let cal = Calendar.current
        let today = SentimentTrendDayParser.etToday()
        let lower = xDomain.lowerBound
        switch trend.window {
        case .week:
            return (0..<7).compactMap { cal.date(byAdding: .day, value: -$0, to: today) }
                .filter { $0 >= lower }
        case .month:
            return stride(from: 0, through: 28, by: 7)
                .compactMap { cal.date(byAdding: .day, value: -$0, to: today) }
                .filter { $0 >= lower }
        case .quarter:
            // Month starts, minus any within 7 days of the axis end: a month label there has
            // no room before the y-axis column (the same clipping as the 30D "S").
            let comps = cal.dateComponents([.year, .month], from: today)
            guard let thisMonth = cal.date(from: comps) else { return [] }
            let latest = cal.date(byAdding: .day, value: -7, to: xDomain.upperBound) ?? today
            return (0..<4).compactMap { cal.date(byAdding: .month, value: -$0, to: thisMonth) }
                .filter { $0 >= lower && $0 <= latest }
        }
    }

    private var axisFormat: Date.FormatStyle {
        switch trend.window {
        case .week: return .dateTime.weekday(.abbreviated)
        case .month: return .dateTime.month(.abbreviated).day()
        case .quarter: return .dateTime.month(.abbreviated)
        }
    }

    /// The whole window, today included, even when the oldest days have no data — so a
    /// scope tracked for five days shows five bars at the right edge of a 30-day axis rather
    /// than five fat bars pretending to be a month.
    ///
    /// "Today" is the ET calendar day (`etToday`), because the day keys are ET days: anchored
    /// on the device's own date, the backend's newest bar fell past the end of the axis every
    /// evening west of New York. The end also stretches to cover the newest bar, as a guard.
    private var xDomain: ClosedRange<Date> {
        let cal = Calendar.current
        let today = SentimentTrendDayParser.etToday()
        let start = cal.date(byAdding: .day, value: -(trend.window.days - 1), to: today) ?? today
        let first = trend.days.first.map { cal.startOfDay(for: $0.date) } ?? start
        let lastDay = trend.days.last.map { cal.startOfDay(for: $0.date) } ?? today
        let endDay = max(today, lastDay)
        let end = cal.date(byAdding: .day, value: 1, to: endDay) ?? endDay
        return min(start, first)...end
    }

    private func nearestDayKey(to date: Date) -> String? {
        let cal = Calendar.current
        let target = cal.startOfDay(for: date)
        return trend.days.min(by: {
            abs($0.date.timeIntervalSince(target)) < abs($1.date.timeIntervalSince(target))
        }).flatMap { abs($0.date.timeIntervalSince(target)) < 86_400 * 1.5 ? $0.dayKey : nil }
    }

    // MARK: - Legend + coverage

    /// The legend, with the coverage label ("since Jul 1") right-aligned on the same row (owner,
    /// TestFlight 1.0 (11)). When that row does not fit — large Dynamic Type, a narrow phone, a
    /// long month name — the label drops to its own line, still on the right, instead of
    /// truncating either half.
    private var legend: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: AppSpacing.md) {
                legendItems
                Spacer(minLength: AppSpacing.sm)
                if let coverage = coverageText {
                    coverageLabel(coverage)
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
            }
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                HStack(spacing: AppSpacing.md) {
                    legendItems
                }
                if let coverage = coverageText {
                    coverageLabel(coverage)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var legendItems: some View {
        legendItem(color: AppColors.gainGraphic, label: "Positive", height: 8)
        legendItem(color: AppColors.lossGraphic, label: "Negative", height: 8)
        legendItem(color: AppColors.textMuted, label: "Neutral", height: 3)
    }

    private func legendItem(color: Color, label: String, height: CGFloat) -> some View {
        HStack(spacing: AppSpacing.xs) {
            RoundedRectangle(cornerRadius: 1.5)
                .fill(color)
                .frame(width: 10, height: height)
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }

    private func coverageLabel(_ text: String) -> some View {
        Text(text)
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
    }

    /// How far back this feed's scored headlines go — a property of the SCOPE, so the same on
    /// every window:
    /// - "since Jul 1": the oldest scored day on file (`trackingSince`), which is the day
    ///   scoring began while the history is younger than the backend log keeps;
    /// - "120+ days" once it reaches that edge (`historyReachesRetentionEdge`): the sweep has
    ///   dropped the first days, the date would drift forward daily, and only the bound is true;
    /// - "filling in 90 days…" while the history is still being built.
    /// nil when the backend sent no date — never a date guessed from the bars, which on 7D
    /// would claim a week-old start for a feed scored since July.
    private var coverageText: String? {
        if trend.isBuildingHistory && !trend.days.isEmpty {
            return "filling in 90 days…"
        }
        guard let since = trend.trackingSince else { return nil }
        if trend.historyReachesRetentionEdge() {
            return "\(SentimentTrend.retentionDays)+ days"
        }
        return "since \(Self.shortDate(since))"
    }

    private var accessibilitySummary: String {
        "\(Self.headlines(trend.total)): \(trend.positive) positive, \(trend.negative) negative, "
            + "\(trend.neutral) neutral. \(Self.toneWord(trend.netScore))."
    }

    // MARK: - Formatting

    static func toneWord(_ net: Int) -> String {
        if net >= 20 { return "Mostly positive" }
        if net <= -20 { return "Mostly negative" }
        return "Mixed"
    }

    static func signed(_ value: Int) -> String {
        value > 0 ? "+\(value)" : "\(value)"
    }

    static func headlines(_ count: Int) -> String {
        "\(count) headline\(count == 1 ? "" : "s")"
    }

    private static let dayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.setLocalizedDateFormatFromTemplate("EEE MMM d")
        return f
    }()

    private static let shortFormatter: DateFormatter = {
        let f = DateFormatter()
        f.setLocalizedDateFormatFromTemplate("MMM d")
        return f
    }()

    static func dayLabel(_ date: Date) -> String { dayFormatter.string(from: date) }
    static func shortDate(_ date: Date) -> String { shortFormatter.string(from: date) }
}

#Preview("30 days") {
    let cal = Calendar.current
    let today = cal.startOfDay(for: Date())
    let counts: [(Int, Int, Int)] = [
        (4, 1, 2), (2, 3, 1), (6, 0, 1), (1, 5, 2), (3, 2, 0), (5, 1, 1), (2, 2, 2),
        (0, 4, 1), (3, 1, 3), (7, 2, 1), (2, 1, 0), (1, 1, 1),
    ]
    let days: [SentimentTrendDay] = counts.enumerated().map { i, c in
        let date = cal.date(byAdding: .day, value: -(counts.count - 1 - i), to: today) ?? today
        return SentimentTrendDay(
            dayKey: "preview-\(i)", date: date,
            positive: c.0, negative: c.1, neutral: c.2,
            isPartial: i == counts.count - 1
        )
    }
    return NewsSentimentTrendChart(
        trend: SentimentTrend(
            scope: "ORCL", window: .month, days: days,
            trackingSince: days.first?.date
        ),
        window: .constant(.month)
    )
    .padding()
    .background(AppColors.background)
}
