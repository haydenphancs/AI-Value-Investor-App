//
//  JourneyProgressCard.swift
//  ios
//
//  Molecule: Card showing current journey progress with steps
//

import SwiftUI

struct JourneyProgressCard: View {
    let track: JourneyTrack
    var onContinue: (() -> Void)?
    var onItemTap: ((JourneyItem) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with track info and progress
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    HStack(spacing: AppSpacing.sm) {
                        // Level icon
                        Image(systemName: track.level.iconName)
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(track.level.color)

                        Text("\(track.level.rawValue) Track")
                            .font(AppTypography.headline)
                            .foregroundColor(AppColors.textPrimary)
                    }

                    Text(track.formattedProgress)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                // Progress percentage
                Text("\(track.progressPercentage)%")
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                    .foregroundColor(track.level.color)
            }

            // Progress bar
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 4)
                        .fill(AppColors.cardBackgroundLight)
                        .frame(height: 8)

                    RoundedRectangle(cornerRadius: 4)
                        .fill(track.level.color)
                        .frame(width: geometry.size.width * CGFloat(track.progress), height: 8)
                }
            }
            .frame(height: 8)

            // Journey items - show only active item
            VStack(spacing: 0) {
                ForEach(Array(track.items.enumerated()), id: \.element.id) { index, item in
                    if item.isActive {
                        JourneyItemRow(
                            item: item,
                            isLast: true
                        ) {
                            onItemTap?(item)
                        }
                    }
                }
            }

            // Continue Learning button
            Button(action: {
                onContinue?()
            }) {
                Text("Resume Lessons")
                    .font(AppTypography.bodyBold)
                    .foregroundColor(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(track.level.color)
                    .cornerRadius(AppCornerRadius.medium)
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.extraLarge)
    }
}

#Preview {
    JourneyProgressCard(track: JourneyTrack.sampleBeginner)
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
