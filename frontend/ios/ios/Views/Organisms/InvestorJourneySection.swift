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

            // Merged Card: Journey Progress and Level badges combined
            VStack(spacing: 0) {
                // Journey Progress content (if available)
                if let track = journeyTrack {
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
                }

                // Level badges in a horizontal row
                HStack(spacing: 0) {
                    ForEach(InvestorLevel.allCases, id: \.rawValue) { level in
                        levelBadgeWithConnector(level: level)
                    }
                }
                .padding(AppSpacing.lg)
            }
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
            // Badge container
            VStack(spacing: AppSpacing.xs) {
                // Icon circle with connector overlay
                ZStack {
                    // Connector line behind the badge (if not first)
                    if level != .foundation {
                        HStack(spacing: 0) {
                            Rectangle()
                                .fill(level.index <= currentLevel.index ? level.color.opacity(0.5) : AppColors.cardBackgroundLight)
                                .frame(width: 24, height: 2)
                            Spacer()
                        }
                    }
                    
                    // Connector line after the badge (if not last)
                    if level != .master {
                        HStack(spacing: 0) {
                            Spacer()
                            Rectangle()
                                .fill(isCompleted ? level.color.opacity(0.5) : AppColors.cardBackgroundLight)
                                .frame(width: 24, height: 2)
                        }
                    }
                    
                    // Badge circle (on top of connector lines)
                    ZStack {
                        Circle()
                            .fill(isActive || isCompleted ? level.color : AppColors.cardBackgroundLight)
                            .frame(width: 44, height: 44)

                        Image(systemName: level.iconName)
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundColor(isActive || isCompleted ? .white : AppColors.textMuted)
                    }
                }

                // Label
                Text(level.rawValue)
                    .font(AppTypography.caption)
                    .foregroundColor(isActive || isCompleted ? AppColors.textPrimary : AppColors.textMuted)
            }
            .frame(maxWidth: .infinity)
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
