//
//  YourJourneySection.swift
//  ios
//
//  Organism: Section showing current journey progress
//

import SwiftUI

struct YourJourneySection: View {
    let track: JourneyTrack
    var onContinue: (() -> Void)?
    var onItemTap: ((JourneyItem) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            Text("Your Journey")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            // Progress card
            JourneyProgressCard(
                track: track,
                onContinue: onContinue,
                onItemTap: onItemTap
            )
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    VStack {
        YourJourneySection(track: JourneyTrack.sampleBeginner)
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
