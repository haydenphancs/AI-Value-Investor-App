//
//  NextLessonCard.swift
//  ios
//
//  Molecule: Card showing the next lesson preview
//

import SwiftUI

struct NextLessonCard: View {
    let lesson: NextLesson
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.lg) {
                // Left side - content
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    // Badge
                    HStack(spacing: AppSpacing.xs) {
                        Text("Next Up")
                            .font(AppTypography.captionBold)
                            .foregroundColor(AppColors.primaryBlue)

                        Text("Journey \(lesson.journeyNumber): \(lesson.journeyTitle)")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }

                    // Title
                    Text(lesson.lessonTitle)
                        .font(AppTypography.headline)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)

                    // Description
                    Text(lesson.lessonDescription)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)

                    // Meta info
                    HStack(spacing: AppSpacing.lg) {
                        ReadTimeLabel(minutes: lesson.estimatedMinutes)
                        ChapterCountBadge(count: lesson.chapterCount)
                    }
                }

                Spacer()

                // Right side - arrow
                Image(systemName: "chevron.right")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.extraLarge)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    NextLessonCard(lesson: NextLesson.sampleData)
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
