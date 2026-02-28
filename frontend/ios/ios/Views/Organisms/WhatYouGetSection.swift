//
//  WhatYouGetSection.swift
//  ios
//
//  Organism: Features list showing what the analysis includes
//

import SwiftUI

struct WhatYouGetSection: View {
    let features: [AnalysisFeature]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("What You'll Get")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)

            // Features list
            VStack(spacing: AppSpacing.sm) {
                ForEach(features) { feature in
                    FeatureRow(feature: feature)
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    ScrollView {
        WhatYouGetSection(features: AnalysisFeature.allFeatures)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
