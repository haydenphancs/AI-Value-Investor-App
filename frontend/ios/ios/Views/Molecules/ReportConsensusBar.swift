//
//  ReportConsensusBar.swift
//  ios
//
//  Molecule: Wall Street consensus rating bar with price targets
//

import SwiftUI

struct ReportConsensusBar: View {
    let consensus: ReportWallStreetConsensus

    private var pricePosition: Double {
        guard consensus.highTarget > consensus.lowTarget else { return 0.5 }
        return (consensus.currentPrice - consensus.lowTarget) / (consensus.highTarget - consensus.lowTarget)
    }

    private var minPrice: Double {
        let targetPrices = [consensus.lowTarget, consensus.targetPrice, consensus.highTarget, consensus.currentPrice]
        let historicalPrices = chartPrices
        let allPrices = targetPrices + historicalPrices
        // Add extra padding at the bottom to push content up
        let minValue = allPrices.min() ?? consensus.lowTarget
        let maxValue = allPrices.max() ?? consensus.highTarget
        let range = maxValue - minValue
        return minValue - (range * 0.3) // Add 30% padding below
    }

    private var maxPrice: Double {
        let targetPrices = [consensus.lowTarget, consensus.targetPrice, consensus.highTarget, consensus.currentPrice]
        let historicalPrices = chartPrices
        let allPrices = targetPrices + historicalPrices
        // Add less padding at the top
        let maxValue = allPrices.max() ?? consensus.highTarget
        let minValue = allPrices.min() ?? consensus.lowTarget
        let range = maxValue - minValue
        return maxValue + (range * 0.1) // Add 10% padding above
    }

