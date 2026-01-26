//
//  LessonStoryProgressBar.swift
//  ios
//
//  Atom: Segmented progress bar for lesson story cards (Instagram/TikTok style)
//

import SwiftUI

struct LessonStoryProgressBar: View {
    let currentIndex: Int
    let totalCount: Int
    var currentProgress: CGFloat = 1.0  // Progress within current segment (0-1)

    private let segmentSpacing: CGFloat = 4
    private let segmentHeight: CGFloat = 3
    private let cornerRadius: CGFloat = 1.5

    var body: some View {
        GeometryReader { geometry in
            let totalSpacing = segmentSpacing * CGFloat(totalCount - 1)
            let segmentWidth = (geometry.size.width - totalSpacing) / CGFloat(totalCount)

            HStack(spacing: segmentSpacing) {
                ForEach(0..<totalCount, id: \.self) { index in
                    segmentView(
                        for: index,
                        width: segmentWidth
                    )
                }
            }
        }
        .frame(height: segmentHeight)
    }

    @ViewBuilder
    private func segmentView(for index: Int, width: CGFloat) -> some View {
        ZStack(alignment: .leading) {
            // Background track
            RoundedRectangle(cornerRadius: cornerRadius)
                .fill(Color.white.opacity(0.3))
                .frame(width: width, height: segmentHeight)

            // Filled portion
            if index < currentIndex {
                // Completed segments
                RoundedRectangle(cornerRadius: cornerRadius)
                    .fill(Color.white)
                    .frame(width: width, height: segmentHeight)
            } else if index == currentIndex {
                // Current segment with progress
                RoundedRectangle(cornerRadius: cornerRadius)
                    .fill(Color.white)
                    .frame(width: width * currentProgress, height: segmentHeight)
            }
            // Future segments remain empty (just background)
        }
        .frame(width: width, height: segmentHeight)
    }
}

#Preview {
    VStack(spacing: 30) {
        // First segment active
        LessonStoryProgressBar(currentIndex: 0, totalCount: 4)
            .padding(.horizontal, 16)

        // Second segment active with partial progress
        LessonStoryProgressBar(currentIndex: 1, totalCount: 4, currentProgress: 0.5)
            .padding(.horizontal, 16)

        // Third segment complete
        LessonStoryProgressBar(currentIndex: 2, totalCount: 4)
            .padding(.horizontal, 16)

        // Last segment active
        LessonStoryProgressBar(currentIndex: 3, totalCount: 4)
            .padding(.horizontal, 16)
    }
    .padding(.vertical, 50)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
