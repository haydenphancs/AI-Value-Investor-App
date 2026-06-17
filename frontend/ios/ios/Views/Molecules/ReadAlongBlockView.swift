//
//  ReadAlongBlockView.swift
//  ios
//
//  Molecule: renders one narrated block (heading or paragraph) of a book core, highlighting the
//  sentence currently being read. Mirrors CoreHeadingView / CoreParagraphView styling so it is a
//  drop-in replacement for the narrated sections, adding only the live highlight.
//
//  `activeTime` is the book narration playhead (seconds) when THIS book's audio is the active
//  episode, else nil (no highlight). The active sentence is the one whose [start, end) contains it.
//

import SwiftUI

struct ReadAlongBlockView: View {
    let block: ReadAlongBlock
    let activeTime: Double?

    /// Index of the sentence currently being read, if the playhead is inside this block.
    private var activeIndex: Int? {
        guard let t = activeTime else { return nil }
        return block.sentences.firstIndex { t >= $0.start && t < $0.end }
    }

    var body: some View {
        if block.isHeading {
            Text(attributed(base: AppColors.textPrimary))
                .font(AppTypography.titleCompact)
                .padding(.top, AppSpacing.md)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            Text(attributed(base: AppColors.textSecondary))
                .font(AppTypography.body)
                .lineSpacing(6)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Build the block's text, lighting up the active sentence with a brighter color + highlight.
    private func attributed(base: Color) -> AttributedString {
        let active = activeIndex
        var result = AttributedString()
        for (i, sentence) in block.sentences.enumerated() {
            var piece = AttributedString(i == 0 ? sentence.text : " " + sentence.text)
            if i == active {
                piece.foregroundColor = AppColors.textPrimary
                piece.backgroundColor = AppColors.primaryBlue.opacity(0.28)
            } else {
                piece.foregroundColor = base
            }
            result.append(piece)
        }
        return result
    }
}
