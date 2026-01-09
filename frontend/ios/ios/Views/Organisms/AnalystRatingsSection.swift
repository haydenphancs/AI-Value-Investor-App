//
//  AnalystRatingsSection.swift
//  ios
//
//  Complete Analyst Ratings section for the Analysis tab
//

import SwiftUI

struct AnalystRatingsSection: View {
    let ratingsData: AnalystRatingsData
    @Binding var selectedMomentumPeriod: AnalystMomentumPeriod
    var onMoreTapped: (() -> Void)?
    var onActionsTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            AnalysisSectionHeader(
                title: "Analyst Ratings",
                subtitle: "Total Analysts \(ratingsData.totalAnalysts)\nUpdated On \(ratingsData.formattedUpdatedDate) ET",
                onAction: { onMoreTapped?() }
            )

            // Consensus and Target
            AnalystConsensusRow(
                consensus: ratingsData.consensus,
                targetPrice: ratingsData.formattedTargetPrice,
                targetUpside: ratingsData.formattedUpside
            )

            // Rating distribution bars
            RatingDistributionList(distributions: ratingsData.distributions)

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
                .padding(.vertical, AppSpacing.sm)

            // Price target range
            PriceTargetRange(priceTarget: ratingsData.priceTarget)

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
                .padding(.vertical, AppSpacing.sm)

            // Momentum section
            AnalysisMomentumSection(
                momentumData: ratingsData.momentumData,
                netPositive: ratingsData.netPositive,
                netNegative: ratingsData.netNegative,
                actionsSummary: ratingsData.actionsSummary,
                selectedPeriod: $selectedMomentumPeriod,
                onActionsTapped: onActionsTapped
            )

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

        ScrollView {
            AnalystRatingsSection(
                ratingsData: AnalystRatingsData.sampleData,
                selectedMomentumPeriod: .constant(.sixMonths),
                onMoreTapped: {}
            )
            .padding()
        }
    }
}
