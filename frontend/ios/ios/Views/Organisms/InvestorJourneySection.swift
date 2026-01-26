//
//  InvestorJourneySection.swift
//  ios
//
//  Organism: Section showing investor journey level progression
//

import SwiftUI

struct InvestorJourneySection: View {
    let currentLevel: InvestorLevel
    let journeyTrack: JourneyTrack?
    var onSeeAll: (() -> Void)?
    var onContinue: (() -> Void)?
    var onItemTap: ((JourneyItem) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            SectionHeader(title: "Investor Journey", showSeeAll: true) {
                onSeeAll?()
            }

            // Journey Progress Card (if available) - now on top
            if let track = journeyTrack {
                JourneyProgressCard(
                    track: track,
                    onContinue: onContinue,
                    onItemTap: onItemTap
                )
            }

            // Level badges in a horizontal row - now below
            HStack(spacing: 0) {
                ForEach(InvestorLevel.allCases, id: \.rawValue) { level in
                    levelBadgeWithConnector(level: level)
                }
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.extraLarge)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    @ViewBuilder
    private func levelBadgeWithConnector(level: InvestorLevel) -> some View {
        let isActive = level == currentLevel
        let isCompleted = level.index < currentLevel.index

        HStack(spacing: 0) {
            LevelBadge(level: level, isActive: isActive, isCompleted: isCompleted)
                .frame(maxWidth: .infinity)

            // Connector line (except for last item)
            if level != .master {
                Rectangle()
                    .fill(isCompleted ? level.color.opacity(0.5) : AppColors.cardBackgroundLight)
                    .frame(height: 2)
                    .frame(maxWidth: 20)
            }
        }
    }
}

#Preview {
    VStack {
        InvestorJourneySection(
            currentLevel: .foundation,
            journeyTrack: JourneyTrack.sampleBeginner
        )
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
