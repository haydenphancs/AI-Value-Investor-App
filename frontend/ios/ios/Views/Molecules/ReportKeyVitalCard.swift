//
//  ReportKeyVitalCard.swift
//  ios
//
//  Molecule: Individual Key Vital card (Valuation, Moat, or Financial Health)
//

import SwiftUI

// MARK: - Valuation Vital Card

struct ReportValuationVitalCard: View {
    let data: ReportValuationData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Title + badge
            HStack {
                Text("Valuation")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                ReportSentimentBadge(
                    text: data.status.rawValue,
                    textColor: data.status.color,
                    backgroundColor: data.status.backgroundColor
                )
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.3))

            // Current Price
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Current Price")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Text(data.formattedCurrentPrice)
                    .font(AppTypography.calloutBold)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Fair Value
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Analyst Fair Value")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                Text(data.formattedFairValue)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.primaryBlue)
            }
            
            // Upside Potential
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Upside Potential")
                    .font(AppTypography.footnote)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                Text(data.formattedUpside)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(data.upsideColor)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Moat Vital Card

struct ReportMoatVitalCard: View {
    let data: ReportMoatData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Title + Wide Moat badge
            HStack {
                Text("Moat")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                ReportSentimentBadge(
                    text: data.overallRating.rawValue,
                    textColor: data.overallRating.color,
                    backgroundColor: data.overallRating.color.opacity(0.15)
                )
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.3))

            // Trend Badge
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Trend Badge")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                
                HStack(spacing: AppSpacing.xs) {
                    
                    Text(data.stabilityLabel)
                        .font(AppTypography.subheadline)
                        .foregroundColor(AppColors.textPrimary)
                        .fontWeight(.semibold)
                }
            }

            // Primary Source
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Primary Source")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Text(data.primarySource)
                    .font(AppTypography.subheadline)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
            }

            Spacer()
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Financial Health Vital Card

struct ReportFinancialHealthVitalCard: View {
    let data: ReportFinancialHealthData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Title + level badge
            HStack {
                Text("Financial Health")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                ReportSentimentBadge(
                    text: data.level.rawValue,
                    textColor: data.level.color,
                    backgroundColor: data.level.color.opacity(0.15)
                )
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.3))

            // Altman Z-Score with Progress Bar
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text("Altman Z-Score")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                HStack(alignment: .center, spacing: AppSpacing.xs) {
                    Text(data.formattedZScore)
                        .font(AppTypography.title3)
                        .fontWeight(.bold)
                        .foregroundColor(data.level.color)
                }

                // Z-Score Progress Bar
                ZScoreProgressBar(score: data.altmanZScore)
                    .frame(height: 12)
                
                Text(data.altmanZLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            // Additional metric - Rising Expenses
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 10))
                    .foregroundColor(data.additionalMetricStatus.color)
                Text(data.additionalMetricDisplayText)
                    .font(AppTypography.caption)
                    .foregroundColor(data.additionalMetricStatus.color)
            }

            // FCF note
            Text(data.fcfNote)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineLimit(2)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Revenue Vital Card

struct ReportRevenueVitalCard: View {
    let data: ReportRevenueVitalData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Title + score badge
            HStack {
                Text("Revenue")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                VitalScoreBadge(score: data.score)
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.3))

            // Total Revenue
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Total Revenue")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Text(data.totalRevenue)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Revenue Growth
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Revenue Growth")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Text(data.formattedGrowth)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(data.growthColor)
            }

            // Top Segment
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Top Segment")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                HStack(spacing: AppSpacing.xs) {
                    Text(data.topSegment)
                        .font(AppTypography.subheadline)
                        .fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)
                    Text(data.formattedTopSegmentGrowth)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.bullish)
                }
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Insider Vital Card

struct ReportInsiderVitalCard: View {
    let data: ReportInsiderVitalData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Title + score badge
            HStack {
                Text("Insiders")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                VitalScoreBadge(score: data.score)
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.3))

            // Net Activity
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Net Activity")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Text(data.netActivity)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(data.activityColor)
            }

            // Buy / Sell Counts
            HStack(spacing: AppSpacing.md) {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Buys")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text("\(data.buyCount)")
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.bullish)
                }

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Sells")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text("\(data.sellCount)")
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.bearish)
                }
            }

            // Key Insight
            Text(data.keyInsight)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineLimit(2)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Macro Vital Card

struct ReportMacroVitalCard: View {
    let data: ReportMacroVitalData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Title + score badge
            HStack {
                Text("Macro")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                VitalScoreBadge(score: data.score)
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.3))

            // Threat Level
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Threat Level")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                ReportSentimentBadge(
                    text: data.threatLevel.rawValue,
                    textColor: data.threatLevel.color,
                    backgroundColor: data.threatLevel.color.opacity(0.15)
                )
            }

            // Top Risk
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Top Risk")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Text(data.topRisk)
                    .font(AppTypography.subheadline)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(2)
            }

            // Risk Trend + Count
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: data.riskTrend.iconName)
                    .font(.system(size: 10))
                    .foregroundColor(data.riskTrend.color)
                Text(data.riskTrend.rawValue)
                    .font(AppTypography.caption)
                    .foregroundColor(data.riskTrend.color)

                Spacer()

                Text(data.formattedRiskCount)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Forecast Vital Card

