//
//  LessonTitleCard.swift
//  ios
//
//  Molecule: Title card for lesson story - displays main title and highlighted subtitle
//  Supports AI voice reading with word-by-word highlighting
//

import SwiftUI

struct LessonTitleCard: View {
    let title: String
    let subtitleSegments: [HighlightedTextSegment]
    let currentWordRange: NSRange
    let isReading: Bool

    // Computed full subtitle text for reading
    var subtitleText: String {
        subtitleSegments.map { $0.text }.joined()
    }

    var body: some View {
        VStack(spacing: AppSpacing.xl) {
            Spacer()
                .frame(height: 60)

            // Main title
            Text(title)
                .font(.system(size: 42, weight: .bold))
                .foregroundColor(AppColors.textPrimary)
                .multilineTextAlignment(.center)
                .lineSpacing(4)
                .padding(.horizontal, AppSpacing.xl)

            // Subtitle with reading highlight
            ReadingHighlightSegmentedText(
                segments: subtitleSegments,
                currentWordRange: currentWordRange,
                isReading: isReading,
                font: .system(size: 20, weight: .regular)
            )
            .multilineTextAlignment(.center)
            .padding(.horizontal, AppSpacing.xxl)

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        LessonTitleCard(
            title: "Buying Dollar Bills for 50 Cents",
            subtitleSegments: [
                HighlightedTextSegment("Why", highlighted: true),
                HighlightedTextSegment(" Warren Buffett never pays retail price.")
            ],
            currentWordRange: NSRange(location: 4, length: 6), // "Warren"
            isReading: true
        )
    }
    .preferredColorScheme(.dark)
}
