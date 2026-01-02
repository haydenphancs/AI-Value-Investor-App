//
//  NextLessonSection.swift
//  ios
//
//  Organism: Section showing the next lesson to take
//

import SwiftUI

struct NextLessonSection: View {
    let lesson: NextLesson
    var onTap: (() -> Void)?

    var body: some View {
        NextLessonCard(lesson: lesson, onTap: onTap)
            .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    VStack {
        NextLessonSection(lesson: NextLesson.sampleData)
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
