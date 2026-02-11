//
//  ReportPriceMovementSection.swift
//  ios
//
//  Organism: Recent Price Movement deep dive content.
//  Shows the "why" behind recent price action: catalyst badge,
//  percentage change with smart time label, sparkline with event dot,
//  and narrative explanation box.
//

import SwiftUI

struct ReportPriceMovementSection: View {
    @StateObject private var viewModel: PriceActionViewModel

    init(data: PriceActionData) {
        _viewModel = StateObject(wrappedValue: PriceActionViewModel(data: data))
    }
    
    var body: some View {
        let ctx = viewModel.context

        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Badge capsule: tag + %
            PriceActionBadge(
                tag: ctx.tag,
                percentage: ctx.displayPercentage,
                isPositive: ctx.isPositive
            )

            // Percentage text + time label
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(ctx.displayPercentage)
                    .font(.system(size: 26, weight: .bold, design: .rounded))
                    .foregroundColor(ctx.trendColor)

                Text(ctx.timeLabel)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Sparkline with event dot
            PriceActionSparkline(
                data: ctx.chartData,
                eventIndex: ctx.eventIndex,
                trendColor: ctx.trendColor
            )

            // Narrative box with header and explanation
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Header: sparkles icon + "Insight"
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "sparkles.2")
                        .foregroundStyle(
                            LinearGradient(
                                colors: [.indigo],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                        .font(.system(size: 16, weight: .semibold))
                    
                    Text("Insight")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(LinearGradient(
                            colors: [.indigo, .cyan],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        ))
                }
                
                // Narrative text
                Text(ctx.narrative)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(3)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

#Preview {
    ScrollView {
        ReportPriceMovementSection(
            data: TickerReportData.sampleOracle.priceAction
        )
        .padding()
    }
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
