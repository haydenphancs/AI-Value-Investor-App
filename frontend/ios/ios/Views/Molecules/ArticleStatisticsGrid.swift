//
//  ArticleStatisticsGrid.swift
//  ios
//
//  Molecule: Grid display of key statistics
//

import SwiftUI

struct ArticleStatisticsGrid: View {
    let statistics: [ArticleStatistic]
    var columns: Int = 3

    private var gridColumns: [GridItem] {
        Array(repeating: GridItem(.flexible(), spacing: AppSpacing.lg), count: columns)
    }

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Grid of statistics
            LazyVGrid(columns: gridColumns, spacing: AppSpacing.xl) {
                ForEach(statistics) { stat in
                    ArticleStatisticValue(
                        value: stat.value,
                        label: stat.label,
                        trend: stat.trend,
                        trendValue: stat.trendValue,
                        alignment: .center
                    )
                }
            }
        }
        .padding(AppSpacing.xl)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .strokeBorder(
                            LinearGradient(
                                colors: [
                                    Color(hex: "3B82F6").opacity(0.3),
                                    Color(hex: "8B5CF6").opacity(0.3),
                                    Color(hex: "06B6D4").opacity(0.3)
                                ],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            ),
                            lineWidth: 1
                        )
                )
        )
    }
}

#Preview {
    ArticleStatisticsGrid(
        statistics: [
            ArticleStatistic(value: "$180B", label: "Total Value Locked", trend: .up, trendValue: "340%"),
            ArticleStatistic(value: "4.2M", label: "Daily Active Users", trend: .up, trendValue: "127%"),
            ArticleStatistic(value: "2,400+", label: "DeFi Protocols", trend: .up, trendValue: "89%")
        ]
    )
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
