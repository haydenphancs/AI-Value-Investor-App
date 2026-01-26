//
//  LessonTitleCard.swift
//  ios
//
//  Molecule: Title card for lesson story - displays main title and highlighted subtitle
//

import SwiftUI

struct LessonTitleCard: View {
    let title: String
    let subtitleSegments: [HighlightedTextSegment]

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

            // Subtitle with highlighted segments
            highlightedText(subtitleSegments)
                .font(.system(size: 20, weight: .regular))
                .multilineTextAlignment(.center)
                .lineSpacing(4)
                .padding(.horizontal, AppSpacing.xxl)

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private func highlightedText(_ segments: [HighlightedTextSegment]) -> some View {
        segments.reduce(Text("")) { result, segment in
            result + Text(segment.text)
                .foregroundColor(
                    segment.isHighlighted ? AppColors.accentCyan : AppColors.textSecondary
                )
        }
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
            ]
        )
    }
    .preferredColorScheme(.dark)
}
