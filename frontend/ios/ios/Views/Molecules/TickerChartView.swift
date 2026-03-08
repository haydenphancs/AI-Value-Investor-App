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

    @State private var showSettingsSheet = false
    @StateObject private var crosshairState = CrosshairState()

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            // Scrubbing price overlay (shows selected point's price when dragging)
            if crosshairState.isDragging, let idx = crosshairState.selectedIndex,
               idx >= 0, idx < pricePoints.count {
                let point = pricePoints[idx]
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

                    Spacer()
                }
                .padding(.horizontal, AppSpacing.lg)
                .transition(.opacity)
            }

            // Main price chart with crosshair gesture
            ZStack {
                MainChartCanvas(
                    pricePoints: pricePoints,
                    isPositive: isPositive,
                    chartType: chartSettings.chartType,
                    overlays: chartSettings.activeOverlays
                )

                ChartCrosshairGesture(
                    pricePoints: pricePoints,
                    selectedRange: selectedRange,
                    crosshairState: crosshairState
                )
            }
            .frame(height: 140)
            .padding(.horizontal, AppSpacing.lg)

            // X-axis date labels
            if pricePoints.count > 1 {
                ChartXAxisLabels(pricePoints: pricePoints, selectedRange: selectedRange)
                    .padding(.horizontal, AppSpacing.lg)
            }

            // Sub-charts (Volume, RSI, MACD, Stoch)
            ForEach(chartSettings.activeSubCharts) { indicator in
                SubChartCanvas(indicator: indicator, pricePoints: pricePoints)
            }

            // Time range selector + icons
            HStack(spacing: 2) {
                ForEach(ChartTimeRange.allCases, id: \.rawValue) { range in
                    TimeRangeButton(range: range, isSelected: selectedRange == range) {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            selectedRange = range
                        }
                    }
                }

                Spacer()

                // Chart type selector (Menu)
                Menu {
                    ForEach(ChartType.allCases) { type in
                        Button {
                            var transaction = Transaction()
                            transaction.disablesAnimations = true
                            withTransaction(transaction) {
                                chartSettings.chartType = type
                            }
                        } label: {
                            Label(type.rawValue, systemImage: type.iconName)
                        }
                    }
                } label: {
                    Image(systemName: chartSettings.chartType.iconName)
                        .font(AppTypography.iconDefault)
                        .foregroundColor(AppColors.textMuted)
                        .padding(.leading, 4)
                        .padding(.trailing, 8)
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
        .onChange(of: selectedRange) { _ in
            // Clear crosshair when range changes
            crosshairState.selectedIndex = nil
            crosshairState.isDragging = false
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
                assetContext: .stock
            )
            .padding(.vertical)
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
