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
        return allPrices.min() ?? consensus.lowTarget
    }

    private var maxPrice: Double {
        let targetPrices = [consensus.lowTarget, consensus.targetPrice, consensus.highTarget, consensus.currentPrice]
        let historicalPrices = consensus.hedgeFundPriceData.map { $0.price }
        let allPrices = targetPrices + historicalPrices
        return allPrices.max() ?? consensus.highTarget
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Title and Description
            analystPriceTargetHeader

            // Price Chart with Target Zones
            analystPriceChart

            // Momentum
            momentumSection

            // Hedge Funds
            hedgeFundsSection
        }
    }

    // MARK: - Analyst Price Target Header

    private var analystPriceTargetHeader: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Analyst Price Target")
                .font(.system(size: 20, weight: .bold))
                .foregroundColor(AppColors.textPrimary)

            Text("One-year price forecast: \(consensus.rating.rawValue.uppercased()) consensus. Target \(consensus.formattedTargetPrice) (range \(consensus.formattedLowTarget) - \(consensus.formattedHighTarget)). Current price: \(consensus.formattedCurrentPrice).")
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Analyst Price Chart

    private var analystPriceChart: some View {
        VStack(alignment: .leading, spacing: 0) {
            GeometryReader { geometry in
                let chartWidth = geometry.size.width - 80 // Reserve 80pts for badges

                ZStack(alignment: .leading) {
                    // Target zone gradients
                    targetZoneLines(chartWidth: chartWidth, in: geometry)

                    // Price line chart
                    if !consensus.hedgeFundPriceData.isEmpty {
                        priceLineChart(chartWidth: chartWidth, in: geometry)
                    }

                    // Current price indicator and dashed line
                    currentPriceIndicator(chartWidth: chartWidth, in: geometry)

                    // Target badges on the right
                    targetBadges(chartWidth: chartWidth, in: geometry)
                }
            }
            .frame(height: 220)
            .clipped()

            // Date labels
            if !consensus.hedgeFundPriceData.isEmpty,
               let firstDate = consensus.hedgeFundPriceData.first?.month,
               let lastDate = consensus.hedgeFundPriceData.last?.month {
                HStack {
                    Text(firstDate)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(AppColors.textMuted)

                    Spacer()

                    Image(systemName: "arrow.right")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)

                    Spacer()

                    Text(lastDate)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.top, AppSpacing.sm)
            }
        }
    }

    // MARK: - Chart Components

    private func targetZoneLines(chartWidth: CGFloat, in geometry: GeometryProxy) -> some View {
        let highY = yPosition(for: consensus.highTarget, in: geometry)
        let avgY = yPosition(for: consensus.targetPrice, in: geometry)
        let lowY = yPosition(for: consensus.lowTarget, in: geometry)

        return Group {
            // Gradient zone from high to average (bullish zone)
            Rectangle()
                .fill(
                    LinearGradient(
                        gradient: Gradient(colors: [
                            AppColors.bullish.opacity(0.15),
                            AppColors.bullish.opacity(0.05)
                        ]),
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: chartWidth, height: avgY - highY)
                .offset(x: 0, y: highY)

            // Gradient zone from average to low (bearish zone)
            Rectangle()
                .fill(
                    LinearGradient(
                        gradient: Gradient(colors: [
                            AppColors.bullish.opacity(0.05),
                            AppColors.bearish.opacity(0.15)
                        ]),
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: chartWidth, height: lowY - avgY)
                .offset(x: 0, y: avgY)

            // High target line
            Rectangle()
                .fill(AppColors.bullish.opacity(0.4))
                .frame(width: chartWidth, height: 1.5)
                .offset(y: highY)

            // Average target line
            Rectangle()
                .fill(AppColors.primaryBlue.opacity(0.6))
                .frame(width: chartWidth, height: 2)
                .offset(y: avgY)

            // Low target line
            Rectangle()
                .fill(AppColors.bearish.opacity(0.4))
                .frame(width: chartWidth, height: 1.5)
                .offset(y: lowY)
        }
    }

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

    private func targetBadges(chartWidth: CGFloat, in geometry: GeometryProxy) -> some View {
        let xPos = chartWidth + 10 // Position badges just after chart area
        let highY = yPosition(for: consensus.highTarget, in: geometry)
        let avgY = yPosition(for: consensus.targetPrice, in: geometry)
        let lowY = yPosition(for: consensus.lowTarget, in: geometry)

        return Group {
            // High target badge
            targetBadge(
                label: "Max",
                price: consensus.formattedHighTarget,
                percent: consensus.formattedHighTargetPercent,
                color: AppColors.bullish
            )
            .offset(x: xPos, y: highY - 14)

            // Average target badge
            targetBadge(
                label: "Avg",
                price: consensus.formattedTargetPrice,
                percent: consensus.formattedAvgTargetPercent,
                color: AppColors.primaryBlue
            )
            .offset(x: xPos, y: avgY - 14)

            // Low target badge
            targetBadge(
                label: "Min",
                price: consensus.formattedLowTarget,
                percent: consensus.formattedLowTargetPercent,
                color: AppColors.bearish
            )
            .offset(x: xPos, y: lowY - 14)
        }
    }

    private func targetBadge(label: String, price: String, percent: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
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
