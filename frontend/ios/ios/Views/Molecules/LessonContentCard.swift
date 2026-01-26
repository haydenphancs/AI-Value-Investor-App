//
//  LessonContentCard.swift
//  ios
//
//  Molecule: Content card for lesson story - displays optional image and highlighted text content
//

import SwiftUI

struct LessonContentCard: View {
    let imageName: String?
    let contentSegments: [HighlightedTextSegment]

    var body: some View {
        VStack(spacing: AppSpacing.xxxl) {
            Spacer()
                .frame(height: 40)

            // Image placeholder or actual image
            imageSection

            Spacer()
                .frame(height: AppSpacing.xl)

            // Content text with highlighted segments
            highlightedText(contentSegments)
                .font(.system(size: 20, weight: .regular))
                .multilineTextAlignment(.leading)
                .lineSpacing(6)
                .padding(.horizontal, AppSpacing.xxl)

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private var imageSection: some View {
        if let imageName = imageName, !imageName.isEmpty {
            // Actual image from assets
            Image(imageName)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(maxWidth: .infinity)
                .frame(height: 200)
                .padding(.horizontal, AppSpacing.xxl)
        } else {
            // Placeholder text for image
            Text("An image here")
                .font(.system(size: 32, weight: .regular))
                .foregroundColor(AppColors.textPrimary)
                .frame(maxWidth: .infinity)
                .frame(height: 150)
        }
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

        LessonContentCard(
            imageName: nil,
            contentSegments: [
                HighlightedTextSegment("Price is what the "),
                HighlightedTextSegment("market", highlighted: true),
                HighlightedTextSegment(" asks. Value is what the business is worth. The gap between them is where investing opportunities are found.")
            ]
        )
    }
    .preferredColorScheme(.dark)
}