struct ReportForecastVitalCard: View {
    let data: ReportForecastVitalData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Title + score badge
            HStack {
                Text("Forecast")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                VitalScoreBadge(score: data.score)
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.3))

            // Revenue CAGR
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Revenue CAGR")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Text(data.formattedRevenueCAGR)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.bullish)
            }

            // EPS CAGR
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("EPS CAGR")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Text(data.formattedEPSCAGR)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.bullish)
            }

            // Guidance
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Guidance")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                ReportSentimentBadge(
                    text: data.guidance.rawValue,
                    textColor: data.guidance.color,
                    backgroundColor: data.guidance.backgroundColor
                )
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Wall Street Vital Card

struct ReportWallStreetVitalCard: View {
    let data: ReportWallStreetVitalData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Title + score badge
            HStack {
                Text("Wall Street")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                VitalScoreBadge(score: data.score)
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.3))

            // Consensus Rating
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Consensus")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                ReportSentimentBadge(
                    text: data.consensusRating.rawValue,
                    textColor: data.consensusRating.color,
                    backgroundColor: data.consensusRating.backgroundColor
                )
            }

            // Price Target
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Price Target")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                HStack(spacing: AppSpacing.xs) {
                    Text(data.formattedTarget)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.primaryBlue)
                    Text(data.formattedUpside)
                        .font(AppTypography.caption)
                        .foregroundColor(data.upsideColor)
                }
            }

            // Upgrades / Downgrades
            HStack(spacing: AppSpacing.md) {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Upgrades")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text("\(data.upgrades)")
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.bullish)
                }

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Downgrades")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text("\(data.downgrades)")
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.bearish)
                }
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Vital Score Badge (Shared Component)

struct VitalScoreBadge: View {
    let score: VitalScore

    var body: some View {
        HStack(spacing: AppSpacing.xxs) {
            Text("\(score.value)")
                .font(AppTypography.captionBold)
                .foregroundColor(score.color)
            Text("/10")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, AppSpacing.xs)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                .fill(score.backgroundColor)
        )
    }
}

// MARK: - Z-Score Progress Bar

struct ZScoreProgressBar: View {
    let score: Double
    
    // Z-Score ranges
    private let distressThreshold: CGFloat = 1.8
    private let greyZoneThreshold: CGFloat = 3.0
    private let maxScore: CGFloat = 5.0
    
    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                // Background bar with color zones
                HStack(spacing: 0) {
                    // Red zone (0 to 1.8)
                    Rectangle()
                        .fill(AppColors.bearish.opacity(0.2))
                        .frame(width: geometry.size.width * (distressThreshold / maxScore))
                    
                    // Grey zone (1.8 to 3.0)
                    Rectangle()
                        .fill(Color.gray.opacity(0.3))
                        .frame(width: geometry.size.width * ((greyZoneThreshold - distressThreshold) / maxScore))
                    
                    // Green zone (3.0+)
                    Rectangle()
                        .fill(AppColors.bullish.opacity(0.2))
                        .frame(width: geometry.size.width * ((maxScore - greyZoneThreshold) / maxScore))
                }
                .cornerRadius(4)
                
                // Score marker
                Circle()
                    .fill(scoreColor)
                    .frame(width: 12, height: 12)
                    .overlay(
                        Circle()
                            .stroke(AppColors.cardBackground, lineWidth: 2)
                    )
                    .offset(x: markerPosition(in: geometry.size.width) - 6)
            }
        }
    }
    
    private var scoreColor: Color {
        if score < Double(distressThreshold) {
            return AppColors.bearish
        } else if score < Double(greyZoneThreshold) {
            return Color.gray
        } else {
            return AppColors.bullish
        }
    }
    
    private func markerPosition(in width: CGFloat) -> CGFloat {
        let clampedScore = min(max(CGFloat(score), 0), maxScore)
        return width * (clampedScore / maxScore)
    }
}

#Preview {
    let sample = TickerReportData.sampleOracle
    ScrollView(.horizontal) {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            ReportValuationVitalCard(data: sample.keyVitals.valuation)
                .frame(width: 185)
            ReportMoatVitalCard(data: sample.keyVitals.moat)
                .frame(width: 185)
            ReportFinancialHealthVitalCard(data: sample.keyVitals.financialHealth)
                .frame(width: 185)
            ReportRevenueVitalCard(data: sample.keyVitals.revenue)
                .frame(width: 185)
            ReportInsiderVitalCard(data: sample.keyVitals.insider)
                .frame(width: 185)
            ReportMacroVitalCard(data: sample.keyVitals.macro)
                .frame(width: 185)
            ReportForecastVitalCard(data: sample.keyVitals.forecast)
                .frame(width: 185)
            ReportWallStreetVitalCard(data: sample.keyVitals.wallStreet)
                .frame(width: 185)
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
