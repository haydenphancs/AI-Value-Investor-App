//
//  ReportGrowthChartCard.swift
//  ios
//
//  Molecule: the paid report's redesigned Growth card. Shows the FULL rich
//  growth chart (parity with the free TickerDetailView Growth chart — absolute-
//  value bars + YoY% line + dashed sector-average line + per-period labels,
//  5 metrics × Annual/Quarterly) wrapped in the deep-dive card chrome (title +
//  star rating + quality label) so it stays visually consistent with the
//  Profitability / Valuation / Health cards. Reuses the free-view chart pieces
//  (GrowthMetricSelector / GrowthPeriodToggle / GrowthChartView / GrowthLegendView).
//

import SwiftUI

struct ReportGrowthChartCard: View {
    let data: DeepDiveMetricCard       // chrome: title, star rating, quality label
    let growthData: GrowthSectionData  // the rich chart data (frozen in the report)

    @State private var selectedMetric: GrowthMetricType = .eps
    @State private var selectedPeriod: GrowthPeriodType = .annual

    private var ratingColor: Color {
        switch data.starRating {
        case 4...5: return AppColors.bullish
        case 3: return AppColors.neutral
        default: return AppColors.bearish
        }
    }

    // Footer color follows the takeaway's SENTIMENT, not the star rating
    // (mirrors ReportDeepDiveMetricCard).
    private var labelColor: Color {
        switch data.qualitySentiment.lowercased() {
        case "negative": return AppColors.bearish
        case "positive": return AppColors.bullish
        default: return ratingColor
        }
    }

    private var currentDataPoints: [GrowthDataPoint] {
        growthData.dataPoints(for: selectedMetric, period: selectedPeriod)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Title + stars — same chrome as the other deep-dive cards.
            HStack {
                Text(data.title)
                    .font(AppTypography.label)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer(minLength: 4)

                HStack(spacing: 2) {
                    ForEach(0..<5, id: \.self) { index in
                        Image(systemName: index < data.starRating ? "star.fill" : "star")
                            .font(AppTypography.iconMicro)
                            .foregroundColor(index < data.starRating ? Color(hex: "F59E0B") : AppColors.textMuted)
                    }
                }
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.2))

            // Rich chart body (reused from the free-view Growth card).
            GrowthMetricSelector(selectedMetric: $selectedMetric)

            GrowthPeriodToggle(selectedPeriod: $selectedPeriod)
                .padding(.leading, AppSpacing.xs)

            GrowthChartView(dataPoints: currentDataPoints)
                .id("\(selectedMetric.rawValue)-\(selectedPeriod.rawValue)")
                .padding(.top, AppSpacing.sm)
                .padding(.leading, -AppSpacing.md)
                .animation(.easeInOut(duration: 0.3), value: selectedMetric)
                .animation(.easeInOut(duration: 0.3), value: selectedPeriod)

            GrowthLegendView()
                .frame(maxWidth: .infinity)
                .padding(.top, AppSpacing.xs)

            // Quality label footer — sentiment color, italic (like the other cards).
            Text(data.qualityLabel)
                .font(AppTypography.labelSmall)
                .foregroundColor(labelColor)
                .lineLimit(2)
                .minimumScaleFactor(0.9)
                .italic()
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }
}

#Preview {
    ScrollView {
        ReportGrowthChartCard(
            data: TickerReportData.sampleOracle.fundamentalMetrics.first { $0.title == "Growth" }
                ?? TickerReportData.sampleOracle.fundamentalMetrics[0],
            growthData: GrowthSectionData.sampleData
        )
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
