//
//  TechnicalAnalysisSection.swift
//  ios
//
//  Complete Technical Analysis section for the Analysis tab
//

import SwiftUI

struct TechnicalAnalysisSection: View {
    let technicalData: TechnicalAnalysisData
    var onDetailTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            AnalysisSectionHeader(
                title: "Technical Analysis",
                actionText: "Detail",
                onAction: { onDetailTapped?() },
                showMoreButton: false
            )

            // Technical Meter
            HStack {
                Spacer()
                TechnicalMeter(technicalData: technicalData)
                Spacer()
            }

            // Disclaimer
            AnalysisDisclaimerText()
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        TechnicalAnalysisSection(
            technicalData: TechnicalAnalysisData.sampleData,
            onDetailTapped: {}
        )
        .padding()
    }
}
