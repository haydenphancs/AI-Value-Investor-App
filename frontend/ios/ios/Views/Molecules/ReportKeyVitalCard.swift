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
        HStack(spacing: AppSpacing.md) {
            ReportValuationVitalCard(data: sample.keyVitals.valuation)
                .frame(width: 160)
            ReportMoatVitalCard(data: sample.keyVitals.moat)
                .frame(width: 160)
            ReportFinancialHealthVitalCard(data: sample.keyVitals.financialHealth)
                .frame(width: 160)
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
