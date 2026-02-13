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
        let historicalPrices = consensus.hedgeFundPriceData.map { $0.price }
        let allPrices = targetPrices + historicalPrices
        // Add extra padding at the bottom to push content up
        let minValue = allPrices.min() ?? consensus.lowTarget
        let maxValue = allPrices.max() ?? consensus.highTarget
        let range = maxValue - minValue
        return minValue - (range * 0.3) // Add 30% padding below
    }

    private var maxPrice: Double {
        let targetPrices = [consensus.lowTarget, consensus.targetPrice, consensus.highTarget, consensus.currentPrice]
        let historicalPrices = consensus.hedgeFundPriceData.map { $0.price }
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

            // Price Chart with Target Zones
            analystPriceChart

            // Momentum
            momentumSection
                .padding(.top, -20)
                .padding(.bottom, AppSpacing.lg)

            // Hedge Funds
            hedgeFundsSection
        }
    }

    // MARK: - Analyst Price Target Header

    private var analystPriceTargetHeader: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Analyst Price Target")
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textSecondary)

            Text("One-year price forecast: \(consensus.rating.rawValue.uppercased()) consensus. Target \(consensus.formattedTargetPrice) (range \(consensus.formattedLowTarget) - \(consensus.formattedHighTarget)). Current price: \(consensus.formattedCurrentPrice).")
                .font(AppTypography.subheadline)
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

            ZStack(alignment: .leading) {
                // Price line chart
                if !consensus.hedgeFundPriceData.isEmpty {
                    priceLineChart(chartWidth: chartWidth, in: geometry)
                        .offset(x: leadingPadding, y: -120) // Move up 10 more points
                }

                // Current price indicator and dashed line
                currentPriceIndicator(chartWidth: chartWidth, in: geometry)
                    .offset(x: leadingPadding, y: -120) // Move up 10 more points

                // Target pole with points (far right)
                targetPole(chartWidth: chartWidth, in: geometry)
                    .offset(x: leadingPadding, y: -120) // Move up 10 more points

                // Target badges on the right
                targetBadges(chartWidth: chartWidth, in: geometry)
                    .offset(x: leadingPadding, y: -120) // Move up 10 more points
            }
        }
        .frame(height: 260)
    }

    // MARK: - Chart Components

    private func priceLineChart(chartWidth: CGFloat, in geometry: GeometryProxy) -> some View {
        Path { path in
            let points = consensus.hedgeFundPriceData
            guard !points.isEmpty else { return }

            let xStep = chartWidth / CGFloat(max(points.count - 1, 1))

            // Start path
            let firstY = yPosition(for: points[0].price, in: geometry)
            path.move(to: CGPoint(x: 0, y: firstY))

            // Draw line through all points
            for (index, point) in points.enumerated() {
                let x = CGFloat(index) * xStep
                let y = yPosition(for: point.price, in: geometry)
                path.addLine(to: CGPoint(x: x, y: y))
            }
        }
        .stroke(AppColors.primaryBlue, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
    }

    private func currentPriceIndicator(chartWidth: CGFloat, in geometry: GeometryProxy) -> some View {
        let yPos = yPosition(for: consensus.currentPrice, in: geometry)

        return Group {
            // Dashed horizontal line
            Path { path in
                path.move(to: CGPoint(x: 0, y: yPos))
                path.addLine(to: CGPoint(x: chartWidth, y: yPos))
            }
            .stroke(AppColors.primaryBlue, style: StrokeStyle(lineWidth: 1.5, dash: [5, 3]))

            // Current price badge - positioned at the end of the price line
            if let lastPrice = consensus.hedgeFundPriceData.last {
                let lastIndex = consensus.hedgeFundPriceData.count - 1
                let xStep = chartWidth / CGFloat(max(consensus.hedgeFundPriceData.count - 1, 1))
                let xPos = CGFloat(lastIndex) * xStep

                Text(consensus.formattedCurrentPrice)
                    .font(.system(size: 12, weight: .bold))
                    .foregroundColor(.white)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(
                        Capsule()
                            .fill(AppColors.primaryBlue)
                    )
                    .offset(x: max(0, min(xPos - 20, chartWidth - 50)), y: yPos - 16)
            }
        }
    }

    private func targetPole(chartWidth: CGFloat, in geometry: GeometryProxy) -> some View {
        let xPos = chartWidth - 3 // Position slightly left of the edge
        let highY = yPosition(for: consensus.highTarget, in: geometry)
        let avgY = yPosition(for: consensus.targetPrice, in: geometry)
        let lowY = yPosition(for: consensus.lowTarget, in: geometry)
        
        // Extend the pole beyond the points
        let poleExtension: CGFloat = 20
        let extendedHighY = highY - poleExtension
        let extendedLowY = lowY + poleExtension

        return ZStack(alignment: .leading) {
            // Thick vertical pole from low to high (extended)
            RoundedRectangle(cornerRadius: 2)
                .fill(AppColors.textMuted.opacity(0.4))
                .frame(width: 6, height: extendedLowY - extendedHighY)
                .offset(x: xPos - 3, y: extendedHighY + (extendedLowY - extendedHighY) / 2)

            // High target point (green)
            Circle()
                .fill(AppColors.bullish)
                .frame(width: 12, height: 12)
                .offset(x: xPos - 6, y: highY)

            // Average target point (blue)
            Circle()
                .fill(AppColors.primaryBlue)
                .frame(width: 12, height: 12)
                .offset(x: xPos - 6, y: avgY)

            // Low target point (red)
            Circle()
                .fill(AppColors.bearish)
                .frame(width: 12, height: 12)
                .offset(x: xPos - 6, y: lowY)
        }
    }

    private func targetBadges(chartWidth: CGFloat, in geometry: GeometryProxy) -> some View {
        let xPos = chartWidth + 5 // Position badges closer to pole
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
            .offset(x: xPos, y: highY + 1)

            // Average target badge - centered vertically with the point
            targetBadge(
                label: "Avg",
                price: consensus.formattedTargetPrice,
                percent: consensus.formattedAvgTargetPercent,
                color: AppColors.primaryBlue
            )
            .offset(x: xPos, y: avgY + 1)

            // Low target badge - centered vertically with the point
            targetBadge(
                label: "Min",
                price: consensus.formattedLowTarget,
                percent: consensus.formattedLowTargetPercent,
                color: AppColors.bearish
            )
            .offset(x: xPos, y: lowY + 1)
        }
    }

    private func targetBadge(label: String, price: String, percent: String, color: Color) -> some View {
        VStack(alignment: .center, spacing: 2) {
            Text(label)
                .font(.system(size: 9, weight: .semibold))
                .foregroundColor(AppColors.textMuted)

            Text(price)
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(AppColors.textPrimary)

            Text(percent)
                .font(.system(size: 10, weight: .bold))
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
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textSecondary)

            HStack(spacing: AppSpacing.lg) {
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundColor(AppColors.bullish)
                    Text("\(consensus.momentumUpgrades)")
                        .font(AppTypography.subheadline)
                        .foregroundColor(AppColors.textPrimary)
                    Text("Upgrades")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "arrow.down")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundColor(AppColors.bearish)
                    Text("\(consensus.momentumDowngrades)")
                        .font(AppTypography.subheadline)
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
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.textSecondary)

                    // Hedge Fund Flow Chart (Price on top, Buy/Sell volume below)
                    if !consensus.hedgeFundPriceData.isEmpty && !consensus.hedgeFundFlowData.isEmpty {
                        VStack(alignment: .leading, spacing: AppSpacing.sm) {
                            Text("12-Month Flow")
                                .font(AppTypography.footnote)
                                .foregroundColor(AppColors.textMuted)
                                .padding(.top, AppSpacing.md)

                            SmartMoneyFlowChart(
                                priceData: consensus.hedgeFundPriceData,
                                flowData: consensus.hedgeFundFlowData
                            )

                            SmartMoneyFlowLegend()
                                .padding(.top, AppSpacing.xs)
                        }
                    }

                    Text(hedgeFundNote)
                        .font(AppTypography.subheadline)
                        .foregroundColor(AppColors.textSecondary)
                        .padding(.top, AppSpacing.sm)
                }
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
