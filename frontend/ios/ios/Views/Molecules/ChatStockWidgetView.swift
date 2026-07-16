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
            if !widget.historicalData.isEmpty {
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
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
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

            // Market status: green "Live" only while the US session is open, else a muted "Closed".
            Circle()
                .fill((widget.isMarketOpen ?? false) ? AppColors.bullish : AppColors.textMuted)
                .frame(width: 6, height: 6)
            Text((widget.isMarketOpen ?? false) ? "Live" : "Closed")
                .font(AppTypography.captionSmall)
                .foregroundColor(AppColors.textMuted)
        }
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
        Chart {
            // Key on the always-unique enumeration offset (also the x-value), NOT the date string:
            // null-coerced ("") or duplicate FMP dates would otherwise collide the ForEach id and
            // drop/collapse chart marks ("undefined results").
            ForEach(Array(widget.historicalData.enumerated()), id: \.offset) { index, point in
                LineMark(
                    x: .value("Day", index),
                    y: .value("Price", point.close)
                )
                .foregroundStyle(widget.isPositive ? AppColors.bullish : AppColors.bearish)
                .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.catmullRom)

                AreaMark(
                    x: .value("Day", index),
                    y: .value("Price", point.close)
                )
                .foregroundStyle(
                    LinearGradient(
                        colors: [
                            (widget.isPositive ? AppColors.bullish : AppColors.bearish).opacity(0.3),
                            (widget.isPositive ? AppColors.bullish : AppColors.bearish).opacity(0.0)
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .interpolationMethod(.catmullRom)
            }
        }
        .chartXAxis(.hidden)
        .chartYAxis {
            AxisMarks(position: .trailing, values: .automatic(desiredCount: 4)) { value in
                AxisValueLabel {
                    if let price = value.as(Double.self) {
                        Text(String(format: "$%.0f", price))
                            .font(AppTypography.captionSmall)
                            .foregroundColor(AppColors.textMuted)
                    }
                }
                AxisGridLine()
                    .foregroundStyle(AppColors.textMuted.opacity(0.2))
            }
        }
        .chartYScale(domain: chartYDomain)
        .frame(height: 140)
    }

    private var chartYDomain: ClosedRange<Double> {
        let closes = widget.chartCloses
        guard let minVal = closes.min(), let maxVal = closes.max() else {
            return 0...1
        }
        // A flat series (single point / all-equal closes → min == max) makes the 10% padding 0, so
        // the domain would collapse to X...X and Charts' (v-min)/(max-min) normalization divides by
        // zero → a degenerate/invisible line. Fall back to a nominal pad so the flat line renders
        // centered.
        let padding = maxVal > minVal ? (maxVal - minVal) * 0.1 : max(abs(maxVal) * 0.01, 1)
        return (minVal - padding)...(maxVal + padding)
    }

    // MARK: - Chart Date Range
    private var chartDateRange: some View {
        HStack {
            if let first = widget.historicalData.first {
                Text(formatDateLabel(first.date))
                    .font(AppTypography.captionSmall)
                    .foregroundColor(AppColors.textMuted)
            }

            Spacer()

            if let last = widget.historicalData.last {
                Text(formatDateLabel(last.date))
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
    private func formatDateLabel(_ dateStr: String) -> String {
        let inputFormatter = DateFormatter()
        inputFormatter.dateFormat = "yyyy-MM-dd"
        guard let date = inputFormatter.date(from: dateStr) else { return dateStr }

        let outputFormatter = DateFormatter()
        outputFormatter.dateFormat = "MMM d"
        return outputFormatter.string(from: date)
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
        ChatStockWidgetView(widget: StockChartWidgetData.sample)
            .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
