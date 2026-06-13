//
//  LessonContentCard.swift
//  ios
//
//  Molecule: Content card for lesson story - displays optional image and highlighted text content
//  Supports AI voice reading with word-by-word highlighting
//

import SwiftUI

struct LessonContentCard: View {
    let imageName: String?
    let contentSegments: [HighlightedTextSegment]
    let currentWordRange: NSRange
    let isReading: Bool

    // Computed full content text for reading
    var contentText: String {
        contentSegments.map { $0.text }.joined()
    }

    var body: some View {
        VStack(spacing: AppSpacing.xl) {
            Spacer()

            // Optional image slot — content cards are text-focused, so only shown
            // when an image is explicitly provided for this card.
            if let imageName = imageName, !imageName.isEmpty {
                LessonImageSlot(imageName: imageName)
            }

            // Content text with reading highlight
            ReadingHighlightSegmentedText(
                segments: contentSegments,
                currentWordRange: currentWordRange,
                isReading: isReading,
                font: .system(size: 20, weight: .regular)
            )
            .multilineTextAlignment(.leading)
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

        LessonContentCard(
            imageName: nil,
            contentSegments: [
                HighlightedTextSegment("Price is what the "),
                HighlightedTextSegment("market", highlighted: true),
                HighlightedTextSegment(" asks. Value is what the business is worth. The gap between them is where investing opportunities are found.")
            ],
            currentWordRange: NSRange(location: 18, length: 6), // "market"
            isReading: true
        )
    }
    .preferredColorScheme(.dark)
}
