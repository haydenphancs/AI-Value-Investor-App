//
//  ChatStockWidgetView.swift
//  ios
//
//  Molecule: Rich media stock chart widget rendered inline in chat.
//  Uses Apple's native Charts framework (iOS 16+).
//

import SwiftUI
import Charts

struct ChatStockWidgetView: View {
    let widget: StockChartWidgetData

    /// When the message carrying this widget was created.
    ///
    /// The payload is persisted verbatim into `chat_messages.rich_content` and replayed forever
    /// with no age check, so `is_market_open` is a clock reading frozen at generation time. Without
    /// this, a three-week-old transcript row renders a green "Live" dot at 2am on a Sunday — a
    /// direct false claim about the price sitting next to it. The widget is fetched during the turn
    /// that produced the message, so the message's age IS the payload's age.
    ///
    /// `nil` (previews, or any caller that has no timestamp) keeps the old behaviour.
    var messageDate: Date? = nil

    /// How recently the payload must have been fetched for "Live" / "Closed" to be a claim about
    /// NOW rather than about some arbitrary past moment.
    private static let freshnessWindow: TimeInterval = 10 * 60

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // ── Header: Ticker & Company Name ──────────────────
            headerSection
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.lg)

            // ── Price & Change ─────────────────────────────────
            priceSection
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.sm)

            // ── Chart ─────────────────────────────────────────
            // Two points is the floor for a LINE. A single vertex used to clear the old
            // `!historicalData.isEmpty` gate and draw a full axis + four gridlines around nothing.
            if widget.chartPoints.count >= 2 {
                chartSection
                    .padding(.top, AppSpacing.lg)
                    .padding(.horizontal, AppSpacing.sm)

                chartDateRange
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.xs)
            }

            // ── Stats Grid ────────────────────────────────────
            statsGrid
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.lg)
                .padding(.bottom, AppSpacing.lg)
        }
        .cardSurface(cornerRadius: AppCornerRadius.large)
    }

    // MARK: - Header
    private var headerSection: some View {
        HStack(spacing: AppSpacing.sm) {
            // Ticker badge
            Text(widget.ticker)
                .font(AppTypography.labelEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xxs)
                .background(
                    widget.isPositive
                        ? AppColors.bullish.opacity(0.2)
                        : AppColors.bearish.opacity(0.2)
                )
                .cornerRadius(AppCornerRadius.small)

            Text(widget.companyName)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .lineLimit(1)

            Spacer()

            freshnessBadge
        }
    }

    /// "Live" / "Closed" while the payload is current; an honest "As of <date>" once it isn't.
    @ViewBuilder
    private var freshnessBadge: some View {
        if isPayloadFresh {
            // Market status: green "Live" only while the US session is open, else a muted "Closed".
            Circle()
                .fill((widget.isMarketOpen ?? false) ? AppColors.bullish : AppColors.textMuted)
                .frame(width: 6, height: 6)
            Text((widget.isMarketOpen ?? false) ? "Live" : "Closed")
                .font(AppTypography.captionSmall)
                .foregroundColor(AppColors.textMuted)
        } else if let asOf = asOfLabel {
            Text("As of \(asOf)")
                .font(AppTypography.captionSmall)
                .foregroundColor(AppColors.textMuted)
                .lineLimit(1)
                // Longer than "Closed", so at large Dynamic Type let the (already truncating)
                // company name yield first — the card's vintage outranks its full legal name.
                .layoutPriority(1)
        }
    }

    private var isPayloadFresh: Bool {
        guard let messageDate else { return true }
        // No lower bound: modest device-vs-server clock skew must not demote a live card.
        return Date().timeIntervalSince(messageDate) < Self.freshnessWindow
    }

    /// The vintage of the numbers on this card — the newest bar in the series, falling back to the
    /// message's own date when the series is missing or unparseable.
    private var asOfLabel: String? {
        if let last = widget.chartPoints.last?.date,
           let date = Self.isoDayFormatter.date(from: last) {
            return Self.asOfFormatter.string(from: date)
        }
        if let messageDate { return Self.asOfFormatter.string(from: messageDate) }
        return nil
    }

    // MARK: - Price
    private var priceSection: some View {
        HStack(alignment: .firstTextBaseline, spacing: AppSpacing.sm) {
            Text(widget.formattedPrice)
                .font(AppTypography.dataHero)
                .foregroundColor(AppColors.textPrimary)

            HStack(spacing: AppSpacing.xxs) {
                Image(systemName: widget.isPositive ? "arrow.up.right" : "arrow.down.right")
                    .font(.system(size: 12, weight: .bold))

                Text(widget.formattedAbsChange)
                    .font(AppTypography.bodyEmphasis)

                Text("(\(widget.formattedChange))")
                    .font(AppTypography.bodySmall)
            }
            .foregroundColor(widget.isPositive ? AppColors.bullish : AppColors.bearish)

            Spacer()
        }
    }

    // MARK: - Chart (native Charts framework)
    private var chartSection: some View {
        // Evaluated ONCE: `chartPoints` is a computed property and the domain, the series colour
        // and the accessibility summary all derive from the same closes. During streaming this
        // row re-renders on every token.
        let points = widget.chartPoints
        let closes = points.map(\.close)
        let domain = Self.yDomain(for: closes)
        // The SERIES direction, not the quote's one-day change — see
        // `StockChartWidgetData.isSeriesPositive`.
        let seriesColor = widget.isSeriesPositive ? AppColors.bullish : AppColors.bearish

        return Chart {
            // Area first so the line strokes on top of its own fill (the order every sibling
            // chart uses). `yStart` pins the fill to the VISIBLE floor: the two-argument
            // `AreaMark(x:y:)` baselines at data-space ZERO, which on a zoomed price scale sits
            // ~8x the plot height below the plot rect — that is what painted a translucent wash
            // over the stats grid and the answer text below the card (TestFlight "Chart got
            // wrong!", 2026-08-22). Same defect class as the BarMark fix in
            // ReportHiddenMarketSignalsSection.
            ForEach(points) { point in
                AreaMark(
                    x: .value("Day", point.id),
                    yStart: .value("Base", domain.lowerBound),
                    yEnd: .value("Price", point.close)
                )
                .foregroundStyle(
                    LinearGradient(
                        colors: [seriesColor.opacity(0.3), seriesColor.opacity(0.0)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .interpolationMethod(.monotone)
            }

            ForEach(points) { point in
                LineMark(
                    x: .value("Day", point.id),
                    y: .value("Price", point.close)
                )
                .foregroundStyle(seriesColor)
                .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.monotone)
            }
        }
        .chartXAxis(.hidden)
        .chartYAxis {
            AxisMarks(position: .trailing, values: .automatic(desiredCount: 4)) { value in
                AxisValueLabel {
                    if let price = value.as(Double.self) {
                        Text(Self.axisLabel(price, span: domain.upperBound - domain.lowerBound))
                            .font(AppTypography.captionSmall)
                            .foregroundColor(AppColors.textMuted)
                    }
                }
                AxisGridLine()
                    .foregroundStyle(AppColors.textMuted.opacity(0.2))
            }
        }
        .chartYScale(domain: domain)
        // Belt-and-braces with the `yStart` above: `.monotone` cannot overshoot, but a future
        // interpolation/mark change must not be able to escape the frame again. Clips the plot
        // rect only — the trailing axis labels live outside it and are unaffected.
        .chartPlotStyle { $0.clipped() }
        .frame(height: 140)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(widget.ticker) price chart")
        .accessibilityValue(Self.accessibilitySummary(closes))
    }

    private static func accessibilitySummary(_ closes: [Double]) -> String {
        guard let first = closes.first, let last = closes.last else { return "No price history" }
        let direction = last >= first ? "up" : "down"
        return "\(closes.count) days, \(direction) from \(priceString(first)) to \(priceString(last))"
    }

    /// Decimal places scale with the domain span. A flat `$%.0f` on a ±10% band around a $2.50
    /// stock labels four gridlines "$3 / $3 / $2 / $2", and a sub-dollar ticker gets four "$0" —
    /// an axis that contradicts the price in the header two rows above it.
    ///
    /// Thresholds are set against the tick step `.automatic(desiredCount: 4)` actually produces
    /// (span/4, snapped to a 1/2/2.5/5 × 10^k "nice" value), verified to yield no duplicate and
    /// no negative label from BRK.A down to a $0.02 penny stock. 1 decimal is deliberately
    /// skipped: for money, whole dollars or cents are the natural presentations, and it would
    /// otherwise print "$300.0" where "$300" is what the screenshot rightly showed.
    private static func axisLabel(_ price: Double, span: Double) -> String {
        let digits: Int
        switch span {
        case 5...:     digits = 0
        case 0.05...:  digits = 2
        case 0.005...: digits = 3
        default:       digits = 4
        }
        return String(format: "$%.\(digits)f", price)
    }

    private static func priceString(_ value: Double) -> String {
        String(format: "$%.2f", value)
    }

    /// `closes` is already filtered to finite, strictly-positive values by `chartPoints`.
    private static func yDomain(for closes: [Double]) -> ClosedRange<Double> {
        guard let minVal = closes.min(), let maxVal = closes.max() else {
            return ChartDomain.make([], includeZero: false, fallback: 0...1)
        }
        // A flat series (single point / all-equal closes → min == max) makes proportional padding
        // 0, so the domain would collapse to X...X and Charts' (v-min)/(max-min) normalization
        // divides by zero → a degenerate/invisible line. Fall back to a proportional nominal pad.
        //
        // Deliberately NOT routed through `ChartDomain.make` for the non-empty case: its
        // `minimumSpan` of 1.0 is tuned for ratio/percentage charts and would force a $1-wide
        // domain onto a penny stock — flattening a real 20-cent range and pushing the floor
        // negative. It is used above only as the no-data fallback.
        let span = maxVal - minVal
        let padding = span > 0 ? span * 0.1 : max(abs(maxVal) * 0.01, 0.01)
        // Floor at 0: a share price is never negative, and on a sub-dollar ticker the padding can
        // exceed the minimum — which would print a "$-0.049" gridline under the line. Every close
        // is > 0, so `lower < minVal <= maxVal < upper` still holds and the range stays valid.
        let lower = max(minVal - padding, 0)
        return lower...(maxVal + padding)
    }

    // MARK: - Chart Date Range
    private var chartDateRange: some View {
        // Read from the PLOTTED points, not `historicalData`: if the oldest bar was filtered out
        // for a bad close, labelling the caption from the raw array names a day the line does not
        // actually start on.
        let points = widget.chartPoints
        return HStack {
            if let first = points.first {
                Text(Self.formatDateLabel(first.date))
                    .font(AppTypography.captionSmall)
                    .foregroundColor(AppColors.textMuted)
            }

            Spacer()

            if let last = points.last {
                Text(Self.formatDateLabel(last.date))
                    .font(AppTypography.captionSmall)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    // MARK: - Stats Grid
    private var statsGrid: some View {
        // A real fixed 2-column grid so every stat aligns in a column (the old hand-rolled HStack +
        // Spacer rows floated Market Cap to the right edge). Order puts Market Cap in the LEFT column,
        // aligned under Volume / Day High.
        LazyVGrid(
            columns: [
                GridItem(.flexible(), alignment: .leading),
                GridItem(.flexible(), alignment: .leading),
            ],
            alignment: .leading,
            spacing: AppSpacing.md
        ) {
            WidgetStatItem(label: "Day High", value: widget.formattedDayHigh)
            WidgetStatItem(label: "Day Low", value: widget.formattedDayLow)
            WidgetStatItem(label: "Volume", value: widget.formattedVolume)
            WidgetStatItem(label: "Avg Volume", value: widget.formattedAvgVolume)
            if let mc = widget.formattedMarketCap {
                WidgetStatItem(label: "Market Cap", value: mc)
            }
            if let pe = widget.peRatio {
                WidgetStatItem(label: "P/E Ratio", value: String(format: "%.1f", pe))
            }
        }
    }

    // MARK: - Helpers

    /// PARSING formatter for FMP's fixed `yyyy-MM-dd`. Pinned to `en_US_POSIX` + Gregorian: an
    /// unpinned `DateFormatter` resolves a literal `dateFormat` against the DEVICE's calendar, so
    /// on a Hijri or Persian locale the parse lands in the wrong era and the chart's date range
    /// prints the wrong month. Same class of defect the Account screen's "Member since" hit.
    private static let marketTimeZone = TimeZone(identifier: "America/New_York") ?? .current

    private static let isoDayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = marketTimeZone
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    /// DISPLAY formatters keep `Locale.current` so month names stay localised, but are pinned to
    /// the Gregorian calendar so the number matches the market's own date. Hoisted to `static`:
    /// the old code allocated two `DateFormatter`s on every body pass of a streaming row.
    ///
    /// The time zone MUST match `isoDayFormatter`'s. These labels are a parse-then-format round
    /// trip of a bare `yyyy-MM-dd`, so a mismatch shifts the instant across midnight and prints
    /// the WRONG DAY: "2026-08-21" parsed at ET midnight, formatted in Denver, is "Aug 20".
    /// ET is the right anchor either way — these are trading days, not the reader's days.
    private static let dayMonthFormatter: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = marketTimeZone
        f.setLocalizedDateFormatFromTemplate("MMM d")
        return f
    }()

    private static let asOfFormatter: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = marketTimeZone
        f.setLocalizedDateFormatFromTemplate("MMM d, yyyy")
        return f
    }()

    private static func formatDateLabel(_ dateStr: String) -> String {
        guard let date = isoDayFormatter.date(from: dateStr) else { return dateStr }
        return dayMonthFormatter.string(from: date)
    }
}

// MARK: - Stat Item
private struct WidgetStatItem: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            Text(label)
                .font(AppTypography.captionSmall)
                .foregroundColor(AppColors.textMuted)

            Text(value)
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Preview
#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.lg) {
            ChatStockWidgetView(widget: StockChartWidgetData.sample)
            // A replayed transcript row: the badge must read "As of …", never a green "Live".
            ChatStockWidgetView(
                widget: StockChartWidgetData.sample,
                messageDate: Date().addingTimeInterval(-21 * 24 * 3600)
            )
        }
        .padding()
    }
    .background(AppColors.background)
}
