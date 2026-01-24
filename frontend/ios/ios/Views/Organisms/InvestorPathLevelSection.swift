//
//  InvestorJourneyLevelSection.swift
//  ios
//
//  Organism: Complete level section with header, progress bar, and horizontal lesson cards
//

import SwiftUI

struct InvestorJourneyLevelSection: View {
    let levelProgress: LevelProgress
    var onLessonTap: ((Lesson) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Level header
            LevelSectionHeader(
                level: levelProgress.level,
                completed: levelProgress.completedCount,
                total: levelProgress.totalCount
            )
            .padding(.horizontal, AppSpacing.lg)

            // Horizontal scrolling lesson cards
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.md) {
                    ForEach(levelProgress.lessons) { lesson in
                        LessonCard(lesson: lesson) {
                            onLessonTap?(lesson)
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }

            // Level progress bar
            HStack {
                Spacer()
                
                LevelProgressBar(
                    progress: levelProgress.progress,
                    completed: levelProgress.completedCount,
                    total: levelProgress.totalCount,
                    color: levelProgress.level.color
                )
                .frame(maxWidth: 120)
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.xxl) {
            InvestorJourneyLevelSection(
                levelProgress: InvestorJourneyData.sampleData.levels[0]
            )

            InvestorJourneyLevelSection(
                levelProgress: InvestorJourneyData.sampleData.levels[1]
            )
        }
        .padding(.vertical)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
