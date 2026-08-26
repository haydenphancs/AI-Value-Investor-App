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
                        .font(AppTypography.bodyEmphasis)
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

                // Footer: duration on the left, status pinned to the trailing edge
                // (completed check sits at the far right; "Up Next" pill shifts right too).
                HStack {
                    LessonDurationLabel(durationMinutes: lesson.durationMinutes)

                    Spacer(minLength: AppSpacing.sm)

                    LessonStatusBadge(status: lesson.status)
                }
            }
            .padding(AppSpacing.md)
            // Height is a FLOOR, not a fixed size. See the header of RelatedTickerCard.swift
            // for the full rationale: a `.frame(height:)` centres an oversized child, so text
            // that outgrows the box bleeds off the top AND bottom edges. `maxHeight: .infinity`
            // lets the card take the height the parent HStack resolves, which keeps interior
            // Spacers working (so nothing moves at the default content size) and keeps every
            // card in the row the same height. Parent uses `HStack(alignment: .top)` to match.
            .frame(minWidth: 160, maxWidth: 160,
                   minHeight: 150, maxHeight: .infinity, alignment: .topLeading)
            .cardSurface(cornerRadius: AppCornerRadius.large)
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
}
