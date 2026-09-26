//
//  ReportConsensusBar.swift
//  ios
//
//  Molecule: the report's "Valuation & Institutions" section (wire key and type names keep
//  their old "Wall Street consensus" spelling) — the Caydex Fair Value Estimate, RANGE FIRST,
//  over the price chart with the estimate's range as a pole; then the Institutions (13F) flow
//  and the AI insight.
//
//  The analyst half this card used to draw (the "Analyst Price Target" heading, the
//  Buy/Hold/Sell bar, the target line, the target pole, Momentum) is GONE for every report,
//  old ones included — the owner's decision of 2026-09-26. Analyst ratings and price targets
//  are outside the signed FMP licence, so it only ever said "No analyst price targets are
//  available", and a model estimate must never sit under a price-target heading or a coloured
//  BUY/HOLD/SELL line (documents/research/dcf-methodology-v1.md §5). The DTO still decodes
//  every analyst field; only `insightWasWrittenForAnalystCard` reads them.
//

import SwiftUI

struct ReportConsensusBar: View {
    let consensus: ReportWallStreetConsensus

    /// Tapped quarter in the hedge-fund net-flow chart; nil → show the latest.
    @State private var selectedFlowIndex: Int? = nil

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
            // The estimate IS this section's headline: the range, its middle mark, the gap
            // against the price frozen with the report (so it says "at report time"), and
            // the "not a price target" line.
            if let estimate = consensus.caydexFairValue {
                CaydexFairValueRow(estimate: estimate, currentPrice: consensus.currentPrice,
                                   priceContext: "at report time")
                    .padding(.bottom, AppSpacing.lg)
            } else {
                // A report saved before the estimate existed, or served while it is off.
                Text(CaydexFairValue.notInReport)
                    .font(AppTypography.label)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.bottom, AppSpacing.lg)
            }

            // Price line + the estimate's range as a pole (Low / Estimate / High).
            CaydexFairValueRangeChart(prices: chartSeries.prices, currentPrice: consensus.currentPrice,
                                      estimate: consensus.caydexFairValue, periodLabel: chartSeries.label,
                                      priceLegend: "Price at report time")

            // Net institutional flow bars, aligned under the price line.
            hedgeFundsSection
                .padding(.top, AppSpacing.md)

            // AI insight — institutional positioning read against the estimate.
            insightSection
                .padding(.top, AppSpacing.xl)
        }
        // Tapping anywhere outside a chart column dismisses the quarter popup.
        .contentShape(Rectangle())
        .onTapGesture { selectedFlowIndex = nil }
    }

    /// The price series the chart draws, and the label for its window. Prefers the ~2-year
    /// daily series carried by the 13F payload — the SAME data the Institutions chart below
    /// plots — then its quarterly closes, then the legacy monthly series. The chart pins the
    /// last point to the report-time price.
    private var chartSeries: (prices: [Double], label: String?) {
        if let daily = consensus.hedgeFundSmartMoney?.dailyPrices, daily.count >= 10 {
            return (daily.map { $0.price },
                    CaydexFairValue.pricePeriodLabel(from: daily.first?.date, to: daily.last?.date))
        }
        if let quarterly = consensus.hedgeFundSmartMoney?.priceData, !quarterly.isEmpty {
            return (quarterly.map { $0.price }, "Price · quarter-end closes")
        }
        if !consensus.hedgeFundPriceData.isEmpty {
            return (consensus.hedgeFundPriceData.map { $0.price }, "Price · month-end closes")
        }
        return ([], nil)
    }

    // MARK: - Insight (AI synthesis of the section)

    /// Shown only on a report that carries the Caydex block, i.e. only beside the estimate the
    /// insight was written against. Every older insight was written for a card this section
    /// no longer draws, and would sit under the new heading with nothing behind it:
    /// • analyst era — "Buy-rated with a $190 target…";
    /// • FMP-DCF era (2026-09-03 → 09-26) — "diverges from our model, which suggests the stock
    ///   is overpriced", right under "No Caydex Fair Value Estimate is available" (seen on the
    ///   simulator, 2026-09-26).
    /// The analyst-era check stays as a second lock in case a block is ever back-filled.
    @ViewBuilder
    private var insightSection: some View {
        if consensus.caydexFairValue != nil,
           !consensus.insightWasWrittenForAnalystCard,
           let insight = consensus.wallStreetInsight, !insight.isEmpty {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: AppSymbols.ai)
                        .foregroundStyle(AppColors.aiRampStart)
                        .font(AppTypography.iconDefault).fontWeight(.semibold)
                    Text("Insight")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundStyle(AppGradients.ai)
                }
                Text(insight)
                    .font(AppTypography.body)
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
        // Always show the "Institutions" title. When there's no institutional
        // (13F) data, an explicit empty line reads as "no data" instead of a
        // silently-missing section (mirrors the Congressional Trades empty state
        // in Hidden Market Signals).
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text("Institutions")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            if hasInstitutionalData {
                hedgeFundFlowContent
            } else {
                Text("No recent institutional trading activity.")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, AppSpacing.xs)
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

                // Net-flow bars drawn in the price chart's exact coordinate
                // system, so the y-axis lands in the same gutter as the
                // estimate pole's badges and the bars span under the price line.
                volumeBarsChart(smartMoney.flowData)

                SmartMoneyFlowLegend(buyLabel: "Net Buying", sellLabel: "Net Selling", font: AppTypography.label, labelColor: AppColors.textMuted)
                    .padding(.top, AppSpacing.xs)

                SmartMoneyNetFlowBadge(summary: smartMoney.summary, compact: true)
                    .padding(.top, -AppSpacing.xxs)
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

                SmartMoneyFlowLegend(buyLabel: "Net Buying", sellLabel: "Net Selling", font: AppTypography.label, labelColor: AppColors.textMuted)
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
            let netStr = Self.formatNetShares(net)
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
                    .cardFill()
                    .shadow(color: AppColors.shadowAmbient, radius: 8, x: 0, y: 4)
            )
            .overlay(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .strokeBorder(AppColors.cardBackgroundLight, lineWidth: 1)
            )
            // Pull the bars chart up toward the card — the bars chart has empty
            // headroom above its centered bars, so this closes the gap below the card.
            .padding(.bottom, -AppSpacing.md)
        }
    }

    // MARK: - Hedge Fund Volume Bars (custom-aligned)

    /// Buy/sell volume bars drawn in the SAME coordinate system as
    /// `CaydexFairValueRangeChart`: bars span the price line's x-range and the
    /// y-axis labels sit in the identical right-hand gutter as the estimate pole's
    /// badges. Custom (not `SmartMoneyFlowChart`) so the axis aligns with the
    /// chart above exactly — Swift Charts' auto-placed axis can't guarantee it.
    private func volumeBarsChart(_ bars: [SmartMoneyFlowDataPoint]) -> some View {
        GeometryReader { geometry in
            let leadingPadding = CaydexFairValueRangeChart.Layout.leadingPadding
            let chartWidth = geometry.size.width - CaydexFairValueRangeChart.Layout.trailingGutter - leadingPadding
            let poleGap = CaydexFairValueRangeChart.Layout.poleGap
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
            // The `1` floor is only a guard against an all-zero series. Applying
            // it unconditionally squashed every sub-1M-share ticker's bars to
            // <=30% of the plot while the Holders tab drew the same data
            // full-height. Floor only when there is genuinely nothing to scale to.
            let axisMax = dataMax > 0 ? niceAxisMax(dataMax) : 1
            // Axis labels start at the SAME x as the price chart's label column above them.
            let gutterCenterX = leadingPadding + chartWidth + CaydexFairValueRangeChart.Layout.labelGap + 25

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
                        .frame(width: 50, alignment: .leading)
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
        .frame(height: 120)
    }

    /// Round a positive value up to a "nice" axis maximum (1/2/2.5/5 × 10ⁿ).
    /// Signed net-share label for the quarter popup.
    ///
    /// `%.0fM` printed "−0M shares" for a real 420,000-share quarter — the popup
    /// asserted zero net flow beside a prominent red bar and a "31 added /
    /// 48 trimmed" caption. Steps down to K like `formatVolumeAxis` does.
    private static func formatNetShares(_ millions: Double) -> String {
        let mag = abs(millions)
        guard mag > 0 else { return "Flat" }
        let sign = millions >= 0 ? "+" : "−"
        if mag >= 1000 { return String(format: "%@%.2fB shares", sign, mag / 1000) }
        if mag >= 1 { return String(format: "%@%.2fM shares", sign, mag) }
        return String(format: "%@%.0fK shares", sign, mag * 1000)
    }

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
    /// Share-count axis label. Mirrors `SmartMoneyFlowChart.formatVolumeValue`
    /// so the report's Institutions chart and the Holders tab's label the same
    /// magnitudes identically.
    ///
    /// The sub-million branches are load-bearing: without them a mid-cap whose
    /// quarters are all under 1M shares rendered its ±0.5 gridlines (±500,000
    /// shares) as "0", producing three stacked "0" labels on one axis.
    private func formatVolumeAxis(_ millions: Double) -> String {
        let magnitude = abs(millions)
        if magnitude >= 1000 { return String(format: "%.0fB", millions / 1000) }
        if magnitude >= 1 { return String(format: "%.0fM", millions) }
        if magnitude >= 0.01 { return String(format: "%.0fK", millions * 1000) }
        if magnitude > 0 { return String(format: "%.1fK", millions * 1000) }
        return "0"
    }
}

