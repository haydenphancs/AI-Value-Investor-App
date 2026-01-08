//
//  TickerDetailPerformanceSection.swift
//  ios
//
//  Organism: Performance section for Ticker Detail
//

import SwiftUI

struct TickerDetailPerformanceSection: View {
    let periods: [PerformancePeriod]

    // Grid columns - 3 columns layout
    private let columns = Array(repeating: GridItem(.flexible(), spacing: AppSpacing.sm), count: 3)

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header
            Text("Performance")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Performance grid
            LazyVGrid(columns: columns, spacing: AppSpacing.sm) {
                ForEach(periods) { period in
                    PerformanceItem(period: period)
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        TickerDetailPerformanceSection(periods: PerformancePeriod.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
