//
//  EarningsSurpriseBarChart.swift
//  ios
//
//  Molecule: Vertical bar chart displaying quarterly EPS surprise percentages
//  Background chart for 3Y view only - shows historical beat/miss trends
//

import SwiftUI

struct EarningsSurpriseBarChart: View {
    let quarters: [EarningsQuarterData]
    
    // Calculate Y-axis range based on surprise percentages
    // Range is symmetric to ensure 0% line is centered
    private var surpriseRange: (min: Double, max: Double) {
        let surprises = quarters.compactMap { $0.surprisePercent }
        guard !surprises.isEmpty else {
            return (min: -10, max: 10)
        }
        
        let minSurprise = surprises.min() ?? -10
        let maxSurprise = surprises.max() ?? 10
        
        // Find the maximum absolute value to make range symmetric
        let absMax = max(abs(minSurprise), abs(maxSurprise))
        
        // Round up to closest clean value (1, 5, 10, 20, 50, etc.)
        let roundedMax = ceil(absMax)
        
        return (min: -roundedMax, max: roundedMax)
    }
    
    private var chartHeight: CGFloat { 100 }
    private var yAxisWidth: CGFloat { 40 }
    private var barWidthRatio: CGFloat { 0.5 } // Bar takes 50% of available space per quarter
    
    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            // Left spacer to align with main chart (matches Y-axis width)
            yAxisLabels()
                .frame(width: yAxisWidth)
            
            // Chart area with manual bar positioning
            GeometryReader { geometry in
                let width = geometry.size.width
                let height = geometry.size.height
                let quarterCount = quarters.count
                let stepX = width / CGFloat(quarterCount)
                let barWidth = stepX * barWidthRatio
                let range = max(surpriseRange.max - surpriseRange.min, 0.01)
                
                ZStack {
                    // Zero line (horizontal line at 0%)
                    let zeroY = normalizedY(0, height: height, range: range)
                    Path { path in
                        path.move(to: CGPoint(x: 0, y: height - zeroY))
                        path.addLine(to: CGPoint(x: width, y: height - zeroY))
                    }
                    .stroke(style: StrokeStyle(lineWidth: 1.5, dash: [5, 3]))
                    .foregroundColor(AppColors.textMuted.opacity(0.6))
                    
                    // Surprise bars
                    ForEach(Array(quarters.enumerated()), id: \.element.id) { index, quarter in
                        if let surprise = quarter.surprisePercent {
                            let x = CGFloat(index) * stepX + stepX / 2
                            let surpriseY = normalizedY(surprise, height: height, range: range)
                            let barHeight = abs(surpriseY - zeroY)
                            
                            // Calculate bar's Y position
                            // For positive: bar extends from zero upward
                            // For negative: bar extends from zero downward
                            let barCenterY: CGFloat = if surprise >= 0 {
                                // Positive: bar center is above zero line
                                height - zeroY - barHeight / 2
                            } else {
                                // Negative: bar center is below zero line
                                height - zeroY + barHeight / 2
                            }
                            
                            RoundedRectangle(cornerRadius: 3)
                                .fill(surprise >= 0 ? AppColors.bullish : AppColors.bearish)
                                .frame(width: barWidth, height: barHeight)
                                .position(x: x, y: barCenterY)
                        }
                    }
                }
            }
            .frame(height: chartHeight)
        }
    }
    
    // MARK: - Helper Views
    
    private func yAxisLabels() -> some View {
        GeometryReader { geometry in
            let height = geometry.size.height
            let range = max(surpriseRange.max - surpriseRange.min, 0.01)
            
            // Calculate Y positions using the same normalization as the chart
            let maxY = normalizedY(surpriseRange.max, height: height, range: range)
            let zeroY = normalizedY(0, height: height, range: range)
            let minY = normalizedY(surpriseRange.min, height: height, range: range)
            
            ZStack(alignment: .trailing) {
                // Max label at top
                Text(formatYValue(surpriseRange.max))
                    .font(.system(size: 11))
                    .foregroundColor(AppColors.textMuted)
                    .position(x: geometry.size.width / 2, y: height - maxY)
                
                // Zero label at calculated position
                Text("0%")
                    .font(.system(size: 11))
                    .foregroundColor(AppColors.textMuted)
                    .position(x: geometry.size.width / 2, y: height - zeroY)
                
                // Min label at bottom
                Text(formatYValue(surpriseRange.min))
                    .font(.system(size: 11))
                    .foregroundColor(AppColors.textMuted)
                    .position(x: geometry.size.width / 2, y: height - minY)
            }
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.sm)
    }
    
    // MARK: - Helper Functions
    
    private func normalizedY(_ value: Double, height: CGFloat, range: Double) -> CGFloat {
        let normalized = (value - surpriseRange.min) / range
        return CGFloat(normalized) * height * 0.85 + height * 0.075
    }
    
    private func formatYValue(_ value: Double) -> String {
        return "\(Int(value))%"
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
