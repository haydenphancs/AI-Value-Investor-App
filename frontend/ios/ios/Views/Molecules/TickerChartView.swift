//
//  TickerChartView.swift
//  ios
//
//  Molecule: Price chart with time range selector, chart type switching,
//  technical indicator overlays, sub-chart panes, interactive crosshair,
//  and adaptive x-axis date labels.
//

import SwiftUI

struct TickerChartView: View {
    let pricePoints: [StockPricePoint]
    let isPositive: Bool
    @Binding var selectedRange: ChartTimeRange
    @ObservedObject var chartSettings: ChartSettings
    let assetContext: ChartAssetContext
    var chartDataVersion: Int = 0
    var chartEventDates: ChartEventDates? = nil
    /// Previous trading day's close — anchors the 1D dashed baseline. nil → the
    /// baseline falls back to the first visible point (start of the range).
    var previousClose: Double? = nil

    /// Reference price for the dashed baseline: prior-day close on 1D, else the
    /// first visible point (start of the selected range).
    private var baselineClose: Double? {
        if selectedRange == .oneDay, let pc = previousClose { return pc }
        return visiblePoints.first?.close
    }

    @State private var showSettingsSheet = false
    @State private var showIntervalMenu = false
    @StateObject private var crosshairState = CrosshairState()
    @StateObject private var viewportState = ChartViewportState()

    /// Number of leading data points the backend fetched as warm-up
    /// for technical indicators (MACD, RSI, etc.).  These points are
    /// not shown on the main chart but are included in `allPricePoints`
    /// passed to SubChartCanvas so indicator values start from the
    /// left edge of the visible chart.
    private var warmupCount: Int {
        // ALL range already has maximum history — no warm-up trimming.
        // 1D / 1W intraday data is dense enough that warm-up is tiny;
        // still trim for correctness.
        guard !pricePoints.isEmpty else { return 0 }
        if selectedRange == .all { return 0 }

        let calendar = Calendar.current
        let now = Date()
        let displayStart: Date?

        // Crypto trades 24/7 — show exact 1D/1W windows (Robinhood-style).
        // Stocks need extra calendar days to cover weekends/holidays.
        let isCrypto = assetContext == .crypto
        switch selectedRange {
        case .oneDay:       displayStart = calendar.date(byAdding: .day, value: isCrypto ? -1 : -3, to: now)
        case .oneWeek:      displayStart = calendar.date(byAdding: .day, value: isCrypto ? -7 : -10, to: now)
        case .threeMonths:  displayStart = calendar.date(byAdding: .month, value: -3, to: now)
        case .sixMonths:    displayStart = calendar.date(byAdding: .month, value: -6, to: now)
        case .oneYear:      displayStart = calendar.date(byAdding: .year, value: -1, to: now)
        case .fiveYears:    displayStart = calendar.date(byAdding: .year, value: -5, to: now)
        case .all:          displayStart = nil
        }

        guard let start = displayStart else { return 0 }

        for (index, point) in pricePoints.enumerated() {
            if let pointDate = ChartDateFormatters.parseDate(point.date),
               pointDate >= start {
                return index
            }
        }
        return 0
    }

    /// Slice of price points visible in the current viewport.
    /// For 1D intraday stock charts, filters to only the latest trading day
    /// to prevent multiple days overlapping on the time-based X axis.
    private var visiblePoints: [StockPricePoint] {
        guard !pricePoints.isEmpty else { return [] }
        let start = max(0, min(viewportState.visibleStart, pricePoints.count - 1))
        let end = max(start, min(pricePoints.count - 1, viewportState.visibleEnd))
        let sliced = Array(pricePoints[start...end])

        // For 1D stock charts, only show the latest trading day
        if selectedRange == .oneDay && assetContext != .crypto {
            return TradingDayHelper.filterToLatestDay(sliced)
        }
        return sliced
    }

