//
//  ReportConsensusBar.swift
//  ios
//
//  Molecule: Wall Street consensus rating bar with price targets
//

import SwiftUI

struct ReportConsensusBar: View {
    let consensus: ReportWallStreetConsensus

    /// Tapped quarter in the hedge-fund net-flow chart; nil → show the latest.
    @State private var selectedFlowIndex: Int? = nil

    /// Every price the chart must keep on-screen: the analyst targets (when
    /// present), the live current price, and the historical line. Targets are
    /// optional — when absent (no analyst coverage) the chart scales to the
    /// price line + current price alone.
    private var priceUniverse: [Double] {
        let targets = [consensus.lowTarget, consensus.targetPrice, consensus.highTarget].compactMap { $0 }
        return targets + [consensus.currentPrice] + chartPrices
    }

    private var minPrice: Double {
        let allPrices = priceUniverse
        let minValue = allPrices.min() ?? consensus.currentPrice
        let maxValue = allPrices.max() ?? consensus.currentPrice
        let range = maxValue - minValue
        return minValue - (range * 0.3) // Add 30% padding below
    }

    private var maxPrice: Double {
        let allPrices = priceUniverse
        let maxValue = allPrices.max() ?? consensus.currentPrice
        let minValue = allPrices.min() ?? consensus.currentPrice
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
                .padding(.bottom, AppSpacing.sm)

            // Period label for the whole view (price chart + volume bars below
            // both span this window). Sits right under the range/current-price
            // description.
            Text(flowPeriodLabel)
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
                .padding(.bottom, AppSpacing.sm)

            // Price line + Min/Avg/Max pole + dashed current-price line
            analystPriceChart

            // Buy/Sell volume bars (no second price line — the price line
            // lives in the analyst chart above)
            hedgeFundsSection

            // Momentum
            momentumSection
                .padding(.top, AppSpacing.sm)

            // Wall Street insight — AI synthesis of price targets, institutions,
            // and momentum, rendered as its own labeled section at the bottom.
            insightSection
                .padding(.top, AppSpacing.md)
        }
        // Tapping anywhere outside a chart column dismisses the quarter popup.
        .contentShape(Rectangle())
        .onTapGesture { selectedFlowIndex = nil }
    }

    /// Period label for the merged view (e.g. "2-Year Flow"), shown once at
    /// the top since both the price chart and the volume bars span it.
    private var flowPeriodLabel: String {
        if let sm = consensus.hedgeFundSmartMoney,
           sm.flowData.contains(where: { $0.hasActivity }) {
            return "\(sm.summary.periodDescription) Flow"
        }
        return "12-Month Flow"
    }

    // MARK: - Analyst Price Target Header

    private var analystPriceTargetHeader: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Analyst Price Target")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            Text(analystPriceTargetSummary)
                .font(AppTypography.label)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Forecast copy. With real analyst coverage it states the consensus +
    /// target range; without it, an honest "no targets" line so we never
    /// imply a forecast the data doesn't support.
    private var analystPriceTargetSummary: String {
        guard consensus.hasAnalystTargets else {
            return "No analyst price targets are available for this company yet."
        }
        return "One-year price forecast: \(consensus.rating.rawValue.uppercased()) consensus.\nTarget \(consensus.formattedTargetPrice) (range \(consensus.formattedLowTarget) - \(consensus.formattedHighTarget))."
    }

    // MARK: - Analyst Price Chart

    private var analystPriceChart: some View {
        GeometryReader { geometry in
            let leadingPadding: CGFloat = 8 // Stretch the line toward the left edge
            let chartWidth = geometry.size.width - 60 - leadingPadding // Reserve 60pts for the pole + badges (with breathing room) on the right

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

    @ViewBuilder
    private func targetPole(chartWidth: CGFloat, leadingPadding: CGFloat, in geometry: GeometryProxy) -> some View {
        if let highTarget = consensus.highTarget,
           let targetPrice = consensus.targetPrice,
           let lowTarget = consensus.lowTarget {
            let xPos = leadingPadding + chartWidth - 3 // Position slightly left of the edge
            let highY = yPosition(for: highTarget, in: geometry)
            let avgY = yPosition(for: targetPrice, in: geometry)
            let lowY = yPosition(for: lowTarget, in: geometry)

            // Extend the pole beyond the points
            let poleExtension: CGFloat = 20
            let extendedHighY = highY - poleExtension
            let extendedLowY = lowY + poleExtension

            Group {
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
    }

    @ViewBuilder
    private func targetBadges(chartWidth: CGFloat, leadingPadding: CGFloat, in geometry: GeometryProxy) -> some View {
        if let highTarget = consensus.highTarget,
           let targetPrice = consensus.targetPrice,
           let lowTarget = consensus.lowTarget {
            let badgeGutter: CGFloat = 50  // badge frame width
            let badgeGap: CGFloat = 7      // breathing room between the pole and the badge text
            let badgeCenterX = leadingPadding + chartWidth + badgeGutter / 2 + badgeGap
            let highY = yPosition(for: highTarget, in: geometry)
            let avgY = yPosition(for: targetPrice, in: geometry)
            let lowY = yPosition(for: lowTarget, in: geometry)

            Group {
                // High target badge - centered vertically with the point
                targetBadge(
                    price: formatTargetPrice(highTarget),
                    percent: consensus.formattedHighTargetPercent,
                    color: AppColors.bullish
                )
                .frame(width: badgeGutter, alignment: .leading)
                .position(x: badgeCenterX, y: highY)

                // Average target badge - centered vertically with the point
                targetBadge(
                    price: formatTargetPrice(targetPrice),
                    percent: consensus.formattedAvgTargetPercent,
                    color: AppColors.primaryBlue
                )
                .frame(width: badgeGutter, alignment: .leading)
                .position(x: badgeCenterX, y: avgY)

                // Low target badge - centered vertically with the point
                targetBadge(
                    price: formatTargetPrice(lowTarget),
                    percent: consensus.formattedLowTargetPercent,
                    color: AppColors.bearish
                )
                .frame(width: badgeGutter, alignment: .leading)
                .position(x: badgeCenterX, y: lowY)
            }
        }
    }

    /// Target price for the badges — shows cents only when the value actually
    /// has them ("$249.53"); whole numbers stay clean ("$400").
    private func formatTargetPrice(_ value: Double) -> String {
        if value == value.rounded() {
            return String(format: "$%.0f", value)
        }
        return String(format: "$%.2f", value)
    }

    private func targetBadge(price: String, percent: String, color: Color) -> some View {
        VStack(alignment: .center, spacing: 2) {
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
            // Period label disambiguates from the chart's "2-Year Flow": these
            // analyst actions are counted over the trailing 12 months
            // (analyst_service._compute_actions_summary, 365-day cutoff).
            HStack(spacing: AppSpacing.xs) {
                Text("Momentum")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textSecondary)
                Text("· Past 12 Months")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            HStack(spacing: AppSpacing.md) {
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

                // Maintains — gray "equal" icon, mirroring the Analysis tab's
                // AnalystActionBadge treatment (AppColors.textSecondary).
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "equal")
                        .font(AppTypography.iconTiny).fontWeight(.bold)
                        .foregroundColor(AppColors.textSecondary)
                    Text("\(consensus.momentumMaintains)")
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textPrimary)
                    Text("Maintains")
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

    // MARK: - Wall Street Insight (AI synthesis across all three sub-sections)

    @ViewBuilder
    private var insightSection: some View {
        if let insight = consensus.wallStreetInsight, !insight.isEmpty {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "sparkles.2")
                        .foregroundStyle(LinearGradient(
                            colors: [.indigo],
                            startPoint: .topLeading, endPoint: .bottomTrailing))
                        .font(AppTypography.iconDefault).fontWeight(.semibold)
                    Text("Insight")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundStyle(LinearGradient(
                            colors: [.indigo, .cyan],
                            startPoint: .topLeading, endPoint: .bottomTrailing))
                }
                Text(insight)
                    .font(AppTypography.label)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - Institutions Section
    // NAMING: "hedge fund" / `hedgeFund*` below is FMP 13F institutional data; this
    // section is labeled "Institutions" in the UI (SmartMoneyTab.hedgeFunds =
    // "Institutions"). The Holders tab renders the same data under that same label.

    /// True when there's real institutional (13F) data to chart — gates the
    /// Institutions section independently of the AI insight.
    private var hasInstitutionalData: Bool {
        if let sm = consensus.hedgeFundSmartMoney,
           sm.flowData.contains(where: { $0.hasActivity }) {
            return true
        }
        return !consensus.hedgeFundPriceData.isEmpty
            && !consensus.hedgeFundFlowData.isEmpty
    }

    private var hedgeFundsSection: some View {
        Group {
            // Gated on real institutional data (decoupled from the insight, which
            // now spans the whole card and lives at the bottom).
            if hasInstitutionalData {
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Text("Institutions")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textSecondary)

                    hedgeFundFlowContent
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
                // Tap-to-inspect popup for the selected quarter (hidden until a
                // bar is tapped). Sits in the slot the always-on summary used
                // to occupy, above the bars it describes.
                flowQuarterPopup(smartMoney.flowData)
                    .transition(.scale.combined(with: .opacity))

                // Net-flow bars drawn in the analyst chart's exact coordinate
                // system, so the y-axis lands in the same gutter as the
                // Min/Avg/Max badges and the bars span under the price line.
                volumeBarsChart(smartMoney.flowData)
                    .padding(.top, AppSpacing.xs)

                SmartMoneyFlowLegend(buyLabel: "Net Buying", sellLabel: "Net Selling")
                    .padding(.top, AppSpacing.xs)

                SmartMoneyNetFlowBadge(summary: smartMoney.summary)
                    .padding(.top, AppSpacing.sm)
            }
            .animation(.spring(response: 0.3, dampingFraction: 0.7), value: selectedFlowIndex)
        } else if !consensus.hedgeFundPriceData.isEmpty && !consensus.hedgeFundFlowData.isEmpty {
            // Legacy monthly fallback (pre-`hedge_fund_smart_money` reports).
            // Net derives from buy−sell here (no counts), so the bar still works.
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                flowQuarterPopup(consensus.hedgeFundFlowData)
                    .transition(.scale.combined(with: .opacity))

                volumeBarsChart(consensus.hedgeFundFlowData)
                    .padding(.top, AppSpacing.xs)

                SmartMoneyFlowLegend(buyLabel: "Net Buying", sellLabel: "Net Selling")
                    .padding(.top, AppSpacing.xs)
            }
            .animation(.spring(response: 0.3, dampingFraction: 0.7), value: selectedFlowIndex)
        }
    }

    /// Tap-to-inspect popup for the selected quarter: net share change as a
    /// header, plus how many institutions added vs trimmed when counts exist
    /// (hedge-fund data). Legacy data has no counts → header (net) only.
    /// Renders only while a bar is selected — tapping a bar toggles
    /// `selectedFlowIndex`; nil collapses this to nothing.
    @ViewBuilder
    private func flowQuarterPopup(_ bars: [SmartMoneyFlowDataPoint]) -> some View {
        if let idx = selectedFlowIndex, bars.indices.contains(idx) {
            let bar = bars[idx]
            let net = bar.netFlow
            let isBuy = net >= 0
            let mag = abs(net)
            let netStr = mag >= 1000
                ? String(format: "%@%.2fB shares", isBuy ? "+" : "−", mag / 1000)
                : String(format: "%@%.0fM shares", isBuy ? "+" : "−", mag)
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                HStack(spacing: 6) {
                    Text(formatMonthLabel(bar.month).replacingOccurrences(of: "\n", with: " "))
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                    Text("·").foregroundColor(AppColors.textMuted)
                    Text(netStr)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(isBuy ? HoldersColors.buyVolume : HoldersColors.sellVolume)
                }
                if let buyers = bar.buyersCount, let sellers = bar.sellersCount {
                    Text("\(buyers.formatted()) added / \(sellers.formatted()) trimmed")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.sm)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackground)
                    .shadow(color: Color.black.opacity(0.15), radius: 8, x: 0, y: 4)
            )
            .overlay(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .strokeBorder(AppColors.cardBackgroundLight, lineWidth: 1)
            )
        }
    }

    // MARK: - Hedge Fund Volume Bars (custom-aligned)

    /// Buy/sell volume bars drawn in the SAME coordinate system as
    /// `analystPriceChart`: bars span the price line's x-range and the billions
    /// y-axis labels sit in the identical right-hand gutter as the Min/Avg/Max
    /// badges. Custom (not `SmartMoneyFlowChart`) so the axis aligns with the
    /// price targets exactly — Swift Charts' auto-placed axis can't guarantee it.
    private func volumeBarsChart(_ bars: [SmartMoneyFlowDataPoint]) -> some View {
        GeometryReader { geometry in
            let leadingPadding: CGFloat = 8
            let chartWidth = geometry.size.width - 60 - leadingPadding
            let poleGap: CGFloat = 24
            let span = max(chartWidth - poleGap, 1)            // == price line's x-span
            let count = max(bars.count, 1)
            let slot = span / CGFloat(count)
            let barWidth = min(slot * 0.5, 22)
            let labelStride = count > 8 ? 2 : 1

            let labelStripHeight: CGFloat = 24
            let plotHeight = geometry.size.height - labelStripHeight
            let zeroY = plotHeight / 2
            let maxBarHeight = max(zeroY - 10, 1)

            let dataMax = bars.map { abs($0.netFlow) }.max() ?? 0
            let axisMax = max(niceAxisMax(dataMax), 1)
            let gutterCenterX = leadingPadding + chartWidth + 25   // == targetBadges center

            ZStack(alignment: .topLeading) {
                // Gridlines + billions y-axis labels (in the badge gutter)
                ForEach(volumeAxisTicks(axisMax), id: \.self) { tick in
                    let y = zeroY - CGFloat(tick / axisMax) * maxBarHeight

                    Path { path in
                        path.move(to: CGPoint(x: leadingPadding, y: y))
                        path.addLine(to: CGPoint(x: leadingPadding + span, y: y))
                    }
                    .stroke(AppColors.cardBackgroundLight.opacity(0.3),
                            style: StrokeStyle(lineWidth: tick == 0 ? 0.75 : 0.5))

                    Text(formatVolumeAxis(tick))
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .frame(width: 50, alignment: .center)
                        .position(x: gutterCenterX, y: y)
                }

                // One net bar per quarter — up & green when net buying, down &
                // red when net selling; height ∝ |net shares|. Tap to inspect.
                ForEach(Array(bars.enumerated()), id: \.offset) { index, bar in
                    let cx = leadingPadding + (CGFloat(index) + 0.5) * slot
                    let net = bar.netFlow
                    let isBuy = net >= 0
                    let netHeight = abs(net) > 0
                        ? max(CGFloat(min(abs(net) / axisMax, 1.0)) * maxBarHeight, 1.5) : 0

                    RoundedRectangle(cornerRadius: 2)
                        .fill(isBuy ? HoldersColors.buyVolume : HoldersColors.sellVolume)
                        .frame(width: barWidth, height: netHeight)
                        .opacity(selectedFlowIndex == nil || selectedFlowIndex == index ? 1.0 : 0.4)
                        .position(x: cx, y: isBuy ? zeroY - netHeight / 2 : zeroY + netHeight / 2)

                    // Full-height transparent hit area: tap to select (toggle).
                    Rectangle()
                        .fill(Color.clear)
                        .contentShape(Rectangle())
                        .frame(width: slot, height: plotHeight)
                        .position(x: cx, y: plotHeight / 2)
                        .onTapGesture {
                            selectedFlowIndex = (selectedFlowIndex == index) ? nil : index
                        }

                    if index % labelStride == 0 {
                        Text(formatMonthLabel(bar.month))
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .multilineTextAlignment(.center)
                            .frame(width: slot * CGFloat(labelStride))
                            .position(x: cx, y: plotHeight + labelStripHeight / 2)
                    }
                }
            }
        }
        .frame(height: 150)
    }

    /// Round a positive value up to a "nice" axis maximum (1/2/2.5/5 × 10ⁿ).
    private func niceAxisMax(_ value: Double) -> Double {
        guard value > 0 else { return 0 }
        let exponent = floor(log10(value))
        let base = pow(10.0, exponent)
        let frac = value / base
        let niceFrac: Double = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 2.5 ? 2.5 : frac <= 5 ? 5 : 10
        return niceFrac * base
    }

    /// Five symmetric ticks for the volume axis: +max, +½max, 0, −½max, −max.
    private func volumeAxisTicks(_ axisMax: Double) -> [Double] {
        [axisMax, axisMax / 2, 0, -axisMax / 2, -axisMax]
    }

    /// Format a volume (in millions) for the y-axis: "200B" / "50M" / "0".
    private func formatVolumeAxis(_ millions: Double) -> String {
        let magnitude = abs(millions)
        if magnitude >= 1000 { return String(format: "%.0fB", millions / 1000) }
        if magnitude >= 1 { return String(format: "%.0fM", millions) }
        return "0"
    }
}

#Preview {
    ReportConsensusBar(consensus: TickerReportData.sampleOracle.wallStreetConsensus)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}

#Preview("No analyst coverage") {
    let base = TickerReportData.sampleOracle.wallStreetConsensus
    return ReportConsensusBar(consensus: ReportWallStreetConsensus(
        rating: base.rating,
        currentPrice: base.currentPrice,
        targetPrice: nil,
        lowTarget: nil,
        highTarget: nil,
        valuationStatus: base.valuationStatus,
        discountPercent: base.discountPercent,
        wallStreetInsight: base.wallStreetInsight,
        hedgeFundPriceData: base.hedgeFundPriceData,
        hedgeFundFlowData: base.hedgeFundFlowData,
        hedgeFundSmartMoney: base.hedgeFundSmartMoney,
        momentumUpgrades: base.momentumUpgrades,
        momentumDowngrades: base.momentumDowngrades,
        momentumMaintains: base.momentumMaintains
    ))
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
