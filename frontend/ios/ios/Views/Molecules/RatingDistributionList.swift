//
//  RatingDistributionList.swift
//  ios
//
//  List of rating distribution bars
//

import SwiftUI

struct RatingDistributionList: View {
    let distributions: [AnalystRatingDistribution]

    private var maxCount: Int {
        distributions.map { $0.count }.max() ?? 1
    }

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            ForEach(distributions) { distribution in
                RatingDistributionBar(
                    label: distribution.label,
                    count: distribution.count,
                    color: distribution.color,
                    maxCount: maxCount
                )
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        RatingDistributionList(distributions: AnalystRatingDistribution.sampleData)
            .padding()
    }
}
