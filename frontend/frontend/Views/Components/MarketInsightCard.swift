//
//  MarketInsightCard.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct MarketInsightCard: View {
    let insight: MarketInsight

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Title
            Text(insight.title)
                .font(.system(size: 18, weight: .bold))
                .foregroundColor(AppColors.primaryText)
                .lineLimit(2)

            // Bullet Points
            VStack(alignment: .leading, spacing: 8) {
                ForEach(insight.bulletPoints, id: \.self) { point in
                    HStack(alignment: .top, spacing: 8) {
                        Circle()
                            .fill(AppColors.secondaryText)
                            .frame(width: 4, height: 4)
                            .padding(.top, 6)

                        Text(point)
                            .font(.system(size: 13, weight: .regular))
                            .foregroundColor(AppColors.secondaryText)
                            .lineLimit(3)
                    }
                }
            }

            // Footer
            HStack {
                HStack(spacing: 6) {
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 10, weight: .bold))

                    Text(insight.sentiment.rawValue)
                        .font(.system(size: 11, weight: .semibold))
                }
                .foregroundColor(AppColors.cardBackground)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(AppColors.positive)
                .cornerRadius(6)

                Spacer()

                Text(insight.updatedTime)
                    .font(.system(size: 11, weight: .regular))
                    .foregroundColor(AppColors.tertiaryText)
            }
        }
        .padding(16)
        .cardStyle(backgroundColor: AppColors.surfaceBackground)
    }
}

#Preview {
    MarketInsightCard(insight: MarketInsight.mockData)
        .padding()
        .background(AppColors.background)
}
