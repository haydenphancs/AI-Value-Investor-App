//
//  ReportPeerComparisonRow.swift
//  ios
//
//  Molecule: Single competitor row with visual score bar for competitive threat
//

import SwiftUI

struct ReportPeerComparisonRow: View {
    let competitor: CompetitorComparison
    let maxScore: Double = 10.0

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            HStack {
                // Name + Ticker
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(competitor.name)
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)
                    Text(competitor.ticker)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer()

                // Threat badge
                Text(competitor.threatLevel.rawValue)
                    .font(AppTypography.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(competitor.threatLevel.color)
                    .padding(.horizontal, AppSpacing.sm)
                    .padding(.vertical, AppSpacing.xxs)
                    .background(
                        // Opaque: caution/alertOrange on their own 12% tint are under
                        // 4.5:1 in light (test_ios_theme_parity §6c).
                        RoundedRectangle(cornerRadius: AppCornerRadius.small)
                            .fill(AppColors.cardBackgroundLight)
                    )
                    // A long name truncates; the badge never squeezes.
                    .fixedSize()
            }

            // Where they compete — its own full-width line, so a 48-char label wraps
            // under the name instead of fighting the badge for the row's width. The server
            // caps the label at 48 chars, so 2 lines always fit at standard sizes; at an
            // accessibility size the caption needs 3-4 lines, so the limit is lifted there
            // rather than cutting the label mid-word.
            if let segment = competitor.segment {
                Text(segment)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityLabel("Competes in \(segment)")
            }

            // Score bar
            HStack(spacing: AppSpacing.sm) {
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        // Track
                        RoundedRectangle(cornerRadius: 3)
                            .fill(AppColors.cardBackgroundLight)
                            .frame(height: 6)

                        // Fill
                        RoundedRectangle(cornerRadius: 3)
                            .fill(barColor)
                            .frame(
                                width: geo.size.width * barFraction,
                                height: 6
                            )
                    }
                }
                .frame(height: 6)

                Text(String(format: "%.1f", competitor.competitiveScore))
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(AppColors.textSecondary)
                    .frame(width: 28, alignment: .trailing)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .cardFill(AppColors.cardBackgroundNested)   // nested in a card: step the surface up in dark
        )
    }

    /// 0...1, so an out-of-range score can never draw past the track (or a negative width).
    private var barFraction: Double {
        let f = competitor.competitiveScore / maxScore
        guard f.isFinite else { return 0 }
        return Swift.min(Swift.max(f, 0), 1)
    }

    private var barColor: Color {
        switch competitor.threatLevel {
        case .high: return AppColors.bearish
        case .moderate: return AppColors.neutral
        case .low: return AppColors.bullish
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ForEach(TickerReportData.sampleOracle.moatCompetition.competitors) { comp in
            ReportPeerComparisonRow(competitor: comp)
        }
    }
    .padding()
    .background(AppColors.cardBackground)
}

#Preview("Accessibility size") {
    ScrollView {
        VStack(spacing: AppSpacing.md) {
            ForEach(TickerReportData.sampleOracle.moatCompetition.competitors) { comp in
                ReportPeerComparisonRow(competitor: comp)
            }
        }
        .padding()
    }
    .background(AppColors.cardBackground)
    .dynamicTypeSize(.accessibility3)
}
