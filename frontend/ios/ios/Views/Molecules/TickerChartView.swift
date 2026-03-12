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

    @State private var showSettingsSheet = false
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

        switch selectedRange {
        case .oneDay:       displayStart = calendar.date(byAdding: .day, value: -3, to: now)
        case .oneWeek:      displayStart = calendar.date(byAdding: .day, value: -10, to: now)
        case .threeMonths:  displayStart = calendar.date(byAdding: .month, value: -3, to: now)
        case .sixMonths:    displayStart = calendar.date(byAdding: .month, value: -6, to: now)
        case .oneYear:      displayStart = calendar.date(byAdding: .year, value: -1, to: now)
        case .fiveYears:    displayStart = calendar.date(byAdding: .year, value: -5, to: now)
        case .all:          displayStart = nil
        }

        guard let start = displayStart else { return 0 }

        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        let startStr = formatter.string(from: start)

        for (index, point) in pricePoints.enumerated() {
            let dateStr = String(point.date.prefix(10))
            if dateStr >= startStr {
                return index
            }
        }
        return 0
    }

    /// Slice of price points visible in the current viewport
    private var visiblePoints: [StockPricePoint] {
        guard !pricePoints.isEmpty else { return [] }
        let start = max(0, min(viewportState.visibleStart, pricePoints.count - 1))
        let end = max(start, min(pricePoints.count - 1, viewportState.visibleEnd))
        return Array(pricePoints[start...end])
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

                    if chartSettings.showExtendedHours && point.isExtendedHours {
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
                    showExtendedHours: chartSettings.showExtendedHours,
                    lookbackCloses: overlayLookbackCloses
                )

                ChartCrosshairGesture(
                    pricePoints: visiblePoints,
                    selectedRange: selectedRange,
                    crosshairState: crosshairState,
                    viewportState: viewportState
                )
            }
            .frame(height: 140)
            .padding(.horizontal, AppSpacing.lg)

            // X-axis date labels
            if visiblePoints.count > 1 {
                ChartXAxisLabels(pricePoints: visiblePoints, selectedRange: selectedRange)
                    .padding(.horizontal, AppSpacing.lg)
            }

            // Sub-charts (Volume, RSI, MACD, Stoch)
            // Pass full data for indicator warm-up, visible slice for rendering
            ForEach(chartSettings.activeSubCharts) { indicator in
                SubChartCanvas(
                    indicator: indicator,
                    pricePoints: visiblePoints,
                    allPricePoints: pricePoints,
                    visibleStartIndex: viewportState.visibleStart
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
                    Menu {
                        ForEach(selectedRange.allowedIntervals) { interval in
                            Button {
                                var transaction = Transaction()
                                transaction.disablesAnimations = true
                                withTransaction(transaction) {
                                    chartSettings.selectedInterval = interval
                                }
                            } label: {
                                HStack {
                                    Text(interval.displayName)
                                    if chartSettings.selectedInterval == interval {
                                        Image(systemName: "checkmark")
                                    }
                                }
                            }
                        }
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
                }

                // Extended hours toggle — visible for intraday intervals on supported assets
                if assetContext.supportsExtendedHours && chartSettings.selectedInterval.isIntraday {
                    Button {
                        chartSettings.showExtendedHours.toggle()
                    } label: {
                        HStack(spacing: 2) {
                            Image(systemName: chartSettings.showExtendedHours ? "sun.max.fill" : "sun.max")
                                .font(.system(size: 10))
                            Text("EXT")
                                .font(AppTypography.labelSmall)
                        }
                        .fixedSize()
                        .foregroundColor(chartSettings.showExtendedHours ? AppColors.textPrimary : AppColors.textMuted)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 4)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                .fill(chartSettings.showExtendedHours
                                      ? AppColors.cardBackgroundLight
                                      : AppColors.cardBackgroundLight.opacity(0.5))
                        )
                    }
                    .buttonStyle(PlainButtonStyle())
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
        .sheet(isPresented: $showSettingsSheet) {
            ChartSettingsSheet(chartSettings: chartSettings, assetContext: assetContext)
                .transaction { $0.disablesAnimations = true }
        }
        .onChange(of: selectedRange) {
            crosshairState.selectedIndex = nil
            crosshairState.isDragging = false
        }
        .onChange(of: chartDataVersion) {
            // Reset viewport when new data is loaded, offsetting past warm-up data
            viewportState.reset(totalCount: pricePoints.count, displayStart: warmupCount)
        }
        .onChange(of: pricePoints.count) {
            // Fallback: also reset when data count changes (covers views without chartDataVersion)
            viewportState.reset(totalCount: pricePoints.count, displayStart: warmupCount)
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
