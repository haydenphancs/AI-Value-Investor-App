//
//  LessonCard.swift
//  ios
//
//  Molecule: Card showing individual lesson with title, description, duration, and status
//

import SwiftUI

struct LessonCard: View {
    let lesson: Lesson
    var onTap: (() -> Void)?

    private var cardOpacity: Double {
        lesson.status == .notStarted ? 0.8 : 1.0
    }

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Title row with category badge
                HStack(alignment: .top, spacing: AppSpacing.sm) {
                    Text(lesson.title)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)

                    if lesson.category == .crypto {
                        LessonCategoryBadge(category: lesson.category)
                    }
                }

                // Description
                Text(lesson.description)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)

                Spacer(minLength: AppSpacing.sm)

                // Footer with duration and status
                HStack {
                    LessonDurationLabel(durationMinutes: lesson.durationMinutes)

                    Text("•")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)

                    LessonStatusBadge(status: lesson.status)
                }
            }
            .padding(AppSpacing.md)
            .frame(width: 160, height: 150)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
            .opacity(cardOpacity)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: AppSpacing.md) {
            LessonCard(lesson: Lesson(
                title: "Compound Interest",
                description: "Discover why Einstein called it the eighth wonder of the world.",
                durationMinutes: 3,
                status: .completed
            ))

            LessonCard(lesson: Lesson(
                title: "Stock vs. Business",
                description: "Learn to think like an owner, not a trader. The fundamental shift.",
                durationMinutes: 4,
                status: .upNext
            ))

            LessonCard(lesson: Lesson(
                title: "Bitcoin: Digital Gold?",
                description: "Understanding the \"Store of Value\" thesis.",
                durationMinutes: 4,
                status: .notStarted,
                category: .crypto
            ))
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