#Preview("Estimate") {
    ReportConsensusBar(consensus: TickerReportData.sampleOracle.wallStreetConsensus)
        .padding()
        .background(AppColors.cardBackground)
}

#Preview("Refused, and an analyst-era report") {
    let base = TickerReportData.sampleOracle.wallStreetConsensus
    func variant(_ estimate: CaydexFairValue?, targets: Bool) -> ReportWallStreetConsensus {
        ReportWallStreetConsensus(
            rating: base.rating,
            currentPrice: base.currentPrice,
            targetPrice: targets ? 190 : nil,
            lowTarget: targets ? 150 : nil,
            highTarget: targets ? 230 : nil,
            valuationStatus: base.valuationStatus,
            discountPercent: base.discountPercent,
            wallStreetInsight: targets
                ? "Buy-rated with a $190 target — this text must NOT render."
                : base.wallStreetInsight,
            hedgeFundPriceData: base.hedgeFundPriceData,
            hedgeFundFlowData: base.hedgeFundFlowData,
            hedgeFundSmartMoney: base.hedgeFundSmartMoney,
            momentumUpgrades: 0,
            momentumDowngrades: 0,
            momentumMaintains: 0,
            analystStrongBuy: 0,
            analystBuy: 0,
            analystHold: 0,
            analystSell: 0,
            analystStrongSell: 0,
            caydexFairValue: estimate
        )
    }
    return ScrollView {
        VStack(spacing: 32) {
            ReportConsensusBar(consensus: variant(.sampleRefused, targets: false))
            ReportConsensusBar(consensus: variant(nil, targets: true))
        }
        .padding()
    }
    .background(AppColors.cardBackground)
}
