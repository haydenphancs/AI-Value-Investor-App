//
//  TickerChartView.swift
//  ios
//
//  Molecule: Price chart with time range selector, chart type switching,
//  technical indicator overlays, sub-chart panes, interactive crosshair,
//  and adaptive x-axis date labels.
//
//  Data: the range payload (`pricePoints`) plus older history the screen's loader pages in
//  as the user pans left (`history`), merged and computed ONCE into a `ChartSeries`. Every
//  overlay and pane reads that series, so an indicator is drawn wherever it validly exists,
//  from the left edge, at any scroll position. While older history can still load, the
//  window cannot go further left than `ChartWarmup.bars` bars from the first bar in hand, so
//  every visible value has its full lookback; only at the true first bar does an indicator
//  start inside the window — where no earlier data exists.
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
    /// Drawn in the chart frame only while `pricePoints` is empty — a remembered range can
    /// now be the one a screen OPENS on, so "no bars yet" and "no bars for this range" are
    /// first-paint states that used to need a tap to reach. nil → the empty frame, as before.
    var placeholder: ChartPlaceholder? = nil
    /// Older bars, strictly before `pricePoints`, paged in by the screen's
    /// `ChartHistoryLoader`. Plain values: this molecule never fetches.
    var history: ChartHistoryState = .empty
    /// Ask the screen for the next older page (`true` = the user tapped Retry).
    var onNeedOlderBars: ((Bool) -> Void)? = nil
    /// The range / interval `pricePoints` were FETCHED for. The pill flips on the tap, the
    /// bars arrive later; every range-dependent mapping follows the bars, never the pill
    /// (3M→1D used to filter DAILY bars to "the latest day" and flash "No chart data").
    var dataRange: ChartTimeRange? = nil
    var dataInterval: ChartInterval? = nil

    @State private var showSettingsSheet = false
    @State private var showIntervalMenu = false
    /// Held in @State, NOT @StateObject: this view must not re-run on every scrub step. Only
    /// the leaf views (scrub header, crosshair, pane readouts) observe it.
    @State private var crosshairState = CrosshairState()
    @StateObject private var viewportState = ChartViewportState()
    @State private var seriesBox = ChartSeriesBox()
    /// Bumped when a background series build lands, to re-run `body`.
    @State private var seriesTick = 0
    @Environment(\.chartTouchRegion) private var touchRegion

    // MARK: - What the bars in hand are

    /// The range the bars in hand were fetched for.
    private var displayedRange: ChartTimeRange { dataRange ?? selectedRange }
    private var displayedInterval: ChartInterval { dataInterval ?? chartSettings.selectedInterval }

    /// The pill (or interval) changed and its bars have not arrived yet.
    private var isAwaitingData: Bool {
        (dataRange != nil && dataRange != selectedRange)
            || (dataInterval != nil && dataInterval != chartSettings.selectedInterval)
    }

    private var stamp: ChartDataStamp {
        ChartDataStamp(range: displayedRange, interval: displayedInterval, version: chartDataVersion)
    }

    /// True when the 1D chart positions each point by its TIME OF DAY rather than
    /// by its index — which is what leaves the untraded remainder of the session
    /// empty on the right instead of stretching a morning across the full width.
    ///
    /// This used to exclude crypto (`assetContext != .crypto`), so a Bitcoin 1D
    /// chart fell back to index mapping and stretched. Commodities were WORSE:
    /// they were included but measured against the 09:30–16:00 equity window, so
    /// every overnight bar clamped to 0 and piled up on the left edge. Both are
    /// fixed by choosing the WINDOW per asset class instead of opting them out.
    private var usesIntradayTimeMapping: Bool { displayedRange == .oneDay }

    /// True when the bars in hand actually include pre/after-hours prints.
    ///
    /// Measured on the FULL fetched series, not the viewport slice: a pinch-zoom
    /// that excludes the pre-market bars must not flip the window mid-gesture and
    /// re-caption the axis. The window is chosen from the DATA as well as the flag,
    /// so a screen whose backend fetched the regular session only can never draw
    /// 09:30–16:00 bars on a 04:00–20:00 axis — the mirror image of the old
    /// GLD-on-a-24-hour-axis defect.
    private var hasExtendedBars: Bool {
        // Memoised per payload: `isExtendedHours` parses the time out of each date string,
        // and this is read on every pan frame.
        seriesBox.memoizedExtended(for: pricePoints) {
            pricePoints.contains { $0.isExtendedHours }
        }
    }

    /// The session the 1D chart is measured against: the 09:30–16:00 ET bell for
    /// equities/ETFs/indices, 04:00–20:00 when the series in hand carries pre/after-hours
    /// bars, the whole calendar day for crypto.
    ///
    /// Driven by the DATA, not by the toggle: the toggle drives the FETCH (the view model
    /// refetches on `$showExtendedHours`), and the bars that arrive decide the axis. Reading
    /// the toggle here too would re-window the still-extended series to the bell for the
    /// duration of the refetch — the pre-market bars would pile on the left edge for a
    /// moment, the exact defect this fix removes. The Home/watchlist card sparkline is
    /// always measured on the bell (backend `intraday_span`), so card and chart agree only
    /// with the toggle OFF — with it ON the chart is deliberately wider.
    private var sessionWindow: TradingDayHelper.SessionWindow {
        let wantsExtended = assetContext.supportsExtendedHours
            && usesIntradayTimeMapping
            && hasExtendedBars
        let window = TradingDayHelper.window(for: assetContext, extendedHours: wantsExtended)
        // Early close (13:00 ET bell): judged from the bars' OWN date, so the axis and the
        // line follow the session the data describes. Only the equity bell narrows — a
        // round-the-clock window has no early close.
        if usesIntradayTimeMapping, window != .roundTheClock,
           let last = pricePoints.last?.date, last.count >= 10,
           MarketHoursUtil.isEarlyClose(String(last.prefix(10))) {
            // Extended hours on a half-day: pre-market is unchanged and after-hours runs
            // 13:00–17:00 ET (the exchanges do trade it; the backend passes those bars
            // through unfiltered), so the extended window ends at 17:00, not 13:00 —
            // narrowing it piled two hours of real prints on the right edge (W2 E-3).
            return window == .extended
                ? TradingDayHelper.SessionWindow.extendedHalfDay
                : .halfDay
        }
        return window
    }

    /// Shading, boundary dashes and the EXT badge follow the same rule as the window.
    private var drawsExtendedHours: Bool {
        assetContext.supportsExtendedHours && hasExtendedBars
    }

    /// Per-bar session fractions for the 1D chart (from the series' ET minute-of-day — no
    /// DateFormatter per bar), or nil for index mapping.
    private var intradayTimeFractions: [CGFloat]? {
        guard usesIntradayTimeMapping else { return nil }
        let series = self.series
        let start = min(series.latestSessionStart, series.count)
        return TradingDayHelper.timeFractions(minutes: series.minuteOfDay[start...], window: sessionWindow)
    }

    /// Sub-panes the BARS IN HAND cannot draw honestly, whatever the asset class allows.
    ///
    /// The enabled set is now one persisted preference shared by every screen, so a
    /// Stochastic turned on for a stock arrives on WTI and Henry Hub too — FRED spot
    /// series, CLOSE ONLY (`commodity_service._fred_history`). A series with no volume
    /// draws an empty Volume pane. Judged on the FULL range payload, and never written back
    /// to `chartSettings` — the preference survives for the next stock. An empty series
    /// hides nothing, so panes do not jump in when the first bars land.
    private var unavailableSubCharts: Set<TechnicalIndicatorType> {
        guard !pricePoints.isEmpty else { return [] }
        var missing: Set<TechnicalIndicatorType> = []
        if !pricePoints.contains(where: { $0.high != nil && $0.low != nil }) {
            missing.insert(.stochastic)
        }
        if !pricePoints.contains(where: { ($0.volume ?? 0) > 0 }) {
            missing.insert(.volume)
        }
        // CoinGecko's intraday "volume" is a ROLLING 24 h total at each timestamp, not the
        // bar's own volume: drawn as bars it is a near-flat wall that says nothing per bar.
        if assetContext == .crypto && displayedInterval.isIntraday {
            missing.insert(.volume)
        }
        return missing
    }

    /// Chart types the BARS IN HAND cannot draw. Candle and Bar need open/high/low; the
    /// renderers fill a missing range from the close, so a close-only series (WTI, Henry
    /// Hub — FRED spot) drew a row of flat dashes under a saved Candle. Same rule as
    /// `unavailableSubCharts`: judged on the full series, empty hides nothing, and the saved
    /// type is never rewritten — the next stock still opens on Candle.
    private var unavailableChartTypes: Set<ChartType> {
        guard !pricePoints.isEmpty else { return [] }
        let hasOHLC = pricePoints.contains { $0.open != nil && $0.high != nil && $0.low != nil }
        return hasOHLC ? [] : [.candle, .bar]
    }

    /// The type the canvas draws: the saved preference unless this asset class or these
    /// bars cannot draw it honestly, then Line.
    private var drawnChartType: ChartType {
        let preferred = chartSettings.chartType
        return assetContext.allowedChartTypes.contains(preferred) && !unavailableChartTypes.contains(preferred)
            ? preferred : .line
    }

    /// Sub-panes drawn: enabled, offered on this asset class, and drawable from these bars.
    private var drawnSubCharts: [TechnicalIndicatorType] {
        let missing = unavailableSubCharts
        return chartSettings.activeSubCharts.filter { assetContext.allowedSubCharts.contains($0) }
            .filter { !missing.contains($0) }
    }

    // MARK: - Series, bounds, frame

    /// Older history does not apply on 1D (one session, never pans), and none exists on ALL.
    private var historyExhausted: Bool {
        history.isExhausted || displayedRange == .all
    }

    private var series: ChartSeries {
        _ = seriesTick
        let older = usesIntradayTimeMapping ? [] : history.olderBars
        let last = pricePoints.last
        let key = ChartSeriesBox.Key(
            historyRevision: usesIntradayTimeMapping ? 0 : history.revision,
            olderCount: older.count,
            payloadCount: pricePoints.count,
            payloadFirst: pricePoints.first?.date,
            payloadLast: last?.date,
            lastClose: last?.close,
            lastHigh: last?.high,
            lastLow: last?.low,
            lastVolume: last?.volume,
            cutoff: seriesBox.cutoff(for: displayedRange, crypto: assetContext == .crypto, lastDate: last?.date),
            stamp: stamp,
            reachedStart: usesIntradayTimeMapping ? false : history.reachedStart
        )
        return seriesBox.series(for: key, older: older, payload: pricePoints) {
            seriesTick &+= 1
        }
    }

    private func bounds(for series: ChartSeries) -> ChartViewportBounds {
        // The leftmost bars are warm-up — their MA/RSI/MACD lack a full lookback — unless they
        // are the symbol's FIRST bars. Paging that merely stopped (an intraday look-back
        // bound, a provider's plan cap, a refused page) keeps the floor. Read from the series
        // BUILT, not the loader's newest state: a long merge builds in the background, and the
        // old series must not be floored by a flag that describes the bars still building.
        let reachedStart = seriesBox.builtKey?.reachedStart ?? false
        let floor = reachedStart ? 0 : min(ChartWarmup.bars, series.rangeStartIndex)
        return ChartViewportBounds.make(
            total: series.count,
            floorIndex: floor,
            defaultCount: series.count - series.rangeStartIndex
        )
    }

    /// The window to DRAW this pass: the committed one once it belongs to the bars in hand,
    /// else the series' default window — computed here, so no frame ever draws new bars
    /// through an old range's window.
    private func effectiveWindow(series: ChartSeries, bounds: ChartViewportBounds) -> ChartWindow {
        // The series in hand was built for the stamp the window was committed for — the
        // normal case, and also a long history still building for a NEW stamp in the
        // background (the old series keeps its own window until the swap).
        if let built = seriesBox.builtKey?.stamp, built == viewportState.appliedStamp {
            return bounds.clamp(viewportState.window)
        }
        return bounds.defaultWindow(rangeStart: series.rangeStartIndex)
    }

    private func chartFrame(series: ChartSeries, bounds: ChartViewportBounds, window: ChartWindow) -> ChartFrame {
        if usesIntradayTimeMapping {
            // 1D: the latest session, derived every pass — never a stored window.
            let start = min(series.latestSessionStart, series.count)
            return ChartFrame(
                drawRange: start..<series.count,
                slotLeft: Double(start),
                slotCount: Double(max(series.count - start, 1)),
                timeFractions: intradayTimeFractions,
                timeSlotFraction: TradingDayHelper.slotFraction(
                    minutes: series.minuteOfDay[start...],
                    window: sessionWindow,
                    intervalMinutes: displayedInterval.minutes
                )
            )
        }
        return ChartFrame(
            drawRange: bounds.drawRange(window),
            slotLeft: bounds.slotLeft(window),
            slotCount: window.visibleCount,
            timeFractions: nil
        )
    }

    /// Reference price for the dashed baseline: prior-day close on 1D, else the
    /// first visible point (start of the window — not the hidden bar past the edge).
    private func baselineClose(series: ChartSeries, frame: ChartFrame) -> Double? {
        if displayedRange == .oneDay, let pc = previousClose { return pc }
        return frame.visibleRange.first.flatMap { $0 < series.count ? series.close[$0] : nil }
    }

    @ViewBuilder
    private func chartPlaceholderView(_ placeholder: ChartPlaceholder) -> some View {
        switch placeholder {
        case .loading:
            // Same block as `DetailHeaderChartSkeleton`'s chart placeholder.
            RoundedRectangle(cornerRadius: AppCornerRadius.medium, style: .continuous)
                .fill(AppColors.cardBackgroundLight)
                .shimmer()
                .accessibilityLabel("Loading chart")
        case .note(let text):
            Text(text)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, AppSpacing.lg)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    var body: some View {
        let series = self.series
        let bounds = bounds(for: series)
        let _ = { viewportState.bounds = bounds }()
        let window = effectiveWindow(series: series, bounds: bounds)
        let frame = chartFrame(series: series, bounds: bounds, window: window)
        let baseline = baselineClose(series: series, frame: frame)
        let drawnType = drawnChartType
        let overlays = chartSettings.activeOverlays
        let yRange = ChartYRange.compute(
            series: series,
            range: frame.visibleRange,
            useOHLC: drawnType == .candle || drawnType == .bar,
            overlays: overlays,
            baseline: baseline
        )
        let awaiting = isAwaitingData || (seriesBox.isBuilding && seriesBox.builtKey?.stamp != stamp)
        let atFloor = !usesIntradayTimeMapping && !historyExhausted
            && bounds.slotLeft(window) <= Double(bounds.floorIndex) + 0.5
        let canNavigate = !usesIntradayTimeMapping && !awaiting && series.count > 1
        // BackSwipe leaves an edge swipe that starts on the chart alone only while the chart
        // can pan it — on 1D (or while data loads) that swipe is the screen's back gesture.
        let _ = { touchRegion?.isPannable = canNavigate }()

        VStack(spacing: AppSpacing.sm) {
            // Main price chart with crosshair gesture. With no bars and a placeholder, the
            // placeholder REPLACES the canvas: drawn on top, its note overprinted the canvas's
            // own centred "No chart data" text (and VoiceOver read both).
            ZStack {
                if pricePoints.isEmpty, let placeholder {
                    chartPlaceholderView(placeholder)
                } else {
                    MainChartCanvas(
                        series: series,
                        frame: frame,
                        yRange: yRange,
                        isPositive: isPositive,
                        // A persisted Candle/Bar preference is coerced on a source that has no
                        // OHLC (crypto, close-only commodities) — see `drawnChartType`.
                        chartType: drawnChartType,
                        overlays: overlays,
                        showExtendedHours: drawsExtendedHours,
                        chartEventDates: chartSettings.showEarningsDates ? chartEventDates : nil,
                        useIntradayTimeMapping: usesIntradayTimeMapping,
                        sessionWindow: sessionWindow,
                        baselineClose: baseline
                    )
                    .equatable()

                    ChartInteractionLayer(
                        series: series,
                        frame: frame,
                        yRange: yRange,
                        displayedRange: displayedRange,
                        canNavigate: canNavigate,
                        canScrub: !awaiting,
                        crosshairState: crosshairState,
                        viewport: viewportState,
                        onNavigationBegan: { requestOlderIfNeeded() }
                    )
                }

                if awaiting && !pricePoints.isEmpty {
                    ProgressView()
                        .controlSize(.small)
                        .tint(AppColors.textMuted)
                        .allowsHitTesting(false)
                }
            }
            .frame(height: 140)
            .overlay(alignment: .leading) {
                historyEdgeMarker(atFloor: atFloor)
            }
            .overlay(alignment: .bottomTrailing) {
                latestChip(window: window, series: series)
            }
            .overlay(alignment: .top) {
                // Scrub readout: a compact chip floated over the top of the chart, on the side
                // AWAY from the finger. No row is inserted while dragging, so the chart never
                // jumps under the finger, and nothing above the chart is covered.
                ChartScrubHeader(
                    crosshairState: crosshairState,
                    series: series,
                    frame: frame,
                    displayedRange: displayedRange,
                    showsExtendedBadge: { point in drawsExtendedHours && point.isExtendedHours }
                )
            }
            .onGeometryChange(for: CGRect.self) { proxy in
                proxy.frame(in: .global)
            } action: { newFrame in
                // A plain box (never published): BackSwipe reads it to leave a pan that
                // starts on the chart alone.
                touchRegion?.chartFrame = newFrame
            }
            .padding(.horizontal, AppSpacing.lg)

            // X-axis date labels
            if frame.drawRange.count > 1 {
                ChartXAxisLabels(
                    series: series,
                    frame: frame,
                    selectedRange: displayedRange,
                    useIntradayTimeMapping: usesIntradayTimeMapping,
                    sessionWindow: sessionWindow
                )
                    .padding(.horizontal, AppSpacing.lg)
            }

            // Sub-charts (Volume, RSI, MACD, Stoch) — same series, same frame. Filtered by
            // what this asset class can draw honestly (Stoch needs high/low, which CoinGecko
            // rows lack) and by what these bars can draw (`unavailableSubCharts`).
            ForEach(drawnSubCharts) { indicator in
                SubChartCanvas(
                    indicator: indicator,
                    series: series,
                    frame: frame,
                    crosshairState: crosshairState
                )
            }

            // Time range selector + icons
            HStack(spacing: 4) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 2) {
                        // Per-asset, NOT `allCases`: 2Y exists only for crypto, whose
                        // source caps at two years. Every other screen's backend 400s it.
                        ForEach(assetContext.allowedRanges, id: \.rawValue) { range in
                            TimeRangeButton(range: range, isSelected: selectedRange == range) {
                                // The ONLY range write: a tap. The screens' restore and
                                // fallbacks only read (see `ChartSelectionMemory`).
                                ChartSelectionMemory.rememberUserRange(range)
                                if range == selectedRange {
                                    // Re-tapping the current pill returns to its window —
                                    // the way home after scrolling decades back.
                                    jumpToDefaultWindow()
                                    return
                                }
                                withAnimation(.easeInOut(duration: 0.2)) {
                                    selectedRange = range
                                }
                            }
                        }
                    }
                }

                // Interval selector — only show when multiple intervals are available
                if assetContext.allowedIntervals(for: selectedRange).count > 1 {
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
            ChartSettingsSheet(
                chartSettings: chartSettings,
                assetContext: assetContext,
                unavailableSubCharts: unavailableSubCharts,
                unavailableChartTypes: unavailableChartTypes
            )
                .transaction { $0.disablesAnimations = true }
        }
        .onChange(of: selectedRange) {
            crosshairState.clearSelection()
            showIntervalMenu = false
        }
        .onChange(of: CommitToken(stamp: stamp, builtStamp: seriesBox.builtKey?.stamp, hasBars: !series.isEmpty)) {
            commitWindowIfNeeded()
        }
        .onChange(of: series.revision) {
            absorbTailAppend()
            keepScrubOnVisibleBars()
            // A long history builds in the background: decide on the next page only once it
            // is drawn, against bounds that include the bars that just landed.
            requestOlderIfNeeded()
        }
        .onChange(of: viewportState.window) {
            requestOlderIfNeeded()
        }
        .onChange(of: history.revision) {
            requestOlderIfNeeded()
        }
        .onChange(of: history.status) {
            // A dropped page publishes no new bars, only a status: ask again from the edge.
            requestOlderIfNeeded()
        }
        .onAppear {
            commitWindowIfNeeded()
        }
        .onDisappear {
            viewportState.stopFling()
        }
    }

    // MARK: - Window commits and history paging

    private struct CommitToken: Equatable {
        let stamp: ChartDataStamp
        let builtStamp: ChartDataStamp?
        /// Bars arriving after an empty write under the same stamp still commit.
        let hasBars: Bool
    }

    /// New bars for a new stamp (range / interval / user refresh) → the range's default
    /// window, committed once the series built for that stamp is in hand. Live ticks and the
    /// core→full swap keep the stamp, so they never move a window the user is looking at.
    private func commitWindowIfNeeded() {
        let current = stamp
        guard viewportState.appliedStamp != current, seriesBox.builtKey?.stamp == current else { return }
        let series = seriesBox.series
        // Never commit over zero bars: a window clamped against an empty series is the
        // 10-bar minimum, and the bars that land next under the same stamp would keep it.
        guard !series.isEmpty else { return }
        let bounds = bounds(for: series)
        viewportState.bounds = bounds
        viewportState.commit(bounds.defaultWindow(rangeStart: series.rangeStartIndex), stamp: current)
    }

    /// Bars appended at the right under the same stamp (a live tick): a window panned back
    /// into history stays on the bars it shows; one pinned to the newest bar follows.
    private func absorbTailAppend() {
        let series = seriesBox.series
        let previous = seriesBox.previous
        guard viewportState.appliedStamp == stamp, seriesBox.builtKey?.stamp == stamp,
              !previous.isEmpty, !series.isEmpty else { return }
        let appended = Self.barsAddedAtTail(from: previous, to: series, endOffset: viewportState.window.endOffset)
        viewportState.bounds = bounds(for: series)
        viewportState.absorbAppend(appended)
        viewportState.reclamp()
    }

    /// A refresh can slide the bar under a held scrub past the edge (a window pinned to the
    /// newest bar follows the new bar): move the selection to the bar now at that edge, as a
    /// fresh snap would. Run AFTER `absorbTailAppend`, against the window the chart settles on.
    private func keepScrubOnVisibleBars() {
        let series = seriesBox.series
        guard crosshairState.isDragging, let i = crosshairState.index(in: series) else { return }
        let bounds = bounds(for: series)
        let frame = chartFrame(series: series, bounds: bounds, window: effectiveWindow(series: series, bounds: bounds))
        let visible = frame.visibleRange
        guard !visible.isEmpty, !visible.contains(i) else { return }
        let j = min(max(i, visible.lowerBound), visible.upperBound - 1)
        guard j >= 0, j < series.count else { return }
        crosshairState.selectedDate = series.dates[j]
        crosshairState.selectedFromEnd = series.count - 1 - j
        crosshairState.selectedIndex = j
    }

    /// How far the newest bar moved right between two series, measured on a bar both share
    /// LEFT of the window's right edge. The newest bar's own date is no anchor: crypto's live
    /// point and a weekly/monthly bucket are RE-DATED in place on a refresh, which counted as
    /// one appended bar and walked a panned-back window one bar older each time. Bars
    /// prepended at the left do not move the anchor's distance from the end.
    static func barsAddedAtTail(from old: ChartSeries, to new: ChartSeries, endOffset: Double) -> Int {
        guard !old.isEmpty, !new.isEmpty else { return 0 }
        let anchor = old.count - 1 - max(1, Int(endOffset.rounded(.up)))
        if anchor >= 0 {
            let date = old.dates[anchor]
            let j = new.lowerBound(date)
            if j < new.count, new.dates[j] == date {
                return (new.count - 1 - j) - (old.count - 1 - anchor)
            }
        }
        // The anchor bar is gone (the payload was replaced around it): fall back to the bars
        // dated after the old newest bar.
        guard let last = old.dates.last else { return 0 }
        return new.count - new.upperBound(last)
    }

    /// Page in older history once the USER has moved the window within one screen of the
    /// floor — so a left pan starts the fetch at once, and the default window a range opens
    /// on never does.
    private func requestOlderIfNeeded() {
        guard let onNeedOlderBars, !usesIntradayTimeMapping, !historyExhausted,
              history.status != .loading, viewportState.userMovedSinceCommit,
              !seriesBox.isBuilding else { return }
        let bounds = viewportState.bounds
        let window = viewportState.window
        let distance = bounds.slotLeft(window) - Double(bounds.floorIndex)
        if distance < max(window.visibleCount, 60) {
            onNeedOlderBars(false)
        }
    }

    private func jumpToDefaultWindow() {
        let series = seriesBox.series
        let bounds = bounds(for: series)
        viewportState.bounds = bounds
        viewportState.stopFling()
        viewportState.set(bounds.defaultWindow(rangeStart: series.rangeStartIndex))
    }

    // MARK: - Edge marker and Latest chip

    @ViewBuilder
    private func historyEdgeMarker(atFloor: Bool) -> some View {
        if atFloor && history.status == .loading {
            // Shown only when the user OUTRAN the page (the window sits on the floor).
            ProgressView()
                .controlSize(.small)
                .tint(AppColors.textMuted)
                .padding(AppSpacing.xs)
                .background(Circle().fill(AppColors.background))
                .padding(.leading, AppSpacing.xs)
                .allowsHitTesting(false)
                .accessibilityLabel("Loading older prices")
        } else if atFloor && history.status == .failed {
            Button {
                onNeedOlderBars?(true)
            } label: {
                Text("Couldn't load older prices · Retry")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .padding(.horizontal, AppSpacing.sm)
                    .padding(.vertical, AppSpacing.xs)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.small)
                            .fill(AppColors.cardBackgroundLight)
                    )
            }
            .buttonStyle(.plain)
            .padding(.leading, AppSpacing.xs)
            .accessibilityLabel("Couldn't load older prices. Retry")
        }
    }

    @ViewBuilder
    private func latestChip(window: ChartWindow, series: ChartSeries) -> some View {
        if !usesIntradayTimeMapping, viewportState.appliedStamp == stamp, window.endOffset > 0.5 {
            Button {
                viewportState.stopFling()
                viewportState.set(ChartWindow(endOffset: 0, visibleCount: window.visibleCount))
            } label: {
                Image(systemName: "chevron.right.2")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(AppColors.textSecondary)
                    .frame(width: 28, height: 22)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.small)
                            .fill(AppColors.cardBackgroundLight)
                    )
                    .hitSlop(reaching: HitSlop.minimumTarget, maxInset: AppSpacing.xs)
            }
            .buttonStyle(.plain)
            .padding(AppSpacing.xs)
            .accessibilityLabel("Jump to latest")
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
            let intervals = assetContext.allowedIntervals(for: selectedRange)
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
                .glassPanel(cornerRadius: AppCornerRadius.large)
                .offset(x: x, y: y)
            }
            .transition(.identity)
        }
    }

    private func intervalRow(_ interval: ChartInterval) -> some View {
        Button {
            // The ONLY interval write: a tap, remembered for the range it was picked on.
            ChartSelectionMemory.rememberUserInterval(interval, for: selectedRange)
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

// MARK: - Scrub header

/// The date, price, change and volume of the bar under the crosshair, as one compact chip at
/// the top of the chart, on the side away from the finger. Observes `CrosshairState` itself,
/// so a scrub step re-renders this chip — not the chart.
private struct ChartScrubHeader: View {
    @ObservedObject var crosshairState: CrosshairState
    let series: ChartSeries
    let frame: ChartFrame
    let displayedRange: ChartTimeRange
    let showsExtendedBadge: (StockPricePoint) -> Bool

    var body: some View {
        if crosshairState.isDragging, let i = crosshairState.index(in: series) {
            let point = StockPricePoint(
                date: series.dates[i],
                close: series.close[i],
                open: series.open[i],
                high: series.high[i],
                low: series.low[i],
                volume: series.volume[i]
            )
            // The finger's side of the chart — where the finger IS (on 1D, past the last bar
            // every x snaps to that bar), else the bar's x: put the chip on the other side.
            let fingerOnLeft = crosshairState.fingerOnLeft ?? (frame.x(i, width: 1) < 0.5)
            HStack(spacing: 0) {
                if fingerOnLeft { Spacer(minLength: 0) }
                chip(point)
                if !fingerOnLeft { Spacer(minLength: 0) }
            }
            .padding(.top, 2)
            .allowsHitTesting(false)
        }
    }

    private func chip(_ point: StockPricePoint) -> some View {
        HStack(spacing: AppSpacing.xs) {
            Text(displayedRange.formatDateForCrosshair(point.date))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Text(formatPrice(point.close))
                .font(AppTypography.labelSmallEmphasis)
                .foregroundColor(AppColors.textPrimary)

            if let open = point.open {
                let change = point.close - open
                let pct = open != 0 ? (change / open) * 100 : 0
                let sign = change >= 0 ? "+" : ""
                Text("\(sign)\(String(format: "%.2f", pct))%")
                    .font(AppTypography.caption)
                    .foregroundColor(change >= 0 ? AppColors.gain : AppColors.loss)
            }

            if let vol = point.volume, vol > 0 {
                Text("Vol \(formatVolume(vol))")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            if showsExtendedBadge(point) {
                Text("EXT")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .lineLimit(1)
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, 3)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                .fill(AppColors.cardBackground)
        )
    }

    private func formatPrice(_ price: Double) -> String {
        price >= 1 ? String(format: "$%.2f", price) : String(format: "$%.6f", price)
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
}

// MARK: - Series memo

/// Memo for the chart's `ChartSeries`. A plain (non-observable, NON-generic — a generic
/// class needs `nonisolated deinit` until Swift 6.4) class held in @State: reading and
/// refilling it during `body` publishes nothing.
///
/// Small series are built synchronously, so the frame after new data is never stale. A long
/// merged history (thousands of older bars) is built OFF the main actor and swapped in when
/// ready; the previous series keeps drawing meanwhile, and the right-anchored window means
/// the swap does not move the view.
final class ChartSeriesBox {
    struct Key: Equatable {
        let historyRevision: Int
        let olderCount: Int
        let payloadCount: Int
        let payloadFirst: String?
        let payloadLast: String?
        let lastClose: Double?
        let lastHigh: Double?
        let lastLow: Double?
        let lastVolume: Double?
        let cutoff: String?
        let stamp: ChartDataStamp
        /// The loader's `reachedStart` for these bars — the floor is read from the BUILT key.
        let reachedStart: Bool
    }

    private(set) var series: ChartSeries = .empty {
        didSet { previous = oldValue }
    }
    /// The series drawn before the current one — what a tail append is measured against.
    private(set) var previous: ChartSeries = .empty
    private(set) var builtKey: Key?
    private var pendingKey: Key?
    private var task: Task<Void, Never>?
    private var nextRevision = 1

    private var extendedMemo: (count: Int, first: String?, last: String?, value: Bool)?

    /// `compute` runs once per payload (count + first/last date identify it).
    func memoizedExtended(for payload: [StockPricePoint], compute: () -> Bool) -> Bool {
        let first = payload.first?.date, last = payload.last?.date
        if let m = extendedMemo, m.count == payload.count, m.first == first, m.last == last { return m.value }
        let value = compute()
        extendedMemo = (payload.count, first, last, value)
        return value
    }

    /// Up to this many bars build inline (~a few ms); more build in the background.
    private static let synchronousLimit = 4_000

    var isBuilding: Bool { pendingKey != nil }

    func series(
        for key: Key,
        older: [StockPricePoint],
        payload: [StockPricePoint],
        onReady: @escaping () -> Void
    ) -> ChartSeries {
        if key == builtKey || key == pendingKey { return series }
        let input = ChartSeriesInput(older: older, payload: payload, rangeStartCutoff: key.cutoff, revision: nextRevision)
        nextRevision &+= 1
        task?.cancel()
        if builtKey == nil || older.count + payload.count <= Self.synchronousLimit {
            series = ChartSeries.build(input)
            builtKey = key
            pendingKey = nil
            return series
        }
        pendingKey = key
        task = Task { [weak self] in
            let built = await Task.detached(priority: .userInitiated) { ChartSeries.build(input) }.value
            guard let self, !Task.isCancelled, self.pendingKey == key else { return }
            self.series = built
            self.builtKey = key
            self.pendingKey = nil
            onReady()
        }
        return series
    }

    // MARK: Range-start cutoff

    private var cutoffKey: (ChartTimeRange, Bool, String)?
    private var cutoffValue: String?

    /// The default window's left edge for `range`, measured back from the NEWEST bar (not
    /// the wall clock — a halted ticker still opens on its last N days), in the bars' own
    /// string format. nil for ALL.
    func cutoff(for range: ChartTimeRange, crypto: Bool, lastDate: String?) -> String? {
        guard let lastDate else { return nil }
        if let k = cutoffKey, k.0 == range, k.1 == crypto, k.2 == lastDate { return cutoffValue }
        let value = Self.computeCutoff(range: range, crypto: crypto, lastDate: lastDate)
        cutoffKey = (range, crypto, lastDate)
        cutoffValue = value
        return value
    }

    private static func computeCutoff(range: ChartTimeRange, crypto: Bool, lastDate: String) -> String? {
        let intraday = ChartDateFormatters.isIntraday(lastDate)
        let formatter = intraday ? Self.etDateTime : Self.utcDate
        guard let reference = formatter.date(from: intraday ? String(lastDate.prefix(19)) : String(lastDate.prefix(10))) else {
            return nil
        }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = formatter.timeZone
        // Crypto trades 24/7 — exact 1D/1W windows. Stocks need extra calendar days to
        // cover weekends/holidays.
        let shifted: Date?
        switch range {
        case .oneDay:      shifted = calendar.date(byAdding: .day, value: crypto ? -1 : -3, to: reference)
        case .oneWeek:     shifted = calendar.date(byAdding: .day, value: crypto ? -7 : -10, to: reference)
        case .threeMonths: shifted = calendar.date(byAdding: .month, value: -3, to: reference)
        case .sixMonths:   shifted = calendar.date(byAdding: .month, value: -6, to: reference)
        case .oneYear:     shifted = calendar.date(byAdding: .year, value: -1, to: reference)
        case .twoYears:    shifted = calendar.date(byAdding: .year, value: -2, to: reference)
        case .fiveYears:   shifted = calendar.date(byAdding: .year, value: -5, to: reference)
        case .all:         shifted = nil
        }
        return shifted.map { formatter.string(from: $0) }
    }

    private static let utcDate: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    /// Intraday bars are stamped in ET wall clock by the backend.
    private static let etDateTime: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = TimeZone(identifier: "America/New_York")
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f
    }()
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selectedRange: ChartTimeRange = .threeMonths
        @StateObject private var chartSettings = ChartSettings()

        var body: some View {
            TickerChartView(
                pricePoints: Self.samplePoints(),
                isPositive: true,
                selectedRange: $selectedRange,
                chartSettings: chartSettings,
                assetContext: .stock,
                chartDataVersion: 0
            )
            .padding(.vertical)
            .background(AppColors.background)
        }

        /// Built in a loop — never one literal of initializer calls (see
        /// `test_ios_no_giant_literals.py`).
        static func samplePoints() -> [StockPricePoint] {
            var points: [StockPricePoint] = []
            var price = 165.0
            var day = Calendar(identifier: .gregorian).date(from: DateComponents(year: 2025, month: 1, day: 2)) ?? Date()
            let f = DateFormatter()
            f.locale = Locale(identifier: "en_US_POSIX")
            f.dateFormat = "yyyy-MM-dd"
            for i in 0..<320 {
                let open = price
                price += sin(Double(i) / 9) * 1.8 + 0.12
                points.append(StockPricePoint(
                    date: f.string(from: day), close: price, open: open,
                    high: max(open, price) + 1.2, low: min(open, price) - 1.2, volume: 40_000_000
                ))
                day = day.addingTimeInterval(86_400)
            }
            return points
        }
    }

    return PreviewWrapper()
}