    /// Close prices preceding the visible range so MA/Bollinger
    /// calculations have warm-up data and lines start from the left edge.
    private var overlayLookbackCloses: [Double] {
        let start = max(0, min(viewportState.visibleStart, pricePoints.count))
        guard start > 0 else { return [] }
        return pricePoints[0..<start].map { $0.close }
    }

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            // Scrubbing price overlay (shows selected point's price when dragging)
            if crosshairState.isDragging, let idx = crosshairState.selectedIndex,
               idx >= 0, idx < visiblePoints.count {
                let point = visiblePoints[idx]
                HStack(spacing: AppSpacing.xs) {
                    Text(formatPrice(point.close))
                        .font(AppTypography.heading)
                        .foregroundColor(AppColors.textPrimary)

                    if let open = point.open {
                        let change = point.close - open
                        let pct = open != 0 ? (change / open) * 100 : 0
                        let sign = change >= 0 ? "+" : ""
                        Text("\(sign)\(String(format: "%.2f", change)) (\(sign)\(String(format: "%.2f", pct))%)")
                            .font(AppTypography.labelSmall)
                            .foregroundColor(change >= 0 ? AppColors.bullish : AppColors.bearish)
                    }

                    if let vol = point.volume, vol > 0 {
                        Text("Vol: \(formatVolume(vol))")
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textMuted)
                    }

                    if chartSettings.showExtendedHours && assetContext.supportsExtendedHours && point.isExtendedHours {
                        Text("EXT")
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundColor(AppColors.textMuted)
                            .padding(.horizontal, 4)
                            .padding(.vertical, 1)
                            .background(
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(AppColors.cardBackgroundLight)
                            )
                    }

                    Spacer()
                }
                .padding(.horizontal, AppSpacing.lg)
                .transition(.opacity)
            }

            // Main price chart with crosshair gesture
            ZStack {
                MainChartCanvas(
                    pricePoints: visiblePoints,
                    isPositive: isPositive,
                    chartType: chartSettings.chartType,
                    overlays: chartSettings.activeOverlays,
                    showExtendedHours: chartSettings.showExtendedHours && assetContext.supportsExtendedHours,
                    lookbackCloses: overlayLookbackCloses,
                    chartEventDates: chartSettings.showEarningsDates ? chartEventDates : nil,
                    useIntradayTimeMapping: selectedRange == .oneDay && assetContext != .crypto,
                    baselineClose: baselineClose
                )

                ChartCrosshairGesture(
                    pricePoints: visiblePoints,
                    selectedRange: selectedRange,
                    crosshairState: crosshairState,
                    viewportState: viewportState,
                    timeFractions: (selectedRange == .oneDay && assetContext != .crypto)
                        ? TradingDayHelper.timeFractions(for: visiblePoints)
                        : nil
                )
            }
            .frame(height: 140)
            .padding(.horizontal, AppSpacing.lg)

            // X-axis date labels
            if visiblePoints.count > 1 {
                ChartXAxisLabels(
                    pricePoints: visiblePoints,
                    selectedRange: selectedRange,
                    useIntradayTimeMapping: selectedRange == .oneDay && assetContext != .crypto
                )
                    .padding(.horizontal, AppSpacing.lg)
            }

            // Sub-charts (Volume, RSI, MACD, Stoch)
            // Pass full data for indicator warm-up, visible slice for rendering
            ForEach(chartSettings.activeSubCharts) { indicator in
                SubChartCanvas(
                    indicator: indicator,
                    pricePoints: visiblePoints,
                    allPricePoints: pricePoints,
                    visibleStartIndex: viewportState.visibleStart,
                    crosshairState: crosshairState
                )
            }

            // Time range selector + icons
            HStack(spacing: 4) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 2) {
                        ForEach(ChartTimeRange.allCases, id: \.rawValue) { range in
                            TimeRangeButton(range: range, isSelected: selectedRange == range) {
                                withAnimation(.easeInOut(duration: 0.2)) {
                                    selectedRange = range
                                }
                            }
                        }
                    }
                }

                // Interval selector — only show when multiple intervals are available
                if selectedRange.allowedIntervals.count > 1 {
                    // Custom Liquid Glass dropdown (see intervalMenuOverlay) —
                    // matches the Assets-tab Sort popup. Not a native Menu (can't
                    // shrink/restyle it) and not a popover (has a beak).
                    Button {
                        showIntervalMenu.toggle()
                    } label: {
                        HStack(spacing: 2) {
                            Image(systemName: "clock")
                                .font(.system(size: 10))
                            Text(chartSettings.selectedInterval.displayName)
                                .font(AppTypography.labelSmall)
                        }
                        .fixedSize()
                        .foregroundColor(AppColors.textMuted)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 4)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                .fill(AppColors.cardBackgroundLight.opacity(0.5))
                        )
                    }
                    .buttonStyle(.plain)
                    .anchorPreference(key: ChartIntervalAnchorKey.self, value: .bounds) { $0 }
                }

                // Settings icon
                Button(action: {
                    showSettingsSheet = true
                }) {
                    Image(systemName: "gearshape")
                        .font(AppTypography.iconDefault)
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())
            }
            .padding(.horizontal, AppSpacing.lg)
        }
        // Custom interval dropdown, anchored to the "5 min" chip. Opens UPWARD
        // over the chart so it floats correctly without the parent's help.
        .overlayPreferenceValue(ChartIntervalAnchorKey.self) { anchor in
            GeometryReader { geo in
                intervalMenuOverlay(anchor: anchor, geo: geo)
            }
        }
        .sheet(isPresented: $showSettingsSheet) {
            ChartSettingsSheet(chartSettings: chartSettings, assetContext: assetContext)
                .transaction { $0.disablesAnimations = true }
        }
        .onChange(of: selectedRange) {
            crosshairState.selectedIndex = nil
            crosshairState.isDragging = false
            showIntervalMenu = false
        }
        .onChange(of: chartDataVersion) {
            // Reset viewport when new data is loaded, offsetting past warm-up data
            viewportState.reset(totalCount: pricePoints.count, displayStart: warmupCount)
        }
        .onChange(of: pricePoints.count) {
            // When zoomed, extend to include new candles without resetting zoom/pan
            if viewportState.isZoomed {
                viewportState.extendToEnd(newTotalCount: pricePoints.count)
            } else {
                viewportState.reset(totalCount: pricePoints.count, displayStart: warmupCount)
            }
        }
        .onAppear {
            viewportState.reset(totalCount: pricePoints.count, displayStart: warmupCount)
        }
    }

    private func formatPrice(_ price: Double) -> String {
        if price >= 1 {
            return String(format: "$%.2f", price)
        } else {
            return String(format: "$%.6f", price)
        }
    }

    private func formatVolume(_ volume: Double) -> String {
        if volume >= 1_000_000_000 {
            return String(format: "%.1fB", volume / 1_000_000_000)
        } else if volume >= 1_000_000 {
            return String(format: "%.1fM", volume / 1_000_000)
        } else if volume >= 1_000 {
            return String(format: "%.1fK", volume / 1_000)
        } else {
            return String(format: "%.0f", volume)
        }
    }

    // MARK: - Interval dropdown (custom Liquid Glass popup)

    private static let intervalMenuWidth: CGFloat = 124
    private static let intervalRowHeight: CGFloat = 33

    /// Scrim (tap to dismiss) + the glass panel positioned just ABOVE the chip.
    @ViewBuilder
    private func intervalMenuOverlay(anchor: Anchor<CGRect>?, geo: GeometryProxy) -> some View {
        if showIntervalMenu, let anchor {
            let rect = geo[anchor]
            let intervals = selectedRange.allowedIntervals
            let width = Self.intervalMenuWidth
            let panelHeight = CGFloat(intervals.count) * Self.intervalRowHeight + AppSpacing.sm
            // Right-align to the chip, clamped to the chart bounds.
            let x = min(max(AppSpacing.sm, rect.maxX - width), geo.size.width - width - AppSpacing.sm)
            // Open upward so the panel never spills under the tabs below.
            let y = rect.minY - AppSpacing.xs - panelHeight

            ZStack(alignment: .topLeading) {
                Color.black.opacity(0.001)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .contentShape(Rectangle())
                    .onTapGesture { showIntervalMenu = false }

                VStack(spacing: 0) {
                    ForEach(intervals) { interval in
                        intervalRow(interval)
                    }
                }
                .frame(width: width, alignment: .leading)
                .padding(.vertical, AppSpacing.xs)
                .glassEffect(.regular, in: RoundedRectangle(cornerRadius: AppCornerRadius.large))
                .offset(x: x, y: y)
            }
            .transition(.identity)
        }
    }

    private func intervalRow(_ interval: ChartInterval) -> some View {
        Button {
            // Preserve the no-animation interval switch from the old menu.
            var transaction = Transaction()
            transaction.disablesAnimations = true
            withTransaction(transaction) {
                chartSettings.selectedInterval = interval
            }
            showIntervalMenu = false
        } label: {
            HStack(spacing: AppSpacing.sm) {
                ZStack {
                    if chartSettings.selectedInterval == interval {
                        Image(systemName: "checkmark")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundColor(AppColors.primaryBlue)
                    }
                }
                .frame(width: 16)

                Text(interval.displayName)
                    .font(.system(size: 14))
                    .foregroundColor(AppColors.textPrimary)

                Spacer(minLength: 0)
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, 7)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// Carries the interval chip's bounds up so the dropdown can anchor to it.
private struct ChartIntervalAnchorKey: PreferenceKey {
    static var defaultValue: Anchor<CGRect>? = nil
    static func reduce(value: inout Anchor<CGRect>?, nextValue: () -> Anchor<CGRect>?) {
        value = nextValue() ?? value
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selectedRange: ChartTimeRange = .threeMonths
        @StateObject private var chartSettings = ChartSettings()

        var body: some View {
            TickerChartView(
                pricePoints: [
                    StockPricePoint(date: "2024-10-01", close: 165, open: 163, high: 166, low: 162, volume: 45_000_000),
                    StockPricePoint(date: "2024-10-15", close: 168, open: 165, high: 169, low: 164, volume: 48_000_000),
                    StockPricePoint(date: "2024-11-01", close: 170, open: 168, high: 171, low: 167, volume: 50_000_000),
                    StockPricePoint(date: "2024-11-15", close: 172, open: 170, high: 173, low: 169, volume: 47_000_000),
                    StockPricePoint(date: "2024-12-01", close: 169, open: 172, high: 173, low: 168, volume: 52_000_000),
                    StockPricePoint(date: "2024-12-15", close: 174, open: 169, high: 175, low: 168, volume: 55_000_000),
                    StockPricePoint(date: "2025-01-02", close: 171, open: 174, high: 175, low: 170, volume: 46_000_000),
                    StockPricePoint(date: "2025-01-15", close: 175, open: 171, high: 176, low: 170, volume: 49_000_000),
                    StockPricePoint(date: "2025-02-01", close: 173, open: 175, high: 176, low: 172, volume: 44_000_000),
                    StockPricePoint(date: "2025-02-15", close: 178, open: 173, high: 179, low: 172, volume: 58_000_000),
                ],
                isPositive: true,
                selectedRange: $selectedRange,
                chartSettings: chartSettings,
                assetContext: .stock,
                chartDataVersion: 0
            )
            .padding(.vertical)
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
