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
            // Buy Rating badge (styled like WIDE MOAT)
            Text(consensus.rating.rawValue)
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(consensus.rating.color)
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xs)
                .background(
                    Capsule()
                        .fill(consensus.rating.backgroundColor)
                )

            // Price targets
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Low")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text(String(format: "$%.0f", consensus.lowTarget))
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.bearish)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                    Text("Target")
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
                    Text(String(format: "$%.0f", consensus.highTarget))
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.bullish)
                }
            }

            // Price range bar
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    // Background bar gradient
                    LinearGradient(
                        gradient: Gradient(colors: [AppColors.bearish, AppColors.neutral, AppColors.bullish]),
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                    .frame(height: 6)
                    .cornerRadius(3)

                    // Current price indicator
                    Circle()
                        .fill(AppColors.textPrimary)
                        .frame(width: 14, height: 14)
                        .shadow(color: .black.opacity(0.3), radius: 2)
                        .offset(x: CGFloat(pricePosition) * (geometry.size.width - 14))
                }
            }
            .frame(height: 14)

            // Valuation status
            if consensus.valuationStatus == .deepUndervalued {
                HStack(spacing: AppSpacing.sm) {
                    Circle()
                        .fill(AppColors.bullish)
                        .frame(width: 8, height: 8)

                    Text("Deep Undervalued")
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.bullish)
                }

                Text(consensus.formattedDiscount)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Momentum
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

                    Text(hedgeFundNote)
                        .font(AppTypography.subheadline)
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
