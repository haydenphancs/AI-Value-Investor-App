//
//  ChartSettingsSheet.swift
//  ios
//
//  Settings sheet for chart indicators and extended hours
//

import SwiftUI

struct ChartSettingsSheet: View {
    @ObservedObject var chartSettings: ChartSettings
    let assetContext: ChartAssetContext
    /// Panes the chart's bars cannot draw (`TickerChartView.unavailableSubCharts` — e.g.
    /// Stoch and Volume on a close-only FRED commodity). Shown disabled, still reflecting
    /// the saved choice, so the toggle neither looks broken nor rewrites the preference.
    var unavailableSubCharts: Set<TechnicalIndicatorType> = []
    /// Chart types the bars cannot draw (`TickerChartView.unavailableChartTypes` — Candle and
    /// Bar on a close-only series). Shown disabled; the saved type is left alone.
    var unavailableChartTypes: Set<ChartType> = []
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {

                    // Chart type section
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Text("Chart Type")
                            .font(AppTypography.headingSmall)
                            .foregroundColor(AppColors.textPrimary)

                        // The type the CANVAS is drawing, not the persisted preference: a
                        // Candle/Bar choice made on a stock is coerced to Line on a source
                        // with no OHLC (crypto) — see `TickerChartView` — so highlighting
                        // the raw preference left NO row selected while a line was drawn. The
                        // same coercion applies to a close-only series (WTI, Henry Hub).
                        let effectiveChartType = assetContext.allowedChartTypes.contains(chartSettings.chartType)
                            && !unavailableChartTypes.contains(chartSettings.chartType)
                            ? chartSettings.chartType : .line
                        HStack(spacing: AppSpacing.sm) {
                            ForEach(assetContext.allowedChartTypes) { type in
                                Button {
                                    // Re-tapping the highlighted type writes nothing. On crypto a
                                    // saved Candle is SHOWN as Line; storing that tap would replace
                                    // the user's Candle — and, with every live chart now synced, flip
                                    // each stock screen in the stack to Line as well.
                                    guard type != effectiveChartType else { return }
                                    var transaction = Transaction()
                                    transaction.disablesAnimations = true
                                    withTransaction(transaction) {
                                        chartSettings.chartType = type
                                    }
                                } label: {
                                    Text(type.rawValue)
                                        .font(AppTypography.bodySmallEmphasis)
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, AppSpacing.sm)
                                        .background(
                                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                                .fill(effectiveChartType == type
                                                      ? AppColors.primaryBlue.opacity(0.15)
                                                      : AppColors.cardBackgroundLight.opacity(0.5))
                                        )
                                        .overlay(
                                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                                .stroke(effectiveChartType == type
                                                        ? AppColors.primaryBlue
                                                        : Color.clear, lineWidth: 1)
                                        )
                                        .foregroundColor(effectiveChartType == type
                                                         ? AppColors.primaryBlue
                                                         : AppColors.textMuted)
                                }
                                .buttonStyle(PlainButtonStyle())
                                .disabled(unavailableChartTypes.contains(type))
                                .opacity(unavailableChartTypes.contains(type) ? 0.4 : 1)
                            }
                        }

                        if !unavailableChartTypes.isEmpty {
                            Text("Candle and Bar need open, high and low prices, which this asset's data doesn't have.")
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }

                    Divider()

                    // Overlays section
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Text("Overlays")
                            .font(AppTypography.headingSmall)
                            .foregroundColor(AppColors.textPrimary)

                        ForEach(TechnicalIndicatorType.allCases.filter(\.isOverlay)) { indicator in
                            Toggle(isOn: indicatorBinding(for: indicator)) {
                                HStack(spacing: AppSpacing.sm) {
                                    Circle()
                                        .fill(indicator.defaultColor)
                                        .frame(width: 8, height: 8)
                                    Text(indicator.rawValue)
                                        .font(AppTypography.body)
                                        .foregroundColor(AppColors.textPrimary)
                                }
                            }
                            .tint(AppColors.primaryBlue)
                        }
                    }

                    Divider()

                    // Sub-charts section
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Text("Sub-charts")
                            .font(AppTypography.headingSmall)
                            .foregroundColor(AppColors.textPrimary)

                        ForEach(assetContext.allowedSubCharts) { indicator in
                            let isUnavailable = unavailableSubCharts.contains(indicator)
                            Toggle(isOn: indicatorBinding(for: indicator)) {
                                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                    HStack(spacing: AppSpacing.sm) {
                                        Circle()
                                            .fill(indicator.defaultColor)
                                            .frame(width: 8, height: 8)
                                        Text(indicator.rawValue)
                                            .font(AppTypography.body)
                                            .foregroundColor(AppColors.textPrimary)
                                    }
                                    if isUnavailable {
                                        Text("Not available for this asset's data")
                                            .font(AppTypography.caption)
                                            .foregroundColor(AppColors.textMuted)
                                    }
                                }
                            }
                            .tint(AppColors.primaryBlue)
                            .disabled(isUnavailable)
                        }
                    }

                    // Extended Hours — only relevant for intraday intervals
                    if assetContext.supportsExtendedHours && chartSettings.selectedInterval.isIntraday {
                        Divider()

                        Toggle(isOn: $chartSettings.showExtendedHours) {
                            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                Text("Extended Hours")
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.textPrimary)
                                Text("Show pre-market and after-hours data")
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textMuted)
                            }
                        }
                        .tint(AppColors.primaryBlue)
                    }

                    // Earnings Dates — stocks only. An ETF has no earnings feed and its
                    // screen passes no `chartEventDates`, so the toggle was inert there.
                    if assetContext == .stock {
                        Divider()

                        Toggle(isOn: $chartSettings.showEarningsDates) {
                            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                Text("Earnings Dates")
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.textPrimary)
                                Text("Show E markers on chart")
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textMuted)
                            }
                        }
                        .tint(AppColors.primaryBlue)
                    }
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Chart Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func indicatorBinding(for indicator: TechnicalIndicatorType) -> Binding<Bool> {
        Binding(
            get: { chartSettings.enabledIndicators.contains(indicator) },
            set: { enabled in
                var transaction = Transaction()
                transaction.disablesAnimations = true
                withTransaction(transaction) {
                    if enabled {
                        chartSettings.enabledIndicators.insert(indicator)
                    } else {
                        chartSettings.enabledIndicators.remove(indicator)
                    }
                }
            }
        )
    }
}
