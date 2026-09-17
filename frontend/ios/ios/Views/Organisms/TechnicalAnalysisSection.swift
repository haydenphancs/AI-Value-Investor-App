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
                actionText: "Details",
                onAction: { onDetailTapped?() },
                showMoreButton: false
            )

            // Technical Meter
            HStack {
                Spacer()
                TechnicalMeter(technicalData: technicalData)
                Spacer()
            }

            // Disclaimer. `.rating` (not the default) because this section hosts the
            // Strong Sell → Strong Buy meter, which is deterministic and rules-based —
            // the generic copy used to call it "AI-generated", which it is not.
            // Full width: the card is a `.leading` VStack with a Spacer-centred meter, so
            // without this a two-line footer pinned left under a centred gauge.
            AnalysisDisclaimerText.rating
                .frame(maxWidth: .infinity)
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
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
