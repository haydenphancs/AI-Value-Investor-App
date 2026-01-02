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
            HStack {
                Text("Your Journey")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button(action: {
                    onContinue?()
                }) {
                    Text("Continue")
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }

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
