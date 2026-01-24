//
//  LessonDurationLabel.swift
//  ios
//
//  Atom: Label showing lesson duration with clock icon
//

import SwiftUI

struct LessonDurationLabel: View {
    let durationMinutes: Int
    var textColor: Color = AppColors.textMuted

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: "clock")
                .font(.system(size: 10))

            Text("\(durationMinutes) min")
                .font(AppTypography.caption)
        }
        .foregroundColor(textColor)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        LessonDurationLabel(durationMinutes: 3)
        LessonDurationLabel(durationMinutes: 8)
        LessonDurationLabel(durationMinutes: 12, textColor: AppColors.textSecondary)
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
