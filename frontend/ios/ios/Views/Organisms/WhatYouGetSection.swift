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
            // Section header with info icon
            HStack {
                Text("What You'll Get")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button(action: {
                    // Show info tooltip
                }) {
                    Image(systemName: "info.circle")
                        .font(.system(size: 16))
                        .foregroundColor(AppColors.textMuted)
                }
            }

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