    /// Format month string from "MM/YYYY" to "MM/YY"
    private func formatMonthLabel(_ month: String) -> String {
        // Convert "02/2025" to "02/25"
        let components = month.split(separator: "/")
        guard components.count == 2,
              let year = components.last,
              year.count == 4 else {
            return month // Return as-is if format is unexpected
        }
        let shortYear = year.suffix(2)
        return "\(components[0])/\(shortYear)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Title and Description
            analystPriceTargetHeader
                .padding(.bottom, AppSpacing.lg)

            // Price line + Min/Avg/Max pole + dashed current-price line
            analystPriceChart

            // Buy/Sell volume bars (no second price line — the price line
            // lives in the analyst chart above)
            hedgeFundsSection

            // Momentum — now the last element
            momentumSection
                .padding(.top, AppSpacing.sm)
        }
    }

    // MARK: - Analyst Price Target Header

    private var analystPriceTargetHeader: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Analyst Price Target")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            Text("One-year price forecast: \(consensus.rating.rawValue.uppercased()) consensus. Target \(consensus.formattedTargetPrice) (range \(consensus.formattedLowTarget) - \(consensus.formattedHighTarget)). Current price: \(consensus.formattedCurrentPrice).")
                .font(AppTypography.label)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Analyst Price Chart

    private var analystPriceChart: some View {
        GeometryReader { geometry in
            let leadingPadding: CGFloat = 20 // Align with Swift Charts leading space
            let chartWidth = geometry.size.width - 50 - leadingPadding // Reserve 50pts for badges and 20pts for leading

            // Single coordinate system: every element below resolves its y
            // through `yPosition(for:in:)` in the GeometryReader's top-origin
            // space, and its x relative to `leadingPadding`. The price line,
            // dashed line, current-price pill, pole, and badges therefore
            // share one vertical scale — so the line terminates exactly at the
            // current-price pill and the dashed line crosses the pole at the
            // current-price level.
            ZStack {
                // Price line chart — nudged up a hair so its endpoint reads
                // as meeting the gray current-price dot. Purely cosmetic; the
                // dot, dashed line, pole, and badges stay anchored to their
                // true price y so nothing misrepresents the data.
                if !chartPrices.isEmpty {
                    priceLineChart(chartWidth: chartWidth, leadingPadding: leadingPadding, in: geometry)
                }

                // Current price indicator and dashed line
                currentPriceIndicator(chartWidth: chartWidth, leadingPadding: leadingPadding, in: geometry)

                // Target pole with points (far right)
                targetPole(chartWidth: chartWidth, leadingPadding: leadingPadding, in: geometry)

                // Target badges on the right
                targetBadges(chartWidth: chartWidth, leadingPadding: leadingPadding, in: geometry)
            }
        }
        .frame(height: 260)
    }

    // MARK: - Chart Components

    /// Price series for the analyst price-target line. Prefers the detailed
    /// ~2-year daily series carried by the hedge-fund smart-money payload —
    /// the SAME data the Hedge Funds chart below plots — then its quarterly
    /// series, then the legacy monthly series. The last point is pinned to
    /// the live `currentPrice` so the line terminates exactly at the gray
    /// current-price dot.
    private var chartPrices: [Double] {
        let source: [Double]
        if let daily = consensus.hedgeFundSmartMoney?.dailyPrices, daily.count >= 10 {
            source = daily.map { $0.price }
        } else if let quarterly = consensus.hedgeFundSmartMoney?.priceData, !quarterly.isEmpty {
            source = quarterly.map { $0.price }
        } else {
            source = consensus.hedgeFundPriceData.map { $0.price }
        }
        guard !source.isEmpty else { return [] }
        var prices = source
        prices[prices.count - 1] = consensus.currentPrice
        return prices
    }

    private func priceLineChart(chartWidth: CGFloat, leadingPadding: CGFloat, in geometry: GeometryProxy) -> some View {
        Path { path in
            let prices = chartPrices
            guard !prices.isEmpty else { return }

            // End the line short of the Min/Avg/Max pole so its endpoint just
            // touches the dashed current-price line without crowding the pole.
            // The dashed line continues from here across to the pole.
            let poleGap: CGFloat = 24
            let lineWidth = max(chartWidth - poleGap, 1)
            let xStep = lineWidth / CGFloat(max(prices.count - 1, 1))

            // Start path
            let firstY = yPosition(for: prices[0], in: geometry)
            path.move(to: CGPoint(x: leadingPadding, y: firstY))

            // Draw line through all points
            for (index, price) in prices.enumerated() {
                let x = leadingPadding + CGFloat(index) * xStep
                let y = yPosition(for: price, in: geometry)
                path.addLine(to: CGPoint(x: x, y: y))
            }
        }
        .stroke(AppColors.primaryBlue, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
    }

    private func currentPriceIndicator(chartWidth: CGFloat, leadingPadding: CGFloat, in geometry: GeometryProxy) -> some View {
        let yPos = yPosition(for: consensus.currentPrice, in: geometry)

        // Dashed horizontal line at the current-price y, read straight across
        // to the gray current-price dot on the target pole (see `targetPole`).
        // Gray to match that dot, distinct from the blue price-history line and
        // the blue Avg marker.
        return Path { path in
            path.move(to: CGPoint(x: leadingPadding, y: yPos))
            path.addLine(to: CGPoint(x: leadingPadding + chartWidth, y: yPos))
        }
        .stroke(AppColors.textSecondary, style: StrokeStyle(lineWidth: 1.5, dash: [5, 3]))
    }

    private func targetPole(chartWidth: CGFloat, leadingPadding: CGFloat, in geometry: GeometryProxy) -> some View {
        let xPos = leadingPadding + chartWidth - 3 // Position slightly left of the edge
        let highY = yPosition(for: consensus.highTarget, in: geometry)
        let avgY = yPosition(for: consensus.targetPrice, in: geometry)
        let lowY = yPosition(for: consensus.lowTarget, in: geometry)

        // Extend the pole beyond the points
        let poleExtension: CGFloat = 20
        let extendedHighY = highY - poleExtension
        let extendedLowY = lowY + poleExtension

        return Group {
            // Thick vertical pole from low to high (extended)
            RoundedRectangle(cornerRadius: 2)
                .fill(AppColors.textMuted.opacity(0.4))
                .frame(width: 6, height: max(extendedLowY - extendedHighY, 1))
                .position(x: xPos, y: (extendedHighY + extendedLowY) / 2)

            // High target point (green) - smaller
            Circle()
                .fill(AppColors.bullish)
                .frame(width: 10, height: 10)
                .position(x: xPos, y: highY)

            // Average target point (blue) - smaller
            Circle()
                .fill(AppColors.primaryBlue)
                .frame(width: 10, height: 10)
                .position(x: xPos, y: avgY)

            // Low target point (red) - smaller
            Circle()
                .fill(AppColors.bearish)
                .frame(width: 10, height: 10)
                .position(x: xPos, y: lowY)
        }
    }

    private func targetBadges(chartWidth: CGFloat, leadingPadding: CGFloat, in geometry: GeometryProxy) -> some View {
        let badgeGutter: CGFloat = 50 // Matches the 50pt reserved in chartWidth
        let badgeLeadingX = leadingPadding + chartWidth // Just right of the pole
        let highY = yPosition(for: consensus.highTarget, in: geometry)
        let avgY = yPosition(for: consensus.targetPrice, in: geometry)
        let lowY = yPosition(for: consensus.lowTarget, in: geometry)

        return Group {
            // High target badge - centered vertically with the point
            targetBadge(
                label: "Max",
                price: consensus.formattedHighTarget,
                percent: consensus.formattedHighTargetPercent,
                color: AppColors.bullish
            )
            .frame(width: badgeGutter, alignment: .leading)
            .position(x: badgeLeadingX + badgeGutter / 2, y: highY)

            // Average target badge - centered vertically with the point
            targetBadge(
                label: "Avg",
                price: consensus.formattedTargetPrice,
                percent: consensus.formattedAvgTargetPercent,
                color: AppColors.primaryBlue
            )
            .frame(width: badgeGutter, alignment: .leading)
            .position(x: badgeLeadingX + badgeGutter / 2, y: avgY)

            // Low target badge - centered vertically with the point
            targetBadge(
                label: "Min",
                price: consensus.formattedLowTarget,
                percent: consensus.formattedLowTargetPercent,
                color: AppColors.bearish
            )
            .frame(width: badgeGutter, alignment: .leading)
            .position(x: badgeLeadingX + badgeGutter / 2, y: lowY)
        }
    }

    private func targetBadge(label: String, price: String, percent: String, color: Color) -> some View {
        VStack(alignment: .center, spacing: 2) {
            Text(label)
                .font(AppTypography.captionTiny)
                .foregroundColor(AppColors.textMuted)

            Text(price)
                .font(AppTypography.caption).fontWeight(.bold)
                .foregroundColor(AppColors.textPrimary)

            Text(percent)
                .font(AppTypography.captionSmall).fontWeight(.bold)
                .foregroundColor(color)
        }
    }

    private func yPosition(for price: Double, in geometry: GeometryProxy) -> CGFloat {
        let priceRange = maxPrice - minPrice
        guard priceRange > 0 else { return geometry.size.height / 2 }

        let normalizedValue = (price - minPrice) / priceRange
        return geometry.size.height * (1 - normalizedValue)
    }

    // MARK: - Momentum Section

    private var momentumSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Momentum")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            HStack(spacing: AppSpacing.lg) {
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "arrow.up")
                        .font(AppTypography.iconTiny).fontWeight(.bold)
                        .foregroundColor(AppColors.bullish)
                    Text("\(consensus.momentumUpgrades)")
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textPrimary)
                    Text("Upgrades")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "arrow.down")
                        .font(AppTypography.iconTiny).fontWeight(.bold)
                        .foregroundColor(AppColors.bearish)
                    Text("\(consensus.momentumDowngrades)")
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textPrimary)
                    Text("Downgrades")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }
        }
    }

    // MARK: - Hedge Funds Section

    private var hedgeFundsSection: some View {
        Group {
            if let hedgeFundNote = consensus.hedgeFundNote {
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Text("Hedge Funds")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textSecondary)

                    hedgeFundFlowContent

                    Text(hedgeFundNote)
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textSecondary)
                        .padding(.top, AppSpacing.sm)
                }
            }
        }
    }

    /// Hedge-fund flow chart. Prefers the quarterly institutional payload
    /// mirrored verbatim from the Holders tab (same chart + net-flow badge,
    /// same `SmartMoneySection` layout). Falls back to the legacy monthly
    /// projection for reports persisted before `hedge_fund_smart_money`
    /// existed.
    @ViewBuilder
    private var hedgeFundFlowContent: some View {
        if let smartMoney = consensus.hedgeFundSmartMoney,
           smartMoney.flowData.contains(where: { $0.hasActivity }) {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("\(smartMoney.summary.periodDescription) Flow")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.top, AppSpacing.md)

                // Volume bars only — the price line lives in the analyst
                // chart above. Trailing inset keeps the bars in the same
                // horizontal band as that line (clear of the pole/badge gutter).
                SmartMoneyFlowChart(
                    priceData: smartMoney.priceData,
                    dailyPrices: smartMoney.dailyPrices,
                    flowData: smartMoney.flowData,
                    showPriceChart: false,
                    showVolumeYAxis: false
                )
                .padding(.trailing, 50)

                SmartMoneyFlowLegend()
                    .padding(.top, AppSpacing.xs)

                SmartMoneyNetFlowBadge(summary: smartMoney.summary)
                    .padding(.top, AppSpacing.sm)
            }
        } else if !consensus.hedgeFundPriceData.isEmpty && !consensus.hedgeFundFlowData.isEmpty {
            // Legacy monthly fallback (pre-`hedge_fund_smart_money` reports)
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("12-Month Flow")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.top, AppSpacing.md)

                SmartMoneyFlowChart(
                    priceData: consensus.hedgeFundPriceData,
                    dailyPrices: [],
                    flowData: consensus.hedgeFundFlowData,
                    showPriceChart: false,
                    showVolumeYAxis: false
                )
                .padding(.trailing, 50)

                SmartMoneyFlowLegend()
                    .padding(.top, AppSpacing.xs)
            }
        }
    }
}

#Preview {
    ReportConsensusBar(consensus: TickerReportData.sampleOracle.wallStreetConsensus)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
