//
//  ChatMarketOverviewWidget.swift
//  ios
//
//  Molecule: Rich media market overview widget rendered inline in chat.
//  Shows valuation gauge, sector performance bars, and macro signal badges.
//

import SwiftUI

struct ChatMarketOverviewWidget: View {
    let data: MarketOverviewWidgetData

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // ── Header ───────────────────────────────────────
            headerSection
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.lg)

            // ── Valuation Metrics ─────────────────────────────
            valuationMetrics
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.lg)

            // ── Sector Performance ───────────────────────────
            sectorSection
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.lg)

            // ── Macro Indicators ─────────────────────────────
            if !data.macroIndicators.isEmpty {
                macroSection
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.lg)
            }

            Spacer().frame(height: AppSpacing.lg)
        }
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    // MARK: - Header

    private var headerSection: some View {
        HStack(spacing: AppSpacing.sm) {
            // Market badge
            Text("Market")
                .font(AppTypography.labelEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xxs)
                .background(AppColors.primaryBlue.opacity(0.2))
                .cornerRadius(AppCornerRadius.small)

            Text("Market Overview")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            // Valuation level badge
            Text(data.valuationLevel)
                .font(AppTypography.captionSmall)
                .foregroundColor(valuationColor)
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xxs)
                .background(valuationColor.opacity(0.15))
                .cornerRadius(AppCornerRadius.small)
        }
    }

    // MARK: - Valuation Metrics

    private var valuationMetrics: some View {
        HStack(spacing: 0) {
            metricPill(label: "P/E (TTM)", value: String(format: "%.1fx", data.peRatio))
            Spacer()
            metricPill(label: "Fwd P/E", value: String(format: "%.1fx", data.forwardPe))
            Spacer()
            metricPill(label: "Yield", value: String(format: "%.1f%%", data.earningsYield))
            Spacer()
            metricPill(label: "10Y Avg", value: String(format: "%.0fx", data.historicalAvgPe))
        }
    }

    private func metricPill(label: String, value: String) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(AppTypography.labelSmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
            Text(label)
                .font(AppTypography.captionSmall)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, AppSpacing.sm)
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
    }

    // MARK: - Sector Performance

    private var sectorSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack {
                Text("Sectors")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                HStack(spacing: AppSpacing.xs) {
                    Text("\(data.advancing) up")
                        .font(AppTypography.captionSmall)
                        .foregroundColor(AppColors.bullish)
                    Text("\u{00B7}")
                        .font(AppTypography.captionSmall)
                        .foregroundColor(AppColors.textMuted)
                    Text("\(data.declining) down")
                        .font(AppTypography.captionSmall)
                        .foregroundColor(AppColors.bearish)
                }
            }

            // Sector bars
            ForEach(data.sectors) { sector in
                HStack(spacing: AppSpacing.sm) {
                    Text(sector.sector)
                        .font(AppTypography.captionSmall)
                        .foregroundColor(AppColors.textSecondary)
                        .frame(width: 110, alignment: .leading)
                        .lineLimit(1)

                    // Performance bar
                    GeometryReader { geo in
                        // Scale bars against the largest magnitude across ALL sectors, not just
                        // first/last — the list isn't guaranteed sorted, so a mid-list outlier would
                        // otherwise make its ratio exceed 1 and push the bar past the container
                        // (max() clamps only the low end). The min() is a belt-and-suspenders cap.
                        let maxPct = max(data.sectors.map { abs($0.changePercent) }.max() ?? 0.1, 0.1)
                        let barWidth = max(2, min(geo.size.width,
                                                  geo.size.width * CGFloat(abs(sector.changePercent) / maxPct)))

                        HStack(spacing: 0) {
                            if sector.isPositive {
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(AppColors.bullish)
                                    .frame(width: barWidth, height: 10)
                                Spacer()
                            } else {
                                Spacer()
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(AppColors.bearish)
                                    .frame(width: barWidth, height: 10)
                            }
                        }
                    }
                    .frame(height: 10)

                    Text(sector.formattedChange)
                        .font(AppTypography.captionSmall)
                        .foregroundColor(sector.isPositive ? AppColors.bullish : AppColors.bearish)
                        .frame(width: 50, alignment: .trailing)
                }
            }
        }
    }

    // MARK: - Macro Indicators

    private var macroSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Economic Outlook")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)

            // Horizontal chips
            let columns = [
                GridItem(.flexible(), spacing: AppSpacing.sm),
                GridItem(.flexible(), spacing: AppSpacing.sm)
            ]
            LazyVGrid(columns: columns, spacing: AppSpacing.sm) {
                ForEach(data.macroIndicators) { indicator in
                    HStack(spacing: AppSpacing.xs) {
                        Circle()
                            .fill(signalColor(indicator.signal))
                            .frame(width: 6, height: 6)
                        Text(indicator.title)
                            .font(AppTypography.captionSmall)
                            .foregroundColor(AppColors.textPrimary)
                            .lineLimit(1)
                    }
                    .padding(.horizontal, AppSpacing.sm)
                    .padding(.vertical, AppSpacing.xs)
                    .background(signalColor(indicator.signal).opacity(0.1))
                    .cornerRadius(AppCornerRadius.small)
                }
            }
        }
    }

    // MARK: - Helpers

    private var valuationColor: Color {
        switch data.valuationLevel.lowercased() {
        case "bargain": return AppColors.bullish
        case "fair value": return AppColors.neutral
        case "expensive": return .orange
        case "overheated": return AppColors.bearish
        default: return AppColors.textMuted
        }
    }

    private func signalColor(_ signal: String) -> Color {
        switch signal.lowercased() {
        case "positive": return AppColors.bullish
        case "cautious": return AppColors.bearish
        default: return AppColors.neutral
        }
    }
}

#Preview {
    ScrollView {
        ChatMarketOverviewWidget(data: MarketOverviewWidgetData(
            widgetType: "market_overview",
            peRatio: 21.4,
            forwardPe: 18.2,
            valuationLevel: "Fair Value",
            earningsYield: 4.68,
            historicalAvgPe: 21.0,
            sectors: [
                .init(sector: "Real Estate", changePercent: 1.6),
                .init(sector: "Technology", changePercent: 0.8),
                .init(sector: "Utilities", changePercent: 0.5),
                .init(sector: "Energy", changePercent: 0.5),
                .init(sector: "Consumer Defensive", changePercent: 0.5),
                .init(sector: "Financial Services", changePercent: 0.2),
                .init(sector: "Basic Materials", changePercent: -0.1),
                .init(sector: "Industrials", changePercent: -0.4),
                .init(sector: "Healthcare", changePercent: -0.6),
                .init(sector: "Consumer Cyclical", changePercent: -1.5),
            ],
            advancing: 6,
            declining: 4,
            macroIndicators: [
                .init(title: "GDP Growth", signal: "positive"),
                .init(title: "Inflation", signal: "neutral"),
                .init(title: "Labor Market", signal: "positive"),
                .init(title: "Trade Policy", signal: "cautious"),
            ]
        ))
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
