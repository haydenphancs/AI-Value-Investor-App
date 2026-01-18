//
//  EarningsSurpriseBarChart.swift
//  ios
//
//  Molecule: Vertical bar chart displaying quarterly EPS surprise percentages
//  Background chart for 3Y view only - shows historical beat/miss trends
//

import SwiftUI
import Charts

struct EarningsSurpriseBarChart: View {
    let quarters: [EarningsQuarterData]
    
    // Filter to only show quarters with actual surprise data (no pending/future quarters)
    private var historicalQuarters: [EarningsQuarterData] {
        quarters.filter { $0.surprisePercent != nil }
    }
    
    // Calculate Y-axis range based on surprise percentages
    private var surpriseRange: (min: Double, max: Double) {
        let surprises = historicalQuarters.compactMap { $0.surprisePercent }
        guard !surprises.isEmpty else {
            return (min: -10, max: 20)
        }
        
        let minSurprise = surprises.min() ?? -10
        let maxSurprise = surprises.max() ?? 20
        
        // Ensure we have reasonable padding
        let min = floor(min(minSurprise, -10))
        let max = ceil(max(maxSurprise, 20))
        
        return (min: min, max: max)
    }
    
    private var chartHeight: CGFloat { 180 }
    
    var body: some View {
        VStack(alignment: .trailing, spacing: AppSpacing.sm) {
            // Chart with Y-axis on the right
            HStack(spacing: 0) {
                // Chart area
                Chart {
                    // Zero reference line
                    RuleMark(y: .value("Zero", 0))
                        .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [5, 3]))
                        .foregroundStyle(AppColors.textMuted.opacity(0.6))
                    
                    // Surprise bars
                    ForEach(historicalQuarters) { quarter in
                        if let surprise = quarter.surprisePercent {
                            BarMark(
                                x: .value("Quarter", quarter.quarter),
                                y: .value("Surprise %", surprise)
                            )
                            .foregroundStyle(surprise >= 0 ? AppColors.bullish : AppColors.bearish)
                            .cornerRadius(4)
                        }
                    }
                }
                .chartYScale(domain: surpriseRange.min...surpriseRange.max)
                .chartYAxis {
                    AxisMarks(position: .trailing, values: .automatic(desiredCount: 5)) { value in
                        AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5))
                            .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.3))
                        
                        AxisValueLabel {
                            if let percent = value.as(Double.self) {
                                Text("\(Int(percent))%")
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textMuted)
                            }
                        }
                    }
                }
                .chartXAxis {
                    AxisMarks(values: .automatic) { value in
                        AxisValueLabel {
                            if let quarter = value.as(String.self) {
                                // Show condensed labels (just Q1, Q2, etc.) for compact view
                                Text(String(quarter.prefix(2)))
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textMuted)
                            }
                        }
                    }
                }
                .frame(height: chartHeight)
                .padding(.leading, AppSpacing.sm)
                .padding(.trailing, AppSpacing.sm)
            }
            
            // Optional: Title or description
            Text("Quarterly EPS Surprise")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .padding(.trailing, AppSpacing.sm)
        }
        .padding(AppSpacing.md)
        .background(Color.black.opacity(0.3))
        .cornerRadius(AppCornerRadius.medium)
    }
}

// MARK: - Preview

#Preview("1Y View - Limited Data") {
    ZStack {
        AppColors.background
            .ignoresSafeArea()
        
        VStack(spacing: AppSpacing.lg) {
            // 1Y view with last 6 quarters
            EarningsSurpriseBarChart(
                quarters: Array(EarningsData.sampleData.epsQuarters.suffix(6))
            )
            .padding(AppSpacing.lg)
        }
    }
}

#Preview("3Y View - Full Data") {
    ZStack {
        AppColors.background
            .ignoresSafeArea()
        
        ScrollView {
            VStack(spacing: AppSpacing.lg) {
                // 3Y view with all historical quarters
                EarningsSurpriseBarChart(
                    quarters: EarningsData.sampleData.epsQuarters
                )
                .padding(AppSpacing.lg)
                
                // Sample with more extreme values
                EarningsSurpriseBarChart(
                    quarters: [
                        EarningsQuarterData(quarter: "Q1 '22", actualValue: 0.45, estimateValue: 0.42, surprisePercent: 7.1),
                        EarningsQuarterData(quarter: "Q2 '22", actualValue: 0.52, estimateValue: 0.50, surprisePercent: 15.5),
                        EarningsQuarterData(quarter: "Q3 '22", actualValue: 0.48, estimateValue: 0.52, surprisePercent: -7.7),
                        EarningsQuarterData(quarter: "Q4 '22", actualValue: 0.55, estimateValue: 0.55, surprisePercent: 0),
                        EarningsQuarterData(quarter: "Q1 '23", actualValue: 0.58, estimateValue: 0.55, surprisePercent: 5.5),
                        EarningsQuarterData(quarter: "Q2 '23", actualValue: 0.62, estimateValue: 0.60, surprisePercent: -12.3),
                        EarningsQuarterData(quarter: "Q3 '23", actualValue: 0.55, estimateValue: 0.58, surprisePercent: 8.2),
                        EarningsQuarterData(quarter: "Q4 '23", actualValue: 0.68, estimateValue: 0.65, surprisePercent: 18.6),
                    ]
                )
                .padding(AppSpacing.lg)
            }
        }
    }
}
