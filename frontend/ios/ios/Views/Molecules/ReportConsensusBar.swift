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
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text("Analyst Price Target")
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)

            Text("One-year price forecast based on \(consensus.rating.rawValue) consensus from Wall Street analysts. Shows average, high, and low price estimates.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(2)
        }
    }

    // MARK: - Analyst Price Chart

    private var analystPriceChart: some View {
        VStack(alignment: .leading, spacing: 0) {
            GeometryReader { geometry in
                let chartWidth = geometry.size.width - 70 // Reserve 70pts for badges

                ZStack(alignment: .leading) {
                    // Target zone lines
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
            .frame(height: 200)
            .clipped()

            // Date labels
            if let firstDate = consensus.hedgeFundPriceData.first?.month,
               let lastDate = consensus.hedgeFundPriceData.last?.month {
                HStack {
                    Text(firstDate)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text(consensus.formattedTargetPrice)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.primaryBlue)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                    Text("High")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.top, AppSpacing.xs)
            }
        }
    }

    // MARK: - Chart Components

    private func targetZoneLines(chartWidth: CGFloat, in geometry: GeometryProxy) -> some View {
        Group {
            // High target line
            Rectangle()
                .fill(AppColors.bullish.opacity(0.2))
                .frame(width: chartWidth, height: 1)
                .offset(y: yPosition(for: consensus.highTarget, in: geometry))

            // Average target line
            Rectangle()
                .fill(AppColors.bullish.opacity(0.2))
                .frame(width: chartWidth, height: 1)
                .offset(y: yPosition(for: consensus.targetPrice, in: geometry))

            // Low target line
            Rectangle()
                .fill(AppColors.bearish.opacity(0.2))
                .frame(width: chartWidth, height: 1)
                .offset(y: yPosition(for: consensus.lowTarget, in: geometry))
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
        let xPos = chartWidth - 50 // Position near the end of chart

        return Group {
            // Dashed horizontal line
            Path { path in
                path.move(to: CGPoint(x: 0, y: yPos))
                path.addLine(to: CGPoint(x: chartWidth, y: yPos))
            }
            .stroke(AppColors.primaryBlue, style: StrokeStyle(lineWidth: 1, dash: [4, 4]))

            // Current price badge
            HStack(spacing: AppSpacing.xxs) {
                Text(consensus.formattedCurrentPrice)
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(.white)
                    .padding(.horizontal, AppSpacing.xs)
                    .padding(.vertical, 2)
                    .background(
                        Capsule()
                            .fill(AppColors.primaryBlue)
                    )
            }
            .offset(x: xPos, y: yPos - 12)
        }
    }

    private func targetBadges(chartWidth: CGFloat, in geometry: GeometryProxy) -> some View {
        let xPos = chartWidth + 8 // Position badges just after chart area

        return Group {
            // High target badge
            targetBadge(
                price: consensus.formattedHighTarget,
                percent: consensus.formattedHighTargetPercent,
                color: AppColors.bullish,
                yPos: yPosition(for: consensus.highTarget, in: geometry)
            )
            .offset(x: xPos, y: 0)

            // Average target badge
            targetBadge(
                price: consensus.formattedTargetPrice,
                percent: consensus.formattedAvgTargetPercent,
                color: AppColors.bullish,
                yPos: yPosition(for: consensus.targetPrice, in: geometry)
            )
            .offset(x: xPos, y: 0)

            // Low target badge
            targetBadge(
                price: consensus.formattedLowTarget,
                percent: consensus.formattedLowTargetPercent,
                color: AppColors.bearish,
                yPos: yPosition(for: consensus.lowTarget, in: geometry)
            )
            .offset(x: xPos, y: 0)
        }
    }

    private func targetBadge(price: String, percent: String, color: Color, yPos: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(price)
                .font(.system(size: 10, weight: .bold))
                .foregroundColor(AppColors.textPrimary)

            Text(percent)
                .font(.system(size: 9, weight: .semibold))
                .foregroundColor(color)
        }
        .offset(y: yPos - 12)
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

            // Hedge Funds
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


#Preview {
    ReportConsensusBar(consensus: TickerReportData.sampleOracle.wallStreetConsensus)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
